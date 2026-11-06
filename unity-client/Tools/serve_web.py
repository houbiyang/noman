"""仅使用 Python 标准库，通过本机 HTTP 服务提供 Unity 网页构建。"""

import argparse
import functools
import hashlib
import http.server
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.web_launch import LocalWebLaunch, WebLaunchError


class SandboxWebHandler(http.server.SimpleHTTPRequestHandler):
    """提供网页文件及 WebAssembly 类型，不列出目录内容。"""

    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".js": "application/javascript",
        ".unityweb": "application/octet-stream",
    }

    def end_headers(self):
        """允许同源的浏览器运行能力，并让启动脚本核对服务目录。"""
        self.send_header("X-Qiongtu-Web", self.server.project_id)
        self.send_header("Cache-Control", "no-store" if self.path == "/api/local-session" else "no-cache")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):
        """不提供文件目录枚举。"""
        self.send_error(404, "Not Found")
        return None

    def do_POST(self):
        """仅允许本机页面同源申请一个席位，不公开引导密钥或其他角色令牌。"""
        if self.path != "/api/local-session":
            self.send_error(404, "Not Found")
            return
        origin = self.headers.get("Origin", "")
        allowed = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
        if origin not in allowed or "http://" + self.headers.get("Host", "") != origin:
            self.reply_json(403, dict(error=dict(message="只允许本机地图页面同源接入")))
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= 1024 or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self.reply_json(400, dict(error=dict(message="接入请求格式无效")))
            return
        try:
            body = json.loads(self.rfile.read(int(length)))
            if not isinstance(body, dict) or set(body) != {"role", "map_hash"}:
                self.reply_json(400, dict(error=dict(message="接入参数无效")))
                return
            result = self.server.launch_provider.session(body["role"], body["map_hash"])
        except (ValueError, OSError, WebLaunchError) as error:
            message = str(error) if isinstance(error, WebLaunchError) else "本机接入未完成，请稍后重试"
            self.reply_json(503, dict(error=dict(message=message)))
            return
        self.reply_json(200, result)

    def reply_json(self, status, value):
        """发送不可缓存的接入结果，不在访问日志记录响应正文。"""
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    """校验构建入口并监听本机端口，工程源码不会作为服务根目录。"""
    parser = argparse.ArgumentParser(description="穹图 Unity 网页本机预览")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--backend-port", type=int, default=18080)
    arguments = parser.parse_args()
    directory = Path(arguments.directory).resolve()
    if not (directory / "index.html").is_file() or not (directory / "Build").is_dir():
        parser.error("找不到完整的 Unity 网页构建，请先执行 start-unity.ps1 -Action BuildWeb")
    if not 1 <= arguments.port <= 65535:
        parser.error("端口必须在 1 至 65535 之间")
    if not 1 <= arguments.backend_port <= 65535:
        parser.error("后端端口必须在 1 至 65535 之间")
    handler = functools.partial(SandboxWebHandler, directory=str(directory))
    with http.server.ThreadingHTTPServer(("127.0.0.1", arguments.port), handler) as server:
        server.project_id = hashlib.sha256(str(directory).encode("utf-8")).hexdigest()
        server.launch_provider = LocalWebLaunch(Path(__file__).resolve().parents[2], arguments.backend_port)
        print(f"穹图网页版已启动：http://127.0.0.1:{arguments.port}/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
