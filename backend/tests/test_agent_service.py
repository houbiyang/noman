"""真实 HTTP 与隔离 PostgreSQL 验证，模型使用显式测试替身。"""

from collections import Counter
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.agent_contracts import AgentConfiguration
from backend.agent_graph import RunCheckpointSaver
from backend.runtime import ServiceError
from backend.tests import test_service as baseline


class RoleModelFixture:
    """不联网、不计费的模型替身，只验证编排和真实持久化。"""

    calls = Counter()
    fail_role = None
    plan_tasks = None
    failure_gate = None

    def __init__(self, configuration):
        self.configuration = configuration

    def analyze(self, role, frozen_input, toolset, output_model, guard):
        """四角色返回明确标记的测试输出，不作为真实模型验收证据。"""
        guard()
        self.calls[role] += 1
        if role == self.fail_role:
            if self.failure_gate:
                self.failure_gate.wait(timeout=5)
            raise ServiceError(503, 'FIXTURE_FAILURE', '模型替身主动制造的恢复测试失败')
        if role == 'commander':
            unit = frozen_input['state']['units'][0]
            output = dict(summary='模型替身的协同方案', tasks=deepcopy(self.plan_tasks) if self.plan_tasks else
                          [dict(task_id='wait-test', unit_id=unit['unit_id'], action='wait')])
        else:
            output = dict(summary=role + ' 测试报告', suggested_tasks=[], support_requests=[], uncertainties=[])
        parsed = output_model.model_validate(output)
        return dict(output=parsed.model_dump(mode='json', exclude_none=True), usage=dict(total_tokens=10),
                    model_calls=1, tools=[], real_model_called=False, source='test_fixture')


