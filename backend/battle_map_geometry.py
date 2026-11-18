"""总册Geometry组合器与真实交互地表适配。

只读服务端冻结文件，不接受HTTP验收/路线权限。不内置accepted场景。
目录摘要保证完整性，不代表独立实地图验收；当前来源缺项见source_readiness。
"""
from copy import deepcopy
from fractions import Fraction as F
from hashlib import sha256 as byte_hash
from pathlib import Path

from backend import battle_capacity, battle_kernel as k, battle_scenario, rulebook
from backend.battle_execution import Geometry, Motion
from backend.battle_map_interactions import ForestIndex, Interactions, distance2, plane_mm
from backend.battle_map_surfaces import rectangle, _covered, _point_inside
from backend.battle_occlusion import RayIndex
from backend.demo_domain import require
from backend.runtime import ServiceError, sha256

INTERACTION_SCHEMA = 'qinglan-map-interactions-v1'
INTERACTION_FIELDS = ('forest_polygons', 'airspace', 'blocker_parts', 'transparent_parts',
                      'solid_volumes_validated', 'capture_layers', 'dock_surfaces')


def source_readiness():
    """区分已有明确来源、尚未转换/验收、源规则字段缺失；不读取98MB网格或写库。"""
    from backend.map_catalog import current_map
    from backend.battle_terrain_assets import MANIFEST_PATH
    value = current_map()
    return dict(production_ready=False, read_only=True,
        available_sources=dict(map_source_sha256=value['import_audit']['source_sha256'],
            capture_layout_sha256=value['import_audit']['capture_layout_sha256'],
            capture_points=deepcopy(value['capture_points']),
            deployment_zones=deepcopy(value['deployment_zones']),
            source_roads_sha256=value['import_audit']['source_roads_sha256'],
            base_ground_annotations=MANIFEST_PATH.exists()),
        blockers=[
            dict(code='BATTLE_SURFACE_ACCEPTANCE_MISSING', category='geometry_acceptance',
                message='部署区、道路和五点已有来源；完整支撑面、覆盖冲突、坡度与道路接入尚须真实几何验收。'),
            dict(code='BATTLE_CAPTURE_MASK_UNVERIFIED', category='geometry_acceptance',
                message='五点位置及桥面引用已冻结；仍需将圆域与真实批准层相交，不能复用旧默认层回退。'),
            dict(code='BATTLE_FOREST_BOUNDARY_MISSING', category='source_semantics',
                message='缺少完整规则森林闭多边形及连通覆盖目录；不能从树名、贴图或道路标签推定。'),
            dict(code='BATTLE_AIRSPACE_MISSING', category='source_semantics',
                message='缺少明确允许空域/禁飞区及合法停靠面目录；不能将未声明当作全图允许。'),
            dict(code='BATTLE_SOLID_VOLUME_UNVERIFIED', category='geometry_acceptance',
                message='建筑/桥梁网格已有来源；完整遮挡分类、实体厚度与锚点实体外资格尚未验收。'),
            dict(code='BATTLE_SCENE_ACCEPTANCE_MISSING', category='independent_acceptance',
                message='缺少与全部冻结摘要绑定的独立真实场景验收记录；草案不自动发布。')])


def seal_interactions(value):
    value = deepcopy(value); value.pop('payload_sha256', None)
    value['payload_sha256'] = sha256(value)
    return value


def _hull(points):
    points = sorted(set(points))
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    halves = []
    for sequence in (points, list(reversed(points))):
        result = []
        for point in sequence:
            while len(result) > 1 and cross(result[-2], result[-1], point) <= 0:
                result.pop()
            result.append(point)
        halves.append(result[:-1])
    return tuple(halves[0]+halves[1])


def _bounds(polygon):
    return (min(p[0] for p in polygon), min(p[1] for p in polygon),
            max(p[0] for p in polygon), max(p[1] for p in polygon))


def _bounds_touch(a, b):
    # 闭边界宽阶段，只剔除绝不相交项；最终仍逐有理数精确覆盖/裁剪。
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


