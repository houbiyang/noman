using System;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        private VisualElement battleOverlay;
        private ScrollView battleChecks;
        private Label battleStatus;
        private Button battleCheckButton, battleAgentButton, battleManualButton;
        private Toggle battleProbeAi;
        private string battleMode = "agent";
        private int battleGeneration;
        private bool battleChecking;

        private void InitializeBattleEntry()
        {
            root.styleSheets.Add(Resources.Load<StyleSheet>("Sandbox/SandboxBattle"));
            BindButton("battleAction", OpenBattleEntry);
        }

        /// <summary>模式选择只产生本地草稿，不创建或改变对局，不借旧演示启动新总册模式。</summary>
        private void OpenBattleEntry()
        {
            if (settingsOverlay != null) { RequestCloseSettings(OpenBattleEntry); return; }
            if (battleOverlay != null) { CloseBattleEntry(); return; }
            ClosePanel(); CloseArmy(); CancelArmyCommand(); PausePlayback();
            battleGeneration++; battleChecking = false;
            battleOverlay = new VisualElement { name = "battleOverlay" };
            battleOverlay.AddToClassList("settings-backdrop"); battleOverlay.AddToClassList("ui-blocker");
            root.Add(battleOverlay);
            var panel = SettingsGroup(battleOverlay, "battle-panel");
            var header = SettingsGroup(panel, "settings-header");
            var heading = SettingsGroup(header, "settings-heading");
            AddText(heading, "穹图 / 战斗测试", "settings-eyebrow");
            AddText(heading, "选择对抗模式", "settings-title");
            AddText(heading, "一轮60仿真秒 · 最多60轮 · 检查不会改动现有对局", "settings-header-description");
            AddButton(header, "×", CloseBattleEntry, "settings-close").name = "closeBattleEntry";
            var modes = SettingsGroup(panel, "battle-modes");
            battleAgentButton = AddButton(modes, "Agent 对抗\n红蓝智能体按同一轮边界规划", () => SelectBattleMode("agent"), "battle-mode");
            battleAgentButton.name = "battleModeAgent";
            battleManualButton = AddButton(modes, "本地手动测试\n双方分别提交 · 主持人统一结算", () => SelectBattleMode("manual"), "battle-mode");
            battleManualButton.name = "battleModeManual";
            var content = SettingsGroup(panel, "battle-content");
            AddText(content, "接入中：模式选择和只读依赖检查已提供。总册结算与播放尚未完整接入，当前不能通过此入口开战。", "settings-notice");
            battleStatus = AddText(content, "", "settings-note"); battleStatus.name = "battleCheckStatus";
            battleProbeAi = new Toggle("同时测试双方已启用模型接口（真实请求可能计费；不自动重试）") { value = false, name = "battleProbeAi" };
            battleProbeAi.AddToClassList("settings-field"); content.Add(battleProbeAi);
            battleChecks = new ScrollView(ScrollViewMode.Vertical) { name = "battleChecks" };
            battleChecks.AddToClassList("battle-checks"); content.Add(battleChecks);
            var footer = SettingsGroup(panel, "settings-footer");
            AddText(footer, "本地手动模式只能绕过AI故障，不能绕过数据库、规则和执行器故障。", "settings-footer-copy");
            battleCheckButton = AddButton(footer, "检查开战依赖", CheckBattleDependencies, "settings-primary"); battleCheckButton.name = "battleCheckButton";
            var start = AddButton(footer, "开始战斗（待接入）", () => { }, "settings-secondary");
            start.name = "battleStartButton"; start.SetEnabled(false);
            SelectBattleMode(battleMode); battleAgentButton.Focus();
        }

        private void SelectBattleMode(string mode)
        {
            if (battleChecking || (mode != "agent" && mode != "manual")) return;
            battleMode = mode;
            battleAgentButton.EnableInClassList("battle-mode-selected", mode == "agent");
            battleManualButton.EnableInClassList("battle-mode-selected", mode == "manual");
            battleChecks.Clear();
            bool host = serverMode && serverClient?.View?.role == "host";
            battleStatus.text = !serverMode ? "离线预览不能执行后端检查；请先连接本机服务。"
                : !host ? "请先关闭面板，切换主持人视角后检查双方依赖。"
                : "尚未检查。未勾选模型测试时，只核对本地依赖与配置，不宣称AI连通成功。";
            battleCheckButton.SetEnabled(host);
        }

        private void CheckBattleDependencies()
        {
            if (battleChecking || battleOverlay == null) return;
            int generation = battleGeneration;
            battleChecking = true;
            battleCheckButton.SetEnabled(false); battleAgentButton.SetEnabled(false); battleManualButton.SetEnabled(false); battleProbeAi.SetEnabled(false);
            battleStatus.text = "正在检查后端、数据库、规则与地图；不会启动或推进对局…";
            bool sent = serverClient != null && serverClient.CheckBattle(battleMode, battleProbeAi.value, (result, error) =>
            {
                if (generation != battleGeneration || battleOverlay == null) return;
                CompleteBattleCheck(result, error);
            });
            if (!sent) CompleteBattleCheck(null, "后端未就绪、正在同步或有命令待核对；未发送检查，请稍后手动重试。");
        }

        private void CompleteBattleCheck(SandboxBattlePreflightResult result, string error)
        {
            battleChecking = false; battleCheckButton.SetEnabled(true); battleAgentButton.SetEnabled(true); battleManualButton.SetEnabled(true); battleProbeAi.SetEnabled(true);
            battleChecks.Clear();
            if (error != null || result?.checks == null)
            {
                battleStatus.text = error ?? "开战检查结果无效；未启动战斗。";
                return;
            }
            int failed = 0;
            foreach (var item in result.checks)
            {
                if (item == null) continue;
                bool good = item.status == "passed" || item.status == "not_required";
                if (!good) failed++;
                string state = item.status == "passed" ? "通过" : item.status == "not_required" ? "不需要" : item.status == "not_tested" ? "未测试" : "未通过";
                var row = SettingsGroup(battleChecks, "battle-check");
                row.EnableInClassList("battle-check-warning", !good);
                AddText(row, state + "  ·  " + item.name, "battle-check-title");
                AddText(row, item.message, "settings-note");
            }
            battleStatus.text = failed > 0 ? "检查完成：存在未通过或未测试项；未启动战斗。"
                : "本次依赖检查完成；不等同开局授权，当前尚未发布新模式开局接口。";
            // 即使旧响应错误地声称可开局，也没有按钮或命令路径绕过未发布的新执行器。
        }

        private void CloseBattleEntry()
        {
            battleGeneration++; battleChecking = false;
            battleOverlay?.RemoveFromHierarchy(); battleOverlay = null;
            root?.Q<Button>("battleAction")?.Focus();
        }
    }
}
