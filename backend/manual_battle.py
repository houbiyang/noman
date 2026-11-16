"""新手动模式的纯状态提交屏障。

不依赖旧unit_interactions，不自行裁决、推进或激活旧局。调用方必须在game行锁和
同一事务内保存返回状态及round记录；执行器未验收时不得调用settle。
"""
from copy import deepcopy
import re
from fractions import Fraction

from pydantic import ValidationError

from backend.battle_contracts import BattleDraftRequest, BattleRoundRequest, BattleUnitIntent
from backend.demo_domain import require
from backend.runtime import sha256

SCHEMA = 'qinglan-manual-planning-v1'
SIDES = ('blue', 'red')
# 除保存/档案外，不能让旧自动推进、部署、AI方案或读档绕过手动提交屏障。
COMMANDS = frozenset(('battle_draft', 'battle_submit', 'battle_resolve', 'create_save', 'archive_match'))


def enabled(state):
    value = state.get('manual_battle')
    return isinstance(value, dict) and value.get('schema_version') == SCHEMA


def initialize(state):
    """仅给新局工厂使用；拒绝升级旧局、带时钟/已有任务的快照。不开放HTTP。"""
    require('manual_battle' not in state, 'MODE_FROZEN', '模式已冻结，不可重新初始化')
    manifest = state['manifest']
    require(manifest.get('battle_mode') == 'manual' and manifest.get('battle_scenario_sha256')
            and manifest.get('battle_rule_sha256'), 'BATTLE_VERSION_REQUIRED', '需要独立冻结的规则和场景')
    require('unit_interactions' not in state and 'coordination' not in state and 'victory' not in state,
            'MODE_CONFLICT', '手动总册新局不能混用旧裁决')
    require(state['lifecycle']['simulation_step'] == 0 and state['lifecycle']['phase'] == 'preparing',
            'NEW_GAME_REQUIRED', '只能初始化独立新局，不迁移旧局')
    state['manual_battle'] = dict(schema_version=SCHEMA, mode='manual', round_number=1,
        completed_rounds=0, elapsed_seconds=0, stage='planning',
        drafts={side: {} for side in SIDES}, submitted={side: False for side in SIDES},
        previous_round_sha256=None)
    validate(state)


def guard_command(state, kind):
    require('manual_battle' not in state or enabled(state), 'MANUAL_STATE_INVALID', '手动协议版本无效，拒绝退回旧模式')
    if enabled(state):
        require(kind in COMMANDS, 'MANUAL_BARRIER_REQUIRED', '本局仅通过双方提交及主持人结算推进，不能使用旧版指令')
    elif kind.startswith('battle_'):
        require(False, 'MANUAL_MODE_REQUIRED', '当前为旧版对局，不能迁移或直接使用总册手动指令')


def _planning(state, round_number):
    require(enabled(state), 'MANUAL_MODE_REQUIRED', '需要独立手动新局')
    validate(state)
    info = state['manual_battle']
    require(info['mode'] == 'manual', 'MODE_FROZEN', '开局模式不可切换')
    require(info['stage'] == 'planning', 'BATTLE_FINISHED', '本局已结束，不能继续决策')
    require(type(round_number) is int and round_number == info['round_number'],
            'ROUND_CONFLICT', '轮次已变化，请重新读取后操作')
    require(state['lifecycle']['simulation_step'] == info['completed_rounds'], 'ROUND_STATE_INVALID', '对局时钟与轮次不一致')
    return info


def _side(actor):
    require(actor in SIDES, 'ROLE_FORBIDDEN', '只能由红蓝各自提交本方指令，主持人不能代替', 403)


def _alive(unit):
    try:
        return Fraction(str(unit.get('durability', 0))) > 0 and not unit.get('retired', False)
    except (ValueError, ZeroDivisionError):
        require(False, 'MANUAL_STATE_INVALID', '单位耐久账本格式无效')


