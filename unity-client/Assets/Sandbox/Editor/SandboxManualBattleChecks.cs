#if UNITY_EDITOR
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    public static class SandboxManualBattleChecks
    {
        private static int checks;
        private static void Check(bool okay, string name) { if (!okay) throw new InvalidOperationException("[ManualBattleChecks] " + name); checks++; }
        private static void Set(object owner, string name, object value) => owner.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(owner, value);
        private static object Call(object owner, string name, params object[] args) => owner.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(owner, args);
        private static SandboxServerUnit Unit(string id, string type = "ground", string side = "blue") => new SandboxServerUnit {
            unit_id = id, life_id = id + "-life", unit_type = type, side = side, name = id,
            durability = 84, max_durability = 100, energy = 73, ammo = 59, max_ammo = type == "ground" ? 60 : 0,
            speed_m_per_minute = 400, current_speed_m_per_minute = 100,
            position = new SandboxServerPosition { x = "10", y = "20" }, interaction_action = "attack", interaction_target_id = "R-G01"
        };
        public static void Run()
        {
            checks = 0;
            var go = new GameObject("IsolatedManualBattleChecks");
            try
            {
                var hud = go.AddComponent<SandboxHudController>(); var client = go.AddComponent<SandboxServerClient>();
                var unit = Unit("B-G01"); unit.position.coordinate_system_id = "unity-demo-120x80-v1"; var enemy = Unit("R-G01", side: "red"); enemy.position.coordinate_system_id = "unity-demo-120x80-v1";
                var point = new SandboxServerPosition { x = "10.25", y = "20.5" };
                string[] requests = {
                    SandboxBattleDraftPayload.Movement(1, unit, point).ToJson(), SandboxBattleDraftPayload.Movement(1, unit).ToJson(),
                    SandboxBattleDraftPayload.Task(1, unit, "stop").ToJson(), SandboxBattleDraftPayload.Task(1, unit, "takeoff").ToJson(),
                    SandboxBattleDraftPayload.Task(1, unit, "land").ToJson(), SandboxBattleDraftPayload.TargetTask(1, unit, "attack", enemy).ToJson(),
                    SandboxBattleDraftPayload.TargetTask(1, unit, "recon", enemy).ToJson(), SandboxBattleDraftPayload.Area(1, unit, point).ToJson(),
                    SandboxBattleDraftPayload.Area(1, unit, point, 250).ToJson(), SandboxBattleDraftPayload.Service(1, unit, enemy, "energy", "10", false).ToJson(),
                    SandboxBattleDraftPayload.Service(1, unit, enemy, "ammo", "60", true).ToJson(), SandboxBattleDraftPayload.Service(1, unit, enemy, "repair", "10.5", false).ToJson(),
                    SandboxBattleDraftPayload.Camp(1, unit, "B-C01").ToJson()
                };
                foreach (string json in requests)
                {
                    Check(!json.Contains(":null") && !json.Contains("\"resource\":\"\""), "minimal payload never emits optional null fields");
                    var parsed = JsonUtility.FromJson<DraftEnvelope>(json);
                    Check(parsed.round_number == 1 && parsed.intents.Length == 1 && parsed.intents[0].life_id == unit.life_id, "payload identity round-trips");
                }
                string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../../.local/manual-battle-20261114"));
                Directory.CreateDirectory(directory); File.WriteAllLines(Path.Combine(directory, "unity-draft-contracts.jsonl"), requests);
                bool rejected = false; try { SandboxBattleDraftPayload.Task(1, unit, "attack"); } catch (ArgumentException) { rejected = true; }
                Check(rejected, "parameterized tasks cannot be emitted as empty actions");
                var root = new VisualElement();
                var view = new SandboxServerView {
                    role = "blue", game = new SandboxServerGame { title = "Isolated manual UI" },
                    decisions = new SandboxServerDecisions(), tasks = Array.Empty<SandboxServerTask>(), timeline = Array.Empty<SandboxServerRecord>(),
                    context = new SandboxServerContext { game_id = "isolated", branch_id = "isolated" },
                    clock = new SandboxServerClock { phase = "preparing" }, map = new SandboxServerMap { map_id = "", content_hash = "", coordinate_system_id = "unity-demo-120x80-v1" },
                    capabilities = new SandboxServerCapabilities(),
                    units = new[] { unit, Unit("B-R01", "recon"), Unit("B-S01", "support") }, contacts = new[] { enemy },
                    camps = new[] { new SandboxServerCamp { camp_id = "B-C01", side = "blue", position = new SandboxServerPosition { x = "10", y = "20", coordinate_system_id = "unity-demo-120x80-v1" } } }, conditions = Array.Empty<SandboxServerCondition>(),
                    manual_battle = new SandboxManualBattleView { stage = "planning", round_number = 1, can_edit = true, can_submit = true,
                        engine_ready = true, submitted = new SandboxBattleSubmitted(), own_drafts = Array.Empty<SandboxBattleIntentView>() }
                };
                // 实际服务的新手动模式不发布旧own_plan；必须通过完整同步校验才能显示。
                string gameId = Guid.NewGuid().ToString();
                view.schema_version = "game-view-v1"; view.game.game_id = gameId;
                view.context.game_id = gameId; view.context.branch_id = Guid.NewGuid().ToString();
                view.context.snapshot_id = Guid.NewGuid().ToString(); view.context.context_token = "isolated-token";
                view.clock.ready = new SandboxServerReady();
                foreach (var own in view.units) own.position.coordinate_system_id = "unity-demo-120x80-v1";
                view.manual_battle.schema_version = "qinglan-manual-planning-v1"; view.manual_battle.mode = "manual";
                view.manual_battle.round_seconds = 60; view.manual_battle.maximum_rounds = 60;
                Check(SandboxServerProjection.Validate(view, gameId, "", "") == "", "actual manual side view accepts absent legacy own_plan");
                view.manual_battle.elapsed_seconds = 5;
                Check(SandboxServerProjection.Validate(view, gameId, "", "").Contains("时钟"), "planning clock must match complete 60-second rounds");
                view.manual_battle.elapsed_seconds = 0; view.manual_battle.can_resolve = true;
                Check(SandboxServerProjection.Validate(view, gameId, "", "").Contains("权限"), "red/blue view cannot claim resolve capability");
                view.manual_battle.can_resolve = false;
                string wire = JsonUtility.ToJson(view).Replace("\"decisions\":" + JsonUtility.ToJson(view.decisions),
                    "\"decisions\":{\"source\":\"local_manual\",\"waiting_sides\":[]}");
                Check(!wire.Contains("\"own_plan\""), "wire fixture omits own_plan like real HTTP");
                var decoded = JsonUtility.FromJson<SandboxServerView>(wire);
                SandboxServerProjection.NormalizeMissingPlan(decoded, wire);
                Check(SandboxServerProjection.Validate(decoded, gameId, "", "") == "", "manual blue wire JSON survives Unity default empty plan: " + SandboxServerProjection.Validate(decoded, gameId, "", ""));
                decoded.decisions.own_plan = new SandboxServerPlan { side = "blue" };
                SandboxServerProjection.NormalizeMissingPlan(decoded, wire.Replace("\"source\":\"local_manual\"", "\"own_plan\":{},\"source\":\"local_manual\""));
                Check(SandboxServerProjection.Validate(decoded, gameId, "", "") != "", "explicit legacy plan in manual view is rejected, not normalized away");
                decoded = JsonUtility.FromJson<SandboxServerView>(wire); decoded.manual_battle = null;
                SandboxServerProjection.NormalizeMissingPlan(decoded, wire);
                Check(SandboxServerProjection.Validate(decoded, gameId, "", "") != "", "legacy blue mode still requires real own_plan");
                string legacyWire = wire.Replace(",\"manual_battle\":" + JsonUtility.ToJson(view.manual_battle), "")
                    .Replace("\"decisions\":{\"source\":\"local_manual\",\"waiting_sides\":[]}",
                        "\"decisions\":{\"source\":\"fixed_demo\",\"own_plan\":{\"side\":\"blue\"},\"waiting_sides\":[]}");
                Check(!legacyWire.Contains("\"manual_battle\""), "legacy HTTP fixture omits new manual protocol");
                decoded = JsonUtility.FromJson<SandboxServerView>(legacyWire); SandboxServerProjection.NormalizeMissingPlan(decoded, legacyWire);
                Check(SandboxServerProjection.Validate(decoded, gameId, "", "") == "", "legacy blue wire JSON remains compatible with new protocol fields: " + SandboxServerProjection.Validate(decoded, gameId, "", ""));
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, view);
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, true);
                Set(hud, "root", root); Set(hud, "serverMode", true); Set(hud, "serverClient", client); Set(hud, "role", SandboxRole.Blue);
                Set(hud, "projection", SandboxServerProjection.Convert(view));
                Call(hud, "RefreshManualBattleBar");
                var bar = root.Q("manual-battle-bar"); Check(bar != null, "manual status and round barrier visible");
                Check(bar.Query<Button>().ToList().Single(b => b.text == "锁定本方本轮指令").enabledInHierarchy, "only own side can lock");
                var report = new ScrollView(); Set(hud, "settingsContent", report);
                view.manual_battle.blue_score = "91/3"; view.manual_battle.red_score = "30";
                view.manual_battle.blue_breakdown = new SandboxBattleScoreBreakdown { control = "1/3", kills = "0", resources = "30", total = "91/3" };
                view.manual_battle.red_breakdown = new SandboxBattleScoreBreakdown { control = "0", kills = "0", resources = "30", total = "30" };
                Call(hud, "BuildResultReport");
                Check(report.Q<Label>("blueManualScore")?.text == "30.333 / 120 分", "manual result uses backend exact score and 120-point maximum");
                Check(report.Q<Label>("manualResultVerdict")?.text.Contains("不预判") == true, "planning result never declares early winner");
                Check(report.Q("redScore100") == null && !report.Query<Button>().ToList().Any(b => b.name == "confirmStageResult"), "manual result cannot use legacy 100-point/stage adjudication");
                report.Clear(); Set(hud, "resultArchiveTitle", "isolated archived result");
                var archiveResult = JsonUtility.FromJson<SandboxManualBattleView>(JsonUtility.ToJson(view.manual_battle));
                archiveResult.stage = "finished"; archiveResult.winner = "red"; archiveResult.finish_reason = "elimination";
                Set(hud, "resultManualArchive", archiveResult); Call(hud, "BuildResultReport");
                Check(report.Q<Label>("manualResultVerdict")?.text == "红方获胜", "archived manual result uses frozen verdict, not current game");
                Check(report.Q("openManualResultReplay") == null, "archive never opens unrelated current game replay");
                Set(hud, "resultArchiveTitle", null); Set(hud, "resultManualArchive", null);

                Set(hud, "controlledUnitId", unit.unit_id); Call(hud, "RefreshSelectedUnitControls");
                var panel = root.Q("selected-unit-controls");
                foreach (string label in new[] { "移动到…", "停止移动", "取消任务", "攻击目标", "范围攻击…", "兵营补充…" })
                    Check(panel.Query<Button>().ToList().Single(b => b.text == label).enabledInHierarchy, "ground manual action: " + label);
                Check(!panel.Query<Button>().ToList().Any(b => b.text == "返回兵营"), "no legacy auto-return command");
                Call(hud, "OpenManualTarget", "B-S01", "service");
                var modal = root.Q("manual-battle-overlay");
                Check(modal != null && root.IndexOf(modal) > root.IndexOf(panel), "dark modal covers bottom controls");
                Check(modal.Q<TextField>()?.value == "10" && modal.Query<DropdownField>().ToList().Count == 3, "service target, category, quantity/balance are explicit");
                Call(hud, "CloseManualBattleOverlay");
                Call(hud, "OpenManualRoundConfirmation");
                Check(root.Q("manual-battle-overlay") != null, "locking requires explicit confirmation");
                Call(hud, "CloseManualBattleOverlay");
                view.manual_battle.engine_ready = false; Call(hud, "RefreshManualBattleBar");
                Check(!bar.Query<Button>().ToList().Single(b => b.text == "锁定本方本轮指令").enabledInHierarchy, "unverified map cannot start battle");
                view.manual_battle.engine_ready = true; view.manual_battle.can_edit = false; view.manual_battle.can_submit = false;
                Call(hud, "RefreshSelectedUnitControls");
                Check(panel.Query<Button>().ToList().Where(b => b.text != "收起").All(b => !b.enabledInHierarchy), "locked side cannot edit any channel");
                view.manual_battle.can_edit = true; view.manual_battle.can_submit = true;
                Set(hud, "controlledUnitId", "B-R01"); Call(hud, "RefreshSelectedUnitControls");
                foreach (string label in new[] { "侦察目标", "侦察位置…", "起飞", "降落" })
                    Check(panel.Query<Button>().ToList().Single(b => b.text == label).enabledInHierarchy, "drone action: " + label);
                Set(hud, "controlledUnitId", "B-S01"); Call(hud, "RefreshSelectedUnitControls");
                Check(panel.Query<Button>().ToList().Any(b => b.text == "补充 / 修复单位"), "support has explicit service panel");
                Set(hud, "controlledUnitId", enemy.unit_id); Call(hud, "RefreshSelectedUnitControls");
                Check(!panel.Query<Button>().ToList().Any(b => b.text == "移动到…"), "enemy is read-only");
                Set(hud, "role", SandboxRole.Host); view.role = "host"; Set(hud, "controlledUnitId", unit.unit_id);
                view.manual_battle.can_resolve = true; view.manual_battle.can_edit = false;
                Call(hud, "RefreshManualBattleBar"); Call(hud, "RefreshSelectedUnitControls");
                Check(bar.Query<Button>().ToList().Any(b => b.text == "统一结算本轮" && b.enabledInHierarchy), "host can resolve only by server capability");
                Check(!panel.Query<Button>().ToList().Any(b => b.text == "移动到…"), "host cannot issue unit drafts");
                string text = (string)Call(hud, "ServerUnitLabel", unit.unit_id);
                Check(text.Contains("血量 84/100") && text.Contains("弹药 59/60") && text.Contains("攻击 → R-G01") && text.Contains("移速 100"), "manual labels show actual end-round resources, target and speed");
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, false); Call(hud, "RefreshManualBattleBar");
                Check(!bar.Query<Button>().ToList().Single(b => b.text == "统一结算本轮").enabledInHierarchy, "disconnected host cannot resolve");
                Check(!client.HasPending && !client.Busy && client.RecentLogs.Count == 0, "UI tests issue zero network commands");
                Debug.Log("[ManualBattleChecks] PASS " + checks + " checks; 13 minimal contract payloads; isolated UI, zero user-game writes");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }
        [Serializable] private sealed class DraftEnvelope { public int round_number; public SandboxBattleIntentView[] intents; }
    }
}
#endif
