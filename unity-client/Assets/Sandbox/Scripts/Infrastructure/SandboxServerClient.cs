using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
#if UNITY_WEBGL && !UNITY_EDITOR
using System.Runtime.InteropServices;
#else
using System.Net.Http;
using System.Net.Http.Headers;
#endif

namespace Qiongtu.Sandbox
{
    /// <summary>区分本机后端连接状态与命令执行记录。</summary>
    public enum SandboxClientLogCategory { Connection, Command }

    /// <summary>仅保存可展示的本机连接与命令摘要。</summary>
    public sealed class SandboxClientLogEntry
    {
        public string Time { get; }
        public string Message { get; }
        public SandboxClientLogCategory Category { get; }
        public bool IsError { get; }

        /// <summary>创建不含席位凭据和请求正文的运行记录。</summary>
        /// <param name="message">供页面展示的操作摘要。</param>
        /// <param name="category">连接或命令分类。</param>
        /// <param name="isError">该记录是否表示失败。</param>
        public SandboxClientLogEntry(string message, SandboxClientLogCategory category, bool isError)
        {
            Time = DateTime.Now.ToString("HH:mm:ss");
            Message = message;
            Category = category;
            IsError = isError;
        }
    }

    /// <summary>只在成功读取并校验后发布权威视图，不把请求中的目标当作已提交事实。</summary>
    public sealed class SandboxServerClient : MonoBehaviour
    {
        private string endpoint, gameId, bearer, mapId, mapHash;
        private string responseBody, pendingBody, pendingKey, pendingCommandType, pendingBranchId;
        private long responseCode;
        private float nextPoll;
        private string automaticRole;
        private string queuedMovementKind;
        private object queuedMovementPayload;
        private readonly List<SandboxClientLogEntry> recentLogs = new List<SandboxClientLogEntry>();
        private string lastConnectionIssue;
#if UNITY_WEBGL && !UNITY_EDITOR
        private bool webComplete;
        private int webSequence;
        [DllImport("__Internal")]
        private static extern void SandboxBackendFetch(string receiver, int sequence, string url, string method, string token, string body, string key);
#endif
        public event Action Changed;
        public event Action<string> Feedback;
        public event Action<string, bool> CommandFinished;
        public event Action LogChanged;
        public IReadOnlyList<SandboxClientLogEntry> RecentLogs => recentLogs;
        public SandboxServerView View { get; private set; }
        public bool Busy { get; private set; }
        public bool Healthy { get; private set; }
        public bool HasPending => pendingBody != null || queuedMovementKind != null;
        public string Status { get; private set; } = "尚未连接后端";

        /// <summary>保留最近一百条公开摘要，避免轮询和长时间运行占用无限内存。</summary>
        /// <param name="message">不包含凭据、请求正文或原始响应的摘要。</param>
        /// <param name="category">连接或命令分类。</param>
        /// <param name="isError">是否显示为失败记录。</param>
        private void AddLog(string message, SandboxClientLogCategory category, bool isError = false)
        {
            recentLogs.Add(new SandboxClientLogEntry(message, category, isError));
            if (recentLogs.Count > 100) recentLogs.RemoveAt(0);
            LogChanged?.Invoke();
        }

        /// <summary>配置本地显示地图身份，服务端版本不一致时禁止发布画面。</summary>
        public void Initialize(string expectedMapId, string expectedHash) { mapId = expectedMapId; mapHash = expectedHash; }

        /// <summary>通过本机同源接入服务获取所选角色会话，不要求用户填写连接参数。</summary>
        /// <param name="role">红方、蓝方或主持人的后端角色标识。</param>
        /// <returns>请求已开始时返回真；原命令未核对或网络忙碌时返回假。</returns>
        public bool ConnectAutomatically(string role)
        {
            if (Busy || HasPending || (role != "red" && role != "blue" && role != "host")) return false;
            automaticRole = role; endpoint = null; bearer = null; gameId = null; View = null; Healthy = false;
            Status = "正在自动接入后端…";
            AddLog("开始接入本机后端 · " + role, SandboxClientLogCategory.Connection);
            StartCoroutine(AutomaticConnect());
            return true;
        }

