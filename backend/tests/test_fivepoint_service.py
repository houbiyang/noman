"""隔离 PostgreSQL 与真实 HTTP 的五点接入验收；几何替身仅用于事务和权限测试。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.demo_domain import initial_state, barracks_id
from backend import fivepoint_service
from backend.spatial import business
from backend.tests import test_service as legacy_service_tests
from backend.tests.test_fivepoint_engine import FixtureGeometry


def configured_state(*arguments):
    """建立测试专用完整三营名册，不写生产地图或现有对局。"""
    state = initial_state(*arguments)
    state["camps"] = [dict(camp_id=barracks_id(side, kind), side=side, unit_type=kind,
                           position=business((1000 if side == "red" else 11000, 1000)), deployment_validated=True)
                      for side in ("red", "blue") for kind in ("recon", "ground", "support")]
    for unit in state["units"]:
        unit["position"] = business((100 if unit["side"] == "red" else 600, 100))
    return state


def binding(_state):
    """替换地图发布入口，不把人工测试场景标为真实青岚地图通过。"""
    return dict(map_hash="fixture-only", capacity_per_side=60, deployment_layer="ground",
                points=[dict(point_id=f"point-{index}", xy=[100 + index * 2000, 100], layer="ground") for index in range(5)]), FixtureGeometry()


class FivepointServiceTests(unittest.TestCase):
    """复用既有测试库生命周期，不继承或重复执行整套旧演示测试。"""

    setUpClass = classmethod(legacy_service_tests.CurrentFeatureTests.setUpClass.__func__)
    cleanup_database = classmethod(legacy_service_tests.CurrentFeatureTests.cleanup_database.__func__)
    command = legacy_service_tests.CurrentFeatureTests.command
    view = legacy_service_tests.CurrentFeatureTests.view

    def setUp(self):
        """只在当前测试生命周期注入几何；所有 HTTP 写入仍经过实际事务和身份校验。"""
        self.initial_patch = patch("backend.service.initial_state", side_effect=configured_state)
        self.initial_patch.start()
        self.addCleanup(self.initial_patch.stop)
        self.geometry_patch = patch.object(fivepoint_service, "published_map", side_effect=binding)
        self.geometry_patch.start()
        self.addCleanup(self.geometry_patch.stop)
        legacy_service_tests.CurrentFeatureTests.setUp(self)
        for side in ("red", "blue"):
            self.command("set_ready", dict(ready=True), side)

    def enable(self):
        """显式从准备阶段启用新版本，再由双方独立采用空方案。"""
        self.command("enable_fivepoint")
        self.command("submit_fivepoint_plan", dict(expected_step=0, decision="reject"), "red")
        self.command("submit_fivepoint_plan", dict(expected_step=0, orders={}), "blue")

    def test_real_map_gate_rejects_without_mutating_game(self):
        self.geometry_patch.stop()
        before = self.view()
        response = self.command("enable_fivepoint", expected=409)
        self.assertEqual(response["error"]["code"], "FIVEPOINT_MAP_NOT_RELEASED")
        self.assertEqual(self.view()["context"], before["context"])

    def test_common_barrier_permissions_and_legacy_commands(self):
        self.command("enable_fivepoint", role="red", expected=403)
        self.command("enable_fivepoint")
        self.command("submit_fivepoint_plan", dict(expected_step=0, orders={}), expected=403)
        self.command("submit_fivepoint_plan", dict(expected_step=0, orders={}), "red")
        self.command("advance_step", expected=409)
        self.command("submit_fivepoint_plan", dict(expected_step=0, orders={}), "blue")
        self.command("advance_step")
        self.assertEqual(self.view()["clock"]["elapsed_seconds"], 5)
        self.command("delete_unit", dict(unit_id="R-G01"), expected=409)
        self.assertEqual(self.view("red")["units"][1]["attributes"]["damage"], 16)

    def test_step_duplicate_and_restore_branch_isolation(self):
        self.enable()
        saved = self.command("create_save")["result"]["save_id"]
        request = dict(context=self.view()["context"], command_type="advance_step", payload={})
        key = str(uuid4())
        first = self.command("advance_step", body=request, key=key)
        repeated = self.command("advance_step", body=request, key=key)
        self.assertEqual(first["command_id"], repeated["command_id"])
        self.assertTrue(repeated["replayed"])
        old_context = deepcopy(self.view()["context"])
        self.command("restore_save", dict(save_id=saved))
        self.assertEqual(self.view()["clock"]["simulation_step"], 0)
        self.assertNotEqual(self.view()["context"]["branch_id"], old_context["branch_id"])
        self.command("advance_step", body=dict(context=old_context, command_type="advance_step", payload={}), expected=409)
        self.command("advance_step")
        self.assertEqual(self.view()["clock"]["simulation_step"], 1)

    def test_sixty_first_step_and_replay_persist(self):
        self.enable()
        for index in range(61):
            if index and index % 12 == 0:
                for side in ("red", "blue"):
                    self.command("submit_fivepoint_plan", dict(expected_step=index, orders={}), side)
            self.command("advance_step")
        self.assertEqual(self.view()["clock"]["simulation_step"], 61)
        forbidden = self.client.get(self.path + "/replay?simulation_step=61", headers=self.headers["red"])
        self.assertEqual(forbidden.status_code, 403)
        response = self.client.get(self.path + "/replay?simulation_step=61", headers=self.headers["host"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["fivepoint"]["second"], 305)
        self.command("surrender", role="red")
        self.command("advance_step")
        replay = self.client.get(self.path + "/replay?simulation_step=61", headers=self.headers["red"])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertNotIn("blue", replay.json()["resources"])

    def test_surrender_final_archive_and_result(self):
        self.enable()
        self.command("surrender", role="red")
        self.command("advance_step")
        result = self.client.get(self.path + "/result", headers=self.headers["blue"])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["result"]["winner"], "blue")
        archives = self.client.get(self.path + "/archives", headers=self.headers["red"])
        self.assertEqual(archives.status_code, 200, archives.text)
        self.assertEqual(len(archives.json()["items"]), 1)

    def test_task_plan_is_private_persisted_and_completed_by_rules(self):
        self.command("enable_fivepoint")
        tasks = [dict(task_id="wait-first", unit_id="R-G01", order=dict(action="wait"), duration_steps=1),
                 dict(task_id="observe-next", unit_id="R-G01", order=dict(action="observe"), depends_on=["wait-first"])]
        self.command("submit_fivepoint_plan", dict(expected_step=0, tasks=tasks), "red")
        self.command("submit_fivepoint_plan", dict(expected_step=0, orders={}), "blue")
        saved = self.command("create_save")["result"]["save_id"]
        self.command("advance_step")
        own_tasks = self.view("red")["fivepoint"]["own_tasks"]
        self.assertEqual([task["status"] for task in own_tasks], ["completed", "waiting"])
        self.assertEqual(self.view("blue")["fivepoint"]["own_tasks"], [])
        self.command("restore_save", dict(save_id=saved))
        self.assertEqual(self.view("red")["fivepoint"]["own_tasks"][0]["status"], "queued")
        self.command("advance_step")
        self.command("advance_step")
        self.assertEqual(self.view("red")["fivepoint"]["own_tasks"][1]["completed_step"], 2)

    def test_client_cannot_forge_completion_or_enemy_tasks(self):
        self.command("enable_fivepoint")
        initial_context = self.view()["context"]
        forged = dict(task_id="forged", unit_id="R-G01", order=dict(action="wait"), duration_steps=1, status="completed")
        self.command("submit_fivepoint_plan", dict(expected_step=0, tasks=[forged]), "red", expected=422)
        forged.pop("status")
        forged["unit_id"] = "B-G01"
        self.command("submit_fivepoint_plan", dict(expected_step=0, tasks=[forged]), "red", expected=409)
        self.assertEqual(self.view()["context"], initial_context)

    def test_playback_frame_is_private_and_does_not_reveal_future(self):
        self.enable()
        self.command('advance_step')
        response = self.client.get(self.path + '/fivepoint/frame?simulation_step=0', headers=self.headers['red'])
        self.assertEqual(response.status_code, 200, response.text)
        frame = response.json()
        self.assertEqual(frame['fivepoint']['second'], 0)
        self.assertTrue(all(unit['side'] == 'red' for unit in frame['units']))
        self.assertIsNone(frame['fivepoint']['result'])
        self.assertEqual(self.client.get(self.path + '/fivepoint/frame?simulation_step=2', headers=self.headers['red']).status_code, 409)

    def test_auto_round_commits_both_plans_and_blocks_human_orders(self):
        from backend.tests.test_fivepoint_completion import PlanningCompletionTests
        model, _ = PlanningCompletionTests().model_factory()
        self.command('enable_fivepoint', dict(control_mode='auto'))
        planner = self.client.app.state.fivepoint_planner
        with patch.object(planner, 'model_factory', model), patch('backend.fivepoint_coordinator.MessageModel.available', return_value=True):
            response = self.client.post(self.path + '/fivepoint/planning', headers=self.headers['host'],
                                        json=dict(context=self.view()['context'], played_second=0))
        self.assertEqual(response.status_code, 202, response.text)
        view = self.view('red')
        self.assertEqual(view['fivepoint']['phase'], 'paused')
        self.assertEqual(view['fivepoint']['planning_source'], 'model')
        self.assertEqual(len(view['fivepoint']['own_tasks']), 1)
        self.assertNotIn('planning_feedback', self.view()['fivepoint'])
        self.command('submit_fivepoint_plan', dict(expected_step=0), 'red', expected=409)
        self.command('advance_step')
        self.assertEqual(self.view('red')['fivepoint']['own_tasks'][0]['status'], 'completed')

    def test_manual_model_candidates_require_both_reviews_and_no_extra_round(self):
        from backend.tests.test_fivepoint_completion import PlanningCompletionTests
        model, _ = PlanningCompletionTests().model_factory()
        self.command('enable_fivepoint')
        planner = self.client.app.state.fivepoint_planner
        with patch.object(planner, 'model_factory', model), patch('backend.fivepoint_coordinator.MessageModel.available', return_value=True):
            response = self.client.post(self.path + '/fivepoint/planning', headers=self.headers['host'],
                                        json=dict(context=self.view()['context'], played_second=0))
            self.assertEqual(response.status_code, 202, response.text)
            repeated = self.client.post(self.path + '/fivepoint/planning', headers=self.headers['host'],
                                        json=dict(context=self.view()['context'], played_second=0))
            self.assertEqual(repeated.status_code, 409, repeated.text)
        self.assertEqual(self.view()['fivepoint']['phase'], 'planning')
        red_plan = self.view('red')['fivepoint']
        self.assertEqual(json.loads(red_plan['planning_candidate_json']), red_plan['planning_candidate'])
        self.assertNotIn('planning_candidate_json', self.view()['fivepoint'])
        self.assertNotIn('B-G01', red_plan['planning_candidate_json'])
        self.command('submit_fivepoint_plan', dict(expected_step=0, use_candidate=True), 'red')
        self.assertEqual(self.view()['fivepoint']['phase'], 'planning')
        self.command('submit_fivepoint_plan', dict(expected_step=0, decision='reject'), 'blue')
        self.assertEqual(self.view()['fivepoint']['phase'], 'paused')
        self.command('advance_step')
        self.assertEqual(self.view('red')['fivepoint']['own_tasks'][0]['status'], 'completed')

    def test_round_production_stops_at_boundary_and_buffer_limit(self):
        self.enable()
        for boundary in (0, 12, 24):
            if boundary:
                for side in ('red', 'blue'):
                    self.command('submit_fivepoint_plan', dict(expected_step=boundary), side)
            response = self.client.post(self.path + '/fivepoint/advance-round', headers=self.headers['host'],
                                        json=dict(context=self.view()['context'], played_second=0))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['completed_steps'], 12)
            self.assertEqual(self.view()['fivepoint']['step'], boundary + 12)
        for side in ('red', 'blue'):
            self.command('submit_fivepoint_plan', dict(expected_step=36), side)
        response = self.client.post(self.path + '/fivepoint/advance-round', headers=self.headers['host'],
                                    json=dict(context=self.view()['context'], played_second=0))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.view()['fivepoint']['step'], 36)

    def test_expired_and_restored_planning_cannot_publish_late_results(self):
        from backend.contracts import FivepointPlanningRequest
        self.command('enable_fivepoint')
        planner = self.client.app.state.fivepoint_planner
        token = self.headers['host']['Authorization'][7:]
        with patch('backend.fivepoint_coordinator.MessageModel.available', return_value=True):
            requested = planner.request(self.game_id, token, FivepointPlanningRequest(context=self.view()['context']))
        saved = self.command('create_save')['result']['save_id']
        self.command('restore_save', dict(save_id=saved))
        self.assertEqual(self.view()['fivepoint']['phase'], 'technical_pause')
        planner.execute(self.game_id, requested['run_id'])
        self.assertEqual(self.view()['fivepoint']['phase'], 'technical_pause')
        self.command('retry_fivepoint_planning')
        with patch('backend.fivepoint_coordinator.MessageModel.available', return_value=True):
            planner.request(self.game_id, token, FivepointPlanningRequest(context=self.view()['context']))
        with self.settings.connect() as connection:
            game = self.service._game(connection, self.game_id, lock=True)
            state = deepcopy(game['state_payload'])
            state['fivepoint']['planning_run']['deadline'] = '2000-01-01T00:00:00+00:00'
            planner.persist(connection, game, state)
        planner.recover_expired()
        self.assertEqual(self.view()['fivepoint']['phase'], 'technical_pause')

    def test_full_720_step_model_workflow_restore_replay_and_archive(self):
        """测试几何和消息替身完成60轮持久化闭环，不计为真实地图或付费模型验收。"""
        from backend.tests.test_fivepoint_completion import PlanningCompletionTests
        model, seen = PlanningCompletionTests().model_factory()
        self.command('enable_fivepoint', dict(control_mode='auto'))
        planner = self.client.app.state.fivepoint_planner
        with patch.object(planner, 'model_factory', model), patch('backend.fivepoint_coordinator.MessageModel.available', return_value=True):
            for boundary in range(0, 720, 12):
                if boundary == 360:
                    saved = self.command('create_save')['result']['save_id']
                    previous_branch = self.view()['context']['branch_id']
                    self.command('restore_save', dict(save_id=saved))
                    self.assertNotEqual(self.view()['context']['branch_id'], previous_branch)
                    self.assertEqual(self.view()['fivepoint']['playback_origin_step'], 360)
                response = self.client.post(self.path + '/fivepoint/planning', headers=self.headers['host'],
                                            json=dict(context=self.view()['context'], played_second=boundary * 5))
                self.assertEqual(response.status_code, 202, response.text)
                response = self.client.post(self.path + '/fivepoint/advance-round', headers=self.headers['host'],
                                            json=dict(context=self.view()['context'], played_second=boundary * 5))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['completed_steps'], 12)
        self.assertEqual(len(seen), 480)
        self.assertEqual(self.view()['fivepoint']['step'], 720)
        result = self.client.get(self.path + '/result', headers=self.headers['red']).json()['result']
        self.assertEqual(result['reason'], 'time_limit')
        self.assertEqual(result['winner'], 'red')
        self.assertEqual(result['scores']['red']['control'], '833/60')
        self.assertEqual(result['scores']['red']['resource'], '30')
        self.assertEqual(result['scores']['blue']['total'], '30')
        for side in ('red', 'blue', 'host'):
            archives = self.client.get(self.path + '/archives', headers=self.headers[side]).json()['items']
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0]['simulation_step'], 720)
        frame = self.client.get(self.path + '/fivepoint/frame?simulation_step=719', headers=self.headers['red']).json()
        self.assertIsNone(frame['fivepoint']['result'])
        self.assertEqual(frame['fivepoint']['second'], 3595)


if __name__ == "__main__":
    unittest.main()
