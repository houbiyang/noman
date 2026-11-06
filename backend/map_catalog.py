"""当前地图完整数据转换、三角高程采样及道路目录校验。"""

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math

from backend.demo_domain import MAP_PATH, require
from backend.runtime import sha256


SCHEMA_VERSION = "map-elevation-roads-v2"
CONVERTER_VERSION = "qinglan-catalog-v2.1"


def number(value) -> str:
    """将有限坐标统一为至多九位小数的非指数十进制字符串。"""
    require(type(value) in (int, float) and math.isfinite(value), "MAP_NUMBER_INVALID", "地图含非有限数值", 422)
    result = f"{value:.9f}".rstrip("0").rstrip(".")
    return "0" if result == "-0" else result


def unique_object(pairs):
    """拒绝源 JSON 重复键，避免解析时静默覆盖。"""
    result = {}
    for key, value in pairs:
        require(key not in result, "MAP_DUPLICATE_KEY", "地图 JSON 存在重复键", 422)
        result[key] = value
    return result


def terrain_height(source: dict, x: float, z: float) -> float:
    """严格按现有 Unity 单格对角线插值，边界合法且越界拒绝。"""
    require(math.isfinite(x) and math.isfinite(z) and 0 <= x <= source["width"] and 0 <= z <= source["length"],
            "MAP_POSITION_INVALID", "地图采样超出范围", 422)
    column = x / source["width"] * (source["heightColumns"] - 1)
    row = z / source["length"] * (source["heightRows"] - 1)
    left = min(int(column), source["heightColumns"] - 2)
    top = min(int(row), source["heightRows"] - 2)
    horizontal, vertical = column - left, row - top
    offset, stride = top * source["heightColumns"] + left, source["heightColumns"]
    values = source["heights"]
    if vertical >= horizontal:
        return values[offset] * (1 - vertical) + values[offset + stride] * (vertical - horizontal) + values[offset + stride + 1] * horizontal
    return values[offset] * (1 - horizontal) + values[offset + 1] * (horizontal - vertical) + values[offset + stride + 1] * vertical


def location(x: float, y: float, z: float) -> dict:
    """生成米制三维位置，与旧业务二维坐标明确分开。"""
    return dict(x=number(x), y=number(y), z=number(z))


def road_profile(source: dict, start: dict, end: dict, bridge_height=None) -> list[dict]:
    """在网格边界及对角线处细分道路，桥面使用独立高程。"""
    delta_x, delta_z = end["x"] - start["x"], end["z"] - start["z"]
    spacing_x = source["width"] / (source["heightColumns"] - 1)
    spacing_z = source["length"] / (source["heightRows"] - 1)
    stops = {0.0, 1.0}
    for origin, delta, spacing, count in ((start["x"], delta_x, spacing_x, source["heightColumns"]),
                                           (start["z"], delta_z, spacing_z, source["heightRows"])):
        if delta:
            stops.update(value for index in range(count) if 0 < (value := (index * spacing - origin) / delta) < 1)
    boundaries = sorted(stops)
    coefficient = delta_z / spacing_z - delta_x / spacing_x
    if abs(coefficient) > 1e-12:
        for lower, upper in zip(boundaries, boundaries[1:]):
            middle = (lower + upper) / 2
            cell_x = min(int((start["x"] + middle * delta_x) / spacing_x), source["heightColumns"] - 2)
            cell_z = min(int((start["z"] + middle * delta_z) / spacing_z), source["heightRows"] - 2)
            crossing = (start["x"] / spacing_x - cell_x - start["z"] / spacing_z + cell_z) / coefficient
            if lower < crossing < upper:
                stops.add(crossing)
    if bridge_height is not None:
        stops = {0.0, 1.0}
    return [location(start["x"] + ratio * delta_x,
        bridge_height if bridge_height is not None else terrain_height(source, start["x"] + ratio * delta_x, start["z"] + ratio * delta_z),
        start["z"] + ratio * delta_z) for ratio in sorted(stops)]


