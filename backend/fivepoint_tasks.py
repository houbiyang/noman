"""五点方案的确定性任务依赖执行；模型只能提出任务，完成状态由规则事实判定。"""

from copy import deepcopy
from pydantic import ValidationError

from backend.fivepoint_parameters import require, number, RuleViolation
from backend.contracts import FivepointTask


TERMINAL = ("completed", "failed", "cancelled")


def execution_order(order: dict, waypoint_index=0) -> dict:
    """把持续任务转换成本步动作，不增加位移或规划机会。"""
    if order['action'] in ('observe', 'capture', 'hold'):
        return dict(action='wait')
    if order['action'] == 'patrol':
        return dict(action='move', destination=order['waypoints'][waypoint_index])
    return order


def validate_tasks(state: dict, side: str, tasks: list) -> list:
    """检查身份、无环依赖、同单位先后顺序和动作权限，返回独立任务状态。"""
    from backend.fivepoint_engine import check_orders
    require(isinstance(tasks, list) and len(tasks) <= 1000, "TASKS_INVALID", "任务列表过长或类型无效")
    require(all(isinstance(task, dict) for task in tasks), "TASKS_INVALID", "任务必须是对象")
    identities = [task.get("task_id") for task in tasks]
    require(all(isinstance(identity, str) and 0 < len(identity) <= 80 for identity in identities)
            and len(identities) == len(set(identities)), "TASK_ID_INVALID", "任务标识必须非空且唯一")
    lookup = {task["task_id"]: task for task in tasks}
    ancestors, pending = {}, set(identities)
    for identity in identities:
        dependencies = lookup[identity].get("depends_on", [])
        require(isinstance(dependencies, list) and all(isinstance(dependency, str) and dependency in lookup for dependency in dependencies)
                and len(dependencies) == len(set(dependencies)), "TASK_DEPENDENCY_INVALID", "前置任务缺失或重复")
    while pending:
        ready = [identity for identity in identities if identity in pending
                 and all(dependency in ancestors for dependency in lookup[identity].get("depends_on", []))]
        require(bool(ready), "TASK_CYCLE", "任务依赖存在环路")
        for identity in ready:
            dependencies = lookup[identity].get("depends_on", [])
            ancestors[identity] = set(dependencies)
            for dependency in dependencies:
                ancestors[identity].update(ancestors[dependency])
            pending.remove(identity)
    normalized = []
    for task in tasks:
        require(set(task) <= {"task_id", "unit_id", "order", "depends_on", "duration_steps", "deadline_second", "target_point_id", "branches"},
                "TASK_FIELDS_INVALID", "任务包含未授权字段")
        identity, unit_id = task["task_id"], task.get("unit_id")
        require(isinstance(unit_id, str) and bool(unit_id), 'ORDER_UNIT_INVALID', '单位标识无效')
        order = task.get("order")
        require(isinstance(order, dict), "TASK_ORDER_INVALID", "任务缺少动作")
        action = order.get("action")
        duration = task.get("duration_steps")
        deadline = task.get("deadline_second")
        if duration is not None:
            require(type(duration) is int and 1 <= duration <= 720, "TASK_DURATION_INVALID", "持续步数须为正整数")
        if deadline is not None:
            require(type(deadline) is int and deadline % 5 == 0 and state["step"] * 5 < deadline <= 3600,
                    "TASK_DEADLINE_INVALID", "任务截止时刻须为未来五秒边界")
        if action == 'patrol':
            require(set(order) == {'action', 'waypoints'} and isinstance(order['waypoints'], list)
                    and 2 <= len(order['waypoints']) <= 100, 'TASK_ROUTE_INVALID', '巡查须明确至少两个授权路径点')
            require(duration is not None or deadline is not None, 'TASK_DURATION_INVALID', '巡查须指定结束条件')
            for destination in order['waypoints']:
                check_orders(state, side, {unit_id: dict(action='move', destination=destination)})
        elif action in ("observe", "capture", "hold"):
            require(set(order) == {"action"}, "TASK_ORDER_INVALID", "驻留任务不接受额外动作参数")
            check_orders(state, side, {unit_id: dict(action="wait")})
            if action == "capture":
                unit = next(unit for unit in state['units'] if unit['unit_id'] == unit_id)
                require(unit['unit_type'] != 'recon' and any(point['point_id'] == task.get('target_point_id') for point in state['points']),
                        "TASK_POINT_INVALID", "占领任务须指定合法地面单位和目标")
            if action == "hold":
                require(duration is not None or deadline is not None, "TASK_DURATION_INVALID", "持续守候须指定结束条件")
        else:
            check_orders(state, side, {unit_id: order})
            if action == "wait":
                require(duration is not None, "TASK_DURATION_INVALID", "等待任务须指定完整步数")
            if action in ('attack', 'move_attack') and 'target_area' in order:
                require(duration is not None or deadline is not None, 'TASK_DURATION_INVALID', '范围交火须指定结束条件')
        branches = task.get('branches', [])
        require(isinstance(branches, list) and len(branches) <= 2, 'TASK_BRANCH_INVALID', '最多预授权两类补给分支')
        conditions = set()
        for branch in branches:
            require(isinstance(branch, dict) and set(branch) == {'condition', 'orders'}
                    and branch['condition'] in ('low_energy', 'ammo_empty') and branch['condition'] not in conditions,
                    'TASK_BRANCH_INVALID', '补给条件须唯一且明确')
            conditions.add(branch['condition'])
            require(isinstance(branch['orders'], list) and 1 <= len(branch['orders']) <= 20,
                    'TASK_BRANCH_INVALID', '补给分支须包含有界动作序列')
            for branch_order in branch['orders']:
                require(isinstance(branch_order, dict) and branch_order.get('action') in ('move', 'camp', 'takeoff', 'land'),
                        'TASK_BRANCH_INVALID', '补给分支仅执行已授权的移动、起降和兵营补给')
                check_orders(state, side, {unit_id: branch_order})
            require(branch['orders'][-1]['action'] == 'camp', 'TASK_BRANCH_INVALID', '补给分支须以明确兵营服务结束')
            camp = next(camp for camp in state['frozen']['camps'] if camp['camp_id'] == branch['orders'][-1]['camp_id'])
            require(all(item['destination'] == camp['position'] for item in branch['orders'] if item['action'] == 'move'),
                    'TASK_BRANCH_INVALID', '补给移动只能前往本分支明确授权的兵营')
        for other in tasks:
            if other is task or other.get("unit_id") != unit_id:
                continue
            require(other["task_id"] in ancestors[identity] or identity in ancestors[other["task_id"]],
                    "TASK_UNIT_CONFLICT", "同一单位的任务须明确先后依赖")
        normalized.append(dict(**deepcopy(task), status="queued", active_steps=0, completed_step=None, reason=None))
    roots = {task['unit_id']: execution_order(task['order'])
             for task in normalized if not task.get('depends_on')}
    check_orders(state, side, roots)
    for index, task in enumerate(normalized):
        for other in normalized[index + 1:]:
            if (task['unit_id'] == other['unit_id'] or task['task_id'] in ancestors[other['task_id']]
                    or other['task_id'] in ancestors[task['task_id']]):
                continue
            alternatives = [[candidate['order']] + [order for branch in candidate.get('branches', []) for order in branch['orders']]
                            for candidate in (task, other)]
            for first_order in alternatives[0]:
                for second_order in alternatives[1]:
                    possible_orders = {candidate['unit_id']: execution_order(order)
                                       for candidate, order in ((task, first_order), (other, second_order))}
                    check_orders(state, side, possible_orders)
    return normalized


