"""S2 可配置测试空间规则；参数随快照冻结，不作为正式作战参数。"""

from copy import deepcopy
import heapq
import hashlib
import json
import math
from pathlib import Path

from backend.demo_domain import MAP_PATH, barracks_id, map_manifest, new_unit, own_side, position, require, reset_plan
from backend.runtime import sha256


def profile() -> dict:
    """读取本机受版本管理的测试参数并校验有限正数。"""
    value = json.loads(Path(__file__).with_name("s2-test-profile.json").read_text(encoding="utf-8"))
    require(value.get("test_only") is True, "PROFILE_INVALID", "空间测试规则必须标明测试用途", 422)
    for name in ("camp_half_extent_m", "building_exclusion_radius_m", "road_connection_max_m", "road_clearance_m", "maximum_slope"):
        require(type(value.get(name)) in (int, float) and math.isfinite(value[name]) and value[name] > 0,
                "PROFILE_INVALID", "空间参数必须为有限正数", 422)
    for kind in ("recon", "ground", "support"):
        for name in ("speed_m_per_step", "energy", "supply", "durability", "energy_per_km"):
            number = value["unit_profiles"][kind][name]
            require(type(number) in (int, float) and math.isfinite(number) and number >= 0,
                    "PROFILE_INVALID", "单位测试参数无效", 422)
        require(value["unit_profiles"][kind]["speed_m_per_step"] > 0, "PROFILE_INVALID", "移动速度必须大于零", 422)
    return value


def map_data(state: dict) -> dict:
    """只允许使用快照所绑定的地图源文件。"""
    raw = MAP_PATH.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == state["manifest"]["map_source_sha256"],
            "MAP_VERSION_UNAVAILABLE", "当前地图与存档版本不同，请恢复对应地图资源")
    return json.loads(raw)


def meters(location: dict) -> tuple[float, float]:
    """将既有业务坐标转换为十二公里地图米制坐标。"""
    checked = position(location)
    return float(checked["x"]) * 100, float(checked["y"]) * 150


def business(point: tuple[float, float]) -> dict:
    """将米制位置量化为既有九位小数业务格式。"""
    return position(dict(x=f"{point[0] / 100:.9f}", y=f"{point[1] / 150:.9f}"))


def segment_distance(point, start, end) -> float:
    """计算点到线段的最短距离，包含端点。"""
    delta = (end[0] - start[0], end[1] - start[1])
    length_squared = delta[0] ** 2 + delta[1] ** 2
    ratio = max(0, min(1, ((point[0] - start[0]) * delta[0] + (point[1] - start[1]) * delta[1]) / length_squared)) if length_squared else 0
    return math.dist(point, (start[0] + ratio * delta[0], start[1] + ratio * delta[1]))


def height(data: dict, point) -> float:
    """双线性采样地图高度；水下区域在测试规则中不可建造。"""
    column = max(0, min(data["heightColumns"] - 1, point[0] / data["width"] * (data["heightColumns"] - 1)))
    row = max(0, min(data["heightRows"] - 1, point[1] / data["length"] * (data["heightRows"] - 1)))
    left, top = int(column), int(row)
    right, bottom = min(left + 1, data["heightColumns"] - 1), min(top + 1, data["heightRows"] - 1)
    sample = lambda x, y: data["heights"][y * data["heightColumns"] + x]
    upper = sample(left, top) * (1 - column + left) + sample(right, top) * (column - left)
    lower = sample(left, bottom) * (1 - column + left) + sample(right, bottom) * (column - left)
    return upper * (1 - row + top) + lower * (row - top)


