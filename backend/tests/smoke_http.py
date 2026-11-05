"""对明确指定的私有演示席位文件执行真实网络联调，不输出令牌。"""

import argparse
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import database


def main() -> None:
    """操作调用者指定的演示局，完成后保持暂停并记录脱敏证据。"""
    parser = argparse.ArgumentParser(description="验证本机现有功能后端 HTTP")
    parser.add_argument("session_file", type=Path)
    arguments = parser.parse_args()
    private = json.loads(arguments.session_file.read_text(encoding="utf-8"))
    base = private["base_url"]
    if not base.startswith("http://127.0.0.1:"):
        raise ValueError("仅允许本机联调地址")
    game_id = private["game"]["game_id"]
    game_path = "/games/" + game_id
    checks = []

    def request(path, role="host", body=None, key=None):
        """只在请求头使用席位凭据。"""
        headers = dict(Authorization="Bearer " + private["sessions"][role]["access_token"])
        if key:
            headers["Idempotency-Key"] = key
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        with urlopen(Request(base + path, data, headers), timeout=20) as response:
            assert response.headers["Cache-Control"] == "no-store"
            return json.load(response)

    def view(role="host"):
        return request(game_path + "/view", role)

    def command(kind, payload=None, role="host"):
        body = dict(context=view(role)["context"], command_type=kind, payload=payload or {})
        return request(game_path + "/commands", role, body, str(uuid4()))

    assert view()["game"]["is_demo"]
    assert view()["clock"]["phase"] == "preparing", "请使用新建演示局运行网络验收"
    command("configure_army", dict(side="red", quantities=dict(recon=2, ground=2, support=1)), "red")
    assert len(view("red")["units"]) == 5
    assert all(unit["side"] == "red" for unit in view("red")["units"])
    checks.append("军队配置与角色投影")
    for role in ("red", "blue"):
        command("set_ready", dict(ready=True), role)
    command("start_game")
    for role in ("red", "blue"):
        plan = view(role)["decisions"]["own_plan"]
        command("review_proposal", dict(proposal_id=plan["proposal_id"], decision="approve"), role)
    checks.append("三席位准备、开局及双方确认")
    save_id = command("create_save", dict(display_name="HTTP 验收存档"))["result"]["save_id"]
    command("continue")
    deadline = time.monotonic() + 8
    while view()["clock"]["simulation_step"] < 1 and time.monotonic() < deadline:
        time.sleep(0.2)
    assert view()["clock"]["simulation_step"] >= 1
    command("pause")
    checks.append("真实进程后台播放与暂停")
    command("move_unit", dict(unit_id="R-G01", position=dict(x="50", y="40")), "red")
    checks.append("演示机动目标持久化")
    condition = dict(condition_type="weather_change", target_unit_id="R-G01")
    preview = request(game_path + "/conditions/preview", body=dict(context=view()["context"], condition=condition))
    command("trigger_condition", dict(condition=condition, preview_token=preview["preview_token"]))
    assert any("天气变化" in event["title"] for event in request(game_path + "/events", "blue")["items"])
    checks.append("事件预览、登记与公开时间线")
    old_branch = view()["context"]["branch_id"]
    command("restore_save", dict(save_id=save_id))
    assert view()["context"]["branch_id"] != old_branch
    assert view()["clock"]["simulation_step"] == 0
    assert view()["clock"]["phase"] == "paused"
    assert not any("天气变化" in event["title"] for event in request(game_path + "/events")["items"])
    checks.append("读档新分支及历史前缀隔离")
    context = view()["context"]
    frame = request(game_path + "/replay?simulation_step=0")
    assert frame["read_only"] and frame["context"] is None
    assert view()["context"] == context
    checks.append("复盘只读、不改变有效头")
    command("begin_exit")
    command("complete_exit", dict(choice="save_and_exit", display_name="HTTP 验收退出"))
    checks.append("保存后退出并保持暂停")
    report = dict(game_id=game_id, checks=checks, passed=True, final_context=view()["context"],
                  final_phase=view()["clock"]["phase"], unity_connected=False, formal_combat=False)
    database.write_private_json(database.LOCAL_ROOT / ("backend-http-smoke-" + str(uuid4()) + ".json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
