"""版本化单位动作；每分钟由12个5秒事务内子步组成。几何仍为测试包络。"""
from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL

from backend import spatial
from backend.demo_domain import require, host_only
from backend.runtime import sha256


@lru_cache(maxsize=1)
def definition():
    return json.loads(Path(__file__).with_name('unit-rules-v1.json').read_text(encoding='utf-8'))


RULE_ID = str(uuid5(NAMESPACE_URL, 'qinglan/units/' + sha256(definition())))


def enabled(state):
    return 'unit_interactions' in state


def initialize(state):
    require('spatial_profile' in state['manifest'], 'SPATIAL_TEST_REQUIRED', '单位交互需要空间对局')
    if not enabled(state):
        state['unit_interactions'] = dict(rule_version_id=RULE_ID, rules=deepcopy(definition()),
            elapsed_seconds=state['lifecycle']['simulation_step'] * 60, orders={}, events=[])
    sync_units(state)
    # 升级前已排队的路线仍冻结原来的几何，但参数切换为本次单位规则。
    frozen=state['unit_interactions']['rules']['movement_profile']
    for route in state['routes']:
        if route.get('movement_profile'):
            route['movement_profile']=deepcopy(frozen)
    observe(state)


def enable(state, actor):
    host_only(actor)
    require(state['lifecycle']['phase'] in ('preparing', 'paused'), 'PHASE_CONFLICT', '请在准备或暂停阶段启用')
    require(not enabled(state), 'ALREADY_ENABLED', '本局已启用单位交互')
    require('coordination' not in state, 'MODE_CONFLICT', '旧版协同测试需另建单位交互对局，避免双重服务')
    initialize(state)


def sync_units(state):
    if not enabled(state): return
    rules = state['unit_interactions']['rules']
    for unit in state['units']:
        p = rules['unit_profiles'][unit['unit_type']]
        for field in ('durability', 'energy'):
            if unit.get(field) is None: unit[field] = p[field]
        unit.setdefault('life_id', str(uuid4()))
        unit.setdefault('ammo', p['ammo'])
        unit.setdefault('stocks', deepcopy(p.get('stocks', {})))
        unit.setdefault('last_fired_at', None)
        unit.setdefault('last_damaged_at', None)
        unit.setdefault('docked', unit['unit_type'] == 'recon' and not unit.get('movement_departed', False) and near_camp(state, unit))
        unit['max_durability'], unit['max_ammo'] = p['durability'], p['ammo']
        unit['speed_m_per_minute'] = p['speed_m_per_minute']


def alive(unit):
    return unit.get('durability', 0) > 0


def active(unit):
    return alive(unit) and unit.get('energy', 0) > 0


def distance(a, b):
    return math.dist(spatial.meters(a['position']), spatial.meters(b['position']))


def los(state, a, b):
    """共享保守遮挡算法；所有观察/开火/服务均使用地表+2m，不用画面飞行高度。"""
    from backend.coordination import visible
    proxy = dict(state, coordination=dict(rules=dict(observation_radius_m=20000, observer_height_m=2)))
    return visible(proxy, a, b)


def can_see(state, observer, target):
    if not active(observer) or not alive(target): return False
    if state['lifecycle']['phase']=='preparing' and (not observer.get('camp_id') or not target.get('camp_id')): return False
    eligible = state['unit_interactions'].get('observation_eligible_ids')
    if eligible is not None and observer['unit_id'] not in eligible: return False
    radius = state['unit_interactions']['rules']['unit_profiles'][observer['unit_type']]['observation_m']
    if observer['unit_type'] == 'recon':
        from backend.movement import Navigator
        nav = Navigator(state, observer, 'move')
        start, end = spatial.meters(observer['position']), spatial.meters(target['position'])
        forests = ('sparse_forest', 'dense_forest')
        if nav.terrain(start) in forests:
            radius = 1000
        elif nav.terrain(end) in forests:
            depth = 0
            total = math.dist(start, end)
            for step in range(1, math.ceil(total / 5) + 1):
                ratio = min(1, step * 5 / total) if total else 1
                point = tuple(end[a] + (start[a] - end[a]) * ratio for a in (0, 1))
                if nav.terrain(point) not in forests: break
                depth = min(total, step * 5)
            if depth > 600: return False
    return distance(observer, target) <= radius and los(state, observer, target)