def validate_source(source: dict) -> None:
    """转换前校验完整高度、节点与源道路，不跳过损坏记录。"""
    require(source.get("coordinateUnit") == "meter" and source.get("schemaVersion") == 2,
            "MAP_SCHEMA_INVALID", "当前转换器仅支持米制地图格式二", 422)
    for field in ("width", "length"):
        require(type(source.get(field)) in (int, float) and math.isfinite(source[field]) and source[field] > 0,
                "MAP_BOUNDS_INVALID", "地图范围无效", 422)
    for field in ("heightColumns", "heightRows"):
        require(type(source.get(field)) is int and source[field] >= 2, "MAP_GRID_INVALID", "高程行列无效", 422)
    values = source.get("heights")
    require(isinstance(values, list) and len(values) == source["heightColumns"] * source["heightRows"]
            and all(type(value) in (int, float) and math.isfinite(value) for value in values),
            "MAP_GRID_INVALID", "高程数量、类型或有限性无效", 422)
    for collection in ("nodes", "roads", "points", "deploymentZones", "buildings", "materials"):
        items = source.get(collection)
        require(isinstance(items, list) and all(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] for item in items),
                "MAP_ID_INVALID", "地图对象缺少稳定编号", 422)
        require(len({item["id"] for item in items}) == len(items), "MAP_ID_DUPLICATE", "地图对象编号重复", 422)
    nodes = {node["id"]: node for node in source["nodes"]}
    require(bool(nodes), "MAP_ID_INVALID", "地图缺少道路节点", 422)
    for node in source["nodes"] + source["points"]:
        terrain_height(source, node["x"], node["z"])
    for road in source["roads"]:
        require(road.get("startId") in nodes and road.get("endId") in nodes and road["startId"] != road["endId"],
                "MAP_ROAD_REFERENCE_INVALID", "道路端点不存在或自环", 422)
        require(type(road.get("isBridge")) is bool and type(road.get("width")) in (int, float)
                and math.isfinite(road["width"]) and road["width"] > 0,
                "MAP_ROAD_INVALID", "道路宽度或桥梁标记无效", 422)
        number(road["surfaceHeight"])
        start, end = nodes[road["startId"]], nodes[road["endId"]]
        require((start["x"], start["z"]) != (end["x"], end["z"]), "MAP_ROAD_INVALID", "道路长度为零", 422)


