"""五点共同规划：冻结双方私有输入、共享整轮期限、一次修复和原子共同采用。"""

from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
import json
import logging
from threading import Lock
from time import monotonic

from backend import fivepoint_engine as engine
from backend.agent_model import MessageModel
from backend.contracts import FivepointTask
from backend.fivepoint_parameters import require
from backend.fivepoint_tasks import accept_independent


def decode_tasks(content) -> list:
    """只接受任务对象，保留逐任务校验机会，不因一个非法节点抛弃合法独立节点。"""
    if not isinstance(content, str):
        raise ValueError('模型未返回文本')
    stripped = content.strip()
    if stripped.startswith('```') and stripped.endswith('```'):
        stripped = '\n'.join(stripped.splitlines()[1:-1])
    result = json.loads(stripped)
    if not isinstance(result, dict) or set(result) != {'tasks'} or not isinstance(result['tasks'], list) or len(result['tasks']) > 1000:
        raise ValueError('模型须返回有界任务列表')
    return result['tasks']


def plan_side(view: dict, configuration, model_factory, deadline: float) -> dict:
    """三个兵种角色并行提出任务，总指挥汇总；全方仅共用一次格式修复机会。"""
    lock = Lock()
    accounting = dict(repairs=0, calls=0, tokens=0)
    schema = FivepointTask.model_json_schema()

    def complete(role, reports=None):
        """每次请求使用剩余时间和总预算，响应晚到时丢弃且不再调用模型。"""
        instructions = (
            '你是虚构青岚五点游戏的' + role + '。仅依据本方合法视图提出任务，不修改世界状态。'
            '任务在每60仿真秒共同采用，每5秒执行；范围攻击和补给分支须明确授权。'
            '未知路线不能推断为可通行；历史敌情不代表当前可见。不得输出分数或任务完成状态。'
            '只输出JSON对象 {"tasks": [...]}，任务结构遵守：' + json.dumps(schema, ensure_ascii=False)
        )
        messages = [dict(role='system', content=instructions),
                    dict(role='user', content=json.dumps(dict(view=view, reports=reports,
                         policy=getattr(configuration, role).model_dump()), ensure_ascii=False))]
        while True:
            remaining = deadline - monotonic()
            require(remaining > 0, 'PLANNING_TIMEOUT', '共同规划已超时')
            request_configuration = configuration.model_copy(deep=True)
            request_configuration.model.timeout_seconds = min(configuration.model.timeout_seconds, remaining)
            with lock:
                remaining_tokens = configuration.max_total_tokens - accounting['tokens']
                require(remaining_tokens > 0, 'MODEL_BUDGET_EXCEEDED', '本轮用量预算已经耗尽')
                accounting['calls'] += 1
            message, usage = model_factory(request_configuration).complete(messages, [], remaining_tokens)
            require(monotonic() <= deadline, 'PLANNING_TIMEOUT', '共同规划响应晚到')
            with lock:
                accounting['tokens'] += usage.get('total_tokens', 0)
                require(accounting['tokens'] <= configuration.max_total_tokens, 'MODEL_BUDGET_EXCEEDED', '本轮用量超过预算')
            try:
                return decode_tasks(message.get('content'))
            except (ValueError, TypeError):
                with lock:
                    require(accounting['repairs'] == 0, 'MODEL_OUTPUT_INVALID', '本轮格式修复机会已经用尽')
                    accounting['repairs'] += 1
                messages.append(dict(role='user', content='返回格式无效，请仅返回包含tasks数组的JSON对象；本次为整轮唯一格式修复。'))

    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='fivepoint-role')
    try:
        pending = {role: pool.submit(complete, role) for role in ('recon', 'ground', 'support')}
        _, unfinished = wait(pending.values(), timeout=max(0, deadline - monotonic()))
        require(not unfinished, 'PLANNING_TIMEOUT', '兵种规划超时')
        reports = {role: future.result() for role, future in pending.items()}
        tasks = complete('commander', reports)
        return dict(tasks=tasks, **accounting)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def run_round(state: dict, configurations: dict, model_factory=MessageModel, budget_seconds=120) -> dict:
    """从同一冻结时刻生成双方方案；任一失败均不采用，迟到线程无状态写入能力。"""
    require(state['phase'] == 'planning' and not state['pending_plans'] and state['step'] % 12 == 0,
            'PLAN_BOUNDARY_INVALID', '共同规划边界无效')
    require(set(configurations) == {'red', 'blue'} and 0 < budget_seconds <= 120,
            'PLANNING_CONFIGURATION_INVALID', '必须配置双方且整轮预算不超过120秒')
    basis = deepcopy(state)
    views = {side: engine.project(basis, side) for side in ('red', 'blue')}
    deadline = monotonic() + budget_seconds
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='fivepoint-side')
    try:
        futures = {side: pool.submit(plan_side, views[side], configurations[side].model_copy(deep=True), model_factory, deadline)
                   for side in ('red', 'blue')}
        _, unfinished = wait(futures.values(), timeout=max(0, deadline - monotonic()))
        if unfinished:
            return engine.fail_planning(basis, next(side for side in futures if futures[side] in unfinished), 'PLANNING_TIMEOUT')
        proposals = {}
        for side, future in futures.items():
            try:
                proposals[side] = future.result()
            except Exception as error:
                # 不将模型原始内容或服务异常正文写入双方可见错误。
                logging.getLogger(__name__).warning('五点共同规划失败：%s', type(error).__name__)
                return engine.fail_planning(basis, side, 'PLANNING_FAILED')
        result = basis
        feedback = {}
        for side in ('red', 'blue'):
            accepted, rejected = accept_independent(basis, side, proposals[side]['tasks'])
            result = engine.submit_plan(result, side, {}, basis['step'], tasks=accepted)
            feedback[side] = dict(rejected=rejected, calls=proposals[side]['calls'],
                                  tokens=proposals[side]['tokens'], repairs=proposals[side]['repairs'])
        result['planning_feedback'] = feedback
        result['planning_source'] = 'model'
        return result
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
