"""总册数值裁决核心：纯JSON状态、精确有理数、失败原子性。

不接受HTTP参数，不提供地图/视线的默认实现。geometry证据必须由冻结地图适配器
生成；这些函数本身不是可发布执行器。旧局不会自动获得此账本。
"""
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
import json
from pathlib import Path
import re

from backend.demo_domain import require
from backend.battle_terrain import slope_factor
from backend.runtime import sha256

SIDES = ('blue', 'red')
RESOURCES = ('energy', 'ammo', 'repair')
SCHEMA = 'qinglan-exact-ledger-v1'
FIELD_ENERGY_SERVICE_POLICY = 'support-to-hovering-recon-v1'


def allows_hover_energy(source, target, resource):
    return (resource == 'energy' and 'camp_id' not in source and source['unit_type'] == 'support'
            and target['unit_type'] == 'recon' and target['air_state'] == 'flying')


@lru_cache(maxsize=1)
def _parameters():
    return json.loads(Path(__file__).with_name('rulebook-parameters-v1.json').read_text())


def number(value):
    """账本不接受float、布尔、指数、NaN及过大的字符串。"""
    require(type(value) is int or isinstance(value, str), 'BATTLE_NUMBER_INVALID', '账本须使用精确数字')
    text = str(value)
    require(len(text) <= 80 and re.fullmatch(r'\d+(?:\.\d+|/\d+)?', text),
            'BATTLE_NUMBER_INVALID', '账本数字格式无效')
    try:
        result = Fraction(text)
    except (ValueError, ZeroDivisionError):
        require(False, 'BATTLE_NUMBER_INVALID', '账本分母无效')
    return result


def exact(value):
    value = Fraction(value)
    require(value >= 0, 'BATTLE_NUMBER_INVALID', '账本不能为负')
    text = str(value)
    require(len(text) <= 80, 'BATTLE_NUMBER_INVALID', '账本精度超出协议容量')
    return text


def profiles():
    return deepcopy(_parameters()['unit_profiles'])


def alive(unit):
    return not unit.get('retired', False) and number(unit['durability']) > 0


def _units(state):
    result = {unit['unit_id']: unit for unit in state['units']}
    require(len(result) == len(state['units']), 'BATTLE_STATE_INVALID', '单位标识重复')
    return result


def resource_totals(state, side):
    total = {key: number(state['battle_ledger'][side]['reserve'][key]) for key in RESOURCES}
    for unit in state['units']:
        if unit['side'] != side:
            continue
        total['energy'] += number(unit['energy'])
        total['ammo'] += number(unit['ammo'])
        for key in RESOURCES:
            total[key] += number(unit.get('cargo', {}).get(key, '0'))
    return total


def _roster_hash(units):
    return sha256(sorted((u['unit_id'], u['life_id'], u['side'], u['unit_type']) for u in units))


def _initial_account(units, side):
    own = [u for u in units if u['side'] == side]
    p = _parameters()
    result = {key: p['barracks']['public_reserve_per_side'][key] for key in RESOURCES}
    for unit in own:
        profile = p['unit_profiles'][unit['unit_type']]
        for key in RESOURCES:
            result[key] += profile.get(key, 0) + profile.get('stocks', {}).get(key, 0)
    return result, sum(p['unit_profiles'][u['unit_type']]['kill_value'] for u in own)


