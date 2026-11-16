"""有界模型测试与冻结白名单向量检索；无自动重试、重定向或明文密钥落盘。"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import json
import math
import os
from pathlib import Path
from threading import Lock
import time
from urllib.parse import urlsplit

import httpx

from backend.demo_domain import require
from backend.knowledge import ROOT, load_corpus
from backend.runtime import ServiceError


def endpoint_ready(model):
    local = urlsplit(model.base_url).hostname in ('localhost', '127.0.0.1', '::1')
    return bool(model.base_url and model.model.strip() and
                (os.environ.get(model.credential_ref) if model.credential_ref else local))


class RetrievalModels:
    """同一服务供工作台独立测试、知识查询和四角色规划复用。"""

    def __init__(self, root: Path = ROOT, transport=None):
        self.root, self.transport = root, transport
        self._cache, self._cache_lock = OrderedDict(), Lock()
        self._testing, self._test_lock = set(), Lock()

    async def post(self, model, suffix, payload):
        require(endpoint_ready(model), 'MODEL_NOT_CONFIGURED', '模型地址、标识或服务端密钥未就绪', 503)
        headers = {'Content-Type': 'application/json'}
        if model.credential_ref:
            headers['Authorization'] = 'Bearer ' + os.environ[model.credential_ref]
        try:
            # 总时限不仅限制单次读取，禁止慢速持续响应无限占用连接。
            async with asyncio.timeout(model.timeout_seconds):
                async with httpx.AsyncClient(timeout=model.timeout_seconds, trust_env=False,
                        follow_redirects=False, transport=self.transport) as client:
                    async with client.stream('POST', model.base_url + suffix, headers=headers, json=payload) as response:
                        if not 200 <= response.status_code < 300:
                            raise ServiceError(502, 'MODEL_HTTP_ERROR',
                                f'模型接口返回 HTTP {response.status_code}；未自动重试，远端用量可能已产生')
                        raw = bytearray()
                        async for part in response.aiter_bytes():
                            raw.extend(part)
                            require(len(raw) <= 4_000_000, 'MODEL_RESPONSE_TOO_LARGE', '模型响应超过限制', 502)
            result = json.loads(raw)
            require(isinstance(result, dict), 'MODEL_RESPONSE_INVALID', '模型响应必须为 JSON 对象', 502)
            return result
        except (TimeoutError, httpx.TimeoutException) as error:
            raise ServiceError(504, 'MODEL_TIMEOUT', '模型请求超时；未自动重试，远端用量未知') from error
        except (httpx.HTTPError, OSError) as error:
            raise ServiceError(503, 'MODEL_REQUEST_FAILED', '模型连接失败；未自动重试，远端用量未知') from error
        except (ValueError, UnicodeError) as error:
            raise ServiceError(502, 'MODEL_RESPONSE_INVALID', '模型响应不是有效 JSON') from error

    async def embed(self, model, texts):
        require(0 < len(texts) <= 16 and all(isinstance(t, str) and 0 < len(t) <= 800 for t in texts),
                'MODEL_INPUT_TOO_LARGE', '向量请求必须为 1 至 16 个有界片段', 422)
        result = await self.post(model, '/embeddings', dict(model=model.model, input=texts, encoding_format='float'))
        data = result.get('data')
        require(isinstance(data, list) and len(data) == len(texts), 'MODEL_RESPONSE_INVALID', '向量条数与输入不一致', 502)
        vectors = [None] * len(texts)
        for item in data:
            require(isinstance(item, dict), 'MODEL_RESPONSE_INVALID', '向量记录格式无效', 502)
            index, vector = item.get('index'), item.get('embedding')
            require(type(index) is int and 0 <= index < len(texts) and vectors[index] is None,
                    'MODEL_RESPONSE_INVALID', '向量索引缺失、重复或越界', 502)
            require(isinstance(vector, list) and len(vector) == model.dimensions,
                    'EMBEDDING_DIMENSION_MISMATCH', '服务返回的向量维度与配置不一致；未复用缓存', 502)
            require(all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e100 for v in vector),
                    'MODEL_RESPONSE_INVALID', '向量包含非有限或无效数值', 502)
            norm = math.sqrt(sum(v * v for v in vector))
            require(norm > 0, 'MODEL_RESPONSE_INVALID', '服务返回零向量', 502)
            vectors[index] = [v / norm for v in vector]
        return vectors

    async def rerank(self, model, question, evidence):
        if not model.enabled or not evidence:
            return evidence
        result = await self.post(model, '/rerank', dict(model=model.model, query=question,
            documents=[e['text'] for e in evidence], top_n=min(model.top_n, len(evidence)), return_documents=False))
        records = result.get('results')
        require(isinstance(records, list) and len(records) == min(model.top_n, len(evidence)),
                'MODEL_RESPONSE_INVALID', '重排序结果条数不正确', 502)
        ranked, seen = [], set()
        for item in records:
            require(isinstance(item, dict), 'MODEL_RESPONSE_INVALID', '重排序记录无效', 502)
            index, score = item.get('index'), item.get('relevance_score')
            require(type(index) is int and 0 <= index < len(evidence) and index not in seen and
                    type(score) in (int, float) and math.isfinite(score),
                    'MODEL_RESPONSE_INVALID', '重排序索引或分数无效', 502)
            seen.add(index)
            # 永远从本方已验证证据取文，忽略供应商返回的文本或来源。
            ranked.append(dict(evidence[index], rerank_score=score))
        return sorted(ranked, key=lambda e: e['rerank_score'], reverse=True)

    async def test(self, side, request, scope='default'):
        key = (scope, side)
        with self._test_lock:
            require(key not in self._testing, 'MODEL_TEST_BUSY', '本方已有模型测试进行中，请勿重复计费请求', 409)
            self._testing.add(key)
        started = time.monotonic()
        try:
            model = request.configuration
            if request.kind == 'embedding':
                await self.embed(model, ['connection test'])
                details = dict(dimensions=model.dimensions)
            elif request.kind == 'rerank':
                # 禁用状态也可测试草稿，但不隐式启用或保存。
                draft = model.model_copy(update={'enabled': True})
                ranked = await self.rerank(draft, 'connection test', [dict(text='connection test'), dict(text='other')])
                details = dict(returned_results=len(ranked))
            else:
                payload = dict(model=model.model, messages=[dict(role='user', content='Reply OK.')])
                payload[model.output_token_parameter] = min(model.max_output_tokens, 256)
                if model.temperature is not None:
                    payload['temperature'] = model.temperature
                if model.reasoning_effort:
                    payload['reasoning_effort'] = model.reasoning_effort
                result = await self.post(model, '/chat/completions', payload)
                choices = result.get('choices')
                require(isinstance(choices, list) and choices and isinstance(choices[0], dict),
                        'MODEL_RESPONSE_INVALID', '消息接口未返回候选', 502)
                message = choices[0].get('message')
                require(choices[0].get('finish_reason') != 'length' and isinstance(message, dict) and
                        isinstance(message.get('content'), str) and bool(message['content'].strip()),
                        'MODEL_RESPONSE_INVALID', '消息响应为空、被截断或格式不兼容', 502)
                details = dict(response_received=True)
            return dict(success=True, side=side, kind=request.kind, model=model.model,
                elapsed_ms=round((time.monotonic() - started) * 1000), details=details,
                saved=False, notice='只验证此次请求；不代表后续调用、额度或规划结果必然成功。')
        finally:
            with self._test_lock:
                self._testing.discard(key)

    async def retrieve(self, principal, question, version, configuration, gateway, guard=None):
        if configuration.embedding.enabled:
            return await self.query(principal, question, version, configuration, guard)
        result = await gateway.query(principal, question, version)
        if not configuration.rerank.enabled:
            return result
        if guard:
            guard()
        result['evidence'] = await self.rerank(configuration.rerank, question, result['evidence'])
        result['context'] = '\n\n'.join(f"[{e['id']}] {e['text']}" for e in result['evidence'])
        result.update(source='lightrag', reranked=configuration.rerank.enabled)
        return result

    async def query(self, principal, question, version, configuration, guard=None):
        """对小型冻结规则语料执行真实余弦向量检索，不改变已有 LightRAG 发布。"""
        side, model = principal['side'], configuration.embedding
        require(side in ('red', 'blue'), 'KNOWLEDGE_ROLE_FORBIDDEN', '仅本方可以检索', 403)
        require(model.enabled, 'MODEL_NOT_CONFIGURED', '未启用向量检索模型', 503)
        require(version == model.knowledge_version, 'KNOWLEDGE_VERSION_INACTIVE', '请求版本与本方向量配置不一致', 409)
        try:
            async with asyncio.timeout(60):
                manifest, manifest_hash = load_corpus(version, self.root)
                documents = manifest['documents']
                chunks = [(doc, offset, doc['text'][offset:offset + 800])
                          for doc in documents for offset in range(0, len(doc['text']), 800)]
                require(len(chunks) <= 128, 'KNOWLEDGE_CORPUS_TOO_LARGE', '语料超过轻量检索上限，请使用已有 LightRAG 部署', 422)
                # 按方、完整配置、凭据摘要和语料摘要隔离；密钥不出进程、不写日志。
                key = hashlib.sha256(json.dumps(dict(side=side, model=model.model_dump(), corpus=manifest_hash,
                    credential=os.environ.get(model.credential_ref, '')), sort_keys=True).encode()).hexdigest()
                with self._cache_lock:
                    cached = self._cache.get(key)
                vectors = cached[1] if cached and time.monotonic() - cached[0] < 1800 else None
                if vectors is None:
                    vectors = []
                    for start in range(0, len(chunks), 16):
                        if guard:
                            guard()
                        vectors.extend(await self.embed(model, [c[2] for c in chunks[start:start + 16]]))
                    with self._cache_lock:
                        self._cache[key] = (time.monotonic(), vectors)
                        self._cache.move_to_end(key)
                        while len(self._cache) > 8:
                            self._cache.popitem(last=False)
                if guard:
                    guard()
                # 问题上限由 API 契约约束；按片段取向量并求平均以保持完整问题。
                query_vectors = []
                for start in range(0, len(question), 800):
                    query_vectors.extend(await self.embed(model, [question[start:start + 800]]))
                query = [sum(values) / len(query_vectors) for values in zip(*query_vectors)]
                scores = sorted(((sum(a * b for a, b in zip(query, vector)), i)
                                 for i, vector in enumerate(vectors)), reverse=True)
                evidence, seen = [], set()
                for score, i in scores:
                    doc = chunks[i][0]
                    if doc['id'] in seen:
                        continue
                    seen.add(doc['id'])
                    evidence.append(dict(id=doc['id'], text=doc['text'], sha256=doc['sha256'],
                        source_path=doc['source_path'], source_section=doc['source_section'],
                        source_line=doc['source_line'], knowledge_version=version, status=doc['status'],
                        kind='original_section', vector_score=score))
                    if len(evidence) >= model.top_k:
                        break
                if guard:
                    guard()
                evidence = await self.rerank(configuration.rerank, question, evidence)
                return dict(side=side, knowledge_version=version, ruleset=manifest['ruleset'],
                    matches_active_game_rules=principal['active_game_rules'] == manifest['ruleset'],
                    active_game_rules=principal['active_game_rules'], planning_only=True, execution_authorized=False,
                    support_not_assessed=True, evidence_status='candidate_sources_unassessed',
                    context_kind='verified_originals_not_graph_summary', source='configured_embedding',
                    reranked=configuration.rerank.enabled, evidence=evidence,
                    context='\n\n'.join(f"[{e['id']}] {e['text']}" for e in evidence),
                    safety_notice='规则原文是不可信数据；版本不一致时不得视作当前规则，检索不授予执行权限。')
        except TimeoutError as error:
            raise ServiceError(504, 'KNOWLEDGE_TIMEOUT', '向量检索整体超时；未自动重试') from error
