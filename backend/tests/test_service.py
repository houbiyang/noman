"""在随机隔离 PostgreSQL 库执行真实 HTTP、权限、并发和恢复验证。"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import unittest
from uuid import UUID, uuid4
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
import psycopg

from backend import database
from backend.main import create_app
from backend.contracts import CommandRequest
from backend.runtime import Settings
from backend.service import SandboxService, SCENARIO_ID, insert


class CurrentFeatureTests(unittest.TestCase):
    """验证现有交互的服务端闭环，不把固定演示当作正式战斗规则。"""

    @classmethod
    def setUpClass(cls):
        """创建本次专用数据库，使用真实应用角色执行所有 HTTP 请求。"""
        cls.database_name = "qiongtu_api_test_" + uuid4().hex
        database.execute("postgres", f"CREATE DATABASE {database.identifier(cls.database_name)} TEMPLATE template0 ENCODING 'UTF8';")
        cls.addClassCleanup(cls.cleanup_database)
        database.apply_migrations(cls.database_name)
        database.configure_privileges(cls.database_name)
        connection = json.loads(database.APPLICATION_CONFIG_PATH.read_text(encoding="utf-8"))
        connection["database"] = cls.database_name
        cls.settings = Settings(connection, "isolated-test-signing-key", "isolated-test-bootstrap-key", False)
        cls.service = SandboxService(cls.settings)
        cls.service.seed_versions()
        cls.client = TestClient(create_app(cls.settings))
        cls.addClassCleanup(cls.client.close)
        cls.bootstrap = {"X-Bootstrap-Key": cls.settings.bootstrap_key}

    @classmethod
    def cleanup_database(cls):
        """只删除由本次随机名称保护的测试数据库。"""
        if not cls.database_name.startswith("qiongtu_api_test_") or len(cls.database_name.rsplit("_", 1)[-1]) != 32:
            raise RuntimeError("测试数据库名称保护失败")
        database.execute("postgres", f"DROP DATABASE {database.identifier(cls.database_name)};")

    def setUp(self):
        """每项测试创建独立对局及三个席位，令牌只保留在内存。"""
        response = self.client.post("/api/v1/games", headers=dict(self.bootstrap, **{"Idempotency-Key": str(uuid4())}),
                                    json=dict(title="后端隔离验证", is_demo=True))
        self.assertEqual(response.status_code, 201, response.text)
        self.game_id = response.json()["game_id"]
        self.path = f"/api/v1/games/{self.game_id}"
        self.headers = {}
        for role in ("host", "red", "blue"):
            response = self.client.post(f"/api/v1/bootstrap/games/{self.game_id}/sessions", headers=self.bootstrap, json=dict(role=role))
            self.assertEqual(response.status_code, 200, response.text)
            self.headers[role] = {"Authorization": "Bearer " + response.json()["access_token"]}

    def view(self, role="host"):
        response = self.client.get(self.path + "/view", headers=self.headers[role])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def command(self, kind, payload=None, role="host", expected=200, body=None, key=None):
        """默认读取最新依据；也允许测试重试原封不动的命令。"""
        request = body or dict(context=self.view(role)["context"], command_type=kind, payload=payload or {})
        response = self.client.post(self.path + "/commands", headers=dict(self.headers[role], **{"Idempotency-Key": key or str(uuid4())}), json=request)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def approve(self, role):
        plan = self.view(role)["decisions"]["own_plan"]
        return self.command("review_proposal", dict(proposal_id=plan["proposal_id"], decision="approve"), role)

    def started(self):
        for role in ("red", "blue"):
            self.command("set_ready", dict(ready=True), role)
        self.command("start_game")
        self.approve("red")
        self.approve("blue")

    def test_01_health_and_creation_idempotency(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["business_table_count"], 20)
        self.assertFalse(response.json()["real_ai"])
        key = str(uuid4())
        headers = dict(self.bootstrap, **{"Idempotency-Key": key})
        body = dict(title="创建重试", is_demo=True)
        first = self.client.post("/api/v1/games", headers=headers, json=body)
        second = self.client.post("/api/v1/games", headers=headers, json=body)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()["game_id"], second.json()["game_id"])
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(self.client.get(f"/api/v1/game-creations/{key}", headers=self.bootstrap).json()["game_id"], first.json()["game_id"])
        self.assertEqual(self.client.post("/api/v1/games", headers=headers, json=dict(body, title="不同输入")).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/games", headers=dict(headers, **{"Idempotency-Key": str(uuid4())}), json=dict(body, is_demo=False)).status_code, 409)

    def test_18_current_map_storage_and_database_guard(self):
        """当前地图单版本初始化幂等，数据库拒绝损坏高程及悬空道路。"""
        from copy import deepcopy
        from backend.map_catalog import current_map
        self.service.seed_versions()
        with self.settings.connect() as connection:
            rows = connection.execute("SELECT * FROM scenario_versions").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0]["id"]), SCENARIO_ID)
            self.assertEqual(rows[0]["definition"]["map"], current_map())
            self.assertEqual(connection.execute("SELECT count(*) AS count FROM pg_indexes WHERE schemaname='sandbox' AND indexname='scenario_map_catalog_idx'").fetchone()["count"], 1)
        for defect in ("grid", "reference", "duplicate"):
            value = deepcopy(rows[0])
            value.pop("created_at")
            value["id"] = uuid4()
            value["version_label"] = str(uuid4())
            geometry = value["definition"]["map"]
            if defect == "grid": geometry["elevation_grid"]["values"].pop()
            elif defect == "reference": geometry["road_edges"][0]["from_node_id"] = "missing"
            else: geometry["road_edges"].append(deepcopy(geometry["road_edges"][0]))
            with self.subTest(defect=defect), self.assertRaises(psycopg.errors.CheckViolation):
                with self.settings.connect() as connection:
                    insert(connection, "scenario_versions", value)

    def test_02_authentication_projection_and_revocation(self):
        self.assertEqual(self.client.get(self.path + "/view").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/games", json=dict(title="未授权", is_demo=True), headers={"Idempotency-Key": str(uuid4())}).status_code, 401)
        red = self.view("red")
        self.assertEqual(len(red["units"]), 3)
        self.assertTrue(all(unit["side"] == "red" for unit in red["units"]))
        self.assertNotIn("controller_session_id", red["clock"])
        self.assertNotIn("own_plan", self.view()["decisions"])
        plan_id = red["decisions"]["own_plan"]["proposal_id"]
        self.assertEqual(self.client.get(self.path + "/proposals/" + plan_id, headers=self.headers["blue"]).status_code, 404)
        self.assertEqual(self.client.get(self.path + "/proposals/" + plan_id, headers=self.headers["host"]).status_code, 404)
        self.assertEqual(self.client.get(self.path + "/view", headers=self.headers["red"]).headers["cache-control"], "no-store")
        self.client.delete("/api/v1/session", headers=self.headers["red"])
        self.assertEqual(self.client.get(self.path + "/view", headers=self.headers["red"]).status_code, 401)

    def test_19_typed_barracks_and_deletion_persist_with_save_restore(self):
        """三类兵营独立保存，重新选址保留单位，删除重试和读档保持一致。"""
        response = self.client.post("/api/v1/games", headers=dict(self.bootstrap, **{"Idempotency-Key": str(uuid4())}),
                                    json=dict(title="兵营页面联调", is_demo=True, spatial_test=True))
        self.assertEqual(response.status_code, 201, response.text)
        self.game_id = response.json()["game_id"]
        self.path = "/api/v1/games/" + self.game_id
        for role in ("host", "red", "blue"):
            seat = self.client.post(f"/api/v1/bootstrap/games/{self.game_id}/sessions", headers=self.bootstrap, json=dict(role=role)).json()
            self.headers[role] = {"Authorization": "Bearer " + seat["access_token"]}
        original_units = self.view("red")["units"]
        locations = {"recon": ("10.3", "26.2"), "ground": ("10.9", "26.2"), "support": ("10.3", "26.7")}
        for kind, (x, y) in locations.items():
            self.command("place_barracks", dict(side="red", unit_type=kind, position=dict(x=x, y=y)), "red")
        placed = self.view("red")
        self.assertEqual(len(placed["camps"]), 3)
        self.assertEqual({camp["unit_type"] for camp in placed["camps"]}, set(locations))
        self.assertEqual([unit["unit_id"] for unit in placed["units"]], [unit["unit_id"] for unit in original_units])
        for unit in placed['units']:
            self.assertTrue(unit['in_barracks'])
            camp=next(camp for camp in placed['camps'] if camp['camp_id']==unit['camp_id'])
            self.assertEqual(unit['position'],camp['position'])
        self.assertTrue(all(unit["camp_id"] for unit in placed["units"]))
        self.assertEqual(self.view("blue")["camps"], [])
        self.command("set_ready", dict(ready=True), "red")
        self.command("place_barracks", dict(side="red", unit_type="recon", position=dict(x="10.3", y="27.2")), "red")
        self.assertFalse(self.view("red")["clock"]["ready"]["red"])
        self.assertEqual(len(self.view("red")["camps"]), 3)
        save_id = self.command("create_save")["result"]["save_id"]
        body = dict(context=self.view("red")["context"], command_type="delete_unit", payload=dict(unit_id="R-U01"))
        key = str(uuid4())
        self.command("delete_unit", role="red", body=body, key=key)
        self.assertTrue(self.command("delete_unit", role="red", body=body, key=key)["replayed"])
        self.assertFalse(any(unit["unit_id"] == "R-U01" for unit in self.view("red")["units"]))
        self.assertEqual(len(self.view("red")["camps"]), 3)
        self.command("restore_save", dict(save_id=save_id))
        self.assertTrue(any(unit["unit_id"] == "R-U01" for unit in self.view("red")["units"]))
        self.assertEqual(len(self.view("red")["camps"]), 3)

    def test_20_barracks_permissions_overlap_and_legacy_demo(self):
        """旧演示局可新增兵营，越权、重叠和删除他方单位均不改变有效头。"""
        payload = dict(side="red", unit_type="ground", position=dict(x="10.3", y="26.2"))
        self.command("place_barracks", payload, "red")
        before = self.view()["context"]
        self.command("place_barracks", dict(payload, side="blue"), "red", expected=403)
        self.command("place_barracks", dict(payload, unit_type="recon"), "red", expected=409)
        self.command("delete_unit", dict(unit_id="B-U01"), "red", expected=404)
        self.assertEqual(self.view()["context"], before)
        self.command("delete_unit", dict(unit_id="R-U01"), "red")
        self.assertFalse(any(unit["unit_id"] == "R-U01" for unit in self.view("red")["units"]))

    def test_03_context_fence_and_duplicate_concurrency(self):
        body = dict(context=self.view("red")["context"], command_type="set_ready", payload=dict(ready=True))
        key = str(uuid4())
        def submit():
            return self.client.post(self.path + "/commands", json=body, headers=dict(self.headers["red"], **{"Idempotency-Key": key}))
        with ThreadPoolExecutor(max_workers=2) as workers:
            responses = list(workers.map(lambda _: submit(), range(2)))
        self.assertEqual([item.status_code for item in responses], [200, 200])
        self.assertEqual(len({item.json()["command_id"] for item in responses}), 1)
        self.assertEqual(sorted(item.json()["replayed"] for item in responses), [False, True])
        self.command("set_ready", role="red", body=body, expected=409)
        altered = dict(body, payload=dict(ready=False))
        self.command("set_ready", role="red", body=altered, key=key, expected=409)
        receipt_id = responses[0].json()["command_id"]
        self.assertEqual(self.client.get(self.path + "/commands/" + receipt_id, headers=self.headers["red"]).status_code, 200)
        self.assertEqual(self.client.get(self.path + "/commands/" + receipt_id, headers=self.headers["blue"]).status_code, 404)

    def test_04_army_deployment_movement_and_atomic_rejection(self):
        quantities = dict(recon=2, ground=3, support=1)
        self.command("configure_army", dict(side="red", quantities=quantities), "red")
        self.assertEqual(len(self.view("red")["units"]), 6)
        before = self.view()["context"]
        rejected = self.command("configure_army", dict(side="blue", quantities=quantities), "red", expected=403)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.view()["context"], before)
        self.command("deploy_army", dict(side="red", unit_type="recon", quantity=3, position=dict(x="119", y="79")), "red", expected=422)
        self.assertEqual(len(self.view("red")["units"]), 6)
        self.command("deploy_army", dict(side="red", unit_type="recon", quantity=2, position=dict(x="23", y="30")), "red")
        self.command("move_unit", dict(unit_id="R-U01", position=dict(x="45.5", y="40")), "red")
        unit = next(unit for unit in self.view("red")["units"] if unit["unit_id"] == "R-U01")
        self.assertEqual(unit["position"]["x"], "45.5")
        self.assertEqual(unit["navigation_status"], "visual_target_only")
        self.command("move_unit", dict(unit_id="B-U01", position=dict(x="20", y="30")), "red", expected=404)
        self.command("move_unit", dict(unit_id="R-U01", position=dict(x="NaN", y="30")), "red", expected=422)
        self.command("configure_army", dict(side="red", quantities=dict(quantities, recon=True)), "red", expected=422)

    def test_04b_authoritative_map_route_query(self):
        response = self.client.get(self.path + "/routes", headers=self.headers["red"],
                                   params=dict(unit_type="ground", from_node_id="N0_0", to_node_id="N0_5"))
        self.assertEqual(response.status_code, 200, response.text)
        route = response.json()
        self.assertEqual(route["schema_version"], "route-view-v1")
        self.assertEqual(route["map_id"], "qinglan-strategic-v6")
        self.assertEqual(route["eligibility"], "needs_verification")
        self.assertEqual(route["node_ids"][0], "N0_0")
        self.assertEqual(route["node_ids"][-1], "N0_5")
        self.assertEqual(len(route["road_ids"]), len(route["node_ids"]) - 1)
        self.assertEqual(self.client.get(self.path + "/routes", headers=self.headers["red"],
                                         params=dict(unit_type="ground", from_node_id="UNKNOWN", to_node_id="N0_5")).status_code, 404)

    def test_deployment_rule_movement_persists_without_advancing_game(self):
        """通过真实接口验证部署试走、权限、幂等、资源隔离与读档恢复。"""
        response = self.client.post("/api/v1/games", headers=dict(self.bootstrap, **{"Idempotency-Key": str(uuid4())}),
                                    json=dict(title="部署试走闭环", is_demo=True, spatial_test=True))
        self.assertEqual(response.status_code, 201, response.text)
        self.game_id = response.json()["game_id"]
        self.path = "/api/v1/games/" + self.game_id
        for role in ("host", "red", "blue"):
            seat = self.client.post(f"/api/v1/bootstrap/games/{self.game_id}/sessions", headers=self.bootstrap, json=dict(role=role)).json()
            self.headers[role] = {"Authorization": "Bearer " + seat["access_token"]}
        self.assertTrue(self.view("red")["capabilities"]["can_test_movement"])
        self.command("movement_order", dict(unit_id="R-U01", mode="move", position=dict(x="35", y="44")), "red", expected=409)
        preparation = dict(side="red", position=dict(x="10.3", y="26.2"), quantities=dict(recon=1, ground=1, support=1))
        preview = self.client.post(self.path + "/preparation/preview", headers=self.headers["red"],
                                   json=dict(context=self.view("red")["context"], preparation=preparation))
        self.assertEqual(preview.status_code, 200, preview.text)
        self.command("set_preparation", dict(preparation, preview_token=preview.json()["preview_token"]), "red")
        self.command("movement_order", dict(unit_id="R-U01", mode="deploy_start", position=dict(x="25", y="44")), "red")
        initial = self.view("red")
        self.command("movement_order", dict(unit_id="R-U01", mode="move", position=dict(x="35", y="44")), "blue", expected=404)
        self.command("movement_order", dict(unit_id="R-U01", mode="move", position=dict(x="55", y="44")), "red")
        self.command("set_ready", dict(ready=True), "red", expected=409)
        body = dict(context=self.view("red")["context"], command_type="advance_deployment_movement", payload=dict(unit_id="R-U01"))
        key = str(uuid4())
        first = self.command("advance_deployment_movement", role="red", body=body, key=key)
        repeated = self.command("advance_deployment_movement", role="red", body=body, key=key)
        self.assertEqual(first["command_id"], repeated["command_id"])
        moved = self.view("red")
        before_unit = next(unit for unit in initial["units"] if unit["unit_id"] == "R-U01")
        moved_unit = next(unit for unit in moved["units"] if unit["unit_id"] == "R-U01")
        self.assertNotEqual(before_unit["position"], moved_unit["position"])
        self.assertEqual(before_unit["energy"], moved_unit["energy"])
        self.assertEqual(initial["clock"], moved["clock"])
        self.assertEqual(moved_unit["movement_test_steps"], 2)
        self.assertLess(moved_unit["movement_test_energy"], moved_unit["energy"])
        self.assertTrue(moved["movement_paths"][0]["points"])
        saved = self.command("create_save", dict(display_name="部署试走恢复"))["result"]["save_id"]
        self.command("advance_deployment_movement", dict(unit_id="R-U01"), "red")
        self.command("restore_save", dict(save_id=saved))
        restored = next(unit for unit in self.view("red")["units"] if unit["unit_id"] == "R-U01")
        self.assertEqual(restored, moved_unit)

    def test_17_spatial_test_preparation_movement_and_restore(self):
        """验证独立测试规则中的完整占地、预览、逐步移动及资源恢复。"""
        response = self.client.post("/api/v1/games", headers=dict(self.bootstrap, **{"Idempotency-Key": str(uuid4())}),
                                    json=dict(title="S2测试闭环", is_demo=True, spatial_test=True))
        self.assertEqual(response.status_code, 201, response.text)
        self.game_id = response.json()["game_id"]
        self.path = "/api/v1/games/" + self.game_id
        for role in ("host", "red", "blue"):
            seat = self.client.post(f"/api/v1/bootstrap/games/{self.game_id}/sessions", headers=self.bootstrap, json=dict(role=role)).json()
            self.headers[role] = {"Authorization": "Bearer " + seat["access_token"]}
        for role in ("host", "red", "blue"):
            self.assertFalse(self.view(role)["capabilities"]["can_move_unit"])
        before = self.view("red")["context"]
        self.command("move_unit", dict(unit_id="R-G01", position=dict(x="12", y="30")), "red", expected=409)
        self.assertEqual(self.view("red")["context"], before)
        self.command("set_ready", dict(ready=True), "red", expected=409)
        for side, coordinates in (("red", ("10.3", "26.2")), ("blue", ("100.3", "50.2"))):
            payload = dict(side=side, position=dict(x=coordinates[0], y=coordinates[1]), quantities=dict(recon=1, ground=1, support=1))
            context = self.view(side)["context"]
            preview = self.client.post(self.path + "/preparation/preview", headers=self.headers[side], json=dict(context=context, preparation=payload))
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertTrue(preview.json()["camp"]["deployment_validated"])
            self.command("set_preparation", dict(payload, quantities=dict(recon=2, ground=1, support=1), preview_token=preview.json()["preview_token"]), side, expected=409)
            self.command("set_preparation", dict(payload, preview_token=preview.json()["preview_token"]), side)
        bad = dict(side="red", position=dict(x="10", y="26"), quantities=dict(recon=1, ground=1, support=1))
        before = self.view()["context"]
        invalid = self.client.post(self.path + "/preparation/preview", headers=self.headers["red"], json=dict(context=self.view("red")["context"], preparation=bad))
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(invalid.json()["error"]["code"], "CAMP_FOOTPRINT_OUTSIDE")
        self.assertEqual(self.view()["context"], before)
        for role in ("red", "blue"):
            self.command("set_ready", dict(ready=True), role)
        self.command("start_game")
        self.assertFalse(self.view("red")["capabilities"]["can_move_unit"])
        self.command("move_unit", dict(unit_id="R-G01", position=dict(x="12", y="30")), "red", expected=409)
        self.approve("red")
        self.assertFalse(self.view("red")["capabilities"]["can_move_unit"])
        self.approve("blue")
        self.assertTrue(self.view("red")["capabilities"]["can_move_unit"])
        self.command("configure_army", dict(side="red", quantities=dict(recon=2, ground=1, support=1)), "red", expected=409)
        initial_position = next(unit for unit in self.view("red")["units"] if unit["unit_id"] == "R-G01")["position"]
        self.command("move_unit", dict(unit_id="R-G01", position=dict(x="12", y="30")), "red")
        self.assertEqual(next(unit for unit in self.view("red")["units"] if unit["unit_id"] == "R-G01")["position"], initial_position)
        self.command("move_unit", dict(unit_id="B-G01", position=dict(x="12", y="30")), "red", expected=404)
        saved = self.command("create_save", dict(display_name="S2路线资源"))["result"]["save_id"]
        initial = next(unit for unit in self.view("red")["units"] if unit["unit_id"] == "R-G01")
        self.command("advance_step")
        moved = next(unit for unit in self.view("red")["units"] if unit["unit_id"] == "R-G01")
        self.assertNotEqual(initial["position"], moved["position"])
        self.assertLess(moved["energy"], initial["energy"])
        self.assertEqual(moved["supply"], initial["supply"])
        self.command("restore_save", dict(save_id=saved))
        restored = next(unit for unit in self.view("red")["units"] if unit["unit_id"] == "R-G01")
        self.assertEqual(restored, initial)
        self.assertEqual(self.view("red")["routes"][0]["status"], "queued")
        self.command("advance_step")
        self.assertEqual(next(unit for unit in self.view("red")["units"] if unit["unit_id"] == "R-G01"), moved)

    def test_05_proposals_tasks_and_readiness(self):
        self.command("start_game", expected=409)
        self.command("set_tasks", dict(task_type="escort"), role="red", expected=403)
        self.command("set_tasks", dict(task_type="escort"))
        self.assertEqual(self.view("red")["tasks"][0]["task_type"], "escort")
        self.command("set_ready", dict(ready=True), "red")
        self.command("set_ready", dict(ready=True), "blue")
        self.command("start_game")
        self.assertEqual(self.view()["clock"]["phase"], "awaiting_confirmation")
        self.command("advance_step", expected=409)
        plan = self.view("red")["decisions"]["own_plan"]
        self.command("review_proposal", dict(proposal_id=plan["proposal_id"], decision="reject"), "red")
        self.command("reload_demo_plan", role="red")
        plan = self.view("red")["decisions"]["own_plan"]
        self.command("review_proposal", dict(proposal_id=plan["proposal_id"], decision="modify_approve", changes=dict(priority="优先观察桥梁", resource_limit=4)), "red")
        self.approve("blue")
        self.assertEqual(self.view()["clock"]["phase"], "paused")
        self.assertEqual(self.view("red")["decisions"]["own_plan"]["priority"], "优先观察桥梁")
        self.command("advance_step")
        self.assertEqual(self.view()["clock"]["simulation_step"], 1)

    def test_06_event_preview_binding_and_private_timeline(self):
        condition = dict(condition_type="equipment_fault", target_unit_id="R-G01")
        context = self.view()["context"]
        preview = self.client.post(self.path + "/conditions/preview", headers=self.headers["host"], json=dict(context=context, condition=condition))
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.json()["effect_applied"])
        self.command("trigger_condition", dict(condition=condition, preview_token=preview.json()["preview_token"]))
        red_events = self.client.get(self.path + "/events", headers=self.headers["red"]).json()["items"]
        blue_events = self.client.get(self.path + "/events", headers=self.headers["blue"]).json()["items"]
        self.assertTrue(any("设备故障" in event["title"] for event in red_events))
        self.assertFalse(any("设备故障" in event["title"] for event in blue_events))
        self.assertEqual(len(self.view("red")["conditions"]), 1)
        self.assertEqual(self.view("blue")["conditions"], [])
        self.assertTrue(any("设备故障" in event["title"] for event in self.view("red")["timeline"]))
        self.assertFalse(any("设备故障" in event["title"] for event in self.view("blue")["timeline"]))
        self.command("trigger_condition", dict(condition=condition, preview_token=preview.json()["preview_token"]), expected=409)
        self.approve("red")
        host_events = self.client.get(self.path + "/events", headers=self.headers["host"]).json()["items"]
        self.assertFalse(any("建议已处理" in event["title"] for event in host_events))

    def test_07_save_restore_new_branch_and_restart(self):
        old_context = self.view()["context"]
        saved = self.command("create_save", dict(display_name="独立存档"))["result"]["save_id"]
        self.command("move_unit", dict(unit_id="R-G01", position=dict(x="60", y="40")))
        body = dict(context=self.view()["context"], command_type="restore_save", payload=dict(save_id=saved))
        key = str(uuid4())
        restored = self.command("restore_save", body=body, key=key)
        self.assertNotEqual(restored["result"]["branch_id"], old_context["branch_id"])
        self.assertEqual(self.view()["clock"]["phase"], "paused")
        self.assertTrue(self.command("restore_save", body=body, key=key)["replayed"])
        new_service = SandboxService(self.settings)
        token = self.headers["host"]["Authorization"][7:]
        self.assertEqual(new_service.view(self.game_id, token)["context"], self.view()["context"])
        ground = next(unit for unit in self.view()["units"] if unit["unit_id"] == "R-G01")
        self.assertEqual(ground["position"]["x"], "30")
        events = self.client.get(self.path + "/events", headers=self.headers["host"]).json()["items"]
        self.assertFalse(any("机动" in event["title"] for event in events))
        self.approve("red")
        self.approve("blue")

    def test_08_replay_cursor_and_cross_game_fences(self):
        for ready in (True, False, True):
            self.command("set_ready", dict(ready=ready), "red")
        first = self.client.get(self.path + "/events?limit=1", headers=self.headers["red"]).json()
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(self.client.get(self.path + "/events", params=dict(cursor=first["next_cursor"]), headers=self.headers["blue"]).status_code, 409)
        self.command("set_ready", dict(ready=False), "red")
        second = self.client.get(self.path + "/events", params=dict(cursor=first["next_cursor"]), headers=self.headers["red"]).json()
        self.assertEqual(len(second["items"]), 2)
        before = self.view()["context"]
        frame = self.client.get(self.path + "/replay?simulation_step=0", headers=self.headers["host"])
        self.assertEqual(frame.status_code, 200)
        self.assertTrue(frame.json()["read_only"])
        self.assertIsNone(frame.json()["context"])
        self.assertEqual(self.view()["context"], before)
        self.assertEqual(self.client.get(self.path + "/replay", headers=self.headers["red"]).status_code, 403)
        other_id = self.client.post("/api/v1/games", headers=dict(self.bootstrap, **{"Idempotency-Key": str(uuid4())}), json=dict(title="另一局", is_demo=True)).json()["game_id"]
        self.assertEqual(self.client.get(f"/api/v1/games/{other_id}/view", headers=self.headers["red"]).status_code, 404)
        self.assertEqual(len(self.client.get("/api/v1/games", headers=self.headers["red"]).json()["items"]), 1)

    def test_09_scheduler_playback_and_revoked_controller_pause(self):
        self.started()
        self.command("continue")
        self.assertEqual(self.service.tick(), 1)
        self.assertEqual(self.view()["clock"]["simulation_step"], 1)
        self.client.delete("/api/v1/session", headers=self.headers["host"])
        self.assertEqual(self.service.tick(), 0)
        self.assertEqual(self.view("red")["clock"]["phase"], "paused")

    def test_10_deadline_result_and_read_only_final(self):
        self.started()
        self.command("continue")
        for _ in range(60):
            self.assertEqual(self.service.tick(), 1)
        self.assertEqual(self.service.tick(), 0)
        self.assertEqual(self.view()["clock"]["phase"], "finished")
        result = self.client.get(self.path + "/result", headers=self.headers["red"])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["result_status"], "not_adjudicated")
        self.assertIsNone(result.json()["winner"])
        self.assertEqual(self.client.get(self.path + "/replay", headers=self.headers["red"]).status_code, 200)
        self.command("advance_step", expected=409)
        saves = self.client.get(self.path + "/saves", headers=self.headers["host"]).json()["items"]
        self.assertEqual(sum(save["save_kind"] == "final" for save in saves), 1)

    def test_11_exit_and_inactive_snapshot_receipts(self):
        self.started()
        self.command("continue")
        self.command("complete_exit", dict(choice="save_and_exit"), expected=409)
        self.command("begin_exit")
        receipt = self.command("complete_exit", dict(choice="save_and_exit", display_name="退出存档"))
        self.assertIsNotNone(receipt["result"]["save_id"])
        self.assertEqual(self.view()["clock"]["phase"], "paused")
        self.assertEqual(self.service.tick(), 0)

    def test_12_database_failure_rolls_back_and_retry_commits_once(self):
        before = self.view()["context"]
        body = dict(context=self.view("red")["context"], command_type="set_ready", payload=dict(ready=True))
        key = str(uuid4())
        original = SandboxService._new_proposals

        def unavailable(*arguments, **keywords):
            original(*arguments, **keywords)
            raise psycopg.OperationalError("模拟落库中途断连，不应出现在 HTTP 响应")

        with patch.object(SandboxService, "_new_proposals", unavailable):
            response = self.client.post(self.path + "/commands", json=body,
                headers=dict(self.headers["red"], **{"Idempotency-Key": key}))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("模拟", response.text)
        self.assertEqual(self.view()["context"], before)
        self.assertFalse(self.view()["clock"]["ready"]["red"])
        receipt = self.command("set_ready", role="red", body=body, key=key)
        self.assertFalse(receipt["replayed"])
        self.assertTrue(self.command("set_ready", role="red", body=body, key=key)["replayed"])

    def test_13_restore_adopted_plans_and_stale_preview(self):
        self.started()
        save_id = self.command("create_save")["result"]["save_id"]
        condition = dict(condition_type="weather_change", target_unit_id="R-G01")
        preview = self.client.post(self.path + "/conditions/preview", headers=self.headers["host"],
            json=dict(context=self.view()["context"], condition=condition)).json()
        self.command("advance_step")
        self.command("trigger_condition", dict(condition=condition, preview_token=preview["preview_token"]), expected=409)
        self.command("restore_save", dict(save_id=save_id))
        self.assertEqual(self.view()["clock"]["simulation_step"], 0)
        self.assertEqual(self.view("red")["decisions"]["own_plan"]["status"], "adopted")
        self.command("advance_step")
        self.assertEqual(self.view()["clock"]["simulation_step"], 1)

    def test_14_own_input_changes_require_new_proposal(self):
        proposal_id = self.view("red")["decisions"]["own_plan"]["proposal_id"]
        self.command("move_unit", dict(unit_id="R-U01", position=dict(x="25.1", y="44")), "red")
        rejected = self.command("review_proposal", dict(proposal_id=proposal_id, decision="approve"), "red", expected=409)
        self.assertEqual(rejected["error"]["code"], "PROPOSAL_STALE")
        self.command("reload_demo_plan", role="red")
        self.approve("red")
        self.command("move_unit", dict(unit_id="R-U01", position=dict(x="1e-999999999", y="44")), "red", expected=422)

    def test_15_parallel_different_commands_cannot_overwrite_new_head(self):
        context = self.view("red")["context"]

        def submit(ready):
            body = dict(context=context, command_type="set_ready", payload=dict(ready=ready))
            return self.client.post(self.path + "/commands", json=body,
                headers=dict(self.headers["red"], **{"Idempotency-Key": str(uuid4())})).status_code

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(submit, (True, False)))
        self.assertEqual(sorted(results), [200, 409])

    def test_16_map_view_keeps_one_head_during_concurrent_commit(self):
        before = self.view()
        original = SandboxService._project
        submitted = False
        pending = CommandRequest(context=before["context"], command_type="move_unit",
            payload=dict(unit_id="R-G01", position=dict(x="35", y="40")))

        def interleave(instance, game, principal, state):
            nonlocal submitted
            result = original(instance, game, principal, state)
            if not submitted:
                submitted = True
                instance.command(self.game_id, self.headers["host"]["Authorization"][7:], uuid4(), pending)
            return result

        with patch.object(SandboxService, "_project", interleave):
            observed = self.view()
        self.assertEqual(observed["context"], before["context"])
        self.assertEqual(observed["units"], before["units"])
        self.assertEqual(observed["timeline"], before["timeline"])
        self.assertNotEqual(self.view()["context"]["snapshot_id"], observed["context"]["snapshot_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