def initialize(state):
    """仅新工厂可调用；满值名册、固定分母，拒绝重建已有账本/死亡单位。"""
    result = deepcopy(state)
    require('battle_ledger' not in result and result['lifecycle']['phase'] == 'preparing'
            and result['lifecycle']['simulation_step'] == 0,
            'NEW_GAME_REQUIRED', '只能在新局准备时初始化账本')
    units = _units(result)
    require(units and len({u['life_id'] for u in units.values()}) == len(units),
            'BATTLE_STATE_INVALID', '单位生命实例必须唯一')
    p = _parameters()
    result['battle_ledger'] = dict(schema_version=SCHEMA, elapsed_seconds=0, roster_sha256=_roster_hash(units.values()))
    for side in SIDES:
        own = [u for u in units.values() if u['side'] == side]
        require(own and all(u['side'] in SIDES for u in units.values()),
                'BATTLE_STATE_INVALID', '双方至少各有一个单位且席位合法')
        for unit in own:
            require(unit['unit_type'] in p['unit_profiles'] and alive(unit),
                    'BATTLE_STATE_INVALID', '新局单位必须存活且类型合法')
            profile = p['unit_profiles'][unit['unit_type']]
            for field in ('durability', 'energy', 'ammo'):
                require(number(unit.get(field, profile[field])) == profile[field],
                        'BATTLE_STATE_INVALID', '新局单位必须满资源且不可重置受损单位')
                unit[field] = str(profile[field])
            unit.update(cargo={k: str(profile.get('stocks', {}).get(k, 0)) for k in RESOURCES},
                retired=False, retirement_reason=None, next_fire_seconds=0,
                last_fire_seconds=None, last_damage_seconds=None, target_cursor=None,
                air_state='docked' if unit['unit_type'] == 'recon' else 'ground')
        initial, initial_value = _initial_account(units.values(), side)
        result['battle_ledger'][side] = dict(initial={k: str(v) for k, v in initial.items()},
            reserve={k: str(v) for k, v in p['barracks']['public_reserve_per_side'].items()},
            consumed={k: '0' for k in RESOURCES}, lost={k: '0' for k in RESOURCES},
            kills='0', control_steps=0,
            initial_value=str(initial_value),
            barracks_cursor=None)
    validate(result)
    return result


def validate(state):
    ledger = state['battle_ledger']
    require(ledger.get('schema_version') == SCHEMA, 'BATTLE_STATE_INVALID', '账本版本无效')
    require(type(ledger['elapsed_seconds']) is int and 0 <= ledger['elapsed_seconds'] <= 3600
            and ledger['elapsed_seconds'] % 5 == 0, 'BATTLE_STATE_INVALID', '账本时钟无效')
    units = _units(state)
    require(len({u['life_id'] for u in units.values()}) == len(units), 'BATTLE_STATE_INVALID', '生命实例重复')
    p = _parameters()
    require(ledger.get('roster_sha256') == _roster_hash(units.values()),
            'BATTLE_STATE_INVALID', '战中不能变更冻结名册、兵种或生命实例')
    for unit in units.values():
        require(unit['side'] in SIDES and unit['unit_type'] in p['unit_profiles'],
                'BATTLE_STATE_INVALID', '单位类型或阵营无效')
        profile = p['unit_profiles'][unit['unit_type']]
        for field in ('durability', 'energy', 'ammo'):
            value = number(unit[field])
            require(0 <= value <= profile[field] and (field != 'ammo' or value.denominator == 1),
                    'BATTLE_STATE_INVALID', '单位资源超容量或弹药不是整数')
        for field in RESOURCES:
            value = number(unit['cargo'][field])
            require(value <= profile.get('stocks', {}).get(field, 0)
                    and (field != 'ammo' or value.denominator == 1), 'BATTLE_STATE_INVALID', '货物无效')
        require(type(unit['retired']) is bool and unit['air_state'] in (
                    ('docked', 'flying', 'taking_off', 'landing') if unit['unit_type'] == 'recon' else ('ground',)),
                'BATTLE_STATE_INVALID', '退场或飞行状态无效')
        require(type(unit['next_fire_seconds']) is int and unit['next_fire_seconds'] >= 0
                and unit['next_fire_seconds'] % 5 == 0, 'BATTLE_STATE_INVALID', '开火冷却无效')
        for field in ('last_fire_seconds', 'last_damage_seconds'):
            stamp = unit[field]
            require(stamp is None or (type(stamp) is int and 5 <= stamp <= ledger['elapsed_seconds'] + 5
                    and stamp % 5 == 0), 'BATTLE_STATE_INVALID', '射击或受伤时间戳无效')
        if unit['retired']:
            require(number(unit['durability']) == 0 and number(unit['energy']) == 0 and number(unit['ammo']) == 0
                    and all(number(v) == 0 for v in unit['cargo'].values()),
                    'BATTLE_STATE_INVALID', '退场单位不能持有可用资源')
        else:
            require(number(unit['durability']) > 0, 'BATTLE_STATE_INVALID', '零耐久必须永久退场')
    for side in SIDES:
        account = ledger[side]
        initial, value = _initial_account(units.values(), side)
        require(any(u['side'] == side for u in units.values()) and number(account['initial_value']) == value,
                'BATTLE_STATE_INVALID', '击毁价值分母必须等于冻结名册')
        for field in RESOURCES:
            require(number(account['initial'][field]) == initial[field],
                    'BATTLE_STATE_INVALID', '资源分母必须等于冻结名册及初始公共库存')
            require(number(account['initial'][field]) > 0, 'BATTLE_STATE_INVALID', '资源分母须为正')
            require(number(account['reserve'][field]) <= p['barracks']['public_reserve_per_side'][field],
                    'BATTLE_STATE_INVALID', '公共库存超过初始容量')
            require(number(account['initial'][field]) == resource_totals(state, side)[field]
                    + number(account['consumed'][field]) + number(account['lost'][field]),
                    'BATTLE_RESOURCE_IMBALANCE', '资源账本不守恒')
        require(type(account['control_steps']) is int and 0 <= account['control_steps'] <= 3600,
                'BATTLE_STATE_INVALID', '点步无效')
        enemy = 'red' if side == 'blue' else 'blue'
        require(0 <= number(account['kills']) <= number(ledger[enemy]['initial_value']),
                'BATTLE_STATE_INVALID', '击毁价值超过敌方初始总值')


