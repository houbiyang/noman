"""本机联调入口：显式创建演示局，把三席位凭据写入受限文件。"""

import argparse
import json
from urllib.request import Request, urlopen
from uuid import uuid4

from backend import database


def main() -> None:
    """仅请求用户指定的本机端口，不在终端输出原始凭据。"""
    parser = argparse.ArgumentParser(description="创建现有功能演示局及私有联调凭据")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--title", default="现有功能后端联调")
    parser.add_argument("--spatial-test", action="store_true", help="显式使用可配置 S2 空间测试规则")
    arguments = parser.parse_args()
    bootstrap = (database.LOCAL_ROOT / "bootstrap-key").read_text(encoding="utf-8-sig").strip()
    base = f"http://127.0.0.1:{arguments.port}/api/v1"

    def post(path: str, body: dict, key: str | None = None) -> dict:
        """请求正文使用 UTF-8，私有响应只写本地受限文件。"""
        headers = {"Content-Type": "application/json", "X-Bootstrap-Key": bootstrap}
        if key:
            headers["Idempotency-Key"] = key
        request = Request(base + path, json.dumps(body, ensure_ascii=False).encode("utf-8"), headers, method="POST")
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    key = str(uuid4())
    destination = database.LOCAL_ROOT / ("demo-session-" + key + ".json")
    record = dict(base_url=base, creation_request_key=key, creation_payload=dict(title=arguments.title, is_demo=True))
    if arguments.spatial_test:
        record["creation_payload"]["spatial_test"] = True
    # 先保留创建键，HTTP 响应丢失时可按原键核对，不能盲目重建。
    database.write_private_json(destination.with_suffix(".request.json"), record)
    game = post("/games", record["creation_payload"], key)
    record.update(game=game, sessions={})
    for role in ("host", "red", "blue"):
        record["sessions"][role] = post(f"/bootstrap/games/{game['game_id']}/sessions", dict(role=role))
    database.write_private_json(destination, record)
    print("演示对局：" + game["game_id"])
    print("私有联调文件：" + str(destination))


if __name__ == "__main__":
    main()
