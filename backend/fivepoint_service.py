"""五点执行器与既有事务、席位、存档和角色视图的适配，不覆盖历史演示局。"""

from copy import deepcopy
import json

from backend import fivepoint_engine as engine
from backend.fivepoint_parameters import VERSION, UNIT_PROFILES, RuleViolation
from backend.demo_domain import require, host_only
from backend.runtime import ServiceError
from backend.spatial import meters, business


def enabled(state: dict) -> bool:
    """只有显式冻结五点版本的快照走新链路。"""
    return state["manifest"].get("fivepoint_version") == VERSION and "fivepoint" in state


def published_map(state: dict):
    """正式入口只接受已发布地图适配器；当前测试通行网格不能自动升级。"""
    raise ServiceError(409, "FIVEPOINT_MAP_NOT_RELEASED",
                       "五点规则地图尚未发布：须完成地表层、森林、桥头、五点通路与服务隔离验收")


def enable(state: dict, actor: str, control_mode='manual', configurations=None) -> None:
    """主持人在准备阶段显式冻结当前名册与已发布地图，失败不会改变原局。"""
    host_only(actor)
    require(state["lifecycle"]["phase"] == "preparing" and not enabled(state),
            "PREPARATION_REQUIRED", "只能在准备阶段首次启用五点规则")
    require("coordination" not in state, "RULE_MODE_CONFLICT", "已有协同测试规则，须另开五点对局")
    require(all(state['lifecycle']['ready'].values()), 'WAITING_READY', '双方须确认初始配置后才能冻结五点对局')
    binding, geometry = published_map(state)
    rosters = {side: [dict(unit_id=unit["unit_id"], unit_type=unit["unit_type"],
                          position=dict(xy=list(meters(unit["position"])), layer=binding["deployment_layer"]))
                      for unit in state["units"] if unit["side"] == side] for side in ("red", "blue")}
    camps = [dict(camp_id=camp["camp_id"], side=camp["side"], unit_type=camp["unit_type"],
                  position=dict(xy=list(meters(camp["position"])), layer=binding["deployment_layer"])) for camp in state["camps"]]
    try:
        core = engine.initialize(rosters, binding["points"], camps, binding["map_hash"], geometry, binding["capacity_per_side"])
    except RuleViolation as error:
        raise ServiceError(422, error.code, str(error)) from error
    state["fivepoint"] = core
    core['frozen']['control_mode'] = control_mode
    core['frozen']['ai_configurations'] = deepcopy(configurations or {})
    core['frozen_hash'] = engine.digest(core['frozen'])
    state["manifest"]["fivepoint_version"] = VERSION
    state["manifest"].pop("victory_rules", None)
    state.pop("victory", None)
    state["routes"] = []
    synchronize(state)


def synchronize(state: dict) -> None:
    """把核心状态投影到既有快照外壳，旧模型展示值不能回写权威账户。"""
    core = state["fivepoint"]
    phase = {"planning": "awaiting_confirmation", "technical_pause": "paused"}.get(core["phase"], core["phase"])
    state["lifecycle"].update(simulation_step=core["step"], phase=phase, deadline_step=720,
                              step_minutes=1 / 12, step_seconds=5, elapsed_seconds=core["step"] * 5)
    if phase != "running":
        state["lifecycle"]["controller_session_id"] = None
    existing = {unit["unit_id"]: unit for unit in state["units"]}
    for unit in core["units"]:
        projected = existing[unit["unit_id"]]
        projected.update(position=business(tuple(float(engine.number(value)) for value in unit["position"]["xy"])),
                         durability=float(engine.number(unit["hp"])), energy=float(engine.number(unit["energy"])),
                         supply=unit["ammo"], availability="available" if engine.alive(unit) else "destroyed",
                         flight_state=unit['flight'],
                         resource_cargo=deepcopy(unit['cargo']), low_energy_reason=unit.get('energy_guard', {}).get('reason'),
                         status="已退场" if not engine.alive(unit) else "五点规则 · " + unit["flight"])
    state["scores"] = dict(available=True, winner=core["result"]["winner"] if core["result"] else None,
                           reason=core["result"]["reason"] if core["result"] else "五点对局进行中")