def _add(account, key, delta):
    account[key] = exact(number(account[key]) + delta)


def consume_energy(state, unit, amount):
    amount = number(exact(amount))
    require(amount <= number(unit['energy']), 'BATTLE_ENERGY_INSUFFICIENT', '能源不足')
    _add(unit, 'energy', -amount)
    _add(state['battle_ledger'][unit['side']]['consumed'], 'energy', amount)


def _retire(state, unit, reason, attacker_side=None):
    if unit['retired']:
        return False
    account = state['battle_ledger'][unit['side']]
    for field in RESOURCES:
        carried = number(unit['cargo'][field])
        if field in ('energy', 'ammo'):
            carried += number(unit[field])
            unit[field] = '0'
        _add(account['lost'], field, carried)
        unit['cargo'][field] = '0'
    unit.update(durability='0', retired=True, retirement_reason=reason, movement=None, task=None)
    unit.pop('movement_progress', None)
    unit.pop('geometry_position', None)
    if attacker_side:
        require(attacker_side in SIDES and attacker_side != unit['side'], 'BATTLE_STATE_INVALID', '击毁归属无效')
        _add(state['battle_ledger'][attacker_side], 'kills', _parameters()['unit_profiles'][unit['unit_type']]['kill_value'])
    return True


def _segment_rates(kind, segment):
    """时间预算与累计路径计费共用同一费率，不在计费阶段再次截断5秒。"""
    p = _parameters()
    require(kind in p['unit_profiles'], 'BATTLE_STATE_INVALID', '兵种无效')
    require(('grade' in segment) != ('grade_squared' in segment),
            'BATTLE_SLOPE_INVALID', '路段须提供唯一坡度证据，不能缺失或混用')
    grade_squared = (number(segment['grade_squared']) if 'grade_squared' in segment
                     else number(segment['grade']) ** 2)
    surface = segment['surface']
    if kind == 'recon':
        require(surface == 'air', 'BATTLE_SURFACE_INVALID', '飞行段必须是已核验空域')
        factor = Fraction(1)
    else:
        f = p['terrain']['surface_factors'][kind].get(surface)
        require(f is not None, 'BATTLE_SURFACE_INVALID', '禁行或未知地表')
        factor = number(str(f)) * slope_factor(kind, grade_squared)
    profile = p['unit_profiles'][kind]
    return Fraction(profile['speed_m_per_minute']) * factor, Fraction(profile['energy_per_km'], 1000) / factor


