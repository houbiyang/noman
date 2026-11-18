"""Frozen, explicit server-owned map semantics; unresolved never means passable.

The exported current catalog inventories source facts, not inferred road/forest/solid
rules. Reviewed layers can be supplied by an independent scene acceptance process.
No file or database is written implicitly; this module cannot publish a scenario.
"""
from copy import deepcopy
from collections import OrderedDict
from hashlib import sha256 as bytes_sha256
import json
from pathlib import Path

from backend.battle_support import (SupportIndex, convex_polygon, _intersection,
                                    _subtract, signed_area)
from backend.battle_terrain import rational as q
from backend.battle_terrain_assets import TerrainAssets
from backend.demo_domain import require
from backend.runtime import sha256

SCHEMA = 'qinglan-map-surface-catalog-v1'
MISSING = ('support_layers', 'road_and_access_permissions', 'forest_and_water_overlays',
           'building_solids', 'service_surfaces', 'docking_surfaces', 'capture_effective_layers')
DEPLOYMENT = {'red': (1000, 3200, 1900, 10400), 'blue': (10000, 1600, 11200, 8800)}
SURFACES = ('road', 'gravel', 'open', 'field', 'sparse_forest', 'dense_forest', 'mud', 'water', 'bridge')
PURPOSES = ('move', 'stop', 'deploy', 'camp', 'service', 'dock', 'capture')


def rectangle(x, z, half_x, half_z=None):
    x, z, half_x = q(x), q(z), q(half_x)
    half_z = half_x if half_z is None else q(half_z)
    require(half_x > 0 and half_z > 0, 'BATTLE_FOOTPRINT_INVALID', '占地尺寸必须为正')
    return ((x-half_x, z-half_z), (x+half_x, z-half_z),
            (x+half_x, z+half_z), (x-half_x, z+half_z))


def face_id(geometry_sha256, part_id, triangle_index):
    """Identity survives annotation revisions but not a change to the actual mesh."""
    require(isinstance(part_id, str) and part_id and type(triangle_index) is int and triangle_index >= 0,
            'BATTLE_SURFACE_INVALID', '真实面ID输入无效')
    part = bytes_sha256(part_id.encode()).hexdigest()
    return f'{geometry_sha256}:{part}:{triangle_index}'


def build_catalog(assets):
    source = assets.manifest
    parts = []
    for part in source['parts']:
        parts.append(dict(part_id=part['part_id'],
            surface_namespace=face_id(source['geometry_sha256'], part['part_id'], 0).rsplit(':', 1)[0],
            source_layer=part['source_layer'],
            source_material=part['source_material'], face_count=assets.face_count(part['part_id']),
            base_surface=part.get('base_surface'), classification_action=part['action'],
            semantic_status='unresolved', missing=list(MISSING)))
    definition = assets.source_definition
    facts = {key: deepcopy(definition.get(key, [])) for key in
             ('nodes', 'roads', 'deploymentZones', 'buildings')}
    from backend import capture_map
    layout, layout_digest = capture_map.current_layout()
    facts['capture_layout_sha256'] = None
    facts['capture_points'] = []
    if layout.get('baseMapId') == definition.get('mapId'):
        capture_map.validate(layout, definition)
        facts['capture_layout_sha256'] = layout_digest
        facts['capture_points'] = capture_map.objectives(definition, layout)
    facts['interpretation'] = 'explicit_source_fields_not_full_footprint_or_connection_acceptance'
    value = dict(schema_version=SCHEMA, source_facts=facts, map_id=source['map_id'],
        definition_sha256=source['definition_sha256'], geometry_sha256=source['geometry_sha256'],
        base_annotations_sha256=source['payload_sha256'], parts=parts, layers=[], connections=[],
        work_items=dict(
            reusable_source_facts=['nodes', 'roads', 'deploymentZones', 'buildings', 'capture_points', 'base_ground_policy'],
            implementation_pending=['source_to_exact_face_mapping', 'road_corridor_full_footprint',
                'existing_building_models_to_rule_solids', 'overlay_boundary_materialization',
                'service_dock_and_capture_effective_masks', 'frozen_navigation_time_energy_search',
                'independent_real_scene_acceptance'],
            semantic_decision_if_source_absent=['non_base_surface_type_or_forest_density_not_explicit_in_source',
                'unmapped_overlay_or_solid_meaning_not_defined_by_existing_rules'],
            known_route_blocks=['unverified_or_discontinuous_bridge_end_connections']),
        acceptance=dict(production_ready=False, missing=list(MISSING)),
        face_identity_format='{geometry_sha256}:{sha256_utf8_part_id}:{original_triangle_index}')
    value['payload_sha256'] = sha256(value)
    return value


