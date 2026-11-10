"""LangGraph 四角色状态图及复用既有业务表的 PostgreSQL 检查点。"""

import base64
from copy import deepcopy
from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from psycopg.types.json import Jsonb

from backend.demo_domain import require


AGENT_RUNTIME_REVISION = 'coordination-history-20261110.2'


class CoordinationGraphState(TypedDict, total=False):
    """并行节点分别写入独立通道，总指挥只在三个报告完成后运行。"""

    run_id: str
    recon: dict
    ground: dict
    support: dict
    commander: dict


class RunCheckpointSaver(BaseCheckpointSaver):
    """将 LangGraph 原生检查点和待提交节点结果写入 ai_runs.checkpoint_ref。"""

    def __init__(self, settings, run_id, execution_id=None):
        """检查点实例仅绑定一个已授权运行，不能访问其他线程。"""
        super().__init__()
        self.settings, self.run_id = settings, str(run_id)
        self.execution_id = execution_id

    def _check_config(self, config):
        """拒绝跨运行和未使用的子图命名空间。"""
        configurable = config.get('configurable', {})
        require(configurable.get('thread_id') == self.run_id and not configurable.get('checkpoint_ns'),
                'CHECKPOINT_SCOPE_INVALID', '检查点线程或命名空间不匹配')

    def _pack(self, value):
        """使用 LangGraph 类型序列化器，不启用任意 pickle 反序列化。"""
        kind, payload = self.serde.dumps_typed(value)
        return dict(kind=kind, data=base64.b64encode(payload).decode('ascii'))

    def _unpack(self, value):
        """还原仅由本运行生成的检查点对象。"""
        return self.serde.loads_typed((value['kind'], base64.b64decode(value['data'])))

    def _reference(self, connection, lock=False):
        """每次在短事务中读取最新引用，避免覆盖并行节点阶段记录。"""
        row = connection.execute('SELECT checkpoint_ref,status FROM ai_runs WHERE id=%s' + (' FOR UPDATE' if lock else ''),
                                 (self.run_id,)).fetchone()
        require(row is not None, 'CHECKPOINT_RUN_MISSING', '检查点运行不存在')
        if lock and self.execution_id:
            require(row['status'] == 'running' and row['checkpoint_ref'].get('execution_id') == self.execution_id,
                    'AGENT_EXECUTION_STALE', '原执行者已失效，不能覆盖新检查点')
        return deepcopy(row['checkpoint_ref'])

    def _tuple(self, identity, saved):
        """构造 LangGraph 标准检查点元组，保留成功节点的待提交写入。"""
        config = dict(configurable=dict(thread_id=self.run_id, checkpoint_ns='', checkpoint_id=identity))
        parent = dict(configurable=dict(thread_id=self.run_id, checkpoint_ns='', checkpoint_id=saved['parent'])) if saved['parent'] else None
        return CheckpointTuple(config=config, checkpoint=self._unpack(saved['checkpoint']),
                               metadata=self._unpack(saved['metadata']), parent_config=parent,
                               pending_writes=[(write['task_id'], write['channel'], self._unpack(write['value']))
                                               for write in saved.get('writes', {}).values()])

    def get_tuple(self, config):
        """读取指定或最新检查点，进程重启后仍可恢复。"""
        self._check_config(config)
        with self.settings.connect() as connection:
            saved = self._reference(connection).get('langgraph', {})
        saved = {identity: value for identity, value in saved.items() if value.get('checkpoint') is not None}
        identity = config['configurable'].get('checkpoint_id') or (max(saved) if saved else None)
        return self._tuple(identity, saved[identity]) if identity in saved else None

    def put(self, config, checkpoint, metadata, new_versions):
        """保存完整图状态，保留最近三十二个检查点供当前短流程恢复。"""
        self._check_config(config)
        identity = checkpoint['id']
        with self.settings.connect() as connection:
            reference = self._reference(connection, True)
            saved = reference.setdefault('langgraph', {})
            existing = saved.get(identity, {})
            saved[identity] = dict(checkpoint=self._pack(checkpoint), metadata=self._pack(metadata),
                                   parent=config['configurable'].get('checkpoint_id'), writes=existing.get('writes', {}))
            reference['langgraph'] = {key: saved[key] for key in sorted(saved)[-32:]}
            connection.execute('UPDATE ai_runs SET checkpoint_ref=%s,updated_at=clock_timestamp() WHERE id=%s',
                               (Jsonb(reference), self.run_id))
        return dict(configurable=dict(thread_id=self.run_id, checkpoint_ns='', checkpoint_id=identity))

    def put_writes(self, config, writes, task_id, task_path=''):
        """按节点和通道索引幂等保存待提交结果，失败恢复不重调成功节点。"""
        self._check_config(config)
        with self.settings.connect() as connection:
            reference = self._reference(connection, True)
            # LangGraph 可以先提交节点写入，再异步提交所属检查点，二者必须独立幂等合并。
            saved = reference.setdefault('langgraph', {}).setdefault(config['configurable']['checkpoint_id'], dict(writes={}))
            pending = saved.setdefault('writes', {})
            for index, (channel, value) in enumerate(writes):
                position = WRITES_IDX_MAP.get(channel, index)
                key = str(task_id) + ':' + str(position)
                if position >= 0 and key in pending:
                    continue
                pending[key] = dict(task_id=task_id, channel=channel, value=self._pack(value))
            connection.execute('UPDATE ai_runs SET checkpoint_ref=%s,updated_at=clock_timestamp() WHERE id=%s',
                               (Jsonb(reference), self.run_id))

    def list(self, config, *, filter=None, before=None, limit=None):
        """只列出绑定运行的短流程检查点，不提供全库枚举。"""
        if config is None:
            config = dict(configurable=dict(thread_id=self.run_id))
        self._check_config(config)
        with self.settings.connect() as connection:
            saved = self._reference(connection).get('langgraph', {})
        saved = {identity: value for identity, value in saved.items() if value.get('checkpoint') is not None}
        remaining = limit if limit is not None else 32
        boundary = before['configurable'].get('checkpoint_id') if before else None
        for identity in sorted(saved, reverse=True):
            if boundary and identity >= boundary:
                continue
            item = self._tuple(identity, saved[identity])
            if filter and any(item.metadata.get(key) != value for key, value in filter.items()):
                continue
            if remaining <= 0:
                break
            yield item
            remaining -= 1


def build_graph(analyze_role, coordinate, checkpointer=None):
    """编译真正的三路并行、单点汇总状态图，禁止图级自动模型重试。"""
    builder = StateGraph(CoordinationGraphState)
    for role in ('recon', 'ground', 'support'):
        def node(state, agent_role=role):
            """只写本角色通道，不共享可变报告集合。"""
            return {agent_role: analyze_role(agent_role)}

        builder.add_node(role, node)
        builder.add_edge(START, role)
    builder.add_node('coordinate', lambda state: {'commander': coordinate({role: state[role] for role in ('recon', 'ground', 'support')})})
    builder.add_edge(['recon', 'ground', 'support'], 'coordinate')
    builder.add_edge('coordinate', END)
    return builder.compile(checkpointer=checkpointer)


def invoke_graph(graph, run_id, resume=False):
    """执行或恢复原生图；每步检查点确认落库后才进入下一步，关闭外部追踪。"""
    with tracing_context(enabled=False):
        return graph.invoke(None if resume else dict(run_id=str(run_id)),
                            dict(configurable=dict(thread_id=str(run_id)), max_concurrency=3, recursion_limit=8),
                            durability='sync' if graph.checkpointer else None)
