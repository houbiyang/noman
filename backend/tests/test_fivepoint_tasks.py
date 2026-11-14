"""以独立结果验收任务图，不使用模型声明作为任务完成证据。"""

import json
import unittest

from backend import fivepoint_engine as engine
from backend.fivepoint_parameters import RuleViolation
from backend.fivepoint_tasks import validate_tasks
from backend.tests.test_fivepoint_engine import fixture, location, step


def task(identity, unit_id="red-0", action="wait", **fields):
    """创建测试任务；默认等待一个完整五秒步。"""
    result = dict(task_id=identity, unit_id=unit_id, order=dict(action=action), **fields)
    if action == "wait":
        result.setdefault("duration_steps", 1)
    return result


def adopt(state, tasks):
    """双方共同采用本方任务与对方待命方案。"""
    state = engine.submit_plan(state, "red", {}, state["step"], tasks=tasks)
    return engine.submit_plan(state, "blue", {}, state["step"])


class TaskAcceptance(unittest.TestCase):
    """覆盖依赖边界、真实完成、失败传播和恢复行为。"""

    def test_successor_starts_next_step_after_json_restore(self):
        state = adopt(fixture(), [task("first"), task("second", depends_on=["first"])])
        state = step(state)
        self.assertEqual([item["status"] for item in state["tasks"]["red"]], ["completed", "waiting"])
        self.assertEqual(state["tasks"]["red"][1]["active_steps"], 0)
        state = step(json.loads(json.dumps(state)))
        self.assertEqual(state["tasks"]["red"][1]["completed_step"], 2)

    def test_cycles_unknown_dependencies_and_same_unit_conflicts_reject(self):
        for tasks in ([task("first", depends_on=["second"]), task("second", depends_on=["first"])],
                      [task("first", depends_on=["missing"])], [task("first"), task("second")],
                      [task("first", unit_id="blue-0")], [None]):
            with self.subTest(tasks=tasks), self.assertRaises(RuleViolation):
                validate_tasks(fixture(), "red", tasks)

    def test_long_chain_does_not_use_python_recursion(self):
        tasks = [task(f"task-{index}", depends_on=[f"task-{index - 1}"] if index else []) for index in range(1000)]
        self.assertEqual(len(validate_tasks(fixture(), "red", tasks)), 1000)

    def test_observe_completes_without_detecting_enemies(self):
        state = fixture()
        for unit in state["units"]:
            if unit["side"] == "blue":
                unit["position"] = location(10000)
        state = step(adopt(state, [task("observe", action="observe")]))
        self.assertEqual(state["visible"]["red"], [])
        self.assertEqual(state["tasks"]["red"][0]["status"], "completed")

    def test_takeoff_then_observe_requires_two_steps(self):
        state = adopt(fixture(), [task("up", "red-2", "takeoff"),
                                  task("observe", "red-2", "observe", depends_on=["up"])])
        state = step(state)
        self.assertEqual(state["tasks"]["red"][1]["active_steps"], 0)
        state = step(state)
        self.assertEqual(state["tasks"]["red"][1]["completed_step"], 2)

    def test_capture_requires_actual_ownership(self):
        state = adopt(fixture(), [task("capture", action="capture", target_point_id="point-0")])
        for _ in range(5):
            state = step(state)
        self.assertEqual(state["tasks"]["red"][0]["status"], "running")
        state = step(state)
        self.assertEqual(state["points"][0]["owner"], "red")
        self.assertEqual(state["tasks"]["red"][0]["completed_step"], 6)

    def test_move_requires_arrival_and_then_waits_full_duration(self):
        move = task("move", action="move")
        move["order"]["destination"] = location(50)
        state = adopt(fixture(), [move, task("wait", depends_on=["move"], duration_steps=2)])
        state = step(state)
        self.assertEqual(state["tasks"]["red"][0]["status"], "running")
        state = step(state)
        self.assertEqual(state["tasks"]["red"][0]["completed_step"], 2)
        state = step(state)
        self.assertEqual(state["tasks"]["red"][1]["status"], "running")
        state = step(state)
        self.assertEqual(state["tasks"]["red"][1]["completed_step"], 4)

    def test_quantity_service_uses_actual_delivered_quantity(self):
        state = fixture()
        engine.spend(state, state["units"][0], "energy", 3)
        service = task("supply", "red-1", "service")
        service["order"].update(target_id="red-0", category="energy", amount=3)
        state = step(adopt(state, [service]))
        self.assertEqual(state["tasks"]["red"][0]["status"], "running")
        self.assertEqual(state["units"][0]["energy"], "99")
        state = step(json.loads(json.dumps(state)))
        self.assertEqual(state["tasks"]["red"][0]["completed_step"], 2)
        self.assertEqual(state["units"][0]["energy"], "100")
        self.assertEqual(step(state)["step"], 3)

    def test_dead_service_target_does_not_block_the_step(self):
        state = fixture()
        service = task("supply", "red-1", "service")
        service["order"].update(target_id="red-0", category="energy", balance=100)
        state = adopt(state, [service, task("after", "red-1", depends_on=["supply"])])
        engine.retire(state, state["units"][0], "test_retirement")
        state = step(state)
        self.assertEqual([item["reason"] for item in state["tasks"]["red"]], ["target_retired", "dependency_failed"])
        self.assertEqual(state["step"], 1)

    def test_waiting_deadline_expires_at_boundary_and_cascades(self):
        state = adopt(fixture(), [task("third", depends_on=["second"]),
                                  task("second", depends_on=["first"], deadline_second=5),
                                  task("first", duration_steps=3)])
        state = step(state)
        self.assertEqual(state["tasks"]["red"][1]["reason"], "deadline_reached")
        state = step(state)
        self.assertEqual(state["tasks"]["red"][0]["reason"], "dependency_failed")

    def test_future_service_conflict_requires_explicit_dependency(self):
        service = task("supply", "red-1", "service", depends_on=["ready"])
        service["order"].update(target_id="red-0", category="energy", amount=3)
        move = task("move", action="move")
        move["order"]["destination"] = location(50)
        with self.assertRaises(RuleViolation) as error:
            validate_tasks(fixture(), "red", [task("ready", "red-1"), service, move])
        self.assertEqual(error.exception.code, "SERVICE_CONFLICT")

    def test_reject_preserves_existing_task_and_waits_for_other_side(self):
        state = adopt(fixture(), [task("long-wait", duration_steps=13)])
        for _ in range(12):
            state = step(state)
        state = engine.submit_plan(state, "red", {}, 12, decision="reject")
        self.assertEqual(state["phase"], "planning")
        self.assertEqual(state["pending_tasks"]["red"][0]["active_steps"], 12)
        with self.assertRaises(RuleViolation):
            step(state)
        state = engine.submit_plan(state, "blue", {}, 12)
        state = step(state)
        self.assertEqual(state["tasks"]["red"][0]["completed_step"], 13)

    def test_reject_cannot_smuggle_new_orders_or_tasks(self):
        for orders, tasks in (({"red-0": dict(action="wait")}, None), ({}, [])):
            with self.subTest(orders=orders, tasks=tasks), self.assertRaises(RuleViolation):
                engine.submit_plan(fixture(), "red", orders, 0, tasks, "reject")

    def test_explicit_cancel_preserves_spent_resources_and_fails_dependents(self):
        state = adopt(fixture(), [task("long-wait", duration_steps=15), task("after", depends_on=["long-wait"])])
        for _ in range(12):
            state = step(state)
        before = json.dumps(state["ledger"], sort_keys=True)
        state = engine.submit_plan(state, "red", {}, 12, decision="reject", cancel_task_ids=["long-wait"])
        state = engine.submit_plan(state, "blue", {}, 12)
        self.assertEqual(state["tasks"]["red"][0]["status"], "cancelled")
        self.assertEqual(json.dumps(state["ledger"], sort_keys=True), before)
        state = step(state)
        self.assertEqual(state["tasks"]["red"][1]["reason"], "dependency_failed")
        self.assertEqual(state["tasks"]["red"][0]["active_steps"], 12)

    def test_unknown_task_cannot_be_cancelled(self):
        with self.assertRaises(RuleViolation) as error:
            engine.submit_plan(fixture(), "red", {}, 0, decision="reject", cancel_task_ids=["blue-task"])
        self.assertEqual(error.exception.code, "TASK_CANCEL_INVALID")


if __name__ == "__main__":
    unittest.main()
