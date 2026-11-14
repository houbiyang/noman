using System;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        private SandboxHistoryStore historyStore;
        private SandboxMatchArchive selectedHistory;
        private SandboxVictoryEvidence resultEvidence;
        private bool showingPreview = true;
        private bool pendingEndResult;
        private string noticedEndKey;
        private string historyMessage = "";
        private string historyName = "";
        private int historyPage;
        private string resultArchiveTitle;

        private SandboxHistoryStore History => historyStore ?? (historyStore = new SandboxHistoryStore());

        private static string ArchiveSource(SandboxMatchArchive entry) => entry.source == "preview" ? "规则预演 · 非真实战果" : entry.source == "server" ? "后端席位观察档案" : "本地沙盘观察档案";

        private bool SaveCurrentHistory(bool automatic, out string message)
        {
            var view = serverMode ? serverClient.View : null;
            if (serverMode && view == null) { message = "尚未取得后端视图，不能归档"; return false; }
            if (serverMode)
            {
                if (automatic) { message = "后端结束事务已自动归档各席位，刷新服务端历史可查看"; return true; }
                if (serverClient.Busy || serverClient.HasPending || !serverClient.Healthy) { message = "后端同步未就绪，未提交归档"; return false; }
                serverClient.Command("archive_match", new SandboxServerSaveInput { display_name = string.IsNullOrWhiteSpace(historyName) ? null : historyName.Trim() });
                message = "已提交服务端归档请求，成功后请刷新记录；未写入浏览器"; return true;
            }
            var observation = SandboxHistoryObservation.FromProjection(projection);
            var entry = NewArchive();
            entry.title = string.IsNullOrWhiteSpace(historyName) ? (automatic ? "对局结束 / " : "对局记录 / ") + projection.title : historyName.Trim();
            if (entry.title.Length > 100) entry.title = entry.title.Substring(0, 100);
            entry.source = serverMode ? "server" : "local"; entry.observation = observation;
            entry.matchId = view?.game.game_id ?? source.Current.sessionId;
            entry.branchId = view?.context.branch_id; entry.snapshotId = view?.context.snapshot_id;
            entry.phase = serverMode ? ServerPhaseLabel() : projection.step >= 60 ? "本地演示结束" : "进行中";
            entry.automaticKey = automatic ? EndKey() : null;
            // Local simulation has no authority to settle war goals. Never attach the sample.
            entry.victory = null;
            if (automatic)
            {
                var existing = History.ReadAll(out string warning);
                if (existing.Any(item => item.automaticKey == entry.automaticKey)) { message = "本场结束档案已存在"; return true; }
                if (warning.Length > 0) { message = warning + "；未自动写入，请在历史记录中手动归档"; return false; }
            }
            return History.TrySave(entry, out message);
        }

        private SandboxMatchArchive NewArchive() => new SandboxMatchArchive {
            id = Guid.NewGuid().ToString("N"), savedAt = DateTimeOffset.Now.ToString("O"), role = role
        };

        private string EndKey() => serverMode
            ? "server/" + serverClient.View.game.game_id + "/" + serverClient.View.context.branch_id + "/" + role
            : "local/" + source.Current.sessionId + "/" + role;

        private void ObserveMatchEnd()
        {
            bool ended = serverMode ? serverClient.View?.clock.phase == "finished" : projection.step >= 60;
            if (!ended) { noticedEndKey = null; return; }
            string key = EndKey();
            if (noticedEndKey == key) return;
            noticedEndKey = key;
            SaveCurrentHistory(true, out historyMessage);
            showingPreview = false; resultEvidence = serverMode ? serverClient.View?.victory : null; resultArchiveTitle = null; pendingEndResult = true;
            Notify(historyMessage + "；结束结果可在设置 → 演示结果查看");
        }

        private void ShowPendingEndResult()
        {
            if (!pendingEndResult || settingsOverlay != null || modalAction != null || connectionOverlay != null) return;
            pendingEndResult = false;
            OpenReport("results");
        }

        private void SelectResultScenario(int scenario)
        {
            showingPreview = true; resultArchiveTitle = null; resultScenario = scenario; resultMessage = "";
            resultEvidence = SandboxBattleRules.ProjectPreview(scenario); RenderSettingsSection();
        }

        private void BuildHistorySettings() => BuildHistoryReport();
        private void BuildResultSettings() => BuildResultReport();
    }
}
