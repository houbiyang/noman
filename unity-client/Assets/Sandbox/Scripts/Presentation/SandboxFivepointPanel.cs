using System;
using System.Linq;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        private VisualElement fivepointPanel;
        private Label fivepointSummary;
        private Button fivepointPlanning, fivepointAdvance, fivepointRetry;
        private Button fivepointAdopt, fivepointReject;
        private Button fivepointAutomatic;
        private Button fivepointEnableManual, fivepointEnableAuto;

        /// <summary>展示统一播放帧中的本方任务事实，主持人不读取双方私有任务。</summary>
        private void BuildFivepointTasks()
        {
            SetText("drawerTitle", "五点任务执行状态");
            SandboxFivepointView frame = serverClient.View.fivepoint;
            AddText(drawerContent, "播放时刻 " + frame.second + " 秒；状态由完整规则步结算。", "body-text");
            if (serverClient.View.role == "host") { AddText(drawerContent, "私有任务仅在本方席位展示。", "body-text"); return; }
            foreach (SandboxFivepointTask task in frame.own_tasks ?? Array.Empty<SandboxFivepointTask>())
                AddText(drawerContent, task.task_id + " · " + task.unit_id + "\n" + task.status
                    + " · 已执行 " + task.active_steps + " 步" + (string.IsNullOrEmpty(task.reason) ? "" : "\n" + task.reason), "body-text");
        }

        /// <summary>展示当前共同边界的本方候选全文；采用绑定打开时的权威依据。</summary>
        private void BuildFivepointPlan()
        {
            SetText("drawerTitle", "五点候选任务与人工确认");
            SandboxServerView authority = serverClient.AuthorityView;
            SandboxFivepointView current = authority.fivepoint;
            AddText(drawerContent, "当前规划边界 " + current.second + " 秒 · 与历史画面播放时刻分别显示", "body-text");
            if (authority.role == "host") { AddText(drawerContent, "主持人只启动共同规划，不查看双方私有候选。", "body-text"); return; }
            if (current.control_mode != "manual") { AddText(drawerContent, "本局为AI自动模式，双方方案由服务器共同采用。", "body-text"); return; }
            AddText(drawerContent, "候选包含单位动作、任务依赖、持续时间和预授权补给分支。采用前可逐项检查全文。", "body-text");
            var content = new TextField("本方候选全文") { multiline = true, isReadOnly = true,
                value = string.IsNullOrEmpty(current.planning_candidate_json) ? "当前边界没有待确认的本方候选。" : current.planning_candidate_json };
            content.AddToClassList("field");
            drawerContent.Add(content);
            SandboxServerContext basis = authority.context;
            bool reviewable = serverClient.Healthy && !serverClient.HasPending
                && current.phase == "planning" && !current.planning_active
                && (current.waiting_sides ?? Array.Empty<string>()).Contains(authority.role);
            AddButton(drawerContent, "采用以上本方候选", () => SendServerCommand("submit_fivepoint_plan",
                new SandboxFivepointDecision { expected_step = current.step, use_candidate = true }, basis), "primary-button")
                .SetEnabled(reviewable && !string.IsNullOrEmpty(current.planning_candidate_json));
            AddButton(drawerContent, "驳回并保留旧任务", () => SendServerCommand("submit_fivepoint_plan",
                new SandboxFivepointDecision { expected_step = current.step, decision = "reject" }, basis)).SetEnabled(reviewable);
        }

        /// <summary>使用实际五点分项显示当前或归档结果，不套用历史三点加权公式。</summary>
        /// <param name="frame">同一播放时刻或所选档案中的五点投影。</param>
        private void BuildFivepointResultReport(SandboxFivepointView frame)
        {
            ReportHeading("青岚五点结果", "占点、击毁与资源", "总分上限120分；全灭优先，正常到时比较总分，再比较控制点步。");
            var toolbar = SettingsGroup(settingsContent, "report-toolbar");
            AddButton(toolbar, "当前对局", () => { resultArchiveTitle = null; showingPreview = false; RenderSettingsSection(); }, "report-button");
            AddButton(toolbar, "历史档案", () => NavigateReport("history"), "report-button");
            var card = ReportCard(settingsContent, string.IsNullOrEmpty(resultArchiveTitle) ? "当前播放结果" : resultArchiveTitle);
            SandboxFivepointResult result = frame.result;
            bool ended = result != null && !string.IsNullOrEmpty(result.reason);
            string verdict = !ended ? "对局进行中" : result.reason == "technical_abort" ? "技术中止 · 不产生竞技胜者"
                : result.winner == "red" ? "红方胜" : result.winner == "blue" ? "蓝方胜" : "平局";
            AddText(card, verdict, "report-verdict");
            AddText(card, "仿真时刻 " + frame.second + " 秒 · " + (ended ? result.reason : "当前预估，不是终局成绩"), "report-description");
            if (ended && result.scores != null)
            {
                AddFivepointScore(card, "红方", result.scores.red);
                AddFivepointScore(card, "蓝方", result.scores.blue);
            }
            else if (frame.own_score != null) AddFivepointScore(card, "本方预估", frame.own_score);
            AddText(card, "占点分 = 70 × 控制点步 / 3600\n击毁分 = 20 × 有效击毁价值 / 敌方初始总价值\n资源分 = 15 × 能源比例 + 9 × 弹药比例 + 6 × 材料比例\n提前结束保持初始分母，显示舍入不参与判胜。", "report-calculation");
        }

        /// <summary>按服务端精确账本显示四项得分。</summary>
        /// <param name="parent">承载结果的容器。</param>
        /// <param name="caption">阵营或本方预估说明。</param>
        /// <param name="score">服务端已经计算的分项。</param>
        private void AddFivepointScore(VisualElement parent, string caption, SandboxFivepointScore score)
        {
            if (score == null) return;
            AddText(parent, caption + " · 总分 " + SandboxFivepointView.Display(score.total)
                + "\n占点 " + SandboxFivepointView.Display(score.control) + " · 击毁 " + SandboxFivepointView.Display(score.destruction)
                + " · 资源 " + SandboxFivepointView.Display(score.resource), "report-calculation");
        }

        /// <summary>展示同一播放帧中的五点、余额、任务和终局；技术按钮依据当前权威状态。</summary>
        private void RefreshFivepointPanel()
        {
            SandboxFivepointView frame = serverMode ? serverClient.View?.fivepoint : null;
            bool preparing = serverMode && frame == null && serverClient.AuthorityView?.role == "host"
                && serverClient.AuthorityView.clock.phase == "preparing";
            if (fivepointPanel == null && (frame != null || preparing))
            {
                fivepointPanel = new VisualElement { name = "fivepointPanel" };
                fivepointPanel.style.position = Position.Absolute;
                fivepointPanel.style.left = 20; fivepointPanel.style.top = 120;
                fivepointPanel.style.width = 370; fivepointPanel.style.paddingTop = 12;
                fivepointPanel.style.paddingBottom = 12; fivepointPanel.style.paddingLeft = 12;
                fivepointPanel.style.paddingRight = 12;
                fivepointPanel.style.backgroundColor = new UnityEngine.Color(0.04f, 0.08f, 0.13f, 0.94f);
                fivepointSummary = new Label(); fivepointSummary.style.whiteSpace = WhiteSpace.Normal;
                fivepointPanel.Add(fivepointSummary);
                fivepointPlanning = new Button(() => serverClient.PlanFivepoint()) { text = "启动双方共同规划" };
                fivepointAdvance = new Button(() => serverClient.ProduceFivepointRound()) { text = "按缓存推进当前轮" };
                fivepointRetry = new Button(() => serverClient.Command("retry_fivepoint_planning")) { text = "重试共同规划" };
                fivepointPanel.Add(fivepointPlanning); fivepointPanel.Add(fivepointAdvance); fivepointPanel.Add(fivepointRetry);
                fivepointAdopt = new Button(() => ShowPanel("plan")) { text = "查看候选并确认采用" };
                fivepointReject = new Button(() => serverClient.Command("submit_fivepoint_plan", new SandboxFivepointDecision
                    { expected_step = serverClient.AuthorityView.fivepoint.step, decision = "reject" })) { text = "驳回并保留旧任务" };
                fivepointPanel.Add(fivepointAdopt); fivepointPanel.Add(fivepointReject);
                fivepointAutomatic = new Button(() => serverClient.ToggleFivepointAutomatic()) { text = "连续运行" };
                fivepointPanel.Add(fivepointAutomatic);
                fivepointEnableManual = new Button(() => serverClient.Command("enable_fivepoint",
                    new SandboxFivepointEnable { control_mode = "manual" })) { text = "启用五点 · 人工确认" };
                fivepointEnableAuto = new Button(() => serverClient.Command("enable_fivepoint",
                    new SandboxFivepointEnable { control_mode = "auto" })) { text = "启用五点 · AI自动" };
                fivepointPanel.Add(fivepointEnableManual); fivepointPanel.Add(fivepointEnableAuto);
                root.Add(fivepointPanel);
            }
            if (fivepointPanel == null) return;
            fivepointPanel.style.display = frame == null && !preparing ? DisplayStyle.None : DisplayStyle.Flex;
            fivepointEnableManual.style.display = fivepointEnableAuto.style.display = preparing ? DisplayStyle.Flex : DisplayStyle.None;
            foreach (Button control in new[] { fivepointPlanning, fivepointAdvance, fivepointRetry, fivepointAdopt, fivepointReject, fivepointAutomatic })
                control.style.display = preparing ? DisplayStyle.None : DisplayStyle.Flex;
            if (preparing)
            {
                fivepointSummary.text = "五点规则\n双方准备完成后，由主持人冻结本局规则与操作模式。";
                bool ready = serverClient.Healthy && !serverClient.Busy && serverClient.AuthorityView.clock.ready.red && serverClient.AuthorityView.clock.ready.blue;
                fivepointEnableManual.SetEnabled(ready); fivepointEnableAuto.SetEnabled(ready);
            }
            if (frame == null) return;
            string summary = "五点对局 · 已播放 " + frame.second + " / 3600 秒\n";
            summary += "占点分 红 " + SandboxFivepointView.Display(frame.public_control?.red)
                + " / 蓝 " + SandboxFivepointView.Display(frame.public_control?.blue) + "\n";
            summary += string.Join("\n", (frame.points ?? Array.Empty<SandboxFivepointPoint>()).Select(point => point.point_id
                + "：" + (point.owner == "red" ? "红方" : point.owner == "blue" ? "蓝方" : "中立")
                + (point.contested ? " · 争夺中" : "")
                + (string.IsNullOrEmpty(point.candidate) ? "" : " · 本方进度 " + point.progress + "/30秒")));
            if (frame.own_score != null)
                summary += "\n本方预估总分 " + SandboxFivepointView.Display(frame.own_score.total)
                    + " · 击毁 " + SandboxFivepointView.Display(frame.own_score.destruction)
                    + " · 资源 " + SandboxFivepointView.Display(frame.own_score.resource);
            if (frame.own_resources != null)
                summary += "\n能源 " + SandboxFivepointView.Display(frame.own_resources.energy)
                    + " · 弹药 " + SandboxFivepointView.Display(frame.own_resources.ammo)
                    + " · 材料 " + SandboxFivepointView.Display(frame.own_resources.material);
            summary += "\n" + string.Join("\n", (frame.own_tasks ?? Array.Empty<SandboxFivepointTask>())
                .GroupBy(task => task.status).Select(group => group.Key + "：" + group.Count()));
            if (frame.result != null && !string.IsNullOrEmpty(frame.result.reason))
                summary += "\n" + (frame.result.reason == "technical_abort" ? "技术中止，无竞技胜者"
                    : frame.result.winner == "red" ? "红方胜" : frame.result.winner == "blue" ? "蓝方胜" : "平局")
                    + " · " + frame.result.reason;
            if (frame.result?.scores != null && !string.IsNullOrEmpty(frame.result.reason))
                summary += "\n终局总分 红 " + SandboxFivepointView.Display(frame.result.scores.red?.total)
                    + " / 蓝 " + SandboxFivepointView.Display(frame.result.scores.blue?.total);
            fivepointSummary.text = summary;
            SandboxFivepointView authority = serverClient.AuthorityView?.fivepoint;
            bool host = serverClient.AuthorityView?.role == "host" && !serverClient.Busy && serverClient.Healthy;
            fivepointPlanning.SetEnabled(host && authority?.phase == "planning" && !authority.planning_active);
            fivepointAdvance.SetEnabled(host && (authority?.phase == "paused" || authority?.phase == "running"));
            fivepointRetry.SetEnabled(host && authority?.phase == "technical_pause");
            bool canReview = !serverClient.Busy && serverClient.Healthy && serverClient.AuthorityView?.role != "host"
                && authority?.phase == "planning" && authority.control_mode == "manual" && !authority.planning_active
                && (authority.waiting_sides ?? Array.Empty<string>()).Contains(serverClient.AuthorityView.role);
            fivepointAdopt.SetEnabled(canReview && !string.IsNullOrEmpty(authority.planning_candidate_json));
            fivepointReject.SetEnabled(canReview);
            fivepointAutomatic.SetEnabled(host && authority?.phase != "technical_pause" && authority?.phase != "finished");
            fivepointAutomatic.text = serverClient.FivepointAutomatic ? "停止连续运行" : "连续运行";
        }
    }
}
