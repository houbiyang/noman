using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        // 未填写的坐标保持空白，不能把(0,0,0)或任意surface当默认合法部署。
        private sealed class PlacementDraft
        {
            public string x = "", z = "", height = "", surface = "";
            public SandboxReplayPosition Position(string label)
            {
                if (!int.TryParse(x, NumberStyles.Integer, CultureInfo.InvariantCulture, out int px)
                    || !int.TryParse(z, NumberStyles.Integer, CultureInfo.InvariantCulture, out int pz)
                    || !int.TryParse(height, NumberStyles.Integer, CultureInfo.InvariantCulture, out int ph)
                    || px < 0 || px > 12000000 || pz < 0 || pz > 12000000 || string.IsNullOrWhiteSpace(surface) || surface.Trim().Length > 80)
                    throw new ArgumentException(label + "：请填写完整的整数毫米坐标和支撑面ID；X/Z须在0～12000000之间。");
                return new SandboxReplayPosition { x_mm = px, z_mm = pz, height_mm = ph };
            }
        }
        private sealed class SideDeploymentDraft
        {
            public readonly int[] counts = new int[3];
            public readonly List<PlacementDraft>[] units = { new List<PlacementDraft>(), new List<PlacementDraft>(), new List<PlacementDraft>() };
            public readonly PlacementDraft[] camps = { new PlacementDraft(), new PlacementDraft(), new PlacementDraft() };
            public readonly PlacementDraft[] service = { new PlacementDraft(), new PlacementDraft(), new PlacementDraft() };
        }
        private static readonly string[] SetupKinds = { "recon", "ground", "support" };
        private static readonly string[] SetupKindLabels = { "无人机", "履带地面单位", "保障单位" };
        private readonly SideDeploymentDraft[] manualSideDrafts = { new SideDeploymentDraft(), new SideDeploymentDraft() };
        private string manualSetupTitle = "手动对战";
        private bool manualSetupUserEdited, manualSetupRecommendationApplied;
        private int manualSetupSide;
        private string manualSetupSourceGame;
        private SandboxManualSetupCatalogue manualCatalogue;
        private Label manualSetupStatus;
        private VisualElement manualSetupForm;
        private ScrollView manualSetupScroll;
        private Button manualSetupSubmit, manualSetupRecover, manualSetupRead;

        private void OpenManualSetup()
        {
            if (!serverMode || serverClient?.View?.role != "host") return;
            string sourceGame = serverClient.View.context?.game_id;
            if (manualSetupSourceGame != sourceGame) { manualCatalogue = null; manualSetupSourceGame = sourceGame; }
            battleOverlay?.RemoveFromHierarchy(); battleGeneration++; battleChecking = false;
            battleOverlay = new VisualElement { name = "battleOverlay" };
            battleOverlay.AddToClassList("settings-backdrop"); battleOverlay.AddToClassList("ui-blocker"); root.Add(battleOverlay);
            var panel = SettingsGroup(battleOverlay, "battle-panel"); panel.name = "manualSetupPanel";
            var header = SettingsGroup(panel, "settings-header");
            var heading = SettingsGroup(header, "settings-heading");
            AddText(heading, "穹图 / 本地手动测试", "settings-eyebrow");
            AddText(heading, "配置独立新对局", "settings-title");
            AddText(heading, "60仿真秒 × 最多60轮 · 不覆盖旧局 · 不改地图", "settings-header-description");
            AddButton(header, "×", CloseBattleEntry, "settings-close");
            var body = SettingsGroup(panel, "battle-content");
            manualSetupStatus = AddText(body, "读取配置后填写双方编制、单位部署和六座兵营。所有位置和服务面均由后端验收。", "settings-notice");
            manualSetupStatus.name = "manualSetupStatus";
            manualSetupScroll = new ScrollView(ScrollViewMode.Vertical); manualSetupScroll.AddToClassList("battle-checks"); body.Add(manualSetupScroll);
            var footer = SettingsGroup(panel, "settings-footer");
            AddButton(footer, "返回模式", () => { CloseBattleEntry(); OpenBattleEntry(); }, "settings-secondary");
            manualSetupRead = AddButton(footer, "读取配置", ReadManualCatalogue, "settings-secondary"); manualSetupRead.name = "manualSetupRead";
            manualSetupRecover = AddButton(footer, "恢复创建结果", RecoverManualSetup, "settings-secondary"); manualSetupRecover.name = "manualSetupRecover";
            manualSetupSubmit = AddButton(footer, "提交配置并验收", SubmitManualSetup, "settings-primary"); manualSetupSubmit.name = "manualSetupSubmit";
            int generation = battleGeneration;
            panel.schedule.Execute(() => { if (generation == battleGeneration) UpdateManualSetupButtons(); }).Every(250);
            RenderManualSetupForm(); UpdateManualSetupButtons(); ReadManualCatalogue();
        }

        private void RenderManualSetupForm()
        {
            manualSetupScroll.Clear();
            RenderManualReleaseScope(manualSetupScroll, manualCatalogue?.release_scope, manualCatalogue?.competitive_ready == true, manualCatalogue?.limitations);
            RenderManualSetupChecks();
            manualSetupForm = new VisualElement(); manualSetupScroll.Add(manualSetupForm);
            var title = new TextField("新对局名称") { value = manualSetupTitle, maxLength = 60, name = "manualSetupTitle" };
            title.AddToClassList("settings-field"); manualSetupForm.Add(title);
            title.RegisterValueChangedCallback(e => { manualSetupUserEdited = true; manualSetupTitle = e.newValue; });
            var tabs = SettingsGroup(manualSetupForm, "battle-modes");
            for (int i = 0; i < 2; i++)
            {
                int side = i;
                var tab = AddButton(tabs, i == 0 ? "蓝方部署" : "红方部署", () => { manualSetupSide = side; RenderManualSetupForm(); UpdateManualSetupButtons(); }, "battle-mode");
                tab.EnableInClassList("battle-mode-selected", manualSetupSide == i);
            }
            AddText(manualSetupForm, "每方至少1个单位；每类0～10个，每方最多30个。双方数量可不同。坐标单位为毫米，支撑面ID必须来自地图适配器；推荐部署仅来自当前场景；提交时仍逐项核验。", "settings-note");
            var draft = manualSideDrafts[manualSetupSide];
            for (int i = 0; i < 3; i++)
            {
                int kind = i;
                var count = new IntegerField(SetupKindLabels[i] + "数量") { value = draft.counts[i], isDelayed = true, name = "manualCount" + i };
                count.AddToClassList("settings-field"); manualSetupForm.Add(count);
                count.RegisterValueChangedCallback(e => {
                    if (e.newValue < 0 || e.newValue > 10) { count.SetValueWithoutNotify(draft.counts[kind]); manualSetupStatus.text = "每类数量只能为0～10，不会自动截断。"; return; }
                    manualSetupUserEdited = true;
                    draft.counts[kind] = e.newValue;
                    while (draft.units[kind].Count < e.newValue) draft.units[kind].Add(new PlacementDraft());
                    RenderManualSetupForm(); UpdateManualSetupButtons();
                });
                for (int n = 0; n < draft.counts[i]; n++) BuildPlacementFields(manualSetupForm, SetupKindLabels[i] + " " + (n + 1), draft.units[i][n]);
                BuildPlacementFields(manualSetupForm, SetupKindLabels[i] + "兵营中心（即使数量为0也需配置）", draft.camps[i]);
                BuildPlacementFields(manualSetupForm, SetupKindLabels[i] + "兵营服务锚点", draft.service[i]);
            }
            var blueDraft = manualSideDrafts[0]; var redDraft = manualSideDrafts[1];
            bool asymmetric = false;
            for (int k = 0; k < 3; k++) if (blueDraft.counts[k] != redDraft.counts[k]) asymmetric = true;
            if (asymmetric) AddText(manualSetupForm, "非对称场景：双方编制不同；归一化计分不表示实力相当。", "settings-notice");
            for (int side = 0; side < 2; side++)
                if (manualSideDrafts[side].counts[0] > 0 && manualSideDrafts[side].counts[1] + manualSideDrafts[side].counts[2] == 0)
                    AddText(manualSetupForm, (side == 0 ? "蓝方" : "红方") + "仅有无人机，缺乏占点能力；不会因此自动判负。", "settings-notice");
            if (manualCatalogue?.pending_creation != null)
                AddText(manualSetupForm, "存在待核对创建请求：配置已冻结。请恢复结果，或以原键和原配置重试；不会使用当前编辑内容重复开局。", "settings-notice");
            manualSetupForm.SetEnabled(manualCatalogue?.pending_creation == null && !serverClient.HasManualCreation);
        }

        // Scope is server-owned and frozen per game; never infer readiness from a
        // missing field or from a successful connection to the local service.
        private void RenderManualReleaseScope(VisualElement parent, string scope, bool competitiveReady, string[] limitations)
        {
            if (scope != "local_manual_test") return;
            var section = SettingsGroup(parent, "manual-release-scope");
            section.name = "manualReleaseScope";
            AddText(section, "本地手动测试 · 非竞技资格", "battle-check-title");
            AddText(section, "仅在已核验的局部地图范围内测试；不代表全图可用或双方公平。Agent模式不开放。", "settings-note");
            if (competitiveReady)
                AddText(section, "当前测试范围与竞技标记不一致，仍按非竞技测试展示。", "settings-notice");
            bool shown = false;
            if (limitations != null)
                foreach (string limitation in limitations)
                    if (!string.IsNullOrWhiteSpace(limitation)) { AddText(section, "限制：" + limitation, "settings-note"); shown = true; }
            if (!shown) AddText(section, "尚未收到具体测试边界；不要据此推定其他区域可用。", "settings-notice");
        }

        private void RenderManualSetupChecks()
        {
            var section = SettingsGroup(manualSetupScroll, "manual-setup-checks");
            section.name = "manualSetupChecks";
            AddText(section, "开战前待完成的验收", "battle-check-title");
            AddText(section, "来源验收与本次部署分别检查；本清单不会启动对局。", "settings-note");
            if (manualCatalogue?.checks == null || manualCatalogue.checks.Length == 0)
            {
                AddText(section, "尚未取得验收明细；不能据此判断可以开战。", "settings-notice");
                return;
            }
            foreach (var item in manualCatalogue.checks)
            {
                if (item == null) continue;
                bool passed = item.status == "passed";
                var row = SettingsGroup(section, "battle-check");
                row.EnableInClassList("battle-check-warning", !passed);
                string state = passed ? "来源已验收" : item.status == "not_tested" ? "待核验" : "待完成";
                AddText(row, state + " · " + item.name, "battle-check-title");
                AddText(row, item.message, "settings-note");
            }
        }

        private void BuildPlacementFields(VisualElement parent, string label, PlacementDraft draft)
        {
            var card = SettingsGroup(parent, "battle-check"); AddText(card, label, "battle-check-title");
            var row = SettingsGroup(card, "manual-placement-row");
            SetupText(row, "X / 毫米", draft.x, v => draft.x = v);
            SetupText(row, "Z / 毫米", draft.z, v => draft.z = v);
            SetupText(row, "高程 / 毫米", draft.height, v => draft.height = v);
            SetupText(card, "支撑面 ID", draft.surface, v => draft.surface = v);
        }
        private void SetupText(VisualElement parent, string label, string value, Action<string> changed)
        {
            var field = new TextField(label) { value = value, maxLength = 80 };
            field.AddToClassList("settings-field"); parent.Add(field); field.RegisterValueChangedCallback(e => { manualSetupUserEdited = true; changed(e.newValue); });
        }

        private SandboxManualSideSetup BuildManualSide(int index)
        {
            var draft = manualSideDrafts[index]; string side = index == 0 ? "蓝方" : "红方";
            if (draft.counts[0] + draft.counts[1] + draft.counts[2] < 1) throw new ArgumentException(side + "至少需要1个单位。");
            var units = new List<SandboxManualDeployment>(); var camps = new List<SandboxManualCampDeployment>();
            for (int i = 0; i < 3; i++)
            {
                for (int n = 0; n < draft.counts[i]; n++)
                {
                    var p = draft.units[i][n]; units.Add(new SandboxManualDeployment { unit_type = SetupKinds[i], ordinal = n + 1,
                        position = p.Position(side + SetupKindLabels[i] + (n + 1)), surface_id = p.surface.Trim() });
                }
                var c = draft.camps[i]; var a = draft.service[i];
                camps.Add(new SandboxManualCampDeployment { unit_type = SetupKinds[i], position = c.Position(side + SetupKindLabels[i] + "兵营"),
                    surface_id = c.surface.Trim(), service_anchor = a.Position(side + SetupKindLabels[i] + "服务锚点"), service_surface_id = a.surface.Trim() });
            }
            return new SandboxManualSideSetup { quantities = new SandboxManualQuantities { recon = draft.counts[0], ground = draft.counts[1], support = draft.counts[2] }, units = units.ToArray(), camps = camps.ToArray() };
        }
        private SandboxManualCreatePayload BuildManualPayload()
        {
            if (manualCatalogue == null) throw new ArgumentException("请先读取配置来源。");
            if (string.IsNullOrWhiteSpace(manualSetupTitle)) throw new ArgumentException("请填写对局名称。");
            return new SandboxManualCreatePayload { title = manualSetupTitle.Trim(), rule_version_id = manualCatalogue.rule_version_id,
                scenario_version_id = manualCatalogue.scenario_version_id, capacity_version_id = manualCatalogue.capacity_version_id,
                rule_sha256 = manualCatalogue.rule_sha256, scenario_sha256 = manualCatalogue.scenario_sha256, capacity_sha256 = manualCatalogue.capacity_sha256,
                blue = BuildManualSide(0), red = BuildManualSide(1) };
        }

        private static PlacementDraft RestorePlacement(SandboxReplayPosition position, string surface)
        {
            if (position == null || string.IsNullOrWhiteSpace(surface)) throw new ArgumentException("原创建配置不完整，请保留请求并核对后端。");
            return new PlacementDraft { x = position.x_mm.ToString(CultureInfo.InvariantCulture), z = position.z_mm.ToString(CultureInfo.InvariantCulture),
                height = position.height_mm.ToString(CultureInfo.InvariantCulture), surface = surface };
        }
        private void RestoreManualDraft(SandboxManualCreatePayload payload)
        {
            if (payload == null) throw new ArgumentException("原创建配置缺失，请保留请求并核对后端。");
            var restored = new SideDeploymentDraft[2];
            var sides = new[] { payload.blue, payload.red };
            for (int side = 0; side < 2; side++)
            {
                var source = sides[side]; var draft = new SideDeploymentDraft(); restored[side] = draft;
                if (source?.quantities == null || source.units == null || source.camps == null) throw new ArgumentException("原创建名册不完整。");
                draft.counts[0] = source.quantities.recon; draft.counts[1] = source.quantities.ground; draft.counts[2] = source.quantities.support;
                for (int k = 0; k < 3; k++)
                {
                    if (draft.counts[k] < 0 || draft.counts[k] > 10) throw new ArgumentException("原创建数量不符合容量版本。");
                    for (int n = 1; n <= draft.counts[k]; n++)
                    {
                        int ordinal = n;
                        var unit = Array.Find(source.units, u => u != null && u.unit_type == SetupKinds[k] && u.ordinal == ordinal);
                        if (unit == null) throw new ArgumentException("原创建名册缺少单位。");
                        draft.units[k].Add(RestorePlacement(unit.position, unit.surface_id));
                    }
                    var camp = Array.Find(source.camps, c => c != null && c.unit_type == SetupKinds[k]);
                    if (camp == null) throw new ArgumentException("原创建名册缺少兵营。");
                    draft.camps[k] = RestorePlacement(camp.position, camp.surface_id);
                    draft.service[k] = RestorePlacement(camp.service_anchor, camp.service_surface_id);
                }
            }
            manualSideDrafts[0] = restored[0]; manualSideDrafts[1] = restored[1]; manualSetupTitle = payload.title;
        }

        private bool ApplyRecommendedManualSetup(SandboxManualSetupCatalogue catalogue)
        {
            // An interrupted creation always wins. A repeated read must not
            // replace any user edit or a recommendation already shown.
            if (catalogue?.recommended_setup == null || catalogue.pending_creation != null
                || serverClient.HasManualCreation || manualSetupUserEdited || manualSetupRecommendationApplied) return false;
            var candidate = catalogue.recommended_setup;
            if (candidate.mode != "manual" || candidate.rule_version_id != catalogue.rule_version_id
                || candidate.scenario_version_id != catalogue.scenario_version_id || candidate.capacity_version_id != catalogue.capacity_version_id
                || candidate.rule_sha256 != catalogue.rule_sha256 || candidate.scenario_sha256 != catalogue.scenario_sha256
                || candidate.capacity_sha256 != catalogue.capacity_sha256)
                throw new ArgumentException("推荐部署与当前场景不一致，未填入。请重新读取配置。");
            RestoreManualDraft(candidate);
            manualSetupRecommendationApplied = true;
            return true;
        }

        private void ReadManualCatalogue()
        {
            int generation = battleGeneration;
            bool sent = serverClient.ReadManualSetup((result, error) => {
                if (generation != battleGeneration || root.Q("manualSetupPanel") == null) return;
                if (error != null) manualSetupStatus.text = error;
                else
                {
                    manualCatalogue = result;
                    try
                    {
                        if (result.pending_creation != null) RestoreManualDraft(result.pending_creation.payload);
                        bool recommended = ApplyRecommendedManualSetup(result);
                        manualSetupStatus.text = result.pending_creation != null ? "已还原原创建配置（只读）。请恢复结果或使用原请求重试。"
                            : (recommended ? "已填入当前场景的推荐部署，可修改；提交后仍须核验。" : "配置来源已读取（60秒/轮，共60轮）。") + result.message;
                    }
                    catch (ArgumentException invalid) { manualSetupStatus.text = invalid.Message; }
                }
                RenderManualSetupForm(); UpdateManualSetupButtons();
            });
            if (sent) manualSetupStatus.text = "正在读取规则、场景和容量版本；不会创建或推进对局…";
            else manualSetupStatus.text = "请等待连接同步完成，再点击读取配置；没有发起新局创建。";
            UpdateManualSetupButtons();
        }
        private void SubmitManualSetup()
        {
            if (recordedOverlay != null) { Notify("历史播放中不能创建新局；请先退出"); return; }
            if (serverClient.View?.context?.game_id != manualSetupSourceGame) { manualSetupStatus.text = "当前对局已变化，请关闭并重新打开新局配置。"; return; }
            SandboxManualCreatePayload payload;
            try { payload = manualCatalogue?.pending_creation?.payload ?? BuildManualPayload(); }
            catch (ArgumentException error) { manualSetupStatus.text = error.Message; return; }
            int generation = battleGeneration;
            bool sent = serverClient.CreateManualGame(payload, (ok, error) => ManualSetupFinished(generation, ok, error));
            if (sent) manualSetupStatus.text = "正在验收并创建；若响应丢失，请恢复原结果，不要重复创建。";
            else manualSetupStatus.text = "连接忙碌或旧命令尚未核对，未提交新局。";
            UpdateManualSetupButtons();
        }
        private void RecoverManualSetup()
        {
            int generation = battleGeneration;
            bool sent = serverClient.RecoverManualCreation((ok, error) => ManualSetupFinished(generation, ok, error));
            if (sent) manualSetupStatus.text = "正在查询原创建结果；不会另建对局…";
            else manualSetupStatus.text = "当前没有可恢复请求，或连接尚未就绪。";
            UpdateManualSetupButtons();
        }
        private void ManualSetupFinished(int generation, bool ok, string error)
        {
            if (generation != battleGeneration || root.Q("manualSetupPanel") == null) return;
            if (ok) { manualCatalogue = null; CloseBattleEntry(); return; }
            manualSetupStatus.text = error + " 如需解除已明确拒绝的请求，请点击读取配置。";
            UpdateManualSetupButtons();
        }
        private void UpdateManualSetupButtons()
        {
            bool ready = serverMode && serverClient != null && serverClient.Healthy && !serverClient.Busy && serverClient.View?.role == "host"
                && serverClient.View.context?.game_id == manualSetupSourceGame;
            manualSetupRead.SetEnabled(ready);
            manualSetupRecover.SetEnabled(ready && serverClient.HasManualCreation);
            manualSetupSubmit.SetEnabled(ready && manualCatalogue != null);
            manualSetupSubmit.text = serverClient.HasManualCreation ? "原请求重试" : "提交配置并验收";
            manualSetupForm?.SetEnabled(ready && !serverClient.HasManualCreation);
        }
    }
}
