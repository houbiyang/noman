"""同源五据点叠加层；独立于底图几何版本，不改写旧场景和通行网格。"""
from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
import math

from backend.demo_domain import MAP_PATH, require

CAPTURE_PATH = MAP_PATH.with_name('capture-points.json')


def validate(layout, source):
    """在发布/加载时拒绝错底图、无效圆域及非桥面引用。"""
    require(layout.get('schemaVersion') == 1 and layout.get('coordinateUnit') == 'meter'
            and layout.get('baseMapId') == source['mapId']
            and layout.get('baseMapContentHash') == source['contentHash'],
            'CAPTURE_MAP_MISMATCH', '据点层与底图版本不一致', 422)
    points = layout.get('points', [])
    require(len(points) == 5 and {p['id'] for p in points} == {f'P-{i:02}' for i in range(1, 6)},
            'CAPTURE_POINTS_INVALID', '新版地图必须包含五个独立据点', 422)
    roads = {r['id']: r for r in source['roads']}
    for point in points:
        require(all(type(point.get(k)) in (int, float) and math.isfinite(point[k]) for k in ('x', 'z', 'radius'))
                and 0 <= point['x'] <= source['width'] and 0 <= point['z'] <= source['length']
                and point['radius'] == 250 and point.get('initialOwner') == 'neutral' and point.get('kind') == 'capture',
                'CAPTURE_POINTS_INVALID', '据点坐标、初始归属或半径无效', 422)
        bridge = point.get('support') == 'bridge_deck'
        require((bridge and point.get('areaPolicy') == 'circle_intersect_bridge_deck'
                 and point.get('bridgeRoadId') in roads and roads[point['bridgeRoadId']]['isBridge'])
                or (point.get('support') == 'ground' and point.get('areaPolicy') == 'circle' and not point.get('bridgeRoadId')),
                'CAPTURE_SUPPORT_INVALID', '据点支撑面与面积政策无效', 422)
        if bridge:
            require(on_deck(point['x'], point['z'], deck(source, point['bridgeRoadId'])),
                    'CAPTURE_SUPPORT_INVALID', '桥面点中心不在指定桥面', 422)


def deck(source, road_id):
    """冻结桥面有限矩形，不能将桥下/河道当作占领面。"""
    road = next(r for r in source['roads'] if r['id'] == road_id)
    nodes = {n['id']: n for n in source['nodes']}
    return dict(start={k: nodes[road['startId']][k] for k in ('x', 'z')},
                end={k: nodes[road['endId']][k] for k in ('x', 'z')},
                width=road['width'], elevation=road['surfaceHeight'],
                navigation_layer_id='bridge-deck:bridge:' + road_id)


def on_deck(x, z, surface):
    """有限桥面矩形包含测试；无端部圆帽，不扩张到河岸/水面。"""
    dx, dz = surface['end']['x'] - surface['start']['x'], surface['end']['z'] - surface['start']['z']
    px, pz = x - surface['start']['x'], z - surface['start']['z']
    length2 = dx * dx + dz * dz
    along = px * dx + pz * dz
    return 0 <= along <= length2 and (px * dz - pz * dx) ** 2 <= surface['width'] ** 2 / 4 * length2


def on_support(point, x, z, unit):
    """发布路面是当前移动器唯一可达层；显式桥下层或错误高程不能参与桥面占领。"""
    if point.get('support') != 'bridge_deck':
        return True
    if not on_deck(float(x), float(z), point['deck']):
        return False
    layer = point['navigation_layer_id']
    if unit.get('navigation_layer_id', layer) != layer:
        return False
    elevation = unit.get('surface_elevation_m')
    return elevation is None or Decimal(str(elevation)) == Decimal(str(point['deck']['elevation']))


@lru_cache(maxsize=1)
def current_layout():
    raw = CAPTURE_PATH.read_bytes()
    value = json.loads(raw)
    validate(value, json.loads(MAP_PATH.read_bytes()))
    return value, hashlib.sha256(raw).hexdigest()


def objectives(source, layout=None):
    """将同源配置转换为可冻结在场景/规则内的米制占点定义。"""
    if layout is None:
        layout, _ = current_layout()
    validate(layout, source)
    result = []
    for p in layout['points']:
        item = dict(point_id=p['id'], name=p['name'], x=str(p['x']), z=str(p['z']), radius_m=p['radius'],
                    initial_owner=p['initialOwner'], area_policy=p['areaPolicy'], support=p['support'],
                    navigation_layer_id='ground')
        if p['support'] == 'bridge_deck':
            item['bridge_road_id'] = p['bridgeRoadId']
            item['deck'] = deck(source, p['bridgeRoadId'])
            item['navigation_layer_id'] = item['deck']['navigation_layer_id']
        result.append(item)
    return deepcopy(result)
