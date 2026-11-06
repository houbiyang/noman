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
            settingsOverlay.AddToClassList("settings-panel"); settingsOverlay.AddToClassList("ui-blocker"); root.Add(settingsOverlay);
            var header = SettingsGroup(settingsOverlay, "settings-header");
            var heading = SettingsGroup(header, "settings-heading");
            AddText(heading, "SANDBOX / CONFIGURATION", "settings-eyebrow");
            AddText(heading, "沙盘设置", "settings-title");
            var close = AddButton(header, "×", () => RequestCloseSettings(), "settings-close"); close.name = "closeSettings"; close.tooltip = "关闭设置，Esc";
            AddText(settingsOverlay, "本机配置  /  " + RoleLabel(role) + "  /  仿真第 " + projection.step + "／60 分钟 · "
                + (serverMode ? ServerPhaseLabel() : "演示已暂停"), "settings-context");
            var body = SettingsGroup(settingsOverlay, "settings-body");
            var nav = SettingsGroup(body, "settings-nav"); settingsNav.Clear();
            SettingsNav(nav, "service", "01  服务连接");
            SettingsNav(nav, "red", "02  红方 AI");
            SettingsNav(nav, "blue", "03  蓝方 AI");
            SettingsNav(nav, "display", "04  沙盘显示");
            AddText(nav, "红蓝双方可直接配置\nAI 配置保存为本机草稿", "settings-nav-note");
            settingsContent = new ScrollView(ScrollViewMode.Vertical) { name = "settingsContent" };
            settingsContent.AddToClassList("settings-content"); body.Add(settingsContent);
            var footer = SettingsGroup(settingsOverlay, "settings-footer");
            var status = SettingsGroup(footer, "settings-footer-copy");
            settingsSaveState = AddText(status, "", "settings-save-state");
            settingsFeedback = AddText(status, settingsWarning.Length > 0 ? settingsWarning : "AI 配置仅保存在本机，尚未应用到仿真服务。", "settings-note");
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
            SettingsSectionTitle("服务连接", "连接仿真服务后，才可读取真实状态、智能体建议与执行回执。");
            var card = SettingsCard("仿真 API", "接口契约 /api/v1 · JSON / UTF-8");
            SettingsText(card, "serviceBaseUrl", "服务根地址", settingsDraft.serviceBaseUrl, value => { settingsDraft.serviceBaseUrl = value; CancelHealthCheck(); healthMessage = "地址已修改，请重新检测"; if (healthStatus != null) healthStatus.text = healthMessage; });
            SettingsInteger(card, "serviceTimeoutSeconds", "连接检测超时（秒）", settingsDraft.serviceTimeoutSeconds, value => settingsDraft.serviceTimeoutSeconds = value);
            healthStatus = AddText(card, healthMessage, "settings-notice"); healthStatus.name = "healthStatus";
            var row = SettingsGroup(card, "settings-actions");
            healthButton = AddButton(row, "检测服务", StartHealthCheck, "settings-secondary"); healthButton.name = "testService"; healthButton.SetEnabled(healthRoutine == null);
            AddButton(row, "取消检测", () => { CancelHealthCheck(); healthMessage = "已取消本机健康检测"; healthStatus.text = healthMessage; }, "settings-secondary");
            AddText(card, "仅请求 GET /health，不携带凭据、不提交命令。HTTP 可达不代表 AI、数据库或席位会话可用。", "settings-note");
            var session = SettingsCard("会话与执行", "真实会话由受控启动入口签发，不在设置页输入或保存令牌。");
            SettingsFact(session, "当前来源", serverMode ? serverClient.Status : "本地演示 · 未绑定服务器会话");
            SettingsFact(session, "角色视图", "GET /games/{game_id}/view");
            SettingsFact(session, "统一变更", "POST /games/{game_id}/commands");
            SettingsFact(session, "一致性", "StateContext + Idempotency-Key + 回执核对");
            AddText(session, "此处地址用于连接检测草稿。地图会话通过同源启动入口自动接入，修改草稿后继续使用原有会话连接。", "settings-note");
        }
        private void BuildSideSettings(SandboxSideAiSettings side, string key, string title)
        {
            SettingsSectionTitle(title + " AI 接口", title + "配置草稿 · 生成、向量与重排序分别设置，凭据仅填写服务端环境变量名。");
            var roles = SettingsCard("四个逻辑智能体", "AI 总指挥 / 侦察 / 陆兵 / 保障。一个兵种智能体可管理多个单位。");
            AddText(roles, "首次方案和重大调整须人工确认。允许工具、资料范围与过期结果写入资格由后端校验。", "settings-note");
            BuildModelSettings(side.generation, key + ".generation", "建议生成", true);
            var tuning = SettingsCard("调用与版本", "下列数值为可编辑草稿初值，不是已冻结的服务端限制。AI 配置保存后不会开始调用。");
            SettingsText(tuning, key + ".profileVersion", "配置版本", side.profileVersion, value => side.profileVersion = value);
            SettingsText(tuning, key + ".promptVersion", "提示模板版本", side.promptVersion, value => side.promptVersion = value);
            SettingsFloat(tuning, key + ".temperature", "采样温度（0–2）", side.temperature, value => side.temperature = value);
            SettingsInteger(tuning, key + ".maxOutputTokens", "单次输出上限（tokens）", side.maxOutputTokens, value => side.maxOutputTokens = value);
            SettingsInteger(tuning, key + ".runTokenBudget", "单次运行预算（tokens）", side.runTokenBudget, value => side.runTokenBudget = value);
            SettingsInteger(tuning, key + ".timeoutSeconds", "调用超时（秒）", side.timeoutSeconds, value => side.timeoutSeconds = value);
            SettingsInteger(tuning, key + ".readRetries", "只读请求重试上限（0–3）", side.readRetries, value => side.readRetries = value);
            AddText(tuning, "超时不等于远端已停止或未计费。游戏变更须查询原请求结果，不能通过此项盲目重试。", "settings-note");
            BuildModelSettings(side.embedding, key + ".embedding", "向量检索", false);
            BuildModelSettings(side.rerank, key + ".rerank", "重排序", false);
            var evidence = SettingsCard("资料与证据", "混合检索、精确原文定位与版本引用是正式建议的前置能力。");
            SettingsText(evidence, key + ".knowledgeScope", "资料集合引用（待后端核准）", side.knowledgeScope, value => side.knowledgeScope = value);
            AddText(evidence, "这里只记录引用草稿，不改变实际资料授权；不在客户端编辑系统提示、允许工具或隐藏状态。", "settings-note");
        }
        private void BuildModelSettings(SandboxModelEndpoint model, string key, string title, bool expanded)
        {
            var fold = new Foldout { text = title + "  /  待接入", value = expanded }; fold.AddToClassList("settings-card"); settingsContent.Add(fold);
            SettingsToggle(fold, key + ".enabled", "准备启用此配置", model.enabled, value => model.enabled = value);
            SettingsText(fold, key + ".provider", "供应商 / 兼容协议", model.provider, value => model.provider = value);
            SettingsText(fold, key + ".baseUrl", "接口根地址", model.baseUrl, value => model.baseUrl = value);
            SettingsText(fold, key + ".model", "模型标识", model.model, value => model.model = value);
            SettingsText(fold, key + ".credentialRef", "密钥环境变量名（可留空）", model.credentialRef, value => model.credentialRef = value);
            AddText(fold, "例如 RED_AI_API_KEY。实际密钥由服务端保管；无鉴权本机模型可留空。", "settings-note");
            var test = AddButton(fold, "模型测试 · 等待后端接口", () => { }, "settings-secondary"); test.SetEnabled(false);
            test.tooltip = "接口契约第 15.3 节尚未定义模型配置、测试与取消路径";
            AddText(fold, "勾选仅记录启用意向。配置发布、脱敏测试、取消与运行状态待后端接口实现。", "settings-note");
        }
        private void BuildDisplaySettings()
        {
            SettingsSectionTitle("沙盘显示", "本机观察偏好，保存后立即生效，不改变仿真状态或可见权限。");
            var layers = SettingsCard("地图图层");
            SettingsToggle(layers, "showGrid", "显示地图网格", settingsDraft.showGrid, value => settingsDraft.showGrid = value);
            SettingsToggle(layers, "showRoutes", "显示道路与路线", settingsDraft.showRoutes, value => settingsDraft.showRoutes = value);
            SettingsToggle(layers, "showFacilities", "显示公共设施", settingsDraft.showFacilities, value => settingsDraft.showFacilities = value);
            SettingsToggle(layers, "showLabels", "显示地图标签", settingsDraft.showLabels, value => settingsDraft.showLabels = value);
            var rules = SettingsCard("仿真与操作边界", "以下为只读说明，设置页不提供修改规则或自动采用建议的开关。");
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
            field.name = "setting-" + key; field.AddToClassList("settings-field"); parent.Add(field); settingsFields[key] = field;
            var error = AddText(parent, "", "settings-field-error"); error.AddToClassList("hidden"); settingsErrors[key] = error;
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
        private void SettingsToggle(VisualElement parent, string key, string label, bool value, Action<bool> change)
        {
            var field = new Toggle(label) { value = value };
            RegisterSettingsField(parent, field, key); field.RegisterValueChangedCallback(evt => { change(evt.newValue); SettingsChanged(); });
        }
        private void SettingsChanged()
        {
            if (settingsIssues.Count > 0) { settingsIssues = SandboxSettingsValidation.Validate(settingsDraft); ShowSettingsErrors(); }
            settingsFeedback.text = "修改尚未保存。AI 配置须由后端接入后才能应用。"; UpdateSettingsDirty();
        }
        private void UpdateSettingsDirty()
        {
            settingsSaveState.text = SettingsDirty ? "●  有未保存的修改" : "本机配置 · 已与已保存版本一致";
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
