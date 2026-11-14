"""总册新增闭环的独立预期，模型替身不构成真实模型或真实地图验收。"""

from copy import deepcopy
import json
from threading import Lock
import time
import unittest

from backend import fivepoint_engine as engine
from backend.agent_contracts import AgentConfiguration
from backend.fivepoint_parameters import RuleViolation
from backend.fivepoint_planning import run_round
from backend.fivepoint_playback import start_or_resume
from backend.fivepoint_tasks import accept_independent
from backend.tests.test_fivepoint_engine import fixture, location, plan, step, FixtureGeometry
from backend.tests.test_fivepoint_tasks import task, adopt


class RuleCompletionTests(unittest.TestCase):
    """覆盖范围选敌、情报隔离、独立任务采用和预授权补给。"""

    def test_range_selects_nearest_then_lowest_health(self):
        state = fixture(('ground', 'ground', 'ground'))
        enemies = state['units'][3:]
        enemies[0]['position'] = location(400)
        enemies[1]['position'] = enemies[2]['position'] = location(300)
        enemies[2]['hp'] = '50'
        order = dict(action='attack', target_area=dict(center=location(300), radius=200))
        state = step(plan(state, {'red-0': order}))
        self.assertEqual([unit['hp'] for unit in state['units'][3:]], ['100', '100', '34'])
        self.assertEqual(state['units'][0]['ammo'], 59)

    def test_range_cursor_persists_and_rejected_shots_do_not_advance(self):
        state = fixture(('ground', 'ground'))
        order = dict(action='attack', target_area=dict(center=location(500), radius=10))
        state = step(plan(state, {'red-0': order}))
        self.assertEqual(state['units'][0]['target_cursor'], 1)
        state = step(json.loads(json.dumps(state)))
        self.assertEqual(state['units'][0]['target_cursor'], 1)
        state['units'][2]['hp'] = state['units'][3]['hp'] = '84'
        state = step(state)
        self.assertEqual(state['units'][3]['hp'], '68')
        self.assertEqual(state['units'][0]['target_cursor'], 0)

    def test_range_never_selects_outside_authorization_or_through_wall(self):
        class BlockedGeometry(FixtureGeometry):
            """明确遮挡第一个目标的测试几何。"""
            def can_fire(self, shooter, target):
                """目标在范围内仍须射手射线合法。"""
                return target['unit_id'] != 'blue-0'
        state = fixture(('ground', 'ground'))
        state['units'][3]['position'] = location(700)
        order = dict(action='attack', target_area=dict(center=location(500), radius=20))
        state = step(plan(state, {'red-0': order}), BlockedGeometry())
        self.assertEqual(state['units'][0]['ammo'], 60)
        self.assertEqual(state['units'][0]['target_cursor'], 0)

    def test_range_task_ends_at_authorized_deadline(self):
        attack = task('area', action='attack', deadline_second=15)
        attack['order']['target_area'] = dict(center=location(500), radius=50)
        state = adopt(fixture(), [attack])
        for _ in range(3):
            state = step(state)
        self.assertEqual(state['tasks']['red'][0]['status'], 'completed')

    def test_private_capture_progress_and_visible_health(self):
        state = step(plan(fixture()))
        self.assertEqual(engine.project(state, 'red')['points'][0]['progress'], 5)
        self.assertNotIn('progress', engine.project(state, 'blue')['points'][0])
        self.assertIn('hp', engine.project(state, 'red')['contacts'][0])
        self.assertNotIn('energy', engine.project(state, 'red')['contacts'][0])

    def test_unknown_target_errors_do_not_probe_hidden_roster(self):
        state = fixture()
        state['history']['red'] = {}
        codes = []
        for identity in ('blue-0', 'unknown-id'):
            with self.assertRaises(RuleViolation) as error:
                engine.check_orders(state, 'red', {'red-0': dict(action='attack', target_id=identity)})
            codes.append(error.exception.code)
        self.assertEqual(codes, ['ATTACK_INVALID', 'ATTACK_INVALID'])

    def test_visible_health_is_committed_after_damage(self):
        state = step(plan(fixture(), {'red-0': dict(action='attack', target_id='blue-0')}))
        contact = next(item for item in engine.project(state, 'red')['contacts'] if item['unit_id'] == 'blue-0')
        self.assertEqual(contact['hp'], '84')

    def test_malformed_model_fields_do_not_discard_independent_tasks(self):
        malformed = task('bad', 'red-1', 'service')
        malformed['order'].update(target_id=[], category={}, amount=2)
        accepted, rejected = accept_independent(fixture(), 'red', [malformed, task('valid')])
        self.assertEqual([item['task_id'] for item in accepted], ['valid'])
        self.assertEqual(rejected, [dict(task_id='bad', code='TASK_FIELDS_INVALID')])

    def test_partial_adoption_rejects_invalid_dependencies_but_keeps_independent(self):
        tasks = [task('bad', unit_id='blue-0'), task('dependent', depends_on=['bad']), task('valid', 'red-1')]
        accepted, rejected = accept_independent(fixture(), 'red', tasks)
        self.assertEqual([item['task_id'] for item in accepted], ['valid'])
        self.assertEqual({item['task_id'] for item in rejected}, {'bad', 'dependent'})

    def test_partial_adoption_handles_cycles_duplicates_and_malformed_units(self):
        tasks = [task('cycle-a', depends_on=['cycle-b']), task('cycle-b', depends_on=['cycle-a']),
                 task('duplicate'), task('duplicate'), task('malformed', unit_id=[]), task('valid', 'red-1')]
        accepted, rejected = accept_independent(fixture(), 'red', tasks)
        self.assertEqual([item['task_id'] for item in accepted], ['valid'])
        self.assertEqual(len(rejected), 4)

    def test_partial_adoption_removes_both_conflicting_roots(self):
        accepted, rejected = accept_independent(fixture(), 'red', [task('first'), task('second'), task('third', 'red-1')])
        self.assertEqual([item['task_id'] for item in accepted], ['third'])
        self.assertEqual(len(rejected), 2)

    def test_empty_ammo_branch_completes_service_before_resuming(self):
        state = fixture()
        engine.spend(state, state['units'][0], 'ammo', 60)
        camp = next(item for item in state['frozen']['camps'] if item['camp_id'] == 'red-ground')
        state['units'][0]['position'] = deepcopy(camp['position'])
        wait_task = task('wait', branches=[dict(condition='ammo_empty', orders=[dict(action='camp', camp_id='red-ground', balances=dict(ammo=10))])])
        state = step(adopt(state, [wait_task]))
        self.assertEqual(state['units'][0]['ammo'], 10)
        self.assertEqual(state['tasks']['red'][0]['status'], 'running')
        self.assertEqual(state['tasks']['red'][0]['active_steps'], 0)
        state = step(json.loads(json.dumps(state)))
        self.assertEqual(state['tasks']['red'][0]['status'], 'completed')

    def test_low_energy_authorized_return_and_no_extra_model_opportunity(self):
        class ReturnGeometry(FixtureGeometry):
            """显式声明沿测试路线服务成本下降。"""
            def service_energy_cost(self, unit, own_view):
                """只使用本方位置计算测试返营成本。"""
                return abs(engine.number(unit['position']['xy'][0])) / 100
            def movement_service_energy_cost(self, unit, movement, own_view):
                """本测试单调返营段的最大成本位于起点。"""
                return self.service_energy_cost(unit, own_view)
        state = fixture()
        camp = next(item for item in state['frozen']['camps'] if item['camp_id'] == 'red-ground')
        camp['position'] = location(0)
        state['frozen_hash'] = engine.digest(state['frozen'])
        state['units'][0]['position'] = location(50)
        engine.spend(state, state['units'][0], 'energy', 75)
        branch = dict(condition='low_energy', orders=[dict(action='move', destination=location(0)),
                                                      dict(action='camp', camp_id='red-ground')])
        state = step(adopt(state, [task('resume', branches=[branch])]), ReturnGeometry())
        self.assertLess(engine.number(state['units'][0]['position']['xy'][0]), 50)
        self.assertEqual(state['step'], 1)
        self.assertNotEqual(state['phase'], 'planning')
        self.assertEqual(state['tasks']['red'][0]['active_branch'], 0)

    def test_start_buffer_and_terminal_drain(self):
        self.assertEqual(start_or_resume(120, 0), 0)
        self.assertEqual(start_or_resume(180, 0), 2)
        self.assertEqual(start_or_resume(30, 0, finished=True), 0.5)
        self.assertEqual(start_or_resume(90, 0, started=True, stalled=True), 0)
        self.assertEqual(start_or_resume(120, 0, started=True, stalled=True), 2)

    def test_patrol_waypoint_cursor_survives_restore_until_deadline(self):
        patrol = task('patrol', action='patrol', deadline_second=20)
        patrol['order']['waypoints'] = [location(20), location(0)]
        state = step(adopt(fixture(), [patrol]))
        self.assertEqual(state['units'][0]['position']['xy'], ['20', '0'])
        self.assertEqual(state['tasks']['red'][0]['waypoint_index'], 1)
        state = step(json.loads(json.dumps(state)))
        self.assertEqual(state['units'][0]['position']['xy'], ['0', '0'])
        state = step(step(state))
        self.assertEqual(state['tasks']['red'][0]['status'], 'completed')

    def test_full_accounts_do_not_accumulate_camp_queue_priority(self):
        state = fixture()
        camp = next(item for item in state['frozen']['camps'] if item['camp_id'] == 'red-ground')
        state['units'][0]['position'] = deepcopy(camp['position'])
        state = step(plan(state, {'red-0': dict(action='camp', camp_id='red-ground')}))
        self.assertIsNone(state['units'][0]['queue_since'])

    def test_technical_abort_retains_statistics_without_winner(self):
        state = engine.technical_abort(engine.fail_planning(fixture(), 'red', 'MODEL_TIMEOUT'))
        self.assertIsNone(state['result']['winner'])
        self.assertEqual(state['result']['scores']['red']['total'], '30')


