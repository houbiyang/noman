using System;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    public static class SandboxHistoryChecks
    {
        private static int count;
        private static void Check(bool value, string message)
        { if (!value) throw new InvalidOperationException("History check failed: " + message); count++; }
        private static SandboxVictoryEvidence Copy(SandboxVictoryEvidence input) => JsonUtility.FromJson<SandboxVictoryEvidence>(JsonUtility.ToJson(input));

        [MenuItem("穹图/设置/验证历史与结果")]
        public static void Run()
        {
            count = 0;
            var sample = SandboxBattleRules.Preview(); var result = SandboxBattleRules.Evaluate(sample);
            Check(result.final && result.verdict == "红方获胜" && result.redPoints == 20 && result.bluePoints == 16, "document example 20:16");
            Check(result.calculations.Length == 3 && result.calculations[1].Contains("2 × 2 = 4") && result.calculations[2].Contains("4 × 3 = 12"), "weights and calculations");
            Check(result.milestones[0].Contains("红方领先") && result.milestones[1].Contains("蓝方反超") && result.milestones[2].Contains("红方反超"), "lead changes at 15/30/45");
            Check(sample.preview && sample.ruleVersion == SandboxBattleRules.Version, "preview has explicit provenance");
            foreach (int minute in new[] { 0, 14, 15, 29, 30, 44, 45, 46 })
            {
                var input = Copy(sample); input.elapsedMinutes = minute; result = SandboxBattleRules.Evaluate(input);
                Check(result.final == (minute >= 45), "no final before third stage " + minute);
                Check(result.redPoints == (minute < 15 ? 0 : minute < 30 ? 4 : minute < 45 ? 8 : 20), "red boundary " + minute);
                Check(result.bluePoints == (minute < 15 ? 0 : minute < 30 ? 2 : minute < 45 ? 10 : 16), "blue boundary " + minute);
            }
            result = SandboxBattleRules.Evaluate(SandboxBattleRules.Preview(1));
            Check(!result.final && result.verdict == "待裁定" && result.redPoints == 18 && result.bluePoints == 18, "tie has no invented tiebreak");
            result = SandboxBattleRules.Evaluate(SandboxBattleRules.Preview(2));
            Check(!result.final && result.redPoints == 8 && result.bluePoints == 10, "30 minute lead is not victory");
            var all = Copy(sample); foreach (var stage in all.stages) { stage.redGoals = 6; stage.blueGoals = 0; }
            result = SandboxBattleRules.Evaluate(all);
            Check(result.final && result.redPoints == 36, "36 theoretical maximum");
            all.elapsedMinutes = 30; result = SandboxBattleRules.Evaluate(all);
            Check(!result.final && result.redPoints == 18, "dominant early lead is not an early win");
            Check(!SandboxBattleRules.Evaluate(null).final, "no evidence no winner");
            var independent = Copy(sample); independent.stages[0].redGoals = independent.stages[0].blueGoals = 6;
            Check(SandboxBattleRules.Evaluate(independent).calculations.Length == 3, "do not infer mutual exclusivity from allocation example");
            foreach (string mode in new[] { "conquest", "campaign", "sudden_death", "surrender" })
            { var input = Copy(sample); input.mode = mode; Check(!SandboxBattleRules.Evaluate(input).final, "unsupported mode not inferred " + mode); }
            foreach (Action<SandboxVictoryEvidence> corrupt in new Action<SandboxVictoryEvidence>[] {
                input => input.ruleVersion = "future", input => input.elapsedMinutes = -1,
                input => input.stages = null, input => input.stages = new SandboxGoalStage[0],
                input => input.stages[1] = null, input => input.stages[1].stage = 1,
                input => input.stages[1].redGoals = -1, input => input.stages[1].blueGoals = 7,
                input => input.stages[0].redGoals = 7, input => input.stages[1].evidence = "" })
            { var input = Copy(sample); corrupt(input); result = SandboxBattleRules.Evaluate(input); Check(!result.final && result.calculations.Length == 0, "invalid evidence rejected atomically"); }
            var missing = Copy(sample); missing.stages[2].confirmed = false;
            Check(!SandboxBattleRules.Evaluate(missing).final, "missing confirmation cannot settle");

            string directory = Path.Combine(Path.GetTempPath(), "qiongtu-history-check-" + Guid.NewGuid().ToString("N"));
            var host = new GameObject("HistoryUiCheck");
            try
            {
                var store = new SandboxHistoryStore(directory);
                Check(store.ReadAll(out string warning).Length == 0 && warning == "", "empty native store");
                var entry = new SandboxMatchArchive { id = Guid.NewGuid().ToString("N"), savedAt = DateTimeOffset.Now.ToString("O"),
                    title = "isolated preview", source = "preview", phase = "预演已结算", victory = sample, role = SandboxRole.Red };
                Check(store.TrySave(entry, out _), "write preview archive");
                Check(!store.TrySave(entry, out _), "do not overwrite previous archive");
                var reloaded = new SandboxHistoryStore(directory).ReadAll(out warning);
                Check(reloaded.Length == 1 && warning == "" && SandboxBattleRules.Evaluate(reloaded[0].victory).redPoints == 20, "reload persists full evidence");
                string invalidPath = Path.Combine(directory, Guid.NewGuid().ToString("N") + ".json"); File.WriteAllText(invalidPath, "{broken");
                Check(store.ReadAll(out warning).Length == 1 && warning.Length > 0 && File.Exists(invalidPath), "corrupt record retained with warning");
                File.Delete(invalidPath);
                var source = new DemoSandboxViewSource(); var projection = source.GetProjection(SandboxRole.Red);
                entry = new SandboxMatchArchive { id = Guid.NewGuid().ToString("N"), savedAt = DateTimeOffset.Now.AddSeconds(1).ToString("O"),
                    title = "seat observation", source = "local", phase = "进行中", role = SandboxRole.Red, observation = SandboxHistoryObservation.FromProjection(projection) };
                Check(typeof(SandboxHistoryObservation).GetField("ownPlan") == null, "archive schema cannot hold private plans");
                projection.ownPlan = null; projection.records = projection.records.Where(record => !record.isPrivatePlan).ToArray();
                Check(store.TrySave(entry, out _), "write seat projection");
                var loadedObservations = store.ReadAll(out var readWarning);
                Check(readWarning == "", "observation reload has no warnings");
                var restored = loadedObservations.First();
                Check(restored.title == entry.title && restored.observation.units.Length == projection.units.Length && restored.observation.records.Length == projection.records.Length, "projection JSON includes unit and event arrays");
                Check(!File.ReadAllText(Path.Combine(directory, entry.id + ".json")).Contains("ownPlan") && restored.victory == null, "no plan leakage and no fabricated results");
                var hud = host.AddComponent<SandboxHudController>();
                var document = host.AddComponent<UIDocument>();
                document.panelSettings = ScriptableObject.CreateInstance<PanelSettings>();
                var content = new ScrollView(); var root = document.rootVisualElement; root.Add(content); root.Add(new Label { name = "feedbackLabel" });
                Action<string, object> set = (field, value) => typeof(SandboxHudController).GetField(field, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(hud, value);
                Func<string, object[], object> invoke = (method, args) => typeof(SandboxHudController).GetMethod(method, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(hud, args);
                set("root", root); set("settingsContent", content); set("historyStore", store); set("source", source); set("projection", projection); set("role", SandboxRole.Red);
                set("settingsSection", "results"); invoke("RenderSettingsSection", new object[0]);
                Check(content.Q<Label>("resultScore").text == "38.89分 : 50.00分" && content.Q<Label>("resultVerdict").text == "蓝方获胜", "actual UI renders adapted project preview");
                Check(content.Q<Label>("redScore100").text == "38.89分" && content.Q<Label>("blueScore100").text == "50.00分", "hero uses points not percentage suffix");
                Check(content.Q<Label>("score100Calculation").text.Contains("× 100 = 38.89分") && !content.Q<Label>("score100Calculation").text.Contains("%"), "100-point formula uses score units");
                Check(content.Query<Label>().ToList().Any(label => label.text.Contains("满分100分")), "100-point scale is explicit");
                Check(content.Q<Button>("resultScenario1") != null, "scenario button available");
                invoke("SelectResultScenario", new object[] { 1 });
                Check(content.Q<Label>("resultScore").text == "50.00分 : 50.00分" && content.Q<Label>("resultVerdict").text == "平局", "UI scenario updates to project draw");
                set("showingPreview", false); set("resultEvidence", null); invoke("RenderSettingsSection", new object[0]);
                Check(content.Q<Label>("resultScore").text == "— : —" && content.Q<Label>("resultVerdict").text == "待裁定", "live page never reuses preview");
                set("settingsSection", "history"); invoke("RenderSettingsSection", new object[0]);
                Check(content.Q<Button>("archiveCurrentMatch") != null && content.Query<Button>().ToList().Count(button => button.name.StartsWith("history-")) == 2, "settings history renders archives and manual action");
                set("role", SandboxRole.Blue); invoke("RenderSettingsSection", new object[0]);
                Check(content.Query<Button>().ToList().Count(button => button.name.StartsWith("history-")) == 1, "different seat sees preview but not red observation");
                set("role", SandboxRole.Red);
                projection.step = 60; set("projection", projection);
                invoke("ObserveMatchEnd", new object[0]); invoke("ObserveMatchEnd", new object[0]);
                Check(store.ReadAll(out _).Count(item => !string.IsNullOrEmpty(item.automaticKey)) == 1, "end automatic archive deduplicated");
                set("noticedEndKey", null); invoke("ObserveMatchEnd", new object[0]);
                Check(store.ReadAll(out _).Count(item => !string.IsNullOrEmpty(item.automaticKey)) == 1, "end archive deduplicates across reloads");
                var client = host.AddComponent<SandboxServerClient>();
                var serverView = new SandboxServerView { role = "red", game = new SandboxServerGame { game_id = "fixture-game", title = "fixture" },
                    context = new SandboxServerContext { branch_id = "fixture-branch", snapshot_id = "fixture-head", context_token = "NEVER-ARCHIVE-THIS-TOKEN" },
                    clock = new SandboxServerClock { phase = "finished", simulation_step = 60 } };
                typeof(SandboxServerClient).GetProperty("View").GetSetMethod(true).Invoke(client, new object[] { serverView });
                typeof(SandboxServerClient).GetProperty("Healthy").GetSetMethod(true).Invoke(client, new object[] { true });
                set("serverClient", client); set("serverMode", true); set("noticedEndKey", null);
                invoke("ObserveMatchEnd", new object[0]);
                Check(!store.ReadAll(out _).Any(item => item.source == "server"), "backend automatic archives are server owned, not browser copies");
                Check(!store.ReadAll(out _).Any(item => item.matchId == "fixture-game"), "server session credentials never persisted in local history");
                set("settingsOverlay", new VisualElement());
                var draft = new SandboxClientSettings(); draft.red.profileVersion = "unsaved-draft"; set("settingsDraft", draft);
                invoke("ShowPendingEndResult", new object[0]);
                Check((bool)typeof(SandboxHudController).GetField("pendingEndResult", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(hud)
                    && draft.red.profileVersion == "unsaved-draft", "end result defers without replacing settings draft");
                set("showLocalReportHistory", true);
                // Real report entry, independent root and reversible Settings draft suspension.
                var settings = new VisualElement { name = "settingsFixture" }; root.Add(settings); set("settingsOverlay", settings);
                root.Add(new VisualElement { name = "drawer" }); set("drawerContent", new ScrollView());
                var world = host.AddComponent<SandboxWorld>();
                typeof(SandboxWorld).GetField("barracksSelectionRing", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(world, host.AddComponent<LineRenderer>());
                set("world", world);
                invoke("OpenReport", new object[] { "history" });
                var report = root.Q("reportWorkspace");
                Check(report != null && report.parent == root && report.ClassListContains("ui-blocker"), "history opens an independent blocking workspace");
                Check(settings.parent == root && settings.style.display.value == DisplayStyle.None && draft.red.profileVersion == "unsaved-draft", "settings draft suspended not discarded");
                Check(report.Q<Button>("reportHistoryTab").ClassListContains("report-nav-active"), "history top tab active");
                var reportScroll = report.Q<ScrollView>("reportContent");
                Check(reportScroll.contentContainer.ClassListContains("report-body") && reportScroll.horizontalScrollerVisibility == ScrollerVisibility.Hidden, "report body has explicit padding hook without workspace horizontal scrollbar");
                Check(Mathf.Approximately(reportScroll.mouseWheelScrollSize, 10f) && reportScroll.mouseWheelScrollSize < new ScrollView().mouseWheelScrollSize, "history wheel step is slower than UI Toolkit default");
                int beforeSearch = reportScroll.Query<Button>().ToList().Count(button => button.name.StartsWith("history-"));
                Check(report.Q<TextField>("historySearch") != null, "search field available in independent workspace");
                // Batch-mode UIDocument has no live panel event loop; browser check covers typing.
                set("historySearch", "no-such-archive-987"); invoke("RenderSettingsSection", new object[0]);
                Check(reportScroll.Query<Button>().ToList().All(button => !button.name.StartsWith("history-")) && beforeSearch > 0, "history search filters actual archives");
                set("historySearch", ""); invoke("RenderSettingsSection", new object[0]);
                Check(reportScroll.Query<Button>().ToList().Count(button => button.name.StartsWith("history-")) == beforeSearch, "clearing search restores same seat archives");
                invoke("NavigateReport", new object[] { "results" }); invoke("SelectResultScenario", new object[] { 0 });
                Check(ReferenceEquals(reportScroll, report.Q<ScrollView>("reportContent")) && Mathf.Approximately(reportScroll.mouseWheelScrollSize, 10f), "results retain same slower report wheel step");
                Check(report.Q<Label>("resultScore").text == "38.89分 : 50.00分" && report.Query<Label>(className: "report-timeline").ToList().Count == 3, "independent result renders evidence and key nodes");
                Check(report.Query<VisualElement>(className: "report-red-bar").ToList().Count == 3, "three weighted stages not legacy prototype categories");
                var malformedEvidence = SandboxBattleRules.Preview(); malformedEvidence.stages = null; set("resultEvidence", malformedEvidence); invoke("RenderSettingsSection", new object[0]);
                Check(report.Q<Label>("resultScore").text == "— : —" && report.Query<VisualElement>(className: "report-red-bar").ToList().Count == 0, "malformed archived evidence renders safely without scores");
                invoke("CloseReport", new object[] { false });
                Check(root.Q("reportWorkspace") == null && settings.style.display.value == DisplayStyle.Flex && draft.red.profileVersion == "unsaved-draft", "return restores same settings and unsaved draft");
                var uxml = File.ReadAllText(Path.Combine(Application.dataPath, "Sandbox/Resources/Sandbox/SandboxHud.uxml"));
                var gear = System.Text.RegularExpressions.Regex.Match(uxml, "<ui:Button name=\"settingsAction\"[\\s\\S]*?</ui:Button>").Value;
                Check(gear.Contains("settings-icon") && !gear.Contains("ui:Label") && !gear.Contains("action-tile"), "settings action is gear only without caption or tile");
                Check(Resources.Load<StyleSheet>("Sandbox/SandboxReports") != null, "report stylesheet imported");
                set("settingsOverlay", null);

            }
            finally { UnityEngine.Object.DestroyImmediate(host); if (Directory.Exists(directory)) Directory.Delete(directory, true); }
            var adapted = SandboxBattleRules.ProjectPreview();
            result = SandboxBattleRules.Evaluate(adapted);
            Check(result.final && result.verdict == "蓝方获胜" && result.redPoints == 7 && result.bluePoints == 9, "project preview 7:9 from three named map points");
            Check(result.calculations[0].Contains("20 分钟") && result.calculations[2].Contains("60 分钟") && result.milestones[2].Contains("蓝方反超"), "project timing and comeback");
            Check(SandboxBattleRules.StageMinutes(adapted) == 20 && SandboxBattleRules.GoalLimit(adapted) == 3, "project and legacy parameters separated");
            Check(result.calculations[1].Contains("1 × 2 = 2") && adapted.stages[1].objectives[1].owner == "contested", "contested point not awarded to either side");
            result = SandboxBattleRules.Evaluate(SandboxBattleRules.ProjectPreview(1));
            Check(result.final && result.verdict == "平局" && result.redPoints == 9 && result.bluePoints == 9, "adapted tie is a final draw, legacy tie stays pending");
            result = SandboxBattleRules.Evaluate(SandboxBattleRules.ProjectPreview(2));
            Check(!result.final && result.redPoints == 4 && result.bluePoints == 3, "40-minute running score not final");
            foreach (int minute in new[] { 19, 20, 39, 40, 59, 60 })
            {
                var boundary = Copy(adapted); boundary.elapsedMinutes = minute;
                foreach (var pending in boundary.stages.Where(stage => stage.stage * 20 > minute))
                { pending.confirmed = false; pending.redGoals = pending.blueGoals = 0; pending.objectives = Array.Empty<SandboxControlObjective>(); pending.evidence = ""; }
                result = SandboxBattleRules.Evaluate(boundary);
                Check(result.final == (minute >= 60), "adapted final boundary " + minute);
                Check(result.redPoints == (minute < 20 ? 0 : minute < 40 ? 2 : minute < 60 ? 4 : 7), "adapted red weighted boundary " + minute);
            }
            var invalid = Copy(adapted); invalid.stages[0].redGoals = 3;
            Check(SandboxBattleRules.Evaluate(invalid).calculations.Length == 0, "reject aggregate inconsistent with named point owners");
            invalid = Copy(adapted); invalid.stages[0].objectives[0].point_id = "unknown";
            Check(SandboxBattleRules.Evaluate(invalid).calculations.Length == 0, "reject unknown project point");
            invalid = Copy(adapted); invalid.stages[0].objectives[0].owner = "neutral";
            Check(SandboxBattleRules.Evaluate(invalid).calculations.Length == 0, "reject owner inconsistent with unit presence");
            var detailed = SandboxBattleRules.Evaluate(SandboxBattleRules.ProjectPreview());
            Check(detailed.stageScores.Length == 3 && detailed.stageScores[1].redPoints == 2 && detailed.stageScores[1].bluePoints == 2
                && detailed.stageScores[1].redTotal == 4 && detailed.stageScores[1].blueTotal == 3, "structured stage and cumulative scores");
            Check(detailed.calculations[1].Contains("中央桥梁") && detailed.calculations[1].Contains("红+0 / 蓝+0"), "per-point arithmetic explains disputed zero");
            invalid = Copy(adapted); invalid.elapsedMinutes = 19;
            Check(SandboxBattleRules.Evaluate(invalid).stageScores.Length == 0, "future sealed project stage fails closed");
            invalid = Copy(adapted); invalid.stages[0].confirmed = false; invalid.stages[0].redGoals = invalid.stages[0].blueGoals = 0; invalid.stages[0].objectives = Array.Empty<SandboxControlObjective>();
            Check(SandboxBattleRules.Evaluate(invalid).calculations.Length == 0, "no later stage accepted over evidence gap");
            invalid = Copy(adapted); invalid.elapsedMinutes = 61;
            Check(SandboxBattleRules.Evaluate(invalid).calculations.Length == 0, "project clock outside frozen deadline rejected");
            invalid = SandboxBattleRules.ProjectPreview(2); invalid.stages[2].redGoals = 1;
            Check(SandboxBattleRules.Evaluate(invalid).calculations.Length == 0, "pending stage cannot carry unawarded aggregates");
            var score100 = SandboxBattleRules.Evaluate(SandboxBattleRules.ProjectPreview());
            Check(score100.scoreMaximum == 18 && score100.redScore100 == 38.89 && score100.blueScore100 == 50, "project score 100-point score uses fixed maximum18");
            score100 = SandboxBattleRules.Evaluate(SandboxBattleRules.ProjectPreview(2));
            Check(!score100.final && score100.redScore100 == 22.22 && score100.blueScore100 == 16.67, "pending 100-point score does not normalize by elapsed stages");
            score100 = SandboxBattleRules.Evaluate(SandboxBattleRules.Preview());
            Check(score100.scoreMaximum == 36 && score100.redScore100 == 55.56 && score100.blueScore100 == 44.44, "legacy 100-point score uses maximum36");
            score100 = SandboxBattleRules.Evaluate(null);
            Check(score100.scoreMaximum == 0 && score100.calculations.Length == 0, "invalid evidence has no 100-point score denominator");
            SandboxSettingsChecks.Run();
            Debug.Log("[HistoryChecks] PASS " + count + " checks; persistence and UI tested in isolated storage");
        }
    }
}