def service_contact(state, source, target):
    if distance(source, target) > 100 or not los(state, source, target): return False
    # 保守的同一连续可达地表校验，不能跨水、实体墙体或非法桥面补给。
    from backend.movement import Navigator
    nav = Navigator(state, source, 'move')
    return nav.segment(spatial.meters(source['position']), spatial.meters(target['position'])) is not None


def observe(state):
    info = state['unit_interactions']
    for side in ('red', 'blue'):
        old = state['observations'].get(side, {})
        history = {c['unit_id']: deepcopy(c) for c in old.get('last_seen_records', [])}
        for c in old.get('contacts', []): history[c['unit_id']] = deepcopy(c)
        contacts = []
        own = [u for u in state['units'] if u['side'] == side]
        for target in state['units']:
            if target['side'] == side: continue
            observer = next((u for u in own if can_see(state, u, target)), None)
            if observer:
                contact = deepcopy(target)
                # 不向对方泄漏命令、保障库存、生命历史等私有状态。
                contact = {k: v for k, v in contact.items() if k in (
                    'unit_id','side','unit_type','name','position','durability','max_durability',
                    'ammo','max_ammo','energy','speed_m_per_minute','current_speed_m_per_minute','life_id','status','docked')}
                contact.update(observed_at_seconds=info['elapsed_seconds'], observed_step=state['lifecycle']['simulation_step'],
                               observer_unit_id=observer['unit_id'], state='current')
                contacts.append(contact)
                history.pop(target['unit_id'], None)
        for c in history.values(): c['state'] = 'last_seen'
        state['observations'][side] = dict(contacts=contacts, last_seen_records=list(history.values()))


def camp_contact(state, camp, unit):
    if (camp['side']!=unit['side'] or not camp.get('deployment_validated') or not camp.get('position')
            or distance(camp,unit)>100 or not los(state,camp,unit)):
        return False
    from backend.movement import Navigator
    # 停靠和补给必须在地面合法点；仅允许经兵营真实出入口接触，不能隔水/穿墙。
    candidate=dict(unit,unit_type='ground',camp_id=camp['camp_id'])
    nav=Navigator(state,candidate,'move')
    return nav.segment(spatial.meters(camp['position']),spatial.meters(unit['position'])) is not None


def near_camp(state, unit):
    return any(camp_contact(state,c,unit) for c in state['camps'])


def stop_route(state, unit):
    for r in state['routes']:
        if r['unit_id'] == unit['unit_id'] and r['status'] in ('queued', 'moving', 'waiting'):
            r['status'] = 'stopped'


