"""为本机地图页面准备一个可复用对局，每次只返回所选席位的会话。"""

import hashlib
import json
import os
import re
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from backend.manual_web_launch import ManualWebLaunch

from backend import database


class WebLaunchError(RuntimeError):
    """只携带可公开的启动失败信息，不包含凭据或上游原始响应。"""


class LocalRedirectBlocker(HTTPRedirectHandler):
    """禁止本机接口将携带凭据的请求重定向到其他地址。"""

    def redirect_request(self, request, response, code, message, headers, new_url):
        """拒绝跟随任何重定向，由调用方按接入失败处理。"""
        return None


class LocalWebLaunch(ManualWebLaunch):
    """通过已有受控接口创建对局、复用会话，并将引导密钥留在服务端。"""

    def __init__(self, project_root: Path, port: int = 18080, *, launch_namespace=None):
        """绑定本项目、本机后端端口及线程互斥，拒绝由页面指定任意接口。"""
        if launch_namespace is not None and (not isinstance(launch_namespace, str)
                or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", launch_namespace) is None):
            raise ValueError("invalid local launch namespace")
        self.launch_namespace = launch_namespace
        self.project_root = project_root
        self.base_url = f"http://127.0.0.1:{port}/api/v1"
        self.lock = Lock()

    def state_path(self, name):
        """Keep independent local pages' game pointers apart; credentials stay private."""
        if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
            raise ValueError("invalid local launch state filename")
        prefix = "" if self.launch_namespace is None else f"web-ns-{self.launch_namespace}-"
        return database.LOCAL_ROOT / (prefix + name)

    def request(self, method: str, path: str, body=None, *, token=None, bootstrap=False, request_key=None):
        """调用已有权限接口，网络失败时不重建对局、不输出私有响应。"""
        headers = {"Content-Type": "application/json"}
        if bootstrap:
            headers["X-Bootstrap-Key"] = (database.LOCAL_ROOT / "bootstrap-key").read_text(encoding="utf-8-sig").strip()
        if token:
            headers["Authorization"] = "Bearer " + token
        if request_key:
            headers["Idempotency-Key"] = request_key
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + path, payload, headers, method=method)
        opener = build_opener(ProxyHandler({}), LocalRedirectBlocker())
        with opener.open(request, timeout=8) as response:
            return json.load(response)

    def save(self, path: Path, record: dict):
        """先保护临时文件，再原子替换记录，避免半写入和凭据进入静态目录。"""
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        database.write_private_json(temporary, record)
        os.replace(temporary, path)

    def session(self, role: str, map_hash: str, capture_layout_sha256: str | None = None) -> dict:
        """为当前地图复用同一对局，仅签发或复用请求角色自身的会话。"""
        if role not in ("red", "blue", "host"):
            raise WebLaunchError("请选择有效角色")
        definition_path = self.project_root / "unity-client/Assets/Sandbox/Resources/Maps/QinglanValley/map-definition.json"
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
        if map_hash != definition["contentHash"]:
            raise WebLaunchError("地图版本已变化，请刷新最新地图页面")
        try:
            with self.lock:
                layout_path = definition_path.with_name("capture-points.json")
                layout_hash = hashlib.sha256(layout_path.read_bytes()).hexdigest() if layout_path.exists() else "legacy"
                if layout_path.exists() and capture_layout_sha256 != layout_hash:
                    raise WebLaunchError("据点版本已变化，请刷新最新地图页面")
                path = self.state_path("web-launch-qinglan-control-v2-" + map_hash + "-" + layout_hash + ".json")
                active = self.manual_active_path(map_hash, layout_hash)
                if active.exists():
                    path = active
                if path.exists():
                    record = json.loads(path.read_text(encoding="utf-8"))
                else:
                    record = dict(map_hash=map_hash, capture_layout_sha256=layout_hash, creation_request_key=str(uuid4()),
                                  creation_payload=dict(title="青岚五据点驻守演示", is_demo=True, spatial_test=True, unit_interactions=True, victory_mode="qinglan_control"), sessions={})
                    self.save(path, record)
                if not record.get("game_id"):
                    game = self.request("POST", "/games", record["creation_payload"], bootstrap=True,
                                        request_key=record["creation_request_key"])
                    record["game_id"] = game["game_id"]
                    self.save(path, record)
                return self.launch_seat(path, record, role)
        except (OSError, URLError, ValueError, KeyError, TypeError) as error:
            raise WebLaunchError("本机后端暂不可用，请等待服务恢复后重试") from error

    def launch_seat(self, path, record, role):
        """只返回所选席位；每次重新确认已保存会话仍有效。调用方持有锁。"""
        session = record["sessions"].get(role)
        if session:
            try:
                current = self.request("GET", "/session", token=session["access_token"])
                if current["game_id"] != record["game_id"] or current["role"] != role:
                    raise WebLaunchError("已保存会话与请求角色不一致")
            except HTTPError as error:
                if error.code not in (401, 403):
                    raise
                session = None
        if session is None:
            session = self.request("POST", f"/bootstrap/games/{record['game_id']}/sessions",
                                   dict(role=role), bootstrap=True)
            if session.get("game_id") != record["game_id"] or session.get("role") != role:
                raise WebLaunchError("新会话与请求对局或角色不一致")
            record["sessions"][role] = session
            self.save(path, record)
        return dict(api_url=self.base_url, game_id=record["game_id"], role=role,
                    access_token=session["access_token"])
