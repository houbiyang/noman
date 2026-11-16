using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>底部指令台的隔离UI验证；不点击网络命令、不读取用户凭据。</summary>
    public static class SandboxSelectedUnitChecks
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private static int checks;
        private static void Set(object target, string field, object value) => target.GetType().GetField(field, Private).SetValue(target, value);
        private static object Call(object target, string method, params object[] args) => target.GetType().GetMethod(method, Private).Invoke(target, args);
        private static void Check(bool value, string detail)
        {
            if (!value) throw new InvalidOperationException("Selected unit controls: " + detail);
            checks++;
        }
        private static SandboxServerUnit Unit(string id, string side) => new SandboxServerUnit {
            unit_id = id, name = id, side = side, unit_type = "ground", life_id = id + "-life",
            durability = 84, max_durability = 100, energy = 73, ammo = 59, max_ammo = 60,
            speed_m_per_minute = 400, position = new SandboxServerPosition { x = "10", y = "10" },
            interaction_action = "attack", interaction_target_id = "B-G01"
        };
        [MenuItem("穹图/军队/验证底部指令台")]
        public static void Run()
        {
            checks = 0;
            var go = new GameObject("IsolatedSelectedUnitChecks");
            try
            {
                var hud = go.AddComponent<SandboxHudController>();
                var client = go.AddComponent<SandboxServerClient>();
                var root = new VisualElement();
                var view = new SandboxServerView {
                    role = "red", game = new SandboxServerGame { title = "UI fixture" },
                    clock = new SandboxServerClock { phase = "paused" }, spatial_test = true,
                    capabilities = new SandboxServerCapabilities { can_move_unit = true, can_test_movement = true },
                    unit_interactions = new SandboxServerUnitInteractions { enabled = true },
                    units = new[] { Unit("R-G01", "red") }, contacts = new[] { Unit("B-G01", "blue") },
                    conditions = Array.Empty<SandboxServerCondition>(), decisions = new SandboxServerDecisions(),
                    tasks = Array.Empty<SandboxServerTask>(), camps = Array.Empty<SandboxServerCamp>(),
                    timeline = Array.Empty<SandboxServerRecord>()
                };
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, view);
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, true);
                Set(hud, "root", root); Set(hud, "serverMode", true); Set(hud, "serverClient", client);
                Set(hud, "role", SandboxRole.Red); Set(hud, "projection", SandboxServerProjection.Convert(view));
                Call(hud, "RefreshSelectedUnitControls");
                Check(root.Q("selected-unit-controls") == null, "no implicit controls for default selection");
                Set(hud, "controlledUnitId", "R-G01"); Call(hud, "RefreshSelectedUnitControls");
                var panel = root.Q("selected-unit-controls");
                Check(panel is ScrollView, "bottom controls are scrollable");
                Check(panel.ClassListContains("ui-blocker"), "pointer does not fall through to map");
                string texts = string.Join("\n", panel.Query<Label>().ToList().Select(item => item.text));
                Check(texts.Contains("84") && texts.Contains("73") && texts.Contains("59"), "authoritative resources visible");
                foreach (string label in new[] { "移动到…", "停止移动", "返回兵营", "攻击目标", "兵营补充", "取消任务" })
                    Check(panel.Query<Button>().ToList().Single(button => button.text == label).enabledInHierarchy, "enabled own action: " + label);
                Check(texts.Contains("旧版单位交互"), "legacy mode cannot masquerade as new rulebook mode");
                var overlay = new VisualElement { name = "test-modal-backdrop" }; root.Add(overlay);
                Call(hud, "RefreshSelectedUnitControls");
                Check(root.IndexOf(overlay) > root.IndexOf(panel), "refresh does not lift controls over a modal");
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, false);
                Call(hud, "RefreshSelectedUnitControls");
                Check(panel.Query<Button>().ToList().Where(button => button.text == "攻击目标" || button.text == "移动到…")
                    .All(button => !button.enabledInHierarchy), "offline controls cannot submit");
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, true);
                Set(hud, "controlledUnitId", "B-G01"); Call(hud, "RefreshSelectedUnitControls");
                Check(!panel.Query<Button>().ToList().Any(button => button.text == "攻击目标" || button.text == "移动到…"), "enemy contacts are read-only");
                string labelText = (string)Call(hud, "ServerUnitLabel", "R-G01");
                Check(labelText.Contains("血量 84/100") && labelText.Contains("弹药 59/60") && labelText.Contains("攻击 → B-G01"), "unit label shows resources and visible target");
                view.units[0].interaction_target_id = "";
                Check(((string)Call(hud, "ServerUnitLabel", "R-G01")).Contains("目标当前不可见"), "hidden target never guessed");
                Set(hud, "controlledUnitId", "missing-unit"); Call(hud, "RefreshSelectedUnitControls");
                Check(root.Q("selected-unit-controls") == null, "removed unit closes controls");
                Set(hud, "controlledUnitId", "R-G01"); Call(hud, "RefreshSelectedUnitControls");
                Call(hud, "CloseSelectedUnitControls");
                Check(root.Q("selected-unit-controls") == null, "explicit close removes controls");
                Check(!client.HasPending && !client.Busy && client.RecentLogs.Count == 0, "all checks issue zero network commands");
                Debug.Log("[SelectedUnitChecks] PASS " + checks + " checks; isolated UI, zero user-game writes");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }
    }
}