def order(state, actor, payload):
    require(enabled(state), 'UNIT_RULES_REQUIRED', '请由主持人启用单位交互')
    require(state['lifecycle']['phase'] in ('paused', 'running'), 'PHASE_CONFLICT', '单位任务仅在开局后执行')
    sync_units(state)
    units = {u['unit_id']: u for u in state['units']}
    unit = units.get(payload['unit_id'])
    require(unit is not None and (actor == 'host' or unit['side'] == actor), 'UNIT_FORBIDDEN', '单位不存在或无权操作', 403)
    require(payload['life_id'] == unit['life_id'], 'LIFE_CHANGED', '单位生命实例已变化，请刷新')
    action = payload['action']
    if action == 'stop':
        state['unit_interactions']['orders'].pop(unit['unit_id'], None)
        unit['status'] = '已取消单位任务'
        return
    require(alive(unit), 'UNIT_DESTROYED', '被击毁单位不能执行任务')
    require(active(unit) or action in ('barracks', 'dock'), 'ENERGY_EMPTY', '单位能源为0，需接受补给')
    target = units.get(payload.get('target_id'))
    if action in ('attack', 'recon'):
        require(unit['unit_type'] == ('ground' if action == 'attack' else 'recon'), 'UNIT_ACTION_FORBIDDEN', '兵种不支持此动作')
        if target:
            visible_ids = {c['unit_id'] for c in state['observations'][unit['side']]['contacts']}
            require(target['side'] != unit['side'] and target['unit_id'] in visible_ids and alive(target),
                    'TARGET_NOT_VISIBLE', '目标不是当前合法可见敌方单位')
            require(payload.get('target_life_id') == target['life_id'], 'TARGET_LIFE_CHANGED', '目标生命实例已变化')
        else:
            require(action == 'recon' and payload.get('position') is not None, 'TARGET_REQUIRED', '请选择当前可见目标或侦察位置')
        if action == 'recon' and payload.get('position'):
            from backend import movement
            movement.order(state, actor, dict(unit_id=unit['unit_id'], mode='move', position=payload['position'], waypoints=[], formation=False))
            unit['docked'] = False
    elif action in ('resupply', 'repair'):
        require(unit['unit_type'] == 'support', 'UNIT_ACTION_FORBIDDEN', '仅保障单位可提供野外服务')
        require(target is not None and target['side'] == unit['side'] and target != unit and alive(target),
                'SERVICE_TARGET_INVALID', '只能服务其他存活的本方单位')
        require(payload.get('target_life_id') == target['life_id'], 'TARGET_LIFE_CHANGED', '目标生命实例已变化')
        require(action == 'repair' or payload['resource'] in ('energy', 'ammo'), 'RESOURCE_INVALID', '请选择弹药或能源')
        require(action == 'repair' or payload['resource'] != 'ammo' or target['max_ammo'] > 0, 'NO_WEAPON', '目标没有武器弹药容量')
        require(service_contact(state, unit, target), 'SERVICE_OUT_OF_RANGE', '请先移动至100米内同一可达服务地表')
        require(target['unit_type'] != 'recon' or target.get('docked'), 'AIRBORNE_TARGET', '无人机必须先在本方兵营合法停靠')
        stop_route(state, unit)
    elif action in ('dock', 'barracks'):
        require(near_camp(state, unit), 'CAMP_OUT_OF_RANGE', '请先返回本方兵营100米内合法接触位置')
        if action == 'dock': require(unit['unit_type'] == 'recon', 'UNIT_ACTION_FORBIDDEN', '仅无人机需要停靠')
        stop_route(state, unit)
        if unit['unit_type'] == 'recon': unit['docked'] = True
    else:
        require(False, 'ACTION_INVALID', '未知单位动作')
    state['unit_interactions']['orders'][unit['unit_id']] = dict(payload, source_life_id=unit['life_id'])
    unit['status'] = {'attack':'攻击任务已下达','recon':'侦察任务已下达','resupply':'补充任务已下达','repair':'维修任务已下达','dock':'已合法停靠','barracks':'兵营补充已申请'}[action]


def advance(state):
    """完整的一分钟，伤害同步提交，随后服务；不依赖遍历次序进行先手击杀。"""
    sync_units(state)
    traces, sequences = {}, {}
    for _ in range(12):
        tick(state)
        for route in state['routes']:
            key=route['unit_id']; sequence=route.get('trace_sequence',0)
            if sequence != sequences.get(key,sequence):
                trace=route.get('last_trace',[])
                traces.setdefault(key,[]).extend(trace if key not in traces else trace[1:])
            elif key not in sequences:
                traces[key]=deepcopy(route.get('last_trace',[]))
            sequences[key]=sequence
    for route in state['routes']:
        if route['unit_id'] in traces: route['last_trace']=traces[route['unit_id']]


