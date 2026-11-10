using System;
using System.Globalization;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>地图主页面自动接入本机后端，并按有效席位发布服务器状态。</summary>
    public sealed partial class SandboxHudController
    {
        private SandboxServerClient serverClient;
        private bool serverMode, serverRefreshPanel;
        private string serverHead;
        private VisualElement connectionOverlay;
        private SandboxServerSave[] serverSaves = Array.Empty<SandboxServerSave>();
        private bool serverSavesLoading;
        private bool serverSavesLoaded;
        private int serverSavesOffset;

        /// <summary>建立连接组件，读取地图身份并绑定状态发布事件。</summary>
        private void InitializeServerSync()
        {
            SandboxMapData map = SandboxMapFactory.LoadDefinition();
            serverClient = gameObject.AddComponent<SandboxServerClient>();
            serverClient.Initialize(map.mapId, map.contentHash);
            serverClient.Feedback += Notify;
            serverClient.Changed += OnServerChanged;
            serverClient.CommandFinished += OnServerCommandFinished;
            BindButton("connectionAction", OpenConnection);
            BeginServerConnection(role);
        }

        /// <summary>清理旧席位画面后自动接入所选角色，不保留旧角色的私有视图。</summary>
        /// <param name="next">待接入的本机对局角色。</param>
        private void BeginServerConnection(SandboxRole next)
        {
            if (serverClient.Busy || serverClient.HasPending) { Notify("请等待同步或先核对原命令结果"); return; }
            playing = false; serverMode = true; role = next; serverHead = null; serverBranch = null;
            deploymentMovingUnit = null;
            Application.runInBackground = true;
            serverSaves = Array.Empty<SandboxServerSave>(); serverSavesLoaded = false; serverSavesOffset = 0;
            ClosePanel(); CloseArmy(); CancelArmyCommand(); CloseModal();
            world.ClearProjection(); selectedId = null; cameraRig.ReturnToOverview();
            serverClient.ConnectAutomatically(next == SandboxRole.Red ? "red" : next == SandboxRole.Blue ? "blue" : "host");
            RefreshProjection();
        }

        /// <summary>后端保存退出成功后才结束当前客户端，失败时保留有效页面。</summary>
        private void OnServerCommandFinished(string commandType, bool succeeded)
        {
            if (!succeeded && (commandType == "movement_order" || commandType == "advance_deployment_movement")) deploymentMovingUnit = null;
            if (succeeded && commandType == "archive_match") { serverHistoryOwner = null; if (reportSection == "history" && reportOverlay != null) RenderSettingsSection(); }
            if (succeeded && (commandType == "create_save" || commandType == "complete_exit")) serverSavesLoaded = false;
            if (commandType != "complete_exit" || !succeeded) return;
#if UNITY_EDITOR
            UnityEditor.EditorApplication.isPlaying = false;
#elif UNITY_WEBGL
            SandboxBrowserStorage.Exit();
#else
            Application.Quit();
#endif
        }

        /// <summary>只用新发布的完整视图更新场景，不叠加旧角色或旧分支对象。</summary>
        private void OnServerChanged()
        {
            if (!serverMode) return;
            string head = serverClient.View?.context.snapshot_id;
            bool changed = head != serverHead;
            if (serverClient.View == null || (serverClient.View.context.branch_id != serverBranch && serverBranch != null))
            {
                world.ClearProjection(); cameraRig.ReturnToOverview(); selectedId = null;
                CloseArmy(); CancelArmyCommand(); CloseModal();
            }
            serverBranch = serverClient.View?.context.branch_id;
            serverHead = head;
            if (changed) serverSavesLoaded = false;
            RefreshProjection();
            if (changed && !serverClient.Busy && armyOverlay != null)
            {
                if (managedArmyType.HasValue) refreshArmyManagement?.Invoke();
                else RenderArmy();
            }
            if (!serverClient.Busy && activePanel != null && (serverRefreshPanel || (changed && activePanel != "plan" && activePanel != "events")))
            {
                string panel = activePanel; serverRefreshPanel = false; ShowPanel(panel);
            }
        }

        private string serverBranch;

        /// <summary>建立空的等待视图，连接失败时不退回本地样例。</summary>
        private SandboxProjection ServerProjection()
        {
            return serverClient.View == null ? new SandboxProjection { role = role, title = "等待后端对局状态", taskType = "等待后端",
                units = Array.Empty<SandboxUnitData>(), records = Array.Empty<SandboxRecord>(), waitingSummary = string.Empty }
                : SandboxServerProjection.Convert(serverClient.View);
        }

        /// <summary>显示服务端真实生命周期，不再根据前端播放变量推算。</summary>
        private string ServerPhaseLabel()
        {
            if (serverClient.View == null) return serverClient.Busy ? "正在接入后端" : "等待后端服务";
            if (!serverClient.Healthy) return "同步中断 · 写入停用";
            switch (serverClient.View?.clock.phase)
            {
                case "preparing": return "准备部署中";
                case "awaiting_confirmation": return "等待双方确认";
                case "running": return "后端播放中";
                case "finished": return "截止步 · 演示结束";
                default: return "后端已暂停";
            }
        }

        /// <summary>角色与操作资格同时以服务端会话及当前视图为准。</summary>
        private void RefreshServerControls()
        {
            bool writable = serverClient.Healthy && !serverClient.Busy && !serverClient.HasPending && serverClient.View != null;
            SandboxServerCapabilities permission = serverClient.View?.capabilities;
            foreach (string name in new[] { "redRole", "blueRole", "hostRole" }) root.Q<Button>(name).SetEnabled(!serverClient.Busy && !serverClient.HasPending);
            root.Q<Button>("stepButton").SetEnabled(writable && permission != null && permission.can_advance && serverClient.View.clock.phase != "running");
            root.Q<Button>("playButton").SetEnabled(writable && permission != null && (serverClient.View.clock.phase == "running" || permission.can_advance));
            SetText("playButton", serverClient.View?.clock.phase == "running" ? "暂停" : "继续播放");
            root.Q<Button>("armyAction").SetEnabled(serverClient.View != null);
            root.Q<Button>("eventsButton").SetEnabled(writable && role == SandboxRole.Host && serverClient.View.clock.phase != "finished");
            SetText("connectionAction", serverClient.HasPending ? "核对提交结果" : serverClient.Healthy ? "后端已连接" : "接入状态");
            SetText("taskProgress", serverClient.View == null ? "等待后端任务状态" : "任务节点状态来自后端 · 规则未裁决时明确标记");
            if (!serverClient.Healthy || serverClient.View == null) { CancelArmyCommand(); CloseArmy(); }
            else armyOverlay?.SetEnabled(writable);
        }

        /// <summary>显示自动接入状态及重试入口，不要求用户填写地址、对局或令牌。</summary>
        private void OpenConnection()
        {
            connectionOverlay?.RemoveFromHierarchy();
            connectionOverlay = new VisualElement(); connectionOverlay.AddToClassList("modal-backdrop"); connectionOverlay.AddToClassList("ui-blocker"); root.Add(connectionOverlay);
            var card = new VisualElement(); card.AddToClassList("modal-card"); connectionOverlay.Add(card);
            AddText(card, "后端接入状态", "drawer-title");
            AddText(card, serverClient.Status + "\n打开地图后自动接入，刷新复用当前对局。角色按钮切换对应席位。", "body-text");
            AddButton(card, "重新接入", () => {
                connectionOverlay.RemoveFromHierarchy(); connectionOverlay = null; BeginServerConnection(role);
            }, "primary-button").SetEnabled(!serverClient.Busy && !serverClient.HasPending);
            if (serverMode)
            {
                AddButton(card, "重新读取状态", serverClient.RefreshNow);
                if (serverClient.HasPending) AddButton(card, "以原请求核对提交结果", serverClient.RetryPending, "primary-button");
            }
            AddButton(card, "关闭", () => { connectionOverlay.RemoveFromHierarchy(); connectionOverlay = null; });
        }

        /// <summary>操作只排入后端命令，成功提交后的刷新负责改变画面。</summary>
        private void SendServerCommand(string kind, object payload = null, SandboxServerContext basis = null)
        {
            serverRefreshPanel = true;
            serverClient.Command(kind, payload, basis);
        }

        /// <summary>显示当前权威任务、节点及准备流程。</summary>
        private void BuildServerTasks()
        {
            SetText("drawerTitle", "后端任务与协作");
            if (serverClient.View == null) { AddText(drawerContent, serverClient.Status, "body-text"); return; }
            SandboxServerView view = serverClient.View;
            if (view.clock.phase == "preparing") AddButton(drawerContent, "配置兵营", OpenArmy);
            AddText(drawerContent, "红方准备：" + (view.clock.ready.red ? "已就绪" : "未就绪") + " · 蓝方准备：" + (view.clock.ready.blue ? "已就绪" : "未就绪"), "section-title");
            if (view.capabilities.can_set_ready) AddButton(drawerContent, "本方准备完成", () => SendServerCommand("set_ready", new SandboxServerReadyInput { ready = true }));
            if (role == SandboxRole.Host && view.clock.phase == "preparing")
            {
                var task = new DropdownField("双方任务模板", DemoSandboxViewSource.TaskTypes.ToList(), 0); drawerContent.Add(task);
                AddButton(drawerContent, "保存双方任务模板", () => SendServerCommand("set_tasks", new SandboxServerTaskInput { task_type = new[] { "patrol", "escort", "protect", "evacuate" }[task.index] }));
                AddButton(drawerContent, "双方准备后开始", () => SendServerCommand("start_game")).SetEnabled(view.capabilities.can_start_game);
            }
            foreach (SandboxServerTask task in view.tasks)
            {
                AddText(drawerContent, SideLabel(SandboxServerProjection.Role(task.side)) + " · " + task.title, "detail-title");
                foreach (SandboxServerTaskNode node in task.nodes) AddText(drawerContent, node.node_id + " · " + ServerTaskStatus(node.status), "detail-line");
            }
            AddText(drawerContent, "当前后端未实施正式任务裁决；不会根据地图动画自动完成节点。", "body-text");
        }

        /// <summary>冻结编辑时依据，期间状态变化会由后端拒绝旧提交。</summary>
        private void BuildServerPlan()
        {
            SetText("drawerTitle", "后端建议与人工确认");
            if (serverClient.View == null || role == SandboxRole.Host) { AddText(drawerContent, "主持人仅查看等待摘要：" + projection.waitingSummary, "body-text"); return; }
            SandboxPlanData draft = projection.ownPlan.Copy();
            SandboxServerPlan plan = serverClient.View.decisions.own_plan;
            SandboxServerContext basis = serverClient.View.context;
            bool editable = serverClient.View.capabilities.can_review_proposal;
            AddText(drawerContent, "固定样例建议 · " + PlanStatusLabel(draft.status) + "\n来源、版本和处理记录由后端保存，尚未调用真实 AI。", "body-text");
            AddPlanTextField("任务优先级", draft.priority, editable, value => draft.priority = value);
            AddPlanTextField("侦察分工", draft.reconAssignment, editable, value => draft.reconAssignment = value);
            AddPlanTextField("地面分工", draft.groundAssignment, editable, value => draft.groundAssignment = value);
            AddPlanTextField("保障分工", draft.supportAssignment, editable, value => draft.supportAssignment = value);
            var allowance = new IntegerField("资源额度") { value = draft.resourceAllowance }; allowance.SetEnabled(editable);
            allowance.RegisterValueChangedCallback(change => draft.resourceAllowance = change.newValue); drawerContent.Add(allowance);
            if (editable)
            {
                AddButton(drawerContent, "确认当前分工并采用", () => SendServerCommand("review_proposal", new SandboxServerReview {
                    proposal_id = plan.proposal_id, decision = "modify_approve", changes = new SandboxServerPlanChanges {
                        priority = draft.priority, recon_assignment = draft.reconAssignment, ground_assignment = draft.groundAssignment,
                        support_assignment = draft.supportAssignment, resource_limit = draft.resourceAllowance } }, basis), "primary-button");
                AddButton(drawerContent, "驳回建议", () => SendServerCommand("review_proposal", new SandboxServerReviewSimple { proposal_id = plan.proposal_id, decision = "reject" }, basis));
            }
            AddButton(drawerContent, "重新载入固定建议", () => SendServerCommand("reload_demo_plan"));
        }

        /// <summary>后端预览原样提交，地图标记来自下一次已保存视图。</summary>
        private void BuildServerEvents()
        {
            SetText("drawerTitle", "后端事件预览与登记");
            if (serverClient.View == null || role != SandboxRole.Host || projection.units.Length == 0) return;
            var type = new DropdownField("事件类型", DemoSandboxViewSource.EventTypes.ToList(), 0);
            var target = new DropdownField("影响单位", projection.units.Select(unit => unit.id).ToList(), 0);
            drawerContent.Add(type); drawerContent.Add(target);
            Label summary = AddText(drawerContent, "先预览再登记；当前事件仅登记，不施加正式效果。", "body-text");
            SandboxServerPreview preview = null; SandboxServerContext basis = null; SandboxServerConditionInput input = null;
            Button apply = null;
            Action invalidate = () => { preview = null; apply?.SetEnabled(false); summary.text = "输入已变化，请重新预览"; };
            type.RegisterValueChangedCallback(_ => invalidate()); target.RegisterValueChangedCallback(_ => invalidate());
            AddButton(drawerContent, "预览后端影响", () => {
                var requested = new SandboxServerConditionInput { condition_type = new[] { "equipment_fault", "communication_outage", "weather_change", "task_change" }[type.index], target_unit_id = target.value };
                serverClient.Preview(requested, (value, context) => {
                    if (requested.target_unit_id != target.value || requested.condition_type != new[] { "equipment_fault", "communication_outage", "weather_change", "task_change" }[type.index]) return;
                    preview = value; basis = context; input = requested; summary.text = value.summary; apply.SetEnabled(true);
                });
            });
            apply = AddButton(drawerContent, "确认登记", () => {
                if (preview == null) return;
                SendServerCommand("trigger_condition", new SandboxServerTrigger { condition = input, preview_token = preview.preview_token }, basis);
                preview = null; apply.SetEnabled(false);
            }, "primary-button"); apply.SetEnabled(false);
            foreach (SandboxServerCondition condition in serverClient.View.conditions) AddText(drawerContent,
                condition.target_unit_id + " · " + ServerConditionName(condition.condition_type) + " · " + (condition.effect_applied ? "效果已执行" : "仅登记"), "detail-line");
        }

        /// <summary>连接后不把本地快照误当作数据库存档。</summary>
        private void BuildServerStorageNotice()
        {
            SetText("drawerTitle", "后端存档与退出");
            AddText(drawerContent, "地图状态由后端自动保存。当前快照：\n" + (serverClient.View?.context.snapshot_id ?? "尚未读取"), "body-text");
            if (role != SandboxRole.Host)
            {
                AddText(drawerContent, "后端整局存档、恢复和退出由主持人操作。", "section-title");
                return;
            }
            var name = new TextField("存档名称") { value = "青岚河谷 · 第 " + (serverClient.View?.clock.simulation_step ?? 0) + " 步", maxLength = 80 };
            name.AddToClassList("field"); drawerContent.Add(name);
            AddButton(drawerContent, "保存当前后端快照", () => SendServerCommand("create_save", new SandboxServerSaveInput { display_name = name.value }), "primary-button");
            if (serverClient.View?.clock.phase != "preparing" && serverClient.View?.clock.phase != "finished")
                AddButton(drawerContent, "准备退出（先暂停后端）", () => SendServerCommand("begin_exit"));
            if (serverClient.View?.clock.phase == "paused")
            {
                AddButton(drawerContent, "保存并退出后端对局", () => SendServerCommand("complete_exit", new SandboxServerExitInput { display_name = name.value, choice = "save_and_exit" }), "primary-button");
                AddButton(drawerContent, "不新增存档，退出后端对局", () => SendServerCommand("complete_exit", new SandboxServerExitInput { display_name = name.value, choice = "without_save" }));
            }
            if (serverSavesLoading)
            {
                AddText(drawerContent, "正在读取后端存档列表…", "body-text");
                return;
            }
            AddText(drawerContent, "后端存档 " + serverSaves.Length + " 条", "section-title");
            if (serverSaves.Length == 0) AddText(drawerContent, "暂无可恢复的后端存档。", "empty-message");
            foreach (SandboxServerSave save in serverSaves)
            {
                var card = new VisualElement(); card.AddToClassList("detail-card"); drawerContent.Add(card);
                string label = string.IsNullOrEmpty(save.display_name) ? save.save_kind : save.display_name;
                AddText(card, label, "detail-title");
                AddText(card, save.saved_at + " · T+" + save.simulation_step + " · " + save.phase, "detail-line");
                if (save.can_restore) AddButton(card, "确认恢复后端分支", () => OpenServerRestoreModal(save));
            }
            AddButton(drawerContent, "刷新存档列表", LoadServerSaves).SetEnabled(serverClient.Healthy && !serverClient.Busy && !serverClient.HasPending);
            if (serverSavesOffset > 0) AddButton(drawerContent, "上一页", () => { serverSavesOffset = Math.Max(0, serverSavesOffset - 50); LoadServerSaves(); });
            if (serverSaves.Length == 50) AddButton(drawerContent, "下一页", () => { serverSavesOffset += 50; LoadServerSaves(); });
            if (!serverSavesLoaded && !serverClient.Busy && serverClient.Healthy && !serverClient.HasPending) LoadServerSaves();
        }

        /// <summary>串行读取存档；错误时解除加载状态并提供显式重试。</summary>
        private void LoadServerSaves()
        {
            serverSavesLoading = true; serverSavesLoaded = true;
            serverClient.LoadSaves(result => {
                serverSavesLoading = false;
                serverSaves = result?.items ?? Array.Empty<SandboxServerSave>();
                if (activePanel == "saves") ShowPanel("saves");
            }, serverSavesOffset);
        }

        /// <summary>恢复前明确提示会创建新分支，并使用后端快照替换当前权威头。</summary>
        private void OpenServerRestoreModal(SandboxServerSave save)
        {
            SetText("modalTitle", "恢复后端存档？");
            SetText("confirmModalButton", "确认恢复后端分支");
            SetText("modalMessage", (string.IsNullOrEmpty(save.display_name) ? save.save_kind : save.display_name) + "\nT+" + save.simulation_step + " · " + save.phase + "\n后端将创建新分支，旧分支保持只读，恢复后保持暂停。");
            modalAction = () => SendServerCommand("restore_save", new SandboxServerRestoreInput { save_id = save.save_id });
            root.Q("modalBackdrop").RemoveFromClassList("hidden");
            root.Q<Button>("cancelModalButton").Focus();
        }

        /// <summary>登记标识仅解释事件记录，不伪装单位已故障。</summary>
        private string ServerUnitLabel(string id)
        {
            int count = serverClient.View?.conditions.Count(condition => condition.target_unit_id == id) ?? 0;
            return id + (count == 0 ? string.Empty : " · 事件登记 " + count);
        }

        private static string ServerTaskStatus(string value) => value == "not_evaluated" ? "待规则裁决" : value == "completed" ? "已完成" : value;
        private static string ServerConditionName(string value) => value == "equipment_fault" ? "设备故障" : value == "communication_outage" ? "通信中断" : value == "weather_change" ? "天气变化" : "任务变化";
        private static string ServerSide(SandboxRole side) => side == SandboxRole.Red ? "red" : "blue";
        private static string ServerType(SandboxUnitType type) => type == SandboxUnitType.Recon ? "recon" : type == SandboxUnitType.Ground ? "ground" : "support";
        private static SandboxServerPosition ServerPosition(float x, float z) => new SandboxServerPosition {
            x = x.ToString("0.######", CultureInfo.InvariantCulture), y = z.ToString("0.######", CultureInfo.InvariantCulture) };
    }
}
