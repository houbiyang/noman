"""开战检查：真实协议替身、故障分类、同意计费及失败关闭；不调用外部AI。"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from pydantic import ValidationError

from backend.agent_contracts import AgentConfiguration
from backend.battle_contracts import BattlePreflightRequest
from backend.battle_preflight import BattlePreflight, check, summarize
from backend.retrieval_models import RetrievalModels
from backend.runtime import ServiceError
from backend.tests.test_model_connections import configuration, mock_response


class PreflightPolicyTests(unittest.TestCase):
    def test_strict_consent_and_mode(self):
        for body in ({'mode': 'bad'}, {'mode': 'manual', 'probe_ai': True},
                     {'mode': 'manual', 'probe_ai': 'true', 'acknowledge_cost': True},
                     {'mode': 'manual', 'acknowledge_cost': 1}, {'mode': 'agent', 'key': 'secret'}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                BattlePreflightRequest.model_validate(body)
        self.assertFalse(BattlePreflightRequest(mode='manual').probe_ai)

    def test_only_ai_failure_can_offer_manual(self):
        checks = [check('db', 'core', 'DB', True, ''), check('ai', 'ai', 'AI', False, '')]
        self.assertTrue(summarize(checks, 'manual')['ready'])
        self.assertTrue(summarize(checks, 'agent')['manual_fallback_available'])
        self.assertFalse(summarize(checks, 'agent')['ready'])
        for category in ('db', 'map', 'engine'):
            blocked = checks + [check(category, 'core', category, False, '')]
            self.assertFalse(summarize(blocked, 'manual')['ready'])
            self.assertFalse(summarize(blocked, 'agent')['manual_fallback_available'])
        self.assertFalse(summarize(checks, 'manual')['can_start'])
        self.assertFalse(summarize([check('ai', 'ai', 'AI', True, '')], 'manual')['ready'])

    def test_untested_is_not_connected_or_ai_failure(self):
        checks = [check('db', 'core', 'DB', True, ''), check('ai', 'ai', 'AI', False, '', status='not_tested')]
        result = summarize(checks, 'agent')
        self.assertFalse(result['ready'])
        self.assertFalse(result['manual_fallback_available'])


class PreflightProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setup_service(self, config=None, handler=mock_response):
        self.calls = []
        def receive(request):
            self.calls.append(request)
            return handler(request)
        agents = SimpleNamespace(config_root=Path('/isolated-preflight'), load_configuration=lambda side: config or configuration())
        service = BattlePreflight(None, agents, RetrievalModels(transport=httpx.MockTransport(receive)))
        service._local = lambda game, token: [check('engine', 'core', '执行器', False, '未接入')]
        return service

    async def test_six_actual_protocols_and_no_private_details(self):
        service = self.setup_service()
        result = await service.run('game', 'token', BattlePreflightRequest(mode='agent', probe_ai=True, acknowledge_cost=True))
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(sum(c['status'] == 'passed' for c in result['checks']), 6)
        self.assertTrue(result['read_only'])
        self.assertFalse(result['ready'])
        self.assertFalse(result['can_start'])
        self.assertNotIn('12345', str(result))
        self.assertNotIn('message-fixture', str(result))
        self.assertNotIn('configuration', str(result))

    async def test_slow_real_protocols_sequence_per_side_without_busy_failures(self):
        calls = []
        async def receive(request):
            calls.append(request)
            await asyncio.sleep(0.002)
            return mock_response(request)
        service = self.setup_service()
        service.models = RetrievalModels(transport=httpx.MockTransport(receive))
        result = await service.run('game', 'token', BattlePreflightRequest(mode='agent', probe_ai=True, acknowledge_cost=True))
        self.assertEqual(len(calls), 6)
        self.assertEqual(sum(c['status'] == 'passed' for c in result['checks']), 6)

    async def test_no_consent_means_zero_network_not_fake_success(self):
        service = self.setup_service()
        result = await service.run('game', 'token', BattlePreflightRequest(mode='manual'))
        self.assertEqual(self.calls, [])
        self.assertEqual(sum(c['status'] == 'not_tested' for c in result['checks']), 6)

    async def test_unconfigured_and_optional_disabled(self):
        service = self.setup_service(AgentConfiguration())
        result = await service.run('game', 'token', BattlePreflightRequest(mode='manual', probe_ai=True, acknowledge_cost=True))
        ai = [c for c in result['checks'] if c['category'] == 'ai']
        self.assertEqual(sum(c['status'] == 'failed' for c in ai), 2)
        self.assertEqual(sum(c['status'] == 'not_required' for c in ai), 4)
        self.assertEqual(self.calls, [])
        self.assertFalse(result['manual_fallback_available'])  # 引擎未接入，不是“仅AI故障”。

    async def test_http_failure_is_redacted_and_not_retried(self):
        service = self.setup_service(handler=lambda req: httpx.Response(401, text='PRIVATE_ENDPOINT_ERROR'))
        result = await service.run('game', 'token', BattlePreflightRequest(mode='agent', probe_ai=True, acknowledge_cost=True))
        self.assertEqual(len(self.calls), 6)
        self.assertNotIn('PRIVATE_ENDPOINT_ERROR', str(result))
        self.assertTrue(all(c['status'] == 'failed' for c in result['checks']))

    async def test_timeout_is_failed_and_no_retry(self):
        service = self.setup_service()
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        service.models.test = AsyncMock(side_effect=slow)
        real_timeout = asyncio.timeout
        with patch('backend.battle_preflight.asyncio.timeout', side_effect=lambda seconds: real_timeout(0.001)):
            result = await service.run('game', 'token', BattlePreflightRequest(mode='manual', probe_ai=True, acknowledge_cost=True))
        self.assertEqual(service.models.test.await_count, 6)
        self.assertTrue(all(c['status'] == 'failed' for c in result['checks']))

    async def test_reauthorize_after_network_discards_revoked_result(self):
        service = self.setup_service()
        calls = []
        def authorize(game, token):
            calls.append(True)
            if len(calls) > 1:
                raise ServiceError(401, 'SESSION_UNAUTHORIZED', '会话已撤销')
            return [check('engine', 'core', '执行器', False, '')]
        service._local = authorize
        with self.assertRaises(ServiceError) as caught:
            await service.run('game', 'token', BattlePreflightRequest(mode='agent', probe_ai=True, acknowledge_cost=True))
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(len(self.calls), 6)

    async def test_corrupt_configuration_is_closed_without_leaking(self):
        service = self.setup_service()
        def bad(side):
            raise ValueError('private path / secret')
        service.agents.load_configuration = bad
        result = await service.run('game', 'token', BattlePreflightRequest(mode='manual'))
        self.assertEqual(self.calls, [])
        self.assertNotIn('secret', str(result))
        self.assertEqual(sum(c['category'] == 'ai' and c['status'] == 'failed' for c in result['checks']), 2)


class PreflightReadinessTests(unittest.TestCase):
    """只测只读门禁；合成通过分支不是地图发布或可玩验收。"""
    def schema_row(self):
        from backend.tests.test_migration_compatibility import MIGRATIONS, VERSION, command_names
        names = sorted(command_names((MIGRATIONS / (VERSION + '.sql')).read_text()))
        expression = 'CHECK ((command_type = ANY (ARRAY[' + ', '.join("'" + name + "'::text" for name in names) + '])))'
        return dict(convalidated=True, expression=expression)

    def test_validated_union_schema_passes_without_write_probe(self):
        from backend.battle_preflight import command_schema_check
        self.assertEqual(command_schema_check(self.schema_row())['status'], 'passed')

    def test_missing_narrowed_or_unvalidated_schema_is_closed(self):
        from backend.battle_preflight import command_schema_check
        row = self.schema_row()
        cases = [None, dict(row, convalidated=False),
                 dict(row, expression=row['expression'].replace("'battle_resolve'::text, ", ''))]
        for row in cases:
            with self.subTest(row=row):
                self.assertEqual(command_schema_check(row)['status'], 'failed')

    def test_schema_does_not_trust_names_embedded_in_other_expressions(self):
        from backend.battle_preflight import command_schema_check
        row = self.schema_row()
        for expression in ('CHECK (true)', row['expression'][:-1] + ' OR true)',
                           row['expression'][:-1] + ' AND false)', "CHECK (command_type IN ('battle_resolve'))"):
            self.assertEqual(command_schema_check(dict(row, expression=expression))['status'], 'failed')

    def test_engine_missing_or_legacy_state_does_not_pass(self):
        from backend.battle_preflight import engine_check
        from backend.tests.test_manual_battle import state_fixture
        from backend.demo_domain import initial_state
        self.assertEqual(engine_check(None, state_fixture())['status'], 'failed')
        supports = Mock(return_value=True)
        self.assertEqual(engine_check(SimpleNamespace(supports=supports), initial_state('old', 'old', 'patrol'))['status'], 'failed')
        supports.assert_not_called()

    def test_engine_checks_a_copy_without_mutating_state(self):
        from copy import deepcopy
        from backend.battle_preflight import engine_check
        from backend.tests.test_manual_battle import state_fixture
        state = state_fixture()
        before = deepcopy(state)
        def supports(copy):
            copy['units'].clear()
            return True
        self.assertEqual(engine_check(SimpleNamespace(supports=supports), state)['status'], 'passed')
        self.assertEqual(state, before)

    def test_engine_failure_is_redacted_and_nonboolean_is_not_ready(self):
        from backend.battle_preflight import engine_check
        from backend.tests.test_manual_battle import state_fixture
        for result in (False, 1, 'true', None):
            self.assertEqual(engine_check(SimpleNamespace(supports=lambda state: result), state_fixture())['status'], 'failed')
        for error in (ValueError('private-source'), RuntimeError('private-source'), ServiceError(503, 'PRIVATE', 'private-source')):
            with self.subTest(error=type(error).__name__):
                def bad(state):
                    raise error
                output = engine_check(SimpleNamespace(supports=bad), state_fixture())
                self.assertEqual(output['status'], 'failed')
                self.assertNotIn('private-source', str(output))

    def bound_fixture(self, *, accepted=False):
        from copy import deepcopy
        from backend import battle_scenario, rulebook
        from backend.runtime import sha256
        from backend.tests.test_manual_battle import state_fixture
        value, rules = battle_scenario.definition(), rulebook.definition()
        if accepted:
            value['acceptance'] = dict(status='accepted', blocks=[], structural_checks_passed=True,
                terrain_masks_validated=True, ground_and_support_routes_validated=True,
                bridge_actual_grade_validated=True, service_surface_isolation_validated=True)
        state = state_fixture()
        scenario_id, rule_id = battle_scenario.identity(value), rulebook.identity(rules)
        state['manifest'].update(scenario_version_id=scenario_id, rule_version_id=rule_id,
            battle_scenario_sha256=sha256(value), battle_rule_sha256=sha256(rules))
        game = dict(state_payload=state, scenario_version_id=scenario_id, rule_version_id=rule_id)
        row = dict(definition=deepcopy(value), payload_sha256=sha256(value), availability='active' if accepted else 'draft',
            rule_definition=deepcopy(rules), rule_payload_sha256=sha256(rules), rule_availability='active')
        return game, row, value

    def test_old_game_or_missing_binding_does_not_pass(self):
        from backend.battle_preflight import scenario_check
        from backend.demo_domain import initial_state
        self.assertEqual(scenario_check(dict(state_payload=initial_state('old', 'old', 'patrol')), None)['status'], 'failed')
        game, row, _ = self.bound_fixture()
        self.assertEqual(scenario_check(game, None)['status'], 'failed')

    def test_matching_draft_is_not_a_published_scenario(self):
        from backend.battle_preflight import scenario_check
        game, row, _ = self.bound_fixture()
        result = scenario_check(game, row)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('草案', result['message'])
        # 即使管理员单独改availability，也不能把未验收来源伪发布。
        row['availability'] = 'active'
        self.assertEqual(scenario_check(game, row)['status'], 'failed')

    def test_accepted_fixture_requires_matching_content_and_frozen_ids(self):
        from copy import deepcopy
        from backend import battle_scenario
        from backend.battle_preflight import scenario_check
        game, row, value = self.bound_fixture(accepted=True)
        with patch.object(battle_scenario, 'definition', return_value=value):
            self.assertEqual(scenario_check(game, row)['status'], 'passed')
            for field in ('scenario_version_id', 'rule_version_id'):
                altered = deepcopy(game)
                altered[field] = 'not-the-frozen-version'
                self.assertEqual(scenario_check(altered, row)['status'], 'failed')
            for field in ('battle_scenario_sha256', 'battle_rule_sha256'):
                altered = deepcopy(game)
                altered['state_payload']['manifest'][field] = '0' * 64
                self.assertEqual(scenario_check(altered, row)['status'], 'failed')
            for field in ('payload_sha256', 'rule_payload_sha256'):
                altered = dict(row, **{field: '0' * 64})
                self.assertEqual(scenario_check(game, altered)['status'], 'failed')
            for field in ('definition', 'rule_definition'):
                altered = dict(row, **{field: {}})
                self.assertEqual(scenario_check(game, altered)['status'], 'failed')
            for field in ('availability', 'rule_availability'):
                self.assertEqual(scenario_check(game, dict(row, **{field: 'draft'}))['status'], 'failed')

    def test_unaccepted_or_blocked_surfaces_do_not_pass(self):
        from backend import battle_scenario
        from backend.battle_preflight import scenario_check
        # 重建ID/摘要以匹配变更后的夹具，确保拒绝源自验收门禁而非摘要错误。
        for field in ('terrain_masks_validated', 'ground_and_support_routes_validated',
                      'bridge_actual_grade_validated', 'service_surface_isolation_validated', 'blocks'):
            game, row, value = self.bound_fixture(accepted=True)
            value['acceptance'][field] = ['pending'] if field == 'blocks' else False
            from backend.runtime import sha256
            new_id = battle_scenario.identity(value)
            game['scenario_version_id'] = game['state_payload']['manifest']['scenario_version_id'] = new_id
            game['state_payload']['manifest']['battle_scenario_sha256'] = sha256(value)
            row.update(definition=value, payload_sha256=sha256(value))
            with patch.object(battle_scenario, 'definition', return_value=value):
                self.assertEqual(scenario_check(game, row)['status'], 'failed')

    def test_scenario_source_errors_do_not_leak(self):
        from backend import battle_scenario
        from backend.battle_preflight import scenario_check
        game, row, _ = self.bound_fixture()
        with patch.object(battle_scenario, 'definition', side_effect=ValueError('private-source-path')):
            result = scenario_check(game, row)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('private-source-path', str(result))


class SetupAcceptanceTests(unittest.TestCase):
    def test_current_scene_reports_each_pending_dependency_without_writes(self):
        from backend import battle_scenario
        from backend.battle_preflight import setup_acceptance_checks
        checks = setup_acceptance_checks(battle_scenario.definition(), None)
        self.assertEqual(len(checks), 6)
        self.assertTrue(all(c['status'] != 'passed' for c in checks))
        self.assertEqual(checks[-1]['status'], 'not_tested')
        self.assertIn('不自动修桥', checks[2]['message'])

    def test_installed_object_is_not_execution_acceptance(self):
        from backend.battle_preflight import setup_acceptance_checks
        class Untouched:
            def supports(self, state):
                raise AssertionError('cannot validate a fabricated state')
        checks = setup_acceptance_checks({}, Untouched())
        self.assertEqual(checks[-2]['status'], 'not_tested')
        self.assertTrue(all(c['status'] != 'passed' for c in checks))

    def test_only_literal_source_acceptance_is_passed(self):
        from backend.battle_preflight import setup_acceptance_checks
        for value in ('true', 1, False, None):
            checks = setup_acceptance_checks({'acceptance': {'terrain_masks_validated': value}}, None)
            self.assertEqual(checks[0]['status'], 'failed')
        checks = setup_acceptance_checks({'acceptance': {'terrain_masks_validated': True}}, None)
        self.assertEqual(checks[0]['status'], 'passed')
        self.assertNotEqual(checks[-1]['status'], 'passed')