def _validate_intent(state, actor, intent):
    units = {unit['unit_id']: unit for unit in state['units']}
    unit = units.get(intent['unit_id'])
    require(unit is not None and unit['side'] == actor, 'UNIT_FORBIDDEN', '单位不存在或无权操作', 403)
    require(unit.get('life_id') == intent['life_id'], 'LIFE_CHANGED', '单位生命实例变化，请重新选择')
    require(_alive(unit), 'UNIT_RETIRED', '退场单位不能接收指令')
    task = intent.get('task')
    if not task:
        return
    action = task['action']
    types = {'attack': 'ground', 'attack_area': 'ground', 'recon': 'recon', 'resupply': 'support',
             'repair': 'support', 'takeoff': 'recon', 'land': 'recon'}
    require(action not in types or unit['unit_type'] == types[action], 'UNIT_ACTION_FORBIDDEN', '兵种不支持此任务')
    target_id = task.get('target_id')
    if target_id:
        target = units.get(target_id)
        if action in ('attack', 'recon'):
            visible = {u['unit_id']: u for u in state['observations'].get(actor, {}).get('contacts', [])}
            contact = visible.get(target_id)
            # 只按本方当前情报拒绝，不向客户端暴露隐藏敌军的生命变化或存活状态。
            require(contact is not None and contact.get('side') != actor
                    and contact.get('life_id') == task['target_life_id'],
                    'TARGET_NOT_VISIBLE', '目标不是当前本方合法可见生命实例')
        else:
            require(target is not None and target['side'] == actor and target_id != unit['unit_id']
                    and target.get('life_id') == task['target_life_id'] and _alive(target), 'SERVICE_TARGET_INVALID', '需要其他存活的本方目标')
            require(action != 'resupply' or task['resource'] != 'ammo' or target['unit_type'] == 'ground',
                    'NO_WEAPON', '目标没有武器弹药容量')
    # 路线、地表、视线、距离、静止、起降能源等执行资格必须在每个执行步重新裁决。
    # 草稿只保存意图，绝不传送、扣弹、提前起飞或自动选择/接近目标。


def draft(state, actor, payload):
    request = BattleDraftRequest.model_validate(payload)
    _side(actor)
    info = _planning(state, request.round_number)
    require(not info['submitted'][actor], 'SIDE_ALREADY_SUBMITTED', '本方已锁定本轮指令，不能再修改')
    items = [item.model_dump(mode='json', exclude_none=True) for item in request.intents]
    for item in items:
        _validate_intent(state, actor, item)
    result = deepcopy(state)
    target = result['manual_battle']['drafts'][actor]
    for item in items:
        # 同单位两个通道分别合并；编辑移动不抹掉已暂存的攻击任务，反之亦然。
        previous = target.get(item['unit_id'], dict(unit_id=item['unit_id'], life_id=item['life_id']))
        previous.update(item)
        target[item['unit_id']] = previous
        # 合并后再次检查起降与移动互斥，失败时不改变调用者状态。
        try:
            BattleUnitIntent.model_validate(previous)
        except ValidationError:
            require(False, 'INTENT_CONFLICT', '暂存任务与移动指令冲突，请先取消冲突通道再提交', 422)
    require(len(target) <= 30, 'CAPACITY_EXCEEDED', '本方草稿超过发布容量')
    return result



def validate_plan(state, actor):
    """锁定前检查本方完整持续计划，避免双方锁定后才发现可修正的静态冲突。

    不裁决地图、观察或距离；草稿允许暂时不完整，只有锁定时要求两通道和服务角色一致。
    """
    units = deepcopy([u for u in state['units'] if u['side'] == actor and _alive(u)])
    by_id = {u['unit_id']: u for u in units}
    for intent in state['manual_battle']['drafts'][actor].values():
        _validate_intent(state, actor, intent)
        unit = by_id[intent['unit_id']]
        for channel in ('movement', 'task'):
            if channel in intent:
                value = intent[channel]
                unit[channel] = None if value['action'] == 'stop' else deepcopy(value)
    sources, receivers, camp_receivers = set(), set(), set()
    from backend.battle_kernel import profiles, number
    for unit in units:
        task = unit.get('task') or {}
        action = task.get('action')
        require(not (unit.get('movement') and action in ('takeoff', 'land')),
                'INTENT_CONFLICT', '起降前请先停止持续移动')
        if action in ('resupply', 'repair'):
            target = by_id.get(task['target_id'])
            # 上轮已退场的接收者在执行器中自动清除，不阻止其他存活单位锁定。
            if target is None or target.get('life_id') != task['target_life_id']:
                continue
            account = 'durability' if action == 'repair' else task['resource']
            if 'target_balance' in task:
                amount = number(task['target_balance'])
                require(amount <= profiles()[target['unit_type']][account],
                        'BATTLE_SERVICE_INVALID', '服务目标余额不能超过目标容量')
                if number(target.get(account, 0)) >= amount:
                    continue
            elif number(task['quantity']) == 0:
                continue
            identity = target['unit_id']
            require(identity not in receivers, 'BATTLE_SERVICE_CONFLICT', '接收者只能指定一个服务来源')
            sources.add(unit['unit_id']); receivers.add(identity)
        elif action == 'barracks':
            # 冻结兵营身份由执行器的Geometry在锁定前校验，不能猜用旧camps。
            profile = profiles()[unit['unit_type']]
            maxima = dict(energy=profile['energy'], ammo=profile['ammo'], durability=profile['durability'],
                **{'stock_' + key: value for key, value in profile.get('stocks', {}).items()})
            require(all(number(value) <= maxima.get(key, 0) for key, value in task.get('targets', {}).items()),
                    'BATTLE_SERVICE_INVALID', '兵营目标余额不能超过本单位容量')
            camp_receivers.add(unit['unit_id'])
    require(not sources.intersection(receivers) and not camp_receivers.intersection(sources | receivers),
            'BATTLE_SERVICE_CONFLICT', '单个单位同一步只能提供或接收一种服务，不能兼任兵营和野外服务')
    require(all(not by_id[i].get('movement') for i in sources | receivers | camp_receivers),
            'INTENT_CONFLICT', '服务供受双方请先明确停止持续移动')