def camp(state: dict, side: str, location: dict) -> dict:
    """校验完整方形占地、部署区、建筑隔离、道路净距和道路连接。"""
    data, rules = map_data(state), state["manifest"]["spatial_profile"]
    center = meters(location)
    extent = rules["camp_half_extent_m"]
    zone = next(zone for zone in data["deploymentZones"] if zone["side"] == side)
    require(zone["minX"] <= center[0] - extent and center[0] + extent <= zone["maxX"]
            and zone["minZ"] <= center[1] - extent and center[1] + extent <= zone["maxZ"],
            "CAMP_FOOTPRINT_OUTSIDE", "兵营完整占地必须位于本方部署区")
    samples = [(center[0] + x * extent, center[1] + y * extent) for x in (-1, 0, 1) for y in (-1, 0, 1)]
    heights = [height(data, point) for point in samples]
    require(min(heights) >= 0 and (max(heights) - min(heights)) / (2 * extent) <= rules["maximum_slope"],
            "CAMP_TERRAIN_INVALID", "兵营占地位于水域或坡度超过测试限制")
    for building in data["buildings"]:
        nearest = (max(center[0] - extent, min(building["x"], center[0] + extent)),
                   max(center[1] - extent, min(building["z"], center[1] + extent)))
        require(math.dist(nearest, (building["x"], building["z"])) > rules["building_exclusion_radius_m"],
                "CAMP_OCCUPIED", "兵营占地与地图建筑保守隔离区重叠")
    nodes = {node["id"]: (node["x"], node["z"]) for node in data["nodes"]}
    for road in data["roads"]:
        require(segment_distance(center, nodes[road["startId"]], nodes[road["endId"]]) > extent * math.sqrt(2) + road["width"] / 2 + rules["road_clearance_m"],
                "CAMP_ROAD_OVERLAP", "兵营占地与道路或桥梁隔离区重叠")
    node_id = min(nodes, key=lambda identity: (math.dist(center, nodes[identity]), identity))
    require(math.dist(center, nodes[node_id]) <= rules["road_connection_max_m"], "CAMP_ROAD_UNREACHABLE", "兵营出入口无法接入道路节点")
    return dict(camp_id=side + "-camp", side=side, position=position(location), status="test_validated",
                deployment_validated=True, ground_node_id=node_id, docking_position=position(location),
                half_extent_m=extent, geometry_policy="conservative_test_envelopes")


def route(state: dict, unit: dict, target: dict) -> dict:
    """为空中单位计算直线航段，为地面单位计算固定道路网络及接入段。"""
    data, rules = map_data(state), state["manifest"]["spatial_profile"]
    start, end = meters(unit["position"]), meters(target)
    points, road_ids = [start, end], []
    if unit["unit_type"] != "recon":
        nodes = {node["id"]: (node["x"], node["z"]) for node in data["nodes"]}
        origin, destination = [min(nodes, key=lambda key: (math.dist(point, nodes[key]), key)) for point in (start, end)]
        require(all(math.dist(point, nodes[key]) <= rules["road_connection_max_m"] for point, key in ((start, origin), (end, destination))),
                "ROAD_ACCESS_UNAVAILABLE", "起点或终点超过测试道路接入距离")
        adjacency = {key: [] for key in nodes}
        for road in data["roads"]:
            first, last = road["startId"], road["endId"]
            distance = math.dist(nodes[first], nodes[last])
            adjacency[first].append((last, distance, road["id"]))
            adjacency[last].append((first, distance, road["id"]))
        queue, distances, previous = [(0, origin)], {origin: 0}, {}
        while queue:
            distance, current = heapq.heappop(queue)
            if distance != distances[current]:
                continue
            for neighbor, cost, identity in sorted(adjacency[current]):
                if distance + cost < distances.get(neighbor, math.inf):
                    distances[neighbor], previous[neighbor] = distance + cost, (current, identity)
                    heapq.heappush(queue, (distance + cost, neighbor))
        require(destination in distances, "ROUTE_UNAVAILABLE", "道路网络不连通")
        chain, cursor = [destination], destination
        while cursor != origin:
            cursor, identity = previous[cursor]
            chain.append(cursor); road_ids.append(identity)
        points = [start] + [nodes[key] for key in reversed(chain)] + [end]
        road_ids.reverse()
    total = sum(math.dist(first, last) for first, last in zip(points, points[1:]))
    return dict(route_id=sha256(dict(unit_id=unit["unit_id"], start=start, target=target, profile=rules)),
                unit_id=unit["unit_id"], points=points, road_ids=road_ids, distance_m=round(total, 6),
                traveled_m=0, segment_index=0, movement_type="air" if unit["unit_type"] == "recon" else "ground",
                status="queued", test_only=True)


