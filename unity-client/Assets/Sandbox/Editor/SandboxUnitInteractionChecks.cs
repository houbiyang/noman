using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>无需连接真实对局的单位面板与序列化回归，不写入用户存档。</summary>
    public static class SandboxUnitInteractionChecks
    {
        private static int checks;
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private static void Check(bool value, string detail)
        {
            if (!value) throw new InvalidOperationException("Unit interaction UI: " + detail);
            checks++;
        }
        private static void Set(object target, string field, object value) => target.GetType().GetField(field, Private).SetValue(target, value);
        private static object Call(object target, string method, params object[] args) => target.GetType().GetMethod(method, Private).Invoke(target, args);
        private static SandboxServerUnit Unit(string id, string side, string kind) => new SandboxServerUnit {
            unit_id = id, name = id, side = side, unit_type = kind, life_id = "life-" + id,
            durability = 90.4, max_durability = 100, energy = 80, ammo = 58, max_ammo = 60,
            speed_m_per_minute = 400, current_speed_m_per_minute = 260, position = new SandboxServerPosition { x = "5", y = "5" }
        };
        private static bool ShouldAdvanceDeployment(SandboxServerMovementPath[] routes, string unitId, bool formation, bool resumeWaiting) =>
            (bool)typeof(SandboxHudController).GetMethod("ShouldAdvanceDeployment", BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(null, new object[] { routes, unitId, formation, resumeWaiting });
        [MenuItem("穹图/军队/验证单位交互界面")]
        public static void Run()
        {
            checks = 0;
            var go = new GameObject("IsolatedUnitInteractionChecks");
            try
            {
                var hud = go.AddComponent<SandboxHudController>();
                var client = go.AddComponent<SandboxServerClient>();
                var root = new VisualElement();
                var view = new SandboxServerView {
                    role = "red", game = new SandboxServerGame { title = "UI fixture" },
                    clock = new SandboxServerClock { phase = "paused" }, spatial_test = true,
                    unit_interactions = new SandboxServerUnitInteractions { enabled = true },
                    units = new[] { Unit("R-G01", "red", "ground"), Unit("R-S01", "red", "support") },
                    contacts = new[] { Unit("B-G01", "blue", "ground") },
                    decisions = new SandboxServerDecisions(), tasks = Array.Empty<SandboxServerTask>(),
                    camps = Array.Empty<SandboxServerCamp>(), timeline = Array.Empty<SandboxServerRecord>()
                };
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, view);
                Set(hud, "root", root); Set(hud, "serverMode", true); Set(hud, "serverClient", client); Set(hud, "role", SandboxRole.Red);
                var unit = SandboxServerProjection.Convert(view).units.First();
                Check(Mathf.Abs(unit.health - 90.4f) < .001f && unit.maxHealth == 100, "real fractional HP projected");
                Check(SandboxServerProjection.Convert(view).units.Length == 3, "only own units plus current contacts projected");
                string resources = (string)Call(hud, "UnitResourceText", unit);
                Check(resources.Contains("90.4") && resources.Contains("400") && resources.Contains("58") && resources.Contains("80") && resources.Contains("260"), "HP, base speed, ammo, energy in resource/hover text");
                foreach (var pair in new[] { (SandboxUnitType.Recon, "侦察目标"), (SandboxUnitType.Ground, "攻击目标"), (SandboxUnitType.Support, "补充单位 / 维修") })
                {
                    unit.type = pair.Item1;
                    var card = new VisualElement(); Call(hud, "AddUnitInteractionActions", card, unit);
                    Check(card.Query<Button>().ToList().Any(b => b.text == pair.Item2 && b.enabledSelf), "correct action for " + pair.Item1);
                    view.clock.phase = "preparing";
                    card.Clear(); Call(hud, "AddUnitInteractionActions", card, unit);
                    Check(card.Query<Button>().ToList().All(b => !b.enabledSelf), "preparing disables actions");
                    view.clock.phase = "paused";
                }
                Call(hud, "OpenUnitTargetPanel", unit, "attack");
                Check(root.Q<DropdownField>().choices.SequenceEqual(new[] { "B-G01 · B-G01" }), "attack targets only current enemies");
                Call(hud, "OpenUnitTargetPanel", unit, "resupply");
                var menus = root.Query<DropdownField>().ToList();
                Check(menus.Count == 2 && menus[0].choices.SequenceEqual(new[] { "R-S01 · R-S01" }), "service targets only other own units");
                Check(menus[1].choices.SequenceEqual(new[] { "能源", "弹药", "维修耐久" }), "all service resources exposed");
                view.contacts = Array.Empty<SandboxServerUnit>();
                Call(hud, "OpenUnitTargetPanel", unit, "attack");
                Check(!root.Query<Button>().ToList().Single(b => b.text == "下达任务").enabledSelf, "no target disables submit");
                Call(hud, "OpenUnitTargetPanel", unit, "recon");
                Check(root.Query<Button>().ToList().Any(b => b.text == "在地图指定侦察区域" && b.enabledSelf), "recon region remains available without contacts");
                string simple = JsonUtility.ToJson(new SandboxServerUnitOrder { unit_id = "R-G01", life_id = "life", action = "stop" });
                Check(!simple.Contains("target_id"), "targetless command omits target fields instead of invalid empty strings");
                string targeted = JsonUtility.ToJson(new SandboxServerTargetUnitOrder { unit_id = "R-G01", life_id = "life", action = "attack", target_id = "B-G01", target_life_id = "enemy-life" });
                Check(targeted.Contains("enemy-life") && targeted.Contains("R-G01"), "inherited targeted command includes both life identities");
                unit.maxHealth = 0; unit.hasCombatResources = false;
                resources = (string)Call(hud, "UnitResourceText", unit);
                Check(resources.Contains("未提供") && resources.Contains("未接入"), "old games do not invent health or ammo");
                var waitingRoute = new SandboxServerMovementPath { unit_id = "B-G01", formation_leader_id = "B-G01", status = "waiting" };
                var movingRoute = new SandboxServerMovementPath { unit_id = "B-G02", formation_leader_id = "B-G01", status = "moving" };
                Check(!ShouldAdvanceDeployment(new[] { waitingRoute }, "B-G01", false, false), "waiting does not auto-submit again");
                Check(ShouldAdvanceDeployment(new[] { waitingRoute }, "B-G01", false, true), "explicit resume allows one retry");
                Check(!ShouldAdvanceDeployment(new[] { waitingRoute }, "B-G01", true, false), "stalled formation does not loop");
                Check(!ShouldAdvanceDeployment(new[] { waitingRoute, movingRoute }, "B-G01", true, false), "blocked leader pauses the formation");
                movingRoute.status = "queued";
                Check(!ShouldAdvanceDeployment(new[] { waitingRoute, movingRoute }, "B-G01", true, false), "queued follower cannot cause infinite retries");
                Check(ShouldAdvanceDeployment(new[] { waitingRoute, movingRoute }, "B-G01", true, true), "explicit retry can resume a blocked formation");
                Check(!ShouldAdvanceDeployment(new[] { movingRoute }, "B-G01", false, false), "another unit does not keep this unit running");
                movingRoute.status = "arrived";
                Check(!ShouldAdvanceDeployment(new[] { movingRoute }, "B-G01", true, true), "resume never advances completed routes");
                Check(!ShouldAdvanceDeployment(null, "B-G01", false, true), "missing route stops auto movement");
                root.Add(new Label { name = "feedbackLabel" });
                view.clock.phase = "preparing"; view.movement_paths = new[] { waitingRoute };
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, true);
                Set(hud, "deploymentMovingUnit", "B-G01"); Set(hud, "deploymentMovingFormation", false);
                Set(hud, "nextDeploymentMovement", -1f);
                for (int i = 0; i < 20; i++) Call(hud, "UpdateDeploymentMovement");
                Check(typeof(SandboxHudController).GetField("deploymentMovingUnit", Private).GetValue(hud) == null, "actual update stops blocked-unit loop");
                Check(!client.HasPending && !client.Busy && client.RecentLogs.Count == 0, "blocked update submits zero commands");
                Check(root.Q<Label>("feedbackLabel").text.Contains("已暂停自动试走"), "user sees the blocked movement explanation");
                Debug.Log("[UnitInteractionChecks] PASS " + checks + " checks; isolated UI, no user game writes");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }
    }
}
