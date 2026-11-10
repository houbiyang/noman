"""持久化四角色编排，分析过程不持有对局事务或直接修改世界。"""

from copy import deepcopy
from datetime import timedelta
import json
import logging
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from backend import database
from backend.agent_contracts import AgentConfiguration, AgentReport, CommanderPlan
from backend.agent_model import MessageModel
from backend.agent_graph import RunCheckpointSaver, build_graph, invoke_graph
from backend.agent_tools import ROLES, RuleTools, freeze_view, validate_plan
from backend.demo_domain import require
from backend.runtime import ServiceError, canonical, sha256, utcnow
from backend.service import HASH_VERSION, insert


CONFIGURATION_LOCK = Lock()


class AgentCoordinator:
    """一个总指挥运行关联三个兵种运行，检查点保存在既有 ai_runs。"""

    def __init__(self, sandbox, config_root=None, model_factory=MessageModel):
        """复用业务连接和身份服务，测试可以注入隔离配置与模型替身。"""
        self.sandbox, self.settings, self.model_factory = sandbox, sandbox.settings, model_factory
        identity = sha256(self.settings.connection['database'])[:16]
        self.config_root = Path(config_root) if config_root else database.LOCAL_ROOT / 'agents' / identity

    def principal(self, game_id, bearer):
        """模型配置和私有运行仅允许本方席位访问，主持人不代读私有建议。"""
        with self.settings.connect() as connection:
            principal = self.sandbox._principal(connection, bearer, game_id)
        require(principal['role'] in ('red', 'blue'), 'AGENT_SIDE_REQUIRED', '请使用红方或蓝方席位操作本方智能体', 403)
        return principal

    def load_configuration(self, side):
        """分别读取红蓝配置，缺失时保留明确的未配置状态。"""
        path = self.config_root / (side + '.json')
        if not path.exists():
            configuration = AgentConfiguration()
            configuration.model.credential_ref = side.upper() + '_AI_API_KEY'
            return configuration
        try:
            return AgentConfiguration.model_validate_json(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, ValidationError) as error:
            raise ServiceError(503, 'AGENT_CONFIGURATION_INVALID', '本方智能体配置无法读取，请重新保存配置') from error

    def configuration(self, game_id, bearer):
        """返回本方非秘密配置及环境变量就绪情况。"""
        principal = self.principal(game_id, bearer)
        configuration = self.load_configuration(principal['role'])
        return dict(side=principal['role'], configuration=configuration.model_dump(),
                    configured=MessageModel.available(configuration), configuration_scope='本机当前数据库的本方配置')

    def save_configuration(self, game_id, bearer, configuration):
        """使用受保护临时文件原子替换本方配置，不改动另一方。"""
        principal = self.principal(game_id, bearer)
        self.config_root.mkdir(parents=True, exist_ok=True)
        destination = self.config_root / (principal['role'] + '.json')
        temporary = self.config_root / (principal['role'] + '-' + uuid4().hex + '.json')
        with CONFIGURATION_LOCK:
            database.write_private_json(temporary, configuration.model_dump())
            temporary.replace(destination)
        return self.configuration(game_id, bearer)

    def request(self, game_id, bearer, request_key, request):
        """幂等登记四角色运行；不会在数据库锁内请求远端模型。"""
        principal = self.principal(game_id, bearer)
        configuration = self.load_configuration(principal['role'])
        context = request.context.model_dump(mode='json')
        require(context['game_id'] == game_id, 'CONTEXT_INVALID', '路径和上下文对局不一致', 422)
        request_hash = sha256(request.model_dump(mode='json'))
        thread = 'coordination/' + game_id + '/' + principal['role'] + '/' + str(request_key)
        with self.settings.connect() as connection:
            game = self.sandbox._game(connection, game_id, lock=True)
            principal = self.sandbox._principal(connection, bearer, game_id)
            existing = connection.execute('SELECT * FROM ai_runs WHERE checkpoint_thread_id=%s', (thread,)).fetchone()
            if existing:
                require(existing['checkpoint_ref']['request_sha256'] == request_hash,
                        'IDEMPOTENCY_KEY_REUSED', '同一分析请求键不能替换输入')
                return self._public(existing), True
            self.sandbox._check_context(game, principal, context)
            require(game['state_payload']['lifecycle']['phase'] in ('preparing', 'paused', 'awaiting_confirmation'),
                    'PAUSE_REQUIRED', '请先暂停对局再启动协同规划')
            require('coordination' in game['state_payload'], 'COORDINATION_REQUIRED', '请先由主持人启用协同测试规则')
            active = connection.execute("""SELECT id FROM ai_runs WHERE game_id=%s AND branch_id=%s AND side=%s
                AND agent_role='commander' AND checkpoint_thread_id LIKE 'coordination/%%'
                AND status IN ('queued','running','waiting_human')""", (game_id, game['active_branch_id'], principal['role'])).fetchone()
            require(active is None, 'AGENT_RUN_ACTIVE', '本方已有分析或待处理方案，请先处理或取消')
            frozen = freeze_view(game['state_payload'], principal['role'])
            root_id = uuid4()
            children = {role: str(uuid4()) for role in ROLES if role != 'commander'}
            for role in ROLES:
                identity = root_id if role == 'commander' else UUID(children[role])
                checkpoint = dict(root_run_id=str(root_id), request_sha256=request_hash, reason=request.reason,
                                  children=children, requested_member_id=str(principal['member_id']),
                                  requested_session_id=str(principal['session_id']), stages=[], lease_until=None)
                insert(connection, 'ai_runs', dict(id=identity, game_id=game_id, branch_id=game['active_branch_id'],
                    base_snapshot_id=game['head_snapshot_id'], side=principal['role'], execution_epoch=game['execution_epoch'],
                    agent_role=role, input_view=frozen, model_manifest=dict(source='multi_agent', configuration=configuration.model_dump(),
                    provider_connection_tested=False), knowledge_manifest=dict(source='runtime_rules_and_reports', external_retrieval=False),
                    input_view_sha256=sha256(frozen), hash_format_version=HASH_VERSION,
                    checkpoint_thread_id=thread if role == 'commander' else thread + '/' + role,
                    checkpoint_ref=checkpoint, status='queued'))
            result = connection.execute('SELECT * FROM ai_runs WHERE id=%s', (root_id,)).fetchone()
        return self._public(result), False

    @staticmethod
    def _public(row):
        """公开本方可读的运行状态，不返回运行输入和会话标识。"""
        return dict(run_id=str(row['id']), role=row['agent_role'], side=row['side'], status=row['status'],
                    branch_id=str(row['branch_id']), created_at=row['created_at'], finished_at=row['finished_at'],
                    output=row['output_payload'], error=row['error_payload'], usage=row['usage'],
                    stages=(row['checkpoint_ref'] or {}).get('stages', []))

    def runs(self, game_id, bearer):
        """只列出本方协调运行，限制数量，避免下载全部历史。"""
        principal = self.principal(game_id, bearer)
        with self.settings.connect() as connection:
            rows = connection.execute("""SELECT * FROM ai_runs WHERE game_id=%s AND side=%s
                AND checkpoint_thread_id LIKE 'coordination/%%' ORDER BY created_at DESC LIMIT 60""",
                                      (game_id, principal['role'])).fetchall()
        return dict(items=[self._public(row) for row in rows])

    def get(self, game_id, bearer, run_id):
        """按方别隐藏其他方运行存在性。"""
        principal = self.principal(game_id, bearer)
        with self.settings.connect() as connection:
            row = connection.execute("""SELECT * FROM ai_runs WHERE game_id=%s AND side=%s AND id=%s
                AND checkpoint_thread_id LIKE 'coordination/%%'""", (game_id, principal['role'], run_id)).fetchone()
        require(row is not None, 'AGENT_RUN_NOT_FOUND', '分析运行不存在或无权读取', 404)
        return self._public(row)

    def cancel(self, game_id, bearer, run_id):
        """取消分析资格，远端请求即使晚到也不能发布或采用结果。"""
        principal = self.principal(game_id, bearer)
        with self.settings.connect() as connection:
            self.sandbox._game(connection, game_id, lock=True)
            root = connection.execute("SELECT * FROM ai_runs WHERE game_id=%s AND side=%s AND id=%s FOR UPDATE",
                                      (game_id, principal['role'], run_id)).fetchone()
            require(root is not None and root['agent_role'] == 'commander'
                    and root['checkpoint_thread_id'].startswith('coordination/'), 'AGENT_RUN_NOT_FOUND', '分析运行不存在', 404)
            require(root['status'] in ('queued', 'running', 'waiting_human', 'failed', 'cancelled'),
                    'AGENT_RUN_FINAL', '已采用或失效的运行不能取消')
            ids = [root['id']] + [UUID(value) for value in root['checkpoint_ref']['children'].values()]
            connection.execute("""UPDATE ai_runs SET status='cancelled',finished_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE id=ANY(%s) AND status IN ('queued','running','waiting_human','failed')""", (ids,))
            connection.execute("UPDATE proposals SET status='cancelled',updated_at=clock_timestamp() WHERE ai_run_id=%s AND status='pending_review'", (root['id'],))
        return self.get(game_id, bearer, run_id)

    def resume(self, game_id, bearer, run_id):
        """只恢复未完成检查点；已成功的兵种报告不重复调用。"""
        principal = self.principal(game_id, bearer)
        with self.settings.connect() as connection:
            game = self.sandbox._game(connection, game_id, lock=True)
            root = connection.execute('SELECT * FROM ai_runs WHERE game_id=%s AND side=%s AND id=%s FOR UPDATE',
                                      (game_id, principal['role'], run_id)).fetchone()
            require(root is not None and root['agent_role'] == 'commander'
                    and root['checkpoint_thread_id'].startswith('coordination/'), 'AGENT_RUN_NOT_FOUND', '分析运行不存在', 404)
            require(root['status'] in ('queued', 'running', 'failed'), 'AGENT_RUN_FINAL', '当前运行不能恢复')
            require((root.get('error_payload') or {}).get('code') not in
                    ('MODEL_NOT_CONFIGURED', 'MODEL_OUTPUT_INVALID', 'MODEL_OUTPUT_TRUNCATED', 'AGENT_OUTPUT_INVALID', 'TASK_DEPENDENCY_CYCLE',
                     'TASK_UNIT_CONFLICT', 'MODEL_BUDGET_EXCEEDED'),
                    'AGENT_RESTART_REQUIRED', '配置、预算或候选内容需要修改，请取消此运行后启动新的规划')
            lease = root['checkpoint_ref'].get('lease_until')
            require(root['status'] != 'running' or not lease or lease < utcnow().timestamp(),
                    'AGENT_RUN_BUSY', '原分析仍在有效执行期内')
            self._check_basis(connection, root, game)
            ids = [root['id']] + [UUID(value) for value in root['checkpoint_ref']['children'].values()]
            connection.execute("""UPDATE ai_runs SET status='queued',error_payload=NULL,finished_at=NULL,updated_at=clock_timestamp()
                WHERE id=ANY(%s) AND status IN ('queued','running','failed')""", (ids,))
        return self.get(game_id, bearer, run_id)

    def _check_basis(self, connection, root, game):
        """重新校验分支、实际本方依据和原请求席位，隐藏变化不单独泄漏。"""
        session = connection.execute("""SELECT id FROM access_sessions WHERE id=%s AND game_id=%s
            AND revoked_at IS NULL AND expires_at>clock_timestamp()""",
                                     (root['checkpoint_ref']['requested_session_id'], game['id'])).fetchone()
        require(session is not None, 'AGENT_SESSION_STALE', '启动分析的会话已失效')
        require(root['branch_id'] == game['active_branch_id'] and root['execution_epoch'] == game['execution_epoch']
                and root['input_view_sha256'] == sha256(freeze_view(game['state_payload'], root['side'])),
                'AGENT_BASIS_STALE', '本方分析依据或分支已变化，请重新规划')

    def _guard(self, root_id, execution_id=None):
        """每次网络调用和工具调用前检查取消、分支与依据。"""
        with self.settings.connect() as connection:
            root = connection.execute('SELECT * FROM ai_runs WHERE id=%s', (root_id,)).fetchone()
            require(root is not None and root['status'] == 'running', 'AGENT_CANCELLED', '分析已取消或不再执行')
            require(execution_id is None or root['checkpoint_ref'].get('execution_id') == execution_id,
                    'AGENT_EXECUTION_STALE', '原执行者已失效，忽略晚到结果')
            self._check_basis(connection, root, self.sandbox._game(connection, str(root['game_id'])))

    def _save_stage(self, root_id, role, result, execution_id):
        """独立保存已完成报告，取消后不覆盖最终状态。"""
        with self.settings.connect() as connection:
            root = connection.execute('SELECT * FROM ai_runs WHERE id=%s FOR UPDATE', (root_id,)).fetchone()
            require(root['status'] == 'running', 'AGENT_CANCELLED', '分析已停止，忽略晚到结果')
            require(root['checkpoint_ref'].get('execution_id') == execution_id,
                    'AGENT_EXECUTION_STALE', '原执行者已失效，忽略晚到报告')
            identity = root_id if role == 'commander' else root['checkpoint_ref']['children'][role]
            connection.execute("""UPDATE ai_runs SET output_payload=%s,usage=%s,status=%s,finished_at=clock_timestamp(),
                updated_at=clock_timestamp() WHERE id=%s AND status IN ('queued','running')""",
                               (Jsonb(result), Jsonb(result.get('usage', {})), 'running' if role == 'commander' else 'succeeded', identity))
            checkpoint = deepcopy(root['checkpoint_ref'])
            checkpoint['stages'].append(dict(role=role, status='succeeded', completed_at=utcnow().isoformat()))
            connection.execute('UPDATE ai_runs SET checkpoint_ref=%s,updated_at=clock_timestamp() WHERE id=%s', (Jsonb(checkpoint), root_id))

    def execute(self, run_id):
        """在后台任务边界记录运行失败，未知异常仅公开固定错误，数据库使用短事务。"""
        execution_id = str(uuid4())
        try:
            self._execute(run_id, execution_id)
        except Exception as error:
            if isinstance(error, ServiceError):
                code, message = error.code, error.message
            elif isinstance(error, ValidationError):
                code, message = 'AGENT_OUTPUT_INVALID', '智能体输出未通过任务契约校验'
            else:
                code, message = 'AGENT_EXECUTION_FAILED', '智能体执行异常，已停止本次分析；可手动恢复检查点，未自动重试模型'
                logging.getLogger(__name__).error('智能体运行 %s 执行异常：%s', run_id, type(error).__name__)
            with self.settings.connect() as connection:
                root = connection.execute('SELECT * FROM ai_runs WHERE id=%s FOR UPDATE', (run_id,)).fetchone()
                if root and root['status'] == 'running' and root['checkpoint_ref'].get('execution_id') == execution_id:
                    status = 'stale' if code in ('AGENT_BASIS_STALE', 'AGENT_SESSION_STALE') else 'failed'
                    connection.execute("""UPDATE ai_runs SET status=%s,error_payload=%s,finished_at=clock_timestamp(),
                        updated_at=clock_timestamp() WHERE id=%s""", (status, Jsonb(dict(code=code, message=message)), run_id))
                    child_ids = [UUID(value) for value in root['checkpoint_ref']['children'].values()]
                    connection.execute("""UPDATE ai_runs SET status=%s,error_payload=%s,finished_at=clock_timestamp(),
                        updated_at=clock_timestamp() WHERE id=ANY(%s) AND status IN ('queued','running')""",
                                       (status, Jsonb(dict(code=code, message=message)), child_ids))

    def _execute(self, run_id, execution_id):
        """领取带租约的协调运行并从持久化检查点继续。"""
        with self.settings.connect() as connection:
            root = connection.execute('SELECT * FROM ai_runs WHERE id=%s FOR UPDATE', (run_id,)).fetchone()
            if not root or root['status'] != 'queued':
                return
            configuration = AgentConfiguration.model_validate(root['model_manifest']['configuration'])
            checkpoint = deepcopy(root['checkpoint_ref'])
            checkpoint['execution_id'] = execution_id
            checkpoint['lease_until'] = (utcnow() + timedelta(seconds=configuration.model.timeout_seconds * 12 + 60)).timestamp()
            connection.execute("""UPDATE ai_runs SET status='running',started_at=clock_timestamp(),checkpoint_ref=%s,
                updated_at=clock_timestamp() WHERE id=%s""", (Jsonb(checkpoint), run_id))
            children = {role: connection.execute('SELECT * FROM ai_runs WHERE id=%s', (identity,)).fetchone()
                        for role, identity in checkpoint['children'].items()}
        require(MessageModel.available(configuration), 'MODEL_NOT_CONFIGURED', '本方模型未配置，或服务进程未加载对应密钥环境变量', 503)
        self._guard(run_id, execution_id)
        view, reports = root['input_view'], {}

        def analyze(role):
            """每个兵种拥有独立角色策略、工具白名单和持久化报告。"""
            child = children[role]
            if child['status'] == 'succeeded':
                return child['output_payload']
            with self.settings.connect() as connection:
                connection.execute("UPDATE ai_runs SET status='running',started_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s AND status='queued'", (child['id'],))
            tools = RuleTools(view, role)
            result = self.model_factory(configuration).analyze(role, dict(state=tools.role_view(), reason=checkpoint['reason']),
                                                               tools, AgentReport, lambda: self._guard(run_id, execution_id))
            for task in result['output']['suggested_tasks']:
                tools.unit(task['unit_id'])
            self._save_stage(run_id, role, result, execution_id)
            return result

        def coordinate(role_reports):
            """等待三路报告，复用已校验落库的总指挥结果，避免恢复时重复请求模型。"""
            self._guard(run_id, execution_id)
            if root['output_payload'] is not None:
                return deepcopy(root['output_payload'])
            consumed = sum(result.get('usage', {}).get('total_tokens', 0) for result in role_reports.values())
            require(consumed < configuration.max_total_tokens, 'MODEL_BUDGET_EXCEEDED', '兵种报告已用尽本次预算，停止总指挥调用')
            tools = RuleTools(view, 'commander')
            result = self.model_factory(configuration).analyze('commander', dict(state=tools.role_view(), reports=role_reports,
                reason=checkpoint['reason']), tools, CommanderPlan, lambda: self._guard(run_id, execution_id))
            require(consumed + result.get('usage', {}).get('total_tokens', 0) <= configuration.max_total_tokens,
                    'MODEL_BUDGET_EXCEEDED', '本次累计用量超过预算，未发布方案')
            result['output'] = validate_plan(view, result['output'])
            self._save_stage(run_id, 'commander', result, execution_id)
            return result

        saver = RunCheckpointSaver(self.settings, run_id, execution_id)
        graph = build_graph(analyze, coordinate, saver)
        restored = saver.get_tuple(dict(configurable=dict(thread_id=str(run_id)))) is not None
        graph_state = invoke_graph(graph, run_id, resume=restored)
        reports = {role: graph_state[role] for role in ('recon', 'ground', 'support')}
        commander, validated = graph_state['commander'], graph_state['commander']['output']
        consumed = sum(result.get('usage', {}).get('total_tokens', 0) for result in reports.values())
        with self.settings.connect() as connection:
            game = self.sandbox._game(connection, str(root['game_id']), lock=True)
            current = connection.execute('SELECT * FROM ai_runs WHERE id=%s FOR UPDATE', (run_id,)).fetchone()
            require(current['status'] == 'running', 'AGENT_CANCELLED', '分析已停止，忽略晚到结果')
            require(current['checkpoint_ref'].get('execution_id') == execution_id,
                    'AGENT_EXECUTION_STALE', '原执行者已失效，不能发布旧方案')
            self._check_basis(connection, current, game)
            proposal_id = uuid4()
            insert(connection, 'proposals', dict(id=proposal_id, game_id=root['game_id'], branch_id=root['branch_id'],
                base_snapshot_id=root['base_snapshot_id'], side=root['side'], execution_epoch=root['execution_epoch'],
                ai_run_id=run_id, proposal_kind='major_adjustment', status='pending_review', requires_confirmation=True,
                schema_version='multi-agent-plan-v1', content_payload=validated, validation_payload=dict(task_graph_valid=True, test_only=True),
                based_on_world_revision=game['world_revision'], input_view_sha256=root['input_view_sha256'], hash_format_version=HASH_VERSION))
            output = dict(commander, proposal_id=str(proposal_id), reports=reports, requires_confirmation=True,
                          total_tokens=consumed + commander.get('usage', {}).get('total_tokens', 0))
            connection.execute("UPDATE ai_runs SET status='waiting_human',output_payload=%s,updated_at=clock_timestamp() WHERE id=%s",
                               (Jsonb(output), run_id))