class CatalogInteractions:
    """真实支撑适配。方形为圆占地的保守外包，不会缩小总册圆半径及1米余量。

    直连用完整扫掠外包而非中心若干采样；可能保守拒绝狭小合法区，绝不批准缺口。
    飞行规则锚点使用unit.surface_id显式支撑绑定；无法提供该点支撑高度则拒绝，
    不使用最近层或可视飞行高度。airspace为闭凸分片并集与禁飞分片。
    """
    def __init__(self, catalog, definition, scenario):
        self.catalog, self.definition = catalog, deepcopy(definition)
        unsigned = deepcopy(definition); digest = unsigned.pop('payload_sha256', None)
        require(definition.get('schema_version') == INTERACTION_SCHEMA and digest == sha256(unsigned)
                and definition.get('surface_catalog_sha256') == catalog.digest,
                'BATTLE_INTERACTIONS_STALE', '交互目录摘要或合法面绑定不一致', 503)
        require(all(field in definition for field in INTERACTION_FIELDS)
                and definition['solid_volumes_validated'] is True,
                'BATTLE_INTERACTIONS_NOT_READY', '交互目录缺森林、空域、完整实体或服务/停靠/据点层', 503)
        from backend.battle_support import convex_polygon
        self.forests = ForestIndex(definition['forest_polygons'])
        air = definition['airspace']
        require(isinstance(air, dict) and isinstance(air.get('allowed'), list)
                and isinstance(air.get('forbidden'), list) and air['allowed']
                and type(air.get('display_height_mm')) is int and air['display_height_mm'] >= 0,
                'BATTLE_AIRSPACE_MISSING', '空域必须明确允许域、禁飞域及固定表现高度', 503)
        self.air_allowed = tuple(convex_polygon(p) for p in air['allowed'])
        self.air_forbidden = tuple(convex_polygon(p) for p in air['forbidden'])
        domains = definition.get('verified_interaction_domains')
        require(domains is None or (isinstance(domains, list) and bool(domains)),
                'BATTLE_INTERACTION_DOMAIN_INVALID', '局部交互域必须显式非空', 503)
        self.interaction_domains = None if domains is None else tuple(convex_polygon(p) for p in domains)
        self._interaction_domain_bounds = tuple((p, _bounds(p)) for p in self.interaction_domains or ())
        self._air_allowed_bounds = tuple((p, _bounds(p)) for p in self.air_allowed)
        self._air_forbidden_bounds = tuple((p, _bounds(p)) for p in self.air_forbidden)
        self.capture_points = deepcopy(scenario['capture_points'])
        self.capture_layers = definition['capture_layers']
        require(set(self.capture_layers) == {p['point_id'] for p in self.capture_points}
                and all(isinstance(v, list) and v for v in self.capture_layers.values()),
                'BATTLE_CAPTURE_MASK_UNVERIFIED', '五点必须逐点绑定真实有效层', 503)
        from backend.battle_support import convex_polygon
        masks = catalog.payload.get('capture_masks')
        require(isinstance(masks, list) and len(masks) == 5
                and {m.get('point_id') for m in masks} == set(self.capture_layers),
                'BATTLE_CAPTURE_MASK_UNVERIFIED', '合法面目录缺五点真实有效面掩码', 503)
        self.capture_masks = {}
        for mask in masks:
            require(mask.get('accepted') is True and mask.get('surface_id') in self.capture_layers[mask['point_id']]
                    and isinstance(mask.get('polygons'), list) and mask['polygons'],
                    'BATTLE_CAPTURE_MASK_UNVERIFIED', '据点真实有效面未验收或交互绑定不符', 503)
            self.capture_masks[mask['point_id']] = dict(surface_id=mask['surface_id'],
                polygons=tuple(convex_polygon(p) for p in mask['polygons']))
        for point in self.capture_points:
            for identity in self.capture_layers[point['point_id']]:
                layer = catalog.layer(identity)
                require(layer.get('navigation_layer_id') == point['navigation_layer_id'],
                        'BATTLE_CAPTURE_LAYER_INVALID', '据点真实层与既有ground/桥面引用不符', 503)
        require(isinstance(definition['dock_surfaces'], list) and definition['dock_surfaces'],
                'BATTLE_DOCK_SURFACE_MISSING', '没有显式合法停靠面', 503)
        for identity in definition['dock_surfaces']:
            catalog.layer(identity)

    def ray_domain_clear(self, source, target):
        """精确覆盖整条规则xz射线，不能穿过局部目录之外的未知森林/实体。"""
        if self.interaction_domains is None:
            return True  # 无新增字段的旧冻结目录继续既有完整目录契约。
        from backend.battle_map_interactions import _interval
        start, end = (tuple(v/1000 for v in plane_mm(u)) for u in (source, target))
        bounds = _bounds((start, end))
        intervals = sorted(i for polygon, box in self._interaction_domain_bounds
                           if _bounds_touch(bounds, box) and (i := _interval(polygon, start, end)) is not None)
        covered = F(0)
        for lo, hi in intervals:
            if lo > covered:
                return False
            covered = max(covered, hi)
            if covered == 1:
                return True
        return False

    def _point(self, unit):
        # 交互只用0.001m规则位置。路线游标是预算/几何证据，不授予额外射程或服务范围。
        point = tuple(v/1000 for v in plane_mm(unit))
        legacy = unit.get('geometry_position')
        if legacy is not None:
            require(isinstance(legacy, dict) and legacy.get('catalog_sha256') == self.catalog.digest
                    and legacy.get('surface_id') == unit['surface_id']
                    and legacy.get('exact_position_m') == [str(v) for v in point],
                    'BATTLE_ROUTE_STALE', '旧跨路线亚毫米权威位置不可自动迁移', 503)
        return point

    def _polygon(self, unit):
        radius = 4 if unit['unit_type'] == 'support' else 3
        return rectangle(*self._point(unit), radius)

    def _height(self, unit):
        height = self.catalog.height(unit['surface_id'], self._point(unit))
        if unit.get('air_state') not in ('flying', 'landing'):
            require(unit['position_mm']['height_mm'] == round(height*1000),
                    'BATTLE_SUPPORT_HEIGHT_INVALID', '权威毫米高度不等于显式支撑层真实高度', 503)
        return height

    def rule_anchor(self, unit):
        x, z = self._point(unit)
        return x, self._height(unit)+2, z

    @staticmethod
    def _legal(callback):
        try:
            callback()
            return True
        except ServiceError as error:
            # 数据缺失/损坏必须技术暂停，不能伪装成暂时动作不合法。
            if error.status >= 500 or error.code in ('BATTLE_SURFACE_CATALOG_STALE', 'BATTLE_INTERACTIONS_STALE'):
                raise
            return False

    def _footprint(self, unit, purpose):
        self._height(unit)
        return self.catalog.footprint(unit['surface_id'], self._polygon(unit), side=unit['side'],
                                      purpose=purpose, kind=unit['unit_type'])

    def service_footprint_legal(self, unit):
        return self.dock_footprint_legal(unit) if unit['unit_type'] == 'recon' else self._legal(
            lambda: self._footprint(unit, 'service'))

    def camp_service_legal(self, camp):
        def validate():
            point = camp['service_anchor_mm']
            anchor = dict(position_mm=point, surface_id=camp['service_surface_id'], side=camp['side'],
                          unit_type='ground', air_state='ground')
            self._height(anchor)
            self._footprint(anchor, 'service')
        return self._legal(validate)

    def service_segment_clear(self, source, target):
        def validate():
            a = source
            if 'camp_id' in source:
                a = dict(source, position_mm=source['service_anchor_mm'],
                         surface_id=source['service_surface_id'], unit_type='ground', air_state='ground')
            # 暂仅单个连续真实层；同层可跨地表标签。跨层接触须明确连接证据，不凭图可达放行。
            require(a['surface_id'] == target['surface_id'], 'BATTLE_SERVICE_DISCONNECTED', '服务直连不在同一连续层')
            hull = _hull(self._polygon(a)+self._polygon(target))
            self.catalog.footprint(a['surface_id'], hull, side=a['side'], purpose='service', kind='ground')
        return self._legal(validate)

    def capture_footprint_legal(self, unit, point):
        if unit['surface_id'] not in self.capture_layers[point['point_id']]:
            return False
        x, z = self._point(unit)
        mask = self.capture_masks[point['point_id']]
        if unit['surface_id'] != mask['surface_id'] or not any(
                _point_inside((x, z), polygon) for polygon in mask['polygons']):
            return False
        if (x-F(point['x']))**2+(z-F(point['z']))**2 > point['radius_m']**2:
            return False
        if point['support'] == 'bridge_deck':
            # 有限桥面矩形，精确有理数检查，不采用capture_map旧默认层/可选高度路径。
            d = point['deck']; a, b = d['start'], d['end']
            dx, dz = F(str(b['x']))-F(str(a['x'])), F(str(b['z']))-F(str(a['z']))
            px, pz = x-F(str(a['x'])), z-F(str(a['z']))
            length2 = dx*dx+dz*dz; along = px*dx+pz*dz
            if not (0 <= along <= length2 and (px*dz-pz*dx)**2 <= F(str(d['width']))**2*length2/4):
                return False
        return self._legal(lambda: self._footprint(unit, 'capture'))

    def dock_footprint_legal(self, unit):
        return unit['surface_id'] in self.definition['dock_surfaces'] and self._legal(
            lambda: self._footprint(unit, 'dock'))

    def air_legal(self, unit):
        polygon = self._polygon(unit)
        from backend.battle_support import _intersection
        bounds = _bounds(polygon)
        allowed = [p for p, box in self._air_allowed_bounds if _bounds_touch(bounds, box)]
        return _covered(polygon, allowed) and not any(_intersection(polygon, p)
            for p, box in self._air_forbidden_bounds if _bounds_touch(bounds, box))

    def same_collision_layer(self, a, b):
        # 地面碰撞在已声明的物理层内；不使用高度容差把不同层合并。
        return self.catalog.layer(a['surface_id']).get('collision_layer_id', a['surface_id']) == \
            self.catalog.layer(b['surface_id']).get('collision_layer_id', b['surface_id'])

    def flight_position(self, unit):
        result = deepcopy(unit['position_mm'])
        height = self.catalog.height(unit['surface_id'], self._point(unit))*1000
        if unit['air_state'] == 'flying':
            height += self.definition['airspace']['display_height_mm']
        # 规则高度由已绑定支撑面重算；路线预算余数不改变规则锚点。
        result['height_mm'] = round(height)
        return result


