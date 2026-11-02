"""通过本机 Blender 生成战损建筑、可平铺材质和同源地图渲染，不安装扩展。"""

from __future__ import annotations

import array
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector


CLIENT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = CLIENT_ROOT / "Assets/Sandbox/Resources/Battlefield"
PREVIEW_ROOT = CLIENT_ROOT / "MapPreview/Battlefield"
SURFACES = {
    "Ground": (137, 137, 112), "Soil": (121, 108, 88), "Road": (132, 130, 117),
    "Plaster": (181, 175, 157), "Brick": (131, 89, 69), "Concrete": (151, 148, 134),
    "Tile": (111, 94, 82), "Timber": (83, 72, 58), "Ash": (72, 70, 63),
    "Foliage": (86, 101, 64), "Water": (75, 91, 85), "Metal": (97, 103, 99),
}


def noise_value(horizontal: float, vertical: float, seed: int, period: int = 32) -> float:
    """计算周期插值噪声，纹理两侧使用相同采样以保持可平铺。"""
    size = period
    lower_x, lower_y = math.floor(horizontal), math.floor(vertical)
    blend_x, blend_y = horizontal-lower_x, vertical-lower_y
    blend_x, blend_y = blend_x*blend_x*(3-2*blend_x), blend_y*blend_y*(3-2*blend_y)

    def lattice(x, y):
        value = ((x % size)*374761393+(y % size)*668265263+seed*1442695041) & 0xffffffff
        value = ((value ^ (value >> 13))*1274126177) & 0xffffffff
        return (value ^ (value >> 16))/4294967295

    return ((1-blend_x)*lattice(lower_x, lower_y)+blend_x*lattice(lower_x+1, lower_y))*(1-blend_y) + \
        ((1-blend_x)*lattice(lower_x, lower_y+1)+blend_x*lattice(lower_x+1, lower_y+1))*blend_y


def write_image(name: str, pixels: array.array, size: int, normal: bool = False) -> None:
    """将生成像素写为 PNG；法线贴图使用非颜色数据。"""
    image = bpy.data.images.new(name, width=size, height=size, alpha=True)
    image.colorspace_settings.name = "Non-Color" if normal else "sRGB"
    image.pixels.foreach_set(pixels)
    image.filepath_raw = str(ASSET_ROOT / "Textures" / (name+".png"))
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def generate_textures() -> None:
    """生成十二组底色与切线法线贴图；素材由本工程原创生成。"""
    size = 512
    for seed, (name, color) in enumerate(SURFACES.items(), start=1):
        colors = array.array("f", [0])*(size*size*4)
        heights = array.array("f", [0])*(size*size)
        for row in range(size):
            for column in range(size):
                horizontal, vertical = column/size, row/size
                coarse = noise_value(horizontal*8, vertical*8, seed, 8)
                detail = noise_value(horizontal*32, vertical*32, seed+19)
                grain = noise_value(horizontal*128, vertical*128, seed+41, 128)
                height = coarse*0.45+detail*0.35+grain*0.2
                variation = (coarse-0.5)*0.3+(detail-0.5)*0.25+(grain-0.5)*0.2
                if name in ("Brick", "Tile"):
                    courses = 12 if name == "Brick" else 8
                    course = int(vertical*courses)
                    mortar = (vertical*courses % 1 < 0.06 or (horizontal*8+course%2*0.5) % 1 < 0.035)
                    if mortar:
                        variation -= 0.27
                        height -= 0.5
                    variation += (noise_value((int(horizontal*8)+course%2)*4, course*4, seed)-0.5)*0.2
                elif name == "Timber":
                    variation += 0.12*math.sin(horizontal*math.tau*22+detail*3)
                    height += 0.12*math.sin(horizontal*math.tau*22+detail*3)
                elif name == "Plaster":
                    chipped = detail < 0.29 and coarse < 0.58
                    variation -= 0.24 if chipped else 0
                    height -= 0.3 if chipped else 0
                    if abs(math.sin(horizontal*math.tau*4+coarse*2+vertical*6)) < 0.025:
                        variation -= 0.2
                        height -= 0.2
                elif name in ("Road", "Soil", "Ground"):
                    # 远景地表只保留细微颗粒，避免高频法线在总览中变成重复棋盘格。
                    variation *= 0.35 if name == "Ground" else 0.5
                    variation -= 0.035 if grain < 0.19 else 0
                    height = (detail*0.5+grain*0.5)*0.12
                elif name == "Water":
                    variation *= 0.25
                    height = 0.06*math.sin(horizontal*math.tau*16+math.sin(vertical*math.tau*4))
                offset = (row*size+column)*4
                for channel in range(3):
                    colors[offset+channel] = max(0, min(1, color[channel]/255*(1+variation)))
                colors[offset+3] = 1
                heights[row*size+column] = height
        normals = array.array("f", [0])*(size*size*4)
        strength = 2.8 if name in ("Brick", "Tile") else 1.6
        for row in range(size):
            for column in range(size):
                gradient_x = (heights[row*size+(column-1)%size]-heights[row*size+(column+1)%size])*strength
                gradient_y = (heights[((row-1)%size)*size+column]-heights[((row+1)%size)*size+column])*strength
                length = math.sqrt(gradient_x**2+gradient_y**2+1)
                offset = (row*size+column)*4
                normals[offset:offset+4] = array.array("f", [gradient_x/length*0.5+0.5, gradient_y/length*0.5+0.5, 0.5+0.5/length, 1])
        write_image(name+"_Base", colors, size)
        write_image(name+"_Normal", normals, size, True)
        print("[战损资产] 贴图完成："+name, flush=True)


