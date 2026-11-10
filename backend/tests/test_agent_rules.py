"""独立预期验证任务依赖、角色隔离、资源守恒和占点轨迹。"""

from copy import deepcopy
from threading import Barrier, Event
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from backend import coordination, spatial
from backend.agent_contracts import AgentConfiguration, CoordinationRules
from backend.agent_graph import build_graph, invoke_graph
from backend.agent_tools import RuleTools, freeze_view, validate_plan
from backend.demo_domain import initial_state, map_manifest
from backend.runtime import ServiceError, sha256


def scenario():
    """创建独立测试世界，坐标与预期明确，不依赖正在运行的对局。"""
    state = initial_state('test-scene', 'test-rule', 'patrol')
    state['manifest'].update(spatial_profile=spatial.profile(), map_source_sha256=map_manifest()['source_file_sha256'])
    state['lifecycle'].update(phase='paused', ready=dict(red=True, blue=True))
    for index, unit in enumerate(state['units']):
        unit.update(energy=100, supply=100, durability=100, movement_departed=True,
                    position=spatial.business((3000 + index * 50, 6000 if unit['side'] == 'red' else 9000)))
    state['coordination'] = dict(schema_version='coordination-test-v1', test_only=True, rules=CoordinationRules().model_dump(),
        points=[dict(point_id=identity, name=identity, position=spatial.business((position, 6000)),
            approach_position=spatial.business((position, 6000)), navigation_status='test_surface_verified',
            owner=None, status='neutral', candidate_side=None, progress_kind=None, progress_steps=0)
            for identity, position in (('TASK_A', 3050), ('TASK_B', 6000), ('TASK_C', 9000))],
        plans={}, all_held=dict(red=0, blue=0), last_scored_step=0, limitations=[])
    state['scores'] = dict(available=True, test_only=True, red=0, blue=0, winner=None, reason=None)
    for plan in state['decisions']['plans'].values():
        plan.update(status='adopted', source='multi_agent')
    return state


def task(identity='capture-a', unit='R-G01', action='capture', dependencies=None, **values):
    """构造明确主动作，便于手算每步预期。"""
    return dict(task_id=identity, unit_id=unit, action=action, target_point_id='TASK_A',
                depends_on=dependencies or [], timeout_steps=15, duration_steps=1, movement_mode='move', reason='验证', **values)