def apply(state: dict, actor: str, kind: str, payload: dict, command_id: str) -> dict:
    """在既有对局事务锁内执行新命令；权限、依据和幂等仍由统一入口检查。"""
    core = state["fivepoint"]
    event = dict(title="五点规则操作已提交", detail="", side=actor, visibility="public", private_plan=False)
    try:
        if kind == "submit_fivepoint_plan":
            require(actor in ("red", "blue"), "ROLE_FORBIDDEN", "主持人不能代替双方提交方案", 403)
            require(core['frozen'].get('control_mode', 'manual') == 'manual', 'AUTONOMOUS_MODE', '自动模式禁止人工改令')
            require(not core.get('planning_run', {}).get('active'), 'PLANNING_BUSY', '共同规划尚未完成')
            if payload.get('use_candidate'):
                require(payload.get('decision', 'adopt') == 'adopt' and not payload['orders'] and payload.get('tasks') is None,
                        'PLAN_FORMAT_CONFLICT', '采用模型候选不能同时传入其他动作')
                candidate = core.get('planning_candidates', {}).get(actor)
                require(candidate is not None, 'PLAN_CANDIDATE_MISSING', '本轮没有本方模型候选')
                payload = dict(payload, tasks=candidate)
            state["fivepoint"] = engine.submit_plan(core, actor, payload["orders"], payload["expected_step"],
                                                   payload.get('tasks'), payload.get('decision', 'adopt'), payload.get('cancel_task_ids', []))
            state['fivepoint'].get('planning_candidates', {}).pop(actor, None)
            event.update(title="本方方案已处理", visibility=actor, private_plan=True)
        elif kind == "surrender":
            require(actor in ("red", "blue"), "ROLE_FORBIDDEN", "只能提交本方投降", 403)
            state["fivepoint"] = engine.request_surrender(core, actor)
            event.update(title="本方投降已登记", visibility=actor)
        elif kind == "advance_step":
            host_only(actor)
            _, geometry = published_map(state)
            state["fivepoint"] = engine.advance(core, geometry, core["step"], command_id)
            event.update(title="五秒规则步已提交", detail="移动、观察、交战、服务、占点和终局已原子结算")
        elif kind in ("pause", "begin_exit", "continue"):
            host_only(actor)
            require(core["result"] is None, "GAME_FINISHED", "已结束对局不可继续")
            if kind == "continue":
                require(core["phase"] == "paused", "WAITING_CONFIRMATION", "须等待双方共同采用方案")
                core["phase"] = "running"
            elif core["phase"] == "running":
                core["phase"] = "paused"
            event.update(title="播放状态已更新")
        elif kind == "retry_fivepoint_planning":
            host_only(actor)
            state["fivepoint"] = engine.retry_planning(core)
            event.update(title="共同规划恢复已开始")
        elif kind == "abort_fivepoint":
            host_only(actor)
            state["fivepoint"] = engine.technical_abort(core)
            event.update(title="对局已技术中止", detail="不产生竞技胜者")
        else:
            raise ServiceError(409, "FIVEPOINT_COMMAND_FORBIDDEN", "五点规则局不能执行旧演示命令")
        engine.validate(state["fivepoint"])
    except RuleViolation as error:
        raise ServiceError(409, error.code, str(error)) from error
    synchronize(state)
    return event


def projection(state: dict, role: str) -> dict:
    """供页面、存档档案及回放复用同一份席位投影，避免私有库存泄漏。"""
    core = state["fivepoint"]
    if role == "host":
        view = dict(step=core["step"], second=core["step"] * 5, phase=core["phase"], rule_version=VERSION,
                    points=deepcopy(core["points"]), result=deepcopy(core["result"]),
                    events=[dict(deepcopy(event), event_index=index) for index, event in enumerate(core['events'])],
                    public_control={side: str(engine.evaluate(core)[side]["control"]) for side in ("red", "blue")})
    else:
        view = engine.project(core, role)
    view['control_mode'] = core['frozen'].get('control_mode', 'manual')
    if role in ('red', 'blue'):
        candidate = view.get('planning_candidate')
        view['planning_candidate_json'] = json.dumps(candidate, ensure_ascii=False, indent=2) if candidate is not None else None
    view['playback_origin_step'] = core.get('playback_origin_step', 0)
    view['planning_active'] = core.get('planning_run', {}).get('active', False)
    view['planning_source'] = core.get('planning_source', 'none')
    current_contacts = {contact['unit_id'] for contact in view.get('contacts', [])}
    view['display_contacts'] = [dict(unit_id=contact['unit_id'], unit_type=contact['unit_type'],
        x=float(engine.number(contact['position']['xy'][0])), z=float(engine.number(contact['position']['xy'][1])),
        hp=contact.get('hp'), observed_second=contact['observed_second'], current=contact['unit_id'] in current_contacts)
        for contact in view.get('history', {}).values()]
    view['planning_completed'] = (core.get('planning_run', {}).get('step') == core['step']
                                  and core.get('planning_run', {}).get('recovery_round') == core['recovery_round']
                                  and not view['planning_active'])
    view["waiting_sides"] = [side for side in ("red", "blue") if core["phase"] == "planning" and side not in core["pending_plans"]]
    return view


def enrich(units: list) -> list:
    """五点单位展示采用实际冻结参数，不显示旧演示的防御或伤害设计参考值。"""
    for unit in units:
        profile = UNIT_PROFILES[unit["unit_type"]]
        unit["attributes"] = dict(vision_m=profile["vision"], damage=16 if unit["unit_type"] == "ground" else 0,
            defense_percent=0, attack_range_m=600 if unit["unit_type"] == "ground" else 0,
            reference_only=False, durability_known=True, energy_known=True, supply_known=True,
            max_health=profile["hp"], max_energy=100, max_supply=profile["ammo"],
            speed_m_per_step=profile["speed"] / 12, speed_m_per_minute=profile["speed"],
            energy_per_km=profile["cost"], attack_interval_steps=2, attack_supply_cost=1,
            service_range_m=100, service_amount=2)
    return units
