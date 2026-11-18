#if UNITY_EDITOR
using System;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>只验证表现映射，使用明确隔离夹具；不证明真实地图动作合法。</summary>
    public static class SandboxRecordedEffectsChecks
    {
        [Serializable] private sealed class Fixture { public SandboxReplayRecord[] records; }
        private static int checks;
        private static void Check(bool ok, string message)
        { if (!ok) throw new InvalidOperationException("Recorded effects: " + message); checks++; }
        [MenuItem("Qiongtu/Checks/Recorded authority effects")]
        public static void Run()
        {
            checks = 0;
            var text = AssetDatabase.LoadAssetAtPath<TextAsset>("Assets/Sandbox/Editor/Fixtures/manual-replay-rounds.json");
            foreach (string receiverKind in new[] { "ground", "recon" })
            {
                var records = JsonUtility.FromJson<Fixture>(text.text).records;
                foreach (var world in records.SelectMany(r => new[] { r.initial_world }.Concat(r.frames.Select(f => f.world))))
                {
                    world.units[0].unit_type = receiverKind;
                    world.units[0].docked = receiverKind == "recon";
                    world.units[1].unit_type = "support";
                }
                var frame = records[0].frames[0];
                var receiver = frame.world.units[0]; var provider = frame.world.units[1];
                var services = new[] { "energy", "ammo", "repair" }.Select(resource => new SandboxReplayEvent
                {
                    event_id = "isolated-" + receiverKind + "-" + resource, kind = "service", at_seconds = 5,
                    source_id = provider.unit_id, source_life_id = provider.life_id,
                    target_id = receiver.unit_id, target_life_id = receiver.life_id,
                    source_position = provider.position, target_position = receiver.position,
                    resource = resource, amount = "1"
                }).ToArray();
                frame.events = frame.events.Concat(services).ToArray();
                string original = JsonUtility.ToJson(new Fixture { records = records });
                var cursor = new SandboxRoundReplayCursor(records);
                var go = new GameObject("IsolatedRecordedServiceChecks");
                try
                {
                    var stage = go.AddComponent<SandboxRoundReplayStage>(); stage.Initialize(null);
                    cursor.Seek(4.99); stage.Sample(cursor);
                    Check(stage.ActiveServiceConnectionCount == 0, receiverKind + " no delivery animation before actual event");
                    cursor.Seek(5.5); stage.Sample(cursor);
                    Check(stage.ActiveServiceConnectionCount == 1, receiverKind + " three resource entries share one connection");
                    var beam = go.GetComponentsInChildren<LineRenderer>().Single(l => l.gameObject.name.StartsWith("Recorded service"));
                    Check(Mathf.Abs(beam.startColor.r - .3f) < .01f && Mathf.Abs(beam.startColor.g - 1) < .01f && Mathf.Abs(beam.startColor.b - .74f) < .01f, receiverKind + " unified service color");
                    var shaderTint = new MaterialPropertyBlock(); beam.GetPropertyBlock(shaderTint);
                    Check(Mathf.Abs(shaderTint.GetColor("_BaseColor").g - 1) < .01f && Mathf.Abs(shaderTint.GetColor("_BaseColor").r - .3f) < .01f,
                        receiverKind + " actual Glow shader property carries resource color");
                    Vector3 endpoint = new Vector3(receiver.position.x_mm, receiver.position.height_mm, receiver.position.z_mm) / 1000f + Vector3.up * 2;
                    Check(Vector3.Distance(beam.GetPosition(1), endpoint) < .001f, receiverKind + " actual recorded target endpoint");
                    int pool = stage.ParticlePoolSize; stage.Sample(cursor);
                    Check(stage.ActiveServiceConnectionCount == 1 && stage.ParticlePoolSize == pool, receiverKind + " resampling is idempotent and pooled");
                    cursor.Seek(7); stage.Sample(cursor);
                    Check(stage.ActiveServiceConnectionCount == 0 && !beam.gameObject.activeSelf, receiverKind + " cosmetic connection expires without fabricated departure");
                    cursor.Seek(0); stage.Sample(cursor);
                    Check(stage.ActiveServiceConnectionCount == 0, receiverKind + " rewind removes future delivery");
                    Check(JsonUtility.ToJson(new Fixture { records = records }) == original, receiverKind + " original authority records unchanged");
                    Check(cursor.Events.Count(e => e.kind == "service") == 3, receiverKind + " individual resource facts are preserved");
                    stage.Release();
                }
                finally { UnityEngine.Object.DestroyImmediate(go); }
            }
            CheckFlyingReceiver(text);
            CheckCombat(text);
            Debug.Log("[RecordedEffectsChecks] PASS " + checks + " checks; isolated fixtures, no network or user-game writes");
        }

        private static Vector3 RecordedPosition(SandboxReplayPosition p)
            => new Vector3(p.x_mm, p.height_mm, p.z_mm) / 1000f;
        private static GameObject[] ActiveParticles(GameObject stage) => stage.transform.Cast<Transform>()
            .Where(t => t.gameObject.activeSelf && t.name.StartsWith("Recorded cosmetic particle"))
            .Select(t => t.gameObject).ToArray();
        private static SandboxReplayRecord[] Decode(string json) => JsonUtility.FromJson<Fixture>(json).records;
        private static SandboxRoundReplayCursor[] BothScopes(string json) => new[]
        {
            new SandboxRoundReplayCursor(Decode(json).Skip(1).ToArray()),
            new SandboxRoundReplayCursor(Decode(json))
        };
        private static void CheckFlyingReceiver(TextAsset text)
        {
            // 隔离回放协议夹具：不同于生产验收，不创建对局或裁决补给资格。
            var records = Decode(text.text);
            foreach (var world in records.SelectMany(r => new[] { r.initial_world }.Concat(r.frames.Select(f => f.world))))
            {
                world.units[0].unit_type = "support";
                world.units[1].side = world.units[0].side;
                world.units[1].unit_type = "recon";
                world.units[1].docked = false;
                world.units[1].position.height_mm = 43217;
            }
            var frame = records[1].frames[0];
            var provider = frame.world.units[0]; var receiver = frame.world.units[1];
            var delivery = new SandboxReplayEvent
            {
                event_id = "isolated-flight-energy", kind = "service", at_seconds = frame.at_seconds,
                source_id = provider.unit_id, source_life_id = provider.life_id,
                target_id = receiver.unit_id, target_life_id = receiver.life_id,
                source_position = provider.position, target_position = receiver.position,
                resource = "energy", amount = "2"
            };
            // 空中服务夹具不引入无关、与修改后单位角色冲突的战斗事件。
            foreach (var f in records.SelectMany(r => r.frames))
                f.events = f.events.Where(e => e.kind == "move").ToArray();
            frame.events = new[] { delivery };
            var json = JsonUtility.ToJson(new Fixture { records = records });
            var go = new GameObject("IsolatedRecordedAirborneServiceChecks");
            try
            {
                var stage = go.AddComponent<SandboxRoundReplayStage>(); stage.Initialize(null);
                foreach (var cursor in BothScopes(json))
                {
                    var scope = cursor.FirstSeconds == 0 ? "cumulative" : "single";
                    cursor.Seek(64.99); stage.Sample(cursor);
                    Check(stage.ActiveServiceConnectionCount == 0 && ActiveParticles(go).Length == 0, scope + " no invented air delivery");
                    cursor.Seek(65.5); stage.Sample(cursor);
                    var fact = cursor.Events.Single(e => e.kind == "service");
                    var beam = go.GetComponentsInChildren<LineRenderer>().Single(l => l.name.StartsWith("Recorded service"));
                    Check(stage.ActiveServiceConnectionCount == 1 && ActiveParticles(go).Length == 4, scope + " recorded air delivery drives one beam and flow particles");
                    Check(Vector3.Distance(beam.GetPosition(0), RecordedPosition(fact.source_position) + Vector3.up * 2) < .001f,
                        scope + " actual recorded provider position");
                    Check(Vector3.Distance(beam.GetPosition(1), RecordedPosition(fact.target_position) + Vector3.up * 2) < .001f
                        && Mathf.Abs(beam.GetPosition(1).y - 45.217f) < .001f, scope + " recorded airborne height, not a ground or nominal height");
                    var target = cursor.World.units.Single(u => u.unit_id == fact.target_id);
                    Check(!target.docked && target.position.height_mm == 43217 && fact.target_life_id == target.life_id
                        && fact.resource == "energy" && fact.amount == "2", scope + " original airborne identity and transfer facts preserved");
                    cursor.Seek(67); stage.Sample(cursor);
                    Check(!beam.gameObject.activeSelf && ActiveParticles(go).Length == 0, scope + " cosmetic expiry without landing or departure");
                    cursor.Seek(65.5); stage.Sample(cursor); cursor.Seek(64.99); stage.Sample(cursor);
                    Check(!beam.gameObject.activeSelf && ActiveParticles(go).Length == 0, scope + " backward seek clears future delivery");
                }
                Check(JsonUtility.ToJson(new Fixture { records = records }) == json, "air delivery leaves original records unchanged");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }

        private static void CheckCombat(TextAsset text)
        {
            var records = Decode(text.text);
            var frame = records[1].frames.Single(f => f.at_seconds == 70);
            var hit = frame.events.Single(e => e.kind == "hit");
            var shot = JsonUtility.FromJson<SandboxReplayEvent>(JsonUtility.ToJson(hit));
            shot.kind = "shot"; shot.event_id = "isolated-round-two-shot";
            frame.events = new[] { shot, hit, new SandboxReplayEvent
            {
                event_id = "isolated-round-two-retired", kind = "retired", at_seconds = hit.at_seconds,
                source_id = hit.target_id, source_life_id = hit.target_life_id
            } };
            foreach (var f in records[1].frames.Where(f => f.at_seconds >= hit.at_seconds))
            {
                var target = f.world.units.Single(u => u.unit_id == hit.target_id);
                target.retired = true; target.durability = "0"; target.speed_m_per_minute = "0";
            }
            var json = JsonUtility.ToJson(new Fixture { records = records });
            var go = new GameObject("IsolatedRecordedCombatChecks");
            try
            {
                var stage = go.AddComponent<SandboxRoundReplayStage>(); stage.Initialize(null);
                foreach (var cursor in BothScopes(json))
                {
                    var scope = cursor.FirstSeconds == 0 ? "cumulative" : "single";
                    void Sample(double time) { cursor.Seek(time); stage.Sample(cursor); }
                    SandboxCombatActor Actor(string id) => go.transform.Find("Recorded " + id).GetComponentsInChildren<SandboxCombatActor>().Single();
                    var update = typeof(SandboxCombatActor).GetMethod("LateUpdate",
                        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
                    Sample(69.99);
                    var source = Actor(shot.source_id); update.Invoke(source, null);
                    var yaw = source.transform.Find("TurretYaw");
                    var recoil = yaw?.Find("GunPitch/GunRecoil");
                    Check(recoil != null, scope + " artillery prefab has a recoil pivot");
                    var beforeYaw = yaw.localRotation;
                    var beforeRecoil = recoil.localPosition;
                    Check(ActiveParticles(go).Length == 0 && !Actor(hit.target_id).Destroyed, scope + " no combat effect or loss before recorded time");
                    Check(stage.ActiveShotTrailCount == 0, scope + " no future shot trails");
                    Sample(70.1);
                    Check(stage.ActiveShotTrailCount == 1, scope + " recorded shot produces one pooled trail");
                    var a = RecordedPosition(shot.source_position) + Vector3.up * 2;
                    var b = RecordedPosition(shot.target_position) + Vector3.up * 2;
                    var effects = ActiveParticles(go);
                    Check(effects.Length == 3 && effects.Any(p => Vector3.Distance(p.transform.position, a) < .001f)
                        && effects.Any(p => Vector3.Distance(p.transform.position, Vector3.Lerp(a, b, .1f / .6f)) < .001f)
                        && effects.Any(p => Vector3.Distance(p.transform.position, b) < .001f), scope + " recorded muzzle, projectile and hit endpoints");
                    update.Invoke(source, null);
                    Check(Vector3.Distance(recoil.localPosition, beforeRecoil) > .01f, scope + " recorded shot drives mechanical recoil: " + beforeRecoil + " -> " + recoil.localPosition);
                    Check(Actor(hit.target_id).Destroyed && cursor.World.units.Single(u => u.unit_id == hit.target_id).retired,
                        scope + " recorded target retirement shown");
                    Sample(72); Check(stage.ActiveShotTrailCount == 0, scope + " expired shot trail hidden"); Check(ActiveParticles(go).Length == 0 && Actor(hit.target_id).Destroyed,
                        scope + " combat particles expire without undoing authoritative loss");
                    Sample(69.99); Check(stage.ActiveShotTrailCount == 0, scope + " backward seek clears shot trail"); update.Invoke(source, null);
                    Check(ActiveParticles(go).Length == 0 && !Actor(hit.target_id).Destroyed
                        && Quaternion.Angle(yaw.localRotation, beforeYaw) < .001f
                        && Vector3.Distance(recoil.localPosition, beforeRecoil) < .001f, scope + " backward seek restores prior shot, hit and retirement pose");
                }
                Check(JsonUtility.ToJson(new Fixture { records = records }) == json, "combat sampling leaves original records unchanged");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }

    }
}
#endif
