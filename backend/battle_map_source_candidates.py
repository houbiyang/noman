"""Read-only original-map source materialization, never production scene approval.

No imports in production geometry and no database access. Generator replay is AST
restricted to its pure mesh/template/export classes; Blender and build_scene never
run. Source-coordinate quantization bounds validate provenance, not rule tolerance.
"""
import argparse
import ast
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math
import random
from pathlib import Path

from backend.battle_map_surfaces import rectangle, seal_catalog
from backend.battle_support import convex_polygon, signed_area, _intersection
from backend.battle_terrain import rational as q
from backend.battle_terrain_assets import TerrainAssets
from backend.demo_domain import require

GENERATOR = Path(__file__).resolve().parents[1] / 'unity-client/Tools/build_strategic_map.py'
OVERLAYS = {('farmland', 'FieldGreen'): 'field_source',
            ('farmland', 'FieldGold'): 'field_source',
            ('logistics', 'FieldGreen'): 'field_source',
            ('logistics', 'FieldGold'): 'field_source',
            ('farmland', 'Mud'): 'mud_source',
            ('landscape', 'Mud'): 'mud_source',
            ('scrub', 'ForestFloor'): 'forest_floor_source_density_unspecified',
            ('water', 'Water'): 'water_source'}


def _base(assets, schema):
    m = assets.manifest
    return dict(schema_version=schema, map_id=m['map_id'],
        definition_sha256=m['definition_sha256'], geometry_sha256=m['geometry_sha256'],
        base_annotations_sha256=m['payload_sha256'],
        generator_sha256=sha256(GENERATOR.read_bytes()).hexdigest(),
        production_ready=False, legal_layers_created=False, grants_route_permission=False)


def _polygon(points):
    return [[str(v) for v in p] for p in points]


def _zone_polygon(zone):
    x0, x1, z0, z1 = (q(zone[k]) for k in ('minX', 'maxX', 'minZ', 'maxZ'))
    return rectangle((x0+x1)/2, (z0+z1)/2, (x1-x0)/2, (z1-z0)/2)


def _bounds(poly):
    return min(v[0] for v in poly), min(v[1] for v in poly), max(v[0] for v in poly), max(v[1] for v in poly)