def _file_signature(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _verified_file(path, digest, previous_signature=None):
    """服务端冻结文件：内容首次hash；inode/ctime/mtime/大小任何变化强制重验。

    不只信mtime：同长覆盖并恢复mtime仍会改变ctime；原子替换改变inode。
    每次验收前stat，读期间再stat防止中途替换。任何缺失/损坏均fail closed。
    """
    try:
        stat = path.stat()
        signature = _file_signature(stat)
        require(path.is_file(), 'BATTLE_FROZEN_SOURCE_CHANGED', '冻结地图来源不是普通文件', 503)
        if previous_signature == signature:
            return None, signature
        raw = path.read_bytes()
        require(_file_signature(path.stat()) == signature and byte_hash(raw).hexdigest() == digest,
                'BATTLE_FROZEN_SOURCE_CHANGED', '冻结文件损坏或读取期间被替换，须技术暂停', 503)
        return raw, signature
    except OSError:
        require(False, 'BATTLE_FROZEN_SOURCE_CHANGED', '冻结地图来源文件无法读取，须技术暂停', 503)


class _MapGeometryComponents(Geometry):
    """完整契约组合，不以组件存在作为生产ready。所有组件及来源只由服务端注入。"""
    camp_collisions = True
    distance_quantum_mm = 1
    movement_energy_quantum = None

    def __init__(self, catalog, interactions, motion, deployment, *, scenario, rules, capacity, source_files=()):
        self.catalog, self.deployment = catalog, deployment
        self.scenario, self.rules, self.capacity = deepcopy(scenario), deepcopy(rules), deepcopy(capacity)
        self.terrain = CatalogInteractions(catalog, interactions, scenario)
        self.motion = motion if motion is not None else MapMotionCoordinator(
            catalog, self.terrain, self.position, camp_collisions=self.camp_collisions,
            distance_quantum_mm=self.distance_quantum_mm)
        rays = RayIndex(catalog.assets, blocker_parts=interactions['blocker_parts'],
                        transparent_parts=interactions['transparent_parts'])
        require(not rays.unresolved, 'BATTLE_RAY_UNRESOLVED', '完整遮挡目录含未分类部件', 503)
        self.interactions = Interactions(self.terrain, rays, self.terrain.forests)
        self.binding = dict(surface_catalog_sha256=catalog.digest, interactions_sha256=interactions['payload_sha256'],
            geometry_sha256=catalog.payload['geometry_sha256'],
            base_annotations_sha256=catalog.payload['base_annotations_sha256'])
        self._source_files = tuple((Path(path), digest) for path, digest in source_files)
        self._source_json_cache = {}
        self._surface_snapshot = catalog.payload
        self._verify_sources()

    def _verify_sources(self):
        payload = getattr(self, '_surface_snapshot', None)
        if payload is None:
            payload = self.catalog.payload
        require(self.catalog.source_matches(payload), 'BATTLE_FROZEN_SOURCE_CHANGED',
                '内存合法面与冻结来源不一致，须技术暂停', 503)
        acceptance = payload.get('acceptance', {})
        require((acceptance.get('production_ready') is True and acceptance.get('missing') == [])
                or acceptance.get('local_manual_test_ready') is True,
                'BATTLE_SURFACE_ACCEPTANCE_MISSING', '真实合法面目录仍有未验收项', 503)
        require(battle_scenario.service_policy_valid(self.scenario), 'BATTLE_SERVICE_POLICY_INVALID',
                '空中补能例外须绑定受支持的新场景版本', 503)
        require(self.scenario.get('geometry_binding') == self.binding,
                'BATTLE_GEOMETRY_BINDING_INVALID', '场景未绑定全部真实目录/交互/网格/基础标注摘要', 503)
        require(self.scenario['map']['source_sha256'] == payload['definition_sha256']
                and self.scenario['rule_payload_sha256'] == sha256(self.rules)
                and self.scenario['rule_version_id'] == rulebook.identity(self.rules)
                and self.scenario['capacity_payload_sha256'] == sha256(self.capacity)
                and self.scenario['round_seconds'] == 60 and self.scenario['maximum_rounds'] == 60,
                'BATTLE_GEOMETRY_BINDING_INVALID', '地图/规则/容量来源或60秒60轮绑定失效', 503)
        baseline = battle_scenario.definition()
        require(all(self.scenario[key] == baseline[key] for key in ('map', 'capture_points', 'capture_layout_sha256')),
                'BATTLE_SCENE_SOURCE_CHANGED', '独立场景不是当前已沿用的底图和五点', 503)
        from backend.battle_terrain_assets import TerrainAssets, GEOMETRY_PATH, MANIFEST_PATH
        from backend.demo_domain import MAP_PATH
        require(type(self.catalog.assets) is TerrainAssets and bool(self._source_files),
                'BATTLE_REAL_SOURCE_REQUIRED', '生产组合须有真实TerrainAssets及冻结文件/独立用例证据，不能凭组件或布尔报告发布', 503)
        files = {path.resolve(): digest for path, digest in self._source_files}
        require(all(path.resolve() in files for path in (MAP_PATH, GEOMETRY_PATH, MANIFEST_PATH)),
                'BATTLE_REAL_SOURCE_REQUIRED', '缺少当前原始地图、网格或基础标注文件绑定', 503)
        require(files[MAP_PATH.resolve()] == payload['definition_sha256']
                and files[GEOMETRY_PATH.resolve()] == payload['geometry_sha256'],
                'BATTLE_REAL_SOURCE_REQUIRED', 'TerrainAssets不是当前路径的原始地图/网格', 503)
        import json
        require(json.loads(MANIFEST_PATH.read_bytes()) == self.catalog.assets.manifest,
                'BATTLE_REAL_SOURCE_REQUIRED', '当前基础标注与TerrainAssets绑定不符', 503)
        records = []
        cache = getattr(self, '_source_json_cache', {})
        signatures = getattr(self, '_source_stat_cache', {})
        for path, digest in files.items():
            raw, signature = _verified_file(path, digest, signatures.get((path, digest)))
            signatures[(path, digest)] = signature
            # 原始98MB网格首次/任何stat变化重算bytes hash；非mtime单字段缓存。
            if path in {p.resolve() for p in (MAP_PATH, GEOMETRY_PATH, MANIFEST_PATH)}:
                continue
            try:
                key = (path, digest)
                if key not in cache:
                    cache[key] = json.loads(raw if raw is not None else path.read_bytes())
                records.append((path, digest, cache[key]))
            except ValueError:
                require(False, 'BATTLE_FROZEN_SOURCE_CHANGED', '冻结服务端JSON损坏', 503)
        self._source_json_cache = cache
        self._source_stat_cache = signatures
        require(all(any(record == source for _, _, record in records) for source in
                    (payload, self.terrain.definition, self.scenario)),
                'BATTLE_FROZEN_MANIFEST_MISSING', '合法面、交互与发布场景必须有完全匹配的服务端冻结文件', 503)
        self._verify_acceptance(records)

    def verify(self, state):
        self._verify_sources()
        manifest = state['manifest']
        require(battle_scenario.service_policy_matches(manifest, self.scenario)
                and battle_scenario.release_scope_matches(manifest, self.scenario)
                and manifest.get('battle_scenario_sha256') == sha256(self.scenario)
                and str(manifest.get('scenario_version_id')) == battle_scenario.identity(self.scenario)
                and manifest.get('battle_rule_sha256') == sha256(self.rules)
                and str(manifest.get('rule_version_id')) == rulebook.identity(self.rules)
                and manifest.get('battle_capacity_sha256') == sha256(self.capacity)
                and str(manifest.get('battle_capacity_version_id')) == battle_capacity.identity(self.capacity)
                and manifest.get('map_source_sha256') == self.scenario['map']['source_sha256']
                and manifest.get('capture_layout_sha256') == self.scenario['capture_layout_sha256']
                and manifest.get('coordinate_system_id') == 'unity-demo-120x80-v1'
                and manifest.get('battle_setup_sha256') == sha256(manifest.get('battle_setup')),
                'BATTLE_FROZEN_BINDING_INVALID', '本局规则/地图/五点/编制冻结绑定不一致', 503)
        require([p['point_id'] for p in state['battle_points']] == [p['point_id'] for p in self.terrain.capture_points],
                'BATTLE_CAPTURE_LAYOUT_CHANGED', '本局据点标识与冻结场景不同', 503)
        for unit in state['units']:
            if not k.alive(unit):
                continue
            self.terrain.rule_anchor(unit)
            if unit['unit_type'] == 'recon' and unit['air_state'] in ('flying', 'landing'):
                require(self.terrain.air_legal(unit), 'BATTLE_AIRSPACE_INVALID', '飞行单位不在合法空域', 503)
            else:
                self.terrain._footprint(unit, 'dock' if unit['unit_type'] == 'recon' else 'stop')
        self.camps(state)

    def readiness(self):
        self._verify_sources()
        return dict(production_ready=True, read_only=True, blockers=[],
                    geometry_binding=deepcopy(self.binding),
                    independent_acceptance_sha256=self.scenario['independent_acceptance_sha256'])

    def validate_deployment(self, state):
        self._verify_sources()
        return self.deployment.validate_deployment(deepcopy(state)) is True

    def position(self, point):
        # 来源map_catalog明确100/150米变换；毫米不够表示的输入拒绝，不截断为额外距离。
        from backend.demo_domain import position
        validated = position(point)
        x, z = F(validated['x'])*100000, F(validated['y'])*150000
        require(x.denominator == z.denominator == 1, 'BATTLE_POSITION_PRECISION', '目标须可精确表示为权威毫米')
        return dict(x_mm=int(x), z_mm=int(z), height_mm=0)

    def display_position(self, point):
        from decimal import Decimal, localcontext
        def text(value, scale):
            with localcontext() as context:
                context.prec = 30
                return format(Decimal(value)/Decimal(scale), '.9f').rstrip('0').rstrip('.') or '0'
        return dict(x=text(point['x_mm'], 100000), y=text(point['z_mm'], 150000))

    def validate_movement_submission(self, unit):
        movement = unit.get('movement')
        if movement:
            # 所有路线坐标必须可精确表示为权威毫米；否则地面也会锁定后才失败。
            for target in movement.get('waypoints', []) + [movement['position']]:
                self.position(target)
        if unit['unit_type'] == 'recon' and unit.get('air_state') == 'flying':
            self.motion._air_motion(deepcopy(unit), movable=True, validate_only=True)

    def motions(self, state, **kwargs):
        return self.motion.motions(deepcopy(state), **kwargs)

    def observe(self, state, **kwargs): return self.interactions.observe(state, **kwargs)
    def capture(self, state, before, motions): return self.interactions.capture(state, before, motions)
    def fire_clear(self, source, target): return self.interactions.fire_clear(source, target)
    def service_clear(self, source, target): return self.interactions.service_clear(source, target)
    def hover_energy_clear(self, source, target):
        return (self.scenario.get('field_energy_service_policy') == k.FIELD_ENERGY_SERVICE_POLICY
                and self.interactions.hover_energy_clear(source, target))
    def flight_legal(self, unit, action): return self.interactions.flight_legal(unit, action)
    def landing_clear(self, unit, state): return self.interactions.landing_clear(unit, state)
    def flight_position(self, unit): return self.terrain.flight_position(unit)

    def camps(self, state):
        camps = deepcopy(state['camps'])
        require(len(camps) == 6 and len({c['camp_id'] for c in camps}) == 6,
                'BATTLE_CAMPS_INVALID', '须保持双方各三座冻结兵营', 503)
        from backend.battle_setup import barracks_id
        setup = state['manifest'].get('battle_setup', {})
        expected = {barracks_id(side, item['unit_type']): dict(item, side=side)
                    for side in k.SIDES for item in setup.get(side, {}).get('camps', [])}
        require(set(expected) == {c['camp_id'] for c in camps},
                'BATTLE_CAMPS_CHANGED', '兵营标识不属于本局冻结部署', 503)
        for camp in camps:
            frozen = expected[camp['camp_id']]
            require(all(camp.get(key) == frozen.get(key) for key in
                        ('side', 'unit_type', 'surface_id', 'service_surface_id'))
                    and camp['position_mm'] == frozen['position']
                    and camp['service_anchor_mm'] == frozen['service_anchor'],
                    'BATTLE_CAMPS_CHANGED', '兵营位置/支撑层/服务锚点不能偏离冻结部署', 503)
            require(self.terrain.camp_service_legal(camp), 'BATTLE_CAMP_SERVICE_INVALID', '兵营服务锚点或真实层失效', 503)
            from backend.battle_map_deployment import _disc_polygon
            center = tuple(v/1000 for v in plane_mm(camp))
            require(not any(_disc_polygon(center, F(100), polygon)
                    for mask in self.terrain.capture_masks.values() for polygon in mask['polygons']),
                    'BATTLE_CAMP_CAPTURE_OVERLAP', '兵营100米服务区与据点有效区相交', 503)
        return camps


class MapGeometry(_MapGeometryComponents):
    """正式资格：保留完整13类实图验收，不消费本地测试发布资格。"""
    def _verify_acceptance(self, records):
        require(self.catalog.payload.get('acceptance', {}).get('production_ready') is True
                and self.catalog.payload['acceptance'].get('missing') == [],
                'BATTLE_SURFACE_ACCEPTANCE_MISSING', '正式地图目录仍有未验收项', 503)
        require(battle_scenario.accepted(self.scenario), 'BATTLE_SCENARIO_NOT_READY', '独立场景仍未验收发布', 503)
        report = self.scenario.get('independent_acceptance', {})
        require(report.get('schema_version') == 'qinglan-real-map-acceptance-v1'
                and report.get('environment') == 'isolated_real_map'
                and report.get('geometry_binding') == self.binding
                and report.get('passed') is True
                and battle_scenario.acceptance_evidence_valid(report,
                    field_energy_service_policy=self.scenario.get('field_energy_service_policy'))
                and self.scenario.get('independent_acceptance_sha256') == sha256(report),
                'BATTLE_SCENE_ACCEPTANCE_MISSING', '缺少独立真实场景验收及绑定摘要；夹具不能发布', 503)
        for case in report['cases']:
            require(any(digest == case['evidence_sha256']
                    and battle_scenario.acceptance_record_matches(record, case['case_id'],
                        geometry_binding=self.binding, scenario=self.scenario)
                    for _, digest, record in records),
                    'BATTLE_ACCEPTANCE_EVIDENCE_MISSING', '每个独立实地图用例须有匹配的原始证据文件，不能只填passed', 503)


class OfflineMapGeometry(_MapGeometryComponents):
    """只供真实源离线验收；不是MapGeometry，服务安装/发布必须拒绝此类型。"""
    def _verify_acceptance(self, records):
        require(self.scenario.get('release_scope') == battle_scenario.LOCAL_MANUAL_SCOPE
                and self.scenario.get('competitive_ready') is False
                and self.scenario.get('acceptance', {}).get('status') == 'local_manual_test_candidate',
                'BATTLE_LOCAL_SCOPE_REQUIRED', '离线入口仅接收明确本地测试候选场景', 503)

    def readiness(self):
        self._verify_sources()
        return dict(production_ready=False, local_manual_test_ready=False, read_only=True,
                    release_scope=battle_scenario.LOCAL_MANUAL_SCOPE,
                    geometry_binding=deepcopy(self.binding),
                    blockers=[dict(code='BATTLE_LOCAL_EVIDENCE_REQUIRED', message='仅离线验收，不能安装服务')])


class LocalManualMapGeometry(_MapGeometryComponents):
    """真实局部手动测试资格；不代表竞技/全图公平，不能用于agent模式。"""
    camp_collisions = False
    distance_quantum_mm = 100
    movement_energy_quantum = F(1, 1000000)

    def position(self, point):
        # Manual clicks and decimal route input are requested targets, not
        # authoritative unit positions. Local manual targets and movement use
        # a 0.1m grid (ties to even); transport remains integer millimetres.
        # Existing deployment coordinates and terrain heights are preserved.
        from backend.demo_domain import position
        validated = position(point)
        return dict(x_mm=round(F(validated['x'])*1000)*100,
                    z_mm=round(F(validated['y'])*1500)*100, height_mm=0)

    def _verify_acceptance(self, records):
        require(battle_scenario.local_manual_accepted(self.scenario), 'BATTLE_LOCAL_SCENARIO_NOT_READY',
                '本地测试场景缺明确范围、限制或真实独立证据', 503)
        report = self.scenario['local_manual_acceptance']
        require(report.get('geometry_binding') == self.binding,
                'BATTLE_LOCAL_EVIDENCE_STALE', '本地测试报告几何来源不符', 503)
        for case in report['cases']:
            require(any(digest == case['evidence_sha256']
                    and battle_scenario.local_manual_record_matches(record, case['case_id'],
                        geometry_binding=self.binding, scenario=self.scenario)
                    and (case['case_id'] != 'source_integrity'
                         or (record.get('database_map_content_hash') == self.scenario['map']['content_hash']
                             and record.get('source_domain_audit_sha256') == self.terrain.definition.get('source_domain_audit_sha256')))
                    for _, digest, record in records),
                    'BATTLE_ACCEPTANCE_EVIDENCE_MISSING', '本地测试每项必须有实际原始证据及数据库地图绑定', 503)
        require(self.terrain.interaction_domains is not None,
                'BATTLE_INTERACTION_DOMAIN_INVALID', '本地测试必须限制完整射线至已核验活动域', 503)

    def readiness(self):
        self._verify_sources()
        return dict(production_ready=False, local_manual_test_ready=True, read_only=True,
                    release_scope=battle_scenario.LOCAL_MANUAL_SCOPE, competitive_ready=False,
                    limitations=deepcopy(self.scenario['limitations']), blockers=[],
                    geometry_binding=deepcopy(self.binding),
                    local_manual_acceptance_sha256=self.scenario['local_manual_acceptance_sha256'])


class MapMotionCoordinator:
    """同步地面扫掠与独立空域移动。默认仅同层候选线，不跨未知桥头寻路。

    可注入服务端冻结route_resolver以使用批准层连接；客户端waypoint本身不是通行证。
    空域完整连续扫掠不查地面f/s，不把飞行单位插入地面碰撞。
    """
    def __init__(self, catalog, terrain, position, *, route_resolver=None, camp_collisions=True, distance_quantum_mm=1):
        from backend.battle_map_motion import MapMotion
        self.catalog, self.terrain, self.position = catalog, terrain, position
        self.distance_quantum_mm = distance_quantum_mm
        self.ground = MapMotion(catalog, surface_transition_persistence=True, camp_collisions=camp_collisions,
                                distance_quantum_mm=distance_quantum_mm)
        self.route_resolver = route_resolver or self._same_layer_route

    def _same_layer_route(self, unit):
        movement = unit.get('movement')
        if not movement:
            return []
        targets = movement.get('waypoints', [])+[movement['position']]
        return [dict(surface_id=unit['surface_id'], end_m=[F(p['x_mm'], 1000), F(p['z_mm'], 1000)])
                for target in targets for p in (self.position(target),)]

    def _air_motion(self, unit, *, movable, validate_only=False):
        from backend.battle_map_motion import arc_length, lerp, sweep, _exact, record_path_segments, pending_path_energy
        from backend.battle_support import _intersection
        authority, surface = self.terrain._point(unit), unit['surface_id']
        require(not unit.get('geometry_position'), 'BATTLE_ROUTE_STALE', '旧跨路线精确位置不得作为飞行起点')
        old = deepcopy(unit.get('movement_progress'))
        trace, continuous = [deepcopy(unit['position_mm'])], []
        progress = dict(schema_version='qinglan-air-progress-v1', position_only=True,
            catalog_sha256=self.catalog.digest, surface_id=surface, exact_position_m=[str(v) for v in authority])
        if old:
            require(old.get('schema_version') == 'qinglan-air-progress-v1'
                    and old.get('catalog_sha256') == self.catalog.digest and old.get('surface_id') == surface
                    and old.get('rule_position_mm') == unit['position_mm'],
                    'BATTLE_ROUTE_STALE', '飞行游标不属于当前规则毫米位置/目录/层', 503)
        legs = self.route_resolver(deepcopy(unit)) if movable else []
        require(isinstance(legs, (list, tuple)) and len(legs) <= 32 and all(isinstance(l, dict)
                and isinstance(l.get('surface_id'), str) and isinstance(l.get('end_m'), (list, tuple))
                and len(l['end_m']) == 2 for l in legs),
                'BATTLE_ROUTE_INVALID', '空域路线必须是有限服务端候选')
        digest = sha256([dict(surface_id=l['surface_id'], end_m=[str(F(v)) for v in l['end_m']]) for l in legs])
        first, resumed_start, resumed_length, resumed_distance = 0, None, None, F(0)
        current = authority
        if movable and old and not old.get('position_only'):
            require(old.get('route_sha256') == digest, 'BATTLE_ROUTE_STALE', '飞行游标不属于当前路线', 503)
            first = old.get('leg_index')
            require(type(first) is int and 0 <= first < len(legs), 'BATTLE_ROUTE_STALE', '飞行路线游标越界', 503)
            resumed_start = tuple(_exact(v) for v in old['leg_start_m'])
            resumed_length, resumed_distance = _exact(old['leg_length_m']), _exact(old['distance_m'])
            current = tuple(_exact(v) for v in old['exact_position_m'])
            require(len(resumed_start) == 2 and resumed_length > 0 and 0 <= resumed_distance <= resumed_length
                    and resumed_length == arc_length(resumed_start, tuple(F(v) for v in legs[first]['end_m']))
                    and current == lerp(resumed_start, tuple(F(v) for v in legs[first]['end_m']), resumed_distance/resumed_length),
                    'BATTLE_ROUTE_STALE', '飞行路线精确余数或长度损坏', 503)
            pending = old.get('path_pending_segments', [])
            require(not pending or tuple(_exact(v) for v in pending[-1]['path_end_m']) == current,
                    'BATTLE_ROUTE_STALE', '飞行尾段未绑定预算末点')
        energy, seconds = max(F(0), k.number(unit['energy'])-pending_path_energy('recon', old)), F(5)
        finished, interrupted = bool(legs), not movable or not legs
        def prove(start, end):
            polygon = sweep(start, end, 3)
            require(_covered(polygon, self.terrain.air_allowed)
                    and not any(_intersection(polygon, p) for p in self.terrain.air_forbidden),
                    'BATTLE_AIR_ROUTE_FORBIDDEN', '完整飞行扫掠触及禁飞区或越界')
            layer = self.catalog.layer(surface)
            evidence = self.catalog.index.footprint(polygon, support_parts=tuple(layer['support_faces']),
                                                    support_faces=layer['support_faces'])
            require(evidence.continuous_support, 'BATTLE_AIR_ANCHOR_LAYER_MISSING',
                    '飞行路线缺少连续规则锚点支撑信息', 503)
        if validate_only:
            # 与执行共用同一游标校验和完整扫掠证明，检查剩余所有腿而非仅首个5秒。
            # 不消耗时间/能源，也不读取对方路线；地面仍采用原有安全前缀截停合同。
            prove(authority, current)
            for index, leg in enumerate(legs):
                if index < first:
                    continue
                require(leg['surface_id'] == surface, 'BATTLE_AIR_ANCHOR_LAYER_MISSING',
                        '跨层飞行尚须显式规则锚点层覆盖；不猜最近层', 503)
                target = tuple(F(v) for v in leg['end_m'])
                prove(current, target)
                current = target
            return
        for index, leg in enumerate(legs):
            if index < first:
                continue
            require(leg['surface_id'] == surface, 'BATTLE_AIR_ANCHOR_LAYER_MISSING',
                    '跨层飞行尚须显式规则锚点层覆盖；不猜最近层', 503)
            target = tuple(F(v) for v in leg['end_m'])
            start = resumed_start if index == first and resumed_start is not None else current
            length = resumed_length if index == first and resumed_length is not None else arc_length(start, target)
            distance = resumed_distance if index == first else F(0)
            remaining = length-distance
            if remaining == 0:
                continue
            budget = k.segment_budget('recon', [dict(length_mm=str(remaining*1000), surface='air', grade_squared='0')],
                                      str(energy), seconds=str(seconds))
            actual = F(budget['lengths_mm'][0])/1000
            if actual == 0:
                finished, interrupted = False, energy == 0
                break
            end = lerp(start, target, (distance+actual)/length)
            prove(current, end)
            continuous.append(dict(length_mm=str(actual*1000), surface='air', grade_squared='0',
                path_start_m=[str(v) for v in current], path_end_m=[str(v) for v in end], path_surface_id=surface,
                path_begin_seconds=str(5-seconds), path_end_seconds=str(5-F(budget['remaining_seconds']))))
            seconds = F(budget['remaining_seconds']); energy -= F(budget['energy'])
            progress = dict(schema_version='qinglan-air-progress-v1', catalog_sha256=self.catalog.digest,
                route_sha256=digest, exact_position_m=[str(v) for v in end], leg_index=index, surface_id=surface,
                leg_start_m=[str(v) for v in start], leg_length_m=str(length), distance_m=str(distance+actual))
            current = end
            if actual < remaining or seconds == 0 or energy == 0:
                finished = index == len(legs)-1 and end == target
                interrupted = not finished and energy == 0
                break
        terminal = finished or interrupted
        billed, pending, recorded = record_path_segments(continuous, old, interrupted=terminal, quantum_mm=self.distance_quantum_mm)
        physical = authority
        for segment in billed:
            point = tuple(_exact(v) for v in segment['path_end_m'])
            snapped = tuple(F(round(v*1000/self.distance_quantum_mm)*self.distance_quantum_mm, 1000) for v in point)
            prove(physical, snapped)
            height = self.catalog.height(surface, snapped)*1000+self.terrain.definition['airspace']['display_height_mm']
            trace.append(dict(x_mm=round(snapped[0]*1000), z_mm=round(snapped[1]*1000), height_mm=round(height)))
            physical = snapped
        require(len(trace) <= 128, 'BATTLE_MOTION_BUDGET_EXCEEDED', '毫米飞行轨迹分段超限', 503)
        if terminal:
            progress = dict(schema_version='qinglan-air-progress-v1', position_only=True,
                catalog_sha256=self.catalog.digest, surface_id=surface, exact_position_m=[str(v) for v in physical])
        progress.update(path_pending_segments=pending, recorded_distance_mm=recorded, rule_position_mm=deepcopy(trace[-1]))
        return Motion(tuple(trace), billed, progress, finished, surface, interrupted, tuple(continuous))

    def motions(self, state, *, start_positive, service_locked, transitions):
        airborne = {u['unit_id'] for u in state['units'] if k.alive(u) and u['unit_type'] == 'recon'
                    and u['air_state'] in ('flying', 'landing')}
        ground_state = deepcopy(state)
        ground_state['units'] = [u for u in ground_state['units'] if u['unit_id'] not in airborne]
        result = self.ground.motions(ground_state, route_resolver=self.route_resolver,
            start_positive=start_positive, service_locked=service_locked, transitions=transitions)
        for unit in state['units']:
            identity = unit['unit_id']
            if identity in airborne:
                result[identity] = self._air_motion(unit, movable=identity in start_positive
                    and identity not in service_locked and identity not in transitions
                    and unit['air_state'] == 'flying' and bool(unit.get('movement')))
        return result


def _load_geometry(surface_path, interaction_path, scenario_path, evidence_directory, geometry_type):
    """受控文件加载；所有源文件与证据逐字节冻结，永不接受HTTP上传。"""
    import json
    from backend.battle_map_surfaces import SurfaceCatalog
    from backend.battle_map_deployment import MapDeployment
    from backend.battle_terrain_assets import TerrainAssets, GEOMETRY_PATH, MANIFEST_PATH
    from backend.demo_domain import MAP_PATH
    paths = [Path(p).resolve() for p in (surface_path, interaction_path, scenario_path)]
    require(all(p.is_file() for p in paths), 'BATTLE_FROZEN_MANIFEST_MISSING',
            '缺少真实合法面/交互/独立场景文件；不会自动生成accepted', 503)
    payload, interactions, scenario = [json.loads(p.read_bytes()) for p in paths]
    files = [(p, byte_hash(p.read_bytes()).hexdigest()) for p in paths+[MAP_PATH, GEOMETRY_PATH, MANIFEST_PATH]]
    if geometry_type is not OfflineMapGeometry:
        report = scenario.get('local_manual_acceptance' if geometry_type is LocalManualMapGeometry else 'independent_acceptance', {})
        valid = (battle_scenario.local_manual_evidence_valid(report, scenario) if geometry_type is LocalManualMapGeometry
                 else battle_scenario.acceptance_evidence_valid(report,
                     field_energy_service_policy=scenario.get('field_energy_service_policy')))
        require(valid, 'BATTLE_SCENE_ACCEPTANCE_MISSING', '缺少对应范围的完整实地图验收报告', 503)
        for case in report['cases']:
            evidence = Path(evidence_directory) / (case['case_id']+'.json')
            require(evidence.is_file() and byte_hash(evidence.read_bytes()).hexdigest() == case['evidence_sha256'],
                    'BATTLE_ACCEPTANCE_EVIDENCE_MISSING', '实地图验收原始证据缺失或摘要不符', 503)
            files.append((evidence, case['evidence_sha256']))
    # 本地活动域的逐真实triangle审核不是passed布尔，必须绑定构建器的原始审计文件。
    if 'source_domain_audit_sha256' in interactions:
        audit_path = paths[1].parent / 'interaction-source-evidence.json'
        require(audit_path.is_file(), 'BATTLE_INTERACTION_AUDIT_MISSING', '缺少活动域来源原始审计', 503)
        audit = json.loads(audit_path.read_bytes())
        # 构建器可在审计完成后附交互反向指纹，不纳入其自引用哈希。
        audit_content = {key: value for key, value in audit.items() if key != 'interactions_sha256'}
        require(sha256(audit_content) == interactions['source_domain_audit_sha256']
                and audit.get('interactions_sha256', interactions['payload_sha256']) == interactions['payload_sha256'],
                'BATTLE_INTERACTION_AUDIT_STALE', '活动域审计与交互目录不符', 503)
        files.append((audit_path, byte_hash(audit_path.read_bytes()).hexdigest()))
    if geometry_type in (OfflineMapGeometry, LocalManualMapGeometry):
        require('source_domain_audit_sha256' in interactions and bool(interactions.get('verified_interaction_domains')),
                'BATTLE_INTERACTION_AUDIT_MISSING', '局部手动测试必须有真实活动域与独立来源审计', 503)
    assets = TerrainAssets.current()
    catalog = SurfaceCatalog(assets, payload)
    terrain = CatalogInteractions(catalog, interactions, scenario)
    def dock_airspace(facility):
        return terrain.air_legal(dict(unit_type='recon', surface_id=facility['dock_surface_id'],
                                      position_mm=facility['dock_position_mm']))
    deployment = MapDeployment(catalog, dock_airspace_validator=dock_airspace,
        allow_derived_facilities=geometry_type is LocalManualMapGeometry)
    return geometry_type(catalog, interactions, None, deployment, scenario=scenario,
        rules=rulebook.definition(), capacity=battle_capacity.definition(), source_files=files)


def load_server_geometry(surface_path, interaction_path, scenario_path, evidence_directory):
    """正式完整地图资格；保留13项真实规则验收门禁。"""
    return _load_geometry(surface_path, interaction_path, scenario_path, evidence_directory, MapGeometry)


def load_acceptance_geometry(surface_path, interaction_path, candidate_scenario_path):
    """真实源离线候选；仅隔离验收库专用安装入口可消费。"""
    return _load_geometry(surface_path, interaction_path, candidate_scenario_path, None, OfflineMapGeometry)


def load_local_manual_geometry(surface_path, interaction_path, scenario_path, evidence_directory):
    """局部真实手动测试资格，不授予正式竞技/Agent资格。"""
    return _load_geometry(surface_path, interaction_path, scenario_path, evidence_directory, LocalManualMapGeometry)
