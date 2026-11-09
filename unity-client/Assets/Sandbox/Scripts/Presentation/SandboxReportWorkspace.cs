using System;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>Prototype P-08/P-09 layout, backed only by actual seat archives and explicit rule previews.</summary>
    public sealed partial class SandboxHudController
    {
        private VisualElement reportOverlay;
        private ScrollView reportContent;
        private const float ReportMouseWheelScrollSize = 10f;
        private string reportSection;
        private string historyFilter = "全部记录", historySearch = "";

        private void OpenReport(string section)
        {
            ClosePanel(); CloseArmy(); CancelArmyCommand(); PausePlayback();
            if (reportOverlay == null)
            {
                // Suspend, do not discard or save the Settings draft while browsing a report.
                if (settingsOverlay != null) settingsOverlay.style.display = DisplayStyle.None;
                reportOverlay = new VisualElement { name = "reportWorkspace" };
                reportOverlay.AddToClassList("report-workspace"); reportOverlay.AddToClassList("ui-blocker"); root.Add(reportOverlay);
                reportOverlay.RegisterCallback<GeometryChangedEvent>(evt => reportOverlay?.EnableInClassList("report-narrow", evt.newRect.width < 950));
                var header = SettingsGroup(reportOverlay, "report-topbar");
                AddText(header, "穹图  /  QIONGTU", "report-brand");
                AddButton(header, "历史档案", () => NavigateReport("history"), "report-nav").name = "reportHistoryTab";
                AddButton(header, "结果报告", () => NavigateReport("results"), "report-nav").name = "reportResultsTab";
                SettingsGroup(header, "report-spacer");
                AddButton(header, "返回沙盘  ×", () => CloseReport(true), "report-button").name = "closeReportMap";
                AddText(reportOverlay, "READ ONLY  /  " + RoleLabel(role) + "  ·  本机档案与规则预演，不推进对局", "report-context");
                reportContent = new ScrollView(ScrollViewMode.Vertical) { name = "reportContent" };
                reportContent.AddToClassList("report-content"); reportContent.contentContainer.AddToClassList("report-body");
                // 历史档案和结果报告使用较小的滚轮步长，避免大界面一次跳过关键节点。
                reportContent.mouseWheelScrollSize = ReportMouseWheelScrollSize;
                reportContent.horizontalScrollerVisibility = ScrollerVisibility.Hidden; reportOverlay.Add(reportContent);
                var footer = SettingsGroup(reportOverlay, "report-footer");
                AddText(footer, "只读观察 · 非录像回放  /  War Goals 1 · 2 · 3", "report-muted");
                AddButton(footer, "返回设置", () => CloseReport(), "report-button").name = "returnReportSettings";
            }
            NavigateReport(section);
        }

        private void NavigateReport(string section)
        {
            if (reportOverlay == null) { OpenReport(section); return; }
            reportSection = section; reportContent.scrollOffset = Vector2.zero;
            RenderReport();
        }

        private void CloseReport(bool toMap = false)
        {
            reportOverlay?.RemoveFromHierarchy(); reportOverlay = null; reportContent = null;
            if (settingsOverlay != null) settingsOverlay.style.display = DisplayStyle.Flex;
            if (toMap) { if (settingsOverlay != null) RequestCloseSettings(); }
            else if (settingsOverlay == null) OpenSettings();
            else root.Q<Button>("settingsNav-" + settingsSection)?.Focus();
        }

        private void RenderReport()
        {
            var previous = settingsContent;
            settingsContent = reportContent;
            reportContent.Clear();
            reportOverlay.Q<Button>("reportHistoryTab").EnableInClassList("report-nav-active", reportSection == "history");
            reportOverlay.Q<Button>("reportResultsTab").EnableInClassList("report-nav-active", reportSection == "results");
            try { if (reportSection == "history") BuildHistoryReport(); else BuildResultReport(); }
            finally { settingsContent = previous; }
        }

        private VisualElement ReportCard(VisualElement parent, string title = null, string hint = null)
        {
            var card = SettingsGroup(parent, "report-card");
            if (title != null) AddText(card, title, "report-card-title");
            if (hint != null) AddText(card, hint, "report-muted");
            return card;
        }

        private void ReportHeading(string eyebrow, string title, string description)
        {
            var heading = SettingsGroup(settingsContent, "report-heading");
            AddText(heading, eyebrow, "report-eyebrow"); AddText(heading, title, "report-title");
            AddText(heading, description, "report-description");
        }

        private static bool ArchiveEnded(SandboxMatchArchive entry) => entry.source == "preview"
            ? SandboxBattleRules.Evaluate(entry.victory).final || entry.victory?.elapsedMinutes >= SandboxBattleRules.StageMinutes(entry.victory) * 3
            : !string.IsNullOrEmpty(entry.automaticKey) || (entry.phase ?? "").Contains("结束");

        private void BuildHistoryReport()
        {
            if (serverMode)
            {
                var switcher = SettingsGroup(settingsContent, "report-toolbar");
                AddButton(switcher, "服务端历史", () => { showLocalReportHistory = false; RenderSettingsSection(); }, "report-button");
                AddButton(switcher, "本机 / 规则预演档案", () => { showLocalReportHistory = true; RenderSettingsSection(); }, "report-button");
                if (!showLocalReportHistory) { BuildServerHistoryReport(); return; }
            }
            ReportHeading("MISSION ARCHIVE / P-08", "每一次推演，都有迹可循。", "历史记录 · 查阅席位档案与关键节点，保留每一次观察。历史不是录像或可恢复的仿真快照。");
            var all = History.ReadAll(out string warning);
            var visible = all.Where(entry => entry.source == "preview" || entry.role == role).ToArray();
            var overview = SettingsGroup(settingsContent, "report-overview");
            SummaryCard(overview, "档案总数", visible.Length + " 份", "本机保存 · 最新记录在前");
            SummaryCard(overview, "已结束 / 进行中", visible.Count(ArchiveEnded) + " / " + visible.Count(entry => !ArchiveEnded(entry)), "规则预演单独标记 · 不冒充真实战果");
            SummaryCard(overview, "按席位查看", RoleLabel(role), "只归档可见内容；本地存储不是认证边界");
            var controls = ReportCard(settingsContent, "归档当前对局", "保存当前可见单位与事件，不保存令牌或双方私有建议。清理站点数据会移除浏览器档案。");
            var actions = SettingsGroup(controls, "report-toolbar");
            var name = new TextField { value = historyName, maxLength = 80, name = "historyName", label = "档案名称" };
            name.AddToClassList("settings-field"); name.AddToClassList("report-name"); name.RegisterValueChangedCallback(evt => historyName = evt.newValue); actions.Add(name);
            AddButton(actions, "归档当前对局", () => { SaveCurrentHistory(false, out historyMessage); RenderSettingsSection(); }, "report-primary").name = "archiveCurrentMatch";
            actions.Q<Button>("archiveCurrentMatch").SetEnabled(!serverMode);
            if (serverMode) AddText(controls, "当前为本机/规则预演档案。真实对局请切回服务端历史归档。", "report-notice");
            AddButton(actions, "刷新记录", () => RenderSettingsSection(), "report-button").name = "refreshHistory";
            AddButton(actions, "可恢复存档…", () => { if (reportOverlay != null) CloseReport(); if (settingsOverlay != null) RequestCloseSettings(() => ShowPanel("saves")); else ShowPanel("saves"); }, "report-button");
            if (historyMessage.Length > 0) AddText(controls, historyMessage, "report-notice");
            if (warning.Length > 0) AddText(controls, warning, "report-notice");
            var table = ReportCard(settingsContent);
            var toolbar = SettingsGroup(table, "report-toolbar");
            VisualElement rows = null;
            foreach (string filter in new[] { "全部记录", "进行中", "已结束" })
            {
                var button = AddButton(toolbar, filter, () => { historyFilter = filter; historyPage = 0; RenderSettingsSection(); }, "report-button");
                button.EnableInClassList("report-selected", historyFilter == filter); button.name = "historyFilter-" + filter;
            }
            var search = new TextField { value = historySearch, label = "搜索对局名称", name = "historySearch" };
            search.AddToClassList("settings-field"); search.AddToClassList("report-search"); toolbar.Add(search);
            var horizontal = new ScrollView(ScrollViewMode.Horizontal); horizontal.AddToClassList("archive-horizontal"); table.Add(horizontal);
            rows = SettingsGroup(horizontal, "archive-table");
            search.RegisterValueChangedCallback(evt => { historySearch = evt.newValue; historyPage = 0; PopulateArchives(rows, visible); });
            PopulateArchives(rows, visible);
            if (selectedHistory != null && visible.Any(entry => entry.id == selectedHistory.id)) BuildArchiveDetail(selectedHistory);
            else selectedHistory = null;
        }

        private void SummaryCard(VisualElement parent, string label, string value, string hint)
        {
            var card = ReportCard(parent); card.AddToClassList("report-summary");
            AddText(card, label, "report-muted"); AddText(card, value, "report-stat"); AddText(card, hint, "report-muted");
        }

        private void ArchiveCell(VisualElement parent, string text, string style)
        { AddText(parent, text, "archive-cell").AddToClassList(style); }

        private void PopulateArchives(VisualElement rows, SandboxMatchArchive[] visible)
        {
            rows.Clear();
            var filtered = visible.Where(entry => (historyFilter == "全部记录" || ArchiveEnded(entry) == (historyFilter == "已结束"))
                && (entry.title ?? "").IndexOf(historySearch.Trim(), StringComparison.OrdinalIgnoreCase) >= 0).ToArray();
            var heading = SettingsGroup(rows, "archive-row"); heading.AddToClassList("archive-head");
            string[] titles = { "对局 / 编号", "任务模板", "保存时间", "仿真步", "状态", "操作" };
            string[] classes = { "archive-title-cell", "archive-type-cell", "archive-date-cell", "archive-step-cell", "archive-state-cell", "archive-action-cell" };
            for (int i = 0; i < titles.Length; i++) ArchiveCell(heading, titles[i], classes[i]);
            historyPage = Math.Max(0, Math.Min(historyPage, Math.Max(0, (filtered.Length - 1) / 20)));
            foreach (var entry in filtered.Skip(historyPage * 20).Take(20))
            {
                var captured = entry; var row = SettingsGroup(rows, "archive-row");
                var title = SettingsGroup(row, "archive-title-cell");
                AddText(title, entry.title, "archive-name"); AddText(title, entry.id.Substring(0, 8) + " · " + ArchiveSource(entry), "report-muted");
                ArchiveCell(row, entry.source == "preview" ? "战争目标预演" : entry.observation?.taskType ?? "观察档案", classes[1]);
                ArchiveCell(row, DateTimeOffset.Parse(entry.savedAt).ToLocalTime().ToString("yyyy-MM-dd\nHH:mm:ss"), classes[2]);
                ArchiveCell(row, entry.source == "preview" ? "—" : entry.observation?.step + " / 60", classes[3]);
                ArchiveCell(row, entry.phase, classes[4]);
                var action = SettingsGroup(row, classes[5]);
                AddButton(action, "查看档案  →", () => { selectedHistory = captured; RenderSettingsSection();
                    var content = reportContent ?? settingsContent; content.schedule.Execute(() => { var detail = content.Q("archiveDetail"); if (detail != null) content.ScrollTo(detail); });
                }, "report-link").name = "history-" + entry.id;
            }
            if (filtered.Length == 0) AddText(rows, visible.Length == 0 ? "还没有历史记录。归档当前对局，或前往演示结果保存一次规则预演。" : "没有匹配记录，请调整名称或筛选条件。", "archive-empty");
            var foot = SettingsGroup(rows, "report-toolbar");
            AddText(foot, "共 " + filtered.Length + " 条记录 · 第 " + (historyPage + 1) + " 页 · 本机只读档案", "report-muted");
            if (filtered.Length > 20)
            {
                AddButton(foot, "上一页", () => { historyPage--; PopulateArchives(rows, visible); }, "report-button").SetEnabled(historyPage > 0);
                AddButton(foot, "下一页", () => { historyPage++; PopulateArchives(rows, visible); }, "report-button").SetEnabled((historyPage + 1) * 20 < filtered.Length);
            }
        }

        private void BuildArchiveDetail(SandboxMatchArchive entry)
        {
            var detail = ReportCard(settingsContent, "档案详情 / " + entry.title, ArchiveSource(entry)); detail.name = "archiveDetail";
            AddText(detail, DateTimeOffset.Parse(entry.savedAt).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss") + "  /  " + RoleLabel(entry.role) + "  /  " + entry.phase, "report-description");
            AddText(detail, "对局 " + (string.IsNullOrEmpty(entry.matchId) ? "规则算例" : entry.matchId) + "  ·  分支 / 快照 " + entry.branchId + " / " + entry.snapshotId, "report-muted");
            if (entry.observation != null)
            {
                foreach (var unit in entry.observation.units)
                    AddText(detail, unit.id + " · " + unit.name + " · " + unit.status + " · (" + unit.x.ToString("0.0") + ", " + unit.z.ToString("0.0") + ")", "report-muted");
                AddText(detail, "关键时间线 / 保存时可见事件（非战果推断）", "report-card-title");
                foreach (var record in entry.observation.records.OrderBy(record => record.step))
                    AddText(detail, "第 " + record.step + " 步 · " + record.title + "\n" + record.detail, "report-timeline");
            }
            var actions = SettingsGroup(detail, "report-toolbar");
            AddButton(actions, "查看计算过程与关键节点", () => {
                showingPreview = entry.source == "preview"; resultEvidence = entry.victory; resultArchiveTitle = entry.title;
                NavigateReport("results");
            }, "report-primary").name = "viewArchivedResult";
            AddButton(actions, "收起详情", () => { selectedHistory = null; serverHistoryDetail = null; RenderSettingsSection(); }, "report-button");
        }

        private void BuildResultReport()
        {
            ReportHeading("AFTER ACTION REPORT / P-09", "行动落幕，决策留痕。", "演示结果 · 青岚三点驻守加权计分。原始文档与项目适配规则分别标注。");
            var sources = SettingsGroup(settingsContent, "report-toolbar");
            AddButton(sources, "规则算例预演", () => { showingPreview = true; resultArchiveTitle = null; resultEvidence = SandboxBattleRules.ProjectPreview(); RenderSettingsSection(); }, "report-button").name = "previewRuleResult";
            AddButton(sources, "当前对局", () => { showingPreview = false; resultArchiveTitle = null; resultEvidence = serverMode ? serverClient.View?.victory : null; RenderSettingsSection(); }, "report-button").name = "currentMatchResult";
            AddButton(sources, "历史档案  →", () => NavigateReport("history"), "report-button");
            if (showingPreview && resultEvidence == null) resultEvidence = SandboxBattleRules.ProjectPreview();
            if (showingPreview)
            {
                string[] names = { "项目预演 · 7:9", "平局 · 9:9", "40 分钟 · 未结束" };
                for (int i = 0; i < 3; i++) { int scenario = i; AddButton(sources, names[i], () => SelectResultScenario(scenario), "report-button").name = "resultScenario" + i; }
            }
            if (!string.IsNullOrEmpty(resultArchiveTitle)) AddText(settingsContent, "正在查看档案：" + resultArchiveTitle, "report-notice");
            if (!showingPreview && string.IsNullOrEmpty(resultArchiveTitle)) resultEvidence = serverMode ? serverClient.View?.victory : null;
            bool project = SandboxBattleRules.IsProject(resultEvidence);
            int stageMinutes = SandboxBattleRules.StageMinutes(resultEvidence), goalLimit = SandboxBattleRules.GoalLimit(resultEvidence);
            var result = SandboxBattleRules.Evaluate(resultEvidence);
            bool usableEvidence = result.calculations.Length == 3;
            var hero = ReportCard(settingsContent); hero.AddToClassList("report-result-hero");
            var seal = SettingsGroup(hero, "report-seal"); SettingsGroup(seal, "report-seal-icon");
            var copy = SettingsGroup(hero, "report-victory");
            AddText(copy, showingPreview ? "RULE PREVIEW / 非真实战果" : !string.IsNullOrEmpty(resultArchiveTitle) ? "ARCHIVED RESULT / 只读历史证据" : resultEvidence == null ? "MATCH RESULT / 缺少结算证据" : project ? "SERVER RESULT / 阶段位置快照 · 自动结算" : "SERVER RESULT / 主持人确认 · 服务端结算", "report-eyebrow");
            AddText(copy, result.verdict, "report-verdict").name = "resultVerdict";
            AddText(copy, result.reason, "report-muted");
            string redScore100 = result.redScore100.ToString("F2", System.Globalization.CultureInfo.InvariantCulture) + "分";
            string blueScore100 = result.blueScore100.ToString("F2", System.Globalization.CultureInfo.InvariantCulture) + "分";
            string scoreKind = result.final ? "最终得分" : "当前得分";
            var red = ReportCard(hero, "红方" + scoreKind); red.AddToClassList("report-score-red");
            AddText(red, !usableEvidence ? "—" : redScore100, "report-score-number").name = "redScore100"; AddText(red, usableEvidence ? "满分100分 · 原始积分 " + result.redPoints + " / " + result.scoreMaximum : "缺少有效结算证据", "report-muted");
            AddText(hero, "VS", "report-versus");
            var blue = ReportCard(hero, "蓝方" + scoreKind); blue.AddToClassList("report-score-blue");
            AddText(blue, !usableEvidence ? "—" : blueScore100, "report-score-number").name = "blueScore100"; AddText(blue, usableEvidence ? "满分100分 · 原始积分 " + result.bluePoints + " / " + result.scoreMaximum : "缺少有效结算证据", "report-muted");
            AddText(settingsContent, !usableEvidence ? "— : —" : redScore100 + " : " + blueScore100, "report-score-summary").name = "resultScore";
            if (!showingPreview && resultEvidence == null) AddText(settingsContent, "当前对局没有可用结算证据；未启用计分模式的旧演示截止不是获胜依据。新建青岚适配模式采用 20/40/60 分钟三点驻守自动结算。", "report-notice");
            if (!showingPreview && string.IsNullOrEmpty(resultArchiveTitle) && serverMode) BuildServerAdjudication();
            if (project && !showingPreview && resultEvidence.liveObjectives != null)
            {
                var current = ReportCard(settingsContent, "当前点位态势 · 非已结算得分", "400米范围内的有效地面单位；仅20/40/60分钟的已提交位置快照计分。点位人数和归属是公开计分信息，不展示敌方坐标或单位编号。");
                foreach (var point in resultEvidence.liveObjectives)
                    AddText(current, point.name + " · " + SandboxBattleRules.OwnerLabel(point.owner) + " · 红 " + point.redUnits + " / 蓝 " + point.blueUnits, "report-muted");
            }
            var grid = SettingsGroup(settingsContent, "report-result-grid");
            var breakdown = ReportCard(grid, "阶段表现", project ? "三个地图点位 · 阶段权重 1 / 2 / 3；不计击毁、资源或未实现任务。" : "旧战争目标规则 · 阶段权重 1 / 2 / 3"); breakdown.AddToClassList("report-breakdown");
            if (usableEvidence)
                for (int i = 0; i < 3; i++)
                {
                    var stage = resultEvidence.stages[i]; var score = result.stageScores[i]; bool confirmed = score.settled;
                    AddText(breakdown, "阶段 " + score.stage + "  /  " + score.minute + " 分钟  ·  权重 " + score.weight, "report-card-title");
                    ScoreBar(breakdown, score.redPoints, "红方", "report-red-bar", confirmed, goalLimit * 3);
                    ScoreBar(breakdown, score.bluePoints, "蓝方", "report-blue-bar", confirmed, goalLimit * 3);
                    AddText(breakdown, confirmed ? "本阶段 " + score.redPoints + " : " + score.bluePoints + "  /  累计 " + score.redTotal + " : " + score.blueTotal : "尚未结算 · 不预支阶段得分", "report-muted");
                    if (project && confirmed && stage.objectives != null)
                        foreach (var point in stage.objectives)
                            AddText(breakdown, point.name + " · " + SandboxBattleRules.OwnerLabel(point.owner) + " · 红 " + point.redUnits + " / 蓝 " + point.blueUnits + " · " + (point.owner == "red" || point.owner == "blue" ? (i + 1) + " 分" : "0 分"), "report-muted");
                }
            else AddText(breakdown, "缺少权威目标证据，暂无可比较的阶段分。", "report-muted");
            var nodes = ReportCard(grid, "关键节点", "阶段结算 / 累计比分变化"); nodes.AddToClassList("report-decisions");
            foreach (string milestone in result.milestones) AddText(nodes, milestone, "report-timeline");
            if (result.milestones.Length == 0) AddText(nodes, "没有已确认的阶段结算节点；原始事件可在历史档案查看。", "report-muted");
            var calc = ReportCard(settingsContent, "计算过程", project ? "每阶段 20 分钟，三个点位400米内单方有效地面单位驻守才得分。每点 × 阶段权重；争夺/无人均为0。60分钟前不判最终胜负。" : "每阶段15分钟、六个目标，主持人确认后结算。");
            if (usableEvidence) AddText(calc, scoreKind + " = 本方累计分 ÷ 理论满分 × 100（满分100分，非双方分数占比）\n红方 " + result.redPoints + " ÷ " + result.scoreMaximum + " × 100 = " + redScore100
                + "\n蓝方 " + result.bluePoints + " ÷ " + result.scoreMaximum + " × 100 = " + blueScore100
                + "\n保留两位小数；未结束时只是当前得分。投降胜负沿用原规则，不改写积分。", "report-calculation").name = "score100Calculation";
            foreach (string line in result.calculations) AddText(calc, line, "report-calculation");
            if (result.calculations.Length == 0) AddText(calc, "暂无可核验的目标结算，不能生成比分。", "report-muted");
            if (showingPreview)
                AddButton(calc, "保存这次预演到历史", () => {
                    var entry = NewArchive(); entry.title = (project ? "青岚驻守预演 / " : "战争目标预演 / ") + result.verdict; entry.source = "preview";
                    entry.phase = result.final ? "预演已结算" : result.verdict; entry.victory = resultEvidence;
                    History.TrySave(entry, out historyMessage); Notify(historyMessage); RenderSettingsSection();
                }, "report-primary").name = "archiveResultPreview";
            if (historyMessage.Length > 0) AddText(calc, historyMessage, "report-notice");
            var reference = ReportCard(settingsContent, "规则依据与适配边界", project ? SandboxBattleRules.ProjectReference : SandboxBattleRules.Reference);
            if (project) AddText(reference, "规则版本：" + SandboxBattleRules.ProjectVersion + "\n继承三阶段与1/2/3权重；改为60分钟、三个地图点位、半径400米。仅ground/support且测试耐久>0计入，侦察无人机不计入；两方同时驻守不得分。各阶段独立取样，不继承无人驻守点的归属。理论满分18；同分记平局；不启用骤死、征服持续产分、任务分或击毁分。这些是项目演示参数，不是断箭官方数值。", "report-muted");
            else AddText(reference, "规则版本：" + SandboxBattleRules.Version + "\n36 分是理论满分，不是提前获胜线；摧毁目标不等于击杀任意单位。平局、征服阈值、骤死时长不擅自补全；本项目单席位队伍须开始至少 5 分钟后由本方确认投降；不支持多人投票。", "report-muted");
        }

        private void ScoreBar(VisualElement parent, int points, string side, string style, bool confirmed, int maximum = 18)
        {
            var row = SettingsGroup(parent, "report-bar-row"); AddText(row, side, "report-bar-label");
            var track = SettingsGroup(row, "report-bar-track"); var fill = SettingsGroup(track, style);
            fill.style.width = Length.Percent(points / (float)maximum * 100); fill.style.height = Length.Percent(100);
            AddText(row, confirmed ? points + " 分" : "未结算", "report-bar-value");
        }
    }
}
