"""用 Blender 创建十二公里原创策略地图、米制模型库及同源 Unity 分块，不安装依赖。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "Assets/Sandbox/Resources/Maps/QinglanValley"
MODELS = ROOT / "Assets/Sandbox/Resources/Battlefield/Models"
OUTPUT = ROOT / "MapPreview/Strategic12km"
SIZE = 12000
STEP = 50
TILE = 2000
MAP_ID = "qinglan-strategic-v6"
SEED = 20261102
PALETTE = {
    "GroundLow": (.43, .46, .34), "GroundHigh": (.45, .44, .37),
    "Grass": (.39, .44, .31), "FieldGreen": (.39, .44, .28), "FieldGold": (.55, .49, .34),
    "Mud": (.32, .30, .24), "Water": (.24, .31, .31), "Asphalt": (.26, .28, .28),
    "Paving": (.55, .55, .50), "Gravel": (.47, .46, .40), "Forest": (.24, .31, .21),
    "ForestLight": (.34, .39, .25), "ForestFloor": (.30, .34, .24), "Marking": (.68, .68, .59),
    "Red": (.62, .29, .24), "Blue": (.26, .45, .57),
    "Battle_Plaster": (.66, .64, .57), "Battle_Concrete": (.53, .53, .49),
    "Battle_Brick": (.46, .34, .27), "Battle_Tile": (.36, .31, .27),
    "Battle_Metal": (.36, .39, .37), "Battle_Ash": (.21, .23, .22),
    "Battle_Timber": (.30, .27, .22),
    "WallWarm": (.68, .52, .40), "Glass": (.20, .28, .32),
}
SURFACE = {
    "GroundLow": "Ground", "GroundHigh": "Ground", "Grass": "Ground", "FieldGreen": "Ground",
    "FieldGold": "Soil", "Mud": "Soil", "Water": "Water", "Asphalt": "Road", "Paving": "Concrete",
    "Gravel": "Soil", "Forest": "Foliage", "ForestLight": "Foliage", "ForestFloor": "Ground",
    "WallWarm": "Plaster", "Glass": "Ash",
}
REGIONS = [
    {"id": "CITY", "name": "中央城市", "x": 5100, "z": 5800, "radius": 1450, "purpose": "街区、交叉路口与公园交错"},
    {"id": "INDUSTRY", "name": "东岸工业货场", "x": 7800, "z": 5200, "radius": 1050, "purpose": "仓库、铁路与罐区形成独立侧翼"},
    {"id": "AIRFIELD", "name": "北部机场", "x": 8400, "z": 9200, "radius": 1600, "purpose": "跑道开阔带与外围机库分离"},
    {"id": "WEST_TOWN", "name": "西南镇", "x": 3000, "z": 3200, "radius": 700, "purpose": "村镇与田间道路连接南部通路"},
    {"id": "NORTH_TOWN", "name": "北岸镇", "x": 4200, "z": 8700, "radius": 700, "purpose": "林地与北桥之间的交通节点"},
    {"id": "EAST_TOWN", "name": "东南村", "x": 8800, "z": 2900, "radius": 650, "purpose": "田野与东岸丘陵连接"},
]
FORESTS = [(1600, 8500, 1650, 2200), (2300, 5900, 1100, 1150), (10400, 6100, 1200, 1850),
           (5800, 10300, 1300, 1050), (6600, 1900, 1100, 1150), (9700, 1150, 1350, 850),
           (1600, 1300, 1050, 850), (10800, 10000, 800, 1700)]


def river_center(position_z: float) -> float:
    """计算贯穿全图的非对称河流中心线，单位为米。"""
    return 6500 + 440 * math.sin(position_z / 1600 + .2) + 150 * math.sin(position_z / 590)


def river_half_width(position_z: float) -> float:
    """河面随河段变化，避免等宽直槽。"""
    return 190 + 40 * math.sin(position_z / 820 + .5)


def tributary_center(position_x: float, northern: bool) -> float:
    """两条支流分别汇入主河的北段和南段。"""
    return (7650 if northern else 3750)+180*math.sin(position_x/720)+70*math.sin(position_x/270)


def raw_height(position_x: float, position_z: float) -> float:
    """生成外围丘陵、河谷和平整建设用地的米制高程。"""
    hills = 125 * math.exp(-((position_x-1450)/1850)**2-((position_z-10000)/2000)**2)
    hills += 180 * math.exp(-((position_x-10800)/1800)**2-((position_z-7100)/2100)**2)
    hills += 65 * math.exp(-((position_x-8300)/1800)**2-((position_z-900)/1200)**2)
    undulation = 6*math.sin(position_x/850)*math.sin(position_z/1200) + 3*math.sin((position_x+position_z)/410)
    height = 20 + hills + undulation
    height += 155*math.exp(-((position_x-3200)/700)**2-((position_z-6900)/900)**2)
    height += 135*math.exp(-((position_x-8500)/850)**2-((position_z-7100)/700)**2)
    height -= 16*math.exp(-((position_x-4800)/650)**2-((position_z-3400)/550)**2)
    for region in REGIONS + [
        {"x":1420,"z":4300,"radius":340}, {"x":10520,"z":8100,"radius":400}
    ]:
        distance = math.hypot(position_x-region["x"], position_z-region["z"])
        factor = max(0, min(1, (distance-region["radius"])/300))
        height = 20 + (height-20)*factor
    shore = abs(position_x-river_center(position_z)) - river_half_width(position_z)
    if position_x<river_center(position_z):
        shore=min(shore,abs(position_z-tributary_center(position_x,True))-65)
    else:
        shore=min(shore,abs(position_z-tributary_center(position_x,False))-75)
    if shore < 0:
        return -6
    return -6 + (height+6)*min(1, shore/95)


HEIGHTS = [[round(raw_height(column*STEP, row*STEP), 3) for column in range(SIZE//STEP+1)]
           for row in range(SIZE//STEP+1)]


def height(position_x: float, position_z: float) -> float:
    """沿地形实际三角形插值，避免双线性曲面与网格之间出现穿插。"""
    column, row = max(0, min(SIZE, position_x))/STEP, max(0, min(SIZE, position_z))/STEP
    lower_x, lower_z = min(int(column), SIZE//STEP-1), min(int(row), SIZE//STEP-1)
    horizontal, vertical = column-lower_x, row-lower_z
    if vertical>=horizontal:
        return HEIGHTS[lower_z][lower_x]*(1-vertical)+HEIGHTS[lower_z+1][lower_x]*(vertical-horizontal)+HEIGHTS[lower_z+1][lower_x+1]*horizontal
    return HEIGHTS[lower_z][lower_x]*(1-horizontal)+HEIGHTS[lower_z][lower_x+1]*(horizontal-vertical)+HEIGHTS[lower_z+1][lower_x+1]*vertical


class Mesh:
    """保存向上为 Y 的米制几何；同一数据用于 Blender 原型与 Unity 导出。"""

    def __init__(self):
        """初始化顶点与带材质的面。"""
        self.vertices = []
        self.faces = []
        self.materials = []

    def face(self, vertices, material):
        """追加具有独立法线边界的一个面。"""
        offset = len(self.vertices)
        self.vertices.extend(vertices)
        self.faces.append(tuple(range(offset, offset+len(vertices))))
        self.materials.append(material)

    def box(self, center, size, material):
        """创建六面体，原点与尺寸均为米。"""
        horizontal, altitude, depth = center
        width, rise, length = (value/2 for value in size)
        points = [(horizontal+side_x*width, altitude+side_y*rise, depth+side_z*length)
                  for side_x, side_y, side_z in [(-1,-1,-1),(1,-1,-1),(1,-1,1),(-1,-1,1),
                                               (-1,1,-1),(1,1,-1),(1,1,1),(-1,1,1)]]
        for indices in [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]:
            # Blender 与 Unity 导出共用向外叉积法线。
            self.face([points[index] for index in reversed(indices)], material)

    def cylinder(self, center, radius, rise, material, sides=10):
        """创建低面数圆柱，用于储罐、塔台与树干。"""
        lower = [(center[0]+radius*math.cos(index*math.tau/sides), center[1],
                  center[2]+radius*math.sin(index*math.tau/sides)) for index in range(sides)]
        upper = [(point[0], point[1]+rise, point[2]) for point in lower]
        self.face(list(reversed(upper)), material)
        for index in range(sides):
            following = (index+1)%sides
            self.face([lower[index], upper[index], upper[following], lower[following]], material)

    def blender_object(self, name, materials, collection):
        """创建可在 Blender 编辑的原生网格和米制 UV。"""
        data = bpy.data.meshes.new(name)
        data.from_pydata([(point[0], -point[2], point[1]) for point in self.vertices], [], self.faces)
        data.update()
        names = list(materials)
        for material in materials.values():
            data.materials.append(material)
        uv = data.uv_layers.new(name="SurfaceUV")
        for polygon, material in zip(data.polygons, self.materials):
            polygon.material_index = names.index(material)
            for loop_index in polygon.loop_indices:
                point = data.vertices[data.loops[loop_index].vertex_index].co
                normal = polygon.normal
                uv.data[loop_index].uv = ((point.x/5, -point.y/5) if abs(normal.z)>.6
                                          else (point.x/3 if abs(normal.y)>abs(normal.x) else -point.y/3, point.z/3))
        instance = bpy.data.objects.new(name, data)
        collection.objects.link(instance)
        return instance


class Exporter:
    """按两公里空间块与材质合批，避免网页逐栋、逐树产生渲染调用。"""

    def __init__(self):
        """初始化分块、实例记录与统计。"""
        self.parts = {}
        self.buildings = []
        self.tree_count = 0

    def append(self, mesh, layer, position=(0,0,0), yaw=0, scale=(1,1,1)):
        """变换原型面并写入空间分块，保持每块小于十六位索引上限。"""
        angle = math.radians(yaw)
        cosine, sine = math.cos(angle), math.sin(angle)
        points = [(position[0]+point[0]*scale[0]*cosine+point[2]*scale[2]*sine,
                   position[1]+point[1]*scale[1],
                   position[2]-point[0]*scale[0]*sine+point[2]*scale[2]*cosine) for point in mesh.vertices]
        for face, material in zip(mesh.faces, mesh.materials):
            center_x = sum(points[index][0] for index in face)/len(face)
            center_z = sum(points[index][2] for index in face)/len(face)
            key = f"{layer}_{material}_{int(center_x//TILE):02}_{int(center_z//TILE):02}"
            suffix = 0
            while key+f"_{suffix}" in self.parts and len(self.parts[key+f"_{suffix}"]["vertices"])+len(face)*3>180000:
                suffix += 1
            key += f"_{suffix}"
            part = self.parts.setdefault(key, {"name": key, "layer": layer, "material": material, "vertices": [], "triangles": []})
            offset = len(part["vertices"])//3
            for index in face:
                part["vertices"].extend(round(value, 3) for value in points[index])
            for corner in range(1, len(face)-1):
                part["triangles"].extend((offset, offset+corner, offset+corner+1))


def make_building(kind: str, damage: int = 0) -> Mesh:
    """建立原创住宅、公寓、工业、铁路及机场模型，战损只改变可见几何。"""
    mesh = Mesh()
    if kind in ("Apartment_Slab", "Apartment_Tower", "Office"):
        width, depth, floors = {"Apartment_Slab": (48,16,8), "Apartment_Tower": (23,23,14), "Office": (38,24,6)}[kind]
        rise = floors*3.1
        mesh.box((0,rise/2,0), (width,rise,depth), "WallWarm" if kind=="Office" else "Battle_Plaster")
        mesh.box((0,rise+.3,0), (width+1,.6,depth+1), "Battle_Concrete")
        mesh.box((0,1,0), (width+.8,2,depth+.8), "Battle_Concrete")
        mesh.box((width*.23,rise+1,0), (5,1.6,4), "Battle_Metal")
        for floor in range(floors):
            for column in range(int(width/4)):
                horizontal = -width/2+2+column*4
                for direction in (-1,1):
                    mesh.face([(horizontal, floor*3.1+1, direction*(depth/2+.04)),
                               (horizontal+1.8, floor*3.1+1, direction*(depth/2+.04)),
                               (horizontal+1.8, floor*3.1+2.3, direction*(depth/2+.04)),
                               (horizontal, floor*3.1+2.3, direction*(depth/2+.04))][::direction], "Glass")
        for direction in (-1,1):
            for floor in range(floors):
                mesh.box((direction*(width/2+.6), floor*3.1+1.1, 0), (1.2,.16,depth*.64), "Battle_Concrete")
    elif kind.startswith("House"):
        width, depth, rise = 11, 9, 6
        if damage<2:
            mesh.box((0,rise/2,0), (width,rise,depth), "Battle_Plaster")
        else:
            for side in (-1,1):
                mesh.box((side*width/2,2,0), (.35,4 if damage==2 else 2,depth), "Battle_Brick")
            mesh.box((0,2,depth/2), (width,4,.35), "Battle_Plaster")
            for floor in (0,3):
                mesh.box((0,floor+.2,0), (width,.25,depth), "Battle_Concrete")
        if damage<3:
            mesh.face([(-6,6,-5),(-6,6,5),(0,9,5),(0,9,-5)], "Battle_Tile")
            if damage<2:
                mesh.face([(0,9,-5),(0,9,5),(6,6,5),(6,6,-5)], "Battle_Tile")
            mesh.face([(-5.5,6,-4.5),(0,9,-4.5),(5.5,6,-4.5)], "Battle_Brick")
            mesh.face([(5.5,6,4.5),(0,9,4.5),(-5.5,6,4.5)], "Battle_Brick")
        if damage<2:
            for horizontal in (-3.5,0,3.5):
                for altitude in (1.3,4.3):
                    mesh.box((horizontal,altitude,-4.54), (1.3,1.5,.07), "Battle_Ash")
            mesh.box((-2,8,2), (.7,3,.8), "Battle_Brick")
        if damage:
            generator = random.Random(100+damage)
            for index in range(8+damage*4):
                mesh.box((generator.uniform(-7,7),.25,generator.uniform(-6,6)),
                         (generator.uniform(.4,1.5),generator.uniform(.2,.9),generator.uniform(.4,1.6)), "Battle_Brick")
    elif kind in ("Warehouse", "Hangar", "RailDepot"):
        width, depth, rise = (60,40,12) if kind=="Hangar" else (45,24,9)
        mesh.box((0,rise/2,0), (width,rise,depth), "Battle_Concrete")
        mesh.face([(-width/2-1,rise,-depth/2-1),(-width/2-1,rise,depth/2+1),(0,rise+3,depth/2+1),(0,rise+3,-depth/2-1)], "Battle_Metal")
        mesh.face([(0,rise+3,-depth/2-1),(0,rise+3,depth/2+1),(width/2+1,rise,depth/2+1),(width/2+1,rise,-depth/2-1)], "Battle_Metal")
        for horizontal in (-width*.28, width*.28):
            mesh.box((horizontal,3,-depth/2-.08), (width*.3,6,.12), "Battle_Ash")
    elif kind=="StorageTank":
        mesh.cylinder((0,0,0), 16, 13, "Battle_Metal", 16)
        mesh.cylinder((0,13,0), 16.4, .5, "Battle_Concrete", 16)
    elif kind=="ControlTower":
        mesh.box((0,10,0), (7,20,7), "Battle_Concrete")
        mesh.box((0,21,0), (13,3,11), "Battle_Ash")
        mesh.box((0,23,0), (14,.6,12), "Battle_Concrete")
    elif kind=="Bunker":
        mesh.box((0,2,0), (14,4,10), "Battle_Concrete")
        mesh.box((0,4.4,0), (16,1,12), "Grass")
        mesh.box((0,2,-5.04), (7,.7,.1), "Battle_Ash")
    elif kind=="Barracks":
        mesh.box((0,3,0), (32,6,12), "Battle_Plaster")
        mesh.box((0,6.25,0), (33,.5,13), "Battle_Metal")
        for horizontal in range(-13,15,4):
            mesh.box((horizontal,2.7,-6.05), (1.7,2,.1), "Battle_Ash")
    elif kind=="Substation":
        mesh.box((0,.5,0), (36,1,24), "Battle_Concrete")
        for horizontal in (-10,0,10):
            mesh.box((horizontal,3,0), (5,5,7), "Battle_Metal")
            mesh.cylinder((horizontal,5.5,0), .8, 3, "Battle_Concrete", 6)
    return mesh


def tree_mesh(conifer: bool) -> Mesh:
    """生成米制低面数林木原型，策略总览保持林带可读且控制面数。"""
    mesh = Mesh()
    mesh.cylinder((0,0,0), .3, 5, "Battle_Timber", 5)
    if conifer:
        for base, radius, rise in [(3,3.6,6),(6,2.8,6)]:
            ring = [(radius*math.cos(index*math.tau/6),base,radius*math.sin(index*math.tau/6)) for index in range(6)]
            for index in range(6):
                mesh.face([ring[index],(0,base+rise,0),ring[(index+1)%6]], "Forest")
    else:
        lower = [(3.5*math.cos(index*math.tau/6),4,3.5*math.sin(index*math.tau/6)) for index in range(6)]
        upper = [(2.6*math.cos(index*math.tau/6+.3),10,2.6*math.sin(index*math.tau/6+.3)) for index in range(6)]
        for index in range(6):
            following = (index+1)%6
            mesh.face([lower[index],upper[index],upper[following],lower[following]], "ForestLight")
            mesh.face([upper[index],(0,12,0),upper[following]], "ForestLight")
    return mesh


def rectangle(mesh, center_x, center_z, width, depth, material, offset=.12):
    """把细分矩形铺在同源地表上，避免大型场坪穿入地形。"""
    columns, rows = max(1,math.ceil(width/45)), max(1,math.ceil(depth/45))
    for row in range(rows):
        for column in range(columns):
            lower_x, upper_x = center_x-width/2+width*column/columns, center_x-width/2+width*(column+1)/columns
            lower_z, upper_z = center_z-depth/2+depth*row/rows, center_z-depth/2+depth*(row+1)/rows
            mesh.face([(position_x,height(position_x,position_z)+offset,position_z)
                       for position_x,position_z in [(lower_x,lower_z),(lower_x,upper_z),(upper_x,upper_z),(upper_x,lower_z)]], material)


def ribbon(mesh, points, width, material, fixed_height=None):
    """沿折线铺设道路或河岸，使用共享接点方向防止接缝。"""
    for index in range(len(points)-1):
        start, end = points[index], points[index+1]
        length = math.dist(start,end)
        count = max(1,math.ceil(length/10))
        normal = (-(end[1]-start[1])/length*width/2, (end[0]-start[0])/length*width/2)
        for segment in range(count):
            positions = [(start[0]+(end[0]-start[0])*parameter/count, start[1]+(end[1]-start[1])*parameter/count)
                         for parameter in (segment,segment+1)]
            corners = [(positions[0][0]-normal[0],positions[0][1]-normal[1]),
                       (positions[1][0]-normal[0],positions[1][1]-normal[1]),
                       (positions[1][0]+normal[0],positions[1][1]+normal[1]),
                       (positions[0][0]+normal[0],positions[0][1]+normal[1])]
            mesh.face([(position_x, (max(20,height(position_x,position_z)) if material in ("Asphalt","Marking") else height(position_x,position_z))+(.4 if material=="Marking" else .32) if fixed_height is None else fixed_height,position_z)
                       for position_x,position_z in reversed(corners)], material)


def distance_to_segment(point, start, end):
    """计算到道路轴线的最近距离，建筑与植被据此保留通路。"""
    direction = (end[0]-start[0], end[1]-start[1])
    parameter = max(0,min(1,((point[0]-start[0])*direction[0]+(point[1]-start[1])*direction[1])/(direction[0]**2+direction[1]**2)))
    return math.hypot(point[0]-start[0]-parameter*direction[0],point[1]-start[1]-parameter*direction[1])


def build_scene(materials):
    """先在 Blender 建立完整场景，再从相同米制模型导出分块与定义。"""
    generator = random.Random(SEED)
    exporter = Exporter()
    scenery = bpy.data.collections.new("十二公里策略地图")
    bpy.context.scene.collection.children.link(scenery)
    library = bpy.data.collections.new("可复用米制建筑原型")
    bpy.context.scene.collection.children.link(library)
    library.hide_render = True
    library.hide_viewport = True
    prototypes = {}
    meshes = {}
    kinds = ["Apartment_Slab","Apartment_Tower","Office","House_0","House_1","House_2","House_3",
             "Warehouse","Hangar","RailDepot","StorageTank","ControlTower","Bunker","Barracks","Substation"]
    for kind in kinds:
        meshes[kind] = make_building(kind, int(kind[-1]) if kind.startswith("House") else 0)
        prototype = meshes[kind].blender_object("Strategic_"+kind,materials,library)
        prototypes[kind] = prototype
        bpy.ops.object.select_all(action="DESELECT")
        prototype.hide_set(False)
        # 导出时临时链接到可见集合，避免隐藏集合阻止 FBX 选择。
        scenery.objects.link(prototype)
        prototype.select_set(True)
        bpy.context.view_layer.objects.active = prototype
        bpy.ops.export_scene.fbx(filepath=str(MODELS/("Strategic_"+kind+".fbx")),use_selection=True,
                                 object_types={"MESH"},axis_forward="-Z",axis_up="Y",apply_unit_scale=True,
                                 bake_space_transform=True,bake_anim=False,add_leaf_bones=False,use_triangles=True)
        scenery.objects.unlink(prototype)

    def place(kind, position_x, position_z, yaw=0, scale=1):
        """放置原创建筑实例，记录可审阅米制坐标并合批至 Unity 导出。"""
        altitude = height(position_x,position_z)+.15
        identifier = f"BUILDING_{len(exporter.buildings)+1:05}"
        instance = bpy.data.objects.new(identifier+"_"+kind,prototypes[kind].data)
        scenery.objects.link(instance)
        instance.location = (position_x,-position_z,altitude)
        instance.rotation_euler.z = math.radians(-yaw)
        instance.scale = (scale,scale,scale)
        exporter.append(meshes[kind],"settlement",(position_x,altitude,position_z),yaw,(scale,scale,scale))
        exporter.buildings.append({"id":identifier,"layer":"settlement","model":"Strategic_"+kind,
                                   "damage":int(kind[-1]) if kind.startswith("House") else 0,
                                   "x":round(position_x,3),"y":round(altitude,3),"z":round(position_z,3),
                                   "yaw":yaw,"scaleX":scale,"scaleY":scale,"scaleZ":scale})

    ground = Mesh()
    for row in range(SIZE//STEP):
        for column in range(SIZE//STEP):
            position_x,position_z = column*STEP,row*STEP
            ground.face([(position_x,HEIGHTS[row][column],position_z),(position_x,HEIGHTS[row+1][column],position_z+STEP),
                         (position_x+STEP,HEIGHTS[row+1][column+1],position_z+STEP),(position_x+STEP,HEIGHTS[row][column+1],position_z)],
                        "GroundHigh" if HEIGHTS[row][column]>65 else "GroundLow")
    exporter.append(ground,"terrain")
    # 可操作范围仍为十二公里；外围仅作远景，避免地图以灰背景方形模型结束。
    background=Mesh()
    for position_z in range(-4000,SIZE+4000,250):
        for position_x in range(-4000,SIZE+4000,250):
            if 0<=position_x<SIZE and 0<=position_z<SIZE:
                continue
            corners=[(position_x,position_z),(position_x,position_z+250),(position_x+250,position_z+250),(position_x+250,position_z)]
            background.face([(horizontal,raw_height(horizontal,depth),depth) for horizontal,depth in corners],
                            "GroundHigh" if (position_x//750+position_z//1000)%4==0 else "GroundLow")
    exporter.append(background,"landscape")
    water = Mesh()
    for position_z in range(-4000,SIZE+4000,25):
        corners = [(river_center(position_z)-river_half_width(position_z),position_z),
                   (river_center(position_z+25)-river_half_width(position_z+25),position_z+25),
                   (river_center(position_z+25)+river_half_width(position_z+25),position_z+25),
                   (river_center(position_z)+river_half_width(position_z),position_z)]
        water.face([(position_x,1.3,depth) for position_x,depth in corners],"Water")
    for northern in (True,False):
        for position_x in range(0 if northern else 6000,7000 if northern else SIZE,25):
            depth=tributary_center(position_x,northern)
            if northern and position_x>river_center(depth) or not northern and position_x<river_center(depth):
                continue
            next_depth=tributary_center(position_x+25,northern)
            half_width=65 if northern else 75
            water.face([(position_x,1.32,depth-half_width),(position_x,1.32,depth+half_width),
                        (position_x+25,1.32,next_depth+half_width),(position_x+25,1.32,next_depth-half_width)],"Water")
    exporter.append(water,"water")

    nodes, roads, segments = [], [], []
    lookup = {}
    def node(identifier, position_x, position_z):
        """注册道路交叉点。"""
        point = {"id":identifier,"name":identifier,"kind":"road","partnerId":"","x":round(position_x,3),"z":round(position_z,3)}
        nodes.append(point)
        lookup[identifier] = (position_x,position_z)
    def road(start, end, width=18, bridge=False):
        """记录连通道路并生成完全相同位置的可见路面。"""
        roads.append({"id":f"ROAD_{len(roads)+1:03}","startId":start,"endId":end,"isBridge":bridge,"width":width,"surfaceHeight":22 if bridge else 0})
        segments.append((lookup[start],lookup[end],width))
        # 中央主干路已覆盖城市同轴道路，保留拓扑边但不重复铺面以免闪烁。
        covered = lookup[start][1]==5800 and lookup[end][1]==5800 and (start.startswith("CITY_") or end.startswith("CITY_"))
        if not covered:
            ribbon(route_mesh,[lookup[start],lookup[end]],width,"Asphalt",22 if bridge else None)
        if width>=14 and not covered:
            start_point,end_point=lookup[start],lookup[end]
            length=math.dist(start_point,end_point)
            for offset in range(15,int(length)-15,35):
                beginning=(start_point[0]+(end_point[0]-start_point[0])*offset/length,start_point[1]+(end_point[1]-start_point[1])*offset/length)
                ending=(start_point[0]+(end_point[0]-start_point[0])*(offset+8)/length,start_point[1]+(end_point[1]-start_point[1])*(offset+8)/length)
                ribbon(route_mesh,[beginning,ending],.18,"Marking",22.04 if bridge else None)
        if bridge:
            start_x,start_z=lookup[start]
            end_x,end_z=lookup[end]
            for direction in (-1,1):
                ribbon(bridge_mesh,[(start_x,start_z+direction*(width/2+.5)),(end_x,end_z+direction*(width/2+.5))],1,"Battle_Concrete",23.3)
            for fraction in (.2,.5,.8):
                position_x=start_x+(end_x-start_x)*fraction
                bridge_mesh.box((position_x,8,start_z),(4,28,width-3),"Battle_Concrete")

    route_mesh, bridge_mesh = Mesh(), Mesh()
    # 三条跨河主路与两岸纵向联络路。北部、中央、南部地形与建设密度不同。
    for row,position_z in enumerate((3000,5800,8500)):
        crossing=river_center(position_z)
        for column,position_x in enumerate((1200,3000,4500,crossing-350,crossing+350,8200,10500)):
            node(f"N{row}_{column}",position_x,position_z)
        for column in range(6):
            road(f"N{row}_{column}",f"N{row}_{column+1}",32 if row==1 else 24,column==3)
    for row in range(2):
        for column in (0,1,2,5,6):
            road(f"N{row}_{column}",f"N{row+1}_{column}",18 if column in (1,5) else 10)
    node("AIRFIELD_ACCESS",8400,9300)
    road("N2_5","AIRFIELD_ACCESS",14)
    # 外围支路形成环线和横向联络，连接新增生活、农业设施。
    for identifier,position_x,position_z,anchor in [
        ("SERVICE_W",2100,4500,"N1_0"),("SERVICE_E",9400,6800,"N1_6"),
        ("FARM_SW",3900,1700,"N0_1"),("FARM_SE",9400,1700,"N0_5"),
        ("PARK_W",2200,3800,"N0_1"),("PARK_N",4800,9400,"N2_2"),
        ("OUTER_W",900,10500,"N2_0"),("OUTER_E",11100,10500,"N2_6")]:
        node(identifier,position_x,position_z)
        road(anchor,identifier,12)
    for start,end in [("SERVICE_W","PARK_W"),("PARK_W","FARM_SW"),("SERVICE_E","FARM_SE"),
                      ("PARK_N","OUTER_W"),("OUTER_E","AIRFIELD_ACCESS"),("FARM_SW","N0_2"),
                      ("FARM_SE","N0_6"),("SERVICE_E","N2_5")]:
        road(start,end,12)
    # 城市街网交叉点属于同一连通图，避免只有贴图道路而没有拓扑。
    city_columns=(3550,3820,4140,4500,4870,5260,5640,5960)
    city_rows=(4400,4680,5000,5360,5800,6120,6520,6870,7180)
    for row,position_z in enumerate(city_rows):
        for column,position_x in enumerate(city_columns):
            if abs(position_x-river_center(position_z))<river_half_width(position_z)+150:
                continue
            identifier=f"CITY_{row}_{column}"
            node(identifier,position_x,position_z)
            if column and f"CITY_{row}_{column-1}" in lookup:
                road(f"CITY_{row}_{column-1}",identifier,22 if row in (1,4,7) else 12)
            if row and f"CITY_{row-1}_{column}" in lookup:
                road(f"CITY_{row-1}_{column}",identifier,22 if column in (2,5) else 12)
    # 同坐标节点用近邻道路连接，禁止零长度边。
    road("N1_2","CITY_4_3",22) if lookup["N1_2"]!=lookup["CITY_4_3"] else road("N1_2","CITY_4_2",22)
    # 两岸沿河干道与横向联络路构成闭环，所有交点进入同一道路图。
    bank_rows=(1700,3000,4400,5800,7000,8500,10400)
    for side in ("WEST","EAST"):
        for row,position_z in enumerate(bank_rows):
            crossing=river_center(position_z)
            columns=(crossing-520,3000,1800) if side=="WEST" else (crossing+520,7600,9000,10200)
            for column,position_x in enumerate(columns):
                identifier=f"BANK_{side}_{row}_{column}"
                node(identifier,position_x,position_z)
                if column:
                    road(f"BANK_{side}_{row}_{column-1}",identifier,18)
                if row:
                    road(f"BANK_{side}_{row-1}_{column}",identifier,18 if column==0 else 14)
            if position_z in (3000,5800,8500):
                main_row=(3000,5800,8500).index(position_z)
                road(f"BANK_{side}_{row}_0",f"N{main_row}_{3 if side=='WEST' else 4}",24)
        road(f"BANK_{side}_0_1","FARM_SW" if side=="WEST" else "FARM_SE",12)
    exporter.append(route_mesh,"routes")
    exporter.append(bridge_mesh,"facilities")

    city_ground, details = Mesh(), Mesh()
    city_trees=[]
    for row in range(len(city_rows)-1):
        for column in range(len(city_columns)-1):
            center_x=(city_columns[column]+city_columns[column+1])/2
            center_z=(city_rows[row]+city_rows[row+1])/2
            if abs(center_x-river_center(center_z))<river_half_width(center_z)+230:
                continue
            park = (row*7+column*3)%13 in (0,1)
            block_width=city_columns[column+1]-city_columns[column]-34
            block_depth=city_rows[row+1]-city_rows[row]-34
            rectangle(city_ground,center_x,center_z,block_width,block_depth,"Grass",.08)
            if park:
                ribbon(details,[(center_x-block_width/2,center_z),(center_x+block_width/2,center_z)],4,"Paving")
                for index in range(65):
                    city_trees.append((center_x+generator.uniform(-block_width*.45,block_width*.45),center_z+generator.uniform(-block_depth*.45,block_depth*.45)))
                continue
            low_rise=row<3 or column==0
            first_building=len(exporter.buildings)
            local_rows,local_columns=(5,7) if low_rise else (3,4)
            ribbon(details,[(center_x-block_width*.46,center_z),(center_x+block_width*.46,center_z)],7,"Asphalt")
            for local_row in range(local_rows):
                for local_column in range(local_columns):
                    # 中央留出院落；住宅、板楼与塔楼使用不同节奏和间隔。
                    if not low_rise and local_row==1 and local_column in (1,2):
                        continue
                    position_x=center_x+(local_column-(local_columns-1)/2)*block_width/(local_columns+.8)+generator.uniform(-5,5)
                    position_z=center_z+(local_row-(local_rows-1)/2)*block_depth/(local_rows+.5)+generator.uniform(-4,4)
                    clearance=18 if low_rise else 38
                    if abs(position_z-center_z)<clearance:
                        position_z+=(24 if low_rise else 42) if local_column%2 else -(24 if low_rise else 42)
                    kind = ("Apartment_Tower" if 2<=column<=4 and 2<=row<=5 and (local_row+local_column)%3==0
                            else "Apartment_Slab" if row>2 and column>0 else "Office" if column==5 else "House_"+str(generator.choices([0,1,2,3],[65,17,13,5])[0]))
                    place(kind,position_x,position_z,90 if local_column%2 else 0,generator.uniform(.85,1.12))
                    rectangle(details,position_x,position_z+12,14 if low_rise else 24,8,"Paving",.16)
                    city_trees.append((position_x+18,position_z+18))
            if not low_rise:
                rectangle(details,center_x,center_z,78,38,"Asphalt",.18)
                for parking in range(22):
                    position_x=center_x-35+parking*3.3
                    for direction in (-1,1):
                        rectangle(details,position_x,center_z+direction*13,.12,5,"Marking",.25)
            block_buildings=exporter.buildings[first_building:]
            for tree_index in range(130):
                position_x=center_x+generator.uniform(-block_width*.47,block_width*.47)
                position_z=center_z+generator.uniform(-block_depth*.47,block_depth*.47)
                if abs(position_z-center_z)<24:
                    continue
                if any(math.hypot(position_x-building["x"],position_z-building["z"])<(16 if low_rise else 33) for building in block_buildings):
                    continue
                city_trees.append((position_x,position_z))
            # 街角停车场、沿街人行道和车行标记均保持米制尺寸。
            ribbon(details,[(center_x-133,center_z-137),(center_x+133,center_z-137)],3,"Paving")
            for position_x in range(int(center_x-100),int(center_x+100),25):
                details.box((position_x,height(position_x,center_z-140)+.32,center_z-140),(9,.03,.15),"Marking")
    exporter.append(city_ground,"central_village")

    # 村庄位于道路周边，保留空地、巷道与农场，而非对称复制城市。
    village_mesh=Mesh()
    for region in REGIONS[3:]:
        center_x,center_z=region["x"],region["z"]
        for street in (-240,-80,80,240):
            ribbon(village_mesh,[(center_x-460,center_z+street),(center_x+460,center_z+street)],7,"Gravel")
        for row in range(7):
            for column in range(12):
                position_x=center_x-420+column*76+generator.uniform(-9,9)
                position_z=center_z-280+row*88+generator.uniform(-7,7)
                if min(distance_to_segment((position_x,position_z),start,end)-width/2 for start,end,width in segments)<20:
                    continue
                if any(abs(position_z-center_z-street)<17 for street in (-240,-80,80,240)):
                    continue
                place("House_"+str(generator.choices([0,1,2,3],[65,16,13,6])[0]),position_x,position_z,generator.choice((0,90,180,270)))
        place("Warehouse",center_x+400,center_z+370)
    exporter.append(village_mesh,"village")

    industrial=Mesh()
    rectangle(industrial,7900,5100,1300,1200,"Gravel")
    for row in range(5):
        for column in range(7):
            place("Warehouse",7380+column*155,4650+row*170,90 if row%2 else 0)
        ribbon(industrial,[(7200,4720+row*170),(8600,4720+row*170)],14,"Asphalt")
    for row in range(3):
        for column in range(4):
            place("StorageTank",9000+column*55,4600+row*55)
    place("Substation",8780,5500)
    # 多股货运线路、有间隔的枕木、货运棚和集装箱构成铁路货场。
    for track in range(4):
        depth=6150+track*10
        ribbon(industrial,[(7400,depth),(11000,depth+450)],5,"Gravel")
        for offset in (-.8,.8):
            ribbon(industrial,[(7400,depth+offset),(11000,depth+450+offset)],.14,"Battle_Metal")
        for index in range(0,240):
            position_x=7400+index*15
            position_z=depth+index*15*450/3600
            industrial.box((position_x,height(position_x,position_z)+.3,position_z),(1.9,.12,3),"Battle_Timber")
    for index in range(7):
        place("RailDepot",7600+index*155,6300)
    for row in range(3):
        for column in range(12):
            position_x,position_z=7550+column*23,6440+row*18
            industrial.box((position_x,height(position_x,position_z)+1.5,position_z),(12,3,2.5),"Battle_Metal" if column%2 else "Battle_Brick")
    exporter.append(industrial,"logistics")

    # 东岸桥头北侧补充生活街区、仓储院落与沿河林带，保留主路通行空间。
    east_bank=Mesh()
    east_streets=[[(6800,5900),(6900,6750),(7200,7350),(7800,7950),(8800,8200)],
                  [(6900,6750),(7600,6850),(8400,7000),(9400,6800)],
                  [(7200,7350),(7900,7500),(8650,7650),(9400,8000)],
                  [(7600,6850),(7900,7500),(7800,7950)],
                  [(8400,7000),(8650,7650),(8800,8200)]]
    east_segments=[(points[index],points[index+1],12)
                   for points in east_streets for index in range(len(points)-1)]
    for points in east_streets:
        ribbon(east_bank,points,12,"Asphalt")
    segments.extend(east_segments)
    first_east_building=len(exporter.buildings)
    for center_x,center_z in ((7300,7070),(8170,7240),(8300,7930),
                              (7100,4900),(7000,5500),(7200,6200),(8050,6500),
                              (9300,4900),(9450,5550),(9570,6250),(9550,7500)):
        for row in range(7):
            for column in range(9):
                position_x=center_x+(column-4)*58+generator.uniform(-8,8)
                position_z=center_z+(row-3)*62+generator.uniform(-8,8)
                if min(distance_to_segment((position_x,position_z),start,end)-width/2
                       for start,end,width in segments)<30:
                    continue
                if any(math.hypot(position_x-building["x"],position_z-building["z"])<48
                       for building in exporter.buildings):
                    continue
                kind=("Apartment_Slab" if (row+column)%5==0 else "Office"
                      if (row+column)%7==0 else "House_0" if (row+column)%4 else "House_1")
                place(kind,position_x,position_z,0 if column%2 else 90)
                rectangle(east_bank,position_x,position_z,31,35,"Gravel",.12)
    for position_x,position_z in ((7160,6570),(7380,6630),(8670,6800),(8950,7270)):
        place("Warehouse",position_x,position_z,15)
        rectangle(east_bank,position_x,position_z,110,90,"Gravel",.12)
        ribbon(east_bank,[(position_x,position_z),(position_x,position_z+130)],7,"Gravel")
    east_buildings=exporter.buildings[first_east_building:]
    for trial in range(2200):
        position_z=generator.uniform(6500,8350)
        river_edge=river_center(position_z)+river_half_width(position_z)
        position_x=generator.uniform(river_edge+85,9150)
        # 交错林团与河岸植被之间保留草地，不将空地全部覆盖。
        forest_patch=(math.sin(position_x/210)+math.cos(position_z/180)>.1
                      or position_x<river_edge+330)
        if not forest_patch or height(position_x,position_z)<3:
            continue
        if min(distance_to_segment((position_x,position_z),start,end)-width/2
               for start,end,width in segments)<22:
            continue
        if any(math.hypot(position_x-building["x"],position_z-building["z"])<32
               for building in east_buildings):
            continue
        city_trees.append((position_x,position_z))
    exporter.append(east_bank,"central_village")

    airfield=Mesh()
    rectangle(airfield,8650,9450,2500,45,"Asphalt",.25)
    rectangle(airfield,8650,9280,2500,18,"Asphalt",.24)
    rectangle(airfield,8200,9070,1500,300,"Paving",.15)
    for position_x in range(7500,9800,80):
        rectangle(airfield,position_x,9450,35,.9,"Marking",.3)
    for index in range(7):
        place("Hangar",7550+index*190,8860)
    place("ControlTower",9050,9000)
    for index in range(4):
        place("Bunker",7400+index*350,9700)
    exporter.append(airfield,"facilities")

    # 保障建筑保持实体米制大小，部署区不再使用彩色大方框。
    for center_x,center_z in ((1300,4200),(10400,8000)):
        for index in range(5):
            place("Barracks",center_x+index*55,center_z)
        place("Warehouse",center_x+90,center_z+110)
        rectangle(details,center_x+120,center_z+50,340,230,"Gravel")
        for index in range(2):
            rectangle(details,center_x+50+index*100,center_z+150,35,35,"Paving")
    farmland=Mesh()
    field_bounds=[]
    for center_x,center_z,columns,rows in ((2200,1900,5,3),(4300,1800,4,4),(9200,3600,4,3),(3000,10300,4,3),(10000,11100,3,2)):
        for row in range(rows):
            for column in range(columns):
                position_x=center_x+column*250+row*25
                position_z=center_z+row*240
                width,depth=generator.uniform(160,210),generator.uniform(150,200)
                rectangle(farmland,position_x,position_z,width,depth,"FieldGold" if (row+column)%3==0 else "FieldGreen",.06)
                for furrow in range(0,int(width),18):
                    ribbon(farmland,[(position_x-width/2+furrow,position_z-depth/2),(position_x-width/2+furrow,position_z+depth/2)],1,"Mud")
                field_bounds.append((position_x,position_z,width,depth))
    exporter.append(farmland,"farmland")
    exporter.append(details,"facilities")

    amenities=Mesh()
    for center_x,center_z in ((2100,4500),(9400,6800)):
        # 加油站：罩棚、支柱、四组油泵、便利店与入口场坪。
        rectangle(amenities,center_x+60,center_z+65,130,110,"Asphalt")
        altitude=height(center_x+60,center_z+65)
        amenities.box((center_x+60,altitude+6,center_z+65),(42,.8,24),"Battle_Metal")
        for offset_x in (-17,17):
            for offset_z in (-9,9):
                amenities.box((center_x+60+offset_x,altitude+3,center_z+65+offset_z),(.5,6,.5),"Battle_Concrete")
        for offset_x in (-10,10):
            for offset_z in (-5,5):
                amenities.box((center_x+60+offset_x,altitude+1,center_z+65+offset_z),(1.3,2,.8),"Battle_Metal")
        amenities.box((center_x+60,altitude+2.5,center_z+105),(24,5,12),"WallWarm")
    for center_x,center_z in ((2200,3800),(4800,9400)):
        # 公园中央保留游乐空间，周边步道及座椅形成可辨认的生活场景。
        rectangle(amenities,center_x+100,center_z+100,180,170,"Grass")
        ribbon(amenities,[(center_x+20,center_z+100),(center_x+180,center_z+100)],4,"Paving")
        rectangle(amenities,center_x+100,center_z+140,45,35,"Gravel")
        altitude=height(center_x+100,center_z+140)
        amenities.cylinder((center_x+100,altitude+.2,center_z+140),6,.5,"Battle_Metal",16)
        for offset in (-8,8):
            amenities.box((center_x+100+offset,altitude+2,center_z+120),(.2,4,.2),"Battle_Metal")
        amenities.box((center_x+100,altitude+4,center_z+120),(16,.2,.2),"Battle_Metal")
        for offset in (-4,4):
            amenities.box((center_x+100+offset,altitude+2.5,center_z+120),(.08,3,.08),"Battle_Metal")
            amenities.box((center_x+100+offset,altitude+1,center_z+120),(1.5,.15,.6),"Battle_Timber")
        for offset in range(25,180,30):
            amenities.box((center_x+offset,height(center_x+offset,center_z+106)+.5,center_z+106),(3,1,.7),"Battle_Timber")
    for center_x,center_z in ((3900,1700),(9400,1700)):
        place("Warehouse",center_x+70,center_z+70)
        place("House_0",center_x+20,center_z+80)
        for offset in (0,22,44):
            amenities.cylinder((center_x+70+offset,height(center_x+70+offset,center_z+115),center_z+115),7,16,"Battle_Metal",12)
        for field in range(5):
            rectangle(amenities,center_x-200+field*95,center_z-170,85,210,"FieldGreen" if field%2 else "FieldGold")
    exporter.append(amenities,"logistics")

    trees=[tree_mesh(False),tree_mesh(True)]
    vegetation=Mesh()
    forest_floor=Mesh()
    # 林底与不规则林缘形成连续环境，树木保持十余米实际尺寸。
    for forest in FORESTS:
        for position_z in range(max(0,int(forest[1]-forest[3])),min(SIZE-60,int(forest[1]+forest[3])),60):
            for position_x in range(max(0,int(forest[0]-forest[2])),min(SIZE-60,int(forest[0]+forest[2])),60):
                center_x,center_z=position_x+30,position_z+30
                radius=((center_x-forest[0])/forest[2])**2+((center_z-forest[1])/forest[3])**2
                if radius>.73+.16*math.sin(center_x/125)*math.sin(center_z/177):
                    continue
                if abs(center_x-river_center(center_z))<river_half_width(center_z)+80:
                    continue
                if any(math.hypot(center_x-region["x"],center_z-region["z"])<region["radius"]+100 for region in REGIONS):
                    continue
                if min(distance_to_segment((center_x,center_z),start,end)-width/2 for start,end,width in segments)<55:
                    continue
                rectangle(forest_floor,center_x,center_z,60,60,"ForestFloor",.04)
    exporter.append(forest_floor,"scrub")
    accepted=[]
    for trial in range(38000):
        forest=FORESTS[trial%len(FORESTS)]
        position_x=generator.uniform(forest[0]-forest[2],forest[0]+forest[2])
        position_z=generator.uniform(forest[1]-forest[3],forest[1]+forest[3])
        if not 20<position_x<SIZE-20 or not 20<position_z<SIZE-20:
            continue
        if height(position_x,position_z)<3:
            continue
        radius=((position_x-forest[0])/forest[2])**2+((position_z-forest[1])/forest[3])**2
        if radius> .8+.18*math.sin(position_x/125)*math.sin(position_z/177):
            continue
        if abs(position_x-river_center(position_z))<river_half_width(position_z)+50:
            continue
        if any(math.hypot(position_x-region["x"],position_z-region["z"])<region["radius"]+100 for region in REGIONS):
            continue
        if min(distance_to_segment((position_x,position_z),start,end)-width/2 for start,end,width in segments)<25:
            continue
        if any(abs(position_x-field[0])<field[2]/2+12 and abs(position_z-field[1])<field[3]/2+12 for field in field_bounds):
            continue
        exporter.append(trees[trial%2],"environment",(position_x,height(position_x,position_z),position_z),generator.uniform(0,360),
                        (generator.uniform(1.3,2.3),generator.uniform(1,1.9),generator.uniform(1.3,2.3)))
        accepted.append((position_x,position_z))
    # 城市公园、林荫道及农田边界补充散生树，保持中心也有掩蔽环境。
    for position_x,position_z in city_trees:
        exporter.append(trees[0],"environment",(position_x,height(position_x,position_z),position_z),scale=(1.5,1.2,1.5))
        accepted.append((position_x,position_z))
    exporter.tree_count=len(accepted)

    # 空网格图层使用真实一公里边界刻线，默认仍由已有图层控制。
    grid=Mesh()
    for coordinate in range(1000,SIZE,1000):
        ribbon(grid,[(coordinate,0),(coordinate,SIZE)],.25,"Marking")
        ribbon(grid,[(0,coordinate),(SIZE,coordinate)],.25,"Marking")
    exporter.append(grid,"grid")
    # 只为非建筑部分创建合批 Blender 对象；建筑仍是可独立编辑的链接实例。
    for part in exporter.parts.values():
        if part["layer"]=="settlement":
            continue
        mesh=Mesh()
        mesh.vertices=[tuple(part["vertices"][offset:offset+3]) for offset in range(0,len(part["vertices"]),3)]
        mesh.faces=[tuple(part["triangles"][offset:offset+3]) for offset in range(0,len(part["triangles"]),3)]
        mesh.materials=[part["material"]]*len(mesh.faces)
        instance=mesh.blender_object(part["name"],materials,scenery)
        if part["layer"]=="grid":
            instance.hide_render=True
            instance.hide_viewport=True
        if part["layer"]=="terrain":
            for polygon in instance.data.polygons:
                polygon.use_smooth=True

    points=[]
    markers=[("TASK_A","西岸检查点","task",3400,5800),("TASK_B","中央桥梁","task",river_center(5800),5800),
             ("TASK_C","东岸检查点","task",8200,5800),("RED_CAMP","红方兵营","facility",1400,4200),
             ("BLUE_CAMP","蓝方兵营","facility",10500,8000),("RED_SUPPLY","红方补给站","facility",1450,4310),
             ("BLUE_SUPPLY","蓝方补给站","facility",10500,8110),("RED_EVAC","红方撤离点","facility",1350,4350),
             ("BLUE_EVAC","蓝方撤离点","facility",10450,8150),("WEST_FACILITY","中央城市","facility",5100,5800),
             ("EAST_FACILITY","工业货场","facility",7900,5200)]
    for identifier,name,kind,position_x,position_z in markers:
        points.append({"id":identifier,"name":name,"kind":kind,"partnerId":"","x":round(position_x,3),"z":position_z})
    definition={"schemaVersion":2,"mapId":MAP_ID,"name":"青岚都市圈 · 十二公里策略地图","coordinateUnit":"meter",
                "width":SIZE,"length":SIZE,"displayScale":1,"buildingsBaked":True,
                "heightColumns":SIZE//STEP+1,"heightRows":SIZE//STEP+1,"heights":[value for row in HEIGHTS for value in row],
                "nodes":nodes,"roads":roads,"points":points,"materials":[{"id":name,"red":color[0],"green":color[1],"blue":color[2]} for name,color in PALETTE.items()],
                "deploymentZones":[{"id":"RED_DEPLOY","side":"red","minX":1000,"maxX":1900,"minZ":3200,"maxZ":10400},
                                   {"id":"BLUE_DEPLOY","side":"blue","minX":10000,"maxX":11200,"minZ":1600,"maxZ":8800}],
                "buildings":exporter.buildings}
    return definition,exporter


def render_preview():
    """渲染总览与城市近景，明确为 Blender 离线效果。"""
    scene=bpy.context.scene
    scene.render.engine="CYCLES"
    scene.cycles.samples=16
    scene.cycles.use_denoising=True
    scene.render.resolution_x=1920
    scene.render.resolution_y=1080
    scene.render.resolution_percentage=100
    scene.render.image_settings.file_format="PNG"
    background=scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value=(.55,.62,.69,1)
    background.inputs["Strength"].default_value=.42
    sun_data=bpy.data.lights.new("阴天日照","SUN")
    sun_data.energy=2.8
    sun_data.angle=.06
    sun=bpy.data.objects.new("阴天日照",sun_data)
    bpy.context.scene.collection.objects.link(sun)
    sun.rotation_euler=(.95,-.4,-.55)
    camera_data=bpy.data.cameras.new("策略地图摄影机")
    camera=bpy.data.objects.new("策略地图摄影机",camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera_data.clip_start=2
    camera_data.clip_end=45000
    scene.camera=camera
    scene.view_settings.view_transform="AgX"
    scene.view_settings.look="AgX - Medium High Contrast"
    for name,location,target,lens in [("十二公里_Blender总览.png",(15000,-16500,14500),(6000,-6000,0),40),
                                      ("中央城市_Blender近景.png",(4950,-4250,450),(5300,-5900,25),40)]:
        camera.location=location
        camera.rotation_euler=(Vector(target)-camera.location).to_track_quat("-Z","Y").to_euler()
        camera_data.lens=lens
        scene.render.filepath=str(OUTPUT/name)
        bpy.ops.render.render(write_still=True)
    # 打开源文件时保持完整总览相机，并使用米制视口裁剪。
    camera.location=(15000,-16500,14500)
    camera.rotation_euler=(Vector((6000,-6000,0))-camera.location).to_track_quat("-Z","Y").to_euler()
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=="VIEW_3D":
                area.spaces.active.clip_end=45000
                area.spaces.active.region_3d.view_distance=16000
                area.spaces.active.region_3d.view_location=(6000,-6000,0)


def main():
    """生成可复现米制地图，校验哈希、原型和拓扑后保存 Blender 源文件。"""
    for directory in (DATA,MODELS,OUTPUT):
        directory.mkdir(parents=True,exist_ok=True)
    if "--render-existing" in sys.argv:
        bpy.ops.wm.open_mainfile(filepath=str(OUTPUT/"青岚_12km策略地图.blend"))
        render_preview()
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT/"青岚_12km策略地图.blend"),compress=True)
        return
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene=bpy.context.scene
    scene.unit_settings.system="METRIC"
    scene.unit_settings.scale_length=1
    specification=importlib.util.spec_from_file_location("battlefield_assets",Path(__file__).with_name("build_battlefield_assets.py"))
    assets=importlib.util.module_from_spec(specification)
    specification.loader.exec_module(assets)
    source_materials=assets.create_materials()
    materials={}
    for name,color in PALETTE.items():
        surface=name[7:] if name.startswith("Battle_") else SURFACE.get(name)
        if surface:
            material=source_materials[surface].copy()
            material.name=name
            tint_values={"GroundLow":(.68,.78,.58),"GroundHigh":(.82,.80,.70),"Asphalt":(.42,.46,.49),
                         "ForestFloor":(.46,.55,.35),"Grass":(.69,.79,.58),"FieldGreen":(.81,.87,.67),
                         "WallWarm":(.88,.66,.48),"Glass":(.5,.66,.78)}
            if name in tint_values:
                shader=material.node_tree.nodes.get("Principled BSDF")
                texture=shader.inputs["Base Color"].links[0].from_socket
                tint=material.node_tree.nodes.new("ShaderNodeMixRGB")
                tint.blend_type="MULTIPLY"
                tint.inputs[0].default_value=1
                tint.inputs[2].default_value=(*tint_values[name],1)
                material.node_tree.links.new(texture,tint.inputs[1])
                material.node_tree.links.new(tint.outputs[0],shader.inputs["Base Color"])
            if name in ("Water","Glass"):
                shader=material.node_tree.nodes.get("Principled BSDF")
                shader.inputs["Roughness"].default_value=.28
                shader.inputs["Metallic"].default_value=.12
        else:
            material=bpy.data.materials.new(name)
            material.diffuse_color=(*color,1)
            material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value=(*color,1)
        materials[name]=material
    definition,exporter=build_scene(materials)
    parts=list(exporter.parts.values())
    # 整数坐标省略小数尾缀，保持数值不变并避免单个资源超过远端文件上限。
    for part in parts:
        part["vertices"]=[int(value) if float(value).is_integer() else value for value in part["vertices"]]
    canonical=json.dumps(definition,ensure_ascii=False,separators=(",",":"),allow_nan=False)
    serialized_parts=json.dumps(parts,ensure_ascii=False,separators=(",",":"),allow_nan=False)
    definition["contentHash"]=hashlib.sha256(canonical.encode()+b"\n"+serialized_parts.encode()).hexdigest()
    (DATA/"map-definition.json").write_text(json.dumps(definition,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    (DATA/"map-geometry.json").write_text('{"schemaVersion":1,"mapId":"'+MAP_ID+'","contentHash":"'+definition["contentHash"]+'","parts":'+serialized_parts+'}\n',encoding="utf-8")
    summary={"mapId":MAP_ID,"contentHash":definition["contentHash"],"sizeMeters":[SIZE,SIZE],"areaSquareKm":144,"seed":SEED,
             "coordinateUnit":"meter","buildingsBaked":True,"meshParts":len(parts),"triangles":sum(len(part["triangles"])//3 for part in parts),
             "buildings":len(definition["buildings"]),"modelCounts":dict(Counter(item["model"] for item in definition["buildings"])),
             "trees":exporter.tree_count,"nodes":len(definition["nodes"]),"roads":len(definition["roads"]),"bridges":3,"regions":REGIONS,
             "source":"原创 Blender 米制模型；仅参考 Broken Arrow 官方设施类型，不使用游戏提取资产",
             "reference":"https://www.matrixgames.com/news/broken-arrow-dev-diary-11-upcoming-beta-maps-and-tools"}
    (OUTPUT/"map-build-summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT/"青岚_12km策略地图.blend"),compress=True)
    print("MAP_EXPORTED "+json.dumps(summary,ensure_ascii=True),flush=True)
    if "--no-render" not in sys.argv:
        render_preview()
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT/"青岚_12km策略地图.blend"),compress=True)
    print("STRATEGIC_MAP_COMPLETE",flush=True)


if __name__=="__main__":
    main()
