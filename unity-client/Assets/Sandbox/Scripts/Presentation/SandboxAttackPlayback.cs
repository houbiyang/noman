using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEngine;
using UnityEngine.Rendering;

namespace Qiongtu.Sandbox
{
    /// <summary>One monotonic cursor per live role/branch. Reconnects and restores baseline, never replay history.</summary>
    public sealed class SandboxAttackCursor
    {
        private string scope;
        private double elapsed;
        public bool Reset { get; private set; }
        public SandboxAttackEvent[] Read(SandboxServerView view)
        {
            string next = view?.context == null ? "" : view.context.game_id + ":" + view.context.branch_id + ":" + view.role;
            double now = view?.unit_interactions?.elapsed_seconds ?? 0;
            Reset = next != scope || now < elapsed;
            if (Reset) { scope = next; elapsed = now; return Array.Empty<SandboxAttackEvent>(); }
            var ids = new HashSet<string>();
            var events = (view?.attack_events ?? Array.Empty<SandboxAttackEvent>()).Where(e => e != null && !string.IsNullOrEmpty(e.event_id)
                && e.at > elapsed && e.at <= now && e.source?.position != null && e.target?.position != null && ids.Add(e.event_id))
                .OrderBy(e => e.at).ThenBy(e => e.event_id, StringComparer.Ordinal).ToArray();
            elapsed = now;
            return events;
        }
    }

    /// <summary>Production adapter for cannon-3d: frozen server events, pooled cosmetic VFX, no fixture/health writes.</summary>
    public sealed class SandboxAttackPlayback : MonoBehaviour
    {
        private sealed class Shot
        {
            public SandboxAttackEvent value;
            public float start;
            public bool fired, hit;
            public Vector3 origin, end;
        }
        private readonly SandboxAttackCursor cursor = new SandboxAttackCursor();
        private readonly List<Shot> shots = new List<Shot>();
        private readonly List<GameObject> pool = new List<GameObject>();
        private readonly List<GameObject> wrecks = new List<GameObject>();
        private readonly Dictionary<string, float> deaths = new Dictionary<string, float>();
        private Material glow, smoke;
        private Mesh sphere;
        private MaterialPropertyBlock block;
        private int used;
        private Func<string, string, SandboxCombatActor> find;
        public int ActiveShots => shots.Count;

        public void Initialize(Func<string, string, SandboxCombatActor> resolver)
        {
            find = resolver; glow = Resources.Load<Material>("Combat/Glow"); smoke = Resources.Load<Material>("Combat/Smoke");
            sphere = Resources.Load<Mesh>("Combat/PrimitiveSphere"); block = new MaterialPropertyBlock();
        }
        public void Apply(SandboxServerView view)
        {
            SandboxAttackEvent[] events = cursor.Read(view);
            if (cursor.Reset) Clear();
            if (events.Length == 0) return;
            // Existing committed movement traces finish in .8s. Combat is a compressed post-settlement replay.
            float start = Mathf.Max(Time.time + .85f, shots.Count == 0 ? 0 : shots.Max(s => s.start) + .8f);
            double first = events[0].at;
            foreach (var e in events)
                shots.Add(new Shot { value = e, start = start + (float)(e.at - first) * .09f });
        }
        public bool HasPendingDeath(string id, string life)
            => shots.Any(s => !s.hit && s.value.lethal && s.value.target.unit_id == id && s.value.target.life_id == life);

        public void Clear()
        {
            shots.Clear(); deaths.Clear();
            foreach (var obj in pool) if (obj) obj.SetActive(false);
            foreach (var obj in wrecks) if (obj) Destroy(obj);
            wrecks.Clear();
        }
        private static Vector3 Position(SandboxAttackEndpoint endpoint)
        {
            var type = endpoint.unit_type == "recon" && !endpoint.docked ? SandboxUnitType.Recon : SandboxUnitType.Ground;
            return SandboxWorld.ArmyPosition(float.Parse(endpoint.position.x, CultureInfo.InvariantCulture), float.Parse(endpoint.position.y, CultureInfo.InvariantCulture), type) + Vector3.up * 2;
        }
        private void Update()
        {
            if (block == null) return;
            used = 0;
            foreach (var s in shots)
            {
                float age = Time.time - s.start;
                var source = find(s.value.source.unit_id, s.value.source.life_id);
                var target = find(s.value.target.unit_id, s.value.target.life_id);
                Vector3 end = target ? target.Center : Position(s.value.target);
                if (age < 0) { if (age > -.8f && source) source.Aim(end); continue; }
                if (!s.fired)
                {
                    s.fired = true;
                    if (source) source.Fire(end);
                    s.origin = source ? source.Muzzle : Position(s.value.source);
                    s.end = end;
                }
                if (age >= .65f && !s.hit)
                {
                    s.hit = true;
                    if (target) target.Hit(s.value.lethal);
                    // A lethal enemy can disappear from current contacts immediately. Show only its frozen,
                    // legally witnessed wreck, never resolve its hidden current position.
                    if (s.value.lethal && !target && !deaths.ContainsKey(s.value.target.life_id))
                    {
                        deaths[s.value.target.life_id] = Time.time;
                        string key = s.value.target.unit_type == "recon" ? "recon" : s.value.target.unit_type == "support" ? "support" : "infantry";
                        var prefab = Resources.Load<GameObject>("Army/" + key);
                        if (prefab)
                        {
                            var obj = Instantiate(prefab, transform); obj.name = "Witnessed wreck · " + s.value.target.unit_id;
                            obj.transform.position = Position(s.value.target) - Vector3.up * 2;
                            obj.transform.localScale = Vector3.one * SandboxUnitView.ModelScale;
                            var meshes = obj.GetComponentsInChildren<Renderer>();
                            if (meshes.Length > 0)
                            {
                                float bottom = meshes.Min(m => m.bounds.min.y);
                                obj.transform.position += Vector3.up * (Position(s.value.target).y - 2 - bottom);
                            }
                            var actor = obj.AddComponent<SandboxCombatActor>();
                            actor.Configure(s.value.target.side == "red" ? new Color(.86f,.38f,.27f) : new Color(.27f,.57f,.76f), s.value.target.unit_type == "recon" && !s.value.target.docked);
                            actor.Hit(true); wrecks.Add(obj); Destroy(obj, 4);
                        }
                    }
                }
                RenderShot(s.origin, s.end, age, s.value.lethal);
            }
            shots.RemoveAll(s => Time.time - s.start > 3.6f);
            wrecks.RemoveAll(w => !w);
            foreach (var key in deaths.Keys.ToArray()) if (Time.time - deaths[key] > 8) deaths.Remove(key);
            for (int i = used; i < pool.Count; i++) pool[i].SetActive(false);
        }

