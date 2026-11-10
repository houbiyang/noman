"""显式启用的多智能体协同测试规则，所有执行结果由程序产生。"""

from copy import deepcopy
import math

from backend import movement, spatial
from backend.demo_domain import require, reset_plan


ACTIVE_TASKS = ("queued", "running", "waiting")


def enable(state, rules):
    """在准备阶段启用独立测试状态，不变更地图原点位或已有单位。"""
    require(state['lifecycle']['phase'] == 'preparing', 'PREPARATION_REQUIRED', '仅准备阶段可以启用协同测试规则')
    require('spatial_profile' in state['manifest'], 'SPATIAL_TEST_REQUIRED', '请使用已启用空间测试的对局')
    require('coordination' not in state, 'COORDINATION_ALREADY_ENABLED', '本局已冻结协同测试规则，不能原地修改')
    data = spatial.map_data(state)
    probe = dict(unit_id='map-validation', side='red', unit_type='ground', position=spatial.business((3000, 6000)), energy=100, durability=100)
    navigator = movement.Navigator(state, probe, 'move')
    points = []
    for source in data['points']:
        if source['id'] not in ('TASK_A', 'TASK_B', 'TASK_C'):
            continue
        center = (source['x'], source['z'])
        candidates = [center] + [(center[0] + math.cos(angle * math.pi / 4) * rules['capture_radius_m'] * .7,
                                 center[1] + math.sin(angle * math.pi / 4) * rules['capture_radius_m'] * .7) for angle in range(8)]
        legal = next((point for point in candidates if navigator.clear(point)), None)
        points.append(dict(point_id=source['id'], name=source['name'], position=spatial.business(center),
                           approach_position=spatial.business(legal) if legal else None,
                           navigation_status='test_surface_verified' if legal else 'needs_verification',
                           navigation_layer='test_surface', owner=None, status='neutral', candidate_side=None,
                           progress_kind=None, progress_steps=0))
    require(len(points) == 3, 'CONTROL_POINTS_MISSING', '当前地图缺少三个共享控制点')
    state['coordination'] = dict(schema_version='coordination-test-v1', test_only=True, rules=deepcopy(rules),
                                 points=points, plans={}, all_held=dict(red=0, blue=0), last_scored_step=0,
                                 geometry_policy='movement_grid_surface_only',
                                 limitations=['导航层为现有移动网格测试地表，未完成正式桥上桥下及场景公平性验收',
                                              '观察采用保守建筑柱体与五十米地形采样，不代表正式视野规则',
                                              '保障采用一单位补给转换一单位能源的抽象测试，不含维修和运输',
                                              '交战与事件效果仍未启用'])
    state['scores'] = dict(available=True, test_only=True, red=0, blue=0, winner=None, reason=None)
    for side in ('red', 'blue'):
        reset_plan(state, side)


def available(unit):
    """已知有效单位才参与动作；未知耐久不补成可用状态。"""
    return unit.get('availability') == 'available' and isinstance(unit.get('durability'), (int, float)) and unit['durability'] > 0


def point_for(state, identity):
    """读取公开点态，未知标识返回明确拒绝。"""
    point = next((item for item in state.get('coordination', {}).get('points', []) if item['point_id'] == identity), None)
    require(point is not None, 'POINT_NOT_FOUND', '控制点不存在或尚未启用协同规则', 422)
    return point


def in_point(state, unit, point):
    """按测试导航地表及米制范围检查占点在场资格。"""
    if unit['unit_type'] != 'ground' or not available(unit) or movement.is_garrisoned(state, unit):
        return False
    if point['navigation_status'] != 'test_surface_verified':
        return False
    radius = state['coordination']['rules']['capture_radius_m']
    return (math.dist(spatial.meters(unit['position']), spatial.meters(point['position'])) <= radius + 1e-6
            and movement.Navigator(state, unit, 'move').clear(spatial.meters(unit['position'])))