def accept_independent(state: dict, side: str, tasks: list) -> tuple:
    """拒绝非法节点、冲突节点及其依赖，保留合法独立任务；错误不包含敌方事实。"""
    require(isinstance(tasks, list) and len(tasks) <= 1000, 'TASKS_INVALID', '任务列表无效')
    identities = [item.get('task_id') if isinstance(item, dict) else None for item in tasks]
    rejected, candidates = {}, {}
    for index, item in enumerate(tasks):
        identity = identities[index]
        label = identity if isinstance(identity, str) else index
        if not isinstance(identity, str) or identities.count(identity) != 1:
            rejected[label] = 'TASK_ID_INVALID'
            continue
        try:
            item = FivepointTask.model_validate(item).model_dump(mode='json', exclude_none=True)
            probe = deepcopy(item)
            probe['depends_on'] = []
            validate_tasks(state, side, [probe])
        except ValidationError:
            rejected[label] = 'TASK_FIELDS_INVALID'
            continue
        except RuleViolation as error:
            rejected[label] = error.code
            continue
        dependencies = item.get('depends_on', [])
        if (not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies)
                or len(dependencies) != len(set(dependencies))):
            rejected[label] = 'TASK_DEPENDENCY_INVALID'
        else:
            candidates[identity] = deepcopy(item)
    normalized = deepcopy(candidates)
    ancestors = {}
    while candidates:
        ready = [identity for identity, item in candidates.items()
                 if all(dependency in ancestors for dependency in item.get('depends_on', []))]
        invalid = [identity for identity, item in candidates.items()
                   if any(dependency in rejected or dependency not in candidates and dependency not in ancestors
                          for dependency in item.get('depends_on', []))]
        for identity in invalid:
            rejected[identity] = 'TASK_DEPENDENCY_INVALID'
            candidates.pop(identity)
        if not ready and not invalid:
            rejected.update({identity: 'TASK_CYCLE' for identity in candidates})
            break
        for identity in ready:
            dependencies = candidates.pop(identity).get('depends_on', [])
            ancestors[identity] = set(dependencies)
            for dependency in dependencies:
                ancestors[identity].update(ancestors[dependency])
    accepted = [item for identity, item in normalized.items() if identity in ancestors]
    for index, item in enumerate(accepted):
        for other in accepted[index + 1:]:
            if item['task_id'] in ancestors[other['task_id']] or other['task_id'] in ancestors[item['task_id']]:
                continue
            pair = [dict(deepcopy(candidate), depends_on=[]) for candidate in (item, other)]
            try:
                validate_tasks(state, side, pair)
            except RuleViolation as error:
                rejected[item['task_id']] = rejected[other['task_id']] = error.code
    for identity, predecessors in ancestors.items():
        if predecessors.intersection(rejected):
            rejected[identity] = 'TASK_DEPENDENCY_INVALID'
    accepted = [item for item in accepted if item['task_id'] not in rejected]
    validate_tasks(state, side, accepted)
    return accepted, [dict(task_id=identity, code=code) if isinstance(identity, str) else dict(task_index=identity, code=code)
                      for identity, code in rejected.items()]


