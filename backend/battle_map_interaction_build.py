"""Build bounded local-manual interactions from the unchanged real map.

This is not competitive scene acceptance. Empty forest/solid lists are justified
only inside explicit verified domains. Both aircraft footprints and entire sight
lines must stay in those domains. Every original mesh remains a ray blocker.
"""
import argparse
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path

from backend.battle_map_geometry import seal_interactions
from backend.battle_map_surfaces import SurfaceCatalog
from backend.battle_support import convex_polygon
from backend.battle_terrain_assets import TerrainAssets
from backend.demo_domain import require
from backend.runtime import sha256

# These are source generator roles, not material-name guessing. The road surface
# approval and the actual terrain support are independently checked by the catalog.
GROUND_ROLES = {('terrain', 'GroundLow'), ('terrain', 'GroundHigh'),
                ('landscape', 'GroundLow'), ('landscape', 'GroundHigh')}
WATER_ROLE = ('water', 'Water')
COSMETIC_ROLES = {('grid', 'Marking'), ('routes', 'Asphalt'), ('routes', 'Marking')}


def _bounds(poly):
    return min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly)


def _touch_bounds(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def verify_clear_domains(assets, layers, *, allow_water_overflight=False):
    """Independently exclude unknown overlays and above-support solids.

    Uses conservative CLOSED projected triangle boxes, including vertical walls
    and degenerate projections. A rejected box may contain empty space; it never
    grants extra clearance. Bridge understructure below its support is separate.
    """
    domains = []
    for layer in layers:
        polygons = layer.get('verified_domains')
        require(isinstance(polygons, list) and polygons, 'BATTLE_LOCAL_DOMAIN_MISSING',
                '本地交互必须绑定非空真实活动域', 503)
        minimum = min(min(v[1] for v in assets.face_vertices(part, index))
                      for part, indices in layer['support_faces'].items() for index in indices)
        for raw in polygons:
            polygon = convex_polygon(raw)
            domains.append(dict(surface_id=layer['surface_id'], polygon=polygon,
                                bounds=_bounds(polygon), support_minimum=minimum,
                                bridge=layer['ground_surface'] == 'bridge'))
    require(domains, 'BATTLE_LOCAL_DOMAIN_MISSING', '无可核验的活动域', 503)
    # Broad phase only: bucket membership never grants clearance. All candidate
    # contacts are still checked with the original rational closed bounds.
    buckets = {}
    def cells(bounds):
        for x in range(int(bounds[0] // 100), int(bounds[2] // 100)+1):
            for z in range(int(bounds[1] // 100), int(bounds[3] // 100)+1):
                yield x, z
    for n, domain in enumerate(domains):
        for cell in cells(domain['bounds']):
            buckets.setdefault(cell, set()).add(n)
    checked_faces = 0
    for part in assets.part_ids():
        annotation = assets.annotation(part)
        role = annotation['source_layer'], annotation['source_material']
        if role in GROUND_ROLES | COSMETIC_ROLES or (allow_water_overflight and role == WATER_ROLE):
            continue
        bounds3 = assets.part_bounds3d(part)
        bounds = bounds3[0], bounds3[2], bounds3[3], bounds3[5]
        candidate_ids = {n for cell in cells(bounds) for n in buckets.get(cell, ())}
        if not candidate_ids:
            continue
        for index in range(assets.face_count(part)):
            vertices = assets.face_vertices(part, index)
            projected = _bounds([(v[0], v[2]) for v in vertices])
            top = max(v[1] for v in vertices)
            checked_faces += 1
            nearby = {n for cell in cells(projected) for n in buckets.get(cell, ())}
            for n in nearby:
                domain = domains[n]
                if not _touch_bounds(projected, domain['bounds']):
                    continue
                if domain['bridge'] and top <= domain['support_minimum']:
                    # Actual piers/water are wholly below the bridge support.
                    continue
                require(False, 'BATTLE_LOCAL_DOMAIN_OBSTRUCTED',
                        f"活动域触及未排除覆盖或实体: {domain['surface_id']} / {part} / {index}", 503)
    return dict(method='closed_original_triangle_aabb_exclusion_with_bridge_vertical_separation',
                domain_count=len(domains), checked_candidate_faces=checked_faces,
                all_source_parts_accounted=len(assets.part_ids()),
                forest_absence_scope='verified_interaction_domains_only',
                solid_anchor_exclusion_scope='verified_interaction_domains_only',
                geometry_sha256=assets.manifest['geometry_sha256'])


def water_overflight_domains(assets):
    """Known water permits flight/rays, never ground travel; unknown cover is closed.

    The existing generator explicitly exports river/tributary sheets in water/Water.
    These meshes remain blockers in RayIndex. All other unknown source projections
    are removed using closed original triangle boxes, including vertical walls.
    """
    from backend.battle_map_surface_build import CELL, COUNT, merge_cells
    blocked = [bytearray(COUNT) for _ in range(COUNT)]
    for part in assets.part_ids():
        annotation = assets.annotation(part)
        role = annotation['source_layer'], annotation['source_material']
        if role in GROUND_ROLES | COSMETIC_ROLES | {WATER_ROLE}:
            continue
        vertices, indices = assets._mesh(part)
        mm = [round(v*1000) for v in vertices]
        require(all(abs(v*1000-i) < 0.00001 for v, i in zip(vertices, mm)),
                'BATTLE_SOURCE_MAPPING_INVALID', '源资产不是已冻结毫米网格', 503)
        for offset in range(0, len(indices), 3):
            corners = indices[offset:offset+3]
            xs, zs = [mm[j*3] for j in corners], [mm[j*3+2] for j in corners]
            x0, x1 = max(0, (min(xs)-1)//(CELL*1000)), min(COUNT-1, max(xs)//(CELL*1000))
            z0, z1 = max(0, (min(zs)-1)//(CELL*1000)), min(COUNT-1, max(zs)//(CELL*1000))
            if x1 >= x0:
                for z in range(z0, z1+1):
                    blocked[z][x0:x1+1] = b'\1'*(x1-x0+1)
    return merge_cells(blocked)


def build_interactions(catalog, *, display_height_mm=30000):
    payload = catalog.payload
    require(payload.get('release_scope') == 'local_manual_test'
            or payload.get('acceptance', {}).get('release_scope') == 'local_manual_test',
            'BATTLE_LOCAL_SCOPE_MISSING', '只生成显式本地手动测试目录，不授予正式地图资格', 503)
    layers = payload['layers']
    audit = verify_clear_domains(catalog.assets, layers)
    domains = water_overflight_domains(catalog.assets)
    ground = next(layer for layer in layers if layer['ground_surface'] == 'open')
    air_proof = verify_clear_domains(catalog.assets, [dict(surface_id='air:water-overflight',
        ground_surface='open', support_faces=ground['support_faces'], verified_domains=domains)],
        allow_water_overflight=True)
    audit['air_and_ray_domain_proof'] = air_proof
    audit['water_policy'] = dict(source_role=list(WATER_ROLE), ground_permission=False,
        flight_permission='explicit_verified_air_domain_only', mesh_ray_blocker=True)
    audit['interaction_domain_count'] = len(domains)
    masks = payload.get('capture_masks', [])
    require(len(masks) == 5 and len({m['point_id'] for m in masks}) == 5,
            'BATTLE_CAPTURE_MASK_UNVERIFIED', '本地地图也须有五点真实有效层绑定', 503)
    docks = [layer['surface_id'] for layer in layers
             if any('dock' in access['purposes'] for access in layer['access'])]
    require(docks, 'BATTLE_DOCK_SURFACE_MISSING', '无明确停靠权限层', 503)
    require(type(display_height_mm) is int and display_height_mm > 0,
            'BATTLE_AIRSPACE_MISSING', '表现高度须为正整毫米', 503)
    audit.update(surface_catalog_sha256=catalog.digest,
                 release_scope='local_manual_test', competitive_ready=False)
    definition = seal_interactions(dict(schema_version='qinglan-map-interactions-v1',
        surface_catalog_sha256=catalog.digest, release_scope='local_manual_test',
        forest_polygons=[], verified_interaction_domains=domains,
        airspace=dict(allowed=domains, forbidden=[], display_height_mm=display_height_mm,
                      policy='explicit_local_verified_domains_with_water_overflight'),
        blocker_parts=list(catalog.assets.part_ids()), transparent_parts=[],
        solid_volumes_validated=True,
        capture_layers={m['point_id']: [m['surface_id']] for m in masks},
        dock_surfaces=docks, source_domain_audit_sha256=sha256(audit),
        limitations=['地面只在核验地面域通行；飞行与射线使用另行核验的空域，可越过水面，未知区域关闭。',
                     '本地手动测试不代表正式地图公平性或跨桥连通验收。']))
    return definition, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--surface', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'BATTLE_OUTPUT_EXISTS', '输出目录已存在，拒绝覆盖')
    catalog = SurfaceCatalog(TerrainAssets.current(), json.loads(args.surface.read_text()))
    definition, audit = build_interactions(catalog)
    args.output.mkdir(parents=True)
    for name, value in [('interactions.json', definition), ('interaction-source-evidence.json', audit)]:
        (args.output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output), interactions_sha256=definition['payload_sha256'],
                         domains=audit['interaction_domain_count']), ensure_ascii=False))


if __name__ == '__main__':
    main()