def task_destination(state, task):
    """显式坐标优先；点位移动使用已经通过测试通行检查的接近点。"""
    if task.get('position'):
        return task['position']
    point = point_for(state, task.get('target_point_id'))
    require(point.get('approach_position') is not None, 'POINT_NEEDS_VERIFICATION', '控制区缺少通过检查的接近点')
    return point['approach_position']


def prepare_step(state):
    """在移动前冻结双方主动作；只执行已批准任务，依赖不在同一步连跳。"""
    actions = {}
    if 'coordination' not in state:
        return actions
    step = state['lifecycle']['simulation_step']
    units = {unit['unit_id']: unit for unit in state['units']}
    for side, plan in state['coordination']['plans'].items():
        if plan['status'] != 'adopted':
            continue
        accepted = state['decisions']['plans'][side]
        if accepted.get('source') != 'multi_agent' or accepted.get('proposal_id') != plan.get('proposal_id'):
            plan.update(status='stale', blocked_reason='原分工已被准备变更或人工建议替换，需要重新规划')
            continue
        tasks = plan['tasks']
        completed = {task['task_id'] for task in tasks if task['status'] == 'succeeded'}
        failed = {task['task_id'] for task in tasks if task['status'] in ('failed', 'cancelled')}
        occupied = {task['unit_id'] for task in tasks if task['status'] == 'running'}
        for task in tasks:
            if task['status'] not in ACTIVE_TASKS:
                continue
            if failed.intersection(task['depends_on']):
                task.update(status='failed', blocked_reason='依赖任务已失败，需要重新规划')
                continue
            if not set(task['depends_on']) <= completed:
                task.update(status='waiting', blocked_reason='等待前置任务完成')
                continue
            unit = units.get(task['unit_id'])
            if unit is None or unit['side'] != side or not available(unit):
                task.update(status='failed', blocked_reason='单位不存在或已失效')
                continue
            if task['status'] != 'running' and unit['unit_id'] in occupied:
                continue
            if unit['unit_id'] in actions:
                continue
            if task.get('started_step') is None:
                task['started_step'] = step
            if step - task['started_step'] >= task['timeout_steps']:
                task.update(status='failed', blocked_reason='任务达到仿真步超时，需重新规划')
                active = next((route for route in state['routes'] if route.get('agent_task_id') == task['task_id']
                               and route['unit_id'] == task['unit_id']), None)
                if active:
                    active['status'] = 'blocked'
                continue
            task.update(status='running', blocked_reason=None)
            if task['action'] == 'move' and not task.get('route_id'):
                from backend.runtime import ServiceError
                try:
                    movement.order(state, side, dict(unit_id=unit['unit_id'], mode=task['movement_mode'],
                                                    position=task_destination(state, task)))
                except ServiceError as error:
                    task.update(status='waiting', blocked_reason=error.message)
                    continue
                route = next(route for route in state['routes'] if route['unit_id'] == unit['unit_id'])
                route['agent_task_id'] = task['task_id']
                task['route_id'] = route['route_id']
            target = units.get(task.get('target_unit_id'))
            actions[unit['unit_id']] = dict(side=side, task=task, start_position=deepcopy(unit['position']),
                                            target_start_position=deepcopy(target['position']) if target else None)
            occupied.add(unit['unit_id'])
    return actions


def visible(state, observer, target):
    """采用声明过的保守采样视线，不从模型报告产生发现事实。"""
    rules, data = state['coordination']['rules'], spatial.map_data(state)
    start, end = spatial.meters(observer['position']), spatial.meters(target['position'])
    distance = math.dist(start, end)
    if distance > rules['observation_radius_m']:
        return False
    start_height = spatial.height(data, start) + rules['observer_height_m']
    end_height = spatial.height(data, end) + 2
    samples = max(2, math.ceil(distance / 50))
    for index in range(1, samples):
        ratio = index / samples
        point = tuple(start[axis] + (end[axis] - start[axis]) * ratio for axis in (0, 1))
        if spatial.height(data, point) >= start_height + (end_height - start_height) * ratio:
            return False
    # 地图建筑缺少规则高度，首版采用保守不透明柱体，避免假定可以透视。
    return not any(spatial.segment_distance((building['x'], building['z']), start, end)
                   <= state['manifest']['spatial_profile']['building_exclusion_radius_m'] for building in data['buildings'])


