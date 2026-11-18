using System;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>隔离编辑器表单检查；不连接API，不发布地图，不使用真实部署。</summary>
    public static class SandboxManualSetupChecks
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private static int checks;
        private static void Check(bool ok, string text) { if (!ok) throw new InvalidOperationException("Manual setup: " + text); checks++; }
        private static void Set(object target, string name, object value) => target.GetType().GetField(name, Private).SetValue(target, value);
        private static object Call(object target, string name, params object[] args) => target.GetType().GetMethod(name, Private).Invoke(target, args);
        private static void Invalid(Action action, string text)
        {
            try { action(); } catch (TargetInvocationException e) when (e.InnerException is ArgumentException) { checks++; return; }
            throw new InvalidOperationException("Manual setup: " + text);
        }
        private static void Fill(VisualElement root)
        {
            foreach (var field in root.Query<TextField>().ToList())
            {
                if (field.name == "manualSetupTitle") continue;
                field.value = field.label == "X / 毫米" ? "1000" : field.label == "Z / 毫米" ? "2000" : field.label == "高程 / 毫米" ? "0" : "synthetic-editor-only";
            }
        }
        public static void RunLocalScopeContracts()
        {
            Run();
            SandboxManualBattleChecks.Run();
        }

        [MenuItem("穹图/战斗/验证手动新局配置")]
        public static void Run()
        {
            checks = 0;
            var go = new GameObject("IsolatedManualSetupChecks");
            PanelSettings panelSettings = null;
            try
            {
                var hud = go.AddComponent<SandboxHudController>(); var client = go.AddComponent<SandboxServerClient>();
                var document = go.AddComponent<UIDocument>(); panelSettings = ScriptableObject.CreateInstance<PanelSettings>();
                document.panelSettings = panelSettings;
                var root = document.rootVisualElement; Resources.Load<VisualTreeAsset>("Sandbox/SandboxHud").CloneTree(root);
                Set(hud, "root", root); Set(hud, "serverClient", client); Set(hud, "serverMode", true);
                var view = new SandboxServerView { role = "host", context = new SandboxServerContext { game_id = Guid.NewGuid().ToString() } };
                typeof(SandboxServerClient).GetProperty("View").SetValue(client, view);
                Call(hud, "OpenManualSetup"); // Healthy=false，不发送网络请求。
                Check(root.Q("manualSetupPanel") != null, "new game modal exists");
                Check(root.Q("battleOverlay").ClassListContains("ui-blocker"), "map input blocked");
                Check(!root.Q<Button>("manualSetupSubmit").enabledSelf, "unready connection cannot submit");
                Check(!client.Busy && !client.HasPending, "opening disconnected form creates nothing");
                Check(root.Q<IntegerField>("manualCount0").value == 0 && root.Q<IntegerField>("manualCount1").value == 0, "no implicit army");
                Check(root.Query<TextField>().ToList().FindAll(f => f.name != "manualSetupTitle").TrueForAll(f => f.value == ""), "coordinates and surfaces are explicitly empty");
                Invalid(() => Call(hud, "BuildManualPayload"), "catalogue required");
                var catalogue = new SandboxManualSetupCatalogue { mode = "manual", round_seconds = 60, maximum_rounds = 60,
                    rule_version_id = Guid.NewGuid().ToString(), scenario_version_id = Guid.NewGuid().ToString(), capacity_version_id = Guid.NewGuid().ToString(),
                    rule_sha256 = new string('a', 64), scenario_sha256 = new string('b', 64), capacity_sha256 = new string('c', 64),
                    capacity = new SandboxManualSetupCapacity { minimum_per_type = 0, maximum_per_type = 10, minimum_per_side = 1,
                        maximum_per_side = 30, maximum_per_match = 60, scope = "local_battle_test_only", overflow_policy = "reject_without_truncation", asymmetric_allowed = true },
                    map = new SandboxManualSetupMap { content_hash = "map" }, can_create = false };
                Check(SandboxServerClient.ValidManualCatalogue(catalogue, "map"), "valid 60x60 source contract");
                // Exercise the actual HTTP deserialization path, including explicit JSON null.
                string wire = JsonUtility.ToJson(catalogue);
                wire = wire.Substring(0, wire.IndexOf("\"pending_creation\"", StringComparison.Ordinal)) + "\"pending_creation\":null}";
                Set(client, "responseBody", wire);
                var read = typeof(SandboxServerClient).GetMethod("ReadJson", Private).MakeGenericMethod(typeof(SandboxManualSetupCatalogue));
                var wireCatalogue = (SandboxManualSetupCatalogue)read.Invoke(client, null);
                Check(wireCatalogue.pending_creation == null, "JSON null does not create a phantom pending request");
                Check(SandboxServerClient.ValidManualCatalogue(wireCatalogue, "map"), "real response without pending creation accepted");
                Check(!SandboxServerClient.ValidManualCatalogue(catalogue, "different"), "map mismatch rejected");
                catalogue.capacity.maximum_per_type = 11;
                Check(!SandboxServerClient.ValidManualCatalogue(catalogue, "map"), "unsupported capacity change rejected instead of silently clamping");
                catalogue.capacity.maximum_per_type = 10;
                catalogue.round_seconds = 5; Check(!SandboxServerClient.ValidManualCatalogue(catalogue, "map"), "five-second submit rejected"); catalogue.round_seconds = 60;
                catalogue.maximum_rounds = 61; Check(!SandboxServerClient.ValidManualCatalogue(catalogue, "map"), "round limit cannot drift"); catalogue.maximum_rounds = 60;
                catalogue.checks = new[] {
                    new SandboxBattleDependencyCheck { key = "terrain", name = "地表与遮挡层", status = "failed", message = "真实网格待验收" },
                    new SandboxBattleDependencyCheck { key = "engine", name = "总册执行器接入", status = "not_tested", message = "本次部署待核验" }
                };
                var decoded = JsonUtility.FromJson<SandboxManualSetupCatalogue>(JsonUtility.ToJson(catalogue));
                Check(decoded.checks.Length == 2 && decoded.checks[0].status == "failed", "acceptance checks survive API JSON");
                Set(hud, "manualCatalogue", catalogue);
                Call(hud, "RenderManualSetupForm");
                var acceptanceSection = root.Q("manualSetupChecks");
                Check(acceptanceSection != null && acceptanceSection.Query<Label>().ToList().Exists(x => x.text.Contains("真实网格待验收")), "pending map details visible in configuration");
                Check(acceptanceSection.Query<Label>().ToList().Exists(x => x.text.Contains("待核验 · 总册执行器接入")), "installed engine is not shown as passed");
                catalogue.release_scope = "local_manual_test"; catalogue.competitive_ready = false;
                catalogue.limitations = new[] { "仅开放已核验道路与部署区", "桥头不连续的路线不可通行" };
                decoded = JsonUtility.FromJson<SandboxManualSetupCatalogue>(JsonUtility.ToJson(catalogue));
                Check(decoded.release_scope == "local_manual_test" && !decoded.competitive_ready && decoded.limitations.Length == 2, "local scope and limitations survive API JSON");
                Call(hud, "RenderManualSetupForm");
                Check(root.Q("manualReleaseScope").Query<Label>().ToList().Exists(x => x.text.Contains("非竞技资格")), "local publication never implies competitive readiness");
                Check(root.Q("manualReleaseScope").Query<Label>().ToList().Exists(x => x.text.Contains("桥头不连续")), "actual local limitations remain visible");
                catalogue.release_scope = null; Call(hud, "RenderManualSetupForm");
                Check(root.Q("manualReleaseScope") == null, "legacy catalogue is not relabeled as local accepted");
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, true);
                Call(hud, "UpdateManualSetupButtons");
                Invalid(() => Call(hud, "BuildManualPayload"), "zero army rejected");
                root.Q<IntegerField>("manualCount1").value = 11;
                Check(root.Q<IntegerField>("manualCount1").value == 0, "invalid quantities not silently truncated");
                root.Q<IntegerField>("manualCount1").value = 1;
                Invalid(() => Call(hud, "BuildManualPayload"), "empty placement rejected");
                Fill(root); Set(hud, "manualSetupSide", 1); Call(hud, "RenderManualSetupForm");
                root.Q<IntegerField>("manualCount1").value = 2; Fill(root);
                var payload = (SandboxManualCreatePayload)Call(hud, "BuildManualPayload");
                Check(payload.blue.units.Length == 1 && payload.red.units.Length == 2, "asymmetric quantities preserved");
                Check(payload.blue.camps.Length == 3 && payload.red.camps.Length == 3, "zero quantity types still have camps");
                Check(payload.blue.units[0].surface_id == "synthetic-editor-only" && payload.blue.units[0].position.height_mm == 0, "explicit coordinates and support ids preserved");
                Check(payload.mode == "manual" && payload.scenario_sha256 == catalogue.scenario_sha256 && payload.capacity_version_id == catalogue.capacity_version_id, "source binding retained");
                string json = JsonUtility.ToJson(payload);
                Check(!json.Contains("durability") && !json.Contains("ammo") && !json.Contains("deployment_validated") && !json.Contains("bootstrap"), "payload has no client resource or acceptance override");
                catalogue.recommended_setup = payload;
                var recommendedWire = JsonUtility.FromJson<SandboxManualSetupCatalogue>(JsonUtility.ToJson(catalogue));
                Check(recommendedWire.recommended_setup.blue.units.Length == payload.blue.units.Length, "recommended layout survives API JSON");
                Set(hud, "manualSetupUserEdited", false); Set(hud, "manualSetupRecommendationApplied", false);
                Check((bool)Call(hud, "ApplyRecommendedManualSetup", catalogue), "current source recommendation initializes an untouched form");
                Check(!(bool)Call(hud, "ApplyRecommendedManualSetup", catalogue), "re-reading cannot replace an existing recommendation");
                Set(hud, "manualSetupRecommendationApplied", false); Set(hud, "manualSetupUserEdited", true);
                Check(!(bool)Call(hud, "ApplyRecommendedManualSetup", catalogue), "user changes win over recommendation");
                Set(hud, "manualSetupUserEdited", false);
                catalogue.pending_creation = new SandboxManualPendingCreation { payload = payload, request_key = Guid.NewGuid().ToString() };
                Check(!(bool)Call(hud, "ApplyRecommendedManualSetup", catalogue), "pending payload wins over recommendation");
                catalogue.pending_creation = null;
                string correctHash = payload.scenario_sha256; payload.scenario_sha256 = new string('f', 64);
                Invalid(() => Call(hud, "ApplyRecommendedManualSetup", catalogue), "recommendation from another scenario rejected");
                payload.scenario_sha256 = correctHash;
                Call(hud, "RestoreManualDraft", payload);
                Set(hud, "manualSetupSide", 0); Call(hud, "RenderManualSetupForm"); Call(hud, "UpdateManualSetupButtons");
                Check(root.Q<IntegerField>("manualCount1").value == 1, "restored blue roster shown");
                Set(hud, "manualSetupSide", 1); Call(hud, "RenderManualSetupForm"); Call(hud, "UpdateManualSetupButtons");
                Check(root.Q<IntegerField>("manualCount1").value == 2, "restored red roster shown");
                var restored = (SandboxManualCreatePayload)Call(hud, "BuildManualPayload");
                Check(JsonUtility.ToJson(restored) == json, "recovered explicit deployment round-trips exactly");
                catalogue.pending_creation = new SandboxManualPendingCreation { request_key = "bad", payload = payload };
                Check(!SandboxServerClient.ValidManualCatalogue(catalogue, "map"), "invalid recovery key rejected");
                Set(client, "manualCreationKey", Guid.NewGuid().ToString()); Set(client, "manualCreationPayload", payload);
                Check(client.HasPending && client.HasManualCreation, "uncertain creation blocks regular writes");
                Call(hud, "RenderManualSetupForm");
                Check(!root.Q<TextField>("manualSetupTitle").enabledInHierarchy, "pending request freezes form");
                Check(root.Q("manualSetupChecks").enabledInHierarchy, "acceptance details remain readable during recovery");
                Check(!client.ConnectAutomatically("blue"), "uncertain create cannot switch seats");
                typeof(SandboxServerClient).GetProperty("Healthy").SetValue(client, false);
                Check(!client.CreateManualGame(payload, (ok, error) => { }), "unhealthy client cannot create");
                Check(!client.Busy, "all editor checks avoid network");
                Call(hud, "CloseBattleEntry"); Check(root.Q("manualSetupPanel") == null, "close removes modal");
                Check(client.HasManualCreation, "closing does not discard uncertain creation");
                Debug.Log("[ManualSetupChecks] PASS " + checks + " checks; no network, database or map writes");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); if (panelSettings != null) UnityEngine.Object.DestroyImmediate(panelSettings); }
        }
    }
}