        /// <summary>申请单席位后读取权威视图，失败时保留等待状态并延时重试。</summary>
        private IEnumerator AutomaticConnect()
        {
            Busy = true; Changed?.Invoke();
            string origin = "http://127.0.0.1:18766";
            if (Uri.TryCreate(Application.absoluteURL, UriKind.Absolute, out Uri page)
                && page.Scheme == "http" && (page.Host == "127.0.0.1" || page.Host == "localhost"))
                origin = page.GetLeftPart(UriPartial.Authority);
            yield return Request("POST", origin + "/api/local-session",
                JsonUtility.ToJson(new SandboxServerLaunchInput { role = automaticRole, map_hash = mapHash }), null, null);
            SandboxServerLaunch launch = responseCode == 200 ? ReadJson<SandboxServerLaunch>() : null;
            Busy = false;
            if (launch == null || launch.role != automaticRole || !Connect(launch.api_url, launch.game_id, launch.access_token))
            {
                Healthy = false; Status = responseCode == 200 ? "接入信息无效，正在等待重试" : PublicError();
                if (lastConnectionIssue != Status) { lastConnectionIssue = Status; AddLog("接入失败 · " + Status, SandboxClientLogCategory.Connection, true); }
                nextPoll = Time.realtimeSinceStartup + 3;
                Changed?.Invoke();
            }
        }

        /// <summary>显式绑定单个席位，凭据只保留在当前进程内存。</summary>
        public bool Connect(string api, string targetGame, string accessToken)
        {
            if (Busy || HasPending) { Feedback?.Invoke("请先等待或核对原命令结果，再更换连接"); return false; }
            if (!Uri.TryCreate(api, UriKind.Absolute, out Uri address) || address.Scheme != "http"
                || (address.Host != "127.0.0.1" && address.Host != "localhost") || !string.IsNullOrEmpty(address.UserInfo)
                || !string.IsNullOrEmpty(address.Query) || !string.IsNullOrEmpty(address.Fragment)
                || address.AbsolutePath.TrimEnd('/') != "/api/v1" || !Guid.TryParse(targetGame, out _)
                || string.IsNullOrWhiteSpace(accessToken) || accessToken.Length > 256 || accessToken.Contains("\n") || accessToken.Contains("\r"))
            { Feedback?.Invoke("请填写本机 /api/v1 地址、有效对局 UUID 和席位令牌"); return false; }
            endpoint = api.TrimEnd('/'); gameId = Guid.Parse(targetGame).ToString(); bearer = accessToken.Trim(); View = null; Healthy = false;
            StartCoroutine(Refresh());
            return true;
        }

        /// <summary>主动刷新以及断线后的重连始终从数据库有效头读取。</summary>
        public void RefreshNow() { if (!Busy && endpoint != null) StartCoroutine(Refresh()); }

        /// <summary>以原键和原正文核对未明确完成的命令，禁止自动改键重发。</summary>
        public void RetryPending() { if (!Busy && HasPending) StartCoroutine(Commit()); }

        /// <summary>提交严格类型的内部载荷，在收据明确之前不修改地图。</summary>
        public void Command(string kind, object payload = null, SandboxServerContext basis = null)
        {
            if (Busy && !HasPending && Healthy && View != null && basis == null && kind == "movement_order")
            {
                queuedMovementKind = kind; queuedMovementPayload = payload;
                Feedback?.Invoke("已接收移动目标，当前状态读取完成后立即提交"); return;
            }
            if (Busy || HasPending || !Healthy || View == null) { Feedback?.Invoke("同步尚未就绪或原命令待核对，请稍后重试"); return; }
            SandboxServerContext context = basis ?? View.context;
            string head = JsonUtility.ToJson(new SandboxServerCommandHead { context = context, command_type = kind });
            pendingBody = head.Substring(0, head.Length - 1) + ",\"payload\":" + (payload == null ? "{}" : JsonUtility.ToJson(payload)) + "}";
            pendingKey = Guid.NewGuid().ToString();
            pendingCommandType = kind;
            pendingBranchId = context.branch_id;
            AddLog("提交命令 · " + kind, SandboxClientLogCategory.Command);
            StartCoroutine(Commit());
        }

        /// <summary>读取后端存档摘要，失败时保持当前页面状态不变。</summary>
        public void LoadSaves(Action<SandboxServerSaveList> completed, int offset = 0)
        {
            if (Busy || HasPending || !Healthy || View == null) { Feedback?.Invoke("同步尚未就绪，暂时不能读取后端存档"); completed?.Invoke(null); return; }
            StartCoroutine(LoadSavesRequest(completed, offset));
        }