def branch_order(state: dict, task: dict, unit: dict, geometry) -> dict:
    """每五秒检查预授权补给条件，不调用模型；分支进度随任务保存。"""
    from backend.fivepoint_engine import project
    if 'active_branch' not in task:
        for index, branch in enumerate(task.get('branches', [])):
            triggered = branch['condition'] == 'ammo_empty' and unit['unit_type'] == 'ground' and unit['ammo'] == 0
            if branch['condition'] == 'low_energy':
                cost = geometry.service_energy_cost(deepcopy(unit), project(state, unit['side']))
                triggered = cost is not None and number(unit['energy']) <= max(30, number(cost) + 10)
            if triggered:
                task.update(active_branch=index, branch_step=0)
                break
    if 'active_branch' in task:
        return task['branches'][task['active_branch']]['orders'][task['branch_step']]
    return task['order']


def prepare(state: dict, geometry) -> dict:
    """仅以前一步已提交事实激活后继；本步完成的任务不提前激活后续。"""
    from backend.fivepoint_engine import alive, check_orders
    units = {unit['unit_id']: unit for unit in state['units']}
    active = {}
    for side, tasks in state.get('tasks', {}).items():
        if not tasks:
            continue
        lookup = {task['task_id']: task for task in tasks}
        orders, selections = {}, {}
        for task in tasks:
            if task['status'] in TERMINAL:
                continue
            if not alive(units[task['unit_id']]):
                task.update(status='failed', reason='unit_retired')
                continue
            if state['step'] * 5 >= task.get('deadline_second', 3601):
                task.update(status='failed', reason='deadline_reached')
                continue
            if task['order']['action'] == 'service' and not alive(units[task['order']['target_id']]):
                task.update(status='failed', reason='target_retired')
        # 先传播失败，避免任务在列表中的顺序改变依赖结果。
        changed = True
        while changed:
            changed = False
            for task in tasks:
                if task['status'] not in TERMINAL and any(lookup[identity]['status'] in ('failed', 'cancelled')
                                                         for identity in task.get('depends_on', [])):
                    task.update(status='failed', reason='dependency_failed')
                    changed = True
        for task in tasks:
            if task['status'] in TERMINAL:
                continue
            dependencies = [lookup[identity] for identity in task.get('depends_on', [])]
            if not all(dependency['status'] == 'completed' and dependency['completed_step'] <= state['step'] for dependency in dependencies):
                task['status'] = 'waiting'
                continue
            task['status'] = 'running'
            order = branch_order(state, task, units[task['unit_id']], geometry)
            orders[task['unit_id']] = execution_order(order, task.get('waypoint_index', 0))
            selections[task['unit_id']] = task['task_id']
        # 动态依赖汇合产生服务冲突时整步拒绝，不能按遍历顺序执行半个方案。
        check_orders(state, side, orders)
        state['plans'][side] = orders
        active[side] = selections
    return active