def tick(state):
    info = state['unit_interactions']
    rules, orders = info['rules'], info['orders']
    units = {u['unit_id']: u for u in state['units']}
    begin = {i: dict(position=deepcopy(u['position']), energy=u['energy'], active=active(u)) for i, u in units.items()}
    end_time = info['elapsed_seconds'] + rules['step_seconds']
    info['observation_eligible_ids'] = [i for i,b in begin.items() if b['active']]
    # 接受服务意图在伤害之前锁定；不因源车被击毁而补发开火。
    receivers = {o['target_id'] for i,o in orders.items() if o['action'] in ('resupply','repair')
                 and i in units and active(units[i]) and o.get('source_life_id')==units[i]['life_id'] and o.get('target_id') in units
                 and o.get('target_life_id')==units[o['target_id']]['life_id']}
    receivers.update(i for i,o in orders.items() if o['action'] == 'barracks')
    held = receivers | {i for i,o in orders.items() if o['action'] in ('resupply','repair','barracks','dock')}
    # 服务全步静止：暂停路线但保留未完成路径，不暗中删除用户移动目标。
    paused = []
    for route in state['routes']:
        if (route['unit_id'] in held or not begin[route['unit_id']]['active']) and route['status'] in ('queued','moving','waiting'):
            paused.append((route, route['status'])); route['status'] = 'service_hold'
    for i,u in units.items():
        if u['unit_type'] == 'recon' and any(r['unit_id']==i and r['status'] in ('queued','moving','waiting') for r in state['routes']): u['docked']=False
    # 飞行维持费用先预留，不能用同一份能源同时行驶和悬停。
    for i,u in units.items():
        if u['unit_type']=='recon' and begin[i]['active'] and not u.get('docked'):
            u['energy']=round(max(0,u['energy']-rules['unit_profiles']['recon']['hover_energy_per_minute']/12),6)
    moving_attackers = {i for i,o in orders.items() if o['action']=='attack' and i not in held}
    spatial.advance(state, time_fraction=1/12, attack_units=moving_attackers)
    for route,status in paused: route['status']=status
    for i,u in units.items():
        u['current_speed_m_per_minute']=round(math.dist(spatial.meters(begin[i]['position']),spatial.meters(u['position']))*12,6)
    observe(state)
    for i,o in orders.items():
        if o['action']=='recon' and i in units and active(units[i]):
            target=units.get(o.get('target_id'))
            if target and any(c['unit_id']==target['unit_id'] for c in state['observations'][units[i]['side']]['contacts']):
                units[i]['status']='侦察目标当前可见，情报已共享'
            elif target: units[i]['status']='侦察目标失视，不追踪隐藏位置'
            else: units[i]['status']='正在区域侦察，发现情报自动共享'
    damage, shots = {}, []
    for i,o in sorted(orders.items()):
        u, target = units.get(i), units.get(o.get('target_id'))
        if not u or o.get('source_life_id') != u['life_id']: continue
        if o['action'] != 'attack' or i in receivers or not begin[i]['active'] or not active(u): continue
        if not target or not alive(target) or o.get('target_life_id') != target['life_id']:
            u['status']='目标失效，等待新命令'; continue
        contact_ids={c['unit_id'] for c in state['observations'][u['side']]['contacts']}
        radius=rules['attack']['range_air_m' if target['unit_type']=='recon' and not target.get('docked') else 'range_ground_m']
        if target['unit_id'] not in contact_ids or distance(u,target)>radius or not los(state,u,target):
            u['status']='等待目标可见、进入射程并拥有独立射线'; continue
        if u['ammo'] <= 0: u['status']='弹药不足，保留攻击任务'; continue
        if u['last_fired_at'] is not None and end_time-u['last_fired_at']<rules['attack']['cooldown_seconds']: continue
        moved = spatial.meters(u['position']) != spatial.meters(begin[i]['position'])
        amount=rules['attack']['damage']*(rules['attack']['moving_damage_factor'] if moved else 1)
        u['ammo']-=rules['attack']['ammo_per_shot']; u['last_fired_at']=end_time
        damage[target['unit_id']]=damage.get(target['unit_id'],0)+amount
        # Freeze legal knowledge before simultaneous damage removes observers/targets.
        audience = ['host']
        for side in ('red', 'blue'):
            known = {v['unit_id'] for v in units.values() if v['side'] == side}
            known.update(c['unit_id'] for c in state['observations'][side]['contacts'])
            if i in known and target['unit_id'] in known: audience.append(side)
        def endpoint(value):
            return {k: deepcopy(value[k]) for k in ('unit_id', 'life_id', 'unit_type', 'side', 'position', 'docked')}
        shots.append(dict(event_id=str(uuid4()), at=end_time, action='attack', unit_id=i,
            target_id=target['unit_id'], amount=amount, source=endpoint(u), target=endpoint(target),
            ammo_after=u['ammo'], audience=audience))
        u['status']='攻击命中'
    # 按可能开火预留预算，但冷却/缺弹/失视等未实际开火时退还附加能耗；不追加移动。
    fired = {shot['unit_id'] for shot in shots}
    for i in moving_attackers - fired:
        u = units[i]
        reserved = begin[i]['energy'] - u['energy']
        u['energy'] = round(u['energy'] + max(0, reserved) * (1 - 1 / rules['attack']['moving_energy_factor']), 6)
    for i,amount in damage.items():
        u=units[i]; u['durability']=round(max(0,u['durability']-amount),6); u['last_damaged_at']=end_time
        if not alive(u):
            u.update(status='已被击毁',availability='destroyed'); stop_route(state,u)
            orders.pop(i,None)
    # Results belong to the whole simultaneous volley, never a second VFX damage pass.
    for shot in shots:
        target = units[shot['target_id']]
        shot.update(hp_after=target['durability'], lethal=not alive(target))
    served=set()
    for i,o in sorted(orders.items()):
        u,target=units.get(i),units.get(o.get('target_id'))
        if not u or not active(u) or not begin[i]['active'] or o['action'] not in ('resupply','repair'): continue
        if not target or not alive(target) or target['unit_id'] in served or o.get('target_life_id')!=target['life_id']: continue
        if not service_contact(state,u,target): u['status']='服务暂停：距离或地表不可达'; continue
        if target['unit_type']=='recon' and not target.get('docked'): continue
        resource='repair' if o['action']=='repair' else o['resource']
        field='durability' if resource=='repair' else resource
        maximum=rules['unit_profiles'][target['unit_type']][field]
        amount=min(rules['service']['amount_per_tick'],u['stocks'][resource],maximum-target[field])
        if amount<=0: u['status']='服务暂停：库存不足或目标已满'; continue
        target[field]=round(target[field]+amount,6); u['stocks'][resource]-=amount; served.add(target['unit_id'])
        u['status']='正在维修' if resource=='repair' else '正在补充'+('能源' if resource=='energy' else '弹药')
        shots.append(dict(at=end_time,action=resource,unit_id=i,target_id=target['unit_id'],amount=amount))
    # 兵营服务使用同一个接收名额集合，不能与野外补充叠加。
    for camp in sorted(state['camps'],key=lambda c:c['camp_id']):
        slots=rules['barracks']['slots_per_tick']
        for i,o in sorted(orders.items()):
            u=units.get(i)
            if slots<=0: break
            if not u or o['action']!='barracks' or i in served or not alive(u) or camp['side']!=u['side']: continue
            if not camp_contact(state,camp,u): continue
            if any(u.get(f) is not None and end_time-u[f]<rules['barracks']['quiet_seconds'] for f in ('last_fired_at','last_damaged_at')):
                u['status']='兵营等待脱战15秒'; continue
            p=rules['unit_profiles'][u['unit_type']]; changed=False
            for field in ('energy','ammo','durability'):
                amount=min(rules['barracks'][field],p[field]-u[field]); u[field]+=amount; changed |= amount>0
            for field,maximum in p.get('stocks',{}).items():
                amount=min(rules['barracks']['stocks'][field],maximum-u['stocks'][field]); u['stocks'][field]+=amount; changed |= amount>0
            if changed: served.add(i); slots-=1; u['status']='兵营正在补充'
    for i,u in units.items():
        if not alive(u): orders.pop(i,None)
    info['elapsed_seconds']=end_time
    info['events']=(info['events']+shots)[-200:]
    observe(state)


