#if UNITY_EDITOR
using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>隔离编辑器夹具：只测历史时钟/界面，不接入用户对局、不伪造可玩的正式新局。</summary>
    public static class SandboxRoundReplayChecks
    {
        [Serializable] private sealed class Fixture { public SandboxReplayRecord[] records; }
        private static int checks;
        private static void Check(bool ok, string message) { if (!ok) throw new InvalidOperationException("Replay check failed: " + message); checks++; }
        private static void Set(object target, string field, object value) => target.GetType().GetField(field, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(target, value);
        private static object Get(object target, string field) => target.GetType().GetField(field, BindingFlags.NonPublic | BindingFlags.Instance).GetValue(target);
        private static object Call(object target, string method, params object[] args) => target.GetType().GetMethod(method, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(target, args);
        private static void Reject(Action action, string message)
        {
            bool rejected = false; try { action(); } catch (ArgumentException) { rejected = true; }
            Check(rejected, message);
        }
        [MenuItem("Qiongtu/Checks/Recorded round replay")]
        public static void Run()
        {
            checks = 0;
            var text = AssetDatabase.LoadAssetAtPath<TextAsset>("Assets/Sandbox/Editor/Fixtures/manual-replay-rounds.json");
            if (!text) throw new InvalidOperationException("缺少编辑器回放测试夹具");
            var records = JsonUtility.FromJson<Fixture>(text.text).records;
            var cursor = new SandboxRoundReplayCursor(records);
            Check(cursor.FirstSeconds == 0 && cursor.LastSeconds == 120, "two rounds retain 60-second boundaries");
            Check(cursor.World.units[1].durability == "100", "initial HP not replaced by final HP");
            cursor.Seek(9.99); Check(cursor.World.units[1].durability == "100", "future damage is not visible");
            cursor.Seek(10); Check(cursor.World.units[1].durability == "84", "damage appears at event time");
            cursor.Seek(0); Check(cursor.World.units[1].durability == "100", "backward seek restores initial facts");
            cursor.Play(); cursor.Advance(60); Check(cursor.World.elapsed_seconds == 60 && cursor.Playing, "continuous playback crosses round boundary");
            cursor.Pause(); cursor.Advance(5); Check(cursor.Seconds == 60, "pause freezes virtual clock");
            cursor.Play(); cursor.Advance(1000); Check(cursor.Seconds == 120 && !cursor.Playing, "playback stops at latest completed round");
            var single = new SandboxRoundReplayCursor(new[] { records[1] });
            Check(single.FirstSeconds == 60 && single.LastSeconds == 120, "single-round playback works without fetching all previous payloads");
            Reject(() => single.Seek(59), "seek cannot read unloaded rounds");
            Reject(() => single.Advance(double.NaN), "nonfinite delta rejected");
            foreach (string invalid in new[] { "NaN", "1/0", "-1/-2", "-1", "1e2", "1/2/3" })
                Reject(() => SandboxRoundReplayCursor.DisplayNumber(invalid), "invalid display number: " + invalid);
            Check(Math.Abs(SandboxRoundReplayCursor.DisplayNumber("492/5") - 98.4) < .00001, "fractions converted only at display boundary");
            var copied = JsonUtility.FromJson<Fixture>(text.text).records;
            var copiedCursor = new SandboxRoundReplayCursor(copied); copied[0].initial_world.units[0].durability = "0";
            copiedCursor.Seek(0); Check(copiedCursor.World.units[0].durability == "100", "input records are defensively copied");
            var bad = JsonUtility.FromJson<Fixture>(text.text).records; bad[1].previous_round_sha256 = new string('f', 64);
            Reject(() => new SandboxRoundReplayCursor(bad), "wrong predecessor rejected");
            bad = JsonUtility.FromJson<Fixture>(text.text).records; bad[1].initial_world.blue_score = "1";
            Reject(() => new SandboxRoundReplayCursor(bad), "world discontinuity rejected");
            bad = JsonUtility.FromJson<Fixture>(text.text).records; bad[1].frames[1].events[0].event_id = bad[0].frames[1].events[0].event_id;
            Reject(() => new SandboxRoundReplayCursor(bad), "cross-round duplicate events rejected");
            bad = JsonUtility.FromJson<Fixture>(text.text).records; bad[0].frames[0].world.units[0].life_id = "replacement";
            Reject(() => new SandboxRoundReplayCursor(bad), "life replacement rejected");
            var campRecords = JsonUtility.FromJson<Fixture>(text.text).records;
            var receiver = campRecords[0].frames[0].world.units[0];
            var campEvent = new SandboxReplayEvent { event_id = "isolated-camp-service", kind = "barracks_service", at_seconds = 5,
                camp_id = "B-C01", target_id = receiver.unit_id, target_life_id = receiver.life_id,
                source_position = new SandboxReplayPosition { x_mm = receiver.position.x_mm + 10000, z_mm = receiver.position.z_mm, height_mm = receiver.position.height_mm },
                target_position = receiver.position, resource = "ammo", delivery_category = "stock_ammo", amount = "2" };
            campRecords[0].frames[0].events = campRecords[0].frames[0].events.Concat(new[] { campEvent }).ToArray();
            var campCursor = new SandboxRoundReplayCursor(campRecords);
            Check(campCursor.Events.Any(e => e.kind == "barracks_service" && e.camp_id == "B-C01"), "camp replay keeps actual identity/category without fake source life");
            campEvent.source_id = receiver.unit_id;
            Reject(() => new SandboxRoundReplayCursor(campRecords), "camp cannot pretend to be unit service");
            campEvent.source_id = null; campEvent.delivery_category = "energy";
            Reject(() => new SandboxRoundReplayCursor(campRecords), "camp delivery resource/category mismatch rejected");
            var trace = records[0].frames[0].events[0].trace;
            Check(Vector3.Distance(SandboxRoundReplayStage.TracePosition(trace, 0), new Vector3(1, 0, 1)) < .001f, "trace begins at authoritative start");
            Check(Vector3.Distance(SandboxRoundReplayStage.TracePosition(trace, .5), new Vector3(3, 0, 3)) < .001f, "interpolation follows recorded corner, not straight shortcut");
            Check(Vector3.Distance(SandboxRoundReplayStage.TracePosition(trace, 1), new Vector3(6, 0, 4)) < .001f, "trace ends at authoritative position");

            var go = new GameObject("IsolatedRecordedReplayChecks");
            var stageObject = new GameObject("IsolatedReplayStageChecks");
            var cameraObject = new GameObject("IsolatedMainCameraChecks");
            cameraObject.tag = "MainCamera"; var liveCamera = cameraObject.AddComponent<Camera>(); liveCamera.cullingMask = -1;
            try
            {
                var hud = go.AddComponent<SandboxHudController>();
                var client = go.AddComponent<SandboxServerClient>();
                var root = new VisualElement(); var bar = new VisualElement(); root.Add(bar); bar.Add(new Button { name = "connectionAction" });
                Set(hud, "root", root); Set(hud, "serverMode", true); Set(hud, "serverClient", client); Set(hud, "role", SandboxRole.Host);
                var view = new SandboxServerView { role = "host", context = new SandboxServerContext { game_id = "fixture", branch_id = "one" }, manual_battle = new SandboxManualBattleView { completed_rounds = 0 } };
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, view);
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, true);
                Call(hud, "InitializeRecordedReplay");
                Check(!root.Q<Button>("recordedReplayEntry").enabledSelf, "no fake replay entry for zero completed rounds");
                view.manual_battle.completed_rounds = 2; Call(hud, "RefreshRecordedReplayEntry");
                Check(root.Q<Button>("recordedReplayEntry").enabledSelf, "host can enter completed replay");
                Call(hud, "BuildRecordedReplayPanel");
                var overlay = root.Q("recorded-replay-overlay");
                Check(overlay.ClassListContains("modal-backdrop") && overlay.ClassListContains("ui-blocker"), "large centered overlay blocks live map input");
                Check(root.Q("recorded-replay-card").ClassListContains("recorded-card"), "command-console styling applied");
                Call(hud, "RenderRecordedFacts", records[0].frames[1].world);
                string labels = string.Join("\n", root.Query<Label>().ToList().Select(l => l.text));
                Check(labels.Contains("P-01") && labels.Contains("P-05"), "all frozen points present");
                Check(labels.Contains("血量 84/100") && labels.Contains("弹药 59/60") && labels.Contains("米/分"), "historical resource labels present");
                Check(root.Q<Image>("recorded-viewport") != null && !root.Q<Button>("recordedPlay").enabledSelf, "viewport exists, playback disabled before data load");
                // 关闭与角色变化均为纯本地行为，没有发起网络读写。
                Set(hud, "recordedScope", "outdated"); Call(hud, "RefreshRecordedReplayEntry");
                Check(root.Q("recorded-replay-overlay") == null, "scope change clears private history");
                view.role = "blue"; Set(hud, "role", SandboxRole.Blue); Call(hud, "RefreshRecordedReplayEntry");
                Check(root.Q<Button>("recordedReplayEntry").style.display == DisplayStyle.None, "nonhost entry hidden");
                Check(!client.LoadBattleReplayIndex(null) && !client.LoadBattleReplayRound(1, null), "client read guard rejects nonhost");
                Check(!client.Busy && !client.HasPending && client.RecentLogs.Count == 0, "UI checks send zero network commands");
                var cameraUsed = Camera.main; int originalMask = cameraUsed.cullingMask;
                var stage = stageObject.AddComponent<SandboxRoundReplayStage>(); stage.Initialize(null);
                Check((cameraUsed.cullingMask & (1 << 31)) == 0, "live camera excludes historical clones");
                cursor.Seek(10.5); stage.Sample(cursor); int poolSize = stage.ParticlePoolSize;
                for (int i = 0; i < 20; i++) stage.Sample(cursor);
                Check(stage.ParticlePoolSize == poolSize && poolSize > 0, "repeated sample reuses effect pool");
                var actor = stageObject.GetComponentsInChildren<SandboxCombatActor>().First();
                var renderer = actor.GetComponentInChildren<Renderer>();
                actor.SampleRecorded(10.5f, new Vector3(20, 0, 20), 10, -100, -100, false);
                Call(actor, "LateUpdate");
                actor.SampleRecorded(0, null, -100, -100, -100, false); Call(actor, "LateUpdate");
                var yaw = actor.transform.Find("TurretYaw");
                Check(yaw != null && Quaternion.Angle(yaw.localRotation, Quaternion.identity) < .001f, "rewind clears future turret pose");
                Check(renderer.gameObject.layer == 31 && stage.Camera.cullingMask == (1 << 31), "stage and camera isolated from live render layer");
                stage.Release(); Check(cameraUsed.cullingMask == originalMask, "close restores original live culling mask");
                Check(!stage.Camera.enabled && !stageObject.activeSelf, "close synchronously disables replay rendering");
                Debug.Log("[RoundReplayChecks] PASS " + checks + " checks; editor-only fixture, zero user-game writes");
            }
            finally { UnityEngine.Object.DestroyImmediate(stageObject); UnityEngine.Object.DestroyImmediate(go); UnityEngine.Object.DestroyImmediate(cameraObject); }
        }
    }
}
#endif
