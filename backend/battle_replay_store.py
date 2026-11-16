"""复用既有append-only game_events存储逐轮动画，不增加可改写播放状态表。"""
from copy import deepcopy
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.battle_replay import validate_record
from backend.demo_domain import require


def append_round(connection, *, game_id, branch_id, snapshot_id, snapshot_revision, command_id, record):
    """供未来已验收执行器的同一提交事务调用，禁止独立于权威快照追加回放。

    快照/命令外键、event唯一事实键及现有不可变触发器共同提供重复/篡改保护。
    调用方仍须持有game行锁；不捕获冲突、不覆盖记录、不创建任何新游戏。
    """
    validate_record(record)
    row = connection.execute('''SELECT s.state_payload,c.command_type,c.game_id AS command_game
        FROM game_snapshots s JOIN game_commands c ON c.id=%s
        WHERE s.id=%s AND s.game_id=%s AND s.branch_id=%s AND s.revision=%s
          AND s.source_command_id=c.id AND c.origin_branch_id=s.branch_id
          AND c.request_payload->>'round_number'=%s''',
        (command_id, snapshot_id, game_id, branch_id, snapshot_revision, str(record['round_number']))).fetchone()
    require(row is not None and str(row['command_game']) == str(game_id) and row['command_type'] == 'battle_resolve',
            'REPLAY_COMMIT_REQUIRED', '回放必须绑定同局主持人结算命令及其权威结果快照')
    state = row['state_payload']
    info = state.get('manual_battle', {})
    require(info.get('completed_rounds') == record['round_number']
            and info.get('elapsed_seconds') == record['end_seconds']
            and info.get('previous_round_sha256') == record['payload_sha256']
            and state['manifest'].get('battle_rule_sha256') == record['rule_sha256']
            and state['manifest'].get('battle_scenario_sha256') == record['scenario_sha256'],
            'REPLAY_COMMIT_REQUIRED', '回放与权威结果快照/冻结版本不一致')
    if record['round_number'] > 1:
        predecessor = connection.execute("""SELECT payload FROM game_events
            WHERE game_id=%s AND branch_id=%s AND snapshot_revision<%s AND event_type='battle_round'
              AND visibility='host' AND fact_key=%s""",
            (game_id, branch_id, snapshot_revision, 'battle-round/%d' % (record['round_number'] - 1))).fetchone()
        require(predecessor is not None, 'REPLAY_PREFIX_INVALID', '必须先保存同分支前一轮回放，不能跳轮追加')
        previous = predecessor['payload']['record']
        validate_record(previous)
        require(not previous['finished'] and previous['payload_sha256'] == record['previous_round_sha256']
                and previous['end_seconds'] == record['start_seconds']
                and previous['rule_sha256'] == record['rule_sha256']
                and previous['scenario_sha256'] == record['scenario_sha256']
                and previous['frames'][-1]['world'] == record['initial_world'],
                'REPLAY_CHAIN_INVALID', '回放必须衔接同分支已持久化的前一轮真实世界及摘要')
    payload = dict(title='第%d轮演示动画' % record['round_number'], detail='只读回放', side='host',
                   private_plan=True, record=deepcopy(record))
    connection.execute('''INSERT INTO game_events
        (id,game_id,branch_id,snapshot_id,snapshot_revision,command_id,event_index,fact_key,
         event_type,simulation_step,visibility,payload)
        VALUES (%s,%s,%s,%s,%s,%s,1,%s,'battle_round',%s,'host',%s)''',
        (uuid4(), game_id, branch_id, snapshot_id, snapshot_revision, command_id,
         'battle-round/%d' % record['round_number'], record['round_number'], Jsonb(payload)))


def read_rounds(connection, game, round_number=None):
    """只读取索引或指定单轮。完整播放由前端依次请求，不一次下载60轮大载荷。"""
    ceiling = game['state_payload']['manual_battle']['completed_rounds']
    args = [game['id'], game['active_branch_id'], game['snapshot_revision']]
    where = "game_id=%s AND branch_id=%s AND snapshot_revision<=%s AND event_type='battle_round' AND visibility='host'"
    if round_number is not None:
        require(type(round_number) is int and 1 <= round_number <= 60, 'ROUND_INVALID', '轮次须为1至60', 422)
        require(round_number <= ceiling, 'REPLAY_NOT_FOUND', '该轮尚未完成或不属于当前分支', 404)
        row = connection.execute('SELECT payload FROM game_events WHERE ' + where + ' AND fact_key=%s',
                                 (*args, 'battle-round/%d' % round_number)).fetchone()
        require(row is not None, 'REPLAY_NOT_FOUND', '该轮尚未完成或不属于当前分支', 404)
        record = row['payload']['record']
        validate_record(record)
        require(record['round_number'] == round_number
                and record['rule_sha256'] == game['state_payload']['manifest']['battle_rule_sha256']
                and record['scenario_sha256'] == game['state_payload']['manifest']['battle_scenario_sha256'],
                'REPLAY_VERSION_INVALID', '回放版本与当前新局不一致')
        return dict(record=record, read_only=True, writes_game_state=False)
    rows = connection.execute('''SELECT simulation_step AS round_number,
        payload->'record'->>'start_seconds' AS start_seconds,
        payload->'record'->>'end_seconds' AS end_seconds,
        payload->'record'->>'payload_sha256' AS payload_sha256
        FROM game_events WHERE ''' + where + ' ORDER BY simulation_step', args).fetchall()
    require([row['round_number'] for row in rows] == list(range(1, ceiling + 1)),
            'REPLAY_PREFIX_INVALID', '已完成轮次的回放前缀不完整，不能跳过缺失轮次')
    return dict(items=[dict(round_number=r['round_number'], start_seconds=int(r['start_seconds']),
        end_seconds=int(r['end_seconds']), payload_sha256=r['payload_sha256']) for r in rows],
        completed_rounds=ceiling, maximum_rounds=60, round_seconds=60, read_only=True, writes_game_state=False)
