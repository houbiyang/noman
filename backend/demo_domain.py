"""复现已存在的本机交互状态，不将演示移动、建议或事件伪装成正式作战裁决。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from backend.runtime import ServiceError


MAP_PATH = Path(__file__).resolve().parents[1] / "unity-client/Assets/Sandbox/Resources/Maps/QinglanValley/map-definition.json"
TASK_NAMES = {"patrol": "区域巡查", "escort": "物资护送", "protect": "关键设施保护", "evacuate": "受损设备撤离"}
UNIT_NAMES = {"recon": "侦察无人机", "ground": "履带地面单元", "support": "六轮保障车"}
CONDITION_NAMES = {"equipment_fault": "设备故障", "communication_outage": "通信中断", "weather_change": "天气变化", "task_change": "任务变化"}


@lru_cache(maxsize=1)
def map_manifest() -> dict:
    """读取当前公开地图身份及连接，省略显示高度数组和大型网格。"""
    raw = MAP_PATH.read_bytes()
    data = json.loads(raw)
    return dict(map_id=data["mapId"], content_hash=data["contentHash"], source_file_sha256=hashlib.sha256(raw).hexdigest(),
                display_coordinate_system=dict(unit="meter", width=data["width"], length=data["length"]),
                business_coordinate_system=dict(id="unity-demo-120x80-v1", width="120", length="80"),
                source_label="当前 Unity 固定虚构显示地图", navigation_status="not_authoritative",
                nodes=data["nodes"], roads=data["roads"], points=data["points"], deployment_zones=data["deploymentZones"])


def require(condition: bool, code: str, message: str, status: int = 409) -> None:
    """统一拒绝不满足业务前置条件的动作。"""
    if not condition:
        raise ServiceError(status, code, message)


def own_side(actor: str, side: str) -> None:
    """仅允许本方或演示主持人管理对应单位，不授予建议代确认权限。"""
    require(actor == "host" or actor == side, "ROLE_FORBIDDEN", "只能操作本方单位", 403)


def host_only(actor: str) -> None:
    """核对主持人控制权限。"""
    require(actor == "host", "ROLE_FORBIDDEN", "该操作仅允许主持人执行", 403)


def position(value: dict) -> dict:
    """验证有限十进制坐标，保留旧业务坐标的明确命名空间。"""
    try:
        coordinates = [Decimal(value[key]) for key in ("x", "y")]
    except (InvalidOperation, TypeError, KeyError) as error:
        raise ServiceError(422, "POSITION_INVALID", "坐标必须是有效十进制字符串") from error
    require(all(number.is_finite() for number in coordinates), "POSITION_INVALID", "坐标不能为非有限数", 422)
    require(all(-9 <= number.as_tuple().exponent <= 3 for number in coordinates),
            "POSITION_PRECISION_INVALID", "坐标最多保留九位小数，不能使用极端指数", 422)
    require(0 <= coordinates[0] <= 120 and 0 <= coordinates[1] <= 80, "POSITION_OUT_OF_BOUNDS", "目标超出演示业务范围", 422)
    return dict(x=format(coordinates[0].normalize(), "f"), y=format(coordinates[1].normalize(), "f"), coordinate_system_id="unity-demo-120x80-v1")


def new_unit(side: str, unit_type: str, number: int, location: dict) -> dict:
    """创建与 Unity 稳定编号对应的演示单位，未配置的资源保持为空。"""
    marker = {"recon": "U", "ground": "G", "support": "S"}[unit_type]
    unit_id = ("R" if side == "red" else "B") + f"-{marker}{number:02}"
    return dict(unit_id=unit_id, side=side, unit_type=unit_type, name=UNIT_NAMES[unit_type] + f" {number:02}",
                position=location, status="已部署 · 待命", availability="available", durability=None, energy=None, supply=None,
                navigation_status="visual_target_only")


def new_plan(side: str, version: int = 1) -> dict:
    """生成明确标记来源的固定建议，不执行模型请求。"""
    return dict(proposal_id=str(uuid4()), side=side, version=version, status="pending_review", source="fixed_demo",
                priority="先检查中央桥梁，再前往对岸检查点", recon_assignment="观察前方区域并报告已发现信息",
                ground_assignment="沿已选路线前往中央桥梁", support_assignment="集结点待命，准备补给与运输",
                resource_limit=2, effects_executed=False)


def task_instances(task_type: str) -> list[dict]:
    """保留四任务展示与节点结构，不伪造任务进度。"""
    return [dict(task_id=f"{side}-task", side=side, task_type=task_type, title=TASK_NAMES[task_type],
                 status="not_evaluated", nodes=[dict(node_id=f"{side}-node-{number}", status="not_evaluated", completed_step=None)
                                                for number in range(1, 4)]) for side in ("red", "blue")]


def initial_state(scenario_id: str, rule_id: str, task_type: str) -> dict:
    """完整初始化演示状态，正式规则和资源参数缺失时不填虚假数值。"""
    units = []
    for side, locations in (("red", ((25, 44), (30, 40), (20, 36))), ("blue", ((95, 36), (90, 40), (100, 44)))):
        for unit_type, coordinates in zip(UNIT_NAMES, locations):
            units.append(new_unit(side, unit_type, 1, position(dict(x=str(coordinates[0]), y=str(coordinates[1])))))
    return dict(manifest=dict(schema_version="unity-demo-world-v1", scenario_version_id=scenario_id, rule_version_id=rule_id,
                             fixture_only=True, coordinate_system_id="unity-demo-120x80-v1"),
                lifecycle=dict(simulation_step=0, phase="preparing", deadline_step=60, step_minutes=1,
                               ready=dict(red=False, blue=False), pause_reasons=[], controller_session_id=None),
                camps=[dict(camp_id=f"{side}-camp", side=side, status="fixed_demo", deployment_validated=False) for side in ("red", "blue")],
                units=units, formations=[], tasks=task_instances(task_type), routes=[],
                observations=dict(red=dict(contacts=[], last_seen_records=[]), blue=dict(contacts=[], last_seen_records=[])),
                logistics=dict(formal_rules_available=False), conditions=dict(instances=[], map_overrides=[]),
                decisions=dict(plans={side: new_plan(side) for side in ("red", "blue")}),
                scores=dict(available=False, winner=None, reason="当前范围没有实现任务、交战与评分裁决"))


def validate_state(state: dict) -> None:
    """检查当前演示结构、稳定身份与坐标，不接受任意客户端整局快照。"""
    require(state["manifest"].get("schema_version") == "unity-demo-world-v1", "SNAPSHOT_INCOMPATIBLE", "存档格式不受当前服务支持")
    lifecycle = state["lifecycle"]
    require(type(lifecycle["simulation_step"]) is int and 0 <= lifecycle["simulation_step"] <= 60,
            "SNAPSHOT_INVALID", "存档仿真步无效")
    identities = set()
    for unit in state["units"]:
        require(unit["unit_id"] not in identities and unit["side"] in ("red", "blue") and unit["unit_type"] in UNIT_NAMES,
                "SNAPSHOT_INVALID", "单位身份或类型无效")
        identities.add(unit["unit_id"])
        position(unit["position"])
    for side in ("red", "blue"):
        for unit_type in UNIT_NAMES:
            count = sum(unit["side"] == side and unit["unit_type"] == unit_type for unit in state["units"])
            require(0 <= count <= 10, "SNAPSHOT_INVALID", "当前演示每方每类须为 0–10 个单位")
        plan = state["decisions"]["plans"][side]
        require(plan["side"] == side and 0 <= plan["resource_limit"] <= 20, "SNAPSHOT_INVALID", "建议所属方或额度无效")
    camps = {camp["camp_id"]: camp for camp in state["camps"]}
    require(len(camps) == len(state["camps"]), "SNAPSHOT_INVALID", "兵营身份重复")
    for camp in camps.values():
        require(camp["side"] in ("red", "blue"), "SNAPSHOT_INVALID", "兵营阵营无效")
        if camp.get("unit_type"):
            require(camp["unit_type"] in UNIT_NAMES and camp["camp_id"] == barracks_id(camp["side"], camp["unit_type"]),
                    "SNAPSHOT_INVALID", "兵营兵种或身份无效")
            position(camp["position"])
    for unit in state["units"]:
        if unit.get("camp_id"):
            camp = camps.get(unit["camp_id"])
            require(camp is not None and camp["side"] == unit["side"] and camp.get("unit_type", unit["unit_type"]) == unit["unit_type"],
                    "SNAPSHOT_INVALID", "单位与兵营归属不一致")
    if "spatial_profile" in state["manifest"]:
        from backend.spatial import validate
        validate(state)


def waiting_sides(state: dict) -> list[str]:
    """返回仅包含方别的阻塞摘要，不公开私有方案。"""
    return [side for side, plan in state["decisions"]["plans"].items() if plan["status"] != "adopted"]


def reset_plan(state: dict, side: str) -> None:
    """单位配置或重新载入建议后，建立新身份而非覆盖原建议内容。"""
    old = state["decisions"]["plans"][side]
    state["decisions"]["plans"][side] = new_plan(side, old["version"] + 1)
    if state["lifecycle"]["phase"] not in ("preparing", "finished"):
        state["lifecycle"].update(phase="awaiting_confirmation", controller_session_id=None)


def barracks_id(side: str, unit_type: str) -> str:
    """生成与既有 Unity 兵营一致的稳定身份。"""
    return ("R" if side == "red" else "B") + "-CAMP-" + {"recon": "recon", "ground": "infantry", "support": "support"}[unit_type]


def delete_unit(state: dict, actor: str, unit_id: str) -> tuple[dict, dict]:
    """删除单位及其活动路线，保留兵营和历史记录，清除相关准备状态。"""
    require(state["lifecycle"]["phase"] not in ("running", "finished"), "PHASE_CONFLICT", "请在准备或暂停阶段删除单位")
    unit = next((item for item in state["units"] if item["unit_id"] == unit_id and (actor == "host" or item["side"] == actor)), None)
    require(unit is not None, "UNIT_NOT_FOUND", "单位不存在或无权操作", 404)
    state = deepcopy(state)
    side = unit["side"]
    state["units"] = [item for item in state["units"] if item["unit_id"] != unit_id]
    state["routes"] = [item for item in state["routes"] if item["unit_id"] != unit_id]
    for formation in state["formations"]:
        formation["unit_ids"] = [identity for identity in formation["unit_ids"] if identity != unit_id]
    state["lifecycle"]["ready"][side] = False
    reset_plan(state, side)
    return state, dict(title="单位已删除", detail=unit_id + " 已移除，兵营保留", side=side, visibility=side, private_plan=False)


def apply_interaction(state: dict, actor: str, command_type: str, payload: dict) -> tuple[dict, dict]:
    """执行已实现的演示交互，返回新状态与可投影的事实描述。"""
    state = deepcopy(state)
    clock = state["lifecycle"]
    require(clock["phase"] != "finished", "GAME_FINISHED", "演示已结束，仅允许读取与保存")
    event = dict(title="演示操作已提交", detail="", side=actor, visibility=actor, private_plan=False)
    if command_type in ("configure_army", "deploy_army"):
        side = payload["side"]
        own_side(actor, side)
        require(clock["phase"] != "running", "PAUSE_REQUIRED", "请先暂停演示，再调整军队")
        units = state["units"]
        if command_type == "configure_army":
            for unit_type, quantity in payload["quantities"].items():
                old = {unit["unit_id"]: unit for unit in units if unit["side"] == side and unit["unit_type"] == unit_type}
                units = [unit for unit in units if unit["side"] != side or unit["unit_type"] != unit_type]
                for offset in range(quantity):
                    location = position(dict(x=str((23 if side == "red" else 97) + (offset % 4) * 2),
                                             y=str(32 + list(UNIT_NAMES).index(unit_type) * 7 + offset // 4 * 2)))
                    candidate = new_unit(side, unit_type, offset + 1, location)
                    units.append(old.get(candidate["unit_id"], candidate))
            event.update(title="演示军队编制已更新", detail="数量已持久化；不是正式兵力上限", side=side, visibility=side)
        else:
            center = position(payload["position"])
            quantity, unit_type = payload["quantity"], payload["unit_type"]
            placed = []
            for offset in range(quantity):
                location = position(dict(x=str(Decimal(center["x"]) + (Decimal(offset % 4) - Decimal(min(quantity, 4)) / 2 + Decimal('0.5')) * Decimal('2.1')),
                                         y=str(Decimal(center["y"]) + Decimal(offset // 4) * Decimal('2.1'))))
                placed.append(new_unit(side, unit_type, offset + 1, location))
            units = [unit for unit in units if unit["side"] != side or unit["unit_type"] != unit_type] + placed
            event.update(title="演示编队位置已保存", detail="仅为演示布置；未进行正式地形通行裁决", side=side, visibility=side)
        state["units"] = units
        reset_plan(state, side)
    elif command_type == "move_unit":
        unit = next((unit for unit in state["units"] if unit["unit_id"] == payload["unit_id"] and (actor == 'host' or unit['side'] == actor)), None)
        require(unit is not None, "UNIT_NOT_FOUND", "单位不存在或不属于当前角色", 404)
        own_side(actor, unit["side"])
        unit["position"] = position(payload["position"])
        unit["status"] = "演示机动目标已保存"
        event.update(title="演示机动目标已保存", detail="坐标是显示目标；未执行正式寻路、到达或资源扣减", side=unit["side"], visibility=unit["side"])
    elif command_type == "set_ready":
        require(actor in ("red", "blue"), "ROLE_FORBIDDEN", "主持人不能代替双方准备", 403)
        require(clock["phase"] == "preparing", "PHASE_CONFLICT", "只能在准备阶段修改准备状态")
        clock["ready"][actor] = payload["ready"]
        event.update(title="本方准备状态已更新", detail="已保存本方准备状态")
    elif command_type == "set_tasks":
        host_only(actor)
        require(clock["phase"] == "preparing", "PHASE_CONFLICT", "只能在准备阶段选择任务模板")
        state["tasks"] = task_instances(payload["task_type"])
        clock["ready"] = dict(red=False, blue=False)
        for side in ("red", "blue"):
            reset_plan(state, side)
        event.update(title="演示任务模板已更新", detail=TASK_NAMES[payload["task_type"]] + "；正式节点进度未实现", visibility="public")
    elif command_type == "start_game":
        host_only(actor)
        require(clock["phase"] == "preparing" and all(clock["ready"].values()), "NOT_READY", "双方准备完成后才能开始")
        clock["phase"] = "awaiting_confirmation" if waiting_sides(state) else "paused"
        event.update(title="演示已开始", detail="首次固定建议须由双方分别处理", visibility="public")
    elif command_type == "reload_demo_plan":
        require(actor in ("red", "blue"), "ROLE_FORBIDDEN", "只能由本方操作员载入固定建议", 403)
        reset_plan(state, actor)
        event.update(title="固定演示建议已重新载入", detail="来源为固定样例，没有调用真实模型", private_plan=True)
    elif command_type in ("pause", "begin_exit", "continue", "advance_step"):
        host_only(actor)
        if command_type in ("pause", "begin_exit"):
            require(clock["phase"] != "preparing", "PHASE_CONFLICT", "请先开始演示")
            clock.update(phase="paused", controller_session_id=None)
            event.update(title="演示已暂停", detail="暂停不解除未处理建议", visibility="public")
        else:
            require(clock["phase"] != "preparing", "PHASE_CONFLICT", "请先完成准备并开始演示")
            require(not waiting_sides(state), "WAITING_CONFIRMATION", "双方建议尚未全部采用，禁止推进")
            if command_type == "continue":
                clock["phase"] = "running"
                event.update(title="演示播放已开启", detail="由后端固定演示节奏发起单步", visibility="public")
            else:
                require(clock["simulation_step"] < clock.get("deadline_step", 60), "DEADLINE_REACHED", "已达到本局时间上限")
                clock["simulation_step"] += 1
                for unit in state["units"]:
                    if unit["unit_type"] == "ground":
                        delta = Decimal('0.45') if unit["side"] == "red" else Decimal('-0.45')
                        unit["position"]["x"] = format(max(Decimal(0), min(Decimal(120), Decimal(unit["position"]["x"]) + delta)), "f")
                if clock["simulation_step"] == clock.get("deadline_step", 60):
                    clock.update(phase="finished", controller_session_id=None)
                event.update(title="演示步已推进", detail="仅执行原有预置位移；未进行任务、交战或评分结算", visibility="public")
    else:
        raise ServiceError(422, "COMMAND_UNSUPPORTED", "当前交互不由演示规则入口处理")
    validate_state(state)
    return state, event