        public void LoadArchives(Action<SandboxServerArchiveList> completed, int offset, string search, string phase)
        {
            if (Busy || HasPending || !Healthy || View == null) { completed?.Invoke(null); return; }
            StartCoroutine(ArchiveRequest("/archives?offset=" + offset + "&limit=20&search=" + Uri.EscapeDataString(search ?? "") + "&phase=" + phase, text => completed?.Invoke(text == null ? null : JsonUtility.FromJson<SandboxServerArchiveList>(text))));
        }

        public void LoadArchive(string id, Action<SandboxServerArchiveDetail> completed)
        {
            if (Busy || HasPending || !Healthy || View == null || !Guid.TryParse(id, out _)) { completed?.Invoke(null); return; }
            StartCoroutine(ArchiveRequest("/archives/" + id, text => completed?.Invoke(text == null ? null : JsonUtility.FromJson<SandboxServerArchiveDetail>(text))));
        }

        private IEnumerator ArchiveRequest(string path, Action<string> completed)
        {
            Busy = true; Changed?.Invoke();
            yield return Fetch("GET", path, null, null);
            string text = responseCode == 200 ? responseBody : null;
            if (text == null) Feedback?.Invoke(PublicError());
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
            if (text != null) completed(text);
            else completed(null);
            Changed?.Invoke();
        }

        /// <summary>事件预览绑定当时快照，调用方保留原依据用于提交。</summary>
        public void Preview(SandboxServerConditionInput condition, Action<SandboxServerPreview, SandboxServerContext> completed)
        {
            if (Busy || HasPending || !Healthy || View == null) { Feedback?.Invoke("请等待同步完成后重新预览"); return; }
            StartCoroutine(PreviewRequest(condition, completed));
        }

        /// <summary>预览测试兵营完整占地，不修改数据库状态。</summary>
        public void PreviewPreparation(SandboxServerPreparation input, Action<SandboxServerPreview, SandboxServerContext> completed)
        {
            if (Busy || HasPending || !Healthy || View == null) { Feedback?.Invoke("请等待同步后再预览兵营"); return; }
            StartCoroutine(PreparationRequest(input, completed));
        }

        private IEnumerator PreparationRequest(SandboxServerPreparation input, Action<SandboxServerPreview, SandboxServerContext> completed)
        {
            Busy = true;
            SandboxServerContext basis = View.context;
            yield return Fetch("POST", "/preparation/preview", JsonUtility.ToJson(new SandboxServerPreparationRequest { context = basis, preparation = input }), null);
            SandboxServerPreview preview = responseCode == 200 ? ReadJson<SandboxServerPreview>() : null;
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
            if (preview != null && preview.valid && !string.IsNullOrEmpty(preview.preview_token)) completed(preview, basis);
            else Feedback?.Invoke(PublicError());
        }

        /// <summary>按现实时间轮询，浏览器恢复前台时读取最新头，不自行补演示步。</summary>
        private void Update()
        {
            if (automaticRole != null && endpoint == null && !Busy && Time.realtimeSinceStartup >= nextPoll)
            { StartCoroutine(AutomaticConnect()); return; }
            if (endpoint != null && !Busy && Time.realtimeSinceStartup >= nextPoll)
            {
                if (pendingBody != null) StartCoroutine(VerifyPending());
                else StartCoroutine(Refresh());
            }
        }

        private IEnumerator Refresh()
        {
            Busy = true;
            yield return Fetch("GET", "/view", null, null);
            PublishResponse();
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
            Changed?.Invoke();
            if (queuedMovementKind != null)
            {
                string kind = queuedMovementKind; object payload = queuedMovementPayload;
                queuedMovementKind = null; queuedMovementPayload = null;
                if (Healthy) Command(kind, payload);
                else Feedback?.Invoke("连接未恢复，移动目标未提交，请重试");
            }
        }

