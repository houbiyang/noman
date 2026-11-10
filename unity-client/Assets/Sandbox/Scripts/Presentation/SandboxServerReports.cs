using System;
using System.Linq;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        private bool showLocalReportHistory, serverHistoryLoading;
        private string serverHistoryOwner;
        private SandboxServerArchiveList serverHistory;
        private SandboxMatchArchive serverHistoryDetail;
        private SandboxServerSettleStage stageDraft;
        private string stageDraftKey;

        private string ServerHistoryOwner => serverClient?.View == null ? null : serverClient.View.game.game_id + "/" + serverClient.View.role;

        private void RefreshServerHistory()
        {
            if (serverHistoryLoading || serverClient?.View == null) return;
            string owner = ServerHistoryOwner;
            serverHistoryLoading = true;
            serverClient.LoadArchives(list => {
                serverHistoryLoading = false;
                if (owner != ServerHistoryOwner) return;
                if (list != null) serverHistory = list;
                historyMessage = list == null ? "服务端档案读取失败或同步忙碌，请重试；未清空已有记录" : "已从数据库读取当前席位档案";
                if (reportSection == "history" && reportOverlay != null) RenderSettingsSection();
            }, historyPage * 20, historySearch, historyFilter == "全部记录" ? "all" : historyFilter == "已结束" ? "finished" : "active");
        }

        private void BuildServerHistoryReport()
        {
            ReportHeading("MISSION ARCHIVE / P-08", "每一次推演，都有迹可循。", "服务端历史 · 不可变席位观察档案。与浏览器预演和可恢复存档分开保存。");
            if (serverClient?.View == null) { AddText(settingsContent, "尚未取得后端视图，不能查询档案", "report-notice"); return; }
            if (serverHistoryOwner != ServerHistoryOwner)
            {
                serverHistoryOwner = ServerHistoryOwner; serverHistory = null; serverHistoryDetail = null; historyPage = 0;
                settingsContent.schedule.Execute(RefreshServerHistory);
            }
            var summary = SettingsGroup(settingsContent, "report-overview");
            SummaryCard(summary, "服务端档案", (serverHistory?.total ?? 0) + " 份", "当前对局 / 当前席位 · 结束自动归档");
            SummaryCard(summary, "访问席位", RoleLabel(role), "身份由后端 Bearer 会话核对");
            SummaryCard(summary, "存储方式", "数据库", "刷新或清理浏览器不会移除服务端档案");
            var controls = ReportCard(settingsContent, "归档与检索", "阶段确认、投降和结束归档都在同一事务内提交；不保存私有计划或写入上下文。");
            var name = new TextField("档案名称") { value = historyName, maxLength = 80 }; name.AddToClassList("settings-field");
            name.RegisterValueChangedCallback(evt => historyName = evt.newValue); controls.Add(name);
            var actions = SettingsGroup(controls, "report-toolbar");
            AddButton(actions, "归档当前对局", () => { SaveCurrentHistory(false, out historyMessage); RenderSettingsSection(); }, "report-primary").SetEnabled(!serverClient.Busy && !serverClient.HasPending && serverClient.Healthy);
            AddButton(actions, serverHistoryLoading ? "正在读取…" : "刷新记录", RefreshServerHistory, "report-button").SetEnabled(!serverHistoryLoading);
            var search = new TextField("搜索名称") { value = historySearch, maxLength = 80 }; search.AddToClassList("settings-field");
            search.RegisterValueChangedCallback(evt => historySearch = evt.newValue); controls.Add(search);
            var filters = SettingsGroup(controls, "report-toolbar");
            foreach (string filter in new[] { "全部记录", "进行中", "已结束" })
                AddButton(filters, filter, () => { historyFilter = filter; historyPage = 0; RefreshServerHistory(); }, "report-button").EnableInClassList("report-selected", historyFilter == filter);
            AddButton(filters, "查询", () => { historyPage = 0; RefreshServerHistory(); }, "report-button");
            if (historyMessage.Length > 0) AddText(controls, historyMessage, "report-notice");
            var table = ReportCard(settingsContent);
            var horizontal = new ScrollView(ScrollViewMode.Horizontal); table.Add(horizontal);
            var rows = SettingsGroup(horizontal, "archive-table");
            foreach (var archive in serverHistory?.items ?? Array.Empty<SandboxServerArchive>())
            {
                var row = SettingsGroup(rows, "archive-row");
                ArchiveCell(row, archive.title, "archive-title-cell"); ArchiveCell(row, "服务端观察档案", "archive-type-cell");
                ArchiveCell(row, DateTimeOffset.Parse(archive.created_at).ToLocalTime().ToString("yyyy-MM-dd\nHH:mm:ss"), "archive-date-cell");
                ArchiveCell(row, "T+" + archive.simulation_step, "archive-step-cell"); ArchiveCell(row, archive.phase == "finished" ? "已结束" : "进行中", "archive-state-cell");
                var action = SettingsGroup(row, "archive-action-cell");
                string id = archive.id;
                AddButton(action, "查看档案 →", () => LoadServerArchive(id), "report-link");
            }
            if (serverHistory?.items == null || serverHistory.items.Length == 0) AddText(rows, serverHistory == null ? "正在加载，或点击刷新记录。" : "没有匹配的服务端档案。", "archive-empty");
            var pages = SettingsGroup(table, "report-toolbar");
            AddText(pages, "共 " + (serverHistory?.total ?? 0) + " 条 · 第 " + (historyPage + 1) + " 页", "report-muted");
            AddButton(pages, "上一页", () => { historyPage--; RefreshServerHistory(); }, "report-button").SetEnabled(historyPage > 0 && !serverHistoryLoading);
            AddButton(pages, "下一页", () => { historyPage++; RefreshServerHistory(); }, "report-button").SetEnabled((historyPage + 1) * 20 < (serverHistory?.total ?? 0) && !serverHistoryLoading);
            if (serverHistoryDetail != null) BuildArchiveDetail(serverHistoryDetail);
        }

        private void LoadServerArchive(string id)
        {
            string owner = ServerHistoryOwner;
            serverClient.LoadArchive(id, response => {
                if (owner != ServerHistoryOwner || response?.item?.projection == null) return;
                var archive = response.item; var view = archive.projection;
                view.game = new SandboxServerGame { title = archive.title }; view.role = archive.role;
                view.decisions = new SandboxServerDecisions { waiting_sides = Array.Empty<string>() };
                view.timeline = view.timeline ?? Array.Empty<SandboxServerRecord>();
                serverHistoryDetail = new SandboxMatchArchive { id = archive.id, title = archive.title, savedAt = archive.created_at,
                    source = "server", matchId = serverClient.View.game.game_id, branchId = archive.branch_id, snapshotId = archive.snapshot_id,
                    role = SandboxServerProjection.Role(archive.role), phase = archive.phase == "finished" ? "已结束" : "进行中",
                    victory = view.victory, observation = SandboxHistoryObservation.FromProjection(SandboxServerProjection.Convert(view)) };
                selectedHistory = serverHistoryDetail;
                if (reportSection == "history" && reportOverlay != null) RenderSettingsSection();
            });
        }

        private void BuildServerAdjudication()
        {
            var view = serverClient?.View;
            if (view?.victory == null || view.capabilities == null) return;
            if (view.capabilities.can_surrender)
            {
                var surrender = ReportCard(settingsContent, "提前结束", "本项目每队只有一个席位，开始 5 分钟后可由本方投降；主持人不能代投。");
                AddButton(surrender, "本方投降…", () => ConfirmReportCommand("确认本方投降？", "该操作将结束对局、本方失败，结果和档案提交到数据库。", () => SendServerCommand("surrender")), "report-button");
            }
            if (!view.capabilities.can_settle_goal_stage) return;
            int stage = view.clock.simulation_step / 15;
            string key = view.game.game_id + "/" + view.context.branch_id + "/" + stage;
            if (stageDraftKey != key)
            {
                stageDraftKey = key;
                stageDraft = new SandboxServerSettleStage { stage = stage, evidence = "", goals = Enumerable.Range(0, 6).Select(i => new SandboxServerGoalDecision { goal_id = (i < 3 ? "capture-" : "destroy-") + (i % 3 + 1), evidence = "" }).ToArray() };
            }
            var card = ReportCard(settingsContent, "阶段 " + stage + " / 主持人目标确认", "逐项确认三个占领目标和三个摧毁目标。不是单位击杀计数；双方完成情况独立登记。依据为公开裁定文本，不可填写私有计划或凭据。提交后不可覆盖。");
            var summary = new TextField("阶段裁定依据") { value = stageDraft.evidence, maxLength = 500 }; summary.AddToClassList("settings-field");
            summary.RegisterValueChangedCallback(evt => stageDraft.evidence = evt.newValue); card.Add(summary);
            foreach (var goal in stageDraft.goals)
            {
                var row = SettingsGroup(card, "report-toolbar"); AddText(row, goal.goal_id, "report-card-title");
                var red = new Toggle("红方完成") { value = goal.red_completed }; red.RegisterValueChangedCallback(evt => goal.red_completed = evt.newValue); row.Add(red);
                var blue = new Toggle("蓝方完成") { value = goal.blue_completed }; blue.RegisterValueChangedCallback(evt => goal.blue_completed = evt.newValue); row.Add(blue);
                var reason = new TextField("目标依据") { value = goal.evidence, maxLength = 300 }; reason.AddToClassList("settings-field");
                reason.RegisterValueChangedCallback(evt => goal.evidence = evt.newValue); card.Add(reason);
            }
            AddButton(card, "确认本阶段裁定…", () => {
                if (string.IsNullOrWhiteSpace(stageDraft.evidence) || stageDraft.goals.Any(g => string.IsNullOrWhiteSpace(g.evidence))) { historyMessage = "每个目标与阶段都必须填写裁定依据"; RenderSettingsSection(); return; }
                var input = stageDraft; var basis = view.context;
                ConfirmReportCommand("确认阶段裁定？", "将公开保存六个目标依据并由服务器计算分数，提交后不可覆盖。第三阶段裁定后自动结束并归档。", () => serverClient.Command("settle_goal_stage", input, basis));
            }, "report-primary").SetEnabled(!serverClient.Busy && !serverClient.HasPending);
        }

        private void ConfirmReportCommand(string title, string message, Action submit)
        {
            SetText("modalTitle", title); SetText("modalMessage", message); SetText("confirmModalButton", "确认提交");
            modalAction = submit;
            var backdrop = root.Q("modalBackdrop"); backdrop.RemoveFromClassList("hidden"); backdrop.BringToFront();
            root.Q<Button>("cancelModalButton").Focus();
        }
    }
}