def create_materials() -> dict:
    """建立 Blender 节点材质，使用与 Unity 相同的 PNG 贴图。"""
    materials = {}
    for name, color in SURFACES.items():
        material = bpy.data.materials.new("Battle_"+name)
        material.diffuse_color = (*[value/255 for value in color], 1)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        shader = nodes.get("Principled BSDF")
        shader.inputs["Roughness"].default_value = 0.9 if name != "Metal" else 0.65
        if name == "Metal":
            shader.inputs["Metallic"].default_value = 0.55
        base = nodes.new("ShaderNodeTexImage")
        base.image = bpy.data.images.load(str(ASSET_ROOT / "Textures" / (name+"_Base.png")), check_existing=True)
        links.new(base.outputs["Color"], shader.inputs["Base Color"])
        normal = nodes.new("ShaderNodeTexImage")
        normal.image = bpy.data.images.load(str(ASSET_ROOT / "Textures" / (name+"_Normal.png")), check_existing=True)
        normal.image.colorspace_settings.name = "Non-Color"
        mapping = nodes.new("ShaderNodeNormalMap")
        mapping.inputs["Strength"].default_value = 0.65
        links.new(normal.outputs["Color"], mapping.inputs["Color"])
        links.new(mapping.outputs["Normal"], shader.inputs["Normal"])
        materials[name] = material
    return materials