        private IEnumerator Commit()
        {
            Busy = true; Changed?.Invoke();
            yield return Fetch("POST", "/commands", pendingBody, pendingKey);
            long commandCode = responseCode;
            SandboxServerReceipt receipt = responseCode >= 200 && responseCode < 500 ? ReadJson<SandboxServerReceipt>() : null;
            if (responseCode == 0 || responseCode >= 500 || (responseCode >= 200 && responseCode < 300 && receipt?.status != "succeeded" && receipt?.status != "rejected"))
            {
                Status = "命令结果待核对；正在自动查询原请求";
                AddLog("命令 " + pendingCommandType + " · 结果待核对 · HTTP " + commandCode, SandboxClientLogCategory.Command, true);
                Feedback?.Invoke(Status);
            }
            else
            {
                string commandType = pendingCommandType;
                bool succeeded = receipt?.status == "succeeded";
                string message = succeeded ? "后端已提交，正在读取保存后的状态" : PublicError();
                AddLog("命令 " + commandType + " · HTTP " + commandCode + " · "
                    + (succeeded ? "执行成功" : "失败 " + (receipt?.error?.code ?? "UNKNOWN") + " · " + message), SandboxClientLogCategory.Command, !succeeded);
                pendingBody = null; pendingKey = null; pendingBranchId = null;
                yield return Fetch("GET", "/view", null, null);
                PublishResponse();
                Feedback?.Invoke(message);
                CommandFinished?.Invoke(commandType, succeeded);
                pendingCommandType = null;
            }
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1; Changed?.Invoke();
        }

        /// <summary>按原分支和幂等键查询超时命令的权威收据，不创建第二条命令。</summary>
        /// <returns>等待本机后端核对结果的协程。</returns>
        private IEnumerator VerifyPending()
        {
            Busy = true;
            yield return Fetch("GET", "/commands/by-request?origin_branch_id=" + Uri.EscapeDataString(pendingBranchId)
                + "&request_key=" + Uri.EscapeDataString(pendingKey), null, null);
            SandboxServerReceipt receipt = responseCode == 200 ? ReadJson<SandboxServerReceipt>() : null;
            if (receipt != null && (receipt.status == "succeeded" || receipt.status == "rejected"))
            {
                string commandType = pendingCommandType;
                bool succeeded = receipt.status == "succeeded";
                string message = succeeded ? "后端已确认命令，正在读取最新状态" : receipt.error?.message ?? "后端已拒绝命令";
                AddLog("命令 " + commandType + " · 核对完成 · "
                    + (succeeded ? "执行成功" : "失败 " + (receipt.error?.code ?? "UNKNOWN") + " · " + message), SandboxClientLogCategory.Command, !succeeded);
                pendingBody = null; pendingKey = null; pendingBranchId = null;
                yield return Fetch("GET", "/view", null, null);
                PublishResponse();
                Feedback?.Invoke(message);
                CommandFinished?.Invoke(commandType, succeeded);
                pendingCommandType = null;
            }
            else Status = responseCode == 404 ? "原命令尚无收据；正在自动核对，可按原请求重试" : PublicError();
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1; Changed?.Invoke();
        }

        private IEnumerator LoadSavesRequest(Action<SandboxServerSaveList> completed, int offset)
        {
            Busy = true;
            Changed?.Invoke();
            yield return Fetch("GET", "/saves?offset=" + offset + "&limit=50", null, null);
            SandboxServerSaveList result = responseCode == 200 ? ReadJson<SandboxServerSaveList>() : null;
            if (result == null || result.items == null) { Feedback?.Invoke(PublicError()); result = null; }
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
            completed?.Invoke(result);
            Changed?.Invoke();
        }

        private IEnumerator PreviewRequest(SandboxServerConditionInput condition, Action<SandboxServerPreview, SandboxServerContext> completed)
        {
            Busy = true;
            SandboxServerContext basis = View.context;
            yield return Fetch("POST", "/conditions/preview", JsonUtility.ToJson(new SandboxServerPreviewRequest { context = basis, condition = condition }), null);
            if (responseCode == 200)
            {
                SandboxServerPreview preview = ReadJson<SandboxServerPreview>();
                if (preview != null && preview.valid && !string.IsNullOrEmpty(preview.preview_token)) completed(preview, basis);
                else Feedback?.Invoke("后端预览格式无效，请重新预览");
            }
            else Feedback?.Invoke(PublicError());
            Busy = false; nextPoll = Time.realtimeSinceStartup + 1;
        }

        private IEnumerator Fetch(string method, string path, string body, string requestKey)
        {
            yield return Request(method, endpoint + "/games/" + gameId + path, body, requestKey, bearer);
        }