def review_plan(connection, game, state, principal, payload):
    """统一命令事务中采用或驳回任务图，草稿从不抢占有效计划。"""
    side = principal['role']
    require(side in ('red', 'blue'), 'AGENT_SIDE_REQUIRED', '主持人不能代本方采用方案', 403)
    root = connection.execute('SELECT * FROM ai_runs WHERE game_id=%s AND side=%s AND id=%s FOR UPDATE',
                              (game['id'], side, payload['run_id'])).fetchone()
    require(root is not None and root['agent_role'] == 'commander' and root['status'] == 'waiting_human',
            'AGENT_PLAN_STALE', '方案不存在或已经处理')
    require(root['branch_id'] == game['active_branch_id'] and root['execution_epoch'] == game['execution_epoch']
            and root['input_view_sha256'] == sha256(freeze_view(state, side)), 'AGENT_BASIS_STALE', '本方方案依据已变化，请重新规划')
    proposal_id = root['output_payload']['proposal_id']
    adopted = payload['decision'] == 'approve'
    if adopted:
        require(state['lifecycle']['phase'] in ('preparing', 'paused', 'awaiting_confirmation'), 'PAUSE_REQUIRED', '请先暂停再采用方案')
        candidate = deepcopy(root['output_payload']['output'])
        for task in candidate['tasks']:
            task.pop('preview', None)
        plan = validate_plan(freeze_view(state, side), candidate)
        previous = state['coordination']['plans'].get(side)
        previous_routes = {task.get('route_id') for task in (previous or {}).get('tasks', [])}
        state['routes'] = [route for route in state['routes'] if route['route_id'] not in previous_routes]
        active_manual = {route['unit_id'] for route in state['routes'] if route['status'] in ('queued', 'moving', 'waiting')}
        require(not active_manual.intersection(task['unit_id'] for task in plan['tasks']),
                'MANUAL_ROUTE_CONFLICT', '任务单位已有人工路线，请先停止原路线')
        tasks = [dict(task, status='queued', started_step=None) for task in plan['tasks']]
        state['coordination']['plans'][side] = dict(run_id=str(root['id']), proposal_id=proposal_id, status='adopted',
                                                   summary=plan['summary'], tasks=tasks)
        state['decisions']['plans'][side].update(status='adopted', source='multi_agent', proposal_id=proposal_id,
            priority=plan['summary'][:120], recon_assignment='按已采用侦察任务图执行',
            ground_assignment='按已采用陆兵任务图执行', support_assignment='按已采用保障任务图执行')
    connection.execute("UPDATE proposals SET status=%s,updated_at=clock_timestamp() WHERE id=%s", ('adopted' if adopted else 'rejected', proposal_id))
    connection.execute("UPDATE ai_runs SET status='succeeded',finished_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s", (root['id'],))
    return dict(proposal_id=proposal_id, adopted=adopted, summary='多智能体方案已采用' if adopted else '多智能体方案已驳回')