def submit(state, actor, payload):
    request = BattleRoundRequest.model_validate(payload)
    _side(actor)
    _planning(state, request.round_number)
    validate_plan(state, actor)
    result = deepcopy(state)
    # 允许明确提交空草稿（保持现有任务/待命）；重复提交不解锁、不推进。
    result['manual_battle']['submitted'][actor] = True
    return result


def projection(state, actor):
    require(actor in (*SIDES, 'host'), 'ROLE_FORBIDDEN', '席位无效', 403)
    info = state['manual_battle']
    result = {key: deepcopy(info[key]) for key in ('schema_version', 'mode', 'round_number',
        'completed_rounds', 'elapsed_seconds', 'stage', 'submitted')}
    result.update(round_seconds=60, maximum_rounds=60,
        can_edit=actor in SIDES and info['stage'] == 'planning' and not info['submitted'][actor],
        can_submit=actor in SIDES and info['stage'] == 'planning' and not info['submitted'][actor],
        can_resolve=actor == 'host' and info['stage'] == 'planning' and all(info['submitted'].values()))
    # 主持人只见双方锁定状态，不提前取得任一方私有指令。
    result['own_drafts'] = list(deepcopy(info['drafts'][actor]).values()) if actor in SIDES else []
    return result


def settle(state, actor, payload, executor):
    """纯事务构建器；executor必须返回已验收的60秒裁决和不可变回放记录。

    此处没有默认执行器，尤其不能隐式退回旧unit_interactions；异常不修改入参。
    """
    request = BattleRoundRequest.model_validate(payload)
    require(actor == 'host', 'ROLE_FORBIDDEN', '只能由主持人统一结算', 403)
    info = _planning(state, request.round_number)
    require(all(info['submitted'].values()), 'BOTH_SIDES_REQUIRED', '必须等待红蓝双方锁定本轮指令')
    require(executor is not None and callable(getattr(executor, 'project_world', None)), 'BATTLE_ENGINE_NOT_READY', '总册执行器未验收，不能使用旧演示裁决', 503)
    before = deepcopy(state)
    intents = {side: list(deepcopy(info['drafts'][side]).values()) for side in SIDES}
    after, record = executor(deepcopy(before), intents)
    from backend.battle_replay import validate_record
    validate_record(record)
    require(record['round_number'] == request.round_number
            and record['start_seconds'] == info['elapsed_seconds']
            and record['previous_round_sha256'] == info['previous_round_sha256'],
            'ROUND_RECORD_INVALID', '执行结果的轮次或回放链不一致')
    require(after['manifest'] == before['manifest'], 'BATTLE_VERSION_CHANGED', '执行器不能更换冻结版本')
    require(record['rule_sha256'] == before['manifest']['battle_rule_sha256']
            and record['scenario_sha256'] == before['manifest']['battle_scenario_sha256'],
            'BATTLE_VERSION_CHANGED', '回放版本与本局冻结版本不一致')
    require(record['initial_world'] == executor.project_world(before)
            and record['frames'][-1]['world'] == executor.project_world(after),
            'ROUND_RECORD_INVALID', '回放首尾必须对应实际裁决世界，不接受预演自算结果')
    elapsed = record['end_seconds']
    finished = record['finished']
    require(elapsed == info['elapsed_seconds'] + 60 or finished, 'ROUND_DURATION_INVALID', '非终局必须完整执行60仿真秒')
    count = info['completed_rounds'] + 1
    require(count <= 60 and (count < 60 or finished), 'BATTLE_DEADLINE_REQUIRED', '第60轮必须结算终局')
    after['manual_battle'] = dict(schema_version=SCHEMA, mode='manual', round_number=min(count + 1, 60),
        completed_rounds=count, elapsed_seconds=elapsed, stage='finished' if finished else 'planning',
        drafts={side: {} for side in SIDES}, submitted={side: False for side in SIDES},
        previous_round_sha256=record['payload_sha256'])
    after['lifecycle'].update(simulation_step=count, phase='finished' if finished else 'paused', controller_session_id=None)
    require(record['before_world_sha256'] == sha256(record['initial_world'])
            and record['after_world_sha256'] == sha256(record['frames'][-1]['world']),
            'ROUND_RECORD_INVALID', '回放世界摘要无效')
    return after, deepcopy(record)