        /// <summary>统一发送本机接入和已认证对局请求，凭据只进入请求头和当前内存。</summary>
        /// <param name="method">请求方法。</param>
        /// <param name="url">已确定的本机接口地址。</param>
        /// <param name="body">请求正文；读取请求可为空。</param>
        /// <param name="requestKey">命令幂等键；读取请求可为空。</param>
        /// <param name="accessToken">当前席位令牌；同源接入请求可为空。</param>
        /// <returns>等待网络响应的协程。</returns>
        private IEnumerator Request(string method, string url, string body, string requestKey, string accessToken)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            webComplete = false; webSequence++;
            SandboxBackendFetch(gameObject.name, webSequence, url, method, accessToken ?? "", body ?? "", requestKey ?? "");
            float deadline = Time.realtimeSinceStartup + 12;
            while (!webComplete && Time.realtimeSinceStartup < deadline) yield return null;
            if (!webComplete) { responseCode = 0; responseBody = string.Empty; }
#else
            using (var handler = new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false })
            using (var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(10) })
            using (var request = new HttpRequestMessage(new HttpMethod(method), url))
            {
                if (!string.IsNullOrEmpty(accessToken)) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", accessToken);
                else request.Headers.TryAddWithoutValidation("Origin", new Uri(url).GetLeftPart(UriPartial.Authority));
                if (body != null) request.Content = new StringContent(body, Encoding.UTF8, "application/json");
                if (requestKey != null) request.Headers.Add("Idempotency-Key", requestKey);
                var sending = client.SendAsync(request);
                while (!sending.IsCompleted) yield return null;
                if (sending.IsFaulted || sending.IsCanceled) { responseCode = 0; responseBody = string.Empty; yield break; }
                using (HttpResponseMessage response = sending.Result)
                {
                    responseCode = (long)response.StatusCode;
                    var reading = response.Content.ReadAsStringAsync();
                    while (!reading.IsCompleted) yield return null;
                    responseBody = reading.IsFaulted || reading.IsCanceled ? string.Empty : reading.Result;
                    if (reading.IsFaulted || reading.IsCanceled) responseCode = 0;
                }
            }
#endif
        }

        /// <summary>浏览器原生网络回调只接受当前请求序号，忽略晚到的旧响应。</summary>
        [UnityEngine.Scripting.Preserve]
        public void ReceiveBackendResponse(string json)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            SandboxBrowserReply reply = JsonUtility.FromJson<SandboxBrowserReply>(json);
            if (reply == null || reply.sequence != webSequence) return;
            responseCode = reply.code; responseBody = reply.body; webComplete = true;
#endif
        }

        private void PublishResponse()
        {
            if (responseCode != 200)
            {
                Healthy = false; Status = PublicError();
                if (lastConnectionIssue != Status) { lastConnectionIssue = Status; AddLog("状态读取失败 · HTTP " + responseCode + " · " + Status, SandboxClientLogCategory.Connection, true); }
                if (responseCode == 401 || responseCode == 403 || responseCode == 404) View = null;
                if (responseCode == 401 && automaticRole != null && !HasPending)
                { endpoint = null; bearer = null; gameId = null; }
                return;
            }
            SandboxServerView candidate = ReadJson<SandboxServerView>();
            string failure = SandboxServerProjection.Validate(candidate, gameId, mapId, mapHash);
            bool wasHealthy = Healthy;
            Healthy = failure.Length == 0;
            if (!Healthy)
            {
                Status = failure;
                if (lastConnectionIssue != Status) { lastConnectionIssue = Status; AddLog("状态校验失败 · " + Status, SandboxClientLogCategory.Connection, true); }
                return;
            }
            View = candidate;
            Status = HasPending ? "后端已同步；原命令结果仍待核对" : "已连接后端 · PostgreSQL 已持久化";
            lastConnectionIssue = null;
            if (!wasHealthy) AddLog("后端连接正常 · 状态已同步", SandboxClientLogCategory.Connection);
        }

        private string PublicError()
        {
            if (responseCode == 0) return "无法连接后端；保留上次确认状态，写入已停用";
            if (responseCode >= 500) return "后端暂未完成请求，请刷新或核对原命令";
            SandboxServerReceipt result = ReadJson<SandboxServerReceipt>();
            return result?.error?.message ?? "请求未完成，请检查席位、状态与后端服务";
        }

        /// <summary>集中处理格式错误，不将原始响应或私有字段输出到日志。</summary>
        private T ReadJson<T>() where T : class
        {
            try
            {
                T result = JsonUtility.FromJson<T>(responseBody);
                if (result is SandboxServerView view) SandboxServerProjection.NormalizeMissingPlan(view, responseBody);
                return result;
            }
            catch (ArgumentException) { return null; }
        }
    }
}