def _positive_bounds(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _edges(poly):
    return zip(poly, poly[1:]+poly[:1])


def build_deployment_ground_candidates(assets):
    """Actual 50m terrain cells inside original aligned deployment rectangles.

    Certificate is structural, not sampled: both exact half-cell projections,
    unique triangle per half, all cells present, common vertex heights identical.
    No overlay, solid, access or slope permission is inferred from this proof.
    """
    definition = assets.source_definition
    step_x = q(definition['width']) / (definition['heightColumns']-1)
    step_z = q(definition['length']) / (definition['heightRows']-1)
    zones = []
    parts = [p['part_id'] for p in assets.manifest['parts']
             if p['source_layer'] == 'terrain' and p['action'] == 'classify_base_ground']
    bounds = {p: assets.part_bounds(p) for p in parts}
    for source in definition['deploymentZones']:
        zone = _zone_polygon(source)
        x0, z0, x1, z1 = _bounds(zone)
        require(all(v % step == 0 for v, step in ((x0,step_x), (x1,step_x), (z0,step_z), (z1,step_z))),
                'BATTLE_SOURCE_MAPPING_UNSUPPORTED', '部署区不对齐原地形格，拒绝生成完整覆盖证明')
        faces, regions, cells, heights = {}, [], defaultdict(set), {}
        height_conflicts = []
        area = q(0)
        for part in parts:
            if not _positive_bounds(bounds[part], (x0,z0,x1,z1)):
                continue
            for index in range(assets.face_count(part)):
                triangle = assets.triangle(part,index)
                poly = convex_polygon([(v[0], v[2]) for v in triangle.vertices])
                if not _positive_bounds(_bounds(poly), (x0,z0,x1,z1)):
                    continue
                clipped = _intersection(poly, zone)
                if signed_area(clipped) <= 0:
                    continue
                # An original aligned rectangle must contain every selected cell.
                require(signed_area(clipped) == signed_area(poly), 'BATTLE_SOURCE_MAPPING_UNSUPPORTED',
                        '部署区截穿了原地形格，不能伪造整格覆盖')
                cx, cz, dx, dz = _bounds(poly)
                require(dx-cx == step_x and dz-cz == step_z and cx % step_x == cz % step_z == 0,
                        'BATTLE_SOURCE_MAPPING_INVALID', '原地形面不是声明的真实格三角')
                lo, hi = (cx,cz), (dx,dz)
                corners = frozenset(poly)
                expected = (frozenset((lo,(cx,dz),hi)), frozenset((lo,(dx,cz),hi)))
                require(corners in expected, 'BATTLE_SOURCE_MAPPING_INVALID', '原面投影不匹配地形格对角线')
                half = expected.index(corners)
                require(half not in cells[(cx,cz)], 'BATTLE_SOURCE_MAPPING_INVALID', '原地形格面重复')
                cells[(cx,cz)].add(half)
                for x,y,z in triangle.vertices:
                    key = x,z
                    if key in heights and heights[key] != y:
                        height_conflicts.append(dict(vertex_xz_m=_polygon([key])[0],
                                                     heights_m=[str(heights[key]),str(y)]))
                    heights[key] = y
                faces.setdefault(part, []).append(index)
                area += signed_area(clipped)
                regions.append(dict(part_id=part, triangle_index=index,
                    polygon_xz_m=_polygon(clipped),
                    vertices_xyz_m=_polygon(triangle.vertices),
                    grade_squared=str(triangle.maximum_local_grade_squared), base_surface='open',
                    semantic_status='base_classification_only_overlays_solids_access_unresolved'))
        expected_count = int((x1-x0)/step_x)*int((z1-z0)/step_z)
        complete = (len(cells) == expected_count and all(v == {0,1} for v in cells.values())
                    and area == signed_area(zone) and not height_conflicts)
        zones.append(dict(source_zone=deepcopy(source), source_polygon_xz_m=_polygon(zone),
            support_faces=faces, regions=regions, face_count=len(regions),
            certificate=dict(method='exhaustive_original_cell_half_identity_and_shared_vertex_heights',
                grid_step_m=[str(step_x),str(step_z)], expected_cell_count=expected_count,
                mapped_cell_count=len(cells), mapped_projection_area_m2=str(area),
                zone_area_m2=str(signed_area(zone)), height_conflicts=height_conflicts,
                complete_base_projection=complete),
            overlays_resolved=False, solids_resolved=False, deploy_accepted=False))
    result = _base(assets,'qinglan-deployment-ground-source-candidates-v1')
    result.update(zones=zones, interpretation='original_support_faces_and_regions_not_legal_deployment')
    return seal_catalog(result)


def _source_faces(assets, pair):
    """Only explicit layer/material pairs listed by the original generator."""
    for part in assets.manifest['parts']:
        if (part['source_layer'],part['source_material']) != pair:
            continue
        name = part['part_id']
        for index in range(assets.face_count(name)):
            xyz = assets.face_vertices(name,index)
            projected = tuple((v[0],v[2]) for v in xyz)
            require(signed_area(projected) != 0, 'BATTLE_SOURCE_MAPPING_INVALID',
                    '覆盖源包含垂直/退化投影，不能静默漏面')
            poly = convex_polygon(projected)
            yield name,index,poly,_bounds(poly)


def _conflicts(groups):
    """All positive-area pair intersections, integer/rational, no sampling.

    100m buckets are a broad phase only. Exact positive bounds/intersection decide;
    repeated bucket pairs are visited once, boundary contact is NOT area conflict.
    Areas are pair sums, explicitly not a union or authority overlay precedence.
    """
    buckets = defaultdict(list)
    for group_index, group in enumerate(groups):
        for part,index,poly,bounds in group:
            for x in range(int(bounds[0]//100), int(bounds[2]//100)+1):
                for z in range(int(bounds[1]//100), int(bounds[3]//100)+1):
                    buckets[(x,z)].append((group_index,part,index,poly,bounds))
    result = []
    for cell, entries in sorted(buckets.items()):
        for n, a in enumerate(entries):
            for b in entries[n+1:]:
                ga,pa,ia,poly_a,ba = a
                gb,pb,ib,poly_b,bb = b
                if not _positive_bounds(ba,bb):
                    continue
                # Unique first common broad-phase cell for this pair.
                if cell != (int(max(ba[0],bb[0])//100),int(max(ba[1],bb[1])//100)):
                    continue
                overlap = _intersection(poly_a,poly_b)
                area = signed_area(overlap)
                if area > 0:
                    result.append(dict(left=[ga,pa,ia],right=[gb,pb,ib],
                        polygon_xz_m=_polygon(overlap), area_m2=str(area),
                        relation='same_source_self_overlap' if ga == gb else 'source_overlay_overlap',
                        precedence_accepted=False))
    return result


def build_deployment_preflight(assets, catalog):
    """Bounded diagnostics at EXISTING source markers, not new camp placement.

    Complete base footprints and all source-overlay contacts are reported, but no
    deferred semantic, access, solid, airspace or capture check is bypassed. The
    output is evidence for the minimum dependency chain, not a frozen facility.
    """
    from backend.battle_support import SupportIndex
    from backend.battle_map_motion import sweep
    from backend.battle_map_deployment import MapDeployment
    from backend.runtime import ServiceError
    definition = assets.source_definition
    points = {p['id']:p for p in definition['points']}
    parts = [p['part_id'] for p in assets.manifest['parts']
             if p['source_layer']=='terrain' and p['action']=='classify_base_ground']
    bounds = {p:assets.part_bounds(p) for p in parts}
    index = SupportIndex(assets)
    observations, footprints = [], []
    for side in ('red','blue'):
        camp, supply = (points[side.upper()+'_'+suffix] for suffix in ('CAMP','SUPPLY'))
        center, anchor = (tuple(q(p[k]) for k in ('x','z')) for p in (camp,supply))
        distance2 = sum((a-b)**2 for a,b in zip(center,anchor))
        entry = dict(side=side,source_camp_marker=deepcopy(camp),
            source_supply_marker=deepcopy(supply),
            marker_role='illustrative_source_points_not_approved_camp_or_service_bindings',
            source_marker_squared_distance_m2=str(distance2),
            source_supply_within_100m=distance2<=10000,required_camp_count=3,
            source_camp_marker_count=1,approved_camp_count=0,footprints={})
        for label,poly,limit in (('camp_60x60',rectangle(*center,30),q('.05')),
                                 ('source_supply_support',rectangle(*anchor,4),q('.10')),
                                 ('direct_service_corridor',sweep(center,anchor,4),q('.10'))):
            pb = _bounds(poly)
            candidates = [p for p in parts if not (bounds[p][2]<pb[0] or pb[2]<bounds[p][0]
                          or bounds[p][3]<pb[1] or pb[3]<bounds[p][1])]
            evidence = index.footprint(poly,support_parts=candidates)
            item = dict(polygon_xz_m=_polygon(poly),base_support_evidence=evidence.to_dict(),
                base_footprint_continuous=evidence.continuous_support,
                base_slope_limit=str(limit),
                base_slope_within_limit=(evidence.max_grade_squared is not None
                    and evidence.max_grade_squared<=limit**2),
                source_overlay_contacts=[],overlays_accepted=False,solids_validated=False,
                access_validated=False,legal_placement=False)
            entry['footprints'][label]=item
            footprints.append((item,poly,pb))
        # Reproduce the actual public component gate without setting resolved or
        # inventing an approved support layer/height from candidate base evidence.
        try:
            MapDeployment(catalog).camp(dict(side=side,surface_id='ground',
                position_mm=dict(x_mm=int(center[0]*1000),z_mm=int(center[1]*1000),height_mm=0)))
        except ServiceError as error:
            entry['component_gate']=dict(code=error.code,
                probe_surface_id='ground',probe_is_approved_source_binding=False)
        else:
            entry['component_gate']=dict(code='UNEXPECTED_PROBE_PASS',production_ready=False)
        observations.append(entry)
    for pair in sorted(OVERLAYS):
        for part,face,poly,bounds in _source_faces(assets,pair):
            for item,footprint,pb in footprints:
                if bounds[2]<pb[0] or pb[2]<bounds[0] or bounds[3]<pb[1] or pb[3]<bounds[1]:continue
                overlap = _intersection(poly,footprint)
                if overlap:
                    item['source_overlay_contacts'].append(dict(source_layer=pair[0],source_material=pair[1],
                        part_id=part,triangle_index=face,intersection_xz_m=_polygon(overlap),
                        positive_area=signed_area(overlap)>0,
                        interpretation='source_projection_contact_not_accepted_rule_classification'))
    payload = catalog.payload
    result = _base(assets,'qinglan-deployment-source-preflight-v1')
    result.update(catalog_sha256=catalog.digest,observations=observations,
        accepted_layer_count=len(payload['layers']),accepted_connection_count=len(payload['connections']),
        frozen_camp_facility_count=len(payload.get('camp_facilities',[])),
        frozen_capture_mask_count=len(payload.get('capture_masks',[])),
        minimal_dependency_chain=[
            dict(stage='legal_continuous_surface',blocked=True,
                existing_component='SurfaceCatalog.__init__/layer/footprint',
                required=['resolved.support','resolved.overlays','resolved.solids','resolved.access'],
                deferred_dependencies=['overlay_resolution_or_certified_exclusion','building_solids_or_certified_exclusion'],
                reason='candidate_ground_alone_cannot_set_all_resolved_flags_true'),
            dict(stage='six_camp_and_unit_placements',blocked=True,
                existing_component='MapDeployment.camp/unit/validate_deployment',
                required=['three_camp_types_each_side','full_footprints','camp_60x60_5pct_open_or_gravel',
                          'nonoverlap','necessary_transit_clear']),
            dict(stage='external_service_and_dock_access',blocked=True,
                existing_component='MapDeployment._facility',
                required=['same_continuous_surface','service_anchor_within_100m','service_footprint_10pct',
                          'entrance_and_two_static_paths','dock_full_footprint_and_explicit_airspace']),
            dict(stage='real_scene_independent_acceptance',blocked=True,
                existing_component='MapDeployment.validate_deployment/load_frozen_geometry',
                required=['five_effective_capture_masks','service_capture_isolation',
                          'independent_real_map_evidence','immutable_source_binding'])],
        deferred_work_was_not_treated_as_absent=True,
        interpretation='read_only_existing_marker_diagnostics_not_six_camp_scene_configuration')
    return seal_catalog(result)


def _edge_hidden_interval(a, b, triangle):
    """Exact interval hidden by right coverage or an earlier coincident boundary."""
    def cross(p, r, v):
        return (r[0]-p[0])*(v[1]-p[1])-(r[1]-p[1])*(v[0]-p[0])
    low, high = q(0), q(1)
    for p,r in _edges(triangle):
        begin, end = cross(p,r,a),cross(p,r,b)
        delta = end-begin
        if delta == 0:
            if begin < 0: return None
        elif delta > 0:
            low = max(low,-begin/delta)
        else:
            high = min(high,-begin/delta)
        if low >= high: return None
    mid = (low+high)/2
    point = tuple(x+(y-x)*mid for x,y in zip(a,b))
    strictly_inside = all(cross(p,r,point)>0 for p,r in _edges(triangle))
    if strictly_inside or any(cross(a,b,v)<0 for v in triangle):
        return low,high
    # Same-side collinear source edges: the lexicographically earliest edge
    # owns overlap, avoiding doubled boundary segments without coordinate snaps.
    for p,r in _edges(triangle):
        if cross(a,b,p)==cross(a,b,r)==0 and tuple(sorted((p,r)))<tuple(sorted((a,b))):
            return low,high
    return None


def _union_boundary(faces):
    """Exact directed boundary fragments of projected source triangle union.

    Shared opposite edges are interior. Remaining CCW source edges keep source
    union on the left; subtract every rational interval with coverage on the right.
    Handles overlaps, coincident triangles, holes and T-junctions without grid
    sampling, floating polygon libraries, snapping or gameplay epsilon.
    """
    directions = defaultdict(set)
    buckets = defaultdict(set)
    for n,(_,_,poly,bounds) in enumerate(faces):
        for a,b in _edges(poly):
            edge = tuple(sorted((a,b)))
            directions[edge].add((a,b))
        for x in range(int(bounds[0]//100),int(bounds[2]//100)+1):
            for z in range(int(bounds[1]//100),int(bounds[3]//100)+1):buckets[x,z].add(n)
    boundary = []
    for edge,directed in sorted(directions.items()):
        if len(directed)==2: continue  # both sides already covered, even with overlaps
        a,b = next(iter(directed))
        bx0,bz0,bx1,bz1 = _bounds(edge)
        candidates = set()
        for x in range(int(bx0//100),int(bx1//100)+1):
            for z in range(int(bz0//100),int(bz1//100)+1):candidates.update(buckets[x,z])
        covered = []
        for n in sorted(candidates):
            _,_,poly,bounds = faces[n]
            if bx0>bounds[2] or bx1<bounds[0] or bz0>bounds[3] or bz1<bounds[1]:continue
            interval = _edge_hidden_interval(a,b,poly)
            if interval:covered.append(interval)
        cursor = q(0)
        for low,high in sorted(covered)+[(q(1),q(1))]:
            if low>cursor:
                boundary.append(tuple(tuple(x+(y-x)*t for x,y in zip(a,b)) for t in (cursor,low)))
            cursor = max(cursor,high)
            if cursor>=1:break
    return boundary


def build_overlay_candidates(assets):
    groups, exported = [], []
    zones = [(_zone_polygon(z),z['id']) for z in assets.source_definition['deploymentZones']]
    for pair, label in sorted(OVERLAYS.items()):
        faces = list(_source_faces(assets,pair))
        if not faces:
            continue  # Absence is reported separately, not invented as empty accepted region.
        edge_counts = Counter()
        refs = defaultdict(list)
        deployment = defaultdict(list)
        for part,index,poly,bounds in faces:
            refs[part].append(index)
            for a,b in _edges(poly):
                edge_counts[tuple(sorted((a,b)))] += 1
            for zone,zone_id in zones:
                if not _positive_bounds(bounds,_bounds(zone)):
                    continue
                clipped = _intersection(poly,zone)
                if signed_area(clipped)>0:
                    deployment[zone_id].append(dict(part_id=part,triangle_index=index,
                                                   polygon_xz_m=_polygon(clipped)))
        # Incidence boundaries are exact mesh edges, not a boolean-union outline.
        exported.append(dict(source_layer=pair[0], source_material=pair[1], source_meaning=label,
            source_faces=dict(refs),face_count=len(faces),
            generator_role=('one_metre_field_furrows' if pair==('farmland','Mud') else
                'forest_floor_tiles_not_density_zones' if pair==('scrub','ForestFloor') else
                'river_and_tributary_surface' if pair==('water','Water') else 'field_surface'),
            boundary_method='exact_rational_projected_triangle_union_directed_edge_fragments',
            union_boundary_edges_xz_m=[_polygon(e) for e in _union_boundary(faces)],
            mesh_incidence_boundary_method='undirected_xz_edges_not_boolean_union',
            boundary_edges_xz_m=[_polygon(e) for e,n in sorted(edge_counts.items()) if n==1],
            nonmanifold_edges=[dict(edge_xz_m=_polygon(e),incidence=n)
                               for e,n in sorted(edge_counts.items()) if n>2],
            deployment_intersections=dict(deployment),
            forest_density=None if pair == ('scrub','ForestFloor') else 'not_applicable',
            semantic_status='source_geometry_only_no_cross_layer_policy_or_precedence'))
        groups.append(faces)
    conflicts = _conflicts(groups)
    result = _base(assets,'qinglan-overlay-source-candidates-v1')
    result.update(groups=exported,positive_area_conflicts=conflicts,
        conflict_area_interpretation='each_exact_face_pair_not_union_area',
        conflict_dimension='xz_projection_only_not_collision_or_same_layer_acceptance',
        absent_source_pairs=[list(pair) for pair in sorted(OVERLAYS)
                            if not any((v['source_layer'],v['source_material'])==pair for v in exported)],
        unresolved=[dict(source_layer='scrub',source_material='ForestFloor',field='forest_density',
            alternatives=['sparse_forest','dense_forest','explicit_spatial_density_regions'],
            source_parts=[p['part_id'] for p in assets.manifest['parts']
                          if (p['source_layer'],p['source_material'])==('scrub','ForestFloor')],
            effects=['ground_movement_speed_energy_and_access'],
            density_does_not_change_forest_observation_rule=True),
            dict(source_layer='farmland',source_material='Mud',
                 field='rendered_one_metre_furrows_vs_tactical_mud_extent',
                 effects=['movement_forbidden_if_classified_mud','docking_forbidden_if_classified_mud']),
            dict(field='overlay_precedence_and_cross_layer_scope',
                 effects=['base_ground_deployment','road_water_forest_intersections'])])
    return seal_catalog(result)


def _pure_generator():
    """Execute only named definitions, not imports/top-level Blender/export code."""
    tree = ast.parse(GENERATOR.read_text())
    allowed = ('Mesh','Exporter','make_building')
    body = [node for node in tree.body if isinstance(node,(ast.ClassDef,ast.FunctionDef))
            and node.name in allowed]
    require(tuple(n.name for n in body) == allowed,'BATTLE_SOURCE_MAPPING_INVALID','原生成器纯定义已变化')
    tile = next(node for node in tree.body if isinstance(node,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='TILE' for t in node.targets))
    namespace = dict(math=math,random=random,TILE=ast.literal_eval(tile.value))
    exec(compile(ast.Module(body=body,type_ignores=[]),str(GENERATOR),'exec'),namespace)
    return namespace


def build_building_bindings(assets):
    """Replay exact template/export order, validate EVERY baked index and vertex.

    Only position in definition is rounded to .001m; yaw/scales are stored intact.
    Each baked coordinate has another .001m rounding. Thus a replay coordinate
    before final rounding may differ from baked by <=.001m (two half-mm bounds).
    This bounded serialization comparison is NOT a gameplay or bridge tolerance.
    Triangle identities are original exact indices, never nearest-face matching.
    """
    pure = _pure_generator()
    exporter = pure['Exporter']()
    mapping = []
    meshes = {}
    # Track append ownership with original Exporter code, no modified partitioning.
    for source in assets.source_definition['buildings']:
        kind = source['model'].removeprefix('Strategic_')
        key = kind,source['damage']
        if key not in meshes:
            meshes[key] = pure['make_building'](kind,source['damage'])
        mesh = meshes[key]
        before = {k:len(v['triangles'])//3 for k,v in exporter.parts.items()}
        exporter.append(mesh,source['layer'],tuple(source[k] for k in ('x','y','z')),
                        source['yaw'],tuple(source[k] for k in ('scaleX','scaleY','scaleZ')))
        ranges = {p:[before.get(p,0),len(v['triangles'])//3]
                  for p,v in exporter.parts.items() if len(v['triangles'])//3 > before.get(p,0)}
        mapping.append(dict(source_building=deepcopy(source),source_template=kind,
            source_fbx='unity-client/Assets/Sandbox/Resources/Battlefield/Models/'+source['model']+'.fbx',
            baked_triangle_ranges=ranges, solid_accepted=False))
    originals = {p['part_id'] for p in assets.manifest['parts'] if p['source_layer']=='settlement'}
    require(set(exporter.parts)==originals, 'BATTLE_SOURCE_MAPPING_INVALID',
            '建筑重放部件与真实settlement不一致，不允许部分匹配冒充完整对应')
    max_error = q(0)
    for part, replay in exporter.parts.items():
        # Internal read-only mesh view; never mutate source assets or shared interfaces.
        vertices, indices = assets._mesh(part)
        require(indices == replay['triangles'] and len(vertices)==len(replay['vertices']),
                'BATTLE_SOURCE_MAPPING_INVALID','建筑重放三角顺序/顶点数量不同')
        # Replay already rounded: maximum difference 1mm on the actual mm lattice.
        for actual, expected in zip(vertices,replay['vertices']):
            error = abs(q(actual)-q(expected))
            max_error = max(max_error,error)
            require(error <= q('0.001'), 'BATTLE_SOURCE_MAPPING_INVALID',
                    '建筑顶点超出定义/烘焙双重量化的来源包络')
    result = _base(assets,'qinglan-building-source-bindings-v1')
    models = {}
    for item in mapping:
        path = item['source_fbx']
        if path not in models:
            models[path] = sha256((GENERATOR.parents[2]/path).read_bytes()).hexdigest()
    result.update(bindings=mapping,source_model_sha256=models,building_count=len(mapping),part_count=len(originals),
        triangle_count=sum(len(v['triangles'])//3 for v in exporter.parts.values()),
        verification=dict(method='source_template_transform_append_order_full_index_and_vertex_replay',
            source_definition_position_quantum_m='0.001',baked_vertex_quantum_m='0.001',
            maximum_baked_vs_rounded_transform_replay_error_m=str(max_error),
            comparison_envelope_m='0.001',rule_geometry_tolerance_granted=False,
            all_settlement_parts_and_triangles_accounted=True,
            exact_unrounded_instance_position_recovered=False),
        interpretation='original_face_ownership_provenance_not_closed_rule_solid_or_thickness_acceptance')
    return seal_catalog(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=('ground','overlays','buildings','deployment-preflight'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    require(not args.output.exists(),'BATTLE_TERRAIN_OUTPUT_EXISTS','来源输出已存在，拒绝覆盖')
    builders = dict(ground=build_deployment_ground_candidates,overlays=build_overlay_candidates,
                    buildings=build_building_bindings)
    if args.kind=='deployment-preflight':
        from backend.battle_map_surfaces import SurfaceCatalog
        value=build_deployment_preflight(TerrainAssets.current(),SurfaceCatalog.current())
    else:
        value = builders[args.kind](TerrainAssets.current())
    with args.output.open('x') as target:
        json.dump(value,target,ensure_ascii=False,separators=(',',':'))
        target.write('\n')
    print(json.dumps(dict(kind=args.kind,payload_sha256=value['payload_sha256'],
                         production_ready=False),ensure_ascii=False))


if __name__=='__main__':
    main()
