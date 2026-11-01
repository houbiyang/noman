"""仅使用 Python 标准库，通过本机 HTTP 服务提供 Unity 网页构建。"""

import argparse
import functools
import hashlib
import http.server
from pathlib import Path


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
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):
        """不提供文件目录枚举。"""
        self.send_error(404, "Not Found")
        return None


def main():
    """校验构建入口并监听本机端口，工程源码不会作为服务根目录。"""
    parser = argparse.ArgumentParser(description="穹图 Unity 网页本机预览")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--port", type=int, default=18766)
    arguments = parser.parse_args()
    directory = Path(arguments.directory).resolve()
    if not (directory / "index.html").is_file() or not (directory / "Build").is_dir():
        parser.error("找不到完整的 Unity 网页构建，请先执行 start-unity.ps1 -Action BuildWeb")
    if not 1 <= arguments.port <= 65535:
        parser.error("端口必须在 1 至 65535 之间")
    handler = functools.partial(SandboxWebHandler, directory=str(directory))
    with http.server.ThreadingHTTPServer(("127.0.0.1", arguments.port), handler) as server:
        server.project_id = hashlib.sha256(str(directory).encode("utf-8")).hexdigest()
        print(f"穹图网页版已启动：http://127.0.0.1:{arguments.port}/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