def seal_catalog(value):
    """Canonical digest only, NOT an approval/signature. Internal tooling/tests only."""
    value = deepcopy(value)
    value.pop('payload_sha256', None)
    value['payload_sha256'] = sha256(value)
    return value



def build_bridge_face_bindings(assets):
    """Map explicit source bridge corridors onto original mesh face references.

    This is NOT a layer, portal or support permission. It uses the generator's
    routes/Asphalt fixed bridge-height contract, then tests actual triangle plane
    and exact positive-area corridor intersection. No near-height snapping or
    invented connecting triangle is allowed, including source node rounding gaps.
    """
    definition, manifest = assets.source_definition, assets.manifest
    nodes = {v['id']: v for v in definition['nodes']}
    bridges = [v for v in definition['roads'] if v.get('isBridge') is True]
    candidates = [v['part_id'] for v in manifest['parts']
                  if v['source_layer'] == 'routes' and v['source_material'] == 'Asphalt']
    bounds = {part: assets.part_bounds(part) for part in candidates}
    bindings = []
    for road in bridges:
        start, end = (nodes[road[key]] for key in ('startId', 'endId'))
        ax, az, bx, bz = (q(v) for v in (start['x'], start['z'], end['x'], end['z']))
        width, height = q(road['width']), q(road['surfaceHeight'])
        require(width > 0 and ((ax == bx) != (az == bz)),
                'BATTLE_SOURCE_MAPPING_UNSUPPORTED', '原始桥轴不是非退化轴向，不能猜测走廊')
        polygon = rectangle((ax+bx)/2, (az+bz)/2,
                            width/2 if ax == bx else abs(bx-ax)/2,
                            width/2 if az == bz else abs(bz-az)/2)
        min_x, max_x = min(x for x,z in polygon), max(x for x,z in polygon)
        min_z, max_z = min(z for x,z in polygon), max(z for x,z in polygon)
        faces, covers = {}, []
        for part in candidates:
            x0,z0,x1,z1 = bounds[part]
            if x1 < min_x or max_x < x0 or z1 < min_z or max_z < z0:
                continue
            for index in range(assets.face_count(part)):
                vertices = assets.face_vertices(part, index)
                if not all(v[1] == height for v in vertices):
                    continue
                projection = tuple((v[0], v[2]) for v in vertices)
                if not signed_area(projection):
                    continue
                projection = convex_polygon(projection)
                overlap = _intersection(polygon, projection)
                if overlap and signed_area(overlap) > 0:
                    faces.setdefault(part, []).append(index)
                    covers.append(projection)
        bindings.append(dict(source_road_id=road['id'], source_road=deepcopy(road),
            source_nodes=[deepcopy(start), deepcopy(end)],
            source_corridor_xz_m=[[str(v) for v in p] for p in polygon],
            true_plane_height_m=str(height), support_face_candidates=faces,
            candidate_face_count=sum(len(v) for v in faces.values()),
            source_corridor_fully_covered=_covered(polygon, covers),
            connection_accepted=False, legal_layer_created=False))
    generator = Path(__file__).resolve().parents[1]/'unity-client/Tools/build_strategic_map.py'
    return seal_catalog(dict(schema_version='qinglan-bridge-source-face-bindings-v1',
        map_id=manifest['map_id'], definition_sha256=manifest['definition_sha256'],
        geometry_sha256=manifest['geometry_sha256'], generator_sha256=bytes_sha256(generator.read_bytes()).hexdigest(),
        generator_reference='unity-client/Tools/build_strategic_map.py:ribbon/road',
        interpretation='exact_source_face_reference_only_not_connection_or_scene_acceptance',
        production_ready=False, bindings=bindings))


def _covered(polygon, covers):
    fragments = [polygon]
    for cover in covers:
        fragments = [piece for fragment in fragments for piece in _subtract(fragment, cover)]
        require(len(fragments) < 20000, 'BATTLE_SURFACE_BUDGET_EXCEEDED', '语义覆盖证明预算超限', 503)
    return not fragments


def _touch(a, b):
    # Contact with a forbidden surface is forbidden, not just positive area overlap.
    return bool(_intersection(a, b))


