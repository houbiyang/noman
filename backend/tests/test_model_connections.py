"""三类模型全链路测试：本地替身，不访问外部接口或用户对局。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from backend.agent_contracts import AgentConfiguration, ModelConnectionTest
from backend.retrieval_models import RetrievalModels
from backend.runtime import ServiceError
from backend.tests import test_agent_service as fixtures

RoleModelFixture = fixtures.RoleModelFixture


def configuration():
    return AgentConfiguration.model_validate(dict(
        model=dict(enabled=True, base_url='http://127.0.0.1:12345/v1', model='message-fixture', credential_ref=''),
        embedding=dict(enabled=True, base_url='http://127.0.0.1:12345/v1', model='embedding-fixture', dimensions=2),
        rerank=dict(enabled=True, base_url='http://127.0.0.1:12345/v1', model='rerank-fixture', top_n=2)))


def mock_response(request):
    data = json.loads(request.content)
    if request.url.path.endswith('/chat/completions'):
        return httpx.Response(200, json=dict(choices=[dict(message=dict(role='assistant', content='OK'), finish_reason='stop')]))
    if request.url.path.endswith('/embeddings'):
        return httpx.Response(200, json=dict(data=[dict(index=i, embedding=[1., float(i + 1)]) for i in range(len(data['input']))]))
    if request.url.path.endswith('/rerank'):
        return httpx.Response(200, json=dict(results=[dict(index=i, relevance_score=1 / (i + 1),
            document=dict(text='UNTRUSTED_UPSTREAM_TEXT')) for i in range(data['top_n'])]))
    raise AssertionError('unexpected model endpoint')


class ModelProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_actual_protocol_requests_do_not_save(self):
        requests = []
        def handler(request):
            requests.append(request)
            return mock_response(request)
        models = RetrievalModels(transport=httpx.MockTransport(handler))
        for kind in ('model', 'embedding', 'rerank'):
            result = await models.test('red', ModelConnectionTest(kind=kind, configuration=getattr(configuration(), kind), acknowledge_cost=True))
            self.assertTrue(result['success'])
            self.assertFalse(result['saved'])
        self.assertEqual(len(requests), 3)
        self.assertEqual(json.loads(requests[0].content)['max_tokens'], 256)
        self.assertNotIn('tools', json.loads(requests[0].content))

    async def test_vector_query_cache_invalidated_by_side_config_and_credentials(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return mock_response(request)
        models = RetrievalModels(transport=httpx.MockTransport(handler))
        config = configuration()
        principal = dict(side='red', active_game_rules='conquest-v1')
        result = await models.query(principal, '占领规则', config.embedding.knowledge_version, config)
        self.assertEqual(result['source'], 'configured_embedding')
        self.assertTrue(result['reranked'])
        self.assertEqual(len(result['evidence']), 2)
        self.assertFalse(result['execution_authorized'])
        self.assertNotIn('UNTRUSTED_UPSTREAM_TEXT', json.dumps(result))
        corpus_calls = sum('input' in c and len(c['input']) > 1 for c in calls)
        await models.query(principal, '占领规则', config.embedding.knowledge_version, config)
        self.assertEqual(sum('input' in c and len(c['input']) > 1 for c in calls), corpus_calls)
        await models.query(dict(principal, side='blue'), '占领规则', config.embedding.knowledge_version, config)
        self.assertGreater(sum('input' in c and len(c['input']) > 1 for c in calls), corpus_calls)
        count = len(models._cache)
        config.embedding.model = 'changed-model'
        await models.query(principal, '占领规则', config.embedding.knowledge_version, config)
        self.assertEqual(len(models._cache), count + 1)
        config.embedding.credential_ref = 'FIXTURE_EMBEDDING_KEY'
        with patch.dict('os.environ', {'FIXTURE_EMBEDDING_KEY': 'one'}):
            await models.query(principal, '占领规则', config.embedding.knowledge_version, config)
        count = len(models._cache)
        with patch.dict('os.environ', {'FIXTURE_EMBEDDING_KEY': 'two'}):
            await models.query(principal, '占领规则', config.embedding.knowledge_version, config)
        self.assertEqual(len(models._cache), count + 1)

    async def test_embeddings_reject_malformed_results(self):
        for data in ([], [dict(index=0, embedding=[1])], [dict(index=0, embedding=[True, 1])],
                     [dict(index=0, embedding=[0, 0])], [dict(index=1, embedding=[1, 2])],
                     [dict(index=0, embedding=[float('inf'), 1])]):
            # NaN/inf cannot be emitted with httpx's strict JSON encoder.
            raw = json.dumps(dict(data=data)).encode()
            model = RetrievalModels(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=raw)))
            with self.assertRaises(ServiceError):
                await model.embed(configuration().embedding, ['test'])
        duplicate = dict(data=[dict(index=0, embedding=[1, 2])] * 2)
        model = RetrievalModels(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=duplicate)))
        with self.assertRaises(ServiceError):
            await model.embed(configuration().embedding, ['one', 'two'])

    async def test_rerank_rejects_missing_duplicate_or_out_of_range_results(self):
        for records in ([], [dict(index=0, relevance_score=1)] * 2,
                        [dict(index=0, relevance_score=1), dict(index=2, relevance_score=0)],
                        [dict(index=0, relevance_score='secret'), dict(index=1, relevance_score=0)]):
            models = RetrievalModels(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=dict(results=records))))
            with self.assertRaises(ServiceError):
                await models.rerank(configuration().rerank, 'test', [dict(text='a'), dict(text='b')])

    async def test_http_errors_redirect_timeout_and_secret_redaction_no_retry(self):
        for status in (301, 302, 401, 403, 429, 500):
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(status, headers={'Location': 'http://127.0.0.1:9/leak'}, text='SECRET_API_KEY')
            models = RetrievalModels(transport=httpx.MockTransport(handler))
            with self.assertRaises(ServiceError) as caught:
                await models.embed(configuration().embedding, ['test'])
            self.assertNotIn('SECRET', caught.exception.message)
            self.assertEqual(len(requests), 1)
        def timeout(request):
            raise httpx.ReadTimeout('secret upstream URL', request=request)
        with self.assertRaises(ServiceError) as caught:
            await RetrievalModels(transport=httpx.MockTransport(timeout)).embed(configuration().embedding, ['test'])
        self.assertEqual(caught.exception.code, 'MODEL_TIMEOUT')
        self.assertNotIn('secret', caught.exception.message)

    async def test_size_bad_json_missing_key_and_truncated_message(self):
        for raw in (b'not json', b'[]', b'x' * 4_000_001):
            models = RetrievalModels(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=raw)))
            with self.assertRaises(ServiceError):
                await models.embed(configuration().embedding, ['test'])
        config = configuration()
        config.embedding.credential_ref = 'MISSING_FIXTURE_KEY'
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(ServiceError) as caught:
            await RetrievalModels().embed(config.embedding, ['test'])
        self.assertEqual(caught.exception.code, 'MODEL_NOT_CONFIGURED')
        for body in (dict(choices=[]), dict(choices=[dict(message=dict(content=''), finish_reason='stop')]),
                     dict(choices=[dict(message=dict(content='OK'), finish_reason='length')])):
            models = RetrievalModels(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
            with self.assertRaises(ServiceError):
                await models.test('red', ModelConnectionTest(kind='model', configuration=configuration().model, acknowledge_cost=True))

    async def test_busy_test_is_not_billed_twice_and_recovers(self):
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def handler(request):
            calls.append(request)
            started.set()
            await finish.wait()
            return mock_response(request)
        models = RetrievalModels(transport=httpx.MockTransport(handler))
        body = ModelConnectionTest(kind='embedding', configuration=configuration().embedding, acknowledge_cost=True)
        first = asyncio.create_task(models.test('red', body))
        await started.wait()
        with self.assertRaises(ServiceError) as caught:
            await models.test('red', body)
        self.assertEqual(caught.exception.code, 'MODEL_TEST_BUSY')
        finish.set()
        await first
        await models.test('red', body)
        self.assertEqual(len(calls), 2)

    def test_old_configs_and_strict_safe_validation(self):
        old = AgentConfiguration.model_validate(dict(model=dict(model='old')))
        self.assertFalse(old.embedding.enabled)
        self.assertFalse(old.rerank.enabled)
        for data in (dict(embedding=dict(enabled=True)), dict(embedding=dict(dimensions=0)),
                     dict(rerank=dict(top_n=21)), dict(embedding=dict(base_url='http://example.com/v1')),
                     dict(rerank=dict(base_url='https://user:password@example.com/v1')),
                     dict(embedding=dict(base_url='https://example.com/v1?key=secret')),
                     dict(rerank=dict(credential_ref='actual-secret-value'))):
            with self.assertRaises(ValidationError):
                AgentConfiguration.model_validate(data)
        with self.assertRaises(ValidationError):
            ModelConnectionTest(kind='model', configuration=old.model, acknowledge_cost=False)


class ModelApiTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AgentServiceTests.setUpClass.__func__)
    cleanup_database = classmethod(fixtures.AgentServiceTests.cleanup_database.__func__)
    setUp = fixtures.AgentServiceTests.setUp
    view = fixtures.AgentServiceTests.view
    command = fixtures.AgentServiceTests.command
    run_agent = fixtures.AgentServiceTests.run_agent

    def test_save_and_test_are_side_isolated_and_never_change_game(self):
        self.agents.models.transport = httpx.MockTransport(mock_response)
        config = configuration().model_dump()
        before = deepcopy(self.view('red'))
        before.pop('observed_at', None)
        for side in ('red', 'blue'):
            draft = deepcopy(config)
            draft['embedding']['model'] = side + '-embed'
            response = self.client.post(self.path + '/agents/configuration', headers=self.headers[side], json=draft)
            self.assertEqual(response.status_code, 200, response.text)
        for kind in ('model', 'embedding', 'rerank'):
            response = self.client.post(self.path + '/agents/models/test', headers=self.headers['red'],
                json=dict(kind=kind, configuration=config[kind], acknowledge_cost=True))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['saved'])
        for side in ('red', 'blue'):
            data = self.client.get(self.path + '/agents/configuration', headers=self.headers[side]).json()
            self.assertEqual(data['configuration']['embedding']['model'], side + '-embed')
            self.assertEqual(json.loads((self.agents.config_root / (side + '.json')).read_text())['rerank']['top_n'], 2)
        after = self.view('red')
        after.pop('observed_at', None)
        self.assertEqual(after, before)
        self.assertNotIn('api_key', (self.agents.config_root / 'red.json').read_text().lower())

    def test_auth_and_consent_precede_network_calls(self):
        calls = []
        def handler(request):
            calls.append(request)
            return mock_response(request)
        self.agents.models.transport = httpx.MockTransport(handler)
        body = dict(kind='embedding', configuration=configuration().embedding.model_dump(), acknowledge_cost=True)
        for headers, code in (({}, 401), ({'Authorization': 'Bearer invalid'}, 401), (self.headers['host'], 403)):
            response = self.client.post(self.path + '/agents/models/test', headers=headers, json=body)
            self.assertEqual(response.status_code, code, response.text)
        for change in (dict(acknowledge_cost=False), dict(kind='unknown'), dict(side='blue')):
            response = self.client.post(self.path + '/agents/models/test', headers=self.headers['red'], json=dict(body, **change))
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(calls, [])

    def test_knowledge_api_and_four_roles_use_saved_models_and_checkpoint_evidence(self):
        self.agents.models.transport = httpx.MockTransport(mock_response)
        response = self.client.post(self.path + '/agents/configuration', headers=self.headers['red'], json=configuration().model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        result = self.client.post(self.path + '/knowledge/query', headers=self.headers['red'],
            json=dict(question='占领规则有哪些', knowledge_version='conquest-v1-20261110'))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['reranked'])
        inputs = {}
        class CaptureModel(RoleModelFixture):
            def analyze(self, role, frozen_input, *args):
                inputs[role] = frozen_input
                return super().analyze(role, frozen_input, *args)
        self.agents.model_factory = CaptureModel
        before = self.view('red')['units']
        run = self.run_agent()
        self.assertEqual(run['status'], 'waiting_human', run)
        self.assertEqual(set(inputs), {'recon', 'ground', 'support', 'commander'})
        self.assertTrue(all('knowledge_evidence' in value for value in inputs.values()))
        self.assertTrue(run['knowledge']['external_retrieval'])
        self.assertEqual(self.view('red')['units'], before)
        self.assertEqual(self.client.get(self.path + '/agents/runs/' + run['run_id'], headers=self.headers['blue']).status_code, 404)

    def test_resume_reuses_frozen_retrieval_without_another_provider_call(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return mock_response(request)
        self.agents.models.transport = httpx.MockTransport(handler)
        self.client.post(self.path + '/agents/configuration', headers=self.headers['red'], json=configuration().model_dump())
        RoleModelFixture.fail_role = 'ground'
        run = self.run_agent()
        self.assertEqual(run['status'], 'failed', run)
        self.assertTrue(run['knowledge']['retrieval']['evidence'])
        retrieval = deepcopy(run['knowledge']['retrieval'])
        first_calls = list(calls)
        self.assertIn('/v1/rerank', first_calls)
        # 清空缓存并更改当前配置，恢复仍只能复用该次运行冻结的证据。
        self.agents.models._cache.clear()
        changed = configuration().model_dump()
        changed['embedding']['model'] = 'different-current-model'
        self.client.post(self.path + '/agents/configuration', headers=self.headers['red'], json=changed)
        RoleModelFixture.fail_role = None
        response = self.client.post(self.path + '/agents/runs/' + run['run_id'] + '/resume', headers=self.headers['red'])
        self.assertEqual(response.status_code, 202, response.text)
        detail = self.client.get(self.path + '/agents/runs/' + run['run_id'], headers=self.headers['red']).json()
        self.assertEqual(detail['status'], 'waiting_human', detail)
        self.assertEqual(detail['knowledge']['retrieval'], retrieval)
        self.assertEqual(calls, first_calls)

    def test_embedding_failure_stops_planning_without_fake_evidence(self):
        self.agents.models.transport = httpx.MockTransport(lambda r: httpx.Response(401, text='secret provider'))
        self.client.post(self.path + '/agents/configuration', headers=self.headers['red'], json=configuration().model_dump())
        before = self.view('red')['units']
        run = self.run_agent()
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['error']['code'], 'MODEL_HTTP_ERROR')
        self.assertNotIn('secret', run['error']['message'])
        self.assertFalse(RoleModelFixture.calls)
        self.assertEqual(before, self.view('red')['units'])
