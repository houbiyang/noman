using System;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>内存UI夹具；无模型调用、无用户对局命令或数据库写入。</summary>
    public static class SandboxBattleEntryChecks
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private static int checks;
        private static void Check(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException("Battle entry: " + message);
            checks++;
        }
        private static void Set(object target, string name, object value) => target.GetType().GetField(name, Private).SetValue(target, value);
        private static object Call(object target, string name, params object[] args) => target.GetType().GetMethod(name, Private).Invoke(target, args);

        [MenuItem("穹图/战斗/验证模式选择和只读检查")]
        public static void Run()
        {
            checks = 0;
            var go = new GameObject("IsolatedBattleEntryChecks");
            try
            {
                var hud = go.AddComponent<SandboxHudController>();
                var client = go.AddComponent<SandboxServerClient>();
                var world = go.AddComponent<SandboxWorld>();
                var ring = new GameObject("IsolatedSelectionRing"); ring.transform.parent = go.transform;
                Set(world, "barracksSelectionRing", ring.AddComponent<LineRenderer>());
                var root = Resources.Load<VisualTreeAsset>("Sandbox/SandboxHud").CloneTree();
                Set(hud, "root", root); Set(hud, "world", world); Set(hud, "drawerContent", root.Q<ScrollView>("drawerContent"));
                Set(hud, "serverClient", client); Set(hud, "serverMode", true);
                var view = new SandboxServerView { role = "host", clock = new SandboxServerClock { phase = "preparing" } };
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, view);
                Check(Resources.Load<StyleSheet>("Sandbox/SandboxBattle") != null, "battle stylesheet loads");
                Call(hud, "InitializeBattleEntry"); Call(hud, "OpenBattleEntry");
                Check(root.Q("battleOverlay") != null, "battle button has a modal destination");
                Check(root.Q("battleOverlay").ClassListContains("ui-blocker"), "modal blocks map input");
                Check(root.Q<Button>("battleModeAgent") != null && root.Q<Button>("battleModeManual") != null, "both modes exist");
                Check(root.Q<Button>("battleModeAgent").ClassListContains("battle-mode-selected"), "agent selected initially");
                Check(!root.Q<Toggle>("battleProbeAi").value, "paid calls default to no consent");
                Check(!root.Q<Button>("battleStartButton").enabledSelf, "unimplemented engine cannot be started");
                Call(hud, "SelectBattleMode", "manual");
                Check(root.Q<Button>("battleModeManual").ClassListContains("battle-mode-selected"), "manual selection is visible");
                Check(!root.Q<Button>("battleModeAgent").ClassListContains("battle-mode-selected"), "selection is exclusive");
                Check(!client.HasPending && !client.Busy, "selecting modes sends no game or model command");
                Call(hud, "CheckBattleDependencies"); // Healthy=false：本地失败，不联网。
                Check(root.Q<Label>("battleCheckStatus").text.Contains("未发送检查"), "unready connection has an explicit error");
                var result = new SandboxBattlePreflightResult { mode = "manual", ready = true, can_start = true, read_only = true,
                    checks = new[] {
                        new SandboxBattleDependencyCheck { name = "数据库", status = "passed", message = "通过" },
                        new SandboxBattleDependencyCheck { name = "AI", status = "not_tested", message = "没有联网验证" },
                        new SandboxBattleDependencyCheck { name = "执行器", status = "failed", message = "尚未接入" }
                    } };
                Call(hud, "CompleteBattleCheck", result, null);
                Check(root.Q<ScrollView>("battleChecks").contentContainer.childCount == 3, "all checks rendered");
                Check(root.Q<Label>("battleCheckStatus").text.Contains("未通过或未测试"), "unknown is not called connected");
                Check(!root.Q<Button>("battleStartButton").enabledSelf, "even bad response cannot enable old start command");
                Call(hud, "CompleteBattleCheck", null, "后端暂未完成请求");
                Check(root.Q<Label>("battleCheckStatus").text == "后端暂未完成请求", "server errors stay in modal");
                Call(hud, "CloseBattleEntry");
                Check(root.Q("battleOverlay") == null, "close removes overlay");
                view.role = "blue"; Call(hud, "OpenBattleEntry");
                Check(!root.Q<Button>("battleCheckButton").enabledSelf, "side cannot inspect both private configurations");
                Check(root.Q<Label>("battleCheckStatus").text.Contains("主持人"), "role requirement explained");
                Check(!client.HasPending && !client.Busy, "checks never send user game writes");
                Call(hud, "CloseBattleEntry");
                Debug.Log("[BattleEntryChecks] PASS " + checks + " checks; no network or user game writes");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }
    }
}
