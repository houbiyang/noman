"""总册60秒执行器与统一5秒协调器。

数值、服务角色、时钟、事件、摘要在这里统一，不调用旧演示执行器。
地图是强制依赖：无默认实现，不从HTTP接收几何证据；真实地图验收前不注册生产实例。
测试地图只能在隔离测试中显式注入，不能据此宣布真实场景通过验收。
"""
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction

from backend import battle_kernel as k, battle_replay, manual_battle
from backend.battle_contracts import BattleUnitIntent
from backend.demo_domain import require
from backend.runtime import ServiceError


@dataclass(frozen=True)
class Motion:
    """已核验、经过同时扫掠碰撞截断的实际路线，绝非客户端计划路线。

    segments记录实际水平弧长/地表/纵横坡；trace为毫米表现轨迹，progress保存同路线
    的精确亚毫米余数。几何适配器负责两者一致性、完整占地、合法停止面及低能源分支。
    """
    trace: tuple
    segments: tuple = ()
    progress: dict | None = None
    finished: bool = False


class Geometry(ABC):
    """只允许服务端冻结地图适配器实现。没有“缺省合法”或测试网格回退。"""
    @abstractmethod
    def verify(self, state):
        """核验场景/规则/资产摘要、各地形门禁及当前完整世界；失败须抛ServiceError。"""

    @abstractmethod
    def position(self, point):
        """显式转换冻结场景使用的业务坐标，返回毫米位置；不得猜坐标系。"""

    @abstractmethod
    def display_position(self, position_mm):
        """转换为旧页面的显示坐标；仅用于显示，不能回写权威毫米账本。"""

    @abstractmethod
    def motions(self, state, *, start_positive, service_locked, transitions):
        """一次处理双方，返回所有存活单位的Motion。不得修改传入状态。"""

    @abstractmethod
    def observe(self, state, *, start_positive, stable_observers):
        """移动后合法当前观察，返回{side: set(enemy_id)}，包括地形射线/森林遮蔽。"""

    @abstractmethod
    def capture(self, state, before, motions):
        """返回(presence,residents)，核验完整占地/有效层/全步驻留；协调器再限资格。"""

    @abstractmethod
    def camps(self, state):
        """返回本局冻结兵营及合法服务面；营区与据点有效区不得相交。"""

    @abstractmethod
    def fire_clear(self, source, target):
        pass

    @abstractmethod
    def service_clear(self, source, target):
        pass

    @abstractmethod
    def flight_legal(self, unit, action):
        """返回(dock_legal,air_legal)，校验完整起降占地、实际高度及空域。"""

    @abstractmethod
    def landing_clear(self, unit, state):
        pass

    @abstractmethod
    def flight_position(self, unit):
        """已完成起降后的权威位置，包括真实停靠面/飞行高度。"""


def _units(state):
    return {u['unit_id']: u for u in state['units']}


def _event(events, seconds, kind, **fields):
    events.append(dict(event_id=f'{seconds}/{len(events):03d}/{kind}', kind=kind, at_seconds=seconds, **fields))


def _interaction(events, seconds, kind, item, units, **fields):
    source, target = units[item['source_id']], units[item['target_id']]
    _event(events, seconds, kind, source_id=source['unit_id'], target_id=target['unit_id'],
        source_life_id=source['life_id'], target_life_id=target['life_id'],
        source_position=deepcopy(source['position_mm']), target_position=deepcopy(target['position_mm']), **fields)


