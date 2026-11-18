using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        /// <summary>由阶段驱动的常驻提示；不使用短时反馈计时，不参与关闭面板逻辑。</summary>
        private void RefreshDeploymentStageBanner()
        {
            VisualElement banner = root?.Q("deploymentStageBanner");
            if (banner == null) return;
            if (deploymentStaging)
            {
                banner.EnableInClassList("hidden", false);
                SetText("deploymentStageTitle", "部署阶段");
                SetText("deploymentStageDetail", BothDeploymentsReady ? "双方部署已就绪 · 战斗 → 本地手动测试 → 对战开始" : "双方各放置三座兵营并生成固定编制，再确认部署完成");
                return;
            }
            if (ManualBattle != null)
            {
                banner.EnableInClassList("hidden", false);
                SetText("deploymentStageTitle", ManualBattle.stage == "finished" ? "战斗结束" : "手动战斗 · 轮次决策");
                SetText("deploymentStageDetail", "本轮60仿真秒 · 红蓝分别锁定 → 主持人统一结算 → 动画回放");
                return;
            }
            string phase = serverMode ? serverClient?.View?.clock?.phase : null;
            bool unconfirmed = serverMode && string.IsNullOrEmpty(phase);
            bool preparing = serverMode ? phase == "preparing" : (projection?.step ?? 0) == 0;
            banner.EnableInClassList("hidden", !preparing && !unconfirmed);
            SetText("deploymentStageTitle", unconfirmed ? "阶段待确认" : "部署阶段");
            SetText("deploymentStageDetail", unconfirmed
                ? "正在核对后端阶段；请等待连接恢复，不会自动开始战斗。"
                : serverMode
                    ? "请完成双方兵营与单位部署；开战前此提示会持续显示。"
                    : "离线预览 · 部署阶段提示常驻；连接后以后端阶段为准。");
        }
    }
}
