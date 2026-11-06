"""从当前已发布几何导出独立、可追溯的移动测试区域，不修改显示地图。"""

import base64
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.spatial import height


def export() -> None:
    """将明确的显示图层转换为本次测试发布的语义区域并记录转换政策。"""
    folder = ROOT / 'unity-client/Assets/Sandbox/Resources/Maps/QinglanValley'
    definition_raw = (folder / 'map-definition.json').read_bytes()
    geometry_raw = (folder / 'map-geometry.json').read_bytes()
    definition = json.loads(definition_raw)
    geometry = json.loads(geometry_raw)
    cell = 5
    columns, rows = int(definition['width'] / cell), int(definition['length'] / cell)
    grid = bytearray([1]) * (columns * rows)
    # 编码为未知、坚实地、农田、疏林、密林、水域、建筑、泥地、道路、桥面、碎石路。
    names = ['unknown', 'open', 'field', 'sparse_forest', 'dense_forest', 'water', 'obstacle', 'mud', 'road', 'bridge', 'gravel']

    def paint(part, value, elevated=False):
        """按三角形实际投影采样区域；建筑投影保守覆盖与网格相交的包围单元。"""
        vertices, triangles = part['vertices'], part['triangles']
        for offset in range(0, len(triangles), 3):
            corners = [vertices[triangles[offset + i] * 3:triangles[offset + i] * 3 + 3] for i in range(3)]
            if elevated:
                center = (sum(p[0] for p in corners) / 3, sum(p[2] for p in corners) / 3)
                if max(p[1] for p in corners) - height(definition, center) < 0.5:
                    continue
            min_column = max(0, math.floor(min(p[0] for p in corners) / cell))
            max_column = min(columns - 1, math.floor(max(p[0] for p in corners) / cell))
            min_row = max(0, math.floor(min(p[2] for p in corners) / cell))
            max_row = min(rows - 1, math.floor(max(p[2] for p in corners) / cell))
            if min_column > max_column or min_row > max_row:
                continue
            first, second, third = [(p[0], p[2]) for p in corners]
            cross = lambda start, end, point: (end[0]-start[0])*(point[1]-start[1])-(end[1]-start[1])*(point[0]-start[0])
            for row in range(min_row, max_row + 1):
                for column in range(min_column, max_column + 1):
                    point = ((column + .5) * cell, (row + .5) * cell)
                    signs = [cross(first, second, point), cross(second, third, point), cross(third, first, point)]
                    if elevated or not (min(signs) < -0.001 and max(signs) > 0.001):
                        grid[row * columns + column] = value

    parts = geometry['parts']
    for layer, materials, value in [('farmland', {'FieldGreen', 'FieldGold'}, 2), ('scrub', {'ForestFloor'}, 4),
                                     ('landscape', {'Mud'}, 7), ('water', {'Water'}, 5)]:
        for part in parts:
            if part['layer'] == layer and part['material'] in materials:
                paint(part, value)
    # 林缘三十米为测试疏林，内部保留密林；这是一条显式测试标注政策。
    forests = bytes(grid)
    for index, value in enumerate(forests):
        if value != 4:
            continue
        column, row = index % columns, index // columns
        if any(0 <= column + dx < columns and 0 <= row + dz < rows
               and forests[(row + dz) * columns + column + dx] != 4
               for dx, dz in ((-6, 0), (6, 0), (0, -6), (0, 6))):
            grid[index] = 3
    for part in parts:
        if part['layer'] == 'routes' and part['material'] in ('Asphalt', 'Gravel', 'Paving'):
            paint(part, 10 if part['material'] == 'Gravel' else 8)
    nodes = {node['id']: node for node in definition['nodes']}
    for road in definition['roads']:
        if not road['isBridge']:
            continue
        start, end = nodes[road['startId']], nodes[road['endId']]
        from backend.spatial import segment_distance
        for row in range(max(0, int((min(start['z'], end['z'])-road['width'])/cell)), min(rows, int((max(start['z'], end['z'])+road['width'])/cell)+1)):
            for column in range(max(0, int((min(start['x'], end['x'])-road['width'])/cell)), min(columns, int((max(start['x'], end['x'])+road['width'])/cell)+1)):
                point = ((column+.5)*cell, (row+.5)*cell)
                if segment_distance(point, (start['x'], start['z']), (end['x'], end['z'])) <= road['width']/2:
                    grid[row*columns+column] = 9
    building_layers = {'settlement', 'logistics', 'facilities', 'central_village', 'village'}
    for part in parts:
        if part['layer'] in building_layers and part['material'] not in ('Asphalt', 'Gravel', 'Paving', 'Marking', 'Grass'):
            paint(part, 6, elevated=True)
    output = dict(schema_version='qinglan-movement-map-v1', map_id=definition['mapId'],
                  source_sha256=hashlib.sha256(definition_raw).hexdigest(), geometry_sha256=hashlib.sha256(geometry_raw).hexdigest(),
                  cell_m=cell, columns=columns, rows=rows, terrain_names=names,
                  conversion_policy='published-geometry-conservative-v1',
                  policies=dict(unclassified_ground='explicit_test_firm_ground', forest_edge_m=30,
                                building_envelope='elevated_triangle_aabb_cells', air='playable_boundary_no_restricted_zones'),
                  no_fly_polygons=[], grid_sha256=hashlib.sha256(grid).hexdigest(),
                  grid_zlib_base64=base64.b64encode(zlib.compress(grid, 9)).decode(),
                  counts={names[key]: value for key, value in sorted(Counter(grid).items())})
    target = ROOT / 'backend/movement-map.json'
    target.write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':'))+'\n', encoding='utf-8')
    print(json.dumps(dict(file=str(target), cells=len(grid), counts=output['counts']), ensure_ascii=False))


if __name__ == '__main__':
    export()