def convert(source: dict, source_sha256: str) -> dict:
    """无损保留全部源字段，并构造可校验的高度与三维道路索引。"""
    validate_source(source)
    grid = dict(elevation_id="terrain-main", columns=source["heightColumns"], rows=source["heightRows"],
        origin_x="0", origin_z="0", spacing_x_m=number(source["width"] / (source["heightColumns"] - 1)),
        spacing_z_m=number(source["length"] / (source["heightRows"] - 1)), array_order="row_major_z_then_x",
        values=deepcopy(source["heights"]), interpolation_policy_id="unity-cell-diagonal-00-11-v1",
        out_of_bounds_policy="reject", no_data_policy="reject_publish", value_min_m=number(min(source["heights"])),
        value_max_m=number(max(source["heights"])), source_kind="unity_display_heightfield", usage_status="test_only")
    grid["values_sha256"] = sha256({key: grid[key] for key in ("columns", "rows", "array_order", "values")})
    nodes = [dict(node_id=node["id"], source_id=node["id"], name=node["name"],
        position=location(node["x"], terrain_height(source, node["x"], node["z"]), node["z"]),
        terrain_elevation_m=number(terrain_height(source, node["x"], node["z"])), navigation_layer_id="ground",
        source_kind=node["kind"], partner_node_id=node.get("partnerId") or None, derived=False) for node in source["nodes"]]
    source_nodes = {node["id"]: node for node in source["nodes"]}
    edges, bridges = [], []
    for road in source["roads"]:
        start, end = source_nodes[road["startId"]], source_nodes[road["endId"]]
        bridge_id = "bridge:" + road["id"] if road["isBridge"] else None
        layer = "bridge-deck:" + bridge_id if bridge_id else "ground"
        polyline = road_profile(source, start, end, road["surfaceHeight"] if bridge_id else None)
        start_id, end_id = road["startId"], road["endId"]
        if bridge_id:
            for key, original, point in (("start", start, polyline[0]), ("end", end, polyline[-1])):
                nodes.append(dict(node_id=bridge_id + ":" + key, source_id=original["id"], name=original["name"],
                    position=point, terrain_elevation_m=number(terrain_height(source, original["x"], original["z"])),
                    navigation_layer_id=layer, source_kind="bridge_deck", partner_node_id=original["id"], derived=True))
            start_id, end_id = bridge_id + ":start", bridge_id + ":end"
            bridges.append(dict(bridge_id=bridge_id, source_road_ids=[road["id"]], deck_edge_ids=[road["id"]],
                deck_height_mode="constant_deck", deck_elevation_m=number(road["surfaceHeight"]), navigation_layer_id=layer,
                connector_node_ids=[], geometry_status="requires_review"))
        lengths, grades = [], []
        for first, last in zip(polyline, polyline[1:]):
            horizontal = math.hypot(float(last["x"]) - float(first["x"]), float(last["z"]) - float(first["z"]))
            vertical = abs(float(last["y"]) - float(first["y"]))
            if horizontal > 1e-8:
                lengths.append(math.hypot(horizontal, vertical)); grades.append(vertical / horizontal)
        edges.append(dict(edge_id=road["id"], source_id=road["id"], source_record=deepcopy(road),
            from_node_id=start_id, to_node_id=end_id, polyline=polyline, width_m=number(road["width"]), direction="both",
            navigation_layer_id=layer, surface_type=None, height_mode="constant_deck" if bridge_id else "terrain_following",
            surface_elevation_m=number(road["surfaceHeight"]) if bridge_id else None,
            length_horizontal_m=number(round(math.hypot(end["x"] - start["x"], end["z"] - start["z"]), 3)),
            length_surface_m=number(round(sum(lengths), 3)), max_grade=number(round(max(grades, default=0), 6)),
            geometry_policy_id="grid-intersection-polyline-v1", bridge_id=bridge_id,
            initial_availability="unknown", movement_policy_id=None, usage_status="requires_review" if bridge_id else "test_only"))
    points = []
    for point in source["points"]:
        elevation = terrain_height(source, point["x"], point["z"])
        points.append(dict(point_id=point["id"], source_id=point["id"], name=point["name"], kind=point["kind"],
            partner_id=point.get("partnerId") or None, position=location(point["x"], elevation, point["z"]),
            terrain_elevation_m=number(elevation), height_mode="terrain_following", navigation_layer_id="ground",
            placement_status="requires_review" if point["id"] == "TASK_B" else "source_position_only"))
    zones = [dict(zone_id=zone["id"], source_id=zone["id"], side=zone["side"],
        polygon_xz=[[number(x), number(z)] for x, z in ((zone["minX"], zone["minZ"]), (zone["maxX"], zone["minZ"]),
        (zone["maxX"], zone["maxZ"]), (zone["minX"], zone["maxZ"]), (zone["minX"], zone["minZ"]))],
        elevation_source_id="terrain-main", buildable_policy_id=None, geometry_status="source_rectangle") for zone in source["deploymentZones"]]
    result = dict(schema_version=SCHEMA_VERSION, map_id=source["mapId"], map_version=CONVERTER_VERSION + "-" + source_sha256[:12],
        name=source["name"], coordinate_system=dict(id="qinglan-local-meters-v1", horizontal_unit="meter", vertical_unit="meter",
        axis_definition=dict(east_west="x", height="y", north_south="z"), origin=location(0, 0, 0),
        bounds=dict(min_x="0", max_x=number(source["width"]), min_z="0", max_z=number(source["length"])),
        vertical_reference="fictional-map-local-v1", legacy_transform=dict(source_id="unity-demo-120x80-v1",
        transform_version="demo120x80-to-map12000-v1", x_scale="100", source_y_to_z_scale="150", x_offset="0", z_offset="0")),
        elevation_grid=grid, road_nodes=nodes, road_edges=edges, bridges=bridges, points=points, deployment_zones=zones,
        source_payload=deepcopy(source), buildings=deepcopy(source["buildings"]), materials=deepcopy(source["materials"]),
        import_audit=dict(source_sha256=source_sha256, converter_version=CONVERTER_VERSION, geometry_policy_id="grid-intersection-polyline-v1",
        source_counts={key: len(source[key]) for key in ("nodes", "roads", "points", "deploymentZones", "buildings", "materials", "heights")},
        source_road_ids_sha256=sha256(sorted(road["id"] for road in source["roads"])),
        source_roads_sha256=sha256(source["roads"]), derived_nodes=len(nodes) - len(source["nodes"]),
        visual_coverage_status="not_verified", warnings=["桥头连接与中央桥梁任务点高度需复核", "建筑保留显示变换，未转换为精确占地"],
        assumptions=["源道路未配置方向，按测试双向保存", "源道路未配置通行政策，保存为未知"]))
    result["content_sha256"] = sha256(result)
    validate(result)
    return result


