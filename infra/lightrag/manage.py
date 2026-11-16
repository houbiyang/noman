#!/usr/bin/env python3
"""本机 LightRAG 管理；不输出密钥、问题、供应商响应或原文。"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from backend.knowledge import KnowledgeGateway, load_corpus, model_fingerprint, read_env
from backend.runtime import ServiceError

LOCAL = ROOT / '.local/lightrag'
DEFAULT_VERSION = 'conquest-v1-20261110'


class ManagementError(ValueError):
    """仅含本项目固定提示的可公开管理错误。"""



def write_json(path: Path, value):
    """同目录原子替换，凭据及发布文件仅当前用户可读写。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temp.replace(path)


def env_for(version, side):
    env = read_env(LOCAL / 'releases' / version / side / 'service.env')
    required = ('LIGHTRAG_API_KEY', 'LLM_BINDING_HOST', 'LLM_BINDING_API_KEY', 'LLM_MODEL',
                'EMBEDDING_BINDING_HOST', 'EMBEDDING_BINDING_API_KEY', 'EMBEDDING_MODEL', 'EMBEDDING_DIM')
    if any(not env.get(k) or re.fullmatch(r'__[A-Z_]+__', env[k]) for k in required):
        raise ManagementError('模型配置未补齐；请编辑本机 service.env，不要在聊天中发送密钥')
    if int(env['EMBEDDING_DIM']) <= 0:
        raise ManagementError('嵌入维度必须为正整数')
    for key in ('LLM_BINDING_HOST', 'EMBEDDING_BINDING_HOST'):
        if not env[key].startswith('https://'):
            raise ManagementError('在线模型必须使用 HTTPS base_url')
    return env


def init(version):
    load_corpus(version)
    directory = LOCAL / 'releases' / version
    if directory.exists():
        raise ManagementError('版本目录已存在；为避免覆盖凭据和索引，拒绝重复初始化')
    directory.mkdir(parents=True, mode=0o700)
    template = (ROOT / 'infra/lightrag/service.env.example').read_text()
    for side in ('red', 'blue'):
        folder = directory / side
        folder.mkdir(mode=0o700)
        for name in ('storage', 'inputs', 'prompts'):
            (folder / name).mkdir(mode=0o700)
        env = folder / 'service.env'
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(template.replace('__GENERATED_PER_SIDE__', secrets.token_urlsafe(48)))
    print('已创建两套独立目录及密钥；尚未激活，尚未配置模型或建库。')


async def preflight(version, side):
    env = env_for(version, side)
    directory = LOCAL / 'releases' / version / side
    marker = directory / 'preflight.json'
    marker.unlink(missing_ok=True)
    async with httpx.AsyncClient(timeout=45, trust_env=False, follow_redirects=False) as client:
        generation = await client.post(env['LLM_BINDING_HOST'].rstrip('/') + '/chat/completions',
            headers={'Authorization': 'Bearer ' + env['LLM_BINDING_API_KEY']}, json={
                'model': env['LLM_MODEL'], 'messages': [{'role': 'user', 'content':
                '只返回JSON对象。将“陆兵可以占领控制点，无人机不能占领控制点”提取为entities数组和relationships数组，每个关系有subject、predicate、object。'}],
                'response_format': {'type': 'json_object'}})
        generation.raise_for_status()
        result = json.loads(generation.json()['choices'][0]['message']['content'])
        if not isinstance(result.get('entities'), list) or not isinstance(result.get('relationships'), list) or not result['relationships']:
            raise ManagementError('生成模型中文结构化提取自检失败')
        embedding = await client.post(env['EMBEDDING_BINDING_HOST'].rstrip('/') + '/embeddings',
            headers={'Authorization': 'Bearer ' + env['EMBEDDING_BINDING_API_KEY']}, json={
                'model': env['EMBEDDING_MODEL'], 'input': ['陆兵占点', '人工确认'], 'encoding_format': 'float',
                **({'dimensions': int(env['EMBEDDING_DIM'])} if env.get('EMBEDDING_SEND_DIM') == 'true' else {})})
        embedding.raise_for_status()
        vectors = embedding.json()['data']
        if len(vectors) != 2 or any(len(v['embedding']) != int(env['EMBEDDING_DIM']) or
                not all(isinstance(x, (int, float)) and math.isfinite(x) for x in v['embedding']) for v in vectors):
            raise ManagementError('嵌入输出数量、维度或数值不匹配')
    write_json(marker, dict(version=version, side=side, model_fingerprint=model_fingerprint(env), checked_at=time.time()))
    print(f'{side}: 模型连接、中文结构及嵌入维度自检通过；不代表 LightRAG 已建库。')


def client_for(side, env):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{9622 if side == 'red' else 9623}",
        headers={'X-API-Key': env['LIGHTRAG_API_KEY']}, timeout=45, trust_env=False, follow_redirects=False)