def finish(state: dict, active: dict, initially_active: set, transitioning: dict) -> None:
    """依据实际归属、位置、账户或状态变化完成任务；等待不伪装成功。"""
    from backend.fivepoint_engine import alive
    units = {unit['unit_id']: unit for unit in state['units']}
    boundary_step = state['step'] + 1
    for side, selections in active.items():
        tasks = {task['task_id']: task for task in state['tasks'][side]}
        for identity, task_id in selections.items():
            task, unit = tasks[task_id], units[identity]
            if not alive(unit):
                task.update(status='failed', reason='unit_retired')
                continue
            if 'active_branch' not in task:
                task['active_steps'] += 1
            order = (task['branches'][task['active_branch']]['orders'][task['branch_step']]
                     if 'active_branch' in task else task['order'])
            completed = False
            action = order['action']
            if action == 'move':
                completed = (unit['position']['layer'] == order['destination']['layer'] and
                             all(number(current) == number(expected) for current, expected in zip(unit['position']['xy'], order['destination']['xy'])))
            elif action == 'patrol':
                waypoint_index = task.get('waypoint_index', 0)
                destination = order['waypoints'][waypoint_index]
                if (unit['position']['layer'] == destination['layer'] and
                        all(number(current) == number(expected) for current, expected in zip(unit['position']['xy'], destination['xy']))):
                    task['waypoint_index'] = (waypoint_index + 1) % len(order['waypoints'])
                completed = (task.get('duration_steps') is not None and task['active_steps'] >= task['duration_steps']
                             or boundary_step * 5 >= task.get('deadline_second', 3601))
            elif action == 'observe':
                completed = (identity in initially_active and identity not in transitioning and number(unit['energy']) > 0
                             and (unit['unit_type'] != 'recon' or unit['flight'] == 'airborne'))
            elif action == 'capture':
                completed = any(point['point_id'] == task['target_point_id'] and point['owner'] == side for point in state['points'])
            elif action in ('wait', 'hold'):
                completed = (task.get('duration_steps') is not None and task['active_steps'] >= task['duration_steps']
                             or action == 'hold' and boundary_step * 5 >= task.get('deadline_second', 3601))
            elif action in ('takeoff', 'land'):
                completed = unit['flight'] == ('airborne' if action == 'takeoff' else 'docked')
            elif action == 'service':
                target = units[order['target_id']]
                if not alive(target):
                    task.update(status='failed', reason='target_retired')
                    continue
                completed = (number(order['amount']) == 0 if 'amount' in order else number(target[order['category']]) >= number(order['balance']))
            elif action in ('attack', 'move_attack'):
                if 'target_area' in order:
                    completed = (task.get('duration_steps') is not None and task['active_steps'] >= task['duration_steps']
                                 or boundary_step * 5 >= task.get('deadline_second', 3601))
                else:
                    target_id = order['target_id']
                    observed = state['history'][side].get(target_id, {})
                    completed = (target_id in units and not alive(units[target_id]) and (target_id in state['kills'][side]
                                  or observed.get('observed_second') == boundary_step * 5))
            elif action == 'camp':
                from backend.fivepoint_parameters import UNIT_PROFILES, CARGO_CAPACITY
                targets = order.get('balances')
                if targets is None:
                    targets = dict(energy=100, ammo=UNIT_PROFILES[unit['unit_type']]['ammo'], hp=UNIT_PROFILES[unit['unit_type']]['hp'])
                    if unit['unit_type'] == 'support':
                        targets.update({'cargo_' + category: capacity for category, capacity in CARGO_CAPACITY.items()})
                completed = all(number(unit['cargo'][category[6:]] if category.startswith('cargo_') else unit[category]) >= number(target)
                                for category, target in targets.items())
            if completed and 'active_branch' in task:
                task['branch_step'] += 1
                if task['branch_step'] == len(task['branches'][task['active_branch']]['orders']):
                    task.pop('active_branch')
                    task.pop('branch_step')
                completed = False
            if completed:
                task.update(status='completed', completed_step=boundary_step, reason='rule_condition_met')
            elif boundary_step * 5 >= task.get('deadline_second', 3601):
                task.update(status='failed', reason='deadline_reached')
    for tasks in state.get('tasks', {}).values():
        for task in tasks:
            if task['status'] not in TERMINAL and boundary_step * 5 >= task.get('deadline_second', 3601):
                task.update(status='failed', reason='deadline_reached')