def finish_step(state, actions):
    """移动之后统一服务、观察、占点和判胜，模型不能直接提交结果。"""
    if 'coordination' not in state:
        return
    units = {unit['unit_id']: unit for unit in state['units']}
    rules = state['coordination']['rules']
    step = state['lifecycle']['simulation_step']
    if step <= state['coordination']['last_scored_step']:
        return
    served = set()
    observed = {side: [] for side in ('red', 'blue')}
    for action in actions.values():
        current_tasks = state['coordination']['plans'].get(action['side'], {}).get('tasks', [])
        current = next((task for task in current_tasks if task['task_id'] == action['task']['task_id']
                        and task['unit_id'] == action['task']['unit_id']), None)
        if current is not None:
            action['task'] = current
    for identity, action in actions.items():
        unit, task = units[identity], action['task']
        stationary = unit['position'] == action['start_position']
        kind = task['action']
        if kind == 'move':
            route = next((route for route in state['routes'] if route['route_id'] == task.get('route_id')), None)
            if not route:
                task.update(status='failed', blocked_reason='原任务路线已被其他命令替换')
            elif route['status'] == 'arrived':
                task['status'] = 'succeeded'
            elif route['status'] in ('waiting', 'blocked'):
                task['blocked_reason'] = '路线受阻或能源不足，等待支援或重新规划'
        elif not stationary:
            task['blocked_reason'] = '当前步发生移动，不能同时执行另一主动作'
        elif kind == 'observe':
            contacts = [dict(unit_id=other['unit_id'], position=deepcopy(other['position']), observed_step=step,
                             observer_unit_id=identity, source='coordination-test-los', state='current')
                        for other in units.values() if other['side'] != unit['side'] and available(other)
                        and not movement.is_garrisoned(state, other) and visible(state, unit, other)]
            observed[unit['side']].extend(contacts)
            task['status'] = 'succeeded'
            task['result'] = dict(observed_step=step, contact_count=len(contacts))
        elif kind == 'resupply':
            target = units.get(task.get('target_unit_id'))
            target_action = actions.get(task.get('target_unit_id'))
            if not target or not available(target) or target.get('energy') is None:
                task.update(status='failed', blocked_reason='服务对象不存在或资源状态未知')
            elif target['position'] != action.get('target_start_position'):
                task['blocked_reason'] = '服务对象本步发生移动'
            elif target['unit_id'] in served:
                task['blocked_reason'] = '服务对象本步已有保障服务'
            elif target_action and target_action['task']['action'] != 'wait':
                task['blocked_reason'] = '服务对象正在执行其他主动作'
            elif math.dist(spatial.meters(unit['position']), spatial.meters(target['position'])) > rules['service_radius_m']:
                task['blocked_reason'] = '服务对象超出补给范围'
            else:
                maximum = state['manifest']['spatial_profile']['unit_profiles'][target['unit_type']]['energy']
                amount = min(rules['service_per_step'], unit.get('supply') or 0, max(0, maximum - target['energy']))
                unit['supply'] -= amount
                target['energy'] += amount
                served.add(target['unit_id'])
                task['result'] = dict(transferred=round(task.get('result', {}).get('transferred', 0) + amount, 6))
                if target['energy'] >= maximum - 1e-6:
                    task['status'] = 'succeeded'
                elif not amount:
                    task['blocked_reason'] = '保障库存不足'
        elif kind == 'wait':
            task['elapsed_steps'] = task.get('elapsed_steps', 0) + 1
            if task['elapsed_steps'] >= task['duration_steps']:
                task['status'] = 'succeeded'
        if task['status'] == 'succeeded':
            task['completed_step'] = step
    for side, contacts in observed.items():
        observation = state['observations'][side]
        history = {record['unit_id']: record for record in observation.get('last_seen_records', [])}
        history.update({record['unit_id']: dict(record, state='historical') for record in observation.get('contacts', [])})
        current = {record['unit_id']: record for record in contacts}
        observation['contacts'] = list(current.values())
        observation['last_seen_records'] = [record for identity, record in history.items() if identity not in current]
    settle_points(state, actions)


