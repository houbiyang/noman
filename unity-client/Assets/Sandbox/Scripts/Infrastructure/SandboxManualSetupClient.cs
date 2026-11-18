using System;
using System.Collections;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxServerClient
    {
        private string manualCreationKey;
        private SandboxManualCreatePayload manualCreationPayload;
        public bool HasManualCreation => manualCreationKey != null;

        private static string LocalWebOrigin()
        {
            if (Uri.TryCreate(Application.absoluteURL, UriKind.Absolute, out Uri page)
                && page.Scheme == "http" && (page.Host == "127.0.0.1" || page.Host == "localhost"))
                return page.GetLeftPart(UriPartial.Authority);
            return "http://127.0.0.1:18766";
        }

        private bool CanConfigureManual => !Busy && pendingBody == null && queuedMovementKind == null && Healthy && View?.role == "host";

        public bool ReadManualSetup(Action<SandboxManualSetupCatalogue, string> completed)
        {
            if (!CanConfigureManual) return false;
            StartCoroutine(ReadManualSetupRequest(completed)); return true;
        }

        private IEnumerator ReadManualSetupRequest(Action<SandboxManualSetupCatalogue, string> completed)
        {
            Busy = true;
            int generation = ConnectionGeneration;
            var input = new SandboxManualLocalInput { game_id = gameId, map_hash = mapHash, capture_layout_sha256 = SandboxCaptureLayout.ContentHash };
            yield return Request("POST", LocalWebOrigin() + "/api/local-manual/setup", JsonUtility.ToJson(input), null, bearer);
            var result = responseCode == 200 ? ReadJson<SandboxManualSetupCatalogue>() : null;
            string error = generation != ConnectionGeneration ? "连接已变化，请重新读取配置。"
                : !ValidManualCatalogue(result, mapHash) ? LocalManualError("手动配置响应无效，请重试。") : null;
            if (error == null)
            {
                manualCreationKey = result.pending_creation?.request_key;
                manualCreationPayload = result.pending_creation?.payload;
            }
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
            completed?.Invoke(error == null ? result : null, error);
            Changed?.Invoke();
        }

        public static bool ValidManualCatalogue(SandboxManualSetupCatalogue value, string expectedMap)
        {
            return value != null && value.mode == "manual" && value.round_seconds == 60 && value.maximum_rounds == 60
                && value.map?.content_hash == expectedMap
                && value.capacity != null && value.capacity.minimum_per_type == 0 && value.capacity.maximum_per_type == 10
                && value.capacity.minimum_per_side == 1 && value.capacity.maximum_per_side == 30 && value.capacity.maximum_per_match == 60
                && value.capacity.scope == "local_battle_test_only" && value.capacity.overflow_policy == "reject_without_truncation"
                && value.capacity.asymmetric_allowed && !value.capacity.formal_release_certified
                && Guid.TryParse(value.rule_version_id, out _) && Guid.TryParse(value.scenario_version_id, out _)
                && Guid.TryParse(value.capacity_version_id, out _)
                && Digest(value.rule_sha256) && Digest(value.scenario_sha256) && Digest(value.capacity_sha256)
                && (value.pending_creation == null || Guid.TryParse(value.pending_creation.request_key, out _) && value.pending_creation.payload != null);
        }
        public bool PreviewCamp(SandboxCampPlacementRequest placement, Action<SandboxCampPlacementResult, string> completed)
        {
            if (!CanConfigureManual || HasManualCreation) return false;
            StartCoroutine(PreviewCampRequest(placement, completed)); return true;
        }
        private IEnumerator PreviewCampRequest(SandboxCampPlacementRequest placement, Action<SandboxCampPlacementResult, string> completed)
        {
            Busy = true;
            var body = new SandboxCampPlacementInput { game_id = gameId, map_hash = mapHash,
                capture_layout_sha256 = SandboxCaptureLayout.ContentHash, placement = placement };
            yield return Request("POST", LocalWebOrigin() + "/api/local-manual/preview", JsonUtility.ToJson(body), null, bearer);
            var result = responseCode == 200 ? ReadJson<SandboxCampPlacementResult>() : null;
            string error = result?.camp?.position != null && result.units != null && result.count == placement.count
                && result.units.Length == placement.count ? null : LocalManualError("部署验收失败；原部署已保留，请重新选择位置。");
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
            completed?.Invoke(error == null ? result : null, error); Changed?.Invoke();
        }

        public bool ValidateManualDeployment(SandboxManualCreatePayload payload, Action<bool, string> completed)
        {
            if (!CanConfigureManual || HasManualCreation) return false;
            StartCoroutine(ValidateManualDeploymentRequest(payload, completed)); return true;
        }
        private IEnumerator ValidateManualDeploymentRequest(SandboxManualCreatePayload payload, Action<bool, string> completed)
        {
            Busy = true;
            var body = new SandboxManualValidateInput { game_id=gameId, map_hash=mapHash,
                capture_layout_sha256=SandboxCaptureLayout.ContentHash, payload=payload };
            yield return Request("POST", LocalWebOrigin() + "/api/local-manual/validate", JsonUtility.ToJson(body), null, bearer);
            bool valid = responseCode == 200 && ReadJson<SandboxManualValidation>()?.valid == true;
            string error = valid ? null : LocalManualError("整体部署验收未完成，请检查兵营和出兵区。");
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
            completed?.Invoke(valid, error); Changed?.Invoke();
        }

        private static bool Digest(string value)
        {
            if (value == null || value.Length != 64) return false;
            foreach (char c in value) if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')) return false;
            return true;
        }

        public bool CreateManualGame(SandboxManualCreatePayload payload, Action<bool, string> completed)
        {
            if (!CanConfigureManual || payload == null) return false;
            // 不允许待核对请求因编辑器对象变化而换正文；只使用此前冻结的深拷贝。
            if (manualCreationKey == null)
            {
                manualCreationKey = Guid.NewGuid().ToString();
                manualCreationPayload = JsonUtility.FromJson<SandboxManualCreatePayload>(JsonUtility.ToJson(payload));
            }
            StartCoroutine(ManualCreationRequest(false, completed)); return true;
        }

        public bool RecoverManualCreation(Action<bool, string> completed)
        {
            if (!CanConfigureManual || manualCreationKey == null) return false;
            StartCoroutine(ManualCreationRequest(true, completed)); return true;
        }

        private IEnumerator ManualCreationRequest(bool recover, Action<bool, string> completed)
        {
            Busy = true;
            string json = recover ? JsonUtility.ToJson(new SandboxManualRecoverInput { game_id = gameId, map_hash = mapHash,
                capture_layout_sha256 = SandboxCaptureLayout.ContentHash, request_key = manualCreationKey })
                : JsonUtility.ToJson(new SandboxManualCreateInput { game_id = gameId, map_hash = mapHash,
                capture_layout_sha256 = SandboxCaptureLayout.ContentHash, request_key = manualCreationKey, payload = manualCreationPayload });
            yield return Request("POST", LocalWebOrigin() + (recover ? "/api/local-manual/recover" : "/api/local-manual/create"), json, null, bearer);
            var launch = responseCode == 200 ? ReadJson<SandboxServerLaunch>() : null;
            bool valid = launch != null && launch.role == "host" && launch.api_url == endpoint
                && Guid.TryParse(launch.game_id, out _) && !string.IsNullOrEmpty(launch.access_token);
            string error = valid ? null : LocalManualError("创建结果待核对；保留原请求，请读取配置或恢复结果。");
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
            if (valid)
            {
                manualCreationKey = null; manualCreationPayload = null;
                automaticRole = "host";
                valid = Connect(launch.api_url, launch.game_id, launch.access_token);
                if (!valid) error = "新局已创建，但接入信息未通过校验，请重新接入主持人。";
            }
            completed?.Invoke(valid, error);
            Changed?.Invoke();
        }

        private string LocalManualError(string fallback)
        {
            // 仅本机代理返回的已脱敏错误可显示；不记录响应正文或席位令牌。
            var result = responseCode > 0 ? ReadJson<SandboxServerReceipt>() : null;
            return result?.error?.message ?? fallback;
        }
    }
}
