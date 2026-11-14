"""核对现有发布地图的正式规则前置条件，全程只读地图和场景资产。"""

import argparse
import hashlib
import json
import math
from pathlib import Path

from backend.fivepoint_delivery import REQUIRED_MAP_FIELDS, PROJECT_ROOT, MAP_FOLDER
from backend.fivepoint_surfaces import SurfaceIndex


def surface_heights(parts: list, horizontal: float, depth: float) -> list:
    """求已发布铺装三角面在给定平面坐标处的真实高度，不采用双线性近似。

    Args:
        parts: 当前发布几何的路面片段。
        horizontal: 地图横向米制坐标。
        depth: 地图纵向米制坐标。

    Returns:
        覆盖该坐标的全部不同路面高度；空列表表示缺失支撑。
    """
    heights = set()
    for part in parts:
        if part.get('layer') != 'routes' or part.get('material') != 'Asphalt':
            continue
        vertices, triangles = part['vertices'], part['triangles']
        for offset in range(0, len(triangles), 3):
            corners = [vertices[triangles[offset + index] * 3:triangles[offset + index] * 3 + 3] for index in range(3)]
            if not (min(point[0] for point in corners) - 1e-7 <= horizontal <= max(point[0] for point in corners) + 1e-7
                    and min(point[2] for point in corners) - 1e-7 <= depth <= max(point[2] for point in corners) + 1e-7):
                continue
            first, second, third = corners
            determinant = ((second[2] - third[2]) * (first[0] - third[0])
                           + (third[0] - second[0]) * (first[2] - third[2]))
            if abs(determinant) < 1e-12:
                continue
            first_weight = ((second[2] - third[2]) * (horizontal - third[0])
                            + (third[0] - second[0]) * (depth - third[2])) / determinant
            second_weight = ((third[2] - first[2]) * (horizontal - third[0])
                             + (first[0] - third[0]) * (depth - third[2])) / determinant
            third_weight = 1 - first_weight - second_weight
            if min(first_weight, second_weight, third_weight) >= -1e-9:
                heights.add(round(first_weight * first[1] + second_weight * second[1] + third_weight * third[1], 9))
    return sorted(heights)


def inspect_map(definition: dict, geometry: dict, rule_map: dict | None = None) -> dict:
    """核对地图身份和每座桥两端的实际铺装高差；缺失正式语义单独列出。

    Args:
        definition: 当前显示地图定义。
        geometry: 与定义绑定的已发布三角面。
        rule_map: 可选的正式规则语义文件，缺失时不猜测通行范围。

    Returns:
        可序列化的身份、桥头高差和缺失项报告；不会发布或修补地图。
    """
    identity = all(definition.get(key) == geometry.get(key) and definition.get(key)
                   for key in ('mapId', 'contentHash'))
    nodes = {node['id']: node for node in definition['nodes']}
    bridge_ends, failures = [], []
    for road in definition['roads']:
        if not road.get('isBridge'):
            continue
        for endpoint in ('startId', 'endId'):
            node = nodes[road[endpoint]]
            heights = surface_heights(geometry['parts'], node['x'], node['z'])
            gap = max(heights) - min(heights) if heights else None
            record = dict(road_id=road['id'], node_id=node['id'], xy=[node['x'], node['z']],
                          declared_bridge_height_m=road['surfaceHeight'], surface_heights_m=heights, height_gap_m=gap)
            bridge_ends.append(record)
            if not heights or not any(abs(altitude - road['surfaceHeight']) <= .01 for altitude in heights):
                failures.append(dict(record, code='BRIDGE_SURFACE_MISSING'))
            elif gap > .01:
                failures.append(dict(record, code='BRIDGE_APPROACH_DISCONTINUITY'))
    missing = [key for key in REQUIRED_MAP_FIELDS if not (rule_map or {}).get(key)]
    return dict(map_id=definition['mapId'], identity_matches=bool(identity),
                geometry_ready=bool(identity) and not failures, release_ready=False,
                bridge_ends=bridge_ends, bridge_discontinuities=failures, missing_semantics=missing,
                note='桥头检查是必要条件；通过不代表完整占地、坡度、森林、服务、通路和公平性验收已完成。')


def inspect_bridge_corridors(definition: dict, geometry: dict, approach_m: float = 100) -> list:
    """连续核对三桥及两端接坡的中线和两侧线，不把端点相接当作整桥通过。

    三条线用于发现实际发布面的缺口、高差和局部陡坡；完整单位扫掠、
    转弯及动态阻挡仍须由正式规则地图验证，不能使用本检查替代。
    """
    surfaces = SurfaceIndex([part for part in geometry['parts']
                             if part['layer'] == 'routes' and part['material'] == 'Asphalt'])
    nodes = {node['id']: node for node in definition['nodes']}
    reports = []
    for road in definition['roads']:
        if not road.get('isBridge'):
            continue
        first, last = nodes[road['startId']], nodes[road['endId']]
        delta = (last['x'] - first['x'], last['z'] - first['z'])
        length = math.hypot(*delta)
        if not length or road['width'] < 8:
            reports.append(dict(road_id=road['id'], passed=False, code='BRIDGE_CORRIDOR_INVALID'))
            continue
        direction = (delta[0] / length, delta[1] / length)
        for lateral in sorted({-road['width'] / 2 + 4, 0., road['width'] / 2 - 4}):
            start = (first['x'] - direction[0] * approach_m - direction[1] * lateral,
                     first['z'] - direction[1] * approach_m + direction[0] * lateral)
            end = (last['x'] + direction[0] * approach_m - direction[1] * lateral,
                   last['z'] + direction[1] * approach_m + direction[0] * lateral)
            traced = surfaces.trace(start, end)
            reports.append(dict(road_id=road['id'], lateral_m=lateral, start_xz=list(start), end_xz=list(end),
                continuous=traced['continuous'], maximum_grade=traced['maximum_grade'],
                gaps=traced['gaps'], discontinuities=traced['discontinuities'], ambiguities=traced['ambiguities'],
                passed=traced['continuous'] and traced['maximum_grade'] <= .2))
    return reports


def audit(root: Path = PROJECT_ROOT) -> dict:
    """读取本项目当前显示地图并绑定原始双摘要，生成可复核报告。"""
    folder = root / MAP_FOLDER
    definition_raw = (folder / 'map-definition.json').read_bytes()
    geometry_raw = (folder / 'map-geometry.json').read_bytes()
    rule_path = root / 'backend/fivepoint-map.json'
    rule_map = json.loads(rule_path.read_text(encoding='utf-8-sig')) if rule_path.exists() else None
    definition, geometry = json.loads(definition_raw), json.loads(geometry_raw)
    result = inspect_map(definition, geometry, rule_map)
    result['bridge_corridors'] = inspect_bridge_corridors(definition, geometry)
    result['geometry_ready'] = result['geometry_ready'] and all(item['passed'] for item in result['bridge_corridors'])
    result.update(source_sha256=hashlib.sha256(definition_raw).hexdigest(),
                  geometry_sha256=hashlib.sha256(geometry_raw).hexdigest())
    return result


def main() -> None:
    """显式运行只读地图审计；未满足正式发布条件时返回非零退出码。"""
    parser = argparse.ArgumentParser(description='只读检查五点地图桥头与规则数据')
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    report = audit()
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(payload + '\n', encoding='utf-8')
    print(payload)
    raise SystemExit(0 if report['release_ready'] else 1)


if __name__ == '__main__':
    main()