def place_barracks(state: dict, actor: str, payload: dict) -> tuple[dict, dict]:
    """校验并保存单一兵种兵营，重新选址只改变建筑和归属。"""
    side, kind = payload["side"], payload["unit_type"]
    own_side(actor, side)
    require(state["lifecycle"]["phase"] == "preparing", "PHASE_CONFLICT", "兵营只能在准备阶段建造或重新选址")
    geometry_state = state
    if "spatial_profile" not in state["manifest"]:
        geometry_state = dict(state, manifest=dict(state["manifest"], spatial_profile=profile(),
                              map_source_sha256=map_manifest()["source_file_sha256"]))
    placed = camp(geometry_state, side, payload["position"])
    placed.update(camp_id=barracks_id(side, kind), unit_type=kind)
    center = meters(placed["position"])
    for other in state["camps"]:
        if other["camp_id"] == placed["camp_id"] or not other.get("position"):
            continue
        other_center = meters(other["position"])
        clearance = placed["half_extent_m"] + other.get("half_extent_m", placed["half_extent_m"])
        require(abs(center[0] - other_center[0]) >= clearance or abs(center[1] - other_center[1]) >= clearance,
                "CAMP_OCCUPIED", "兵营占地与已有兵营重叠")
    state = deepcopy(state)
    state["camps"] = [item for item in state["camps"] if item["camp_id"] != placed["camp_id"]] + [placed]
    for unit in state["units"]:
        if unit["side"] == side and unit["unit_type"] == kind:
            unit["camp_id"] = placed["camp_id"]
            if "spatial_profile" in state["manifest"]:
                limits = state["manifest"]["spatial_profile"]["unit_profiles"][kind]
                for field in ("energy", "supply", "durability"):
                    if unit.get(field) is None:
                        unit[field] = limits[field]
    used_camps = {unit.get("camp_id") for unit in state["units"]}
    state["camps"] = [item for item in state["camps"]
                      if item["side"] != side or item.get("unit_type") or item["camp_id"] in used_camps]
    state["lifecycle"]["ready"][side] = False
    reset_plan(state, side)
    return state, dict(title="兵营位置已保存", detail="已校验部署区、完整占地、建筑和道路接入，已有单位保留原位置",
                       side=side, visibility=side, private_plan=False)


def set_preparation(state: dict, actor: str, payload: dict) -> tuple[dict, dict]:
    """在准备阶段整体替换本方兵营和单位，旧已准备状态失效。"""
    own_side(actor, payload["side"])
    require(actor != "host", "ROLE_FORBIDDEN", "主持人不能代替本方配置测试兵营", 403)
    require(state["lifecycle"]["phase"] == "preparing", "PHASE_CONFLICT", "开局后兵营和编制固定")
    state = deepcopy(state)
    side = payload["side"]
    placed = camp(state, side, payload["position"]) if payload.get("position") else None
    state["camps"] = [item for item in state["camps"] if item["side"] != side] + ([placed] if placed else [])
    units = []
    for kind, count in payload["quantities"].items():
        for number in range(1, count + 1):
            unit = new_unit(side, kind, number, placed["position"] if placed else position(dict(x="0", y="0")))
            unit.update({name: state["manifest"]["spatial_profile"]["unit_profiles"][kind][name] for name in ("energy", "supply", "durability")})
            unit.update(camp_id=side + "-camp" if placed else None, formation_id=side + "-" + kind, navigation_status="test_route", availability="available", status="兵营待命" if placed else "等待配置兵营")
            units.append(unit)
    state["units"] = [item for item in state["units"] if item["side"] != side] + units
    state["formations"] = [dict(formation_id=side_name + "-" + kind, side=side_name, unit_type=kind,
        unit_ids=[unit["unit_id"] for unit in state["units"] if unit["side"] == side_name and unit["unit_type"] == kind])
        for side_name in ("red", "blue") for kind in ("recon", "ground", "support")]
    state["lifecycle"]["ready"][side] = False
    reset_plan(state, side)
    return state, dict(title="测试准备配置已保存", detail="完整占地和道路接入已校验；正式参数未批准", side=side, visibility=side, private_plan=False)