class BuildingMesh:
    """汇总分材质建筑面，避免以数千个独立立方体导出。"""

    def __init__(self):
        self.vertices, self.faces, self.materials = [], [], []

    def face(self, vertices, material):
        """追加一个保持外法线绕序的多边形。"""
        offset = len(self.vertices)
        self.vertices.extend(vertices)
        self.faces.append(tuple(range(offset, offset+len(vertices))))
        self.materials.append(material)

    def box(self, center, dimensions, material, rotation=0):
        """追加可绕竖轴旋转的实心构件。"""
        horizontal, vertical = math.cos(rotation), math.sin(rotation)
        corners = []
        for direction_x, direction_y, direction_z in ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)):
            position_x, position_y = direction_x*dimensions[0]/2, direction_y*dimensions[1]/2
            corners.append((center[0]+position_x*horizontal-position_y*vertical,
                            center[1]+position_x*vertical+position_y*horizontal, center[2]+direction_z*dimensions[2]/2))
        for indices in ((0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)):
            self.face([corners[index] for index in indices], material)

    def beam(self, start, end, width, material):
        """以六面棱柱连接两点，表示椽木或外露支撑。"""
        direction = Vector(end)-Vector(start)
        normal = direction.normalized().cross(Vector((0,0,1)))
        if normal.length < 0.01:
            normal = Vector((1,0,0))
        normal.normalize()
        perpendicular = direction.normalized().cross(normal)
        rings = [[Vector(center)+normal*horizontal*width/2+perpendicular*vertical*width/2
                  for horizontal, vertical in ((-1,-1),(1,-1),(1,1),(-1,1))] for center in (start,end)]
        self.face(list(reversed(rings[0])), material)
        self.face(rings[1], material)
        for index in range(4):
            following = (index+1)%4
            self.face([rings[0][index],rings[0][following],rings[1][following],rings[1][index]],material)

    def rubble(self, center, scale, material, generator):
        """追加不规则封闭石块，避免瓦砾呈整齐方块。"""
        rings = []
        for height, radius in ((-0.35,0.7),(0.25,1),(0.65,0.35)):
            rings.append([(center[0]+math.cos(index*math.tau/5)*scale[0]*radius*generator.uniform(.75,1.2),
                           center[1]+math.sin(index*math.tau/5)*scale[1]*radius*generator.uniform(.75,1.2),
                           center[2]+height*scale[2]*generator.uniform(.8,1.2)) for index in range(5)])
        self.face(list(reversed(rings[0])),material)
        self.face(rings[-1],material)
        for level in range(2):
            for index in range(5):
                following=(index+1)%5
                self.face([rings[level][index],rings[level][following],rings[level+1][following],rings[level+1][index]],material)

    def object(self, name, materials):
        """创建一个具有米制平铺 UV 的 Blender 网格对象。"""
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.vertices, [], self.faces)
        mesh.update()
        instance = bpy.data.objects.new(name,mesh)
        bpy.context.collection.objects.link(instance)
        names = list(materials)
        for material in materials.values():
            mesh.materials.append(material)
        uv_layer = mesh.uv_layers.new(name="SurfaceUV")
        for polygon, material in zip(mesh.polygons,self.materials):
            polygon.material_index = names.index(material)
            normal = polygon.normal
            for loop_index in polygon.loop_indices:
                vertex = mesh.vertices[mesh.loops[loop_index].vertex_index].co
                if abs(normal.z)>0.6:
                    coordinate=(vertex.x/3,vertex.y/3)
                elif abs(normal.y)>abs(normal.x):
                    coordinate=(vertex.x/3,vertex.z/3)
                else:
                    coordinate=(vertex.y/3,vertex.z/3)
                uv_layer.data[loop_index].uv=coordinate
        return instance