        // The following timing, palette, tapered trail and sparks come from the referenced 3D rehearsal.
        private void RenderShot(Vector3 origin, Vector3 end, float age, bool lethal)
        {
            Vector3 dir = (end - origin).normalized;
            if (age < .13f)
            {
                float k = 1 - age / .13f;
                Fx(origin + dir * .5f, Vector3.one * (.3f + k * .6f), new Color(4, 2.4f, .6f, k), glow);
                Beam(origin, origin + dir * (1.8f * k), .22f * k, new Color(4, 1.4f, .15f, k));
            }
            if (age < .65f)
            {
                Vector3 point = Vector3.Lerp(origin, end, age / .65f);
                float length = Mathf.Min(10.4f, Vector3.Distance(origin, point));
                for (int i = 0; i < 8; i++)
                {
                    float a = i / 8f, b = (i + 1) / 8f, fade = 1 - a;
                    Vector3 head = point - dir * (length * a), tail = point - dir * (length * b);
                    Beam(tail, head, .60f * fade + .08f, new Color(3, 1.1f, .12f, .32f * fade));
                    Beam(tail, head, .26f * fade + .05f, new Color(5, 3.2f, 1.1f, .9f * fade));
                }
                Fx(point, Vector3.one * .64f, new Color(6, 4, 1.5f, 1), glow);
            }
            if (age < 1.15f)
                Beam(origin, Vector3.Lerp(origin, end, Mathf.Clamp01(age / .65f)), .13f, new Color(2.6f, 1.25f, .25f, age < .65f ? .24f : .24f * (1 - (age - .65f) / .5f)));
            for (int i = 0; i < 5; i++)
            {
                float a = age - i * .035f;
                if (a > 0 && a < 1.15f) Fx(origin + dir * (.3f + a * 1.5f) + Vector3.up * a, Vector3.one * (.3f + a), new Color(.4f, .38f, .32f, (1 - a / 1.15f) * .23f), smoke);
            }
            float impact = age - .65f;
            if (impact < 0) return;
            if (impact < .32f) Fx(end, Vector3.one * (3 + impact * 14), new Color(5, 2.1f, .3f, 1 - impact / .32f), glow);
            for (int i = 0; i < 9; i++)
            {
                if (impact > .8f) break;
                Vector3 v = new Vector3(Mathf.Sin(i * 9) * 6, 4 + i % 3, Mathf.Cos(i * 5) * 6);
                Vector3 p = end + v * impact + Vector3.down * impact * impact * 4;
                Beam(p, p - v * .05f, .07f, new Color(4, 1.6f, .2f, 1 - impact / .8f));
            }
            if (impact < 1.8f) Fx(end + Vector3.up * impact * 2, Vector3.one * (1 + impact * 2), new Color(.15f, .16f, .14f, .3f * (1 - impact / 1.8f)), smoke);
            if (lethal)
            {
                for (int i = 0; i < 7; i++)
                {
                    float a = (impact + i * .27f) % 2.1f;
                    Fx(end + new Vector3(Mathf.Sin(i * 4), a, Mathf.Cos(i * 3)), Vector3.one * (.6f + a * .3f), new Color(2.5f, 1 - a / 2.1f, .06f, .7f * (1 - a / 2.1f)), glow);
                }
            }
        }
        private GameObject Fx(Vector3 p, Vector3 size, Color color, Material material)
        {
            GameObject obj;
            if (used == pool.Count)
            {
                obj = new GameObject("Pooled authoritative attack VFX"); obj.transform.SetParent(transform, false);
                obj.AddComponent<MeshFilter>().sharedMesh = sphere;
                var mesh = obj.AddComponent<MeshRenderer>(); mesh.shadowCastingMode = ShadowCastingMode.Off;
                pool.Add(obj);
            }
            else obj = pool[used];
            used++; obj.SetActive(true); obj.transform.position = p; obj.transform.localScale = size; obj.transform.rotation = Quaternion.identity;
            var renderer = obj.GetComponent<Renderer>(); renderer.sharedMaterial = material;
            block.Clear(); block.SetColor("_BaseColor", color); renderer.SetPropertyBlock(block);
            return obj;
        }
        private void Beam(Vector3 a, Vector3 b, float width, Color color)
        {
            Vector3 d = b - a; if (d.sqrMagnitude < .00001f) return;
            Fx((a + b) * .5f, new Vector3(width, width, d.magnitude), color, glow).transform.rotation = Quaternion.LookRotation(d);
        }
        private void OnDisable() { Clear(); }
    }
}
