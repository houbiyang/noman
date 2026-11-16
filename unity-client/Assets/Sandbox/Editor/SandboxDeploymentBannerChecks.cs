using System;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>独立内存夹具，不访问用户真实对局，不发送任何命令。</summary>
    public static class SandboxDeploymentBannerChecks
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private static int checks;
        private static void Check(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException("Deployment banner: " + message);
            checks++;
        }
        private static void Set(object target, string name, object value) =>
            target.GetType().GetField(name, Private).SetValue(target, value);
        private static void Refresh(SandboxHudController hud) =>
            typeof(SandboxHudController).GetMethod("RefreshDeploymentStageBanner", Private).Invoke(hud, null);

        [MenuItem("穹图/战斗/验证常驻部署提示")]
        public static void Run()
        {
            checks = 0;
            var go = new GameObject("IsolatedDeploymentBannerChecks");
            try
            {
                var hud = go.AddComponent<SandboxHudController>();
                var client = go.AddComponent<SandboxServerClient>();
                var tree = Resources.Load<VisualTreeAsset>("Sandbox/SandboxHud");
                Check(tree != null, "HUD asset loads");
                var root = tree.CloneTree();
                var banner = root.Q("deploymentStageBanner");
                Check(banner != null, "dedicated banner exists");
                Check(banner.pickingMode == PickingMode.Ignore, "banner does not block map input");
                Check(banner.Q<Button>() == null, "banner has no close control");
                Set(hud, "root", root); Set(hud, "serverClient", client); Set(hud, "serverMode", true);
                var view = new SandboxServerView { clock = new SandboxServerClock { phase = "preparing" } };
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, view);
                Refresh(hud);
                Check(!banner.ClassListContains("hidden"), "preparing shows banner");
                Check(root.Q<Label>("deploymentStageTitle").text == "部署阶段", "preparing title");
                foreach (float timer in new[] { -1000f, 0f, 100000f })
                {
                    Set(hud, "feedbackUntil", timer);
                    Set(hud, "activePanel", "settings");
                    Refresh(hud);
                    Check(!banner.ClassListContains("hidden"), "feedback timer and drawer cannot hide deployment stage");
                }
                foreach (string phase in new[] { "paused", "running", "awaiting_confirmation", "finished" })
                {
                    view.clock.phase = phase; Refresh(hud);
                    Check(banner.ClassListContains("hidden"), "non-deployment phase does not claim deployment: " + phase);
                }
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, null);
                Refresh(hud);
                Check(!banner.ClassListContains("hidden"), "missing view shows persistent connection state");
                Check(root.Q<Label>("deploymentStageTitle").text == "阶段待确认", "unknown state is not reported as known deployment");
                view.clock.phase = "preparing";
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, view);
                Refresh(hud);
                Check(root.Q<Label>("deploymentStageTitle").text == "部署阶段", "connection recovery restores authoritative stage");
                Set(hud, "serverMode", false); Refresh(hud);
                Check(!banner.ClassListContains("hidden"), "fresh offline preview shows deployment notice");
                Check(root.Q<Label>("deploymentStageDetail").text.Contains("离线预览"), "offline preview is labeled honestly");
                Check(!client.HasPending && !client.Busy, "checks send no commands");
                Debug.Log("[DeploymentBannerChecks] PASS " + checks + " checks; no user game writes");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
        }
    }
}