async def ingest(version, side):
    manifest, manifest_hash = load_corpus(version)
    env = env_for(version, side)
    directory = LOCAL / 'releases' / version / side
    preflight_state = json.loads((directory / 'preflight.json').read_text())
    if preflight_state['model_fingerprint'] != model_fingerprint(env):
        raise ManagementError('模型配置已变化，必须先自检；已建索引应发布新版本，不原地复用')
    fingerprint = model_fingerprint(env)
    journal_path = directory / 'ingestion.json'
    state = json.loads(journal_path.read_text()) if journal_path.exists() else dict(
        version=version, side=side, manifest_sha256=manifest_hash, model_fingerprint=fingerprint, tracks={})
    if state['manifest_sha256'] != manifest_hash or state['model_fingerprint'] != fingerprint:
        raise ManagementError('已有入库记录与配置不兼容；请建立新版本目录')
    (directory / 'ready.json').unlink(missing_ok=True)
    async with client_for(side, env) as client:
        gateway = KnowledgeGateway()
        for doc in manifest['documents']:
            source = doc['file_source']
            if source not in state['tracks']:
                result = await gateway.request(client, 'POST', '/documents/text', json={
                    'text': doc['text'], 'file_source': source})
                if result.get('status') != 'success' or not result.get('track_id'):
                    raise ManagementError('入库未返回成功追踪标识；不标记完成')
                state['tracks'][source] = result['track_id']
                write_json(journal_path, state)
            deadline = time.monotonic() + 1200
            while True:
                result = await gateway.request(client, 'GET', '/documents/track_status/' + state['tracks'][source])
                docs = result.get('documents', [])
                if any(d.get('status') == 'failed' for d in docs):
                    raise ManagementError('文档处理失败；请在本机管理界面核对，不盲目重新提交')
                if any(d.get('file_path') == source and d.get('status') == 'processed' and d.get('chunks_count', 0) > 0 for d in docs):
                    break
                if time.monotonic() > deadline:
                    raise ManagementError('文档处理超过20分钟；已保存追踪记录，不宣称建库完成')
                await asyncio.sleep(3)
            print(f'{side}: {doc["id"]} 处理完成')
        await gateway.check_index(client, manifest, state)
        write_json(directory / 'ready.json', state)
    print(f'{side}: 全部审核章节已处理，索引发布标记已写入；仍需真实检索质量验收。')


async def activate(version):
    manifest, manifest_hash = load_corpus(version)
    envs = {s: env_for(version, s) for s in ('red', 'blue')}
    if envs['red']['LIGHTRAG_API_KEY'] == envs['blue']['LIGHTRAG_API_KEY']:
        raise ManagementError('双方服务密钥不得相同')
    for side in ('red', 'blue'):
        state = json.loads((LOCAL / 'releases' / version / side / 'ready.json').read_text())
        if (state['manifest_sha256'] != manifest_hash or state['model_fingerprint'] != model_fingerprint(envs[side])
                or state['side'] != side or state['version'] != version):
            raise ManagementError('发布标记或模型配置不匹配')
        async with client_for(side, envs[side]) as client:
            await KnowledgeGateway().check_index(client, manifest, state)
    write_json(LOCAL / 'backend.json', dict(active_version=version))
    print('已原子切换后端活动版本；目标规则仍为 planning_only。')


async def status(version, side):
    env = env_for(version, side)
    manifest, manifest_hash = load_corpus(version)
    state = json.loads((LOCAL / 'releases' / version / side / 'ready.json').read_text())
    if state['manifest_sha256'] != manifest_hash or state['model_fingerprint'] != model_fingerprint(env):
        raise ManagementError('发布标记已过期')
    async with client_for(side, env) as client:
        await KnowledgeGateway().check_index(client, manifest, state)
    print(f'{side}: 所有冻结文档已处理，服务索引可读；不等于检索质量通过。')


async def evaluate(version, side):
    questions = json.loads((ROOT / 'knowledge' / version / 'evaluation.json').read_text())
    results = []
    for item in questions:
        result = await KnowledgeGateway().query(dict(side=side, active_game_rules='not_conquest'), item['question'], version)
        hits = {e['id'] for e in result['evidence']}
        results.append(dict(id=item['id'], critical=item['critical'], passed=bool(hits.intersection(item['expected_sources']))))
    passed = sum(r['passed'] for r in results)
    success = passed >= 18 and all(r['passed'] for r in results if r['critical'])
    write_json(LOCAL / 'releases' / version / side / 'evaluation-result.json',
               dict(version=version, side=side, passed=passed, total=len(results), acceptance_passed=success,
                    evaluated_at=time.time(), note='仅来源命中评测；未自动证明结论蕴含、隔离或恢复。', results=results))
    print(f'{side}: 来源命中 {passed}/{len(results)}；关键问题全部命中={all(r["passed"] for r in results if r["critical"])}')
    if not success:
        raise ManagementError('真实检索来源命中未达到验收门槛')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'validate', 'preflight', 'ingest', 'status', 'activate', 'evaluate'])
    parser.add_argument('--version', default=DEFAULT_VERSION)
    parser.add_argument('--side', choices=['red', 'blue'])
    args = parser.parse_args()
    try:
        load_corpus(args.version)  # 先验证路径及冻结哈希，任何操作都不接受任意目录。
        if args.action == 'init':
            init(args.version)
        elif args.action == 'validate':
            manifest, _ = load_corpus(args.version)
            print(f'冻结语料校验通过：{len(manifest["documents"])} 章；未调用在线模型。')
        elif args.action == 'activate':
            asyncio.run(activate(args.version))
        else:
            if not args.side:
                parser.error('此操作必须提供 --side red 或 --side blue')
            asyncio.run(globals()[args.action](args.version, args.side))
    except ServiceError as error:
        print(f'失败：{error.code} / {error.message}', file=sys.stderr)
        return 1
    except ManagementError as error:
        print('失败：' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
        # 异常字符串可能携带 URL、密钥、供应商错误和语料，不直接打印。
        print('失败：本机配置、模型自检或索引处理未通过。请按说明核对；未输出敏感错误详情。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
