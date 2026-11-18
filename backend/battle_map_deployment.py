"""Fail-closed deployment checks over trusted frozen SurfaceCatalog annotations.

Does not create games, write acceptance flags, or move submitted configurations.
Full Geometry must additionally validate five effective masks and all world rules.
"""
from fractions import Fraction as F
from itertools import combinations

from backend import battle_kernel as k
from backend.battle_map_motion import position_m, sweep
from backend.battle_map_surfaces import DEPLOYMENT, rectangle, _touch
from backend.battle_terrain import rational as q
from backend.demo_domain import require


def in_deployment(side, polygon):
    require(side in DEPLOYMENT, 'BATTLE_DEPLOYMENT_INVALID', '未知部署阵营')
    x0, z0, x1, z1 = DEPLOYMENT[side]
    require(all(x0 <= x <= x1 and z0 <= z <= z1 for x, z in polygon),
            'BATTLE_DEPLOYMENT_OUTSIDE', '完整占地不在本方部署区')


def _height(catalog, surface_id, position):
    point = q(position['x_mm'])/1000, q(position['z_mm'])/1000
    require(round(catalog.height(surface_id, point)*1000) == position['height_mm'],
            'BATTLE_SUPPORT_INVALID', '部署高程不是所选真实面')
    return point


def _distance_squared(a, b):
    return sum((x-y)**2 for x, y in zip(a, b))


def _disc_polygon(center, radius, polygon):
    """Exact disc/convex-polygon contact (for service-radius isolation)."""
    from backend.battle_map_surfaces import _point_inside
    if _point_inside(center, polygon):
        return True
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        v = tuple(y-x for x, y in zip(a, b))
        norm = sum(x*x for x in v)
        t = max(F(0), min(F(1), sum((x-y)*z for x, y, z in zip(center, a, v))/norm)) if norm else F(0)
        closest = tuple(x+t*y for x, y in zip(a, v))
        if _distance_squared(center, closest) <= radius**2:
            return True
    return False


