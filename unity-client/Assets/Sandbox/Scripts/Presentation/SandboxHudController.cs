using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>绑定独立 Unity 作战沙盘的 HUD、抽屉和本机操作流程。</summary>
    [DefaultExecutionOrder(100)]
    public sealed class SandboxHudController : MonoBehaviour
    {
        private DemoSandboxViewSource source;
        private SandboxWorld world;
        private SandboxCameraRig cameraRig;
        private SandboxSaveStore saveStore;
        private UIDocument document;
        private PanelSettings panelSettings;
        private Font runtimeFont;
        private VisualElement root;
        private ScrollView drawerContent;
        private SandboxProjection projection;
        private SandboxRole role = SandboxRole.Red;
        private string selectedId;
        private string activePanel;
        private string unitSearch = string.Empty;
        private bool playing;
        private float nextFrameAt;
        private float feedbackUntil;
        private bool gridVisible;
        private bool routesVisible = true;
        private bool facilitiesVisible = true;
        private Action modalAction;
        private readonly Dictionary<string, Label> mapLabels = new Dictionary<string, Label>();

        /// <summary>连接场景、相机和数据适配器，创建原生 UI Toolkit 文档。</summary>
        /// <param name="viewSource">本阶段固定演示来源。</param>
        /// <param name="sandboxWorld">三维表现层。</param>
        /// <param name="rig">独立观察相机。</param>
        public void Initialize(DemoSandboxViewSource viewSource, SandboxWorld sandboxWorld, SandboxCameraRig rig)
        {
            source = viewSource;
            world = sandboxWorld;
            world.SetLayer("grid", gridVisible);
            cameraRig = rig;
            saveStore = new SandboxSaveStore();
            panelSettings = ScriptableObject.CreateInstance<PanelSettings>();
            panelSettings.name = "SandboxRuntimePanel";
            panelSettings.scaleMode = PanelScaleMode.ScaleWithScreenSize;
            panelSettings.referenceResolution = new Vector2Int(1600, 900);
            panelSettings.match = 0.5f;
            panelSettings.themeStyleSheet = Resources.Load<ThemeStyleSheet>("Sandbox/SandboxTheme");
            document = gameObject.AddComponent<UIDocument>();
            document.panelSettings = panelSettings;
            document.visualTreeAsset = Resources.Load<VisualTreeAsset>("Sandbox/SandboxHud");
            if (document.visualTreeAsset == null) throw new InvalidOperationException("缺少 SandboxHud.uxml 界面资源");
            root = document.rootVisualElement.Q("sandboxRoot");
            root.pickingMode = PickingMode.Ignore;
            drawerContent = root.Q<ScrollView>("drawerContent");
#if UNITY_WEBGL && !UNITY_EDITOR
            runtimeFont = Resources.Load<Font>("Fonts/NotoSansCJKsc-Regular");
            if (runtimeFont == null) throw new InvalidOperationException("缺少网页中文字体资源");
#else
            runtimeFont = Font.CreateDynamicFontFromOSFont(new[] { "Microsoft YaHei", "SimHei", "Arial Unicode MS", "Arial" }, 16);
#endif
            root.style.unityFontDefinition = FontDefinition.FromFont(runtimeFont);
            root.RegisterCallback<GeometryChangedEvent>(_ => root.EnableInClassList("compact", root.resolvedStyle.width < 1450));
            cameraRig.IsPointerOverUi = IsPointerOverUi;
            cameraRig.IsEditingUi = () => activePanel != null || modalAction != null;
            cameraRig.UnitSelected += SelectUnit;
            cameraRig.ViewChanged += UpdateCameraButton;
            BindButton("redRole", () => ChangeRole(SandboxRole.Red));
            BindButton("blueRole", () => ChangeRole(SandboxRole.Blue));
            BindButton("hostRole", () => ChangeRole(SandboxRole.Host));
            BindButton("unitsButton", () => TogglePanel("units"));
            BindButton("tasksButton", () => TogglePanel("tasks"));
            BindButton("taskDetailsButton", () => TogglePanel("tasks"));
            BindButton("planButton", () => TogglePanel("plan"));
            BindButton("waitingPlanButton", () => TogglePanel("plan"));
            BindButton("eventsButton", () => TogglePanel("events"));
            BindButton("timelineButton", () => TogglePanel("timeline"));
            BindButton("savesButton", () => TogglePanel("saves"));
            BindButton("layersButton", () => TogglePanel("layers"));
            BindButton("closeDrawerButton", ClosePanel);
            BindButton("followButton", ToggleFollow);
            BindButton("overviewButton", cameraRig.ResetOverview);
            BindButton("zoomInButton", () => cameraRig.Zoom(8));
            BindButton("zoomOutButton", () => cameraRig.Zoom(-8));
            BindButton("stepButton", Advance);
            BindButton("playButton", TogglePlayback);
            BindButton("confirmModalButton", ConfirmModal);
            BindButton("cancelModalButton", CloseModal);
            root.RegisterCallback<KeyDownEvent>(HandleModalKeyboard, TrickleDown.TrickleDown);
            RefreshProjection();
        }

        /// <summary>读取并同步同一版本的三维对象、HUD 与操作能力。</summary>
        private void RefreshProjection()
        {
            projection = source.GetProjection(role);
            world.ApplyProjection(projection);
            RebuildMapLabels();
            if (!projection.units.Any(unit => unit.id == selectedId)) selectedId = projection.units.FirstOrDefault()?.id;
            world.Select(selectedId);
            SetText("sessionTitle", projection.title);
            SetText("sourceLabel", source.SourceLabel);
            SetText("roleCaption", RoleLabel(role));
            SetText("taskName", projection.taskType);
            SetText("taskDescription", TaskDescription(projection.taskType));
            SetText("stepLabel", projection.step.ToString("00") + " / 60");
            string phase = projection.step >= 60 ? "截止步 · 演示结束" : projection.waitingSummary.Length > 0 ? "等待 " + projection.waitingSummary : playing ? "演示播放中" : "演示已暂停";
            SetText("phaseLabel", phase);
            SandboxUnitData selected = projection.units.FirstOrDefault(unit => unit.id == selectedId);
            SetText("selectedSummary", selected != null ? selected.id + "  ·  " + selected.name : "尚未选择单位");
            root.Q("waitingCard").EnableInClassList("hidden", projection.waitingSummary.Length == 0 || projection.step >= 60);
            SetText("waitingTitle", "关键建议待确认 · " + projection.waitingSummary);
            SetText("waitingDetail", role == SandboxRole.Host ? "主持人只查看等待摘要，不能代替双方确认。" : "关闭抽屉不会采用建议，也不会解除推进阻塞。");
            string blocker = source.GetAdvanceBlocker(role);
            root.Q<Button>("stepButton").SetEnabled(blocker.Length == 0 && !playing);
            root.Q<Button>("playButton").SetEnabled(playing || blocker.Length == 0);
            root.Q<Button>("stepButton").tooltip = blocker;
            root.Q<Button>("playButton").tooltip = blocker;
            SetText("playButton", playing ? "暂停演示" : "播放演示");
            root.Q<Button>("eventsButton").SetEnabled(role == SandboxRole.Host && projection.step < 60);
            foreach (SandboxRole candidate in Enum.GetValues(typeof(SandboxRole)))
                root.Q<Button>(candidate == SandboxRole.Red ? "redRole" : candidate == SandboxRole.Blue ? "blueRole" : "hostRole").EnableInClassList("selected-role", role == candidate);
            UpdateCameraButton();
        }

        /// <summary>清理旧角色私有对象、草稿和相机目标后切换角色。</summary>
        private void ChangeRole(SandboxRole next)
        {
            if (next == role) return;
            PausePlayback();
            CloseModal();
            ClosePanel();
            cameraRig.ReturnToOverview();
            world.ClearProjection();
            selectedId = null;
            unitSearch = string.Empty;
            role = next;
            RefreshProjection();
            Notify("已切换为" + RoleLabel(role) + "；当前为本机演示角色审阅");
        }

        /// <summary>只允许选择当前投影内的单位。</summary>
        private void SelectUnit(string unitId)
        {
            if (unitId == null || !projection.units.Any(unit => unit.id == unitId)) return;
            selectedId = unitId;
            if (cameraRig.IsFollowing) cameraRig.Follow(selectedId);
            world.Select(selectedId);
            SandboxUnitData selected = projection.units.FirstOrDefault(unit => unit.id == selectedId);
            SetText("selectedSummary", selected == null ? "尚未选择单位" : selected.id + "  ·  " + selected.name);
            UpdateCameraButton();
            ShowPanel("units");
        }

        /// <summary>展开或关闭同一抽屉，避免同时遮挡地图。</summary>
        private void TogglePanel(string panel)
        {
            if (activePanel == panel) ClosePanel();
            else ShowPanel(panel);
        }

        /// <summary>创建指定功能面板并暂停本地演示播放。</summary>
        private void ShowPanel(string panel)
        {
            PausePlayback();
            activePanel = panel;
            root.Q("drawer").RemoveFromClassList("hidden");
            root.AddToClassList("panel-open");
            drawerContent.Clear();
            switch (panel)
            {
                case "units": BuildUnitsPanel(); break;
                case "tasks": BuildTasksPanel(); break;
                case "plan": BuildPlanPanel(); break;
                case "events": BuildEventsPanel(); break;
                case "timeline": BuildTimelinePanel(); break;
                case "saves": BuildSavesPanel(); break;
                case "layers": BuildLayersPanel(); break;
            }
            root.Q<Button>("closeDrawerButton").Focus();
        }

        /// <summary>关闭抽屉但不改变任何建议处理状态。</summary>
        private void ClosePanel()
        {
            activePanel = null;
            root.Q("drawer").AddToClassList("hidden");
            root.RemoveFromClassList("panel-open");
            drawerContent.Clear();
        }

        /// <summary>显示单位搜索、当前选中详情与观察入口。</summary>
        private void BuildUnitsPanel()
        {
            SetText("drawerTitle", "单位与观察");
            AddText(drawerContent, "只展示当前角色投影中的单位。点选与跟随均不下达行动命令。", "body-text");
            var searchField = new TextField("查找单位") { value = unitSearch, maxLength = 60 };
            searchField.AddToClassList("field");
            drawerContent.Add(searchField);
            var list = new VisualElement();
            drawerContent.Add(list);
            Action render = null;
            render = () =>
            {
                list.Clear();
                SandboxUnitData[] filtered = projection.units.Where(unit => (unit.id + unit.name).IndexOf(unitSearch, StringComparison.OrdinalIgnoreCase) >= 0).ToArray();
                if (filtered.Length == 0) AddText(list, "没有匹配的可见单位。", "empty-message");
                foreach (SandboxUnitData unit in filtered)
                {
                    var card = new VisualElement();
                    card.AddToClassList("detail-card");
                    card.EnableInClassList("selected-unit", unit.id == selectedId);
                    list.Add(card);
                    AddText(card, unit.id + "  " + unit.name, "detail-title");
                    AddText(card, SideLabel(unit.side) + " · " + unit.status, "detail-line");
                    AddText(card, "平面坐标 X " + unit.x.ToString("0.0") + " / Z " + unit.z.ToString("0.0"), "detail-line");
                    var actions = new VisualElement();
                    actions.AddToClassList("action-row");
                    card.Add(actions);
                    AddButton(actions, "选择定位", () => { selectedId = unit.id; world.Select(unit.id); cameraRig.Locate(unit.id); RefreshProjection(); render?.Invoke(); });
                    AddButton(actions, "跟随观察", () => { selectedId = unit.id; world.Select(unit.id); cameraRig.Follow(unit.id); RefreshProjection(); render?.Invoke(); });
                }
            };
            searchField.RegisterValueChangedCallback(change => { unitSearch = change.newValue; render(); });
            render();
        }

        /// <summary>展示当前任务、三节点与前端尚未获得的业务事实。</summary>
        private void BuildTasksPanel()
        {
            SetText("drawerTitle", "任务与协作");
            AddText(drawerContent, projection.taskType, "section-title");
            AddText(drawerContent, TaskDescription(projection.taskType), "body-text");
            string[] nodes = projection.taskType == "关键设施保护" ? new[] { "第 20 步设施检查", "第 40 步设施检查", "第 60 步设施检查" }
                : projection.taskType == "受损设备撤离" ? new[] { "定位受损设备", "接近受损设备", "到达撤离点" }
                : projection.taskType == "物资护送" ? new[] { "第一检查点", "第二检查点", "第三检查点" } : new[] { "A · 西岸检查点", "B · 中央桥梁", "C · 东岸检查点" };
            foreach (string node in nodes)
            {
                var card = new VisualElement();
                card.AddToClassList("detail-card");
                drawerContent.Add(card);
                AddText(card, node, "detail-title");
                AddText(card, "进度待服务端返回；不按镜头或演示步推断完成", "detail-line");
            }
            AddText(drawerContent, "截止第 60 步 · 双方各 20 条资源指令预算", "body-text");
            AddText(drawerContent, "侦察：观察与报告\n陆兵：地面任务执行\n保障：有限补给与运输\n正式任务分数与实际资源消耗待后端返回。", "body-text");
        }

        /// <summary>绑定本方建议草稿，主持人仅看到双方等待摘要。</summary>
        private void BuildPlanPanel()
        {
            SetText("drawerTitle", "建议与人工确认");
            if (role == SandboxRole.Host)
            {
                AddText(drawerContent, projection.waitingSummary.Length == 0 ? "双方已采用当前演示建议" : "等待 " + projection.waitingSummary, "section-title");
                AddText(drawerContent, "主持人不能查看对局中的私有建议详情，也不能代替任何一方确认。", "body-text");
                return;
            }
            SandboxPlanData draft = projection.ownPlan.Copy();
            AddText(drawerContent, SideLabel(role) + " · 演示建议 v" + draft.version, "section-title");
            AddText(drawerContent, "固定前端建议样例。尚未调用真实模型或检索资料，不提供伪造引用。", "body-text");
            AddText(drawerContent, "状态：" + PlanStatusLabel(draft.status), "detail-title");
            bool editable = draft.status == SandboxPlanStatus.Pending && projection.step < 60;
            AddPlanTextField("任务优先级", draft.priority, editable, value => draft.priority = value);
            AddPlanTextField("侦察无人机分工", draft.reconAssignment, editable, value => draft.reconAssignment = value);
            AddPlanTextField("陆兵分工", draft.groundAssignment, editable, value => draft.groundAssignment = value);
            AddPlanTextField("保障分工", draft.supportAssignment, editable, value => draft.supportAssignment = value);
            var allowance = new IntegerField("本次拟用资源额度") { value = draft.resourceAllowance };
            allowance.AddToClassList("field");
            allowance.SetEnabled(editable);
            allowance.RegisterValueChangedCallback(change => draft.resourceAllowance = change.newValue);
            drawerContent.Add(allowance);
            AddText(drawerContent, "资源额度允许 0—20。采用建议与实际执行、扣费、完成任务分别记录。", "body-text");
            Label validation = AddText(drawerContent, string.Empty, "validation-message");
            if (editable)
            {
                AddButton(drawerContent, "确认采用本方建议", () =>
                {
                    if (!source.TryApprove(role, draft, out string message)) { validation.text = message; return; }
                    RefreshProjection();
                    ShowPanel("plan");
                    Notify(message);
                }, "primary-button");
                AddButton(drawerContent, "驳回本方建议", () =>
                {
                    source.TryReject(role, draft.version, out string message);
                    RefreshProjection();
                    ShowPanel("plan");
                    Notify(message);
                });
            }
            else if (projection.step < 60)
                AddButton(drawerContent, "重新载入固定演示建议", () =>
                {
                    source.TryReloadDemoPlan(role, out string message);
                    RefreshProjection();
                    ShowPanel("plan");
                    Notify(message);
                });
        }

        /// <summary>展示主持人事件预览并以输入与修订共同限制登记。</summary>
        private void BuildEventsPanel()
        {
            SetText("drawerTitle", "事件预览与登记");
            if (role != SandboxRole.Host) { AddText(drawerContent, "只有主持人可以注入事件。", "body-text"); return; }
            var eventType = new DropdownField("事件类型", DemoSandboxViewSource.EventTypes.ToList(), 0);
            var target = new DropdownField("影响单位", projection.units.Select(unit => unit.id).ToList(), 0);
            eventType.AddToClassList("field");
            target.AddToClassList("field");
            drawerContent.Add(eventType);
            drawerContent.Add(target);
            AddText(drawerContent, "事件登记只形成前端记录。故障、通信、天气和目标变化的实际效果由后端结算；登记本身不扣安全分。", "body-text");
            Label preview = AddText(drawerContent, "更改类型或目标后需要重新预览。", "evidence-box");
            int previewRevision = -1;
            string previewType = null;
            string previewTarget = null;
            Button apply = null;
            Action invalidate = () =>
            {
                previewRevision = -1;
                preview.text = "输入已变化，请重新预览影响。";
                apply?.SetEnabled(false);
            };
            eventType.RegisterValueChangedCallback(_ => invalidate());
            target.RegisterValueChangedCallback(_ => invalidate());
            AddButton(drawerContent, "预览影响", () =>
            {
                previewRevision = projection.revision;
                previewType = eventType.value;
                previewTarget = target.value;
                preview.text = "T+" + projection.step + " · " + previewType + "\n目标：" + previewTarget + "\n" + EventDescription(previewType) + "\n此处仅预览与登记，未执行规则。";
                apply.SetEnabled(projection.step < 60);
            });
            apply = AddButton(drawerContent, "确认登记当前事件", () =>
            {
                if (previewType != eventType.value || previewTarget != target.value) { invalidate(); return; }
                bool success = source.TryRegisterEvent(role, previewType, previewTarget, previewRevision, out string message);
                RefreshProjection();
                if (success) ShowPanel("events");
                else invalidate();
                Notify(message);
            }, "primary-button");
            apply.SetEnabled(false);
        }

        /// <summary>显示角色允许查看的实际前端操作记录。</summary>
        private void BuildTimelinePanel()
        {
            SetText("drawerTitle", "本次演示时间线");
            AddText(drawerContent, "仅记录本地操作及来源明确的样例。不把记录登记当作仿真执行完成。", "body-text");
            foreach (SandboxRecord record in projection.records.Reverse())
            {
                var card = new VisualElement();
                card.AddToClassList("detail-card");
                drawerContent.Add(card);
                AddText(card, "T+" + record.step.ToString("00") + "  " + record.title, "detail-title");
                AddText(card, record.detail, "detail-line");
                AddText(card, record.isPublic ? "公开记录" : SideLabel(record.side) + "记录", "muted");
            }
        }

        /// <summary>提供本机前端快照保存、目录读取与明确恢复确认。</summary>
        private void BuildSavesPanel()
        {
            SetText("drawerTitle", Application.platform == RuntimePlatform.WebGLPlayer ? "浏览器前端存档" : "本机前端存档");
            AddText(drawerContent, "保存双方演示状态与操作记录。它不包含正式仿真数据库、AI 检查点或 Unity 相机状态。", "body-text");
            if (Application.platform == RuntimePlatform.WebGLPlayer)
                AddText(drawerContent, "仅保存在当前浏览器和当前网址目录下；更换浏览器或访问地址不会共享存档。清理站点数据会移除记录，无痕窗口关闭后可能不保留。", "body-text");
            if (role != SandboxRole.Host) { AddText(drawerContent, "整局存档与恢复由主持人操作。", "section-title"); return; }
            var name = new TextField("存档名称") { value = "青岚河谷 · 第 " + projection.step + " 步", maxLength = 60 };
            name.AddToClassList("field");
            drawerContent.Add(name);
            AddButton(drawerContent, "保存当前前端快照", () =>
            {
                if (saveStore.TrySave(name.value, source.Current, out string message)) ShowPanel("saves");
                Notify(message);
            }, "primary-button");
            string exitTarget = Application.platform == RuntimePlatform.WebGLPlayer ? "沙盘" : "客户端";
            AddButton(drawerContent, "保存并退出" + exitTarget, () => OpenExitModal(true, name.value));
            AddButton(drawerContent, "不新增存档，退出" + exitTarget, () => OpenExitModal(false, name.value));
            SandboxLocalSave[] saves = saveStore.ReadAll(out string warning);
            if (warning.Length > 0) AddText(drawerContent, warning, "validation-message");
            AddText(drawerContent, "已保存 " + saves.Length + " 条", "section-title");
            if (saves.Length == 0) AddText(drawerContent, "暂无本地存档。保存后可在再次进入沙盘时重新读取。", "empty-message");
            foreach (SandboxLocalSave save in saves)
            {
                var card = new VisualElement();
                card.AddToClassList("detail-card");
                drawerContent.Add(card);
                AddText(card, save.name, "detail-title");
                AddText(card, DateTimeOffset.Parse(save.savedAt).LocalDateTime.ToString("yyyy-MM-dd HH:mm") + " · T+" + save.snapshot.step, "detail-line");
                AddButton(card, "查看并恢复", () => OpenRestoreModal(save));
            }
        }

        /// <summary>绑定独立显示图层开关。</summary>
        private void BuildLayersPanel()
        {
            SetText("drawerTitle", "地图图层");
            AddText(drawerContent, "仅改变公开场景的显示，不修改道路合法性、任务和单位状态。", "body-text");
            AddLayerToggle("坐标网格", gridVisible, value => { gridVisible = value; world.SetLayer("grid", value); });
            AddLayerToggle("公开道路", routesVisible, value => { routesVisible = value; world.SetLayer("routes", value); });
            AddLayerToggle("设施与任务点", facilitiesVisible, value => { facilitiesVisible = value; world.SetLayer("facilities", value); });
        }

        /// <summary>显示恢复前的覆盖范围，确认后以独立副本替换演示。</summary>
        private void OpenRestoreModal(SandboxLocalSave save)
        {
            PausePlayback();
            SetText("modalTitle", "恢复到保存的时刻？");
            SetText("confirmModalButton", "确认恢复前端快照");
            SetText("modalMessage", save.name + "\nT+" + save.snapshot.step + " · " + save.snapshot.title + "\n恢复将替换当前双方演示状态，保持暂停，并清除旧选择、草稿和跟随目标。");
            modalAction = () =>
            {
                SandboxSessionData restored = JsonUtility.FromJson<SandboxSessionData>(JsonUtility.ToJson(save.snapshot));
                if (source.TryRestore(restored, out string message))
                {
                    cameraRig.ResetOverview();
                    world.ClearProjection();
                    selectedId = null;
                    ClosePanel();
                    RefreshProjection();
                }
                Notify(message);
            };
            root.Q("modalBackdrop").RemoveFromClassList("hidden");
            root.Q<Button>("cancelModalButton").Focus();
        }

        /// <summary>暂停演示并确认本次客户端退出，保存失败时保留当前画面。</summary>
        /// <param name="saveFirst">是否先保存独立前端快照。</param>
        /// <param name="saveName">用户填写的存档名称。</param>
        private void OpenExitModal(bool saveFirst, string saveName)
        {
            if (role != SandboxRole.Host) return;
            PausePlayback();
            SetText("modalTitle", saveFirst ? "保存此刻，再退出？" : "退出当前沙盘？");
            SetText("modalMessage", saveFirst ? "将保存双方前端演示状态和时间线。写入成功后才退出沙盘。" : "不会新增存档；已保存的记录仍然保留。当前未保存的演示修改将在退出后丢失。");
            SetText("confirmModalButton", saveFirst ? "确认保存并退出" : "确认不新增存档退出");
            modalAction = () =>
            {
                if (saveFirst && !saveStore.TrySave(saveName, source.Current, out string error)) { Notify(error); return; }
#if UNITY_EDITOR
                UnityEditor.EditorApplication.isPlaying = false;
#elif UNITY_WEBGL
                SandboxBrowserStorage.Exit();
#else
                Application.Quit();
#endif
            };
            root.Q("modalBackdrop").RemoveFromClassList("hidden");
            root.Q<Button>("cancelModalButton").Focus();
        }

        /// <summary>执行当前明确选择的确认操作。</summary>
        private void ConfirmModal()
        {
            Action confirmed = modalAction;
            CloseModal();
            confirmed?.Invoke();
        }

        /// <summary>取消恢复，不改变任何演示状态。</summary>
        private void CloseModal()
        {
            modalAction = null;
            root.Q("modalBackdrop").AddToClassList("hidden");
            if (activePanel != null) root.Q<Button>("closeDrawerButton").Focus();
        }

        /// <summary>在恢复对话框内约束键盘焦点，防止误操作底层按钮。</summary>
        private void HandleModalKeyboard(KeyDownEvent keyboardEvent)
        {
            if (modalAction == null || keyboardEvent.keyCode != KeyCode.Tab) return;
            Button confirm = root.Q<Button>("confirmModalButton");
            Button cancel = root.Q<Button>("cancelModalButton");
            if (root.focusController.focusedElement == confirm) cancel.Focus();
            else confirm.Focus();
            root.focusController.IgnoreEvent(keyboardEvent);
            keyboardEvent.StopPropagation();
        }

        /// <summary>开始或暂停明确标注的前端演示播放。</summary>
        private void TogglePlayback()
        {
            if (playing) { PausePlayback(); return; }
            string blocker = source.GetAdvanceBlocker(role);
            if (blocker.Length > 0) { Notify(blocker); return; }
            ClosePanel();
            playing = true;
            nextFrameAt = Time.unscaledTime + 2;
            RefreshProjection();
        }

        /// <summary>停止本地计时，不影响当前建议状态。</summary>
        private void PausePlayback()
        {
            playing = false;
            if (source != null && root != null) RefreshProjection();
        }

        /// <summary>通过同一适配器推进一个演示步。</summary>
        private void Advance()
        {
            if (source.TryAdvance(role, out string message))
            {
                if (source.Current.step == 60) playing = false;
                RefreshProjection();
            }
            else playing = false;
            if (!playing) Notify(message);
        }

        /// <summary>切换当前可见单位的跟随观察。</summary>
        private void ToggleFollow()
        {
            if (cameraRig.IsFollowing) cameraRig.ReturnToOverview();
            else if (selectedId == null || !cameraRig.Follow(selectedId)) Notify("请先选择一个当前可见单位");
            UpdateCameraButton();
        }

        /// <summary>同步相机模式按钮和可用状态。</summary>
        private void UpdateCameraButton()
        {
            if (root == null) return;
            SetText("followButton", cameraRig.IsFollowing ? "返回总览" : "跟随单位");
            root.Q<Button>("followButton").SetEnabled(selectedId != null || cameraRig.IsFollowing);
        }

        /// <summary>将屏幕指针转换到面板坐标，仅拦截实际交互区域。</summary>
        private bool IsPointerOverUi(Vector2 screenPosition)
        {
            if (root.panel == null) return true;
            Vector2 point = RuntimePanelUtils.ScreenToPanel(root.panel, new Vector2(screenPosition.x, Screen.height - screenPosition.y));
            VisualElement target = root.panel.Pick(point);
            while (target != null)
            {
                if (target.ClassListContains("ui-blocker")) return true;
                target = target.parent;
            }
            return false;
        }

        /// <summary>处理本地播放和退出观察快捷键，时间不按后台停留补步。</summary>
        private void Update()
        {
            if (root == null) return;
            if (playing && Time.unscaledTime >= nextFrameAt)
            {
                Advance();
                nextFrameAt = Time.unscaledTime + 2;
            }
            if (feedbackUntil > 0 && Time.unscaledTime > feedbackUntil)
            {
                root.Q("feedbackLabel").AddToClassList("hidden");
                feedbackUntil = 0;
            }
            Keyboard keyboard = Keyboard.current;
            if (keyboard == null) return;
            if (keyboard.escapeKey.wasPressedThisFrame)
            {
                if (modalAction != null) CloseModal();
                else if (activePanel != null) ClosePanel();
                else cameraRig.ReturnToOverview();
            }
            if (keyboard.fKey.wasPressedThisFrame && activePanel == null && modalAction == null) ToggleFollow();
        }

        /// <summary>失焦后停止播放，避免后台继续推进演示。</summary>
        private void OnApplicationFocus(bool focused)
        {
            if (!focused && root != null) PausePlayback();
        }

        /// <summary>按当前投影重建轻量标签，切角色时不保留对方标识。</summary>
        private void RebuildMapLabels()
        {
            VisualElement annotations = root.Q("mapAnnotations");
            annotations.Clear();
            mapLabels.Clear();
            foreach (SandboxUnitData unit in projection.units)
            {
                var label = new Label(unit.id) { pickingMode = PickingMode.Ignore };
                label.AddToClassList("map-label");
                label.AddToClassList(unit.side == SandboxRole.Red ? "red-text" : "blue-text");
                annotations.Add(label);
                mapLabels.Add(unit.id, label);
            }
            foreach (SandboxMapPoint point in world.MapPoints)
            {
                var label = new Label(point.name) { pickingMode = PickingMode.Ignore };
                label.AddToClassList("map-label");
                annotations.Add(label);
                mapLabels.Add(point.id, label);
            }
        }

        /// <summary>在相机完成观察更新后定位屏幕标签，不改变业务坐标。</summary>
        private void LateUpdate()
        {
            if (root?.panel == null) return;
            foreach (KeyValuePair<string, Label> pair in mapLabels)
            {
                Transform unit = world.FindUnit(pair.Key);
                SandboxMapPoint point = unit == null ? world.MapPoints.FirstOrDefault(candidate => candidate.id == pair.Key) : null;
                if (unit == null && (point == null || !world.FacilitiesVisible)) { pair.Value.style.display = DisplayStyle.None; continue; }
                Vector3 position = unit != null ? unit.position + Vector3.up * 2.3f : SandboxWorld.Surface(point.x, point.z) + Vector3.up * (point.kind == "camp" || point.kind == "facility" || point.kind == "task" ? 4 : 2);
                Vector3 screen = cameraRig.WorldToScreen(position);
                Vector2 panelPosition = RuntimePanelUtils.ScreenToPanel(root.panel, new Vector2(screen.x, Screen.height - screen.y));
                bool visible = screen.z > 0 && panelPosition.x > 0 && panelPosition.x < root.resolvedStyle.width && panelPosition.y > 16 && panelPosition.y < root.resolvedStyle.height - 120;
                pair.Value.style.display = visible ? DisplayStyle.Flex : DisplayStyle.None;
                if (!visible) continue;
                pair.Value.style.left = panelPosition.x - 30;
                pair.Value.style.top = panelPosition.y;
            }
        }

        /// <summary>追加具有完整文字说明的表单字段。</summary>
        private void AddPlanTextField(string label, string value, bool editable, Action<string> changed)
        {
            var field = new TextField(label) { value = value, maxLength = 120, multiline = true, isReadOnly = !editable };
            field.AddToClassList("field");
            field.RegisterValueChangedCallback(change => changed(change.newValue));
            drawerContent.Add(field);
        }

        /// <summary>追加一个受控图层开关。</summary>
        private void AddLayerToggle(string label, bool initial, Action<bool> changed)
        {
            var toggle = new Toggle(label) { value = initial };
            toggle.AddToClassList("field");
            toggle.RegisterValueChangedCallback(change => changed(change.newValue));
            drawerContent.Add(toggle);
        }

        /// <summary>按名称绑定静态界面按钮。</summary>
        private void BindButton(string name, Action action) => root.Q<Button>(name).clicked += action;

        /// <summary>更新标签或按钮的文字。</summary>
        private void SetText(string name, string text) => root.Q<TextElement>(name).text = text;

        /// <summary>追加可换行的文字元素。</summary>
        private static Label AddText(VisualElement parent, string text, string className)
        {
            var label = new Label(text);
            label.AddToClassList(className);
            parent.Add(label);
            return label;
        }

        /// <summary>追加绑定操作的原生按钮。</summary>
        private static Button AddButton(VisualElement parent, string text, Action action, string className = null)
        {
            var button = new Button(action) { text = text };
            if (className != null) button.AddToClassList(className);
            parent.Add(button);
            return button;
        }

        /// <summary>显示短暂且不会覆盖状态事实的操作反馈。</summary>
        private void Notify(string message)
        {
            SetText("feedbackLabel", message);
            root.Q("feedbackLabel").RemoveFromClassList("hidden");
            feedbackUntil = Time.unscaledTime + 5;
        }

        /// <summary>返回当前角色的中文显示名。</summary>
        private static string RoleLabel(SandboxRole value) => value == SandboxRole.Host ? "中立主持人" : SideLabel(value) + "操作员";

        /// <summary>返回阵营名称。</summary>
        private static string SideLabel(SandboxRole value) => value == SandboxRole.Red ? "红方" : value == SandboxRole.Blue ? "蓝方" : "主持人";

        /// <summary>区分建议处理状态和真正动作执行状态。</summary>
        private static string PlanStatusLabel(SandboxPlanStatus status) => status == SandboxPlanStatus.Approved ? "已采用，未代表动作执行" : status == SandboxPlanStatus.Rejected ? "已驳回，尚未有新建议" : "待本方确认";

        /// <summary>返回已确定任务模板的简明说明。</summary>
        private static string TaskDescription(string taskType) => taskType == "区域巡查" ? "西岸检查 → 中央桥梁 → 东岸检查" : taskType == "物资护送" ? "依次通过三个护送检查点" : taskType == "关键设施保护" ? "在第 20、40、60 步保持设施正常" : "定位 → 接近 → 到达撤离点";

        /// <summary>展示已确认事件行为，保留具体区间由规则设计决定的边界。</summary>
        private static string EventDescription(string eventType) => eventType == "设备故障" ? "指定单位暂停 5 步，具体生效区间待规则确认。" : eventType == "通信中断" ? "受影响报告标为未知 5 步，不将缺失信息补成零。" : eventType == "天气变化" ? "受影响任务进度延迟 5 步。" : "替换目标至本局结束，旧进度保留供复盘。";

        /// <summary>清理相机回调和运行时界面资源。</summary>
        private void OnDestroy()
        {
            playing = false;
            if (cameraRig != null)
            {
                cameraRig.UnitSelected -= SelectUnit;
                cameraRig.ViewChanged -= UpdateCameraButton;
                cameraRig.IsPointerOverUi = null;
                cameraRig.IsEditingUi = null;
            }
            if (panelSettings != null) Destroy(panelSettings);
#if !UNITY_WEBGL || UNITY_EDITOR
            if (runtimeFont != null) Destroy(runtimeFont);
#endif
        }
    }
}