class AgentRuleTests(unittest.TestCase):
    """不调用真实模型的规则测试，不能作为真实模型质量证明。"""

    def setUp(self):
        self.state = scenario()
        navigator = patch('backend.coordination.movement.Navigator')
        self.navigation = navigator.start()
        self.navigation.return_value.clear.return_value = True
        self.addCleanup(navigator.stop)

    def advance(self, current):
        """保留完整步初位置，独立推进一步并读取程序结算。"""
        unit = next(unit for unit in self.state['units'] if unit['unit_id'] == current['unit_id'])
        actions = {unit['unit_id']: dict(side=unit['side'], task=current, start_position=deepcopy(unit['position']))}
        if current.get('target_unit_id'):
            target = next(unit for unit in self.state['units'] if unit['unit_id'] == current['target_unit_id'])
            actions[unit['unit_id']]['target_start_position'] = deepcopy(target['position'])
        self.state['lifecycle']['simulation_step'] += 1
        coordination.finish_step(self.state, actions)
        return self.state['coordination']['points'][0]

    def test_capture_requires_two_complete_steps_and_scores_later(self):
        capture = task(status='running')
        self.assertIsNone(self.advance(capture)['owner'])
        self.assertEqual(self.advance(capture)['owner'], 'red')
        self.assertEqual(self.state['scores']['red'], 0)
        self.advance(task(action='hold', status='running'))
        self.assertEqual(self.state['scores']['red'], 1)

    def test_enemy_point_neutralizes_before_capturing(self):
        self.state['coordination']['points'][0]['owner'] = 'blue'
        capture = task(status='running')
        self.assertEqual(self.advance(capture)['owner'], 'blue')
        self.assertIsNone(self.advance(capture)['owner'])
        self.assertIsNone(self.advance(capture)['owner'])
        self.assertEqual(self.advance(capture)['owner'], 'red')
        self.assertEqual(self.state['scores']['red'], 0)

    def test_contest_retains_owner_resets_progress_and_stops_score(self):
        point = self.state['coordination']['points'][0]
        point.update(owner='red', progress_steps=1, candidate_side='blue')
        blue = next(unit for unit in self.state['units'] if unit['unit_id'] == 'B-G01')
        blue['position'] = deepcopy(point['position'])
        self.advance(task(status='running'))
        self.assertEqual((point['owner'], point['status'], point['progress_steps']), ('red', 'contested', 0))
        self.assertEqual(self.state['scores']['red'], 0)

    def test_moving_into_point_does_not_capture_same_step(self):
        self.advance(task(action='move', route_id='missing', status='running'))
        self.assertIsNone(self.state['coordination']['points'][0]['owner'])
        self.assertEqual(self.state['coordination']['points'][0]['progress_steps'], 0)

    def test_score_same_step_is_idempotent(self):
        self.state['coordination']['points'][0]['owner'] = 'red'
        self.advance(task(action='hold', status='running'))
        before = deepcopy(self.state['scores'])
        coordination.settle_points(self.state, {})
        self.assertEqual(self.state['scores'], before)

    def test_score_target_and_draw_are_symmetric(self):
        self.state['coordination']['rules']['victory_points'] = 1
        self.state['coordination']['points'][0]['owner'] = 'red'
        self.state['coordination']['points'][1]['owner'] = 'blue'
        self.advance(task(action='hold', status='running'))
        self.assertEqual(self.state['scores']['reason'], 'score_target')
        self.assertIsNone(self.state['scores']['winner'])
        self.assertEqual(self.state['lifecycle']['phase'], 'finished')

    def test_supply_transfer_conserves_abstract_resources(self):
        source = next(unit for unit in self.state['units'] if unit['unit_id'] == 'R-S01')
        target = next(unit for unit in self.state['units'] if unit['unit_id'] == 'R-G01')
        target['energy'] = 40
        source['position'] = deepcopy(target['position'])
        total = source['supply'] + target['energy']
        self.advance(task(unit='R-S01', action='resupply', target_unit_id='R-G01', status='running'))
        self.assertEqual(target['energy'], 60)
        self.assertEqual(source['supply'] + target['energy'], total)

    def test_dependencies_wait_and_do_not_execute_same_step(self):
        first = task('first', action='wait', status='queued', started_step=None)
        second = task('second', action='wait', dependencies=['first'], status='queued', started_step=None)
        self.state['coordination']['plans']['red'] = dict(status='adopted', tasks=[first, second],
            proposal_id=self.state['decisions']['plans']['red']['proposal_id'])
        actions = coordination.prepare_step(self.state)
        self.assertEqual(actions['R-G01']['task']['task_id'], 'first')
        self.assertEqual(second['status'], 'waiting')
        self.state['lifecycle']['simulation_step'] = 1
        coordination.finish_step(self.state, actions)
        self.assertEqual(first['status'], 'succeeded')
        self.assertEqual(second['status'], 'waiting')
        self.assertEqual(coordination.prepare_step(self.state)['R-G01']['task']['task_id'], 'second')

    def test_timeout_fails_task_and_dependent(self):
        first = task('first', action='wait', status='running', started_step=0)
        first['timeout_steps'] = 1
        second = task('second', action='wait', dependencies=['first'], status='queued', started_step=None)
        self.state['coordination']['plans']['red'] = dict(status='adopted', tasks=[first, second],
            proposal_id=self.state['decisions']['plans']['red']['proposal_id'])
        self.state['lifecycle']['simulation_step'] = 1
        coordination.prepare_step(self.state)
        self.assertEqual(first['status'], 'failed')
        coordination.prepare_step(self.state)
        self.assertEqual(second['status'], 'failed')

    def test_hidden_enemy_changes_do_not_change_frozen_input(self):
        before = sha256(freeze_view(self.state, 'red'))
        next(unit for unit in self.state['units'] if unit['side'] == 'blue')['energy'] = 1
        self.state['coordination']['plans']['blue'] = dict(status='adopted', tasks=[])
        self.assertEqual(sha256(freeze_view(self.state, 'red')), before)

    def test_tools_reject_cross_side_and_other_role(self):
        tools = RuleTools(freeze_view(self.state, 'red'), 'recon')
        result = tools.call('preview_route', dict(unit_id='B-U01', position=dict(x='30', y='40')))
        self.assertEqual(result['code'], 'UNIT_NOT_FOUND')
        self.assertEqual(tools.call('preview_route', dict(unit_id='R-G01'))['code'], 'AGENT_ROLE_FORBIDDEN')
        self.assertEqual(tools.call('execute_sql', {})['code'], 'TOOL_FORBIDDEN')

    def test_plan_rejects_cycles_unit_conflicts_and_wrong_capability(self):
        view = freeze_view(self.state, 'red')
        for tasks, code in [([task('one', dependencies=['two']), task('two', dependencies=['one'])], 'TASK_DEPENDENCY_CYCLE'),
                            ([task('one'), task('two')], 'TASK_UNIT_CONFLICT'),
                            ([task(unit='R-U01')], 'CAPTURE_ROLE_INVALID'),
                            ([task(unit='B-G01')], 'TASK_UNIT_UNAVAILABLE')]:
            with self.subTest(code=code), self.assertRaises(ServiceError) as caught:
                validate_plan(view, dict(summary='校验', tasks=tasks))
            self.assertEqual(caught.exception.code, code)

    def test_configuration_rejects_credentials_in_endpoint(self):
        with self.assertRaises(ValueError):
            AgentConfiguration.model_validate(dict(model=dict(base_url='https://user:secret@example.com/v1')))

    def test_native_langgraph_runs_three_roles_in_parallel_before_commander(self):
        barrier, completed = Barrier(3, timeout=5), []

        def analyze(role):
            barrier.wait()
            completed.append(role)
            return dict(role=role)

        def coordinate(reports):
            self.assertEqual(set(completed), {'recon', 'ground', 'support'})
            return dict(roles=sorted(reports))

        result = invoke_graph(build_graph(analyze, coordinate), 'isolated-graph')
        self.assertEqual(result['commander']['roles'], ['ground', 'recon', 'support'])

    def test_checkpoint_failure_prevents_next_stage_model_calls(self):
        """输入检查点写入失败时，不得提前进入有外部调用的分析节点。"""
        node_started = Event()
        calls = []

        class FailingCheckpointSaver(InMemorySaver):
            """在输入状态落盘时等待节点信号，稳定暴露异步写盘的执行窗口。"""

            def put(self, config, checkpoint, metadata, new_versions):
                """模拟输入检查点不可持久化，不影响前一个空检查点。"""
                if metadata['step'] == 0:
                    node_started.wait(timeout=0.5)
                    raise OSError('测试检查点写入失败')
                return super().put(config, checkpoint, metadata, new_versions)

        def analyze(role):
            """记录是否在输入尚未可靠保存时开始了模型节点。"""
            calls.append(role)
            node_started.set()
            return dict(role=role)

        graph = build_graph(analyze, lambda reports: dict(summary='测试汇总'), FailingCheckpointSaver())
        with self.assertRaises(OSError):
            invoke_graph(graph, 'checkpoint-write-failure')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
