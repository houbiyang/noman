"""虚构五点游戏的原子五秒执行器；采用冻结参数、精确资源账本和显式地图适配器。"""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json

from backend.fivepoint_parameters import (
    VERSION, TERRAIN_VERSION, SIDES, UNIT_PROFILES, PUBLIC_RESOURCES, CARGO_CAPACITY,
    STEP_SECONDS, DURATION_SECONDS, PLAN_SECONDS, require, number, roster_baseline, score,
)


def encode(value):
    """递归生成可持久化的精确 JSON；分数用分子／分母字符串保存。"""
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encode(item) for item in value]
    return value


def digest(value) -> str:
    """计算冻结配置摘要，不包含可变库存、计划或展示舍入值。"""
    return hashlib.sha256(json.dumps(encode(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def alive(unit: dict) -> bool:
    """能源为零仍存活；只有永久退场状态才失去全部资格。"""
    return unit["retired"] is None


def initialize(rosters: dict, points: list, camps: list, map_hash: str, geometry, capacity_per_side: int) -> dict:
    """验证后创建独立状态；数量、位置、点位和发布容量必须由调用方明确提供。"""
    require(set(rosters) == set(SIDES), "ROSTER_INVALID", "必须提供双方名册")
    require(type(capacity_per_side) is int and capacity_per_side > 0, "CAPACITY_INVALID", "须声明已验证发布容量")
    require(len(points) == 5 and len({point["point_id"] for point in points}) == 5,
            "POINTS_INVALID", "必须恰有五个不同的目标")
    require(len(camps) == 6 and len({camp["camp_id"] for camp in camps}) == 6,
            "CAMPS_INVALID", "双方各须三座不同的兵营")
    for side in SIDES:
        require({camp["unit_type"] for camp in camps if camp["side"] == side} == set(UNIT_PROFILES),
                "CAMPS_INVALID", "每方须包含三类兵营")
    geometry.verify(map_hash)
    units, baselines = [], {}
    for side in SIDES:
        require(len(rosters[side]) <= capacity_per_side, "CAPACITY_EXCEEDED", "名册超过发布包容量")
        counts = {kind: 0 for kind in UNIT_PROFILES}
        for source in rosters[side]:
            require(source["unit_type"] in counts, "UNIT_TYPE_INVALID", "名册兵种无效")
            kind = source["unit_type"]
            counts[kind] += 1
            profile = UNIT_PROFILES[kind]
            units.append(dict(unit_id=source["unit_id"], side=side, unit_type=kind,
                position=deepcopy(source["position"]), hp=str(profile["hp"]), energy="100", ammo=profile["ammo"],
                cargo={key: str(value if kind == "support" else 0) for key, value in CARGO_CAPACITY.items()},
                retired=None, flight="docked" if kind == "recon" else "ground", next_fire=0,
                last_fire=None, last_damage=None, queue_since=None, queue_camp=None, route_remainder="0", target_cursor=0))
        baselines[side] = roster_baseline(counts)
    require(len({unit["unit_id"] for unit in units}) == len(units)
            and all(isinstance(unit["unit_id"], str) and unit["unit_id"] for unit in units),
            "UNIT_ID_INVALID", "单位标识必须非空且全局唯一")
    frozen = dict(rule_version=VERSION, terrain_version=TERRAIN_VERSION, map_hash=map_hash,
                  capacity_per_side=capacity_per_side, rosters=deepcopy(rosters), baselines=baselines,
                  points=deepcopy(points), camps=deepcopy(camps))
    state = dict(schema_version="fivepoint-state-v1", frozen=frozen, frozen_hash=digest(frozen),
                 step=0, phase="planning", units=units,
                 points=[dict(**point, owner=None, candidate=None, progress=0) for point in points],
                 ledger={side: dict(public=deepcopy(PUBLIC_RESOURCES), consumed=dict.fromkeys(PUBLIC_RESOURCES, "0"),
                                    lost=dict.fromkeys(PUBLIC_RESOURCES, "0")) for side in SIDES},
                 control=dict.fromkeys(SIDES, 0), kills={side: [] for side in SIDES},
                 plans={side: {} for side in SIDES}, pending_plans={},
                 tasks={side: [] for side in SIDES}, pending_tasks={},
                 visible={side: [] for side in SIDES}, history={side: {} for side in SIDES},
                 service_cursor=dict.fromkeys(SIDES, 0), events=[], result=None, surrender=[],
                 processed_steps=[], recovery_round=0)
    update_observation(state, geometry, {unit['unit_id'] for unit in units}, {}, observation_second=0)
    validate(state)
    return state


def balances(state: dict, side: str) -> dict:
    """合计公共储备与存活单位账户；退场资源只能出现在损失账中。"""
    result = {key: number(value) for key, value in state["ledger"][side]["public"].items()}
    for unit in state["units"]:
        if unit["side"] != side or not alive(unit):
            continue
        result["energy"] += number(unit["energy"]) + number(unit["cargo"]["energy"])
        result["ammo"] += unit["ammo"] + number(unit["cargo"]["ammo"])
        result["material"] += number(unit["cargo"]["material"])
    return result


def validate(state: dict) -> None:
    """提交前检查冻结身份、容量、守恒、死亡、点步和击毁唯一性。"""
    frozen = state["frozen"]
    require(state["schema_version"] == "fivepoint-state-v1" and frozen["rule_version"] == VERSION
            and frozen["terrain_version"] == TERRAIN_VERSION and digest(frozen) == state["frozen_hash"],
            "VERSION_MISMATCH", "规则版本或冻结配置不一致")
    require(type(state["step"]) is int and 0 <= state["step"] <= 720, "STEP_INVALID", "仿真步越界")
    identities = {unit["unit_id"] for unit in state["units"]}
    originals = {unit["unit_id"]: (side, unit["unit_type"]) for side in SIDES for unit in frozen["rosters"][side]}
    require(len(identities) == len(state["units"]) and identities == set(originals), "ROSTER_CHANGED", "固定名册发生变化")
    for unit in state["units"]:
        require(originals[unit["unit_id"]] == (unit["side"], unit["unit_type"]), "ROSTER_CHANGED", "兵种或阵营变化")
        profile = UNIT_PROFILES[unit["unit_type"]]
        require(0 <= number(unit["hp"]) <= profile["hp"] and 0 <= number(unit["energy"]) <= 100
                and type(unit["ammo"]) is int and 0 <= unit["ammo"] <= profile["ammo"],
                "ACCOUNT_INVALID", "自身账户超过容量或为负")
        for key, capacity in CARGO_CAPACITY.items():
            value = number(unit["cargo"][key])
            require(0 <= value <= (capacity if unit["unit_type"] == "support" else 0), "ACCOUNT_INVALID", "货物账户无效")
            require(key != "ammo" or value.denominator == 1, "ACCOUNT_INVALID", "弹药须为整数")
        if not alive(unit):
            require(number(unit["hp"]) == 0 and number(unit["energy"]) == 0 and unit["ammo"] == 0
                    and all(number(value) == 0 for value in unit["cargo"].values()),
                    "DEATH_ACCOUNT_INVALID", "死亡账户未结清")
        else:
            require(number(unit["hp"]) > 0, "DEATH_INVALID", "零耐久不能保持存活")
    require(len(state["points"]) == 5 and {point["point_id"] for point in state["points"]}
            == {point["point_id"] for point in frozen["points"]}, "POINTS_INVALID", "目标列表改变")
    require(sum(state["control"].values()) <= 5 * state["step"], "CONTROL_INVALID", "控制量超过实际时间容量")
    for point in state["points"]:
        original = next(item for item in frozen["points"] if item["point_id"] == point["point_id"])
        require(all(point.get(key) == value for key, value in original.items()), "POINTS_CHANGED", "冻结点位几何发生变化")
        require(point["owner"] in (None, *SIDES) and point["candidate"] in (None, *SIDES)
                and type(point["progress"]) is int and point["progress"] in range(0, 30, 5),
                "CAPTURE_INVALID", "占领归属或阶段进度无效")
    for side in SIDES:
        ledger = state["ledger"][side]
        current = balances(state, side)
        for category in PUBLIC_RESOURCES:
            require(0 <= number(ledger["public"][category]) <= PUBLIC_RESOURCES[category],
                    "ACCOUNT_INVALID", "公共储备无效")
            consumed, lost = number(ledger["consumed"][category]), number(ledger["lost"][category])
            require(consumed >= 0 and lost >= 0 and current[category] + consumed + lost
                    == frozen["baselines"][side][category], "RESOURCE_CONSERVATION", "资源账本不守恒")
            if category == "ammo":
                require(all(number(value).denominator == 1 for value in (ledger["public"][category], consumed, lost)),
                        "ACCOUNT_INVALID", "弹药账本不能包含小数")
        require(len(state["kills"][side]) == len(set(state["kills"][side])), "DUPLICATE_KILL", "重复击毁记录")
        require(all(identity in originals and originals[identity][0] != side
                    and next(unit for unit in state["units"] if unit["unit_id"] == identity)["retired"] == "destroyed"
                    for identity in state["kills"][side]), "KILL_INVALID", "击毁记录不合法")
        require(type(state["control"][side]) is int and state["control"][side] >= 0, "CONTROL_INVALID", "控制量无效")


def evaluate(state: dict) -> dict:
    """返回精确的三项得分；终局前仅供服务端及本方投影使用。"""
    units = {unit["unit_id"]: unit for unit in state["units"]}
    result = {}
    for side in SIDES:
        enemy = "blue" if side == "red" else "red"
        value = sum(UNIT_PROFILES[units[identity]["unit_type"]]["value"] for identity in state["kills"][side])
        result[side] = score(state["control"][side], value, state["frozen"]["baselines"][enemy]["value"],
                             balances(state, side), state["frozen"]["baselines"][side])
    return result


def spend(state: dict, unit: dict, category: str, amount) -> None:
    """记录实际消耗，保证扣账与消耗分类同量发生。"""
    amount = number(amount)
    require(amount >= 0 and number(unit[category]) >= amount, "RESOURCE_INSUFFICIENT", "资源不足")
    remaining = number(unit[category]) - amount
    unit[category] = int(remaining) if category == "ammo" else str(remaining)
    ledger = state["ledger"][unit["side"]]["consumed"]
    ledger[category] = str(number(ledger[category]) + amount)


def retire(state: dict, unit: dict, reason: str) -> None:
    """一次永久退场；余额归入损失，先前消耗不重复记账。"""
    if not alive(unit):
        return
    lost = state["ledger"][unit["side"]]["lost"]
    for category in PUBLIC_RESOURCES:
        amount = number(unit["cargo"][category])
        if category in ("energy", "ammo"):
            amount += number(unit[category])
        lost[category] = str(number(lost[category]) + amount)
        unit["cargo"][category] = "0"
    unit.update(hp="0", energy="0", ammo=0, retired=reason, queue_since=None, queue_camp=None)
    state["plans"][unit["side"]].pop(unit["unit_id"], None)
    state["events"].append(dict(kind=reason, unit_id=unit["unit_id"], second=(state["step"] + 1) * 5))


def check_orders(state: dict, side: str, orders: dict) -> None:
    """验证一轮直接动作及服务互斥；客户端不能提交资源、分数或敌方单位动作。"""
    require(isinstance(orders, dict) and len(orders) <= 1000, "ORDERS_INVALID", "动作须为有界对象")
    units = {unit["unit_id"]: unit for unit in state["units"]}
    providers, receivers = set(), set()
    for identity, order in orders.items():
        require(identity in units and units[identity]["side"] == side and alive(units[identity]),
                "ORDER_UNIT_INVALID", "不能指挥该单位")
        require(isinstance(order, dict) and set(order) <= {"action", "destination", "target_id", "target_area", "category", "amount", "balance", "camp_id", "balances"},
                "ORDER_INVALID", "动作包含未知字段")
        action, unit = order.get("action"), units[identity]
        require(action in ("wait", "move", "attack", "move_attack", "takeoff", "land", "service", "camp"),
                "ACTION_INVALID", "动作不受支持")
        if action in ("move", "move_attack"):
            check_position(order.get("destination"))
        if action in ("attack", "move_attack"):
            require(unit["unit_type"] == "ground" and ("target_id" in order) != ("target_area" in order),
                    "ATTACK_INVALID", "攻击须选择单个目标或授权范围")
            if "target_area" in order:
                area = order["target_area"]
                require(isinstance(area, dict) and set(area) == {"center", "radius"}, "ATTACK_INVALID", "范围须指定中心及半径")
                check_position(area["center"])
                require(0 < number(area["radius"]) <= 12000, "ATTACK_INVALID", "授权半径无效")
            else:
                # 只依据本方已有观察或击毁回执验证，未知标识不能成为探测隐藏名册的接口。
                target_id = order.get("target_id")
                require(isinstance(target_id, str) and (target_id in state["history"][side] or target_id in state["kills"][side]),
                        "ATTACK_INVALID", "目标不在本方已知记录中")
        if action in ("takeoff", "land"):
            require(unit["unit_type"] == "recon", "FLIGHT_INVALID", "起降仅适用于无人机")
        if action == "service":
            target = units.get(order.get("target_id"))
            require(unit["unit_type"] == "support" and target is not None and target["side"] == side
                    and target["unit_id"] != identity and alive(target), "SERVICE_INVALID", "服务对象无效")
            category = order.get("category")
            require(category in ("energy", "ammo", "hp") and (category != "ammo" or target["unit_type"] == "ground"),
                    "SERVICE_INVALID", "资源类别不适用")
            require(("amount" in order) != ("balance" in order), "SERVICE_INVALID", "数量与目标余额须二选一")
            amount = number(order.get("amount", order.get("balance")))
            capacity = 100 if category == "energy" else UNIT_PROFILES[target["unit_type"]][category]
            require(amount > 0 and ("balance" not in order or amount <= capacity)
                    and (category != "ammo" or amount.denominator == 1), "SERVICE_INVALID", "服务请求量无效")
            require(target["unit_id"] not in receivers, "SERVICE_CONFLICT", "接收者只能有一个来源")
            providers.add(identity)
            receivers.add(target["unit_id"])
        if action == "camp":
            require(any(camp["camp_id"] == order.get("camp_id") and camp["side"] == side
                        for camp in state["frozen"]["camps"]), "CAMP_INVALID", "兵营不属于本方")
            require(identity not in receivers, "SERVICE_CONFLICT", "接收者不能同时接受兵营和野外服务")
            receivers.add(identity)
            targets = order.get("balances")
            if targets is not None:
                capacities = dict(energy=100, ammo=UNIT_PROFILES[unit["unit_type"]]["ammo"], hp=UNIT_PROFILES[unit["unit_type"]]["hp"])
                if unit["unit_type"] == "support":
                    capacities.update({"cargo_" + key: value for key, value in CARGO_CAPACITY.items()})
                require(isinstance(targets, dict) and bool(targets) and set(targets) <= set(capacities),
                        "CAMP_TARGET_INVALID", "补充类别不适用")
                for category, target in targets.items():
                    amount = number(target)
                    require(0 <= amount <= capacities[category] and ("ammo" not in category or amount.denominator == 1),
                            "CAMP_TARGET_INVALID", "兵营目标余额无效")
    require(not providers.intersection(receivers), "SERVICE_CONFLICT", "同一步不能同时提供和接收服务")
    for identity in receivers:
        require(orders.get(identity, {"action": "wait"})["action"] in ("wait", "camp"),
                "SERVICE_CONFLICT", "接收服务不能同时移动、攻击或起降")


def check_position(position: dict) -> None:
    """检查规则坐标结构和有限数值，真实支撑面资格仍交给地图执行器。"""
    require(isinstance(position, dict) and set(position) == {"xy", "layer"}
            and isinstance(position["layer"], str) and 0 < len(position["layer"]) <= 80
            and isinstance(position["xy"], (list, tuple)) and len(position["xy"]) == 2,
            "POSITION_INVALID", "必须提供二维米制坐标及地表层")
    for value in position['xy']:
        number(value)


def submit_plan(state: dict, side: str, orders: dict, expected_step: int, tasks=None, decision="adopt", cancel_task_ids=()) -> dict:
    """双方方案齐备才共同采用；快者不能提前行动，旧时刻方案直接拒绝。"""
    require(side in SIDES and state["phase"] == "planning" and state["step"] == expected_step
            and expected_step % 12 == 0, "PLAN_BOUNDARY_INVALID", "不在共同规划边界")
    require(side not in state["pending_plans"], "PLAN_ALREADY_SUBMITTED", "本方本轮已经处理")
    require(decision in ("adopt", "reject"), "PLAN_DECISION_INVALID", "方案处理方式无效")
    require(decision != "reject" or not orders and tasks is None,
            "PLAN_FORMAT_CONFLICT", "驳回方案不能同时提供新动作或任务")
    require(isinstance(cancel_task_ids, (list, tuple)) and all(isinstance(identity, str) for identity in cancel_task_ids)
            and len(cancel_task_ids) == len(set(cancel_task_ids)) and (not cancel_task_ids or decision == "reject"),
            "TASK_CANCEL_INVALID", "取消须在保留旧方案时明确指定不同的本方任务")
    check_orders(state, side, orders)
    normalized_tasks = deepcopy(state.get('tasks', {}).get(side, [])) if decision == "reject" else []
    cancellable = {task['task_id'] for task in normalized_tasks if task['status'] not in ('completed', 'failed', 'cancelled')}
    require(set(cancel_task_ids) <= cancellable, "TASK_CANCEL_INVALID", "任务不存在或已经结束")
    for task in normalized_tasks:
        if task['task_id'] in cancel_task_ids:
            task.update(status='cancelled', reason='operator_cancelled')
    if tasks is not None:
        from backend.fivepoint_tasks import validate_tasks
        require(not orders, 'PLAN_FORMAT_CONFLICT', '直接动作和任务图不能同时提交')
        normalized_tasks = validate_tasks(state, side, tasks)
    result = deepcopy(state)
    result["pending_plans"][side] = deepcopy(state["plans"][side] if decision == "reject" else orders)
    result['pending_tasks'][side] = normalized_tasks
    if set(result["pending_plans"]) == set(SIDES):
        result["plans"] = result["pending_plans"]
        result["pending_plans"] = {}
        result['tasks'] = result['pending_tasks']
        result['pending_tasks'] = {}
        result["phase"] = "paused"
        result["recovery_round"] = 0
    return result


def fail_planning(state: dict, side: str, reason: str) -> dict:
    """任一方失败时丢弃双方待采用方案，并保留最后完整步骤和旧任务。"""
    require(side in SIDES and state["phase"] == "planning", "PHASE_INVALID", "没有待处理共同规划")
    result = deepcopy(state)
    result.update(phase="technical_pause", pending_plans={}, pending_tasks={})
    result["events"].append(dict(kind="planning_failed", side=side, code=reason, second=state["step"] * 5))
    return result


def retry_planning(state: dict) -> dict:
    """最多两轮技术恢复，不推进仿真时钟或提前采用快方结果。"""
    require(state["phase"] == "technical_pause" and state["recovery_round"] < 2,
            "RECOVERY_LIMIT", "不满足恢复条件或两轮恢复已经用尽")
    result = deepcopy(state)
    result.update(phase="planning", pending_plans={}, pending_tasks={}, recovery_round=state["recovery_round"] + 1)
    return result


def request_surrender(state: dict, side: str) -> dict:
    """登记本方投降，在下个未开始边界结算；已经终局的结果不可覆盖。"""
    require(side in SIDES and state["result"] is None, "SURRENDER_INVALID", "席位无效或对局已结束")
    result = deepcopy(state)
    if side not in result["surrender"]:
        result["surrender"].append(side)
    return result


def technical_abort(state: dict) -> dict:
    """技术终止保留统计证据但不产生竞技胜者。"""
    require(state["phase"] == "technical_pause", "PHASE_INVALID", "只有技术暂停局可技术终止")
    result = deepcopy(state)
    result.update(phase="finished", result=encode(dict(winner=None, reason="technical_abort", scores=evaluate(state), second=state["step"] * 5)))
    return result


def update_points(state: dict, before: dict, snapshot: dict, moved: set) -> None:
    """按伤害前快照压制收入，按伤害后完整驻留推进阶段。"""
    units = {unit["unit_id"]: unit for unit in state["units"]}
    for point in state["points"]:
        present = snapshot[point["point_id"]]
        old_owner = point["owner"]
        point['contested'] = bool(present['red'] and present['blue'] or old_owner is not None and present['blue' if old_owner == 'red' else 'red'])
        candidates = [side for side in SIDES if present[side] and not present["blue" if side == "red" else "red"]]
        side = candidates[0] if candidates else None
        resident = side is not None and any(identity not in moved and alive(units[identity])
            and identity in before[point["point_id"]][side] for identity in present[side])
        if resident and side != old_owner:
            point["progress"] = point["progress"] + 5 if point["candidate"] == side else 5
            point["candidate"] = side
            if point["progress"] == 30:
                point.update(owner=None if old_owner else side, candidate=None, progress=0)
        else:
            point.update(candidate=None, progress=0)
        if old_owner and point["owner"] == old_owner and not present["blue" if old_owner == "red" else "red"]:
            state["control"][old_owner] += 1


def point_snapshot(state: dict, geometry, initially_active: set) -> dict:
    """只登记正能源地面单位，补给后恢复能源不追溯本步资格。"""
    return {point["point_id"]: {side: [unit["unit_id"] for unit in state["units"]
        if unit["side"] == side and unit["unit_id"] in initially_active and alive(unit)
        and unit["unit_type"] != "recon" and number(unit["energy"]) > 0 and geometry.in_point(unit, point)]
        for side in SIDES} for point in state["points"]}


def update_observation(state: dict, geometry, initially_active: set, transitioning: dict, observation_second=None) -> None:
    """仅保存本方当前所见及最后观察副本；隐藏目标移动不更新历史位置。"""
    for side in SIDES:
        observers = [unit for unit in state["units"] if unit["side"] == side and alive(unit)
                     and unit["unit_id"] in initially_active and number(unit["energy"]) > 0
                     and unit["unit_id"] not in transitioning
                     and (unit["unit_type"] != "recon" or unit["flight"] == "airborne")]
        seen = []
        for target in state["units"]:
            if target["side"] != side and alive(target) and any(geometry.can_observe(observer, target) for observer in observers):
                seen.append(target["unit_id"])
                state["history"][side][target["unit_id"]] = dict(unit_id=target["unit_id"],
                    unit_type=target["unit_type"], position=deepcopy(target["position"]), hp=target["hp"],
                    observed_second=(state["step"] + 1) * 5 if observation_second is None else observation_second)
        state["visible"][side] = seen


def select_target(state: dict, unit: dict, order: dict, geometry):
    """范围选敌仅取当前合法目标；等距等耐久时使用射手持久游标。"""
    from backend.fivepoint_geometry import distance
    enemies = sorted((target for target in state['units'] if target['side'] != unit['side']), key=lambda target: target['unit_id'])
    cursor = unit.get('target_cursor', 0) % len(enemies)
    candidates = []
    for index, target in enumerate(enemies):
        if not alive(target) or target['unit_id'] not in state['visible'][unit['side']]:
            continue
        if 'target_area' in order:
            area = order['target_area']
            if (target['position']['layer'] != area['center']['layer']
                    or distance(target['position']['xy'], area['center']['xy']) > number(area['radius'])):
                continue
        elif target['unit_id'] != order.get('target_id'):
            continue
        if geometry.can_fire(unit, target):
            candidates.append((distance(unit['position']['xy'], target['position']['xy']), number(target['hp']),
                               (index - cursor) % len(enemies), index, target))
    if not candidates:
        return None, None
    chosen = min(candidates, key=lambda item: item[:3])
    return chosen[4], (chosen[3] + 1) % len(enemies)


def attacks(state: dict, geometry, orders: dict, moved: set, costs: dict, initially_active: set, receivers: set) -> None:
    """统一冻结合法攻击并扣弹，然后同步伤害，允许双方互毁。"""
    units = {unit["unit_id"]: unit for unit in state["units"]}
    damage = {}
    boundary = (state["step"] + 1) * 5
    for identity in sorted(orders):
        order, unit = orders[identity], units[identity]
        if (order["action"] not in ("attack", "move_attack") or not alive(unit)
                or identity not in initially_active or identity in receivers or unit["ammo"] < 1
                or unit["next_fire"] > boundary):
            continue
        extra = costs.get(identity, Fraction(0)) / 4 if identity in moved else Fraction(0)
        if number(unit["energy"]) <= extra:
            continue
        target, next_cursor = select_target(state, unit, order, geometry)
        if target is None:
            continue
        spend(state, unit, "energy", extra)
        spend(state, unit, "ammo", 1)
        unit.update(next_fire=boundary + 10, last_fire=boundary)
        if 'target_area' in order:
            unit['target_cursor'] = next_cursor
        damage[target["unit_id"]] = damage.get(target["unit_id"], Fraction(0)) + (Fraction(48, 5) if identity in moved else 16)
        state["events"].append(dict(kind="shot", unit_id=identity, target_id=target["unit_id"], second=boundary))
    for identity, amount in damage.items():
        unit = units[identity]
        unit["hp"] = str(max(Fraction(0), number(unit["hp"]) - amount))
        unit["last_damage"] = boundary
    for identity in damage:
        unit = units[identity]
        if number(unit["hp"]) == 0:
            enemy = "blue" if unit["side"] == "red" else "red"
            state["kills"][enemy].append(identity)
            retire(state, unit, "destroyed")


def transfer(state: dict, source: dict, target: dict, category: str, limit, request) -> Fraction:
    """野外服务按真实缺口部分交付，维修材料记消耗，普通转账守恒。"""
    cargo_kind = "material" if category == "hp" else category
    capacity = 100 if category == "energy" else UNIT_PROFILES[target["unit_type"]][category]
    amount = min(number(limit), number(source["cargo"][cargo_kind]), capacity - number(target[category]), number(request))
    source["cargo"][cargo_kind] = str(number(source["cargo"][cargo_kind]) - amount)
    target[category] = int(number(target[category]) + amount) if category == "ammo" else str(number(target[category]) + amount)
    if category == "hp":
        consumed = state["ledger"][source["side"]]["consumed"]
        consumed["material"] = str(number(consumed["material"]) + amount)
    return amount


def camp_targets(unit: dict, order: dict) -> dict:
    """得到本次明确要求的补给余额，满额单位不占队列资历。"""
    if order.get('balances') is not None:
        return order['balances']
    targets = dict(energy=100, ammo=UNIT_PROFILES[unit['unit_type']]['ammo'], hp=UNIT_PROFILES[unit['unit_type']]['hp'])
    if unit['unit_type'] == 'support':
        targets.update({'cargo_' + category: capacity for category, capacity in CARGO_CAPACITY.items()})
    return targets


def services(state: dict, geometry, orders: dict, moved: set, initially_active: set, transitioning: dict) -> None:
    """先处理已锁定野外请求，再按全方公共队列核销兵营库存。"""
    units = {unit["unit_id"]: unit for unit in state["units"]}
    boundary = (state["step"] + 1) * 5
    def stopped(unit):
        return alive(unit) and unit["unit_id"] not in moved and unit["unit_id"] not in transitioning and (
            unit["unit_type"] != "recon" or unit["flight"] == "docked")
    for identity in sorted(orders):
        order, source = orders[identity], units[identity]
        if order["action"] != "service":
            continue
        target = units[order["target_id"]]
        if not (stopped(source) and stopped(target) and identity in initially_active
                and number(source["energy"]) > 0 and geometry.can_service(source, target)):
            continue
        category = order["category"]
        request = number(order["amount"]) if "amount" in order else max(Fraction(0), number(order["balance"]) - number(target[category]))
        delivered = transfer(state, source, target, category, 2, request)
        if "amount" in order:
            order["amount"] = str(request - delivered)
        maximum = 100 if category == "energy" else UNIT_PROFILES[target["unit_type"]][category]
        if ("amount" in order and (number(order["amount"]) == 0 or number(target[category]) >= maximum)) or ("balance" in order and number(target[category]) >= number(order["balance"])):
            state["plans"][source["side"]][identity] = dict(action="wait")
    camps = {camp["camp_id"]: camp for camp in state["frozen"]["camps"]}
    for unit in state["units"]:
        order = orders.get(unit["unit_id"], {})
        camp = camps.get(order.get("camp_id")) if order.get("action") == "camp" else None
        targets = camp_targets(unit, order) if camp is not None else {}
        needs_service = any(number(unit['cargo'][category[6:]] if category.startswith('cargo_') else unit[category]) < number(value)
                            for category, value in targets.items())
        if camp is None or not needs_service or not stopped(unit) or not geometry.can_service(camp, unit):
            unit.update(queue_since=None, queue_camp=None)
        elif unit["queue_camp"] != camp["camp_id"]:
            unit.update(queue_since=state["step"] * 5, queue_camp=camp["camp_id"])
    categories = (("energy", "energy", 100, 50), ("ammo", "ammo", None, 50), ("hp", "material", None, 50),
                  ("cargo_energy", "energy", 200, 50), ("cargo_ammo", "ammo", 120, 60), ("cargo_material", "material", 100, 50))
    for side in SIDES:
        identities = sorted(unit["unit_id"] for unit in state["units"] if unit["side"] == side)
        cursor = state["service_cursor"][side] % len(identities)
        order_index = {identity: (index - cursor) % len(identities) for index, identity in enumerate(identities)}
        queue = sorted([unit for unit in state["units"] if unit["side"] == side and unit["queue_camp"] is not None],
                       key=lambda unit: (unit["queue_since"], order_index[unit["unit_id"]]))
        slots = dict.fromkeys(camps, 0)
        for unit in queue:
            if slots[unit["queue_camp"]] >= 4 or any(stamp is not None and boundary - stamp < 15 for stamp in (unit["last_fire"], unit["last_damage"])):
                continue
            delivered_total = Fraction(0)
            public = state["ledger"][side]["public"]
            for account, category, capacity, limit in categories:
                cargo = account.startswith("cargo_")
                if cargo and unit["unit_type"] != "support":
                    continue
                key = account[6:] if cargo else account
                holder = unit["cargo"] if cargo else unit
                maximum = capacity if capacity is not None else UNIT_PROFILES[unit["unit_type"]][account]
                targets = orders[unit["unit_id"]].get("balances")
                if targets is not None:
                    if account not in targets:
                        continue
                    maximum = number(targets[account])
                amount = min(number(public[category]), max(Fraction(0), maximum - number(holder[key])), Fraction(limit))
                public[category] = str(number(public[category]) - amount)
                holder[key] = int(number(holder[key]) + amount) if account == "ammo" else str(number(holder[key]) + amount)
                delivered_total += amount
                if account == "hp":
                    consumed = state["ledger"][side]["consumed"]
                    consumed["material"] = str(number(consumed["material"]) + amount)
            if delivered_total:
                slots[unit["queue_camp"]] += 1
                state["service_cursor"][side] = (identities.index(unit["unit_id"]) + 1) % len(identities)


def finish(state: dict) -> None:
    """完整结算后按双方全灭、单方全灭和时限顺序判定，比较不使用显示舍入。"""
    survivors = {side: sum(alive(unit) and unit["side"] == side for unit in state["units"]) for side in SIDES}
    if all(survivors.values()) and state["step"] < 720:
        return
    scores = evaluate(state)
    if bool(survivors["red"]) != bool(survivors["blue"]):
        winner = "red" if survivors["red"] else "blue"
        reason = "elimination"
    else:
        comparison = {side: (scores[side]["total"], state["control"][side]) for side in SIDES}
        winner = None if comparison["red"] == comparison["blue"] else max(SIDES, key=lambda side: comparison[side])
        reason = "mutual_elimination" if not any(survivors.values()) else "time_limit"
    state.update(phase="finished", result=encode(dict(winner=winner, reason=reason, scores=scores, second=state["step"] * 5)))


def advance(state: dict, geometry, expected_step: int, request_id: str) -> dict:
    """在副本上完成一个完整步骤，失败保留旧状态；回执重发不再次计账。"""
    validate(state)
    receipt = next((item for item in state["processed_steps"] if item["request_id"] == request_id), None)
    if receipt:
        require(receipt["expected_step"] == expected_step, "REQUEST_REUSED", "同一请求键载荷不一致")
        return deepcopy(state)
    require(isinstance(request_id, str) and bool(request_id) and state["step"] == expected_step,
            "STEP_CONFLICT", "请求时刻已过期或请求键缺失")
    require((state["phase"] in ("paused", "running") or (state["surrender"] and state["result"] is None))
            and state["step"] < 720, "PHASE_INVALID", "尚未共同采用方案或对局已结束")
    geometry.verify(state["frozen"]["map_hash"])
    result = deepcopy(state)
    if result["surrender"]:
        losers = set(result["surrender"])
        winner = None if len(losers) == 2 else next(side for side in SIDES if side not in losers)
        result.update(phase="finished", result=encode(dict(winner=winner, reason="surrender", scores=evaluate(result), second=result["step"] * 5)))
        result["processed_steps"].append(dict(request_id=request_id, expected_step=expected_step))
        return result
    from backend import fivepoint_tasks
    active_tasks = fivepoint_tasks.prepare(result, geometry)
    service_returns = {task['unit_id'] for tasks in result.get('tasks', {}).values() for task in tasks
                       if task['status'] == 'running' and 'active_branch' in task}
    orders = {identity: order for plan in result["plans"].values() for identity, order in plan.items()}
    initially_active = {unit["unit_id"] for unit in result["units"] if alive(unit) and number(unit["energy"]) > 0}
    before = point_snapshot(result, geometry, initially_active)
    receivers = {order["target_id"] for order in orders.values() if order["action"] == "service"}
    receivers.update(identity for identity, order in orders.items() if order["action"] == "camp")
    moved, costs, transitioning = set(), {}, {}
    movement_basis = deepcopy(result["units"])
    private_views = {side: project(result, side) for side in SIDES}
    for unit in result["units"]:
        identity = unit["unit_id"]
        if not alive(unit):
            continue
        order = orders.get(identity, dict(action="wait"))
        action = order["action"]
        if unit["unit_type"] == "recon":
            transition = action == "takeoff" and unit["flight"] == "docked" or action == "land" and unit["flight"] == "airborne"
            if transition and number(unit["energy"]) > Fraction(7, 6) and geometry.can_dock(unit, result["units"]):
                transitioning[identity] = "airborne" if action == "takeoff" else "docked"
                spend(result, unit, "energy", Fraction(7, 6))
                unit["flight"] = "taking_off" if action == "takeoff" else "landing"
            elif unit["flight"] == "airborne":
                spend(result, unit, "energy", min(number(unit["energy"]), Fraction(1, 6)))
                if number(unit["energy"]) == 0:
                    retire(result, unit, "power_failure")
        can_move = (identity in initially_active and alive(unit) and identity not in receivers
                    and identity not in transitioning and number(unit["energy"]) > 0
                    and (unit["unit_type"] != "recon" or unit["flight"] == "airborne")
                    and (action != "move_attack" or unit["ammo"] > 0))
        if action in ("move", "move_attack") and can_move:
            reserve = Fraction(5, 4) if action == "move_attack" else Fraction(1)
            service_cost = geometry.service_energy_cost(deepcopy(unit), deepcopy(private_views[unit['side']]))
            trigger = None if service_cost is None else max(Fraction(30), number(service_cost) + 10)
            require(service_cost is None or number(service_cost) >= 0, 'SERVICE_ESTIMATE_INVALID', '服务成本不能为负')
            budget = Fraction(0) if trigger is None else max(Fraction(0), number(unit['energy']) - trigger)
            returning = identity in service_returns and action == 'move' and service_cost is not None
            if returning:
                budget = max(Fraction(0), number(unit['energy']) - 10)
            unit['energy_guard'] = dict(reason='service_cost_unknown' if trigger is None else 'low_energy' if not budget else 'ready',
                                        trigger=encode(trigger), second=(result['step'] + 1) * 5)
            if not budget:
                unit['route_remainder'] = '0'
                continue
            movement = geometry.move(deepcopy(unit), order["destination"], 5, budget, reserve, movement_basis)
            cost = number(movement["energy"])
            require(0 <= cost * reserve <= budget, "MOVEMENT_BUDGET", "路线执行超过预留能源")
            route_service_cost = geometry.movement_service_energy_cost(deepcopy(unit), deepcopy(movement), deepcopy(private_views[unit['side']]))
            require(route_service_cost is None or number(route_service_cost) >= 0, 'SERVICE_ESTIMATE_INVALID', '路线服务成本不能为负')
            endpoint = deepcopy(unit)
            endpoint['position'] = deepcopy(movement['position'])
            endpoint_cost = geometry.service_energy_cost(endpoint, deepcopy(private_views[unit['side']])) if returning else None
            safe_return = (returning and endpoint_cost is not None and number(endpoint_cost) >= 0
                           and number(endpoint_cost) <= number(service_cost)
                           and number(unit['energy']) - cost >= number(endpoint_cost) + 10
                           and route_service_cost is not None and number(route_service_cost) <= number(service_cost))
            if not safe_return and (route_service_cost is None or number(unit['energy']) - cost * reserve < max(Fraction(30), number(route_service_cost) + 10)):
                unit['energy_guard']['reason'] = 'route_service_unknown' if route_service_cost is None else 'route_exceeds_reserve'
                unit['route_remainder'] = '0'
                continue
            if movement["moved"]:
                moved.add(identity)
                unit["position"] = deepcopy(movement["position"])
            unit["route_remainder"] = str(movement.get("remainder", 0))
            costs[identity] = cost
            spend(result, unit, "energy", cost)
            if unit["unit_type"] == "recon" and number(unit["energy"]) == 0:
                retire(result, unit, "power_failure")
        else:
            unit["route_remainder"] = "0"
    snapshot = point_snapshot(result, geometry, initially_active)
    update_observation(result, geometry, initially_active, transitioning)
    attacks(result, geometry, orders, moved, costs, initially_active, receivers)
    # 伤害前已经取得的历史记录保留；唯一观察来源死亡后不再维持当前可见资格。
    for side in SIDES:
        observers = [unit for unit in result['units'] if unit['side'] == side and alive(unit)
                     and unit['unit_id'] in initially_active and unit['unit_id'] not in transitioning
                     and number(unit['energy']) > 0 and (unit['unit_type'] != 'recon' or unit['flight'] == 'airborne')]
        targets = {unit['unit_id']: unit for unit in result['units']}
        result['visible'][side] = [identity for identity in result['visible'][side] if alive(targets[identity])
                                  and any(geometry.can_observe(observer, targets[identity]) for observer in observers)]
    services(result, geometry, orders, moved, initially_active, transitioning)
    # 当前仍合法可见的目标显示本步末耐久；失视记录保留最后观察值。
    for side in SIDES:
        for identity in result['visible'][side]:
            result['history'][side][identity]['hp'] = targets[identity]['hp']
    update_points(result, before, snapshot, moved)
    for unit in result["units"]:
        identity = unit["unit_id"]
        if identity in transitioning and alive(unit):
            destination = transitioning[identity]
            unit["flight"] = destination if destination == "airborne" or geometry.can_dock(unit, result["units"]) else "airborne"
    fivepoint_tasks.finish(result, active_tasks, initially_active, transitioning)
    result["step"] += 1
    result["processed_steps"].append(dict(request_id=request_id, expected_step=expected_step))
    if result["step"] % 12 == 0:
        result["phase"] = "planning"
    finish(result)
    validate(result)
    return encode(result)


def project(state: dict, side: str) -> dict:
    """只投影本方资源及当前可见敌军位置，敌方总分与私有账户到终局才公开。"""
    require(side in SIDES, "ROLE_INVALID", "须提供本方席位")
    scores = evaluate(state)
    own = [deepcopy(unit) for unit in state["units"] if unit["side"] == side]
    own_ids = {unit['unit_id'] for unit in own}
    points = deepcopy(state['points'])
    for point in points:
        if point['candidate'] != side:
            point.pop('candidate', None)
            point.pop('progress', None)
    return encode(dict(step=state["step"], second=state["step"] * 5, phase=state["phase"],
        rule_version=VERSION, units=own, contacts=[deepcopy(state["history"][side][identity]) for identity in state["visible"][side]],
        history=deepcopy(state["history"][side]), points=points,
        baselines=deepcopy(state['frozen']['baselines']),
        camps=[deepcopy(camp) for camp in state['frozen']['camps'] if camp['side'] == side],
        planning_candidate=deepcopy(state.get('planning_candidates', {}).get(side)),
        planning_feedback=deepcopy(state.get('planning_feedback', {}).get(side)),
        own_tasks=deepcopy(state.get('tasks', {}).get(side, [])),
        events=[dict(deepcopy(event), event_index=index) for index, event in enumerate(state['events'])
                if event.get('unit_id') in own_ids or event.get('side') == side],
        public_control={team: scores[team]["control"] for team in SIDES}, own_score=scores[side],
        own_resources=balances(state, side), result=deepcopy(state["result"])))