def movement_cost(kind, segments, *, quantum=None):
    """按已记录累计路程增量精确收费；绝不重复应用时间预算或路段取整。"""
    cost = Fraction(0)
    for segment in segments:
        _, energy_per_m = _segment_rates(kind, segment)
        cost += number(segment['length_mm']) / 1000 * energy_per_m
    if quantum is not None:
        # Local manual billing uses bounded fixed-point amounts. Round down so
        # quantization never consumes more than the proven energy budget. The
        # SAME amount is debited from the unit and credited to consumed.energy.
        require(isinstance(quantum, Fraction) and quantum > 0,
                'BATTLE_NUMBER_INVALID', '能源记账网格无效')
        cost = (cost // quantum) * quantum
    return exact(cost)


def segment_budget(kind, segments, energy, *, moving_attack=False, seconds=5):
    """连续几何预算证明；规则毫米累计路径由适配器统一记录后用movement_cost收费。

    grade为旧合成几何使用的绝对坡度；真实面可传grade_squared，二者必须且只能提供一个。
    仅返回数值预算，不移动单位；同一未中断路线保存不足毫米预算，终止清除。
    """
    energy = number(energy); remaining = number(seconds)
    require(remaining <= 5, 'BATTLE_STATE_INVALID', '单步预算不能超过5秒')
    spent, lengths = Fraction(0), []
    reservation = Fraction(5, 4) if kind == 'ground' and moving_attack else Fraction(1)
    for segment in segments:
        speed, energy_per_m = _segment_rates(kind, segment)
        length = number(segment['length_mm']) / 1000
        actual = min(length, remaining * speed / 60, (energy - spent * reservation) / (energy_per_m * reservation))
        actual = max(Fraction(0), actual)
        lengths.append(exact(actual * 1000))
        spent += actual * energy_per_m; remaining -= actual * 60 / speed
        if actual < length or remaining == 0:
            break
    return dict(lengths_mm=lengths, energy=exact(spent), reserved_energy=exact(spent * reservation),
                remaining_seconds=exact(remaining))


def air_begin(state, unit_id, action, *, dock_legal, air_legal):
    """起降步开始；几何布尔值不可从用户载荷直接传入。返回实际过渡动作。"""
    result = deepcopy(state); unit = _units(result)[unit_id]
    require(unit['unit_type'] == 'recon' and action in (None, 'takeoff', 'land'),
            'BATTLE_STATE_INVALID', '飞行任务无效')
    if not alive(unit):
        return result, None
    air = unit['air_state']; cost, transition = Fraction(0), None
    if air == 'docked':
        if action == 'takeoff' and dock_legal and air_legal and number(unit['energy']) > Fraction(7, 6):
            cost, transition = Fraction(7, 6), 'takeoff'
    elif air == 'flying':
        if action == 'land' and dock_legal and air_legal and number(unit['energy']) > Fraction(7, 6):
            cost, transition = Fraction(7, 6), 'land'
        else:
            cost = min(number(unit['energy']), Fraction(1, 6))
    else:
        require(False, 'BATTLE_STATE_INVALID', '步初无人机不能停留在未完成过渡状态')
    consume_energy(result, unit, cost)
    if transition:
        unit['air_state'] = 'taking_off' if transition == 'takeoff' else 'landing'
    if air != 'docked' and number(unit['energy']) == 0:
        _retire(result, unit, 'air_energy_exhausted')
    validate(result)
    return result, transition


def air_finish(state, unit_id, *, landing_clear):
    result = deepcopy(state); unit = _units(result)[unit_id]
    if alive(unit):
        if unit['air_state'] == 'taking_off':
            unit['air_state'] = 'flying'
        elif unit['air_state'] == 'landing':
            unit['air_state'] = 'docked' if landing_clear else 'flying'
    validate(result)
    return result


def _distance2(a, b):
    return sum((number(a[k]) - number(b[k])) ** 2 for k in ('x_mm', 'z_mm'))


def resolve_fire(state, *, seconds, start_positive, moved_energy, observed, receivers, fire_clear):
    """冻结观察和步初资格后一次确定所有射击，再同步伤害；不循环边打边移除目标。"""
    result = deepcopy(state); units = _units(result); shots = []; damage = {}
    require(seconds == result['battle_ledger']['elapsed_seconds'] + 5, 'BATTLE_STATE_INVALID', '射击时钟无效')
    for identity in sorted(units):
        unit = units[identity]; task = unit.get('task') or {}
        if not (alive(unit) and identity in start_positive and number(unit['energy']) > 0
                and unit['unit_type'] == 'ground' and identity not in receivers
                and number(unit['ammo']) >= 1 and unit['next_fire_seconds'] <= seconds
                and task.get('action') in ('attack', 'attack_area')):
            continue
        base = number(moved_energy.get(identity, '0')); extra = base / 4
        if number(unit['energy']) <= extra:
            continue
        candidates = []
        for target in units.values():
            if not (alive(target) and target['side'] != unit['side']
                    and target['unit_id'] in observed.get(unit['side'], set())):
                continue
            if task['action'] == 'attack':
                if target['unit_id'] != task['target_id'] or target['life_id'] != task['target_life_id']:
                    continue
            else:
                if _distance2(target['position_mm'], task['center_mm']) > number(task['radius_mm']) ** 2:
                    continue
            range_mm = (800 if target['unit_type'] == 'recon' and target['air_state'] != 'docked' else 600) * 1000
            distance2 = _distance2(unit['position_mm'], target['position_mm'])
            if distance2 <= range_mm ** 2 and fire_clear(unit, target):
                candidates.append((distance2, number(target['durability']), target['unit_id']))
        if not candidates:
            continue
        best = min((d, h) for d, h, _ in candidates)
        ties = sorted(i for d, h, i in candidates if (d, h) == best)
        target_id = next((i for i in ties if unit['target_cursor'] is None or i > unit['target_cursor']), ties[0])
        target = units[target_id]
        consume_energy(result, unit, extra); _add(unit, 'ammo', -1)
        _add(result['battle_ledger'][unit['side']]['consumed'], 'ammo', 1)
        unit.update(next_fire_seconds=seconds + 10, last_fire_seconds=seconds, target_cursor=target_id)
        amount = Fraction(48, 5) if base > 0 else Fraction(16)
        damage[target_id] = damage.get(target_id, Fraction(0)) + amount
        shots.append(dict(source_id=identity, target_id=target_id, source_life_id=unit['life_id'],
                          target_life_id=target['life_id'], damage=exact(amount), at_seconds=seconds))
    for identity, amount in damage.items():
        unit = units[identity]; unit['durability'] = exact(max(0, number(unit['durability']) - amount))
        unit['last_damage_seconds'] = seconds
        if number(unit['durability']) == 0:
            _retire(result, unit, 'attack', 'red' if unit['side'] == 'blue' else 'blue')
    validate(result)
    return result, shots


def capture_step(state, points, *, presence, residents):
    """presence为移动后冻结在场，residents为全步合法停止且伤害后存活者。

    调用者须依据冻结地图有效地表生成两个集合；仅圆内距离不能生成合法证据。
    """
    result = deepcopy(state); updated = deepcopy(points); units = _units(result)
    require([p['point_id'] for p in updated] == [f'P-{i:02d}' for i in range(1, 6)],
            'BATTLE_POINT_LAYOUT_CHANGED', '仅支持已确认五点')
    for point in updated:
        identity = point['point_id']; owner = point['owner']
        require(owner in (*SIDES, 'neutral'), 'BATTLE_STATE_INVALID', '据点归属无效')
        present = {s: bool(presence.get(identity, {}).get(s, set())) for s in SIDES}
        stationed = {s: any(i in units and alive(units[i]) and units[i]['side'] == s
            and units[i]['unit_type'] in ('ground', 'support')
            and i in presence.get(identity, {}).get(s, set())
            for i in residents.get(identity, {}).get(s, set())) for s in SIDES}
        point['contested'] = all(present.values())
        suppressed = owner in SIDES and present['red' if owner == 'blue' else 'blue']
        point['suppressed'] = bool(suppressed)
        actor = next((s for s in SIDES if stationed[s] and not present['red' if s == 'blue' else 'blue'] and owner != s), None)
        if actor is None:
            point.update(advancing_side='none', progress_steps=0)
        else:
            progress = point['progress_steps'] + 1 if point.get('advancing_side') == actor else 1
            if progress == 6:
                point.update(owner=actor if owner == 'neutral' else 'neutral', advancing_side='none', progress_steps=0)
            else:
                require(0 < progress < 6, 'BATTLE_STATE_INVALID', '占领阶段进度无效')
                point.update(advancing_side=actor, progress_steps=progress)
        if owner in SIDES and point['owner'] == owner and not suppressed:
            result['battle_ledger'][owner]['control_steps'] += 1
    validate(result)
    return result, updated


def score(state, side):
    validate(state); account = state['battle_ledger'][side]
    enemy = 'red' if side == 'blue' else 'blue'
    totals = resource_totals(state, side)
    control = Fraction(70 * account['control_steps'], 3600)
    kills = 20 * number(account['kills']) / number(state['battle_ledger'][enemy]['initial_value'])
    resources = sum(Fraction(weight) * totals[key] / number(account['initial'][key])
                    for key, weight in zip(RESOURCES, (15, 9, 6)))
    return dict(control=exact(control), kills=exact(kills), resources=exact(resources), total=exact(control + kills + resources))


def terminal(state, seconds):
    require(type(seconds) is int and 0 <= seconds <= 3600 and seconds % 5 == 0,
            'BATTLE_STATE_INVALID', '终局时钟无效')
    living = {s: any(alive(u) and u['side'] == s for u in state['units']) for s in SIDES}
    if all(living.values()) and seconds < 3600:
        return None
    if living['blue'] != living['red']:
        return dict(winner='blue' if living['blue'] else 'red', reason='elimination')
    values = {s: (number(score(state, s)['total']), state['battle_ledger'][s]['control_steps']) for s in SIDES}
    winner = 'draw' if values['blue'] == values['red'] else max(SIDES, key=lambda s: values[s])
    return dict(winner=winner, reason='mutual_elimination' if not any(living.values()) else 'time_limit')


def service_step(state, requests, *, seconds, stationary, start_positive, service_clear, hover_energy_clear=None):
    """野外服务：请求必须为已采用任务，几何回调只由地图适配器提供。

    接收意图应在resolve_fire前由service_receivers锁定；先伤害再调用本函数。
    requests含source/target、双方life、resource及quantity/target_balance之一。
    """
    result = deepcopy(state); units = _units(result); deliveries = []
    service_receivers(result, requests)  # 整批冲突先拒绝，不因遍历顺序偷偷选择赢家。
    manifest = result.get('manifest', {})
    hover_policy = (manifest.get('battle_mode') == 'manual'
                    and manifest.get('field_energy_service_policy') == FIELD_ENERGY_SERVICE_POLICY)
    for request in requests:
        source = units[request['source_id']]; target = units[request['target_id']]
        resource, amount = request['resource'], number(request.get('quantity', request.get('target_balance')))
        account = 'durability' if resource == 'repair' else resource
        maximum = _parameters()['unit_profiles'][target['unit_type']][account]
        missing = max(Fraction(0), maximum - number(target[account]))
        wanted = amount if 'quantity' in request else max(Fraction(0), amount - number(target[account]))
        if not (alive(source) and alive(target) and source['unit_id'] in start_positive
                and number(source['energy']) > 0
                and source['unit_id'] in stationary and target['unit_id'] in stationary
                and _distance2(source['position_mm'], target['position_mm']) <= 100000 ** 2):
            continue
        if hover_policy and allows_hover_energy(source, target, resource):
            if hover_energy_clear is None or hover_energy_clear(source, target) is not True:
                continue
        elif ((target['unit_type'] == 'recon' and target['air_state'] != 'docked')
              or not service_clear(source, target)):
            continue
        delivered = min(Fraction(2), number(source['cargo'][resource]), missing, wanted)
        if delivered <= 0:
            continue
        _add(source['cargo'], resource, -delivered); _add(target, account, delivered)
        if resource == 'repair':
            _add(result['battle_ledger'][source['side']]['consumed'], 'repair', delivered)
        deliveries.append(dict(source_id=source['unit_id'], target_id=target['unit_id'],
            source_life_id=source['life_id'], target_life_id=target['life_id'],
            resource=resource, amount=exact(delivered), at_seconds=seconds))
        if 'quantity' in request:
            request = dict(request, quantity=exact(amount - delivered))
        # 结果包含剩余请求，调用者须存回任务；不修改输入请求。
        deliveries[-1]['remaining_request'] = deepcopy(request)
    validate(result)
    return result, deliveries


def service_receivers(state, requests):
    """在攻击前锁定接收意图。即使地表/库存随后不合格也不能补发攻击。"""
    units = _units(state); sources, targets = set(), set()
    for request in requests:
        source, target = units.get(request['source_id']), units.get(request['target_id'])
        require(source is not None and target is not None and source['side'] == target['side']
                and source['unit_id'] != target['unit_id'] and source['unit_type'] == 'support'
                and source['life_id'] == request['source_life_id'] and target['life_id'] == request['target_life_id'],
                'BATTLE_SERVICE_INVALID', '服务来源、接收者或生命实例无效')
        resource = request['resource']
        require(resource in RESOURCES and (resource != 'ammo' or target['unit_type'] == 'ground'),
                'BATTLE_SERVICE_INVALID', '野外弹药只能补坦克自用弹药')
        require(('quantity' in request) != ('target_balance' in request),
                'BATTLE_SERVICE_INVALID', '服务请求须指定数量或目标余额之一')
        amount = number(request.get('quantity', request.get('target_balance')))
        account = 'durability' if resource == 'repair' else resource
        maximum = _parameters()['unit_profiles'][target['unit_type']][account]
        require((resource != 'ammo' or amount.denominator == 1)
                and ('quantity' in request or amount <= maximum), 'BATTLE_SERVICE_INVALID', '请求数量或目标余额无效')
        require(source['unit_id'] not in sources and target['unit_id'] not in targets,
                'BATTLE_SERVICE_CONFLICT', '单车只能一目标一类别，接收者只能一个来源')
        sources.add(source['unit_id']); targets.add(target['unit_id'])
    require(not sources.intersection(targets), 'BATTLE_SERVICE_CONFLICT', '同一步不能同时提供与接收服务')
    return targets


def barracks_step(state, requests, camps, *, seconds, stationary, service_clear):
    """三营共用库存/排序。持久登记与游标写回账本；无交付不占名额。

    camps为冻结地图验收后的兵营；service_clear必须校验完整停驻地表与连续连接。
    不自动选营；requests明确camp_id，刷新等价请求不重置登记时间。
    """
    result = deepcopy(state); units = _units(result)
    camp_map = {c['camp_id']: c for c in camps}; ids = set(); queue = []
    require(len(camp_map) == len(camps), 'BATTLE_SERVICE_INVALID', '兵营标识重复')
    for request in requests:
        identity = request['unit_id']; unit = units.get(identity); camp = camp_map.get(request['camp_id'])
        require(unit is not None and camp is not None and unit['side'] == camp['side']
                and unit['life_id'] == request['life_id'] and identity not in ids,
                'BATTLE_SERVICE_INVALID', '兵营请求身份/阵营或重复请求无效')
        ids.add(identity)
        profile = _parameters()['unit_profiles'][unit['unit_type']]
        categories = request.get('targets')
        maxima = dict(energy=profile['energy'], ammo=profile['ammo'], durability=profile['durability'],
            stock_energy=profile.get('stocks', {}).get('energy', 0),
            stock_ammo=profile.get('stocks', {}).get('ammo', 0), stock_repair=profile.get('stocks', {}).get('repair', 0))
        if categories is None:
            categories = {k: str(v) for k, v in maxima.items() if v > 0}
        require(isinstance(categories, dict) and categories and all(k in maxima for k in categories),
                'BATTLE_SERVICE_INVALID', '兵营服务类别无效')
        for key, value in categories.items():
            amount = number(value)
            require(amount <= maxima[key] and ('ammo' not in key or amount.denominator == 1),
                    'BATTLE_SERVICE_INVALID', '兵营目标余额超容量或弹药非整数')
        signature = sha256(dict(camp_id=camp['camp_id'], targets={k: exact(number(v)) for k, v in categories.items()}))
        legal = (alive(unit) and identity in stationary
            and (unit['unit_type'] != 'recon' or unit['air_state'] == 'docked')
            and _distance2(unit['position_mm'], camp['position_mm']) <= 100000 ** 2 and service_clear(camp, unit))
        if not legal:
            unit.pop('barracks_registration', None)
            continue
        has_need = any(number(unit['cargo'][k[6:]] if k.startswith('stock_') else unit[k]) < number(v)
                       for k, v in categories.items())
        if not has_need:
            unit.pop('barracks_registration', None)
            continue
        registration = unit.get('barracks_registration')
        if registration is None or registration['signature'] != signature:
            registration = dict(signature=signature, since_seconds=seconds - 5, camp_id=camp['camp_id'])
            unit['barracks_registration'] = registration
        queue.append((registration['since_seconds'], identity, categories, camp))
    for identity, unit in units.items():
        if identity not in ids:
            unit.pop('barracks_registration', None)
    deliveries, slots = [], {identity: 0 for identity in camp_map}
    limits = dict(energy=50, ammo=50, durability=50, stock_energy=50, stock_ammo=60, stock_repair=50)
    order = tuple(limits)
    for side in SIDES:
        cursor = result['battle_ledger'][side]['barracks_cursor']
        candidates = sorted((item for item in queue if units[item[1]]['side'] == side),
            key=lambda item: (item[0], cursor is not None and item[1] <= cursor, item[1]))
        for _, identity, targets, camp in candidates:
            unit = units[identity]
            if slots[camp['camp_id']] >= 4 or any(unit[k] is not None and seconds - unit[k] < 15
                                                for k in ('last_fire_seconds', 'last_damage_seconds')):
                continue
            delivered = False
            for category in order:
                if category not in targets:
                    continue
                resource = 'repair' if category in ('durability', 'stock_repair') else category.removeprefix('stock_')
                container, field = (unit['cargo'], category[6:]) if category.startswith('stock_') else (unit, category)
                reserve = result['battle_ledger'][side]['reserve']
                amount = min(Fraction(limits[category]), number(reserve[resource]),
                             max(Fraction(0), number(targets[category]) - number(container[field])))
                if amount <= 0:
                    continue
                _add(reserve, resource, -amount); _add(container, field, amount)
                if category == 'durability':
                    _add(result['battle_ledger'][side]['consumed'], 'repair', amount)
                deliveries.append(dict(camp_id=camp['camp_id'], target_id=identity, resource=category,
                                       amount=exact(amount), at_seconds=seconds))
                delivered = True
            if delivered:
                slots[camp['camp_id']] += 1
                result['battle_ledger'][side]['barracks_cursor'] = identity
    validate(result)
    return result, deliveries
