"""把五点共同规划接入现有事务和快照；网络调用不持有数据库锁。"""

from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4, uuid5, NAMESPACE_URL
from psycopg.types.json import Jsonb

from backend.agent_contracts import AgentConfiguration
from backend.agent_model import MessageModel
from backend.demo_domain import host_only, require
from backend import fivepoint_engine as engine, fivepoint_service
from backend.fivepoint_planning import run_round
from backend.fivepoint_playback import can_produce
from backend.runtime import utcnow, sha256
from backend.contracts import CommandRequest
from backend.service import insert, HASH_VERSION


class FivepointCoordinator:
    """共同轮由数据库头及随机运行标识锁定；分支变化使晚到结果失效。"""

    def __init__(self, sandbox, model_factory=MessageModel):
        """复用已有连接与快照写入，测试可注入模型替身。"""
        self.sandbox = sandbox
        self.model_factory = model_factory

    def persist(self, connection, game, state):
        """在调用方事务内替换完整快照及自动存档，不覆盖旧帧。"""
        fivepoint_service.synchronize(state)
        snapshot_id, command_id = uuid4(), uuid4()
        host = connection.execute("SELECT id FROM game_members WHERE game_id=%s AND role='host'", (game['id'],)).fetchone()
        summary = dict(run_id=state['fivepoint'].get('planning_run', {}).get('run_id'), phase=state['fivepoint']['phase'])
        insert(connection, 'game_commands', dict(id=command_id, game_id=game['id'], origin_branch_id=game['active_branch_id'],
            actor_member_id=host['id'], request_key=uuid4(), request_hash=sha256(summary), hash_format_version=HASH_VERSION,
            command_type='fivepoint_planning', expected_control_revision=game['control_revision'], base_snapshot_id=game['head_snapshot_id'],
            request_payload=summary, source_kind='scheduler', execution_epoch=game['execution_epoch'], status='accepted'))
        self.sandbox._snapshot(connection, game['id'], game['active_branch_id'], snapshot_id, state,
                               game['snapshot_revision'] + 1, game['world_revision'] + 1, command_id)
        result = dict(branch_id=str(game['active_branch_id']), snapshot_id=str(snapshot_id), simulation_step=state['fivepoint']['step'])
        connection.execute("""UPDATE game_commands SET status='succeeded',result_branch_id=%s,result_snapshot_id=%s,
            result_payload=%s,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s""",
                           (game['active_branch_id'], snapshot_id, Jsonb(result), command_id))
        connection.execute('UPDATE games SET head_snapshot_id=%s,control_revision=control_revision+1,updated_at=clock_timestamp() WHERE id=%s',
                           (snapshot_id, game['id']))
        connection.execute("""INSERT INTO game_saves(id,game_id,branch_id,snapshot_id,save_kind,source_command_id)
            VALUES (%s,%s,%s,%s,'auto',%s) ON CONFLICT (game_id,branch_id) WHERE save_kind='auto'
            DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id,source_command_id=EXCLUDED.source_command_id,updated_at=clock_timestamp()""",
                           (uuid4(), game['id'], game['active_branch_id'], snapshot_id, command_id))
        insert(connection, 'game_events', dict(id=uuid4(), game_id=game['id'], branch_id=game['active_branch_id'], snapshot_id=snapshot_id,
            snapshot_revision=game['snapshot_revision'] + 1, command_id=command_id, event_index=0, fact_key=str(command_id),
            event_type='fivepoint_planning', simulation_step=state['fivepoint']['step'], visibility='public',
            payload=dict(title='共同规划状态更新', detail=summary['phase'], side='host', visibility='public')))

    def request(self, game_id, bearer, request):
        """主持人明确触发同一轮，冻结输入之前核对模式、配置与播放背压。"""
        with self.sandbox.settings.connect() as connection:
            principal = self.sandbox._principal(connection, bearer, game_id)
            host_only(principal['role'])
            game = self.sandbox._game(connection, game_id, lock=True)
            self.sandbox._check_context(game, principal, request.context.model_dump(mode='json'))
            state = deepcopy(game['state_payload'])
            require(fivepoint_service.enabled(state), 'FIVEPOINT_REQUIRED', '须先启用五点规则')
            core = state['fivepoint']
            require(core['phase'] == 'planning' and not core['pending_plans'], 'PLAN_BOUNDARY_INVALID', '须处于未处理的共同规划边界')
            require(not core.get('planning_run', {}).get('active'), 'PLANNING_BUSY', '本轮已经开始计算')
            previous = core.get('planning_run', {})
            require(previous.get('step') != core['step'] or previous.get('recovery_round') != core['recovery_round'],
                    'PLAN_ALREADY_COMPUTED', '本轮已经计算，不提供额外规划机会')
            require(can_produce(core['step'] * 5, request.played_second), 'PLAYBACK_BACKPRESSURE', '等待观看端消费已确认结果')
            configurations = {side: AgentConfiguration.model_validate(core['frozen']['ai_configurations'][side]) for side in ('red', 'blue')}
            require(all(MessageModel.available(configuration) for configuration in configurations.values()),
                    'MODEL_NOT_CONFIGURED', '冻结的双方模型尚未就绪', 503)
            run_id = str(uuid4())
            core['planning_run'] = dict(run_id=run_id, active=True, branch_id=str(game['active_branch_id']),
                                        step=core['step'], recovery_round=core['recovery_round'],
                                        execution_epoch=str(game['execution_epoch']), deadline=(utcnow() + timedelta(seconds=120)).isoformat())
            self.persist(connection, game, state)
            return dict(run_id=run_id, status='planning')

    def execute(self, game_id, run_id):
        """双边计算后复核运行标识、分支和执行代次，再共同提交或技术暂停。"""
        with self.sandbox.settings.connect() as connection:
            game = self.sandbox._game(connection, game_id)
            basis = deepcopy(game['state_payload']['fivepoint'])
        run = basis.get('planning_run', {})
        if run.get('run_id') != run_id or not run.get('active'):
            return
        remaining = max(0, (datetime.fromisoformat(run['deadline']) - utcnow()).total_seconds())
        configurations = {side: AgentConfiguration.model_validate(basis['frozen']['ai_configurations'][side]) for side in ('red', 'blue')}
        result = (run_round(basis, configurations, self.model_factory, min(120, remaining)) if remaining > 0
                  else engine.fail_planning(basis, 'red', 'PLANNING_TIMEOUT'))
        with self.sandbox.settings.connect() as connection:
            game = self.sandbox._game(connection, game_id, lock=True)
            state = deepcopy(game['state_payload'])
            current = state.get('fivepoint', {})
            if (current.get('planning_run') != run or current.get('phase') != 'planning'
                    or current.get('pending_plans') or current.get('surrender')
                    or str(game['active_branch_id']) != run['branch_id'] or str(game['execution_epoch']) != run['execution_epoch']):
                return
            if result['phase'] == 'paused' and current['frozen'].get('control_mode', 'manual') == 'manual':
                current['planning_candidates'] = {
                    side: [{key: value for key, value in task.items() if key not in ('status', 'active_steps', 'completed_step', 'reason')}
                           for task in tasks] for side, tasks in result['tasks'].items()}
                current['planning_feedback'] = result['planning_feedback']
                current['planning_source'] = 'model'
                result = current
            result['planning_run'] = dict(run, active=False)
            state['fivepoint'] = result
            self.persist(connection, game, state)

    def advance_round(self, game_id, bearer, request):
        """最多推进本轮剩余完整步，每步独立原子提交，暂停或分支改变立即停止。"""
        with self.sandbox.settings.connect() as connection:
            principal = self.sandbox._principal(connection, bearer, game_id)
            host_only(principal['role'])
            game = self.sandbox._game(connection, game_id, lock=True)
            self.sandbox._check_context(game, principal, request.context.model_dump(mode='json'))
            require(fivepoint_service.enabled(game['state_payload']), 'FIVEPOINT_REQUIRED', '须启用五点规则')
            core = game['state_payload']['fivepoint']
            require(core['phase'] in ('paused', 'running') and core['result'] is None,
                    'PLAN_BOUNDARY_INVALID', '须先共同采用当前轮方案')
            round_start = core['step'] // 12 * 12
            require(request.played_second <= core['step'] * 5
                    and can_produce(round_start * 5, min(request.played_second, round_start * 5)),
                    'PLAYBACK_BACKPRESSURE', '缓存已满，等待消费后再推进')
            branch, epoch = str(game['active_branch_id']), game['execution_epoch']
            expected_snapshot = str(game['head_snapshot_id'])
            end_step = min(720, round_start + 12)
        completed = 0
        while True:
            with self.sandbox.settings.connect() as connection:
                game = self.sandbox._game(connection, game_id)
                principal = self.sandbox._principal(connection, bearer, game_id)
                core = game['state_payload']['fivepoint']
                if (str(game['active_branch_id']) != branch or game['execution_epoch'] != epoch
                        or str(game['head_snapshot_id']) != expected_snapshot or core['step'] >= end_step
                        or core['phase'] not in ('paused', 'running') or core['result'] is not None):
                    break
                command = CommandRequest(context=self.sandbox._context(game, principal), command_type='advance_step', payload={})
                request_key = uuid5(NAMESPACE_URL, f'fivepoint/{game_id}/{branch}/{epoch}/{core["step"]}/{expected_snapshot}')
            receipt, status = self.sandbox.command(game_id, bearer, request_key, command)
            if status != 200:
                return dict(completed_steps=completed, status='interrupted', error=receipt['error'])
            expected_snapshot = receipt['result']['snapshot_id']
            completed += 1
        return dict(completed_steps=completed, status='committed')

    def recover_expired(self):
        """进程重启或工作线程中断后将过期轮置技术暂停，不重复调用或静默采用。"""
        with self.sandbox.settings.connect() as connection:
            rows = connection.execute("""SELECT games.id FROM games JOIN game_snapshots snapshots ON games.head_snapshot_id=snapshots.id
                WHERE snapshots.state_payload->'fivepoint'->'planning_run'->>'active'='true'
                AND (snapshots.state_payload->'fivepoint'->'planning_run'->>'deadline')::timestamptz < clock_timestamp()""").fetchall()
        for row in rows:
            with self.sandbox.settings.connect() as connection:
                game = self.sandbox._game(connection, row['id'], lock=True)
                state = deepcopy(game['state_payload'])
                core = state['fivepoint']
                run = core.get('planning_run', {})
                if core['phase'] == 'planning' and run.get('active') and datetime.fromisoformat(run['deadline']) < utcnow():
                    state['fivepoint'] = engine.fail_planning(core, 'red', 'PLANNING_INTERRUPTED')
                    state['fivepoint']['planning_run']['active'] = False
                    self.persist(connection, game, state)