class MapDeployment:
    def __init__(self, catalog, *, dock_airspace_validator=None, allow_derived_facilities=False):
        self.catalog = catalog
        self.allow_derived_facilities = allow_derived_facilities
        self.dock_airspace_validator = dock_airspace_validator

    def camp(self, camp):
        center = _height(self.catalog, camp['surface_id'], camp['position_mm'])
        polygon = rectangle(*center, 30)
        in_deployment(camp['side'], polygon)
        self.catalog.footprint(camp['surface_id'], polygon, side=camp['side'], purpose='camp')
        layer = self.catalog.layer(camp['surface_id'])
        for access in layer['access']:
            if access.get('necessary_transit') is True:
                require(not _touch(polygon, access['polygon']), 'BATTLE_CAMP_BLOCKS_ROUTE', '兵营不得堵塞必要道路/通路')
        anchor = _height(self.catalog, camp['service_surface_id'], camp['service_anchor_mm'])
        require(_distance_squared(center, anchor) <= 100**2, 'BATTLE_SERVICE_INVALID', '服务锚点超出100米')
        self.catalog.footprint(camp['service_surface_id'], rectangle(*anchor, 4), side=camp['side'],
                               purpose='service', kind='support')
        require(not _touch(polygon, rectangle(*anchor, 4)), 'BATTLE_SERVICE_INVALID', '服务完整占地压在兵营实体上')
        return polygon

    def unit(self, unit):
        center = _height(self.catalog, unit['surface_id'], unit['position_mm'])
        require(unit['unit_type'] in k.profiles(), 'BATTLE_DEPLOYMENT_INVALID', '未知兵种')
        require(unit['unit_type'] != 'recon' or unit.get('air_state') == 'docked',
                'BATTLE_DEPLOYMENT_INVALID', '新局无人机须停靠部署')
        radius = k.profiles()[unit['unit_type']]['ground_radius_m']+1
        polygon = rectangle(*center, radius)
        in_deployment(unit['side'], polygon)
        purpose = 'dock' if unit['unit_type'] == 'recon' else 'deploy'
        self.catalog.footprint(unit['surface_id'], polygon, side=unit['side'], purpose=purpose, kind=unit['unit_type'])
        return polygon

    def _facility(self, camp):
        facilities = self.catalog.payload.get('camp_facilities', [])
        matches = [f for f in facilities if f.get('camp_id') == camp['camp_id']
                   and f.get('camp_position_mm') == camp['position_mm']
                   and f.get('service_anchor_mm') == camp['service_anchor_mm']
                   and f.get('service_surface_id') == camp['service_surface_id']]
        if not matches and self.allow_derived_facilities:
            from backend.manual_deployment_preview import derived_facility
            matches = [derived_facility(camp, self.catalog)]
        require(len(matches) == 1, 'BATTLE_CAMP_ACCESS_UNRESOLVED', '缺少该选址的冻结出入口/服务/停靠路径', 503)
        facility = matches[0]
        require(facility.get('surface_id') == camp['surface_id'] == camp['service_surface_id'],
                'BATTLE_LAYER_CONNECTION_MISSING', '本组件仅验收同层兵营接入；跨层须完整适配器验收')
        entrance = _height(self.catalog, facility['surface_id'], facility['entrance_mm'])
        center = position_m(camp)
        # A reviewed facility entrance is an external service access anchor,
        # not a newly invented fixed-distance game rule.
        require(_distance_squared(center, entrance) <= 100**2
                and not _touch(rectangle(*center, 30), rectangle(*entrance, 4)),
                'BATTLE_SERVICE_INVALID', '接入口须在100米内且完整占地位于兵营实体外')
        anchor = (q(camp['service_anchor_mm']['x_mm'])/1000, q(camp['service_anchor_mm']['z_mm'])/1000)
        dock = _height(self.catalog, facility['dock_surface_id'], facility['dock_position_mm'])
        require(facility['dock_surface_id'] == camp['surface_id'] and _distance_squared(center, dock) <= 100**2,
                'BATTLE_DOCK_INVALID', '每营须有100米内同层完整停靠面')
        self.catalog.footprint(facility['dock_surface_id'], rectangle(*dock, 3), side=camp['side'], purpose='dock', kind='recon')
        self.catalog.footprint(camp['surface_id'], sweep(center, anchor, 4), side=camp['side'],
                               purpose='service', kind='support')
        # Prove continuous static access, not just endpoint permission. Speed is
        # irrelevant to deployment; routes may take many steps and are not run.
        paths = facility.get('paths')
        require(isinstance(paths, list) and len(paths) == 2, 'BATTLE_CAMP_ACCESS_UNRESOLVED', '缺少服务/停靠连续接入路径')
        for path, endpoint in zip(paths, (anchor, dock)):
            require(isinstance(path, list) and 2 <= len(path) <= 32, 'BATTLE_SERVICE_INVALID', '接入路径无效')
            points = [tuple(q(v) for v in point) for point in path]
            require(points[0] == entrance and points[-1] == endpoint,
                    'BATTLE_SERVICE_INVALID', '接入路径端点不对应实际锚点')
            for a, b in zip(points, points[1:]):
                self.catalog.footprint(camp['surface_id'], sweep(a, b, 4), side=camp['side'], purpose='move', kind='support',
                                       direction=(a, b) if a != b else None)
        return facility

    def validate_deployment(self, state):
        camps, units = state['camps'], state['units']
        require(len(camps) == 6 and len({c['camp_id'] for c in camps}) == 6,
                'BATTLE_DEPLOYMENT_INVALID', '新局须双方各三座唯一兵营')
        for side in ('red', 'blue'):
            own = [c for c in camps if c['side'] == side]
            require(len(own) == 3 and {c['unit_type'] for c in own} == {'recon', 'ground', 'support'},
                    'BATTLE_DEPLOYMENT_INVALID', '每方三类兵营各一座')
        polygons = [(camp, self.camp(camp)) for camp in camps]
        for a, b in combinations(polygons, 2):
            require(not _touch(a[1], b[1]), 'BATTLE_DEPLOYMENT_OVERLAP', '兵营完整实体不可重叠或接触')
        # Effective masks are trusted frozen server data, not client state circles.
        captures = self.catalog.payload.get('capture_masks', [])
        source_ids = {p['point_id'] for p in self.catalog.payload['source_facts']['capture_points']}
        require(len(captures) == 5 and {p.get('point_id') for p in captures} == source_ids,
                'BATTLE_CAPTURE_SURFACE_UNRESOLVED', '五据点有效层掩码未完成', 503)
        from backend.battle_support import convex_polygon
        masks = []
        source_points = {p['point_id']: p for p in self.catalog.payload['source_facts']['capture_points']}
        for point in captures:
            source = source_points[point['point_id']]
            layer = self.catalog.layer(point['surface_id'])
            require(point.get('source_navigation_layer_id') == source['navigation_layer_id']
                    and layer.get('navigation_layer_id') == source['navigation_layer_id'],
                    'BATTLE_CAPTURE_SURFACE_UNRESOLVED', '据点面未绑定来源有效层')
            require(point.get('accepted') is True and isinstance(point.get('polygons'), list) and point['polygons'],
                    'BATTLE_CAPTURE_SURFACE_UNRESOLVED', '缺少已验收据点有效面')
            for raw in point['polygons']:
                polygon = convex_polygon(raw)
                center = q(source['x']), q(source['z'])
                require(all(_distance_squared(center, p) <= q(source['radius_m'])**2 for p in polygon),
                        'BATTLE_CAPTURE_OUTSIDE', '有效面超出冻结据点候选圈')
                if source['support'] == 'bridge_deck':
                    deck = source['deck']
                    a, b = deck['start'], deck['end']
                    require(a['z'] == b['z'], 'BATTLE_CAPTURE_SURFACE_UNRESOLVED', '非轴向桥面须独立有效面验收')
                    require(all(q(a['x']) <= x <= q(b['x']) and abs(z-q(a['z'])) <= q(deck['width'])/2
                                for x, z in polygon), 'BATTLE_CAPTURE_OUTSIDE', '桥面据点面超出来源桥面范围')
                for side in ('red', 'blue'):
                    self.catalog.footprint(point['surface_id'], polygon, side=side, purpose='capture')
                masks.append((point['point_id'], polygon))
        for (ida, a), (idb, b) in combinations(masks, 2):
            if ida != idb:
                require(not _touch(a, b), 'BATTLE_CAPTURE_OVERLAP', '五点有效区不可相交')
        for camp, polygon in polygons:
            facility = self._facility(camp)
            dock = (q(facility['dock_position_mm']['x_mm'])/1000, q(facility['dock_position_mm']['z_mm'])/1000)
            require(not any(_touch(rectangle(*dock, 3), p) for _, p in polygons),
                    'BATTLE_DOCK_INVALID', '兵营停靠面触及兵营实体')
            # Local manual service paths are generated access hints, not occupied
            # entities. Their clearance envelope must not veto a valid deployment.
            if not self.allow_derived_facilities:
                for path in facility['paths']:
                    points = [tuple(q(v) for v in point) for point in path]
                    for a, b in zip(points, points[1:]):
                        require(not any(_touch(sweep(a, b, 4), p) for _, p in polygons),
                                'BATTLE_CAMP_BLOCKS_ROUTE', '兵营实体阻断服务/停靠连续接入')
            require(self.dock_airspace_validator is not None
                    and self.dock_airspace_validator(facility) is True,
                    'BATTLE_DOCK_AIRSPACE_UNRESOLVED', '完整部署须接入已验收停靠上方空域组件', 503)
            require(not any(_disc_polygon(position_m(camp), 100, p) for _, p in masks),
                    'BATTLE_CAMP_CAPTURE_OVERLAP', '兵营100米服务区不得与据点有效区相交')
        occupied = [(unit, self.unit(unit)) for unit in units]
        for unit, polygon in occupied:
            require(not any(_touch(polygon, p) for _, p in polygons),
                    'BATTLE_DEPLOYMENT_OVERLAP', '单位完整占地不得压兵营')
        for (a, pa), (b, pb) in combinations(occupied, 2):
            # Explicit new-game contract requires non-overlap; friendly units do
            # NOT block one another during later motion.
            radius = k.profiles()[a['unit_type']]['ground_radius_m']+k.profiles()[b['unit_type']]['ground_radius_m']+1
            require(_distance_squared(position_m(a), position_m(b)) > radius**2,
                    'BATTLE_DEPLOYMENT_OVERLAP', '新局完整单位占地不可重叠')
        return True
