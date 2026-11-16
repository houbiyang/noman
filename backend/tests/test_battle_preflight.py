"""开战检查：真实协议替身、故障分类、同意计费及失败关闭；不调用外部AI。"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

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
