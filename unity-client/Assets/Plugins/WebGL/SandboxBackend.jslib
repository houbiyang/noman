mergeInto(LibraryManager.library, {
    SandboxBackendFetch: function (receiverPointer, sequence, urlPointer, methodPointer, tokenPointer, bodyPointer, keyPointer) {
        var receiver = UTF8ToString(receiverPointer);
        var url = UTF8ToString(urlPointer);
        var method = UTF8ToString(methodPointer);
        var token = UTF8ToString(tokenPointer);
        var body = UTF8ToString(bodyPointer);
        var key = UTF8ToString(keyPointer);
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, 10000);
        var headers = {};
        if (token) headers["Authorization"] = "Bearer " + token;
        if (body) headers["Content-Type"] = "application/json";
        if (key) headers["Idempotency-Key"] = key;
        // 凭据仅通过请求头发送，不写网址、缓存、日志或站点存储。
        fetch(url, { method: method, headers: headers, body: body || undefined, signal: controller.signal,
            credentials: "omit", cache: "no-store", redirect: "error" })
            .then(function (response) { return response.text().then(function (text) {
                return { sequence: sequence, code: response.status, body: text };
            }); })
            .catch(function () { return { sequence: sequence, code: 0, body: "" }; })
            .then(function (reply) {
                clearTimeout(timer);
                SendMessage(receiver, "ReceiveBackendResponse", JSON.stringify(reply));
            });
    }
});
