using System;
using System.Linq;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>设置内的历史与结果视图，数据来自席位档案和显式规则预演。</summary>
    public sealed partial class SandboxHudController
    {
        private const float ReportMouseWheelScrollSize = 10f;
        private string historyFilter = "全部记录", historySearch = "", resultMessage = "";
        private int resultScenario;
        private bool archiveNameExpanded;
        private static readonly string[] ResultChoices = { "当前对局", "规则预演 · 7:9", "规则预演 · 平局 9:9", "规则预演 · 40分钟未结束" };

        private DropdownField ReportSelect(VisualElement parent, string label, string name, string[] choices, string value, Action<string> changed)
        {
            var field = new DropdownField(label, new List<string>(choices), Array.IndexOf(choices, value)) { name = name };
            field.AddToClassList("settings-field"); field.AddToClassList("report-select");
            field.RegisterValueChangedCallback(evt => changed(evt.newValue)); parent.Add(field); return field;
        }

        private void BuildArchiveName(VisualElement parent, string fieldName)
        {
            var options = new Foldout { text = "自定义档案名称（可选）", value = archiveNameExpanded, name = "archiveNameOptions" };
            options.AddToClassList("settings-details");
            options.RegisterValueChangedCallback(evt => archiveNameExpanded = evt.newValue);
            var name = new TextField { value = historyName, maxLength = 80, name = fieldName, label = "留空则自动命名" };
            name.AddToClassList("settings-field"); name.RegisterValueChangedCallback(evt => historyName = evt.newValue);
            options.Add(name); parent.Add(options);
        }

        /// <summary>历史和结果共用设置面板，不另建全屏工作区，不覆盖未保存的草稿。</summary>
        private void OpenReport(string section)
        {
            if (settingsOverlay == null) OpenSettings();
            settingsSection = section == "history" ? "history" : "results";
            settingsContent.scrollOffset = Vector2.zero;
            RenderSettingsSection();
            root.Q<Button>("settingsNav-" + settingsSection)?.Focus();
        }

        private void NavigateReport(string section) => OpenReport(section);

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
                ReportSelect(switcher, "档案来源", "historySource", new[] { "服务端历史", "本机 / 规则预演档案" },
                    showLocalReportHistory ? "本机 / 规则预演档案" : "服务端历史", value => {
                        showLocalReportHistory = value != "服务端历史"; historyPage = 0;
                        selectedHistory = null; serverHistoryDetail = null;
                        if (!showLocalReportHistory) serverHistoryOwner = null;
                        RenderSettingsSection();
                    });
                if (!showLocalReportHistory) { BuildServerHistoryReport(); return; }
            }
            ReportHeading("穹图 / 席位档案", "历史记录", "历史记录 · 查阅席位档案与关键节点，保留每一次观察。历史不是录像或可恢复的仿真快照。");
            var all = History.ReadAll(out string warning);
            var visible = all.Where(entry => entry.source == "preview" || entry.role == role).ToArray();
            var overview = SettingsGroup(settingsContent, "report-overview");
            SummaryCard(overview, "档案总数", visible.Length + " 份", "本机保存 · 最新记录在前");
            SummaryCard(overview, "已结束 / 进行中", visible.Count(ArchiveEnded) + " / " + visible.Count(entry => !ArchiveEnded(entry)), "规则预演单独标记 · 不冒充真实战果");
            if (historyMessage.Length > 0) AddText(settingsContent, historyMessage, "report-notice");
            if (warning.Length > 0) AddText(settingsContent, warning, "report-notice");
            var table = ReportCard(settingsContent);
            var toolbar = SettingsGroup(table, "report-toolbar");
            VisualElement rows = null;
            ReportSelect(toolbar, "记录状态", "historyStatus", new[] { "全部记录", "进行中", "已结束" }, historyFilter,
                value => { historyFilter = value; historyPage = 0; selectedHistory = null; RenderSettingsSection(); });
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
                ArchiveCell(row, entry.source == "preview" ? (SandboxBattleRules.IsProject(entry.victory) ? "青岚驻守预演" : "战争目标预演") : entry.observation?.taskType ?? "观察档案", classes[1]);
                ArchiveCell(row, DateTimeOffset.Parse(entry.savedAt).ToLocalTime().ToString("yyyy-MM-dd\nHH:mm:ss"), classes[2]);
                ArchiveCell(row, entry.source == "preview" ? "—" : entry.observation?.step + " / 60", classes[3]);
                ArchiveCell(row, entry.phase, classes[4]);
                var action = SettingsGroup(row, classes[5]);
                AddButton(action, selectedHistory?.id == entry.id ? "收起档案" : "查看档案 →", () => { selectedHistory = selectedHistory?.id == captured.id ? null : captured; RenderSettingsSection();
                    var content = settingsContent; content.schedule.Execute(() => { var detail = content.Q("archiveDetail"); if (detail != null) content.ScrollTo(detail); });
                }, "report-link").name = "history-" + entry.id;
            }
            if (filtered.Length == 0) AddText(rows, visible.Length == 0 ? "还没有历史记录。归档当前对局，或前往演示结果保存一次规则预演。" : "没有匹配记录，请调整名称或筛选条件。", "archive-empty");
            var foot = SettingsGroup(rows, "report-toolbar");
            AddText(foot, "共 " + filtered.Length + " 条记录 · 第 " + (historyPage + 1) + " 页 · 本机只读档案", "report-muted");
            if (filtered.Length > 20)
            {
                var previous = AddButton(foot, "上一页", () => { historyPage--; PopulateArchives(rows, visible); }, "report-button");
                previous.name = "historyPrevious"; previous.SetEnabled(historyPage > 0);
                var next = AddButton(foot, "下一页", () => { historyPage++; PopulateArchives(rows, visible); }, "report-button");
                next.name = "historyNext"; next.SetEnabled((historyPage + 1) * 20 < filtered.Length);
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
        }

        private void BuildResultReport()
        {
            ReportHeading("穹图 / 结果审计", "演示结果", "演示结果 · 青岚三点驻守加权计分。原始文档与项目适配规则分别标注。");
            var sources = SettingsGroup(settingsContent, "report-toolbar");
            var choices = ResultChoices.ToList();
            if (!string.IsNullOrEmpty(resultArchiveTitle)) choices.Add("历史档案（只读）");
            ReportSelect(sources, "查看内容", "resultSource", choices.ToArray(),
                !string.IsNullOrEmpty(resultArchiveTitle) ? "历史档案（只读）" : showingPreview ? ResultChoices[resultScenario + 1] : ResultChoices[0], value => {
                    int index = Array.IndexOf(ResultChoices, value);
                    if (index < 0) return;
                    resultMessage = "";
                    if (index > 0) SelectResultScenario(index - 1);
                    else { showingPreview = false; resultArchiveTitle = null; resultEvidence = serverMode ? serverClient?.View?.victory : null; RenderSettingsSection(); }
                });
            if (showingPreview && resultEvidence == null) resultEvidence = SandboxBattleRules.ProjectPreview();
            if (showingPreview && string.IsNullOrEmpty(resultArchiveTitle))
                AddButton(sources, "保存预演到历史", () => {
                    var entry = NewArchive(); entry.title = "规则预演 / " + SandboxBattleRules.Evaluate(resultEvidence).verdict; entry.source = "preview";
                    entry.phase = SandboxBattleRules.Evaluate(resultEvidence).final ? "预演已结算" : "预演未结束"; entry.victory = resultEvidence;
                    History.TrySave(entry, out resultMessage); Notify(resultMessage); RenderSettingsSection();
                }, "report-primary").name = "archiveResultPreview";
            if (resultMessage.Length > 0) AddText(settingsContent, resultMessage, "report-notice");
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
