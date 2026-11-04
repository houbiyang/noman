using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace Qiongtu.Sandbox
{
    // Local configuration draft. This is deliberately separate from GameView, commands and game saves.
    [Serializable]
    public sealed class SandboxModelEndpoint
    {
        public bool enabled;
        public string provider = "兼容接口";
        public string baseUrl = "";
        public string model = "";
        public string credentialRef = "";
    }

    [Serializable]
    public sealed class SandboxSideAiSettings
    {
        public SandboxModelEndpoint generation = new SandboxModelEndpoint { provider = "DeepSeek", model = "deepseek-chat" };
        public SandboxModelEndpoint embedding = new SandboxModelEndpoint();
        public SandboxModelEndpoint rerank = new SandboxModelEndpoint();
        public string profileVersion = "local-v1";
        public string promptVersion = "";
        public string knowledgeScope = "";
        public float temperature = .2f;
        public int maxOutputTokens = 4096;
        public int runTokenBudget = 20000;
        public int timeoutSeconds = 60;
        public int readRetries;
    }

    [Serializable]
    public sealed class SandboxClientSettings
    {
        public int formatVersion = 1;
        public string serviceBaseUrl = "http://127.0.0.1:8000/api/v1";
        public int serviceTimeoutSeconds = 10;
        public SandboxSideAiSettings red = new SandboxSideAiSettings();
        public SandboxSideAiSettings blue = new SandboxSideAiSettings();
        public bool showGrid;
        public bool showRoutes = true;
        public bool showFacilities = true;
        public bool showLabels = true;
    }

    public sealed class SandboxSettingsIssue
    {
        public string field;
        public string message;
        public SandboxSettingsIssue(string path, string text) { field = path; message = text; }
    }

    public static class SandboxSettingsValidation
    {
        // These bounds protect a local draft; they are not authoritative server quotas or frozen API fields.
        public static List<SandboxSettingsIssue> Validate(SandboxClientSettings settings)
        {
            var errors = new List<SandboxSettingsIssue>();
            if (settings == null || settings.formatVersion != 1)
            { errors.Add(new SandboxSettingsIssue("formatVersion", "设置格式不支持，原记录已保留")); return errors; }
            Endpoint(errors, "serviceBaseUrl", settings.serviceBaseUrl, true);
            if (!string.IsNullOrWhiteSpace(settings.serviceBaseUrl) && !settings.serviceBaseUrl.TrimEnd('/').EndsWith("/api/v1", StringComparison.Ordinal))
                errors.Add(new SandboxSettingsIssue("serviceBaseUrl", "仿真服务根地址须以 /api/v1 结尾"));
            Range(errors, "serviceTimeoutSeconds", settings.serviceTimeoutSeconds, 1, 120);
            Side(errors, "red", settings.red); Side(errors, "blue", settings.blue);
            return errors;
        }

        public static string EndpointError(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return "请填写接口根地址";
            if (value.Length > 512 || !Uri.TryCreate(value.Trim(), UriKind.Absolute, out var uri)
                || (uri.Scheme != "https" && uri.Scheme != "http") || string.IsNullOrEmpty(uri.Host)) return "请填写有效的 HTTP(S) 根地址";
            if (!string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment))
                return "根地址不能携带账号、密码、查询参数或片段";
            if (uri.Scheme == "http" && !uri.IsLoopback) return "远程服务请使用 HTTPS；HTTP 仅限本机回环地址";
            return "";
        }

        private static void Side(List<SandboxSettingsIssue> errors, string path, SandboxSideAiSettings side)
        {
            if (side == null) { errors.Add(new SandboxSettingsIssue(path, "阵营配置缺失")); return; }
            Model(errors, path + ".generation", side.generation);
            Model(errors, path + ".embedding", side.embedding);
            Model(errors, path + ".rerank", side.rerank);
            if (float.IsNaN(side.temperature) || float.IsInfinity(side.temperature) || side.temperature < 0 || side.temperature > 2)
                errors.Add(new SandboxSettingsIssue(path + ".temperature", "采样温度须在 0–2 之间"));
            Range(errors, path + ".maxOutputTokens", side.maxOutputTokens, 1, 131072);
            Range(errors, path + ".runTokenBudget", side.runTokenBudget, 1, 1000000);
            if (side.maxOutputTokens > side.runTokenBudget) errors.Add(new SandboxSettingsIssue(path + ".runTokenBudget", "单次运行预算不能低于输出上限"));
            Range(errors, path + ".timeoutSeconds", side.timeoutSeconds, 1, 600);
            Range(errors, path + ".readRetries", side.readRetries, 0, 3);
            Text(errors, path + ".profileVersion", side.profileVersion, 80, true);
            Text(errors, path + ".promptVersion", side.promptVersion, 80, false);
            Text(errors, path + ".knowledgeScope", side.knowledgeScope, 120, false);
        }

        private static void Model(List<SandboxSettingsIssue> errors, string path, SandboxModelEndpoint model)
        {
            if (model == null) { errors.Add(new SandboxSettingsIssue(path, "模型配置缺失")); return; }
            Endpoint(errors, path + ".baseUrl", model.baseUrl, model.enabled);
            Text(errors, path + ".model", model.model, 120, model.enabled);
            Text(errors, path + ".provider", model.provider, 80, model.enabled);
            if (!string.IsNullOrEmpty(model.credentialRef) && !Regex.IsMatch(model.credentialRef, "^[A-Z][A-Z0-9_]{0,79}$"))
                errors.Add(new SandboxSettingsIssue(path + ".credentialRef", "只填写大写环境变量名，例如 RED_AI_API_KEY；不要填写密钥本身"));
        }
        private static void Endpoint(List<SandboxSettingsIssue> errors, string path, string value, bool required)
        {
            if (!required && string.IsNullOrEmpty(value)) return;
            string error = EndpointError(value);
            if (error.Length > 0) errors.Add(new SandboxSettingsIssue(path, error));
        }
        private static void Text(List<SandboxSettingsIssue> errors, string path, string value, int max, bool required)
        {
            if (required && string.IsNullOrWhiteSpace(value) || value != null && (value.Length > max || value.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0))
                errors.Add(new SandboxSettingsIssue(path, "请填写有效文本，最多 " + max + " 字"));
        }
        private static void Range(List<SandboxSettingsIssue> errors, string path, int value, int min, int max)
        {
            if (value < min || value > max) errors.Add(new SandboxSettingsIssue(path, "数值须在 " + min + "–" + max + " 之间"));
        }
    }
}
