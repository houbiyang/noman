"""生成青岚河谷的统一布局、原生网格数据和 OBJ；仅使用 Python 标准库。"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from pathlib import Path


CLIENT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = CLIENT_ROOT / "Assets/Sandbox/Resources/Maps/QinglanValley"
EXPORT_ROOT = CLIENT_ROOT / "MapPreview"
WIDTH = 120.0
LENGTH = 80.0
COLUMNS = 144
ROWS = 96
DISPLAY_SCALE = 3.0
PALETTE = {
    "GroundLow": "#7b875e", "GroundHigh": "#65734e", "Rock": "#8d947d",
    "Water": "#4c8290", "Road": "#b7ac89", "Bridge": "#9e9f8f",
    "Wall": "#d1c8aa", "Roof": "#66706a", "Forest": "#31553c",
    "ForestLight": "#4c6c42", "Wood": "#6e6347", "Base": "#273e40",
    "Red": "#be7762", "Blue": "#669bb5", "Mission": "#d8c17b",
    "White": "#e4dfc4", "Crop": "#a1a66d", "Grid": "#8e9b78",
    "Bank": "#aa9f7d", "Mud": "#736b50", "Soil": "#8c7955",
    "FieldGreen": "#7f9750", "FieldGold": "#b4aa68", "Plaster": "#b9b29a",
    "Tile": "#885d4a", "Asphalt": "#686e68", "Glass": "#455e66",
    "Meadow": "#858c64", "MeadowDry": "#93906a", "Earth": "#898270", "Ash": "#48463f",
}


def point(identifier: str, name: str, kind: str, x: float, z: float, partner: str = "") -> dict:
    """创建具有稳定身份的公开地图对象。"""
    return {"id": identifier, "name": name, "kind": kind, "x": x, "z": z, "partnerId": partner}


NODES = [
    point("R_EXIT", "红方外接道路", "road", 12, 40, "B_EXIT"),
    point("R_GATE", "红方兵营出入口", "road", 22, 40, "B_GATE"),
    point("R_CENTER", "西岸路口", "road", 34, 40, "B_CENTER"),
    point("R_NORTH", "西北路口", "road", 34, 60, "B_SOUTH"),
    point("R_SOUTH", "西南路口", "road", 34, 20, "B_NORTH"),
    point("WN", "北桥西端", "road", 52, 60, "ES"),
    point("EN", "北桥东端", "road", 68, 60, "WS"),
    point("WC", "中桥西端", "road", 52, 40, "EC"),
    point("EC", "中桥东端", "road", 68, 40, "WC"),
    point("WS", "南桥西端", "road", 52, 20, "EN"),
    point("ES", "南桥东端", "road", 68, 20, "WN"),
    point("B_CENTER", "东岸路口", "road", 86, 40, "R_CENTER"),
    point("B_NORTH", "东北路口", "road", 86, 60, "R_SOUTH"),
    point("B_SOUTH", "东南路口", "road", 86, 20, "R_NORTH"),
    point("B_GATE", "蓝方兵营出入口", "road", 98, 40, "R_GATE"),
    point("B_EXIT", "蓝方外接道路", "road", 108, 40, "R_EXIT"),
    point("R_EVAC", "红方撤离点", "road", 14, 32, "B_EVAC"),
    point("B_EVAC", "蓝方撤离点", "road", 106, 48, "R_EVAC"),
    point("R_SUPPLY", "红方保障节点", "road", 28, 48, "B_SUPPLY"),
    point("B_SUPPLY", "蓝方保障节点", "road", 92, 32, "R_SUPPLY"),
    point("R_FACILITY", "西岸设施接入口", "road", 42, 52, "B_FACILITY"),
    point("B_FACILITY", "东岸设施接入口", "road", 78, 28, "R_FACILITY"),
]
EDGE_PAIRS = [
    ("R_EXIT", "R_GATE"), ("R_GATE", "R_CENTER"), ("R_CENTER", "WC"), ("WC", "EC"),
    ("EC", "B_CENTER"), ("B_CENTER", "B_GATE"), ("B_GATE", "B_EXIT"),
    ("R_CENTER", "R_NORTH"), ("R_CENTER", "R_SOUTH"),
    ("B_CENTER", "B_NORTH"), ("B_CENTER", "B_SOUTH"),
    ("R_NORTH", "WN"), ("WN", "EN"), ("EN", "B_NORTH"),
    ("R_SOUTH", "WS"), ("WS", "ES"), ("ES", "B_SOUTH"),
    ("R_EXIT", "R_EVAC"), ("B_EXIT", "B_EVAC"),
    ("R_GATE", "R_SUPPLY"), ("B_GATE", "B_SUPPLY"),
    ("R_NORTH", "R_FACILITY"), ("B_SOUTH", "B_FACILITY"),
]
NODES.extend([
    point("R_VILLAGE", "西岸村镇路口", "road", 34, 70, "B_VILLAGE"),
    point("B_VILLAGE", "东岸村镇路口", "road", 86, 10, "R_VILLAGE"),
    point("R_FARM", "西岸农田路口", "road", 26, 25, "B_FARM"),
    point("B_FARM", "东岸农田路口", "road", 94, 55, "R_FARM"),
    point("R_LOGISTICS", "红方仓储接入口", "road", 25, 61, "B_LOGISTICS"),
    point("B_LOGISTICS", "蓝方仓储接入口", "road", 95, 19, "R_LOGISTICS"),
    point("R_CENTRAL", "西岸中央村落", "road", 46, 34, "B_CENTRAL"),
    point("B_CENTRAL", "东岸中央村落", "road", 74, 46, "R_CENTRAL"),
])
EDGE_PAIRS.extend([("R_NORTH", "R_VILLAGE"), ("B_SOUTH", "B_VILLAGE"),
                   ("R_SOUTH", "R_FARM"), ("B_NORTH", "B_FARM"),
                   ("R_GATE", "R_LOGISTICS"), ("B_GATE", "B_LOGISTICS"),
                   ("R_CENTER", "R_CENTRAL"), ("B_CENTER", "B_CENTRAL")])
NODE_LOOKUP = {node["id"]: node for node in NODES}
BRIDGES = {frozenset(pair) for pair in [("WN", "EN"), ("WC", "EC"), ("WS", "ES")]}
MARKERS = [
    point("TASK_A", "A · 西岸检查点", "task", 34, 40, "TASK_C"),
    point("TASK_B", "B · 中央桥梁", "task", 60, 40, "TASK_B"),
    point("TASK_C", "C · 东岸检查点", "task", 86, 40, "TASK_A"),
    point("RED_CAMP", "红方兵营", "camp", 17, 40, "BLUE_CAMP"),
    point("BLUE_CAMP", "蓝方兵营", "camp", 103, 40, "RED_CAMP"),
    point("RED_SUPPLY", "红方补给站", "supply", 28, 48, "BLUE_SUPPLY"),
    point("BLUE_SUPPLY", "蓝方补给站", "supply", 92, 32, "RED_SUPPLY"),
    point("RED_EVAC", "红方撤离点", "evacuation", 14, 32, "BLUE_EVAC"),
    point("BLUE_EVAC", "蓝方撤离点", "evacuation", 106, 48, "RED_EVAC"),
    point("WEST_FACILITY", "西岸关键设施", "facility", 42, 52, "EAST_FACILITY"),
    point("EAST_FACILITY", "东岸关键设施", "facility", 78, 28, "WEST_FACILITY"),
]


def river_center(z: float) -> float:
    """计算非对称曲流河道，最终再投影到自然河谷布局。"""
    return 60 + 2.8 * math.sin(z * 0.08 + 0.7) + 1.2 * math.sin(z * 0.23 + 0.4)


def project_position(x: float, z: float) -> tuple[float, float]:
    """将业务平面平滑投影到非对称河谷，边界保持在公开地图范围内。"""
    return (x + math.sin(math.pi*x/WIDTH)*(6*math.sin(z*0.065+0.8)+2*math.sin(x*0.15+z*0.12)),
            z + math.sin(math.pi*z/LENGTH)*4*math.sin(x*0.055+0.4))


def unproject_position(x: float, z: float) -> tuple[float, float]:
    """反解显示平面，为规则高度网格采样同一片地表。"""
    source_x, source_z = x, z
    for _ in range(18):
        projected_x, projected_z = project_position(source_x, source_z)
        source_x += x-projected_x
        source_z += z-projected_z
    return source_x, source_z


def segment_distance(x: float, z: float, start: dict, end: dict) -> float:
    """返回点到一段道路中心线的平面距离。"""
    direction_x, direction_z = end["x"] - start["x"], end["z"] - start["z"]
    parameter = max(0, min(1, ((x - start["x"]) * direction_x + (z - start["z"]) * direction_z) / (direction_x ** 2 + direction_z ** 2)))
    return math.hypot(x - start["x"] - parameter * direction_x, z - start["z"] - parameter * direction_z)


def terrain_height(x: float, z: float) -> float:
    """计算显示地形高度；不作为通行、视野或对抗规则。"""
    river_distance = abs(x - river_center(z))
    if river_distance < 3.7:
        return -0.25
    ridge = 9*math.exp(-((x-19)/24)**2-((z-72)/13)**2)
    ridge += 4.5*math.exp(-((x-98)/17)**2-((z-10)/20)**2)
    hill = 0.3 + 1.4*math.exp(-((x-45)/9)**2-((z-29)/11)**2)
    hill += 0.9*math.exp(-((x-82)/13)**2-((z-56)/10)**2)
    hill += 0.12*math.sin(x*0.43+z*0.27)*math.sin(z*0.31)
    elevation = 0.25 + ridge * min(1, (river_distance - 3.7) / 12) + hill
    road_distance = min(segment_distance(x, z, NODE_LOOKUP[start], NODE_LOOKUP[end]) for start, end in EDGE_PAIRS)
    flatten = min(1, max(0, (road_distance - 1.4) / 2.6))
    if 8 <= x <= 28 and 28 <= z <= 52 or 92 <= x <= 112 and 28 <= z <= 52:
        flatten = 0
    bank_blend = min(1, (river_distance - 3.7) / 2)
    return -0.25*(1-bank_blend)+(0.32+(elevation-0.32)*flatten)*bank_blend


def deck_height(x: float, z: float) -> float:
    """将显示道路衔接到桥面；没有创建业务可通行结论。"""
    if min(abs(z - bridge_z) for bridge_z in (20, 40, 60)) < 0.1 and 47 <= x <= 73:
        transition = max(0, min(1, (x - 47) / 5, (73 - x) / 5))
        return 0.42 + transition * 0.85
    return terrain_height(x, z) + 0.025


class Geometry:
    """按图层与材质合并静态网格，避免为每株树生成独立资源。"""

    def __init__(self) -> None:
        self.parts: dict[tuple[str, str], dict] = {}
        self.buildings: list[dict] = []
        self.tree_positions: list[tuple[float,float,float]] = []

    def house(self, layer: str, x: float, z: float, width: float, depth: float, height: float, damage: int, yaw: float) -> None:
        """记录 Blender 建筑的同源放置数据，替换无洞口的盒体房屋。"""
        projected_x, projected_z = project_position(x, z)
        self.buildings.append({"id": f"BUILDING_{len(self.buildings)+1:03}", "layer": layer,
                               "model": f"House_{damage}", "damage": damage,
                               "x": round(projected_x*DISPLAY_SCALE,5), "z": round(projected_z*DISPLAY_SCALE,5),
                               "y": round((terrain_height(x,z)-0.04)*DISPLAY_SCALE,5), "yaw": round(yaw,3),
                               "scaleX": round(width*DISPLAY_SCALE/7.5,5), "scaleY": round(height*DISPLAY_SCALE/5.4,5),
                               "scaleZ": round(depth*DISPLAY_SCALE/6.3,5)})

    def face(self, layer: str, material: str, vertices: list[tuple[float, float, float]]) -> None:
        """写入一个保持顶点绕序的凸面。"""
        key = (layer, material)
        part = self.parts.setdefault(key, {"name": layer + "_" + material, "layer": layer, "material": material, "vertices": [], "triangles": []})
        offset = len(part["vertices"]) // 3
        for vertex in vertices:
            part["vertices"].extend(round(component, 5) for component in vertex)
        for index in range(1, len(vertices) - 1):
            part["triangles"].extend((offset, offset + index, offset + index + 1))

    def box(self, layer: str, material: str, x: float, y: float, z: float, width: float, height: float, depth: float) -> None:
        """建立以给定位置为中心的实心方盒。"""
        corners = [(x + horizontal * width / 2, y + vertical * height / 2, z + longitudinal * depth / 2)
                   for horizontal, vertical, longitudinal in [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1), (-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1)]]
        for indices in [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (4, 5, 6, 7), (3, 2, 1, 0)]:
            self.face(layer, material, [corners[index] for index in reversed(indices)])

    def ribbon(self, layer: str, material: str, start: tuple[float, float], end: tuple[float, float], width: float, height_function) -> None:
        """沿平面线段采样一条贴地带，避免长直段穿入山坡。"""
        delta_x, delta_z = end[0] - start[0], end[1] - start[1]
        distance = math.hypot(delta_x, delta_z)
        segments = max(1, math.ceil(distance / 1.5))
        normal_x, normal_z = -delta_z / distance * width / 2, delta_x / distance * width / 2
        for index in range(segments):
            positions = []
            for parameter, direction in [(index / segments, -1), (index / segments, 1), ((index + 1) / segments, 1), ((index + 1) / segments, -1)]:
                position_x = start[0] + parameter * delta_x + direction * normal_x
                position_z = start[1] + parameter * delta_z + direction * normal_z
                positions.append((position_x, height_function(position_x, position_z), position_z))
            self.face(layer, material, positions)

    def cylinder(self, layer: str, material: str, x: float, y: float, z: float, radius: float, height: float, sides: int = 10) -> None:
        """建立低多边形圆柱或圆形地面标记。"""
        upper = [(x + math.cos(index * math.tau / sides) * radius, y + height, z + math.sin(index * math.tau / sides) * radius) for index in range(sides)]
        lower = [(position[0], y, position[2]) for position in upper]
        self.face(layer, material, list(reversed(upper)))
        for index in range(sides):
            following = (index + 1) % sides
            self.face(layer, material, [lower[index], upper[index], upper[following], lower[following]])

    def tree(self, x: float, z: float, size: float) -> None:
        """先记录树木候选位置，待建筑布局完整后排除穿过房屋的树冠。"""
        self.tree_positions.append((x,z,size))

    def build_trees(self) -> None:
        """保留建筑占地和树冠净空，避免新战损模型与原植被相互穿插。"""
        footprints=[(*unproject_position(building["x"]/DISPLAY_SCALE,building["z"]/DISPLAY_SCALE),
                     math.hypot(building["scaleX"]*7.5,building["scaleZ"]*6.3)/6+0.3) for building in self.buildings]
        for x,z,size in self.tree_positions:
            if any(math.hypot(x-center_x,z-center_z)<radius+size*0.65 for center_x,center_z,radius in footprints):
                continue
            self.draw_tree(x,z,size)

    def draw_tree(self, x: float, z: float, size: float) -> None:
        """建立封闭的阔叶树冠，避免从俯视角度看到顶部空洞。"""
        y = terrain_height(x, z)
        self.box("environment", "Wood", x, y+size*0.45,z,0.12,size*0.9,0.12)
        rings=[]
        for level,radius in ((0.55,0.3),(0.85,0.58),(1.15,0.45),(1.38,0.08)):
            rings.append([(x+math.cos(index*math.tau/9)*size*radius*(1+0.12*math.sin(index*2+x)),
                           y+size*level,z+math.sin(index*math.tau/9)*size*radius) for index in range(9)])
        for level in range(3):
            for index in range(9):
                following=(index+1)%9
                self.face("environment","Forest" if (index+level)%3 else "ForestLight",
                          [rings[level][index],rings[level+1][index],rings[level+1][following],rings[level][following]])
        self.face("environment","Forest",list(reversed(rings[-1])))
        self.face("environment","Forest",rings[0])


def add_landscape_context(geometry: Geometry) -> None:
    """延伸地平线、远山与上下游，避免可操作区域呈现悬浮矩形底座。"""
    def context_height(x,z):
        edge_x,edge_z=max(0,min(120,x)),max(0,min(80,z))
        separation=math.hypot(x-edge_x,z-edge_z)
        blend=min(1,separation/18)
        hills=4+9*math.sin(x*0.025+0.9)**2+7*math.sin(z*0.035-0.2)**2
        hills *= min(1,max(0,(abs(x-river_center(z))-3.7)/14))
        if abs(x-river_center(z))<3.7:
            hills=-0.25
        return terrain_height(edge_x,edge_z)*(1-blend)+hills*blend
    for row in range(-22,54):
        z=row*2.5
        for column in range(-28,76):
            x=column*2.5
            if 0<=x<120 and 0<=z<80:
                continue
            vertices=[(horizontal,context_height(horizontal,longitudinal),longitudinal)
                      for horizontal,longitudinal in ((x,z),(x,z+2.5),(x+2.5,z+2.5),(x+2.5,z))]
            geometry.face("landscape","GroundHigh",vertices)
    for z in list(range(-55,0))+list(range(80,135)):
        geometry.face("water","Water",[(river_center(z)-3.7,0.13,z),(river_center(z+1)-3.7,0.13,z+1),
                                       (river_center(z+1)+3.7,0.13,z+1),(river_center(z)+3.7,0.13,z)])


def create_geometry() -> Geometry:
    """生成共享坐标下的完整地图模型。"""
    geometry = Geometry()
    for row in range(ROWS):
        for column in range(COLUMNS):
            x, z = column * WIDTH / COLUMNS, row * LENGTH / ROWS
            coordinates = [(x, z), (x, z + LENGTH / ROWS), (x + WIDTH / COLUMNS, z + LENGTH / ROWS), (x + WIDTH / COLUMNS, z)]
            vertices = [(horizontal, terrain_height(horizontal, longitudinal), longitudinal) for horizontal, longitudinal in coordinates]
            geometry.face("terrain", "GroundLow", vertices)
    add_landscape_context(geometry)
    for index in range(80):
        lower_z, upper_z = float(index), float(index + 1)
        geometry.face("water", "Water", [(river_center(lower_z) - 3.7, 0.13, lower_z), (river_center(upper_z) - 3.7, 0.13, upper_z), (river_center(upper_z) + 3.7, 0.13, upper_z), (river_center(lower_z) + 3.7, 0.13, lower_z)])
        for direction in (-1, 1):
            geometry.ribbon("riverbanks", "Bank", (river_center(lower_z) + direction * 4.9, lower_z),
                            (river_center(upper_z) + direction * 4.9, upper_z), 1.6, lambda x, z: terrain_height(x, z) + 0.03)
    for start_id, end_id in EDGE_PAIRS:
        if frozenset((start_id, end_id)) in BRIDGES:
            continue
        start, end = NODE_LOOKUP[start_id], NODE_LOOKUP[end_id]
        geometry.ribbon("routes", "Road", (start["x"], start["z"]), (end["x"], end["z"]), 2.2, deck_height)
    for bridge_z in (20, 40, 60):
        geometry.box("routes", "Bridge", 60, 1.0, bridge_z, 16, 0.5, 3.2)
        for rail_z in (bridge_z - 1.6, bridge_z + 1.6):
            geometry.box("routes", "Roof", 60, 1.65, rail_z, 16.2, 0.25, 0.15)
            for post_x in (52.4, 56, 60, 64, 67.6):
                geometry.box("routes", "Bridge", post_x, 1.4, rail_z, 0.15, 0.8, 0.15)
        for support_x in (55, 65):
            geometry.box("routes", "Rock", support_x, 0.3, bridge_z, 0.8, 1.4, 2.6)
    for side, camp_x in [("Red", 17), ("Blue", 103)]:
        ground = terrain_height(camp_x, 40)
        geometry.box("facilities", "Base", camp_x, ground + 0.15, 40, 8, 0.3, 6)
        geometry.box("facilities", side, camp_x, ground + 1.45, 40, 5.8, 2.6, 3.9)
        geometry.box("facilities", "Roof", camp_x, ground + 2.95, 40, 6.2, 0.5, 4.3)
        geometry.box("facilities", "Wall", camp_x + (2.91 if side == "Red" else -2.91), ground + 0.9, 40, 0.1, 1.5, 1.5)
        zone_min_x, zone_max_x = (8, 28) if side == "Red" else (92, 112)
        corners = [(zone_min_x, 28), (zone_min_x, 52), (zone_max_x, 52), (zone_max_x, 28)]
        for index, start in enumerate(corners):
            geometry.ribbon("deployment", side, start, corners[(index + 1) % 4], 0.27, lambda x, z: terrain_height(x, z) + 0.16)
        landing_x, landing_z = (17, 47) if side == "Red" else (103, 33)
        geometry.cylinder("facilities", "Roof", landing_x, terrain_height(landing_x, landing_z) + 0.04, landing_z, 2.2, 0.08, 16)
        geometry.box("facilities", "White", landing_x, 0.54, landing_z, 2.6, 0.04, 0.2)
        geometry.box("facilities", "White", landing_x - 1.2, 0.54, landing_z, 0.2, 0.04, 2.1)
        geometry.box("facilities", "White", landing_x + 1.2, 0.54, landing_z, 0.2, 0.04, 2.1)
    for marker in MARKERS:
        x, z, kind = marker["x"], marker["z"], marker["kind"]
        y = deck_height(x, z) if kind == "task" else terrain_height(x, z)
        if kind == "task":
            geometry.cylinder("markers", "Mission", x, y + 0.1, z, 1.3, 0.12)
            geometry.box("markers", "Wood", x, y + 1.8, z, 0.12, 3.2, 0.12)
            geometry.box("markers", "Mission", x + 0.65, y + 3, z, 1.3, 0.65, 0.08)
        elif kind == "evacuation":
            geometry.cylinder("markers", "White", x, y + 0.1, z, 1.9, 0.08, 16)
            geometry.cylinder("markers", "Blue" if marker["id"].startswith("BLUE") else "Red", x, y + 0.2, z, 1.5, 0.08, 16)
            geometry.box("markers", "White", x, y + 0.32, z, 2, 0.04, 0.35)
            geometry.box("markers", "White", x, y + 0.32, z, 0.35, 0.04, 2)
        elif kind == "supply":
            for offset_x, offset_z in [(-1, 0), (0.4, 0), (-0.3, 1.3)]:
                geometry.box("facilities", "Wood", x + offset_x, y + 0.55, z + offset_z, 1.1, 1.1, 1.1)
            geometry.box("facilities", "Mission", x - 0.3, y + 1.4, z + 0.6, 3.5, 0.15, 3)
        elif kind == "facility":
            geometry.box("facilities", "Wall", x, y + 1.3, z, 4, 2.6, 3)
            geometry.box("facilities", "Roof", x, y + 2.75, z, 4.3, 0.35, 3.3)
            geometry.cylinder("facilities", "Bridge", x + 2.6, y, z, 0.65, 2.3)
    for index in range(12):
        x = 35 + index % 3 * 3.7
        z = 47 + index // 3 * 3.2
        for house_x, house_z in ((x, z), (80+index%4*2.9, 23+index//4*3.6)):
            facility_x, facility_z = (42, 52) if house_x < 60 else (78, 28)
            if abs(house_x - facility_x) < 4 and abs(house_z - facility_z) < 3.8:
                continue
            height = 1.1 + index % 3 * 0.3
            damage = (index+(2 if house_x>60 else 0))%4
            geometry.house("settlement",house_x,house_z,2.3,1.8,height,damage,8*math.sin(index*1.7))
    for index in range(360):
        x, z = 3 + index * 37 % 54, 3 + index * 29 % 74
        if 7 <= x <= 43 and 7 <= z <= 25 or 27 <= x <= 49 and 62 <= z <= 77:
            continue
        if abs(x - river_center(z)) < 9 or 7 <= x <= 47 and 17 <= z <= 64:
            continue
        if min(segment_distance(x, z, NODE_LOOKUP[start], NODE_LOOKUP[end]) for start, end in EDGE_PAIRS) < 2.8:
            continue
        size = 1.8 + index % 5 * 0.18
        geometry.tree(x, z, size)
        other_x, other_z = 72 + index*23 % 45, 4 + index*17 % 72
        if abs(other_x-river_center(other_z)) > 9 and not (75 < other_x < 110 and 25 < other_z < 63):
            if min(segment_distance(other_x,other_z,NODE_LOOKUP[start],NODE_LOOKUP[end]) for start,end in EDGE_PAIRS)>3:
                geometry.tree(other_x, other_z, size*0.85)
    for x in range(0, 121, 10):
        geometry.ribbon("grid", "Grid", (x, 0), (x, 80), 0.025, lambda horizontal, longitudinal: max(terrain_height(horizontal, longitudinal), 0.15) + 0.035)
    for z in range(0, 81, 10):
        geometry.ribbon("grid", "Grid", (0, z), (120, z), 0.025, lambda horizontal, longitudinal: max(terrain_height(horizontal, longitudinal), 0.15) + 0.035)
    add_rural_environment(geometry)
    add_central_environment(geometry)
    geometry.build_trees()
    add_surface_damage(geometry)
    return geometry


def add_surface_damage(geometry: Geometry) -> None:
    """在已有受损建筑周围加入不规则烧蚀地表，只改变视觉表现。"""
    for index, building in enumerate(geometry.buildings):
        if building["damage"]<2:
            continue
        x,z=unproject_position(building["x"]/DISPLAY_SCALE,building["z"]/DISPLAY_SCALE)
        radius=1.4+building["damage"]*0.4
        for segment in range(14):
            positions=[(x,terrain_height(x,z)+0.022,z)]
            for vertex in (segment,segment+1):
                angle=vertex*math.tau/14
                extent=radius*(0.85+0.15*math.sin(vertex*2.7+index))
                horizontal,vertical=x+math.cos(angle)*extent,z+math.sin(angle)*extent
                positions.append((horizontal,terrain_height(horizontal,vertical)+0.025,vertical))
            geometry.face("damage","Ash",list(reversed(positions)))


def add_central_environment(geometry: Geometry) -> None:
    """为中部作战区建立沿道路与河谷分布的院落、田块、土路及植被。"""
    for east_bank in (False, True):
        def locate(x, z):
            return (117-x*0.92, 75-z*0.88) if east_bank else (x, z)

        for index in range(24):
            x, z = locate(43 + index % 3 * 3.7+0.65*math.sin(index*2.4), 27 + index // 3 * 3.8+0.6*math.sin(index*1.7))
            if abs(x-river_center(z)) < 7:
                continue
            road_distance = min(segment_distance(x,z,NODE_LOOKUP[start],NODE_LOOKUP[end]) for start,end in EDGE_PAIRS)
            if road_distance < 2.8:
                continue
            y = terrain_height(x,z)
            height = 1.2 + index%3*0.3
            damage = (index+(1 if east_bank else 2))%4
            geometry.house("central_village",x,z,2.1,1.7,height,damage,12*math.sin(index*1.9)+(16 if east_bank else -5))
            for wall_x in (x-1.5,x+1.5):
                geometry.box("central_village", "Wall", wall_x,y+0.3,z,0.1,0.6,2.8)
        for start,end in [((46,34),(50,27)),((46,34),(49,52)),((40,28),(49,28)),((43,49),(49,49))]:
            geometry.ribbon("routes", "Soil", locate(*start),locate(*end),0.8,lambda x,z:terrain_height(x,z)+0.015)
        for index in range(4):
            x,z=locate(40+index%2*6,22+index//2*35)
            for row in range(7):
                geometry.ribbon("farmland", "FieldGold" if index%2 else "FieldGreen",(x-2,z-2+row*0.6),(x+2,z-2+row*0.6),0.35,lambda x,z:terrain_height(x,z)+0.08)
            geometry.ribbon("ditches","Mud",(x-2.5,z-2.5),(x+2.5,z-2.5),0.35,lambda x,z:terrain_height(x,z)+0.03)
        for index in range(32):
            x,z=locate(47+(index%3)*2,15+index*1.6)
            if abs(x-river_center(z))<5.5 or min(segment_distance(x,z,NODE_LOOKUP[start],NODE_LOOKUP[end]) for start,end in EDGE_PAIRS)<2.7:
                continue
            if index%4==0:
                geometry.tree(x,z,1.3+index%3*0.2)
            else:
                geometry.cylinder("scrub","ForestLight",x,terrain_height(x,z),z,0.45,0.35,7)


def add_rural_environment(geometry: Geometry) -> None:
    """补齐虚构乡村河谷的农田、村镇、院落、保障设施及连续支路。"""
    surface = lambda x, z: terrain_height(x, z) + 0.015
    # 东岸采用不同的间距、朝向和覆盖范围，不复制西岸街区。
    for east_bank in (False, True):
        def locate(x, z):
            return (116-x*0.88, 77-z*0.9) if east_bank else (x, z)

        for index in range(8):
            x, z = locate(11 + index % 4 * 9+0.8*math.sin(index*1.8), 11 + index // 4 * 9+0.6*math.cos(index*2.4))
            material = "FieldGreen" if index % 3 else "FieldGold"
            geometry.ribbon("farmland", "Soil", (x - 3.8, z), (x + 3.8, z), 6.7, surface)
            for row in range(9):
                geometry.ribbon("farmland", material, (x - 3.5, z - 2.8 + row * 0.7), (x + 3.5, z - 2.8 + row * 0.7), 0.42, lambda x, z: surface(x, z) + 0.06)
            geometry.ribbon("ditches", "Mud", (x - 4, z - 3.7), (x + 4, z - 3.7), 0.4, surface)
        for start, end in [((6, 25), (46, 25)), ((6, 25), (12, 40)), ((26, 25), (34, 20)),
                           ((26, 25), (26, 8)), ((28, 63), (48, 63)), ((34, 60), (34, 70))]:
            geometry.ribbon("routes", "Road", locate(*start), locate(*end), 1.2, surface)
        for index in range(18):
            x, z = locate(29 + index % 6 * 3.6+0.65*math.sin(index*2.1), 64 + index // 6 * 3.8+0.7*math.cos(index*1.8))
            y = terrain_height(x, z)
            height = 1.6 + index % 3 * 0.35
            geometry.house("village",x,z,2.5,2.1,height,(index+(1 if east_bank else 0))%4,7*math.sin(index*2.1))
            geometry.box("village", "Wall", x-1.6, y+0.35, z, 0.12, 0.7, 3)
            geometry.box("village", "Wall", x+1.6, y+0.35, z, 0.12, 0.7, 3)
        for x, z in (locate(25, 57), locate(25, 61)):
            y = terrain_height(x, z)
            geometry.box("logistics", "Wall", x, y+1.4, z, 5, 2.8, 3)
            geometry.box("logistics", "Roof", x, y+2.9, z, 5.4, 0.25, 3.4)
            geometry.box("logistics", "Asphalt", x, y+0.03, z-3, 5, 0.06, 2.5)
            for offset in (-1.5, 0, 1.5):
                geometry.box("logistics", "Wood", x+offset, y+0.4, z-3, 1, 0.8, 0.8)
        geometry.ribbon("routes", "Road", locate(22, 40), locate(25, 61), 1.6, surface)
        # 池塘位于开阔低地，水面与泥岸独立于主河流。
        x, z = locate(47, 11)
        y = terrain_height(x, z)
        geometry.cylinder("ponds", "Mud", x, y+0.03, z, 3.6, 0.02, 20)
        geometry.cylinder("ponds", "Water", x, y+0.06, z, 3.1, 0.02, 20)
        for index in range(45):
            x, z = locate(4 + index * 13 % 47, 4 + index * 19 % 72)
            if 6 < z < 25 or 27 < x < 49 and 62 < z < 77:
                continue
            if min(segment_distance(x,z,NODE_LOOKUP[start],NODE_LOOKUP[end]) for start,end in EDGE_PAIRS) < 3:
                continue
            geometry.cylinder("scrub", "ForestLight", x, terrain_height(x,z), z, 0.6 + index%3*0.2, 0.45, 7)


def validate_layout() -> None:
    """核对道路连通、桥梁与非对称地表投影，不要求两岸镜像公平。"""
    adjacency = defaultdict(set)
    edge_set = {frozenset(pair) for pair in EDGE_PAIRS}
    for start, end in EDGE_PAIRS:
        adjacency[start].add(end)
        adjacency[end].add(start)
    pending = deque(["R_GATE"])
    visited = set()
    while pending:
        current = pending.popleft()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency[current] - visited)
    assert visited == set(NODE_LOOKUP), "道路节点不连通"
    assert len(BRIDGES) == 3
    for row in range(9):
        for column in range(13):
            x, z = column * 10, row * 10
            projected = project_position(x,z)
            restored = unproject_position(*projected)
            assert math.hypot(restored[0]-x,restored[1]-z)<0.00001


def main() -> None:
    """写入真实网格资产及其可复现布局，不需要 Unity 许可证。"""
    validate_layout()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    materials = [{"id": name, "red": int(color[1:3], 16) / 255, "green": int(color[3:5], 16) / 255, "blue": int(color[5:7], 16) / 255} for name, color in PALETTE.items()]
    definition = {
        "schemaVersion": 1, "mapId": "qinglan-valley-v1", "name": "青岚河谷", "width": WIDTH, "length": LENGTH,
        "coordinateUnit": "scene-unit", "heightColumns": COLUMNS + 1, "heightRows": ROWS + 1,
        "heights": [round(terrain_height(column * WIDTH / COLUMNS, row * LENGTH / ROWS), 5) for row in range(ROWS + 1) for column in range(COLUMNS + 1)],
        "nodes": NODES, "roads": [{"id": f"ROAD_{index + 1:02}", "startId": start, "endId": end, "isBridge": frozenset((start, end)) in BRIDGES,
                                   "width": 3.2 if frozenset((start, end)) in BRIDGES else 2.2,
                                   "surfaceHeight": 1.25 if frozenset((start, end)) in BRIDGES else 0} for index, (start, end) in enumerate(EDGE_PAIRS)],
        "points": MARKERS, "materials": materials,
        "deploymentZones": [{"id": "RED_DEPLOY", "side": "red", "minX": 8, "maxX": 28, "minZ": 28, "maxZ": 52}, {"id": "BLUE_DEPLOY", "side": "blue", "minX": 92, "maxX": 112, "minZ": 28, "maxZ": 52}],
    }
    geometry = create_geometry()
    parts = list(geometry.parts.values())
    # 业务演示坐标保持原值，地图与建筑按三倍显示尺度发布，旧存档无需改写。
    definition.update(mapId="qinglan-valley-v4", width=WIDTH*DISPLAY_SCALE,
                      length=LENGTH*DISPLAY_SCALE, displayScale=DISPLAY_SCALE)
    definition["buildings"] = geometry.buildings
    definition["heights"] = [round(terrain_height(*unproject_position(column*WIDTH/COLUMNS,row*LENGTH/ROWS))*DISPLAY_SCALE,5)
                             for row in range(ROWS+1) for column in range(COLUMNS+1)]
    for key, source in (("nodes",NODES),("points",MARKERS)):
        definition[key] = [dict(item, x=project_position(item["x"],item["z"])[0]*DISPLAY_SCALE,
                                z=project_position(item["x"],item["z"])[1]*DISPLAY_SCALE,partnerId="") for item in source]
    for road in definition["roads"]:
        road["width"] *= DISPLAY_SCALE
        road["surfaceHeight"] *= DISPLAY_SCALE
    for zone in definition["deploymentZones"]:
        samples=[project_position(zone["minX"]+(zone["maxX"]-zone["minX"])*column/20,
                                  zone["minZ"]+(zone["maxZ"]-zone["minZ"])*row/20) for column in range(21) for row in range(21)]
        zone.update(minX=min(position[0] for position in samples)*DISPLAY_SCALE,maxX=max(position[0] for position in samples)*DISPLAY_SCALE,
                    minZ=min(position[1] for position in samples)*DISPLAY_SCALE,maxZ=max(position[1] for position in samples)*DISPLAY_SCALE)
    for part in parts:
        for index in range(0,len(part["vertices"]),3):
            x,y,z=part["vertices"][index:index+3]
            position_x,position_z=project_position(x,z)
            part["vertices"][index:index+3]=[round(position_x*DISPLAY_SCALE,5),round(y*DISPLAY_SCALE,5),round(position_z*DISPLAY_SCALE,5)]
    serialized = json.dumps(definition, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for part in parts:
        assert len(part["vertices"]) % 3 == 0 and len(part["triangles"]) % 3 == 0
        assert len(part["vertices"]) // 3 < 65535
        assert all(0 <= index < len(part["vertices"]) // 3 for index in part["triangles"])
    canonical_geometry = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    definition["contentHash"] = hashlib.sha256(serialized.encode("utf-8") + b"\n" + canonical_geometry.encode("utf-8")).hexdigest()
    (OUTPUT_ROOT / "map-definition.json").write_text(json.dumps(definition, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    model = {"schemaVersion": 1, "mapId": definition["mapId"], "contentHash": definition["contentHash"], "parts": parts}
    (OUTPUT_ROOT / "map-geometry.json").write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    object_lines = ["# Qinglan Valley - fictional scene geometry", "mtllib QinglanValley.mtl"]
    offset = 1
    for part in parts:
        object_lines.extend(("o " + part["name"], "usemtl " + part["material"]))
        for index in range(0, len(part["vertices"]), 3):
            object_lines.append("v " + " ".join(str(value) for value in part["vertices"][index:index + 3]))
        for index in range(0, len(part["triangles"]), 3):
            object_lines.append("f " + " ".join(str(vertex + offset) for vertex in part["triangles"][index:index + 3]))
        offset += len(part["vertices"]) // 3
    (EXPORT_ROOT / "QinglanValley.obj").write_text("\n".join(object_lines) + "\n", encoding="utf-8")
    (EXPORT_ROOT / "QinglanValley.mtl").write_text("\n".join(f'newmtl {material["id"]}\nKd {material["red"]:.5f} {material["green"]:.5f} {material["blue"]:.5f}\nKa 0.2 0.2 0.2\n' for material in materials), encoding="utf-8")
    summary = {"mapId": definition["mapId"], "contentHash": definition["contentHash"], "nodes": len(NODES), "roads": len(EDGE_PAIRS), "bridges": len(BRIDGES), "points": len(MARKERS), "meshParts": len(parts), "triangles": sum(len(part["triangles"]) // 3 for part in parts),
               "buildings":len(geometry.buildings),"damageCounts":{str(damage):sum(building["damage"]==damage for building in geometry.buildings) for damage in range(4)}}
    (EXPORT_ROOT / "map-build-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