def advance(state: dict) -> None:
    """每步沿冻结路线推进并扣能源，资源耗尽后保留可恢复进度。"""
    for active in state["routes"]:
        if active["status"] not in ("queued", "moving"):
            continue
        unit = next(unit for unit in state["units"] if unit["unit_id"] == active["unit_id"])
        rules = state["manifest"]["spatial_profile"]["unit_profiles"][unit["unit_type"]]
        if unit["durability"] <= 0 or unit["energy"] <= 0:
            active["status"] = "blocked"; unit["status"] = "资源不足或单位失效"; continue
        allowance = min(rules["speed_m_per_step"], unit["energy"] * 1000 / rules["energy_per_km"] if rules["energy_per_km"] else math.inf)
        moved = 0.0
        current = meters(unit["position"])
        while allowance > 0 and active["segment_index"] < len(active["points"]) - 1:
            target = active["points"][active["segment_index"] + 1]
            distance = math.dist(current, target)
            amount = min(allowance, distance)
            current = tuple(current[index] + (target[index] - current[index]) * amount / distance for index in (0, 1)) if distance else tuple(target)
            allowance -= amount; moved += amount
            if amount >= distance:
                active["segment_index"] += 1
        unit["position"] = business(current)
        unit["energy"] = round(max(0, unit["energy"] - moved / 1000 * rules["energy_per_km"]), 6)
        active["traveled_m"] = round(active["traveled_m"] + moved, 6)
        active["status"] = "arrived" if active["segment_index"] == len(active["points"]) - 1 else "moving"
        unit["status"] = "测试路线已到达" if active["status"] == "arrived" else "沿测试路线移动"


def validate(state: dict) -> None:
    """验证测试单位资源、兵营关系及可恢复路线游标。"""
    map_data(state)
    camps = {item["camp_id"]: item for item in state["camps"]}
    units = {item["unit_id"]: item for item in state["units"]}
    for unit in units.values():
        if not unit.get("camp_id"):
            require(not state["lifecycle"]["ready"][unit["side"]], "SNAPSHOT_INVALID", "未配置兵营不能处于准备完成状态")
            continue
        require(unit["camp_id"] in camps and camps[unit["camp_id"]]["side"] == unit["side"], "SNAPSHOT_INVALID", "单位兵营归属无效")
        limits = state["manifest"]["spatial_profile"]["unit_profiles"][unit["unit_type"]]
        for field in ("energy", "supply", "durability"):
            require(type(unit[field]) in (int, float) and math.isfinite(unit[field]) and 0 <= unit[field] <= limits[field],
                    "SNAPSHOT_INVALID", "测试资源值超出冻结参数范围")
    seen = set()
    for active in state["routes"]:
        require(active["unit_id"] in units and active["unit_id"] not in seen, "SNAPSHOT_INVALID", "路线单位缺失或重复")
        seen.add(active["unit_id"])
        require(len(active["points"]) >= 2 and 0 <= active["segment_index"] < len(active["points"])
                and 0 <= active["traveled_m"] <= active["distance_m"] + 0.001, "SNAPSHOT_INVALID", "测试路线进度无效")
        for point in active["points"]:
            require(len(point) == 2 and all(math.isfinite(value) and 0 <= value <= 12000 for value in point), "SNAPSHOT_INVALID", "路线坐标无效")