def validate(state):
    """持久化边界校验；版本、席位、轮次、已锁定草稿不能被畸形快照绕过。"""
    require(enabled(state), 'MANUAL_STATE_INVALID', '手动协议版本无效')
    info = state['manual_battle']
    manifest = state.get('manifest', {})
    require(manifest.get('battle_mode') == 'manual'
            and all(isinstance(manifest.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', manifest[key])
                    for key in ('battle_rule_sha256', 'battle_scenario_sha256')),
            'MANUAL_STATE_INVALID', '缺少冻结规则或场景摘要')
    require(info.get('mode') == 'manual' and info.get('stage') in ('planning', 'finished'),
            'MANUAL_STATE_INVALID', '手动模式或阶段无效')
    count, elapsed = info.get('completed_rounds'), info.get('elapsed_seconds')
    require(type(count) is int and 0 <= count <= 60 and type(elapsed) is int
            and 0 <= elapsed <= 3600 and elapsed % 5 == 0,
            'MANUAL_STATE_INVALID', '轮次时钟无效')
    require(type(info.get('round_number')) is int and info['round_number'] == min(count + 1, 60)
            and state['lifecycle']['simulation_step'] == count,
            'MANUAL_STATE_INVALID', '轮次与权威时钟不一致')
    require(count == 0 and elapsed == 0 or count > 0 and (count - 1) * 60 < elapsed <= count * 60,
            'MANUAL_STATE_INVALID', '轮次累计时间无效')
    require(info['stage'] == 'finished' or count < 60 and elapsed == count * 60,
            'MANUAL_STATE_INVALID', '未终局不能跳过完整轮次')
    previous = info.get('previous_round_sha256')
    require(previous is None if count == 0 else isinstance(previous, str) and re.fullmatch(r'[0-9a-f]{64}', previous),
            'MANUAL_STATE_INVALID', '已完成轮次必须绑定不可变回放前驱')
    phase = state.get('lifecycle', {}).get('phase')
    require(phase == 'finished' if info['stage'] == 'finished' else phase in (('preparing', 'paused') if count == 0 else ('paused',)),
            'MANUAL_STATE_INVALID', '协议阶段与对局阶段不一致')
    require(count > 0 or info['stage'] != 'finished', 'MANUAL_STATE_INVALID', '无裁决记录不能标记终局')
    require(set(info.get('submitted', {})) == set(SIDES)
            and all(type(v) is bool for v in info['submitted'].values())
            and set(info.get('drafts', {})) == set(SIDES), 'MANUAL_STATE_INVALID', '席位提交状态无效')
    require(not ('unit_interactions' in state or 'coordination' in state or 'victory' in state),
            'MODE_CONFLICT', '不能在新模式混用旧裁决')
    for side in SIDES:
        drafts = info['drafts'][side]
        require(isinstance(drafts, dict) and len(drafts) <= 30, 'MANUAL_STATE_INVALID', '草稿容量无效')
        if drafts:
            try:
                batch = BattleDraftRequest.model_validate(dict(round_number=info['round_number'], intents=list(drafts.values())))
            except ValidationError:
                require(False, 'MANUAL_STATE_INVALID', '快照包含非法指令草稿')
            require(all(key == item['unit_id'] for key, item in drafts.items()), 'MANUAL_STATE_INVALID', '草稿索引无效')
            for item in batch.intents:
                _validate_intent(state, side, item.model_dump(mode='json', exclude_none=True))
