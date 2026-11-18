using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.Rendering;

namespace Qiongtu.Sandbox
{
    /// <summary>真实历史使用原地图与主相机；只有独立合成预演使用隔离视口。只采样已裁决记录，不ApplyProjection或发命令。
    /// 复用对抗预演的机械动作与补给预演的连接束/流动粒子，不导入其自动裁决流程。</summary>
    public sealed class SandboxRoundReplayStage : MonoBehaviour
    {
        private const int Layer = 31;
        private readonly Dictionary<string, GameObject> models = new Dictionary<string, GameObject>();
        private readonly Dictionary<string, SandboxCombatActor> actors = new Dictionary<string, SandboxCombatActor>();
        private readonly Dictionary<string, SandboxReplayEvent> shots = new Dictionary<string, SandboxReplayEvent>();
        private readonly Dictionary<string, SandboxReplayEvent> hits = new Dictionary<string, SandboxReplayEvent>();
        private readonly Dictionary<string, SandboxReplayEvent> retired = new Dictionary<string, SandboxReplayEvent>();
        private readonly Dictionary<string, SandboxReplayEvent> headings = new Dictionary<string, SandboxReplayEvent>();
        private readonly Dictionary<string, SandboxReplayEvent> moves = new Dictionary<string, SandboxReplayEvent>();
        private readonly Dictionary<string, LineRenderer> points = new Dictionary<string, LineRenderer>();
        private readonly List<GameObject> particles = new List<GameObject>();
        private readonly List<LineRenderer> shotTrails = new List<LineRenderer>();
        private readonly List<LineRenderer> beams = new List<LineRenderer>();
        private readonly List<SandboxReplayEvent> recent = new List<SandboxReplayEvent>();
        private readonly HashSet<(int, string, string, Vector3, Vector3)> serviceConnections = new HashSet<(int, string, string, Vector3, Vector3)>();
        private static readonly Color ServiceColor = new Color(.3f, 1, .74f);
        private MaterialPropertyBlock tint;
        private Camera view, main;
        private int mainMask, particleCount, beamCount, trailCount, frameAt = -1;
        private SandboxRoundReplayCursor sampled;
        private Material glow;
        private RenderTexture texture;
        private Vector3 focus = new Vector3(6000, 0, 6000);
        private float distance = 16000;
        private bool released, inMap;
        public bool UsesOriginalMap => inMap;
        public RenderTexture Texture => texture;
        public Camera Camera => view;
        public int ParticlePoolSize => particles.Count;
        public int ActiveServiceConnectionCount => beamCount;
        public int ActiveShotTrailCount => trailCount;
        private static Color Team(string side) => side == "blue" ? new Color(.27f, .57f, .76f)
            : side == "red" ? new Color(.86f, .38f, .27f) : new Color(.7f, .76f, .65f);
        private static void SetLayer(Transform root)
        {
            root.gameObject.layer = Layer;
            foreach (Transform child in root) SetLayer(child);
        }
        public void Initialize(SandboxWorld source, bool originalMap = false)
        {
            inMap = originalMap;
            tint = new MaterialPropertyBlock();
            main = Camera.main;
            if (main) { mainMask = main.cullingMask; main.cullingMask = inMap ? mainMask | (1 << Layer) : mainMask & ~(1 << Layer); }
            if (inMap) view = main;
            else
            {
            texture = new RenderTexture(1280, 720, 24) { name = "Read-only recorded battle viewport" };
            texture.Create();
            var cameraObject = new GameObject("Recorded battle camera"); cameraObject.transform.SetParent(transform, false);
            view = cameraObject.AddComponent<Camera>();
            if (main) view.CopyFrom(main);
            view.cullingMask = 1 << Layer; view.targetTexture = texture; view.orthographic = false;
            view.fieldOfView = 50; view.rect = new Rect(0, 0, 1, 1); view.nearClipPlane = .5f;
            view.farClipPlane = 40000; view.enabled = true; view.useOcclusionCulling = false;
            }
            if (!inMap && source?.ReplayMapSource != null)
            {
                var map = Instantiate(source.ReplayMapSource, transform, true); map.name = "Frozen map visual clone";
                SetLayer(map.transform);
                foreach (var collider in map.GetComponentsInChildren<Collider>(true)) collider.enabled = false;
                foreach (var behaviour in map.GetComponentsInChildren<MonoBehaviour>(true)) behaviour.enabled = false;
                foreach (var renderer in map.GetComponentsInChildren<Renderer>(true)) renderer.shadowCastingMode = ShadowCastingMode.Off;
                foreach (var light in map.GetComponentsInChildren<Light>(true)) light.enabled = false;
                foreach (var camera in map.GetComponentsInChildren<Camera>(true)) camera.enabled = false;
                foreach (var audio in map.GetComponentsInChildren<AudioSource>(true)) audio.enabled = false;
            }
            glow = Resources.Load<Material>("Combat/Glow");
            if (!glow) throw new InvalidOperationException("缺少回放光效材质");
            var definition = SandboxMapFactory.LoadDefinition();
            foreach (var point in SandboxCaptureLayout.Load(definition).points)
            {
                var line = NewBeam("Recorded capture " + point.id);
                var boundary = SandboxCaptureLayout.Boundary(definition, point);
                line.positionCount = boundary.Length; line.SetPositions(boundary); line.startWidth = line.endWidth = 6;
                points.Add(point.id, line);
            }
            if (!inMap) PoseCamera();
        }
        public static Vector3 Position(SandboxReplayPosition value) => new Vector3(value.x_mm, value.height_mm, value.z_mm) / 1000f;
        public void Focus(SandboxReplayUnit unit)
        {
            focus = models.TryGetValue(unit.unit_id, out var model) ? model.transform.position : Position(unit.position);
            distance = 160; PoseCamera();
        }
        /// <summary>只调整预演镜头，不移动实时单位或改写回放端点。</summary>
        public void FrameRehearsal(SandboxReplayUnit[] units)
        {
            if (units == null || units.Length == 0) return;
            Bounds bounds = new Bounds(Position(units[0].position), Vector3.zero);
            foreach (var unit in units) bounds.Encapsulate(Position(unit.position));
            // Rehearsal-only display magnification; authoritative coordinates and events stay untouched.
            foreach (var unit in units)
                if (models.TryGetValue(unit.unit_id, out var model))
                {
                    var renderers = model.GetComponentsInChildren<Renderer>();
                    if (renderers.Length == 0) continue;
                    Bounds visual = renderers[0].bounds;
                    foreach (var renderer in renderers) visual.Encapsulate(renderer.bounds);
                    float longest = Mathf.Max(visual.size.x, visual.size.y, visual.size.z);
                    if (longest > .001f) model.transform.localScale *= (unit.unit_type == "recon" ? 12 : 18) / longest;
                }
            focus = bounds.center;
            distance = Mathf.Max(260, bounds.size.magnitude * 1.65f);
            PoseCamera();
        }
        public void Overview() { focus = new Vector3(6000, 0, 6000); distance = 16000; PoseCamera(); }
        public void Zoom(float factor) { distance = Mathf.Clamp(distance * factor, 25, 24000); PoseCamera(); }
        private void PoseCamera()
        {
            view.transform.position = focus + new Vector3(.45f, .65f, -.74f).normalized * distance;
            view.transform.LookAt(focus);
        }
        private GameObject Model(SandboxReplayUnit unit)
        {
            if (models.TryGetValue(unit.unit_id, out var existing)) return existing;
            string key = unit.unit_type == "ground" ? "Combat/infantry" : "Army/" + (unit.unit_type == "recon" ? "recon" : "support");
            var prefab = Resources.Load<GameObject>(key);
            if (!prefab) throw new InvalidOperationException("缺少回放模型 " + key);
            var holder = new GameObject("Recorded " + unit.unit_id); holder.transform.SetParent(transform, false);
            var model = Instantiate(prefab, holder.transform, false);
            if (inMap) model.transform.localScale *= SandboxUnitView.ModelScale;
            var renderers = model.GetComponentsInChildren<Renderer>();
            if (renderers.Length > 0)
            {
                Bounds bounds = renderers[0].bounds;
                foreach (var r in renderers) { bounds.Encapsulate(r.bounds); r.shadowCastingMode = ShadowCastingMode.Off; }
                model.transform.localPosition = new Vector3(0, -bounds.min.y, 0);
            }
            var actor = model.GetComponent<SandboxCombatActor>() ?? model.AddComponent<SandboxCombatActor>();
            actor.Configure(Team(unit.side), unit.unit_type == "recon" && !unit.docked);
            SetLayer(holder.transform);
            foreach (var collider in holder.GetComponentsInChildren<Collider>()) collider.enabled = false;
            models.Add(unit.unit_id, holder); actors.Add(unit.unit_id, actor);
            return holder;
        }
        /// <summary>只对后端给出的折线作显示插值；不寻路、不重采样地形、不修改权威坐标。</summary>
        public static Vector3 TracePosition(SandboxReplayPosition[] trace, double fraction)
        {
            if (trace == null || trace.Length < 2) throw new ArgumentException("缺少已裁决移动轨迹");
            float total = 0;
            for (int i = 1; i < trace.Length; i++) total += Vector3.Distance(Position(trace[i - 1]), Position(trace[i]));
            float remaining = total * Mathf.Clamp01((float)fraction);
            for (int i = 1; i < trace.Length; i++)
            {
                var a = Position(trace[i - 1]); var b = Position(trace[i]); float length = Vector3.Distance(a, b);
                if (remaining <= length && length > 0) return Vector3.Lerp(a, b, remaining / length);
                remaining -= length;
            }
            return Position(trace[trace.Length - 1]);
        }
        private void IndexEvents(SandboxRoundReplayCursor cursor)
        {
            int at = (int)(Math.Floor(cursor.Seconds / 5) * 5);
            if (sampled == cursor && frameAt == at) return;
            sampled = cursor; frameAt = at;
            shots.Clear(); hits.Clear(); retired.Clear(); moves.Clear(); headings.Clear(); recent.Clear();
            foreach (var e in cursor.Events)
            {
                if (e.at_seconds <= at)
                {
                    if (e.kind == "move") headings[e.source_id] = e;
                    if (e.kind == "shot") shots[e.source_id] = e;
                    if (e.kind == "hit") hits[e.target_id] = e;
                    if (e.kind == "retired") retired[e.source_id ?? e.target_id] = e;
                    if (e.at_seconds == at) recent.Add(e);
                }
                if ((e.kind == "move" || e.kind == "takeoff" || e.kind == "land") && e.at_seconds == at + 5) moves[e.source_id] = e;
            }
        }
        public void Sample(SandboxRoundReplayCursor cursor)
        {
            IndexEvents(cursor); particleCount = 0; beamCount = 0; trailCount = 0; serviceConnections.Clear();
            foreach (var unit in cursor.World.units)
            {
                var model = Model(unit); Vector3 position = Position(unit.position);
                if (moves.TryGetValue(unit.unit_id, out var movement))
                    position = movement.kind == "move" ? TracePosition(movement.trace, (cursor.Seconds - (movement.at_seconds - 5)) / 5)
                        : Vector3.Lerp(Position(movement.source_position), Position(movement.target_position), (float)(cursor.Seconds - (movement.at_seconds - 5)) / 5);
                model.transform.position = position;
                SandboxReplayEvent bearing = movement;
                if (bearing == null) headings.TryGetValue(unit.unit_id, out bearing);
                model.transform.rotation = Quaternion.identity;
                if (bearing != null && bearing.kind == "move")
                {
                    double part = movement == null ? 1 : (cursor.Seconds - (movement.at_seconds - 5)) / 5;
                    var tangent = TracePosition(bearing.trace, Math.Min(1, part + .001)) - TracePosition(bearing.trace, Math.Max(0, part - .001));
                    tangent.y = 0;
                    if (tangent.sqrMagnitude > .000001f) model.transform.rotation = Quaternion.LookRotation(tangent);
                }
                var actor = actors[unit.unit_id]; actor.SetAirborne(unit.unit_type == "recon" && !unit.docked);
                shots.TryGetValue(unit.unit_id, out var shot); hits.TryGetValue(unit.unit_id, out var hit); retired.TryGetValue(unit.unit_id, out var end);
                actor.SampleRecorded((float)cursor.Seconds, shot?.target_position == null ? (Vector3?)null : Position(shot.target_position),
                    shot?.at_seconds ?? -100, hit?.at_seconds ?? -100, end?.at_seconds ?? -100, unit.retired);
            }
            foreach (var point in cursor.World.capture_points)
                if (points.TryGetValue(point.point_id, out var ring)) TintLine(ring, Team(point.owner));
            // 端点、时间、交付资源都来自记录；没有命中/资源交付判定。
            foreach (var e in recent)
            {
                float age = (float)(cursor.Seconds - e.at_seconds);
                if (age < 0 || age >= 2 || e.source_position == null || e.target_position == null) continue;
                Vector3 a = Position(e.source_position) + Vector3.up * 2, b = Position(e.target_position) + Vector3.up * 2;
                if (e.kind == "shot")
                {
                    if (age < .6f) {
                        var head = Vector3.Lerp(a, b, Mathf.Clamp01(age / .6f));
                        Sphere(head, inMap ? 1.4f : .7f, new Color(1, .7f, .2f));
                        if (trailCount == shotTrails.Count) shotTrails.Add(NewBeam("Recorded shot streak (pooled)"));
                        var trail = shotTrails[trailCount++]; trail.gameObject.SetActive(true);
                        trail.SetPosition(0, Vector3.Lerp(a, b, Mathf.Clamp01((age - .12f) / .6f))); trail.SetPosition(1, head);
                        TintLine(trail, new Color(1, .72f, .25f));
                        trail.startWidth = trail.endWidth = CosmeticWidth(head);
                    }
                    if (age < .18f) Sphere(a, 2.5f * (1 - age / .18f), new Color(1, .8f, .3f));
                }
                else if (e.kind == "hit") Sphere(b, (1 + age * 3) * Mathf.Clamp01(1 - age / 2), new Color(1, .4f, .13f));
                else if (e.kind == "service" || e.kind == "barracks_service")
                {
                    // 同一次实际服务可能产生能源/弹药/维修多条账目；统一为一条补给连接。
                    // 不合成靠近、停靠或离场动作，也不在无交付事件时制造动画。
                    if (!serviceConnections.Add((e.at_seconds, e.camp_id ?? e.source_id, e.target_id, a, b))) continue;
                    Color color = e.resource == "ammo" ? new Color(1, .72f, .25f) : e.resource == "repair" ? new Color(.35f, .78f, 1) : ServiceColor;
                    Beam(a, b, color);
                    for (int i = 0; i < 4; i++) Sphere(Vector3.Lerp(a, b, Mathf.Repeat(age * .65f + i * .25f, 1)), inMap ? 1.15f : .55f, color);
                }
            }
            for (int i = particleCount; i < particles.Count; i++) particles[i].SetActive(false);
            for (int i = beamCount; i < beams.Count; i++) beams[i].gameObject.SetActive(false);
            for (int i = trailCount; i < shotTrails.Count; i++) shotTrails[i].gameObject.SetActive(false);
        }
        public Vector3 RecordedPosition(string unitId) => models[unitId].transform.position;
        public bool TryCapturePosition(string id, out Vector3 position)
        {
            position = Vector3.zero;
            if (id == null || !points.TryGetValue(id, out var ring) || ring.positionCount == 0) return false;
            for (int i = 0; i < ring.positionCount; i++) position += ring.GetPosition(i);
            position /= ring.positionCount;
            return true;
        }
        public Vector3 ViewPosition(string unitId) => view.WorldToViewportPoint(models[unitId].transform.position + Vector3.up * 8);
        private void Sphere(Vector3 position, float radius, Color color)
        {
            if (particleCount == particles.Count)
            {
                var obj = GameObject.CreatePrimitive(PrimitiveType.Sphere); obj.name = "Recorded cosmetic particle (pooled)";
                obj.transform.SetParent(transform, false); obj.layer = Layer; obj.GetComponent<Collider>().enabled = false;
                obj.GetComponent<Renderer>().sharedMaterial = glow; particles.Add(obj);
            }
            var particle = particles[particleCount++]; particle.SetActive(true);
            particle.transform.position = position; particle.transform.localScale = Vector3.one * radius;
            tint.Clear(); tint.SetColor("_BaseColor", color); particle.GetComponent<Renderer>().SetPropertyBlock(tint);
        }
        private LineRenderer NewBeam(string name)
        {
            var obj = new GameObject(name); obj.transform.SetParent(transform, false); obj.layer = Layer;
            var line = obj.AddComponent<LineRenderer>(); line.sharedMaterial = glow; line.positionCount = 2;
            line.startWidth = line.endWidth = .28f; return line;
        }
        private void TintLine(LineRenderer line, Color color)
        {
            line.startColor = line.endColor = color;
            // Glow shader has no vertex-color input. Supply its actual shader property,
            // not just LineRenderer gradient, otherwise all recorded beams render white.
            tint.Clear(); tint.SetColor("_BaseColor", color); line.SetPropertyBlock(tint);
        }
        private float CosmeticWidth(Vector3 position) => !inMap || !view ? .28f : Mathf.Clamp(
            Vector3.Distance(view.transform.position, position) * Mathf.Tan(view.fieldOfView * Mathf.Deg2Rad * .5f) * 4 / Mathf.Max(1, view.pixelHeight), .45f, 4);
        private void Beam(Vector3 a, Vector3 b, Color color)
        {
            if (beamCount == beams.Count) beams.Add(NewBeam("Recorded service connection (pooled)"));
            var line = beams[beamCount++]; line.gameObject.SetActive(true);
            line.startWidth = line.endWidth = CosmeticWidth((a + b) * .5f);
            line.SetPosition(0, a); line.SetPosition(1, b); TintLine(line, color);
        }
        // 同步释放隔离相机，再帧末销毁物体；快速关闭后重开也不会恢复错的主相机mask。
        public void Release()
        {
            if (released) return; released = true;
            if (view && !inMap) { view.enabled = false; view.targetTexture = null; }
            if (main) main.cullingMask = mainMask;
            if (texture) { texture.Release(); if (Application.isPlaying) Destroy(texture); else DestroyImmediate(texture); texture = null; }
            gameObject.SetActive(false);
        }
        private void OnDestroy() => Release();
    }
}