def validate(state):
    if not enabled(state): return
    info=state['unit_interactions']; rules=info['rules']
    require(info['rule_version_id']==RULE_ID and rules==definition(), 'UNIT_RULE_VERSION_INVALID','单位规则版本内容不符')
    for u in state['units']:
        p=rules['unit_profiles'][u['unit_type']]
        for f in ('energy','ammo','durability'):
            v=u.get(f); require(type(v) in (int,float) and math.isfinite(v) and 0<=v<=p[f], 'UNIT_RESOURCE_INVALID','单位资源超出规则容量')
        for f,limit in p.get('stocks',{}).items():
            v=u.get('stocks',{}).get(f); require(type(v) in (int,float) and math.isfinite(v) and 0<=v<=limit,'UNIT_STOCK_INVALID','保障库存无效')


def on_move_order(state, unit_id):
    if not enabled(state): return
    orders=state['unit_interactions']['orders']
    for source,order in list(orders.items()):
        if (source==unit_id and order['action'] in ('resupply','repair','barracks','dock')
                or order.get('target_id')==unit_id and order['action'] in ('resupply','repair')):
            orders.pop(source,None)
    unit=next(u for u in state['units'] if u['unit_id']==unit_id)
    if unit['unit_type']=='recon': unit['docked']=False


def attack_events(state, role):
    """Public immutable VFX feed; old events without frozen visibility are not publishable."""
    if not enabled(state): return []
    fields = ('event_id', 'at', 'amount', 'source', 'target', 'ammo_after', 'hp_after', 'lethal')
    return [deepcopy({k: e[k] for k in fields})
            for e in state['unit_interactions']['events']
            if e.get('action') == 'attack' and e.get('event_id')
            and role in e.get('audience', []) and all(k in e for k in fields)]
