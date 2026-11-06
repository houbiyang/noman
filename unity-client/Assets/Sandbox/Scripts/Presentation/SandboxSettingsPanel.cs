using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        private SandboxSettingsStore settingsStore;
        private SandboxClientSettings savedSettings;
        private SandboxClientSettings settingsDraft;
        private string settingsWarning = "";
        private string settingsSection = "service";
        private VisualElement settingsOverlay;
        private ScrollView settingsContent;
        private Label settingsFeedback;
        private Label settingsSaveState;
        private Button settingsSaveButton;
        private readonly Dictionary<string, Button> settingsNav = new Dictionary<string, Button>();
        private readonly Dictionary<string, VisualElement> settingsFields = new Dictionary<string, VisualElement>();
        private readonly Dictionary<string, Label> settingsErrors = new Dictionary<string, Label>();
        private List<SandboxSettingsIssue> settingsIssues = new List<SandboxSettingsIssue>();
        private Coroutine healthRoutine;
        private UnityWebRequest healthRequest;
        private Label healthStatus;
        private Button healthButton;
        private string healthMessage = "尚未检测 · 当前沙盘使用本地演示数据";

        private void InitializeSettings()
        {
            settingsStore = new SandboxSettingsStore();
            savedSettings = settingsStore.Load(out settingsWarning);
            ApplyDisplaySettings();
            root.styleSheets.Add(Resources.Load<StyleSheet>("Sandbox/SandboxSettings"));
            BindButton("settingsAction", OpenSettings);
        }
        private static SandboxClientSettings CopySettings(SandboxClientSettings value) => JsonUtility.FromJson<SandboxClientSettings>(JsonUtility.ToJson(value));
        private bool SettingsDirty => settingsDraft != null && JsonUtility.ToJson(settingsDraft) != JsonUtility.ToJson(savedSettings);

        private void OpenSettings()
        {
            if (settingsOverlay != null) { RequestCloseSettings(); return; }
            ClosePanel(); CloseArmy(); CancelArmyCommand(); PausePlayback();
            settingsDraft = CopySettings(savedSettings);
            settingsDraft.showGrid = gridVisible; settingsDraft.showRoutes = routesVisible; settingsDraft.showFacilities = facilitiesVisible;
            settingsIssues.Clear(); settingsSection = "service";
            healthMessage = serverMode ? "尚未检测 · 当前沙盘状态来自后端席位" : "尚未检测 · 当前沙盘使用本地演示数据";
            settingsOverlay = new VisualElement { name = "settingsOverlay" };
            settingsOverlay.AddToClassList("settings-backdrop"); settingsOverlay.AddToClassList("ui-blocker"); root.Add(settingsOverlay);
            var panel = SettingsGroup(settingsOverlay, "settings-panel");
            var header = SettingsGroup(panel, "settings-header");
            var heading = SettingsGroup(header, "settings-heading");
            AddText(heading, "穹图 / 配置中心", "settings-eyebrow");
            AddText(heading, "沙盘设置", "settings-title");
            AddText(heading, "连接服务、配置智能体与调整观察界面", "settings-header-description");
            AddText(header, "本机配置", "settings-badge");
            var close = AddButton(header, "×", () => RequestCloseSettings(), "settings-close"); close.name = "closeSettings"; close.tooltip = "关闭设置，Esc";
            AddText(panel, RoleLabel(role) + "  ·  第 " + projection.step + "／60 分钟  ·  "
                + (serverMode ? ServerPhaseLabel() : "演示已暂停"), "settings-context");
            var body = SettingsGroup(panel, "settings-body");
            var nav = SettingsGroup(body, "settings-nav"); settingsNav.Clear();
            AddText(nav, "配置分类", "settings-nav-caption");
            SettingsNav(nav, "service", "服务连接");
            SettingsNav(nav, "red", "红方 AI");
            SettingsNav(nav, "blue", "蓝方 AI");
            SettingsNav(nav, "display", "沙盘显示");
            AddText(nav, "AI 参数保存为本机草稿\n显示偏好保存后生效", "settings-nav-note");
            settingsContent = new ScrollView(ScrollViewMode.Vertical) { name = "settingsContent" };
            settingsContent.AddToClassList("settings-content"); body.Add(settingsContent);
            var footer = SettingsGroup(panel, "settings-footer");
            var status = SettingsGroup(footer, "settings-footer-copy");
            settingsSaveState = AddText(status, "", "settings-save-state");
            settingsFeedback = AddText(status, settingsWarning.Length > 0 ? settingsWarning : "显示偏好保存后应用；AI 参数保存为本机草稿。", "settings-note");
            var actions = SettingsGroup(footer, "settings-actions");
            AddButton(actions, "校验", () => ValidateSettings(false), "settings-secondary").name = "validateSettings";
            AddButton(actions, "关闭", () => RequestCloseSettings(), "settings-secondary").name = "cancelSettings";
            settingsSaveButton = AddButton(actions, "保存设置", SaveSettings, "settings-primary"); settingsSaveButton.name = "saveSettings";
            RenderSettingsSection(); UpdateSettingsDirty(); close.Focus();
        }

        private static VisualElement SettingsGroup(VisualElement parent, string style)
        {
            var element = new VisualElement(); element.AddToClassList(style); parent.Add(element); return element;
        }
        private void SettingsNav(VisualElement parent, string key, string title)
        {
            var button = AddButton(parent, title, () => { settingsSection = key; RenderSettingsSection(); }, "settings-nav-item");
            button.name = "settingsNav-" + key;
            if (key == "red" || key == "blue") button.AddToClassList("settings-side-" + key);
            settingsNav[key] = button;
        }
        private void RenderSettingsSection()
        {
            settingsContent.Clear(); settingsFields.Clear(); settingsErrors.Clear();
            settingsContent.scrollOffset = Vector2.zero;
            settingsOverlay.EnableInClassList("settings-red-section", settingsSection == "red");
            settingsOverlay.EnableInClassList("settings-blue-section", settingsSection == "blue");
            foreach (var item in settingsNav) item.Value.EnableInClassList("settings-nav-active", item.Key == settingsSection);
            switch (settingsSection)
            {
                case "service": BuildServiceSettings(); break;
                case "red": BuildSideSettings(settingsDraft.red, "red", "红方"); break;
                case "blue": BuildSideSettings(settingsDraft.blue, "blue", "蓝方"); break;
                case "display": BuildDisplaySettings(); break;
            }
            ShowSettingsErrors();
        }
        private void SettingsSectionTitle(string title, string subtitle)
        {
            AddText(settingsContent, title, "settings-section-title");
            AddText(settingsContent, subtitle, "settings-description");
        }
        private VisualElement SettingsCard(string title, string hint = null)
        {
            var card = SettingsGroup(settingsContent, "settings-card");
            AddText(card, title, "settings-card-title");
            if (!string.IsNullOrEmpty(hint)) AddText(card, hint, "settings-note");
            return card;
        }
        private void BuildServiceSettings()
        {
            SettingsSectionTitle("服务连接", "查看当前对局连接，或检查指定服务地址的可达性。");
            var session = SettingsCard("当前对局连接");
            var connection = SettingsGroup(session, "settings-connection");
            AddText(connection, serverMode && serverClient.Healthy ? "连接正常" : serverMode ? "等待连接" : "本机演示", "settings-connection-state");
            AddText(connection, serverMode ? serverClient.Status : source.SourceLabel, "settings-note");
            var facts = SettingsGroup(session, "settings-session-facts");
            SettingsFact(facts, "当前席位", RoleLabel(role));
            SettingsFact(facts, "对局阶段", serverMode ? ServerPhaseLabel() : "演示已暂停");
            AddText(session, "会话由本机启动入口自动接入。", "settings-note");
            var technical = new Foldout { text = "查看接口信息", value = false }; technical.AddToClassList("settings-details"); session.Add(technical);
            SettingsFact(technical, "角色视图", "GET /games/{game_id}/view");
            SettingsFact(technical, "提交入口", "POST /games/{game_id}/commands");
            SettingsFact(technical, "一致性", "StateContext · Idempotency-Key · 回执核对");
            var card = SettingsCard("连接检测", "检测地址单独保存为本机草稿，当前对局继续使用原有会话连接。");
            var fields = SettingsGroup(card, "settings-grid");
            SettingsText(fields, "serviceBaseUrl", "检测目标地址", settingsDraft.serviceBaseUrl, value => { settingsDraft.serviceBaseUrl = value; CancelHealthCheck(); healthMessage = "地址已修改，请重新检测"; if (healthStatus != null) healthStatus.text = healthMessage; });
            SettingsInteger(fields, "serviceTimeoutSeconds", "检测超时 / 秒", settingsDraft.serviceTimeoutSeconds, value => settingsDraft.serviceTimeoutSeconds = value);
            healthStatus = AddText(card, healthMessage, "settings-notice"); healthStatus.name = "healthStatus";
            var row = SettingsGroup(card, "settings-actions");
            healthButton = AddButton(row, "检测服务", StartHealthCheck, "settings-secondary"); healthButton.name = "testService"; healthButton.SetEnabled(healthRoutine == null);
            AddButton(row, "取消检测", () => { CancelHealthCheck(); healthMessage = "已取消本机健康检测"; healthStatus.text = healthMessage; }, "settings-secondary");
            AddText(card, "检测结果表示 /health 的可达性，模型和席位状态由各自服务提供。", "settings-note");
        }
        private void BuildSideSettings(SandboxSideAiSettings side, string key, string title)
        {
            SettingsSectionTitle(title + " AI 配置", "本机草稿 · 建议生成、向量检索和重排序可分别配置。");
            var roles = SettingsCard("智能体协同", "总指挥统筹，侦察、陆兵与保障智能体分别管理所属单位。");
            var agentRoles = SettingsGroup(roles, "settings-agent-roles");
            foreach (string agentRole in new[] { "总指挥", "侦察", "陆兵", "保障" }) AddText(agentRoles, agentRole, "settings-agent-role");
            AddText(roles, "首次方案与重大调整沿用人工确认流程。", "settings-note");
            BuildModelSettings(side.generation, key + ".generation", "建议生成", true);
            var tuning = new Foldout { text = "调用参数与版本", value = false }; tuning.AddToClassList("settings-card"); settingsContent.Add(tuning);
            var parameters = SettingsGroup(tuning, "settings-grid");
            SettingsText(parameters, key + ".profileVersion", "配置版本", side.profileVersion, value => side.profileVersion = value);
            SettingsText(parameters, key + ".promptVersion", "提示模板版本", side.promptVersion, value => side.promptVersion = value);
            SettingsFloat(parameters, key + ".temperature", "采样温度 / 0–2", side.temperature, value => side.temperature = value);
            SettingsInteger(parameters, key + ".maxOutputTokens", "输出上限 / tokens", side.maxOutputTokens, value => side.maxOutputTokens = value);
            SettingsInteger(parameters, key + ".runTokenBudget", "运行预算 / tokens", side.runTokenBudget, value => side.runTokenBudget = value);
            SettingsInteger(parameters, key + ".timeoutSeconds", "调用超时 / 秒", side.timeoutSeconds, value => side.timeoutSeconds = value);
            SettingsInteger(parameters, key + ".readRetries", "只读重试 / 0–3", side.readRetries, value => side.readRetries = value);
            AddText(tuning, "参数保存为草稿，模型调用由后端接入后执行。", "settings-note");
            BuildModelSettings(side.embedding, key + ".embedding", "向量检索", false);
            BuildModelSettings(side.rerank, key + ".rerank", "重排序", false);
            var evidence = SettingsCard("资料范围", "填写供后端核准的资料集合引用。");
            SettingsText(evidence, key + ".knowledgeScope", "资料集合引用", side.knowledgeScope, value => side.knowledgeScope = value);
        }
        private void BuildModelSettings(SandboxModelEndpoint model, string key, string title, bool expanded)
        {
            var fold = new Foldout { text = title, value = expanded }; fold.AddToClassList("settings-card"); settingsContent.Add(fold);
            AddText(fold, "本机配置草稿 · 等待模型服务接入", "settings-model-status");
            SettingsToggle(fold, key + ".enabled", "记录启用意向", model.enabled, value => model.enabled = value);
            var fields = SettingsGroup(fold, "settings-grid");
            SettingsText(fields, key + ".provider", "供应商 / 协议", model.provider, value => model.provider = value);
            SettingsText(fields, key + ".model", "模型标识", model.model, value => model.model = value);
            SettingsText(fields, key + ".baseUrl", "接口根地址", model.baseUrl, value => model.baseUrl = value);
            SettingsText(fields, key + ".credentialRef", "密钥环境变量名", model.credentialRef, value => model.credentialRef = value);
            AddText(fold, "例如 RED_AI_API_KEY；实际密钥由服务端保管，无鉴权本机模型可留空。", "settings-note");
            var test = AddButton(fold, "模型测试 · 等待后端接口", () => { }, "settings-secondary"); test.SetEnabled(false);
            test.tooltip = "接口契约第 15.3 节尚未定义模型配置、测试与取消路径";
        }
        private void BuildDisplaySettings()
        {
            SettingsSectionTitle("沙盘显示", "调整本机观察图层，保存后应用到当前画面。");
            var layers = SettingsCard("地图图层");
            var grid = SettingsGroup(layers, "settings-display-grid");
            SettingsToggle(grid, "showGrid", "地图网格", settingsDraft.showGrid, value => settingsDraft.showGrid = value, "辅助判断方位和地面位置");
            SettingsToggle(grid, "showRoutes", "道路与路线", settingsDraft.showRoutes, value => settingsDraft.showRoutes = value, "显示地图道路与路线图层");
            SettingsToggle(grid, "showFacilities", "公共设施", settingsDraft.showFacilities, value => settingsDraft.showFacilities = value, "显示公共设施及区域标记");
            SettingsToggle(grid, "showLabels", "地图标签", settingsDraft.showLabels, value => settingsDraft.showLabels = value, "显示单位、兵营和地点名称");
            var rules = new Foldout { text = "仿真与操作说明", value = false }; rules.AddToClassList("settings-card"); settingsContent.Add(rules);
            SettingsFact(rules, "时间", "每次推进 1 分钟 · 截止第 60 分钟");
            SettingsFact(rules, "人工确认", "首次方案与重大调整必须确认");
            SettingsFact(rules, "本地兵营", "只建造或重新选址 · 无手动出兵选项");
            SettingsFact(rules, "出兵流程", "待接入仿真 / AI 指令后由对应兵营出发");
            SettingsFact(rules, "当前单位限制", "本地演示沿用红蓝合计 10 个，非正式契约上限");
            AddText(rules, "初始六个单位和已有存档继续保留。本次不增加自动出兵计时器。", "settings-note");
        }
        private static void SettingsFact(VisualElement parent, string title, string value)
        {
            var row = SettingsGroup(parent, "settings-fact"); AddText(row, title, "settings-fact-key"); AddText(row, value, "settings-fact-value");
        }
        private void RegisterSettingsField(VisualElement parent, VisualElement field, string key)
        {
            var wrapper = SettingsGroup(parent, "settings-field-wrap");
            if (key == "serviceBaseUrl" || key.EndsWith(".baseUrl") || key.EndsWith(".credentialRef")) wrapper.AddToClassList("settings-span-full");
            field.name = "setting-" + key; field.AddToClassList("settings-field"); wrapper.Add(field); settingsFields[key] = field;
            var error = AddText(wrapper, "", "settings-field-error"); error.AddToClassList("hidden"); settingsErrors[key] = error;
        }
        private void SettingsText(VisualElement parent, string key, string label, string value, Action<string> change)
        {
            var field = new TextField(label) { value = value ?? "", maxLength = key.EndsWith("baseUrl") || key == "serviceBaseUrl" ? 512 : 120 };
            RegisterSettingsField(parent, field, key);
            field.RegisterValueChangedCallback(evt => { change(evt.newValue.Trim()); SettingsChanged(); });
        }
        private void SettingsInteger(VisualElement parent, string key, string label, int value, Action<int> change)
        {
            var field = new IntegerField(label) { value = value };
            RegisterSettingsField(parent, field, key); field.RegisterValueChangedCallback(evt => { change(evt.newValue); SettingsChanged(); });
        }
        private void SettingsFloat(VisualElement parent, string key, string label, float value, Action<float> change)
        {
            var field = new FloatField(label) { value = value };
            RegisterSettingsField(parent, field, key); field.RegisterValueChangedCallback(evt => { change(evt.newValue); SettingsChanged(); });
        }
        private void SettingsToggle(VisualElement parent, string key, string label, bool value, Action<bool> change, string description = null)
        {
            var field = new Toggle(label) { value = value };
            RegisterSettingsField(parent, field, key); field.RegisterValueChangedCallback(evt => { change(evt.newValue); SettingsChanged(); });
            if (description != null) AddText(field.parent, description, "settings-toggle-description");
        }
        private void SettingsChanged()
        {
            if (settingsIssues.Count > 0) { settingsIssues = SandboxSettingsValidation.Validate(settingsDraft); ShowSettingsErrors(); }
            settingsFeedback.text = "显示偏好保存后应用；AI 参数保存为本机草稿。"; UpdateSettingsDirty();
        }
        private void UpdateSettingsDirty()
        {
            settingsSaveState.text = SettingsDirty ? "●  有未保存的修改" : "当前无未保存修改";
            settingsSaveState.EnableInClassList("settings-unsaved", SettingsDirty);
            settingsSaveButton.SetEnabled(SettingsDirty);
        }
        private bool ValidateSettings(bool forSave)
        {
            settingsIssues = SandboxSettingsValidation.Validate(settingsDraft);
            if (settingsIssues.Count > 0)
            {
                var first = settingsIssues[0];
                settingsSection = first.field.StartsWith("red.") ? "red" : first.field.StartsWith("blue.") ? "blue" : "service";
                RenderSettingsSection();
                foreach (var fold in settingsContent.Query<Foldout>().ToList()) fold.value = true;
                if (settingsFields.TryGetValue(first.field, out var target)) { target.Focus(); settingsContent.ScrollTo(target); }
                settingsFeedback.text = "!  " + settingsIssues.Count + " 项需修正：" + first.message;
                return false;
            }
            ShowSettingsErrors();
            if (!forSave) settingsFeedback.text = "校验通过 · 仅格式与本地范围检查，未进行模型调用。";
            return true;
        }
        private void ShowSettingsErrors()
        {
            foreach (var item in settingsFields)
            {
                var issue = settingsIssues.FirstOrDefault(error => error.field == item.Key);
                item.Value.EnableInClassList("settings-invalid", issue != null);
                settingsErrors[item.Key].text = issue == null ? "" : "!  " + issue.message;
                settingsErrors[item.Key].EnableInClassList("hidden", issue == null);
            }
        }
        private void SaveSettings()
        {
            if (!ValidateSettings(true)) return;
            if (!settingsStore.TrySave(settingsDraft, out string message)) { settingsFeedback.text = message; return; }
            savedSettings = CopySettings(settingsDraft); settingsWarning = ""; ApplyDisplaySettings(); UpdateSettingsDirty(); settingsFeedback.text = message;
        }
        private void ApplyDisplaySettings()
        {
            gridVisible = savedSettings.showGrid; routesVisible = savedSettings.showRoutes; facilitiesVisible = savedSettings.showFacilities;
            world.SetLayer("grid", gridVisible); world.SetLayer("routes", routesVisible); world.SetLayer("facilities", facilitiesVisible);
            root.Q("mapAnnotations").EnableInClassList("hidden", !savedSettings.showLabels);
        }
        private void RequestCloseSettings(Action after = null)
        {
            if (!SettingsDirty) { CloseSettings(); after?.Invoke(); return; }
            SetText("modalTitle", "放弃未保存的设置？");
            SetText("modalMessage", "未保存的本机配置将被丢弃。地图、兵营和已保存版本保持不变。");
            SetText("confirmModalButton", "放弃修改并关闭");
            modalAction = () => { CloseSettings(); after?.Invoke(); };
            root.Q("modalBackdrop").BringToFront(); root.Q("modalBackdrop").RemoveFromClassList("hidden"); root.Q<Button>("cancelModalButton").Focus();
        }
        private void CloseSettings()
        {
            CancelHealthCheck(); settingsOverlay?.RemoveFromHierarchy(); settingsOverlay = null; settingsDraft = null;
            settingsFields.Clear(); settingsErrors.Clear();
        }
        private void StartHealthCheck()
        {
            string error = SandboxSettingsValidation.EndpointError(settingsDraft.serviceBaseUrl);
            if (error.Length > 0) { healthStatus.text = "!  " + error; return; }
            if (settingsDraft.serviceTimeoutSeconds < 1 || settingsDraft.serviceTimeoutSeconds > 120) { healthStatus.text = "!  检测超时须为 1–120 秒"; return; }
            if (!settingsDraft.serviceBaseUrl.TrimEnd('/').EndsWith("/api/v1", StringComparison.Ordinal)) { healthStatus.text = "!  服务根地址须以 /api/v1 结尾"; return; }
            CancelHealthCheck(); healthButton.SetEnabled(false);
            healthMessage = "正在请求 /health…"; healthStatus.text = healthMessage;
            healthRoutine = StartCoroutine(CheckServiceHealth(settingsDraft.serviceBaseUrl.TrimEnd('/') + "/health", settingsDraft.serviceTimeoutSeconds));
        }
        private IEnumerator CheckServiceHealth(string url, int timeout)
        {
            healthRequest = UnityWebRequest.Get(url); healthRequest.timeout = timeout; healthRequest.redirectLimit = 0;
            yield return healthRequest.SendWebRequest();
            long code = healthRequest.responseCode;
            healthMessage = code == 200 ? "收到 HTTP 200 · 仅证明 /health 可达，未验证 AI 与会话" : code == 503 ? "!  HTTP 503 · 检测目标的核心依赖未就绪" : code > 0 ? "!  HTTP " + code + " · 请核对服务与契约路径" : "!  未能到达服务 · 检查启动状态、地址、跨域与超时";
            healthRequest.Dispose(); healthRequest = null; healthRoutine = null;
            if (settingsOverlay != null && settingsSection == "service") { healthStatus.text = healthMessage; healthButton.SetEnabled(true); }
        }
        private void CancelHealthCheck()
        {
            if (healthRoutine != null) { StopCoroutine(healthRoutine); healthRoutine = null; }
            if (healthRequest != null) { healthRequest.Abort(); healthRequest.Dispose(); healthRequest = null; }
            if (healthButton != null) healthButton.SetEnabled(true);
        }
    }
}