def validate(value: dict) -> None:
    """检查转换结果、道路引用、高程完整性和内容摘要。"""
    source = value["source_payload"]
    validate_source(source)
    require(value["elevation_grid"]["values"] == source["heights"], "MAP_HEIGHT_CHANGED", "入库高程与源数据不一致", 422)
    nodes = {node["node_id"]: node for node in value["road_nodes"]}
    edges = value["road_edges"]
    require(len(nodes) == len(value["road_nodes"]) and len({edge["edge_id"] for edge in edges}) == len(edges),
            "MAP_ID_DUPLICATE", "转换道路或节点身份重复", 422)
    require(sorted(edge["source_id"] for edge in edges) == sorted(road["id"] for road in source["roads"]),
            "MAP_ROADS_INCOMPLETE", "没有完整保存全部源道路", 422)
    originals = {road["id"]: road for road in source["roads"]}
    for edge in edges:
        require(edge["source_record"] == originals[edge["source_id"]] and edge["from_node_id"] in nodes and edge["to_node_id"] in nodes,
                "MAP_ROAD_REFERENCE_INVALID", "道路原字段或节点引用不一致", 422)
        require(len(edge["polyline"]) >= 2 and edge["polyline"][0] == nodes[edge["from_node_id"]]["position"]
                and edge["polyline"][-1] == nodes[edge["to_node_id"]]["position"], "MAP_ROAD_PROFILE_INVALID", "路面点与端点不一致", 422)
        require(float(edge["width_m"]) > 0 and float(edge["length_surface_m"]) > 0, "MAP_ROAD_INVALID", "道路宽度或长度无效", 422)
    require(value["buildings"] == source["buildings"] and value["materials"] == source["materials"], "MAP_SOURCE_CHANGED", "建筑或材质数据不完整", 422)
    require(value["content_sha256"] == sha256({key: item for key, item in value.items() if key != "content_sha256"}),
            "MAP_HASH_INVALID", "地图内容摘要不一致", 422)


@lru_cache(maxsize=1)
def current_map() -> dict:
    """每个服务进程仅转换一次当前源文件，不触发数据库写入。"""
    raw = MAP_PATH.read_bytes()
    source = json.loads(raw, object_pairs_hook=unique_object)
    return convert(source, hashlib.sha256(raw).hexdigest())


@lru_cache(maxsize=1)
def asset_manifest() -> dict:
    """记录完整地图数据及外部几何文件摘要，二进制资源保留项目相对引用。"""
    root = MAP_PATH.parents[6]
    resources = []
    battlefield = MAP_PATH.parents[2] / "Battlefield"
    paths = sorted([*MAP_PATH.parent.glob("*"), *battlefield.rglob("*")])
    for path in paths:
        if not path.is_file():
            continue
        with path.open("rb") as resource:
            digest = hashlib.file_digest(resource, "sha256").hexdigest()
        resources.append(dict(resource_path=path.relative_to(root).as_posix(), sha256=digest, bytes=path.stat().st_size))
    return dict(map_id=current_map()["map_id"], content_sha256=current_map()["content_sha256"],
                source_file_sha256=current_map()["import_audit"]["source_sha256"], resources=resources,
                binary_resources_in_database=False, geometry_policy="source_geometry_external")