def build_house(damage: int, materials: dict):
    """构造完整、轻损、重损或坍塌住宅，包含真实洞口和内部楼板。"""
    generator = random.Random(3721+damage*113)
    mesh = BuildingMesh()
    width, depth, height = 7.5, 6.3, 5.4
    mesh.box((0,0,.14),(width+.35,depth+.35,.28),"Concrete")
    mesh.box((0,0,2.68),(width-.45,depth-.45,.17),"Timber")
    if damage>=2:
        mesh.box((0,0,.3),(width-.4,depth-.4,.2),"Ash")
    # 按窗洞和门洞分割墙段，保留可从缺损处看到的室内空间。
    for side in range(4):
        length = width if side<2 else depth
        segments = 15 if side<2 else 13
        step = length/segments
        for column in range(segments):
            position=-length/2+(column+.5)*step
            damaged = damage>=2 and ((side==0 and position>-.8) or (damage==3 and side==3))
            top=height
            if damaged:
                top = generator.uniform(.6,2.9) if damage==3 else generator.uniform(2.3,4.5)
            for floor in range(2):
                lower, upper = floor*2.7,min(top,(floor+1)*2.7)
                if upper<=lower:
                    continue
                door = side==0 and abs(position)<.65 and floor==0
                window = (abs(position+2.15)<.55 or abs(position-2.15)<.55 or side>=2 and abs(position)<.55)
                intervals = [(lower,upper)]
                if door or window:
                    opening_bottom=lower if door else lower+.9
                    opening_top=lower+2.15
                    intervals=[(lower,min(upper,opening_bottom)),(max(lower,opening_top),upper)]
                for base, ceiling in intervals:
                    if ceiling-base<.025:
                        continue
                    center=(position,(-1 if side==0 else 1)*depth/2,(base+ceiling)/2) if side<2 else ((-1 if side==2 else 1)*width/2,position,(base+ceiling)/2)
                    dimensions=(step+.012,.3,ceiling-base) if side<2 else (.3,step+.012,ceiling-base)
                    mesh.box(center,dimensions,"Brick" if damaged and ceiling>top-.8 else "Plaster")
            if damaged:
                for brick in range(2):
                    center=(position,-depth/2-.025,top+brick*.12) if side==0 else (width/2+.01,position,top+brick*.12)
                    mesh.box(center,(.32,.34,.11),"Brick",generator.uniform(-.08,.08))
        # 窗框及窗台是独立细构件，不以黑色矩形代替墙洞。
        for floor in range(2):
            for position in (-2.15,2.15):
                if damage>=2 and side==0 and position>-.8:
                    continue
                if side>=2:
                    continue
                plane_y=(-1 if side==0 else 1)*(depth/2+.04)
                for offset in (-.58,.58):
                    mesh.box((position+offset,plane_y,floor*2.7+1.53),(.09,.13,1.35),"Timber")
                mesh.box((position,plane_y,floor*2.7+.88),(1.32,.36,.12),"Concrete")
                mesh.box((position,plane_y,floor*2.7+2.18),(1.24,.14,.1),"Timber")
                if damage<2:
                    mesh.box((position,plane_y,floor*2.7+1.55),(.065,.13,1.22),"Timber")
                    mesh.box((position,plane_y,floor*2.7+1.55),(1.18,.13,.065),"Timber")
    # 山墙及屋顶分成可缺失的瓦片，破损形状带固定随机扰动。
    for side in (-1,1):
        if damage<3:
            mesh.face([(-width/2,side*depth/2,height),(width/2,side*depth/2,height),(0,side*depth/2,height+1.65)],"Brick")
            mesh.face([(0,side*depth/2,height+1.65),(width/2,side*depth/2,height),(-width/2,side*depth/2,height)],"Brick")
    for row in range(13):
        position_y=-depth/2-.25+row*(depth+.5)/12
        for side in (-1,1):
            if damage==3 and row<9 or damage==2 and side==1 and row<7:
                continue
            mesh.beam((0,position_y,height+1.65),(side*(width/2+.3),position_y,height-.08),.12,"Timber")
        for side in (-1,1):
            for column in range(7):
                position_x=(column+.5)*(width/2+.35)/7
                missing=(damage==1 and side==1 and 3<row<8 and column<3) or (damage==2 and side==1 and row<9) or (damage==3 and (row<10 or side==1))
                missing=missing or (damage>0 and generator.random()<.045*damage)
                if missing:
                    continue
                lower=column*(width/2+.35)/7
                upper=(column+1)*(width/2+.35)/7
                mesh.face([(side*lower,position_y-.29,height+1.68-lower*.43),
                           (side*upper,position_y-.29,height+1.68-upper*.43),
                           (side*upper,position_y+.29,height+1.68-upper*.43),
                           (side*lower,position_y+.29,height+1.68-lower*.43)][::side],"Tile")
    if damage<3:
        mesh.box((-2,1.6,height+1.2),(.65,.7,2.1),"Brick")
        mesh.box((-2,1.6,height+2.3),(.85,.9,.16),"Concrete")
    if damage>0:
        for index in range(24+damage*24):
            angle=generator.uniform(0,math.tau)
            radius=generator.uniform(2.3,5.1)
            position_x,position_y=math.cos(angle)*radius,math.sin(angle)*radius*.8
            scale=generator.uniform(.16,.55)
            mesh.rubble((position_x,position_y,.15),(scale,scale*.75,scale*.8),"Brick" if index%3 else "Concrete",generator)
        for index in range(damage*5):
            mesh.box((generator.uniform(-3,3),generator.uniform(-3.2,2),.3),(.13,generator.uniform(.7,2.7),.13),"Timber",generator.uniform(-1.3,1.3))
    return mesh.object("House_"+str(damage),materials)