class SurfaceCatalog:
    """Validated frozen layer data. Construct exclusively from trusted server files.

    Integrity hashes do not establish semantic approval. The complete adapter must
    pin this payload digest to its separately accepted scenario manifest.
    """
    def __init__(self, assets, payload):
        baseline = build_catalog(assets)
        unsigned = deepcopy(payload)
        digest = unsigned.pop('payload_sha256', None)
        require(digest == sha256(unsigned), 'BATTLE_SURFACE_CATALOG_STALE', '合法面目录摘要不匹配')
        for key in ('schema_version', 'map_id', 'definition_sha256', 'geometry_sha256',
                    'base_annotations_sha256', 'parts', 'face_identity_format', 'source_facts', 'work_items'):
            require(payload.get(key) == baseline[key], 'BATTLE_SURFACE_CATALOG_STALE', '目录不是当前冻结真实资产')
        self.assets, self.index = assets, SupportIndex(assets)
        self._payload = deepcopy(payload)
        layers = payload.get('layers')
        require(isinstance(layers, list) and len(layers) <= 256,
                'BATTLE_SURFACE_INVALID', '层目录必须是有限列表')
        self._layers = {}
        self._footprint_cache = OrderedDict()
        self._height_cache = OrderedDict()
        for raw in layers:
            layer = deepcopy(raw)
            identity = layer.get('surface_id')
            require(isinstance(identity, str) and identity and identity not in self._layers,
                    'BATTLE_SURFACE_INVALID', '合法层ID缺失或重复')
            require(all(layer.get('resolved', {}).get(k) is True for k in ('support', 'overlays', 'solids', 'access')),
                    'BATTLE_SURFACE_UNRESOLVED', '层的支撑、覆盖、实体及权限必须全部明确')
            faces = layer.get('support_faces')
            require(isinstance(faces, dict) and 0 < len(faces) <= 64, 'BATTLE_SURFACE_INVALID', '层须指定真实支撑面')
            for part, indices in faces.items():
                require(part in assets.part_ids() and isinstance(indices, list) and indices
                        and len(set(indices)) == len(indices)
                        and all(type(i) is int and 0 <= i < assets.face_count(part) for i in indices),
                        'BATTLE_SURFACE_INVALID', '真实支撑面索引无效')
                for i in indices:
                    assets.triangle(part, i)  # vertical/degenerate faces cannot become support
            require(layer.get('ground_surface') in SURFACES, 'BATTLE_SURFACE_UNRESOLVED', '层地表未确认')
            for group in ('regions', 'overlays', 'solids', 'access'):
                require(isinstance(layer.get(group), list) and len(layer[group]) <= 1024,
                        'BATTLE_SURFACE_UNRESOLVED', '缺少明确的地表、覆盖、实体或权限列表')
                for item in layer[group]:
                    item['polygon'] = convex_polygon(item['polygon'])
                    if group == 'regions':
                        require(item.get('surface') in SURFACES, 'BATTLE_SURFACE_INVALID', '区域地表无效')
                    elif group == 'overlays':
                        require(item.get('kind') in ('forest', 'water'), 'BATTLE_SURFACE_INVALID', '覆盖类型无效')
                    elif group == 'access':
                        require(isinstance(item.get('sides'), list) and item['sides']
                                and set(item['sides']) <= set(DEPLOYMENT)
                                and isinstance(item.get('purposes'), list) and item['purposes']
                                and set(item['purposes']) <= set(PURPOSES),
                                'BATTLE_SURFACE_INVALID', '访问权限须明确阵营及动作')
            # Ambiguous region interiors must not choose a rule by order/part name.
            regions = layer['regions']
            for n, a in enumerate(regions):
                for b in regions[:n]:
                    require(a['surface'] == b['surface'] or signed_area(_intersection(a['polygon'], b['polygon'])) == 0,
                            'BATTLE_SURFACE_CONFLICT', '不同地表区域重叠')
            if 'road_faces' in layer:
                require(isinstance(layer['road_faces'], dict) and len(layer['road_faces']) <= 64,
                        'BATTLE_SURFACE_INVALID', '道路必须绑定有限原始铺面部件')
                for part, indices in layer['road_faces'].items():
                    annotation = assets.annotation(part)
                    require(annotation['source_layer'] == 'routes' and annotation['source_material'] == 'Asphalt'
                            and isinstance(indices, list) and indices and len(set(indices)) == len(indices)
                            and all(type(i) is int and 0 <= i < assets.face_count(part) for i in indices),
                            'BATTLE_SURFACE_INVALID', '道路投影必须引用原始routes/Asphalt面')
            if 'verified_domains' in layer:
                require(isinstance(layer['verified_domains'], list) and 0 < len(layer['verified_domains']) <= 4096,
                        'BATTLE_SURFACE_INVALID', '局部验真层必须有有限非空活动域')
                layer['verified_domains'] = [convex_polygon(p) for p in layer['verified_domains']]
            self._layers[identity] = layer
        require(isinstance(payload.get('connections'), list), 'BATTLE_SURFACE_INVALID', '须显式提供层连接清单')

    @classmethod
    def current(cls):
        assets = TerrainAssets.current()
        return cls(assets, json.loads(Path(__file__).with_name('battle-map-surfaces-v1.json').read_bytes()))

    @property
    def payload(self):
        return deepcopy(self._payload)

    def source_matches(self, payload):
        """Read-only full semantic equality; never expose mutable frozen payload."""
        return self._payload == payload

    @property
    def digest(self):
        return self._payload['payload_sha256']

    def layer(self, surface_id):
        require(surface_id in self._layers, 'BATTLE_SURFACE_UNRESOLVED', '该真实层未验收；拒绝相应动作', 503)
        return deepcopy(self._layers[surface_id])

    def triangles(self, surface_id, bounds=None):
        """Exact original faces, optionally broad-phased by a closed xz AABB."""
        from backend.battle_support import _overlap
        self.layer(surface_id) if surface_id not in self._layers else None
        layer = self._layers[surface_id]
        selected = []
        for part, ids in layer['support_faces'].items():
            if part not in self.index._bounds:
                self.index._bounds[part] = self.assets.part_bounds(part)
            if bounds is not None and not _overlap(bounds, self.index._bounds[part]):
                continue
            allowed = set(ids)
            faces, _ = self.index._part(part)
            selected.extend((part, f.triangle_index, f.triangle) for f in faces
                            if f.triangle_index in allowed and (bounds is None or _overlap(bounds, f.bounds)))
        return tuple(selected)

    def semantic_regions(self, surface_id, bounds=None):
        """Base regions plus exact visual road projections; never elevated support."""
        from backend.battle_support import _bounds, _overlap
        if surface_id not in self._layers:
            self.layer(surface_id)
        layer = self._layers[surface_id]
        regions = [r for r in layer['regions'] if bounds is None or _overlap(bounds, _bounds(r['polygon']))]
        for part, ids in layer.get('road_faces', {}).items():
            if part not in self.index._bounds:
                self.index._bounds[part] = self.assets.part_bounds(part)
            if bounds is not None and not _overlap(bounds, self.index._bounds[part]):
                continue
            allowed = set(ids)
            faces, _ = self.index._part(part)
            regions.extend(dict(polygon=f.projection, surface='road') for f in faces
                           if f.triangle_index in allowed and (bounds is None or _overlap(bounds, f.bounds)))
        return tuple(regions)

    def height(self, surface_id, point):
        point = tuple(q(v) for v in point)
        key = (surface_id, point)
        if key in self._height_cache:
            self._height_cache.move_to_end(key)
            return self._height_cache[key]
        bounds = point + point
        heights = {triangle.height(point) for _, _, triangle in self.triangles(surface_id, bounds)
                   if triangle.contains(point)}
        require(len(heights) == 1, 'BATTLE_SUPPORT_INVALID', '支撑高度缺失或多层冲突')
        value = next(iter(heights))
        self._height_cache[key] = value
        if len(self._height_cache) > 4096:
            self._height_cache.popitem(last=False)
        return value

    def footprint(self, surface_id, polygon, *, side, purpose, kind='ground', direction=None):
        # Immutable source catalog + complete exact input. No quantized position,
        # rounded slope, client state, negative result or acceptance is cached.
        polygon = convex_polygon(polygon)
        direction_key = None if direction is None else tuple(tuple(q(v) for v in point) for point in direction)
        key = (surface_id, polygon, side, purpose, kind, direction_key)
        if key in self._footprint_cache:
            self._footprint_cache.move_to_end(key)
            return self._footprint_cache[key]
        evidence = self._footprint_uncached(surface_id, polygon, side=side, purpose=purpose,
                                            kind=kind, direction=direction_key)
        self._footprint_cache[key] = evidence
        if len(self._footprint_cache) > 2048:
            self._footprint_cache.popitem(last=False)
        return evidence

    def _footprint_uncached(self, surface_id, polygon, *, side, purpose, kind='ground', direction=None):
        require(side in DEPLOYMENT and purpose in PURPOSES and kind in ('ground', 'support', 'recon'),
                'BATTLE_SURFACE_INVALID', '未知阵营、动作或兵种')
        polygon = convex_polygon(polygon)
        if surface_id not in self._layers:
            self.layer(surface_id)
        layer = self._layers[surface_id]
        if 'verified_domains' in layer:
            from backend.battle_support import _bounds, _overlap
            bounds = _bounds(polygon)
            covers = [p for p in layer['verified_domains'] if _overlap(bounds, _bounds(p))]
            require(_covered(polygon, covers), 'BATTLE_SURFACE_OUTSIDE_VERIFIED_DOMAIN',
                    '完整占地超出真实来源局部验真域；部署区权限不能扩大验真范围')
        evidence = self.index.footprint(polygon, support_parts=tuple(layer['support_faces']),
                                       support_faces=layer['support_faces'])
        require(evidence.continuous_support, 'BATTLE_SUPPORT_INVALID', '完整占地缺口、越界或真实高度不连续')
        require(not evidence.non_support_face_ids, 'BATTLE_SUPPORT_INVALID', '支撑层混入垂直面')
        covers = [a['polygon'] for a in layer['access'] if side in a['sides'] and purpose in a['purposes']]
        if purpose in ('move', 'stop', 'deploy', 'camp'):
            x0, z0, x1, z1 = DEPLOYMENT[side]
            covers.append(((q(x0), q(z0)), (q(x1), q(z0)), (q(x1), q(z1)), (q(x0), q(z1))))
        require(_covered(polygon, covers), 'BATTLE_ROUTE_FORBIDDEN', '完整占地超出本方部署区或批准走廊/停止面')
        require(not any(_touch(polygon, s['polygon']) for s in layer['solids']),
                'BATTLE_STATIC_BLOCKED', '完整占地触及静态实体')
        forbidden = {'water', 'dense_forest', 'mud'}
        if purpose == 'camp':
            forbidden = set(SURFACES) - {'open', 'gravel'}
        elif purpose == 'dock':
            forbidden = set(SURFACES) - {'road', 'open', 'gravel', 'field'}
        elif kind == 'support':
            forbidden.add('sparse_forest')
        # Default ground applies only outside explicit region interiors.
        fragments = [polygon]
        from backend.battle_support import _bounds
        for region in self.semantic_regions(surface_id, _bounds(polygon)):
            if region['surface'] in forbidden:
                require(not _touch(polygon, region['polygon']), 'BATTLE_TERRAIN_FORBIDDEN', '完整占地触及禁行地表')
            if layer['ground_surface'] in forbidden:
                fragments = [piece for fragment in fragments for piece in _subtract(fragment, region['polygon'])]
            require(len(fragments) <= 20000, 'BATTLE_SURFACE_BUDGET_EXCEEDED', '地表差集预算超限', 503)
        require(not fragments or layer['ground_surface'] not in forbidden,
                'BATTLE_TERRAIN_FORBIDDEN', '完整占地包含禁行默认地表')
        for overlay in layer['overlays']:
            if overlay['kind'] == 'water' or purpose in ('camp', 'dock'):
                require(not _touch(polygon, overlay['polygon']), 'BATTLE_OVERLAY_FORBIDDEN', '完整占地触及禁用覆盖')
        if purpose in ('camp', 'dock', 'service'):
            limit = q('0.10') if purpose == 'service' else q('0.05')
            require(evidence.max_grade_squared is not None and evidence.max_grade_squared <= limit**2,
                    'BATTLE_SLOPE_INVALID', '完整占地局部坡度超过服务/停靠/建营上限')
        elif kind != 'recon':
            from backend.battle_terrain import slope_factor
            for part, i in evidence.face_ids:
                triangle = self.assets.triangle(part, i)
                grade = (triangle.directional_slope(*direction).grade_squared if direction
                         else triangle.maximum_local_grade_squared)
                slope_factor(kind, grade)
        return evidence

    def center_surface(self, surface_id, point):
        if surface_id not in self._layers:
            self.layer(surface_id)
        layer = self._layers[surface_id]
        # Exact boundaries must be split; take an interior point for costing.
        covers = [r['surface'] for r in self.semantic_regions(surface_id, tuple(point)+tuple(point))
                  if _point_inside(point, r['polygon'])]
        require(len(set(covers)) <= 1, 'BATTLE_SURFACE_CONFLICT', '中心恰处不同地表交界，须分段取内部点')
        return covers[0] if covers else layer['ground_surface']


def _point_inside(point, polygon):
    x, z = point
    return all((b[0]-a[0])*(z-a[1])-(b[1]-a[1])*(x-a[0]) >= 0
               for a, b in zip(polygon, polygon[1:]+polygon[:1]))


def export_current(path):
    path = Path(path)
    with path.open('x') as stream:
        json.dump(build_catalog(TerrainAssets.current()), stream, ensure_ascii=False, indent=2)
        stream.write('\n')