class Executor:
    def __init__(self, geometry):
        require(isinstance(geometry, Geometry), 'BATTLE_ENGINE_NOT_READY', '需要完整的冻结地图适配器', 503)
        self.geometry = geometry

    def supports(self, state):
        """只读能力检查；无修改、联网、创建游戏、隐式版本升级。"""
        try:
            self._verify(state)
        except (ServiceError, KeyError, TypeError, ValueError):
            return False
        return True

    def _verify(self, state):
        manual_battle.validate(state)
        k.validate(state)
        require(state['battle_ledger']['elapsed_seconds'] == state['manual_battle']['elapsed_seconds'],
                'BATTLE_STATE_INVALID', '轮次时钟与精确账本不一致')
        require(not state.get('battle_result') and state['manual_battle']['stage'] == 'planning',
                'BATTLE_FINISHED', '终局不得继续执行')
        self.geometry.verify(deepcopy(state))
        self.project_world(state)

    def project_world(self, state):
        k.validate(state)
        profiles = k.profiles()
        units = []
        for unit in sorted(state['units'], key=lambda u: u['unit_id']):
            p = profiles[unit['unit_type']]
            units.append(dict(unit_id=unit['unit_id'], life_id=unit['life_id'], side=unit['side'],
                unit_type=unit['unit_type'], position=deepcopy(unit['position_mm']),
                durability=k.exact(k.number(unit['durability'])), energy=k.exact(k.number(unit['energy'])),
                ammo=k.exact(k.number(unit['ammo'])), max_durability=str(p['durability']),
                max_energy=str(p['energy']), max_ammo=str(p['ammo']),
                base_speed_m_per_minute=str(p['speed_m_per_minute']),
                speed_m_per_minute='0' if unit['retired'] else unit.get('speed_m_per_minute', '0'),
                retired=unit['retired'], docked=unit['air_state'] == 'docked',
                attack_target_id=unit.get('last_attack_target_id')))
        points = [{key: p[key] for key in ('point_id', 'owner', 'advancing_side', 'progress_steps', 'contested')}
                  for p in state['battle_points']]
        world = dict(elapsed_seconds=state['battle_ledger']['elapsed_seconds'], units=units, capture_points=points,
                     blue_score=k.score(state, 'blue')['total'], red_score=k.score(state, 'red')['total'])
        battle_replay.VisualWorld.model_validate(world)
        return world

    def _adopt(self, state, intents):
        units = _units(state)
        require(set(intents) == set(k.SIDES), 'BOTH_SIDES_REQUIRED', '共同计划须同时包含双方')
        for side in k.SIDES:
            seen = set()
            for item in intents[side]:
                item = BattleUnitIntent.model_validate(item).model_dump(mode='json', exclude_none=True)
                identity = item['unit_id']
                require(identity not in seen, 'INTENT_CONFLICT', '共同计划含重复单位')
                seen.add(identity)
                manual_battle._validate_intent(state, side, item)
                unit = units[identity]
                if 'movement' in item:
                    unit['movement'] = None if item['movement']['action'] == 'stop' else deepcopy(item['movement'])
                    unit.pop('movement_progress', None)
                if 'task' in item:
                    task = deepcopy(item['task'])
                    if task['action'] == 'attack_area':
                        task['center_mm'] = self.geometry.position(task.pop('position'))
                        task['radius_mm'] = str(task.pop('radius_m') * 1000)
                    elif task.get('position'):
                        task['position_mm'] = self.geometry.position(task.pop('position'))
                    unit['task'] = None if task['action'] == 'stop' else task
        # 两通道可能跨轮持续，不能只校验本次载荷中的同时出现。
        for unit in units.values():
            task = unit.get('task') or {}
            require(not (unit.get('movement') and task.get('action') in ('takeoff', 'land')),
                    'INTENT_CONFLICT', '已有移动必须先停止才能起降')
        self._services(state)

    def validate_submission(self, state, actor):
        self._verify(state)
        self._adopt(deepcopy(state), {side: list(state['manual_battle']['drafts'][side].values())
            if side == actor else [] for side in k.SIDES})

    def _services(self, state):
        units = _units(state); field, barracks = [], []
        camps = self.geometry.camps(deepcopy(state))
        camp_map = {c['camp_id']: c for c in camps}
        require(len(camp_map) == len(camps), 'BATTLE_SERVICE_INVALID', '兵营标识重复')
        for unit in sorted(units.values(), key=lambda u: u['unit_id']):
            if not k.alive(unit):
                continue
            task = unit.get('task') or {}; action = task.get('action')
            if action in ('resupply', 'repair'):
                target = units.get(task['target_id'])
                if target is None or not k.alive(target) or target['life_id'] != task['target_life_id']:
                    unit['task'] = None
                    continue
                resource = 'repair' if action == 'repair' else task['resource']
                account = 'durability' if resource == 'repair' else resource
                amount_key = 'quantity' if 'quantity' in task else 'target_balance'
                amount = k.number(task[amount_key])
                if amount == 0 or (amount_key == 'target_balance' and k.number(target[account]) >= amount):
                    unit['task'] = None
                    continue
                field.append(dict(source_id=unit['unit_id'], source_life_id=unit['life_id'],
                    target_id=target['unit_id'], target_life_id=target['life_id'], resource=resource,
                    **{amount_key: task[amount_key]}))
            elif action == 'barracks':
                camp = camp_map.get(task['camp_id'])
                require(camp is not None and camp['side'] == unit['side'],
                        'BATTLE_SERVICE_INVALID', '须指定本方冻结兵营')
                request = dict(unit_id=unit['unit_id'], life_id=unit['life_id'], camp_id=task['camp_id'])
                if 'targets' in task:
                    request['targets'] = deepcopy(task['targets'])
                barracks.append(request)
        receivers = k.service_receivers(state, field)
        suppliers = {r['source_id'] for r in field}
        camp_receivers = {r['unit_id'] for r in barracks}
        require(not camp_receivers.intersection(receivers | suppliers),
                'BATTLE_SERVICE_CONFLICT', '兵营及野外统一限制：一单位仅一种角色和一个来源')
        locked = receivers | suppliers | camp_receivers
        require(all(not units[i].get('movement') for i in locked),
                'INTENT_CONFLICT', '服务供受双方须先明确停止持续移动')
        return field, barracks, receivers | camp_receivers, locked, camps

    def _motion(self, state, positive, locked, transitions, events, seconds):
        units = _units(state)
        motions = self.geometry.motions(deepcopy(state), start_positive=frozenset(positive),
            service_locked=frozenset(locked), transitions=frozenset(transitions))
        require(set(motions) == {i for i, u in units.items() if k.alive(u)},
                'BATTLE_GEOMETRY_INVALID', '地图必须提供所有存活实体的同步轨迹证据')
        stationary, moved_energy = set(), {}
        for identity, motion in sorted(motions.items()):
            unit = units[identity]
            require(isinstance(motion, Motion) and 1 <= len(motion.trace) <= 128,
                    'BATTLE_GEOMETRY_INVALID', '轨迹证据无效')
            for position in motion.trace:
                battle_replay.VisualPosition.model_validate(position)
            require(motion.trace[0] == unit['position_mm'], 'BATTLE_GEOMETRY_INVALID', '轨迹起点必须等于权威位置')
            distance = sum((k.number(segment['length_mm']) for segment in motion.segments), Fraction(0))
            if distance == 0:
                require(all(p == unit['position_mm'] for p in motion.trace),
                        'BATTLE_GEOMETRY_INVALID', '零路程不能更改位置')
                stationary.add(identity)
                unit['speed_m_per_minute'] = '0'
            else:
                require(identity in positive and identity not in locked and identity not in transitions
                        and unit.get('movement') and (unit['unit_type'] != 'recon' or unit['air_state'] == 'flying')
                        and len(motion.trace) >= 2,
                        'BATTLE_GEOMETRY_INVALID', '失能、服务中、起降中或无移动授权的单位不能移动')
                budget = k.segment_budget(unit['unit_type'], motion.segments, unit['energy'],
                    moving_attack=unit['unit_type'] == 'ground' and (unit.get('task') or {}).get('action') in ('attack', 'attack_area'))
                require(budget['lengths_mm'] == [k.exact(k.number(s['length_mm'])) for s in motion.segments],
                        'BATTLE_GEOMETRY_INVALID', '实际轨迹超出5秒或能源预留预算')
                moved_energy[identity] = budget['energy']
                k.consume_energy(state, unit, k.number(budget['energy']))
                unit['position_mm'] = deepcopy(motion.trace[-1])
                unit['speed_m_per_minute'] = k.exact(distance * 12 / 1000)
                _event(events, seconds, 'move', source_id=identity, source_life_id=unit['life_id'], trace=deepcopy(list(motion.trace)))
                if unit['unit_type'] == 'recon' and k.number(unit['energy']) == 0:
                    k._retire(state, unit, 'air_energy_exhausted')
            if motion.progress is not None:
                unit['movement_progress'] = deepcopy(motion.progress)
            if motion.finished:
                unit['movement'] = None
                unit.pop('movement_progress', None)
        k.validate(state)
        return motions, stationary, moved_energy

    def step(self, state):
        """同一个纯事务内完整5秒；异常丢弃全部局部结果，不返回半次伤害/转移。"""
        before = deepcopy(state); result = deepcopy(state); events = []
        k.validate(result)
        self.geometry.verify(deepcopy(result))
        seconds = result['battle_ledger']['elapsed_seconds'] + 5
        require(seconds <= 3600 and not result.get('battle_result'), 'BATTLE_FINISHED', '终局不得再推进')
        units = _units(result)
        positive = {i for i, u in units.items() if k.alive(u) and k.number(u['energy']) > 0}
        stable = {i for i, u in units.items() if k.alive(u) and (u['unit_type'] != 'recon' or u['air_state'] == 'flying')}
        field, barracks, receivers, locked, camps = self._services(result)
        transitions = {}
        for identity in sorted(units):
            unit = _units(result)[identity]
            if not k.alive(unit) or unit['unit_type'] != 'recon':
                continue
            action = (unit.get('task') or {}).get('action')
            action = action if action in ('takeoff', 'land') else None
            dock, air = self.geometry.flight_legal(deepcopy(unit), action)
            result, transition = k.air_begin(result, identity, action, dock_legal=dock is True, air_legal=air is True)
            if transition:
                transitions[identity] = transition
                stable.discard(identity)
        motions, stationary, moved_energy = self._motion(result, positive, locked, transitions, events, seconds)
        units = _units(result)
        visible = self.geometry.observe(deepcopy(result), start_positive=frozenset(positive), stable_observers=frozenset(stable))
        require(set(visible) == set(k.SIDES) and all(set(ids) <= {i for i, u in units.items()
            if k.alive(u) and u['side'] != side} for side, ids in visible.items()),
            'BATTLE_GEOMETRY_INVALID', '观察证据须为当前存活敌军集合')
        presence, residents = self.geometry.capture(deepcopy(result), deepcopy(before), motions)
        eligible = {i for i, u in units.items() if i in positive and k.alive(u)
                    and k.number(u['energy']) > 0 and u['unit_type'] in ('ground', 'support')}
        presence = {p['point_id']: {s: set(presence.get(p['point_id'], {}).get(s, ()))
                    & eligible & {i for i, u in units.items() if u['side'] == s} for s in k.SIDES}
                    for p in result['battle_points']}
        residents = {p: {s: set(residents.get(p, {}).get(s, ())) & stationary & ids for s, ids in sides.items()}
                     for p, sides in presence.items()}
        result, shots = k.resolve_fire(result, seconds=seconds, start_positive=positive,
            moved_energy=moved_energy, observed=visible, receivers=receivers,
            fire_clear=lambda a, b: self.geometry.fire_clear(deepcopy(a), deepcopy(b)) is True)
        units = _units(result)
        for unit in units.values():
            unit.pop('last_attack_target_id', None)
        for shot in shots:
            units[shot['source_id']]['last_attack_target_id'] = shot['target_id']
            _interaction(events, seconds, 'shot', shot, units)
            _interaction(events, seconds, 'hit', shot, units, amount=shot['damage'])
        clear = lambda a, b: self.geometry.service_clear(deepcopy(a), deepcopy(b)) is True
        result, deliveries = k.service_step(result, field, seconds=seconds, stationary=stationary,
                                           start_positive=positive, service_clear=clear)
        units = _units(result)
        for delivery in deliveries:
            _interaction(events, seconds, 'service', delivery, units, resource=delivery['resource'], amount=delivery['amount'])
            source, target = units[delivery['source_id']], units[delivery['target_id']]
            request = delivery['remaining_request']; task = source.get('task')
            if task is not None:
                if 'quantity' in request:
                    task['quantity'] = request['quantity']
                    done = k.number(request['quantity']) == 0
                else:
                    field_name = 'durability' if request['resource'] == 'repair' else request['resource']
                    done = k.number(target[field_name]) >= k.number(request['target_balance'])
                if done:
                    source['task'] = None
        result, camp_deliveries = k.barracks_step(result, barracks, camps, seconds=seconds,
                                                stationary=stationary, service_clear=clear)
        # 兵营不是单位：独立事件类型，绝不编造单位life或调用预演的自动补给。
        camp_map = {camp['camp_id']: camp for camp in camps}
        category_resource = dict(energy='energy', ammo='ammo', durability='repair',
                                 stock_energy='energy', stock_ammo='ammo', stock_repair='repair')
        units = _units(result)
        for delivery in camp_deliveries:
            camp, target = camp_map[delivery['camp_id']], units[delivery['target_id']]
            _event(events, seconds, 'barracks_service', camp_id=camp['camp_id'],
                target_id=target['unit_id'], target_life_id=target['life_id'],
                source_position=deepcopy(camp['position_mm']), target_position=deepcopy(target['position_mm']),
                delivery_category=delivery['resource'], resource=category_resource[delivery['resource']], amount=delivery['amount'])
        result.setdefault('battle_round_facts', []).append(dict(at_seconds=seconds,
            shots=deepcopy(shots), deliveries=deepcopy(deliveries), barracks_deliveries=deepcopy(camp_deliveries)))
        old_points = deepcopy(result['battle_points'])
        result, result['battle_points'] = k.capture_step(result, result['battle_points'], presence=presence, residents=residents)
        for old, point in zip(old_points, result['battle_points']):
            if old['owner'] != point['owner']:
                _event(events, seconds, 'capture', source_id=point['point_id'])
        for identity, transition in sorted(transitions.items()):
            unit = _units(result)[identity]
            if not k.alive(unit):
                continue
            result = k.air_finish(result, identity, landing_clear=self.geometry.landing_clear(deepcopy(unit), deepcopy(result)) is True)
            unit = _units(result)[identity]
            if unit['air_state'] == ('flying' if transition == 'takeoff' else 'docked'):
                unit['position_mm'] = self.geometry.flight_position(deepcopy(unit))
                battle_replay.VisualPosition.model_validate(unit['position_mm'])
                unit['task'] = None
                _event(events, seconds, transition, source_id=identity, source_life_id=unit['life_id'],
                    source_position=deepcopy(_units(before)[identity]['position_mm']), target_position=deepcopy(unit['position_mm']))
        units = _units(result)
        for identity, unit in sorted(units.items()):
            unit['position'] = self.geometry.display_position(deepcopy(unit['position_mm']))
            if unit['retired'] and not _units(before)[identity]['retired']:
                unit['speed_m_per_minute'] = '0'
                _event(events, seconds, 'retired', source_id=identity, source_life_id=unit['life_id'])
        result['battle_ledger']['elapsed_seconds'] = seconds
        k.validate(result)
        result['battle_result'] = k.terminal(result, seconds)
        # 当前可见集合在交火前冻结；只向各方发布可见敌军，不返回主持人的完整世界。
        for side in k.SIDES:
            result.setdefault('observations', {}).setdefault(side, {})['contacts'] = [
                {key: deepcopy(units[i][key]) for key in ('unit_id', 'life_id', 'side', 'unit_type',
                    'position_mm', 'position', 'durability', 'energy', 'ammo', 'retired', 'air_state', 'speed_m_per_minute')} for i in sorted(visible[side])]
        return result, dict(at_seconds=seconds, world=self.project_world(result), events=events)

    def __call__(self, state, intents):
        self._verify(state)
        result = deepcopy(state)
        initial = self.project_world(result)
        self._adopt(result, deepcopy(intents))
        result['battle_round_facts'] = []
        frames = []
        for _ in range(12):
            result, frame = self.step(result)
            frames.append(frame)
            if result.get('battle_result'):
                break
        info = state['manual_battle']; manifest = state['manifest']
        record = battle_replay.seal_record(round_number=info['round_number'],
            rule_sha256=manifest['battle_rule_sha256'], scenario_sha256=manifest['battle_scenario_sha256'],
            previous_round_sha256=info['previous_round_sha256'], initial_world=initial, frames=frames,
            finished=bool(result.get('battle_result')))
        return result, record