def export_library(materials: dict) -> dict:
    """输出四种住宅 FBX 和一套瓦砾簇，供 Unity 原生导入器读取。"""
    statistics = {}
    for damage in range(4):
        instance = build_house(damage,materials)
        bpy.ops.object.select_all(action="DESELECT")
        instance.select_set(True)
        bpy.context.view_layer.objects.active=instance
        bpy.ops.export_scene.fbx(filepath=str(ASSET_ROOT/"Models"/(instance.name+".fbx")),use_selection=True,
                                 object_types={"MESH"},axis_forward="-Z",axis_up="Y",apply_unit_scale=True,
                                 bake_space_transform=True,bake_anim=False,add_leaf_bones=False,use_triangles=True)
        instance.data.calc_loop_triangles()
        statistics[instance.name]={"triangles":len(instance.data.loop_triangles),"vertices":len(instance.data.vertices),"damage":damage}
        instance.location.x=damage*15
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(PREVIEW_ROOT/"战损建筑源模型.blend"))
    return statistics


def setup_render(location, target, output: Path) -> None:
    """设置独立 Cycles 成片相机与日光；不将离线效果冒充网页效果。"""
    scene=bpy.context.scene
    scene.render.engine="CYCLES"
    scene.cycles.samples=32
    scene.cycles.use_denoising=True
    scene.render.resolution_x=1440
    scene.render.resolution_y=960
    scene.render.resolution_percentage=100
    scene.world.color=(.3,.3,.3)
    background=scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value=(.54,.59,.65,1)
    background.inputs["Strength"].default_value=.55
    light_data=bpy.data.lights.new("阴天斜阳","SUN")
    light_data.energy=2.3
    light_data.angle=math.radians(12)
    sun=bpy.data.objects.new("阴天斜阳",light_data)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler=(math.radians(28),math.radians(-24),math.radians(-28))
    camera_data=bpy.data.cameras.new("战损河谷摄影机")
    camera=bpy.data.objects.new("战损河谷摄影机",camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location=location
    camera.rotation_euler=(Vector(target)-camera.location).to_track_quat("-Z","Y").to_euler()
    camera_data.lens=48
    camera_data.clip_end=3000
    scene.camera=camera
    scene.view_settings.view_transform="AgX"
    scene.render.image_settings.file_format="PNG"
    scene.render.filepath=str(output)


def render_map(materials: dict) -> None:
    """将与网页同源的地图和建筑放入 Blender，保存可编辑场景并离线渲染。"""
    data_root=CLIENT_ROOT/"Assets/Sandbox/Resources/Maps/QinglanValley"
    definition=json.loads((data_root/"map-definition.json").read_text(encoding="utf-8"))
    geometry=json.loads((data_root/"map-geometry.json").read_text(encoding="utf-8"))
    source_materials={material["id"]:material for material in definition["materials"]}
    surface_map={"GroundLow":"Ground","GroundHigh":"Ground","Rock":"Concrete","Meadow":"Ground","MeadowDry":"Ground","Earth":"Soil",
                 "Water":"Water","Road":"Road","Bridge":"Concrete","Wall":"Plaster","Roof":"Metal","Forest":"Foliage","ForestLight":"Foliage",
                 "Wood":"Timber","Bank":"Soil","Mud":"Soil","Soil":"Soil","FieldGreen":"Ground","FieldGold":"Soil","Plaster":"Plaster","Tile":"Tile","Asphalt":"Road","Glass":"Ash",
                 "Paving":"Concrete","Gravel":"Soil","Grass":"Ground"}
    for identifier,tint in {"Asphalt":(.52,.56,.58),"Paving":(.84,.87,.84),"Gravel":(.89,.9,.85),"Grass":(.69,.79,.58)}.items():
        surface=surface_map[identifier]
        material=materials[surface].copy()
        shader=material.node_tree.nodes.get("Principled BSDF")
        color_input=shader.inputs["Base Color"]
        texture=color_input.links[0].from_socket
        multiply=material.node_tree.nodes.new("ShaderNodeMixRGB")
        multiply.blend_type="MULTIPLY"
        multiply.inputs[0].default_value=1
        multiply.inputs[2].default_value=(*tint,1)
        material.node_tree.links.new(texture,multiply.inputs[1])
        material.node_tree.links.new(multiply.outputs[0],color_input)
        materials[identifier]=material
        surface_map[identifier]=identifier
    for part in geometry["parts"]:
        if part["layer"] in ("grid","deployment","markers"):
            continue
        vertices=[(part["vertices"][offset],-part["vertices"][offset+2],part["vertices"][offset+1]) for offset in range(0,len(part["vertices"]),3)]
        faces=[part["triangles"][offset:offset+3] for offset in range(0,len(part["triangles"]),3)]
        builder=BuildingMesh()
        builder.vertices=vertices
        builder.faces=faces
        builder.materials=[surface_map.get(part["material"],"Concrete")]*len(faces)
        if part["layer"] in ("terrain","landscape","environment"):
            # 合并重复位置后再平滑，令离线地形与 Unity 的连续法线保持一致。
            unique_vertices=[]
            position_indices={}
            remapping=[]
            for vertex in vertices:
                if vertex not in position_indices:
                    position_indices[vertex]=len(unique_vertices)
                    unique_vertices.append(vertex)
                remapping.append(position_indices[vertex])
            builder.vertices=unique_vertices
            builder.faces=[[remapping[index] for index in face] for face in faces]
        instance=builder.object(part["name"],materials)
        if part["layer"] in ("routes","farmland","ditches","riverbanks","damage","ponds"):
            instance.visible_shadow=False
        if part["layer"] in ("terrain","landscape","environment"):
            for polygon in instance.data.polygons:
                polygon.use_smooth=True
    prototypes={damage:build_house(damage,materials) for damage in range(4)}
    for prototype in prototypes.values():
        prototype.hide_render=True
        prototype.hide_viewport=True
    for building in definition["buildings"]:
        instance=bpy.data.objects.new(building["id"],prototypes[building["damage"]].data)
        bpy.context.collection.objects.link(instance)
        instance.location=(building["x"],-building["z"],building["y"])
        instance.rotation_euler.z=math.radians(-building["yaw"])
        instance.scale=(building["scaleX"],building["scaleZ"],building["scaleY"])
    setup_render((155,-195,65),(132,-130,3),PREVIEW_ROOT/"战损河谷_Blender渲染.png")
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(PREVIEW_ROOT/"战损河谷完整场景.blend"))
    bpy.ops.render.render(write_still=True)
    scene=bpy.context.scene
    scene.camera.location=(285,-395,255)
    scene.camera.rotation_euler=(Vector((180,-120,0))-scene.camera.location).to_track_quat("-Z","Y").to_euler()
    scene.camera.data.lens=38
    scene.render.filepath=str(PREVIEW_ROOT/"战损河谷_Blender总览.png")
    bpy.ops.render.render(write_still=True)
    manifest={"mapId":definition["mapId"],"contentHash":definition["contentHash"],"renderer":"Blender Cycles",
              "samples":scene.cycles.samples,"resolution":[scene.render.resolution_x,scene.render.resolution_y],
              "buildings":len(definition["buildings"]),"images":["战损河谷_Blender渲染.png","战损河谷_Blender总览.png"]}
    (PREVIEW_ROOT/"render-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def main() -> None:
    """根据命令参数生成资产，或渲染已经生成的同源战损地图。"""
    for directory in (ASSET_ROOT/"Models",ASSET_ROOT/"Textures",PREVIEW_ROOT):
        directory.mkdir(parents=True,exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    arguments=sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    if "--render-map" not in arguments and "--models-only" not in arguments:
        generate_textures()
    materials=create_materials()
    if "--render-map" in arguments:
        render_map(materials)
    else:
        statistics=export_library(materials)
        manifest={"generator":"Blender "+bpy.app.version_string,"originalAssets":True,"textureSize":512,"textures":len(SURFACES)*2,"models":statistics}
        manifest["files"]={str(path.relative_to(ASSET_ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in ASSET_ROOT.rglob("*") if path.suffix in (".fbx",".png")}
        (PREVIEW_ROOT/"asset-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(manifest["models"]),flush=True)


if __name__=="__main__":
    main()
