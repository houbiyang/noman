"""冻结用户确认的基础地表标注，不覆盖旧测试网格、场景或用户对局。

此标注仅消除GroundLow/GroundHigh的分类歧义。其余覆盖层原样保留；它不是
全地图畅通证明。CLI仅写显式指定的本地输出，不连接数据库。
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from backend.battle_terrain import SupportTriangle, rational
from backend.demo_domain import MAP_PATH, require
from backend.runtime import sha256

POLICY_PATH = Path(__file__).with_name('battle-terrain-policy-v1.json')
MANIFEST_PATH = Path(__file__).with_name('battle-terrain-annotations-v1.json')
GEOMETRY_PATH = MAP_PATH.with_name('map-geometry.json')
SCHEMA = 'qinglan-base-ground-annotations-v1'


def policy():
    value = json.loads(POLICY_PATH.read_bytes())
    require(value.get('schema_version') == 'qinglan-base-ground-policy-v1'
            and value.get('base_materials') == {'GroundLow': 'open', 'GroundHigh': 'open'}
            and value.get('base_layers') == ['terrain', 'landscape']
            and value.get('other_parts') == 'preserve_existing_no_reclassification'
            and value.get('outside_playable_boundary') == 'forbidden'
            and value.get('overlays_must_be_resolved_before_ground_use') is True
            and value.get('grants_route_permission') is False
            and value.get('changes_existing_games') is False
            and value.get('bridge_grade_policy') == 'actual_support_triangles_not_surfaceHeight_or_riverbed',
            'BATTLE_TERRAIN_POLICY_INVALID', '基础地表政策超出本次确认范围')
    return value


def build_manifest(definition_raw, geometry_raw, approved_policy=None):
    """绑定两份原始资产及确认政策的摘要；不以网格中心采样替代真实面。"""
    approved_policy = deepcopy(policy() if approved_policy is None else approved_policy)
    # 禁止调用者通过传入另一政策扩大本次已确认范围。
    require(approved_policy == policy(), 'BATTLE_TERRAIN_POLICY_INVALID', '必须使用本次已确认的冻结政策')
    definition, geometry = json.loads(definition_raw), json.loads(geometry_raw)
    require(definition.get('mapId') == geometry.get('mapId')
            and definition.get('contentHash') == geometry.get('contentHash')
            and definition.get('coordinateUnit') == 'meter'
            and definition.get('width') == 12000 and definition.get('length') == 12000,
            'BATTLE_MAP_VERSION_INVALID', '地表标注必须绑定同一十二公里米制地图')
    require(approved_policy.get('approved_map') == dict(map_id=definition['mapId'],
                definition_sha256=hashlib.sha256(definition_raw).hexdigest(),
                geometry_sha256=hashlib.sha256(geometry_raw).hexdigest()),
            'BATTLE_TERRAIN_APPROVAL_STALE', '资产不是本次确认的地图，需要新的明确标注版本')
    parts = geometry.get('parts')
    require(isinstance(parts, list) and len(parts) > 0,
            'BATTLE_MAP_VERSION_INVALID', '缺少地图几何')
    annotations, identities = [], set()
    for part in parts:
        name, layer, material = (part.get(key) for key in ('name', 'layer', 'material'))
        require(all(isinstance(v, str) and v for v in (name, layer, material)) and name not in identities,
                'BATTLE_MAP_VERSION_INVALID', '几何部件标识缺失或重复')
        identities.add(name)
        annotation = dict(part_id=name, source_layer=layer, source_material=material,
                          action='preserve_existing')
        if layer in approved_policy['base_layers'] and material in approved_policy['base_materials']:
            annotation.update(action='classify_base_ground', base_surface='open')
        annotations.append(annotation)
    require(any(a['action'] == 'classify_base_ground' for a in annotations),
            'BATTLE_MAP_VERSION_INVALID', '当前地图不存在已确认的基础地表')
    value = dict(schema_version=SCHEMA, policy=approved_policy, policy_sha256=sha256(approved_policy),
                 map_id=definition['mapId'], map_content_hash=definition['contentHash'],
                 definition_sha256=hashlib.sha256(definition_raw).hexdigest(),
                 geometry_sha256=hashlib.sha256(geometry_raw).hexdigest(),
                 playable_bounds_m=dict(min_x=0, max_x=definition['width'], min_z=0, max_z=definition['length']),
                 parts=sorted(annotations, key=lambda a: a['part_id']),
                 acceptance=dict(base_ground_classified=True, production_ready=False,
                     overlays_resolved=False, support_layers_validated=False,
                     routes_validated=False, bridges_validated=False,
                     note='基础分类不授予通行；覆盖层、支撑层及完整占地须另行核验'))
    value['payload_sha256'] = sha256(value)
    return value


class TerrainAssets:
    """只读资产验证及真实面提取。刻意不实现Geometry，不能注入生产结算。"""
    def __init__(self, definition_raw, geometry_raw, manifest):
        expected = build_manifest(definition_raw, geometry_raw)
        require(manifest == expected, 'BATTLE_TERRAIN_MANIFEST_INVALID', '地表标注或绑定资产已变更，拒绝旧标注')
        self.manifest = deepcopy(expected)
        self._definition = json.loads(definition_raw)
        self._parts = {p['name']: p for p in json.loads(geometry_raw)['parts']}
        self._annotations = {p['part_id']: p for p in expected['parts']}

    @classmethod
    def current(cls):
        return cls(MAP_PATH.read_bytes(), GEOMETRY_PATH.read_bytes(), json.loads(MANIFEST_PATH.read_bytes()))

    @property
    def source_definition(self):
        """原定义字段只读副本；语义目录复用明确来源，不按部件名字猜规则。"""
        return deepcopy(self._definition)

    def annotation(self, part_id):
        require(part_id in self._annotations, 'BATTLE_SURFACE_UNRESOLVED', '未知几何部件不可默认开阔地')
        return deepcopy(self._annotations[part_id])

    def part_ids(self):
        """稳定部件ID，不暴露可变原始网格。"""
        return tuple(sorted(self._parts))

    def _mesh(self, part_id):
        require(part_id in self._parts, 'BATTLE_SUPPORT_INVALID', '支撑几何不存在')
        part = self._parts[part_id]
        vertices, indices = part.get('vertices'), part.get('triangles')
        require(isinstance(vertices, list) and len(vertices) > 0 and len(vertices) % 3 == 0
                and isinstance(indices, list) and len(indices) % 3 == 0,
                'BATTLE_SUPPORT_INVALID', '三角面索引或顶点数据无效')
        return vertices, indices

    def part_bounds(self, part_id):
        """真实网格的xz包围盒，只用于排除不相交部件，不授予通行。"""
        vertices, _ = self._mesh(part_id)
        xs = [rational(v) for v in vertices[0::3]]
        zs = [rational(v) for v in vertices[2::3]]
        return min(xs), min(zs), max(xs), max(zs)

    def part_bounds3d(self, part_id):
        """真实三维包围盒用于射线候选筛选；不能用xz投影判定遮挡。"""
        vertices, _ = self._mesh(part_id)
        axes = [tuple(rational(v) for v in vertices[i::3]) for i in range(3)]
        return tuple(min(axis) for axis in axes) + tuple(max(axis) for axis in axes)

    def face_count(self, part_id):
        return len(self._mesh(part_id)[1]) // 3

    def face_vertices(self, part_id, triangle_index):
        """包括垂直面；调用者不得把不能支撑的面悄悄当作平地。"""
        vertices, indices = self._mesh(part_id)
        require(type(triangle_index) is int and 0 <= triangle_index < len(indices)//3,
                'BATTLE_SUPPORT_INVALID', '三角面索引或顶点数据无效')
        corners = indices[triangle_index*3:triangle_index*3+3]
        require(all(type(i) is int and 0 <= i < len(vertices)//3 for i in corners),
                'BATTLE_SUPPORT_INVALID', '三角面引用了不存在的顶点')
        return tuple(tuple(rational(v) for v in vertices[i*3:i*3+3]) for i in corners)

    def triangle(self, part_id, triangle_index):
        """按原始索引返回真实面；不用height插值、surfaceHeight或零坡度。"""
        return SupportTriangle(self.face_vertices(part_id, triangle_index))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='显式导出基础地表标注；不写数据库')
    parser.add_argument('--check', action='store_true', help='只读核验已导出的标注与源资产')
    args = parser.parse_args()
    if args.output is not None:
        # 防止误写地图、政策、旧移动网格或任意已有业务文件。
        target = args.output.resolve()
        require(not target.exists(), 'BATTLE_TERRAIN_OUTPUT_EXISTS', '输出已存在，拒绝覆盖；请显式使用新的文件名')
        content = json.dumps(build_manifest(MAP_PATH.read_bytes(), GEOMETRY_PATH.read_bytes()),
                             ensure_ascii=False, indent=2) + '\n'
        with target.open('x') as output:
            output.write(content)
    elif args.check:
        assets = TerrainAssets.current()
        count = sum(p['action'] == 'classify_base_ground' for p in assets.manifest['parts'])
        print(f'PASS: {count} base-ground parts classified as open; other parts preserved; production_ready=false')
    else:
        parser.error('需要--output或--check；没有隐式写入')


if __name__ == '__main__':
    main()
