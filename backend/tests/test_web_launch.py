"""验证自动接入的创建幂等、席位隔离和过期恢复，不连接业务数据库。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from backend.web_launch import LocalWebLaunch, WebLaunchError


class WebLaunchTests(TestCase):
    """使用隔离私有目录和受控接口替身检查接入流程。"""

    def setUp(self):
        """准备最小地图身份和独立记录目录，不读取项目凭据。"""
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        resources = self.root / "unity-client/Assets/Sandbox/Resources/Maps/QinglanValley"
        resources.mkdir(parents=True)
        self.map_hash = "1" * 64
        (resources / "map-definition.json").write_text(json.dumps(dict(contentHash=self.map_hash)), encoding="utf-8")
        self.local = self.root / "private"
        self.local.mkdir()
        self.patch_local = patch("backend.web_launch.database.LOCAL_ROOT", self.local)
        self.patch_local.start()
        self.addCleanup(self.patch_local.stop)
        self.patch_write = patch("backend.web_launch.database.write_private_json",
                                 side_effect=lambda path, value: path.write_text(json.dumps(value), encoding="utf-8"))
        self.patch_write.start()
        self.addCleanup(self.patch_write.stop)
        self.provider = LocalWebLaunch(self.root)
        self.calls = []
        self.fail_creation = False
        self.expired = False
        self.issued = 0

        def request(method, path, body=None, **options):
            """记录接口顺序，模拟丢失创建响应和单席位会话。"""
            self.calls.append((method, path, options))
            if path == "/games":
                if self.fail_creation:
                    self.fail_creation = False
                    raise URLError("模拟响应丢失")
                return dict(game_id="test-game")
            if path.endswith("/sessions"):
                self.issued += 1
                return dict(game_id="test-game", role=body["role"], access_token=f"test-{body['role']}-{self.issued}")
            if path == "/session":
                if self.expired:
                    self.expired = False
                    raise HTTPError("http://127.0.0.1/session", 401, "expired", {}, None)
                return dict(game_id="test-game", role=options["token"].split("-")[1])
            raise AssertionError("出现未声明的接口")

        self.provider.request = request

    def test_refresh_reuses_game_and_selected_session(self):
        """重复打开复用同一对局及同一有效席位。"""
        first = self.provider.session("red", self.map_hash)
        second = self.provider.session("red", self.map_hash)
        self.assertEqual(first, second)
        self.assertEqual(sum(path == "/games" for _, path, _ in self.calls), 1)
        self.assertEqual(self.issued, 1)

    def test_roles_share_game_without_returning_other_tokens(self):
        """三个角色共用对局，但每次响应只包含所选角色凭据。"""
        results = [self.provider.session(role, self.map_hash) for role in ("red", "blue", "host")]
        self.assertEqual({result["game_id"] for result in results}, {"test-game"})
        self.assertEqual([result["role"] for result in results], ["red", "blue", "host"])
        self.assertTrue(all(set(result) == {"api_url", "game_id", "role", "access_token"} for result in results))
        self.assertEqual(sum(path == "/games" for _, path, _ in self.calls), 1)

    def test_uncertain_creation_reuses_original_key(self):
        """创建响应丢失后沿用原请求键，不产生新的创建意图。"""
        self.fail_creation = True
        with self.assertRaises(WebLaunchError):
            self.provider.session("red", self.map_hash)
        self.provider.session("red", self.map_hash)
        creation_keys = [options["request_key"] for _, path, options in self.calls if path == "/games"]
        self.assertEqual(len(creation_keys), 2)
        self.assertEqual(creation_keys[0], creation_keys[1])

    def test_expired_session_is_reissued_for_same_role(self):
        """过期仅替换当前角色会话，保留原对局。"""
        first = self.provider.session("blue", self.map_hash)
        self.expired = True
        second = self.provider.session("blue", self.map_hash)
        self.assertEqual(first["game_id"], second["game_id"])
        self.assertEqual(second["role"], "blue")
        self.assertNotEqual(first["access_token"], second["access_token"])

    def test_invalid_role_and_map_do_not_create_game(self):
        """无效角色或旧地图在发起任何创建请求之前被拒绝。"""
        for role, map_hash in (("admin", self.map_hash), ("red", "2" * 64)):
            with self.assertRaises(WebLaunchError):
                self.provider.session(role, map_hash)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    main()