class PlanningCompletionTests(unittest.TestCase):
    """模型替身验证同步屏障与私有输入，测试不会调用远端接口。"""

    def model_factory(self, *, malformed=False, slow=False):
        """构造可控回应，用锁记录并发调用而不读取凭据。"""
        lock, seen = Lock(), []
        class FakeModel:
            """仅供测试的消息模型。"""
            def __init__(self, configuration):
                """记录调用配置，不创建网络连接。"""
                self.configuration = configuration
            def complete(self, messages, tools, remaining_tokens=None):
                """按私有视图的单位阵营生成任务或故意制造格式失败。"""
                payload = json.loads(messages[1]['content'])
                with lock:
                    seen.append(payload)
                    sequence = len(seen)
                if slow:
                    time.sleep(0.05)
                if malformed and sequence == 1:
                    return dict(content='不是JSON'), dict(total_tokens=1)
                own = payload['view']['units'][0]['unit_id']
                return dict(content=json.dumps(dict(tasks=[task('wait', own)]))), dict(total_tokens=1)
        return FakeModel, seen

    def test_both_sides_share_time_and_only_private_inputs(self):
        model, seen = self.model_factory()
        state = fixture()
        original = deepcopy(state)
        result = run_round(state, {side: AgentConfiguration() for side in ('red', 'blue')}, model)
        self.assertEqual(state, original)
        self.assertEqual(result['phase'], 'paused')
        self.assertEqual(len(seen), 8)
        self.assertTrue(all(item['view']['second'] == 0 for item in seen))
        self.assertTrue(all(len({unit['side'] for unit in item['view']['units']}) == 1 for item in seen))
        self.assertEqual(result['planning_feedback']['red']['calls'], 4)

    def test_one_format_repair_stays_in_same_round(self):
        model, seen = self.model_factory(malformed=True)
        result = run_round(fixture(), {side: AgentConfiguration() for side in ('red', 'blue')}, model)
        self.assertEqual(result['phase'], 'paused')
        self.assertEqual(len(seen), 9)
        self.assertEqual(sum(info['repairs'] for info in result['planning_feedback'].values()), 1)

    def test_timeout_does_not_adopt_late_output(self):
        model, _ = self.model_factory(slow=True)
        result = run_round(fixture(), {side: AgentConfiguration() for side in ('red', 'blue')}, model, budget_seconds=0.01)
        self.assertEqual(result['phase'], 'technical_pause')
        self.assertEqual(result['pending_plans'], {})
        saved = json.dumps(result, sort_keys=True)
        time.sleep(0.1)
        self.assertEqual(json.dumps(result, sort_keys=True), saved)


if __name__ == '__main__':
    unittest.main()