class AgentServiceTests(unittest.TestCase):
    """复用原有隔离库初始化与清理保护，不修改用户对局。"""

    setUpClass = classmethod(baseline.CurrentFeatureTests.setUpClass.__func__)
    cleanup_database = classmethod(baseline.CurrentFeatureTests.cleanup_database.__func__)
    view = baseline.CurrentFeatureTests.view
    command = baseline.CurrentFeatureTests.command

    def setUp(self):
        response = self.client.post('/api/v1/games', headers=dict(self.bootstrap, **{'Idempotency-Key': str(uuid4())}),
                                    json=dict(title='多智能体隔离验证', is_demo=True, spatial_test=True))
        self.assertEqual(response.status_code, 201, response.text)
        self.game_id = response.json()['game_id']
        self.path = '/api/v1/games/' + self.game_id
        self.headers = {}
        for role in ('host', 'red', 'blue'):
            response = self.client.post(self.path.replace('/games/', '/bootstrap/games/') + '/sessions',
                                        headers=self.bootstrap, json=dict(role=role))
            self.assertEqual(response.status_code, 200, response.text)
            self.headers[role] = {'Authorization': 'Bearer ' + response.json()['access_token']}
        for side, position in (('red', dict(x='10.3', y='26.2')), ('blue', dict(x='100.3', y='50.2'))):
            preparation = dict(side=side, position=position, quantities=dict(recon=1, ground=1, support=1))
            preview = self.client.post(self.path + '/preparation/preview', headers=self.headers[side],
                                       json=dict(context=self.view(side)['context'], preparation=preparation))
            self.assertEqual(preview.status_code, 200, preview.text)
            self.command('set_preparation', dict(preparation, preview_token=preview.json()['preview_token']), side)
        self.command('enable_coordination')
        directory = TemporaryDirectory(prefix='qiongtu-agents-')
        self.addCleanup(directory.cleanup)
        self.agents = self.client.app.state.agents
        self.agents.config_root = Path(directory.name)
        self.agents.model_factory = RoleModelFixture
        RoleModelFixture.calls = Counter()
        RoleModelFixture.fail_role = None
        RoleModelFixture.plan_tasks = None
        RoleModelFixture.failure_gate = None

    def configure(self, side='red', model='isolated-fixture'):
        configuration = AgentConfiguration()
        configuration.model.enabled = True
        configuration.model.base_url = 'http://127.0.0.1:1/v1'
        configuration.model.model = model
        response = self.client.post(self.path + '/agents/configuration', headers=self.headers[side], json=configuration.model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_agent(self, side='red', key=None, body=None):
        body = body or dict(context=self.view(side)['context'], reason='独立编排验证')
        response = self.client.post(self.path + '/agents/runs', headers=dict(self.headers[side], **{'Idempotency-Key': key or str(uuid4())}), json=body)
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()['run_id']
        detail = self.client.get(self.path + '/agents/runs/' + run_id, headers=self.headers[side])
        self.assertEqual(detail.status_code, 200, detail.text)
        return detail.json()

    def test_red_blue_configuration_and_private_runs_are_isolated(self):
        self.configure('red', 'red-model')
        self.configure('blue', 'blue-model')
        red = self.client.get(self.path + '/agents/configuration', headers=self.headers['red']).json()
        blue = self.client.get(self.path + '/agents/configuration', headers=self.headers['blue']).json()
        self.assertEqual(red['configuration']['model']['model'], 'red-model')
        self.assertEqual(blue['configuration']['model']['model'], 'blue-model')
        run = self.run_agent()
        self.assertEqual(run['status'], 'waiting_human', run)
        self.assertEqual(self.client.get(self.path + '/agents/runs/' + run['run_id'], headers=self.headers['blue']).status_code, 404)
        self.assertEqual(self.client.get(self.path + '/agents/configuration', headers=self.headers['host']).status_code, 403)

    def test_health_identifies_loaded_agent_runtime_revision(self):
        """启动器可以识别加载了旧编排模块的服务，避免仅凭端口开放判定交付就绪。"""
        from backend.agent_graph import AGENT_RUNTIME_REVISION

        response = self.client.get('/api/v1/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['agent_orchestration']['runtime_revision'], AGENT_RUNTIME_REVISION)

    def test_missing_model_is_persisted_failure_without_fixed_fallback(self):
        run = self.run_agent()
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['error']['code'], 'MODEL_NOT_CONFIGURED')
        self.assertIsNone(run['output'])
        self.assertFalse(RoleModelFixture.calls)

    def test_four_roles_publish_only_after_all_reports_and_request_is_idempotent(self):
        self.configure()
        body, key = dict(context=self.view('red')['context']), str(uuid4())
        before = deepcopy(self.view('red')['units'])
        first = self.run_agent(key=key, body=body)
        second = self.run_agent(key=key, body=body)
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertEqual(RoleModelFixture.calls, Counter(commander=1, recon=1, ground=1, support=1))
        self.assertEqual(before, self.view('red')['units'])
        self.assertEqual(len(first['output']['reports']), 3)
        self.assertTrue(first['output']['requires_confirmation'])

    def test_native_checkpoints_survive_new_saver_instance(self):
        self.configure()
        run = self.run_agent()
        saver = RunCheckpointSaver(self.settings, run['run_id'])
        checkpoint = saver.get_tuple(dict(configurable=dict(thread_id=run['run_id'])))
        self.assertIn('commander', checkpoint.checkpoint['channel_values'])
        self.assertGreaterEqual(len(list(saver.list(checkpoint.config))), 3)

    def test_approval_is_explicit_atomic_and_idempotent(self):
        self.configure()
        run = self.run_agent()
        body = dict(context=self.view('red')['context'], command_type='review_agent_plan', payload=dict(run_id=run['run_id'], decision='approve'))
        key = str(uuid4())
        first = self.command('review_agent_plan', role='red', body=body, key=key)
        repeated = self.command('review_agent_plan', role='red', body=body, key=key)
        self.assertEqual(first['command_id'], repeated['command_id'])
        plan = self.view('red')['coordination']['plans']['red']
        self.assertEqual(plan['status'], 'adopted')
        self.assertEqual(plan['tasks'][0]['status'], 'queued')
        self.assertEqual(self.view('blue')['coordination']['plans'], {})

    def test_changed_basis_rejects_approval(self):
        self.configure()
        run = self.run_agent()
        self.command('set_ready', dict(ready=True), 'red')
        result = self.command('review_agent_plan', dict(run_id=run['run_id'], decision='approve'), 'red', expected=409)
        self.assertEqual(result['error']['code'], 'AGENT_BASIS_STALE')

    def test_cancelled_run_cannot_be_adopted(self):
        self.configure()
        run = self.run_agent()
        response = self.client.post(self.path + '/agents/runs/' + run['run_id'] + '/cancel', headers=self.headers['red'])
        self.assertEqual(response.json()['status'], 'cancelled')
        result = self.command('review_agent_plan', dict(run_id=run['run_id'], decision='approve'), 'red', expected=409)
        self.assertEqual(result['error']['code'], 'AGENT_PLAN_STALE')

    def test_graph_recovers_without_repeating_successful_role(self):
        self.configure()
        RoleModelFixture.fail_role = 'ground'
        RoleModelFixture.failure_gate = Event()
        saved_roles = set()
        original_save = self.agents._save_stage

        def save_stage(root_id, role, result, execution_id):
            """明确等待两个成功报告持久化后再制造失败，验证恢复的独立预期。"""
            original_save(root_id, role, result, execution_id)
            saved_roles.add(role)
            if {'recon', 'support'} <= saved_roles:
                RoleModelFixture.failure_gate.set()

        with patch.object(self.agents, '_save_stage', side_effect=save_stage):
            run = self.run_agent()
        self.assertEqual(run['status'], 'failed', run)
        successful = {role: RoleModelFixture.calls[role] for role in ('recon', 'support')}
        RoleModelFixture.fail_role = None
        RoleModelFixture.failure_gate = None
        response = self.client.post(self.path + '/agents/runs/' + run['run_id'] + '/resume', headers=self.headers['red'])
        self.assertEqual(response.status_code, 202, response.text)
        detail = self.client.get(self.path + '/agents/runs/' + run['run_id'], headers=self.headers['red']).json()
        self.assertEqual(detail['status'], 'waiting_human', detail)
        self.assertEqual({role: RoleModelFixture.calls[role] for role in successful}, successful)

    def test_unexpected_node_failure_is_recorded_without_exposing_details(self):
        """未知节点异常结束运行，不向接口泄漏异常原文，也不自动重试模型。"""
        self.configure()
        with patch.object(RoleModelFixture, 'analyze', side_effect=RuntimeError('private-provider-response')) as analyze:
            run = self.run_agent()
        requested_roles = [call.args[0] for call in analyze.call_args_list]
        self.assertEqual(len(requested_roles), len(set(requested_roles)))
        self.assertNotIn('commander', requested_roles)
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['error']['code'], 'AGENT_EXECUTION_FAILED')
        self.assertNotIn('private-provider-response', str(run))
        rows = self.client.get(self.path + '/agents/runs', headers=self.headers['red']).json()['items']
        self.assertFalse(any(row['status'] in ('queued', 'running') for row in rows))
        self.assertFalse(any(row['role'] == 'commander' and row['output'] for row in rows))

    def test_saved_commander_result_is_reused_after_checkpoint_failure(self):
        """总指挥结果落库后图节点异常，人工恢复不得重复请求任何已完成角色。"""
        self.configure()
        original_save = self.agents._save_stage

        def interrupt_after_save(root_id, role, result, execution_id):
            """模拟报告已保存、图节点尚未确认完成的中断窗口。"""
            original_save(root_id, role, result, execution_id)
            if role == 'commander':
                raise RuntimeError('private-checkpoint-failure')

        with patch.object(self.agents, '_save_stage', side_effect=interrupt_after_save):
            run = self.run_agent()
        self.assertEqual(run['status'], 'failed', run)
        self.assertNotIn('proposal_id', run['output'])
        completed_calls = dict(RoleModelFixture.calls)
        response = self.client.post(self.path + '/agents/runs/' + run['run_id'] + '/resume', headers=self.headers['red'])
        self.assertEqual(response.status_code, 202, response.text)
        detail = self.client.get(self.path + '/agents/runs/' + run['run_id'], headers=self.headers['red']).json()
        self.assertEqual(detail['status'], 'waiting_human', detail)
        self.assertEqual(dict(RoleModelFixture.calls), completed_calls)
        self.assertEqual(detail['output']['total_tokens'], 40)
        with self.settings.connect() as connection:
            proposals = connection.execute('SELECT id FROM proposals WHERE ai_run_id=%s', (run['run_id'],)).fetchall()
        self.assertEqual(len(proposals), 1)

    def test_accepted_tasks_and_score_survive_save_restore(self):
        self.configure()
        for role in ('red', 'blue'):
            self.command('set_ready', dict(ready=True), role)
        self.command('start_game')
        for role in ('red', 'blue'):
            self.configure(role)
            run = self.run_agent(role)
            self.assertEqual(run['status'], 'waiting_human', run)
            self.command('review_agent_plan', dict(run_id=run['run_id'], decision='approve'), role)
        self.command('advance_step')
        self.assertEqual(self.view('red')['coordination']['plans']['red']['tasks'][0]['status'], 'succeeded')
        saved = self.command('create_save', dict(display_name='协同恢复验证'))['result']['save_id']
        before = self.view('red')
        self.command('advance_step')
        self.command('restore_save', dict(save_id=saved))
        after = self.view('red')
        self.assertEqual(after['scores'], before['scores'])
        self.assertEqual(after['coordination']['plans'], before['coordination']['plans'])
        self.assertNotEqual(after['context']['branch_id'], before['context']['branch_id'])

    def test_real_movement_then_complete_capture_and_score(self):
        """模型仅给任务意图，实际地图移动、占领和分数来自 HTTP 单步结算。"""
        self.command('movement_order', dict(unit_id='R-G01', mode='deploy_start', position=dict(x='30', y='40')), 'red')
        for role in ('red', 'blue'):
            self.command('set_ready', dict(ready=True), role)
        self.command('start_game')
        self.configure()
        RoleModelFixture.plan_tasks = [dict(task_id='move-a', unit_id='R-G01', action='move', target_point_id='TASK_A'),
            dict(task_id='capture-a', unit_id='R-G01', action='capture', target_point_id='TASK_A', depends_on=['move-a'])]
        run = self.run_agent()
        self.assertEqual(run['status'], 'waiting_human', run)
        self.command('review_agent_plan', dict(run_id=run['run_id'], decision='approve'), 'red')
        baseline.CurrentFeatureTests.approve(self, 'blue')
        for _ in range(12):
            self.command('advance_step')
            current = self.view('red')
            if current['coordination']['points'][0]['owner'] == 'red':
                break
        self.assertEqual(current['coordination']['points'][0]['owner'], 'red', current['coordination']['plans'])
        self.assertEqual(current['scores']['red'], 0)
        self.command('advance_step')
        self.assertEqual(self.view('red')['scores']['red'], 1)

    def test_old_execution_cannot_overwrite_a_resumed_run(self):
        """恢复更换执行身份后，旧报告和检查点写入均被拒绝。"""
        self.configure()
        run = self.run_agent()
        with self.settings.connect() as connection:
            reference = connection.execute('SELECT checkpoint_ref FROM ai_runs WHERE id=%s', (run['run_id'],)).fetchone()['checkpoint_ref']
        from psycopg.types.json import Jsonb
        old_execution = reference['execution_id']
        reference['execution_id'] = str(uuid4())
        with self.settings.connect() as connection:
            connection.execute("UPDATE ai_runs SET status='running',checkpoint_ref=%s WHERE id=%s", (Jsonb(reference), run['run_id']))
        with self.assertRaises(ServiceError) as caught:
            self.agents._save_stage(run['run_id'], 'recon', {}, old_execution)
        self.assertEqual(caught.exception.code, 'AGENT_EXECUTION_STALE')
        saver = RunCheckpointSaver(self.settings, run['run_id'], old_execution)
        checkpoint = saver.get_tuple(dict(configurable=dict(thread_id=run['run_id'])))
        with self.assertRaises(ServiceError):
            saver.put_writes(checkpoint.config, [('recon', {})], 'old-node')


if __name__ == '__main__':
    unittest.main()