def settle_points(state, actions):
    """双方同一步统一计分，重复调用同一步不重复累计。"""
    coordination, step = state['coordination'], state['lifecycle']['simulation_step']
    if step <= coordination['last_scored_step']:
        return
    rules, scores = coordination['rules'], state['scores']
    stable = dict(red=0, blue=0)
    for point in coordination['points']:
        previous_owner = point['owner']
        present = {side: [unit for unit in state['units'] if unit['side'] == side and in_point(state, unit, point)]
                   for side in ('red', 'blue')}
        eligible = {side: [unit for unit in present[side] if unit['unit_id'] in actions
                          and actions[unit['unit_id']]['task']['action'] in ('capture', 'hold')
                          and actions[unit['unit_id']]['task'].get('target_point_id') == point['point_id']
                          and actions[unit['unit_id']]['start_position'] == unit['position']] for side in present}
        if all(present.values()):
            point.update(status='contested', candidate_side=None, progress_kind=None, progress_steps=0)
            continue
        side = next((side for side in ('red', 'blue') if present[side]), None)
        if side and side != point['owner'] and eligible[side]:
            kind = 'capture' if point['owner'] is None else 'neutralize'
            progress = point['progress_steps'] + 1 if point['candidate_side'] == side and point['progress_kind'] == kind else 1
            point.update(status='capturing' if kind == 'capture' else 'neutralizing', candidate_side=side,
                         progress_kind=kind, progress_steps=progress)
            if progress >= rules['capture_steps' if kind == 'capture' else 'neutralize_steps']:
                point.update(owner=side if kind == 'capture' else None, status='controlled' if kind == 'capture' else 'neutral',
                             candidate_side=None, progress_kind=None, progress_steps=0)
        else:
            point.update(status='controlled' if point['owner'] else 'neutral', candidate_side=None,
                         progress_kind=None, progress_steps=0)
        owner = point['owner']
        if owner and previous_owner == owner and (side is None or side == owner):
            scores[owner] += 1
            stable[owner] += 1
        for unit in eligible.get(side, []) if side else []:
            task = actions[unit['unit_id']]['task']
            if task['action'] == 'capture' and point['owner'] == side:
                task.update(status='succeeded', completed_step=step)
            elif task['action'] == 'hold' and point['owner'] == side:
                task['elapsed_steps'] = task.get('elapsed_steps', 0) + 1
                if task['elapsed_steps'] >= task['duration_steps']:
                    task.update(status='succeeded', completed_step=step)
    coordination['last_scored_step'] = step
    for side in ('red', 'blue'):
        coordination['all_held'][side] = coordination['all_held'][side] + 1 if stable[side] == 3 else 0
    winner = next((side for side in ('red', 'blue') if coordination['all_held'][side] >= rules['all_points_steps']), None)
    reason = 'all_points_held' if winner else None
    if not reason and (max(scores['red'], scores['blue']) >= rules['victory_points'] or step >= state['lifecycle']['deadline_step']):
        winner = None if scores['red'] == scores['blue'] else ('red' if scores['red'] > scores['blue'] else 'blue')
        reason = 'score_target' if max(scores['red'], scores['blue']) >= rules['victory_points'] else 'time_limit'
    if reason:
        scores.update(winner=winner, reason=reason)
        state['lifecycle'].update(phase='finished', controller_session_id=None)
