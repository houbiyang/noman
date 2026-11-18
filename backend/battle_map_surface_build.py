"""Reproducible local-manual surface component from frozen real mesh; no DB.

Unknown non-base geometry is excluded, not reclassified. The conservative cell
cover grants no formal scenario/fairness qualification and never changes assets.
"""
import argparse
from collections import deque
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from backend.battle_map_surfaces import (SurfaceCatalog, build_catalog, seal_catalog,
    build_bridge_face_bindings, rectangle, DEPLOYMENT, PURPOSES)
from backend.battle_map_deployment import MapDeployment
from backend.battle_terrain_assets import TerrainAssets
from backend.battle_terrain import rational as q
from backend.demo_domain import require, barracks_id, ServiceError
from backend.runtime import sha256 as payload_hash

GENERATOR = Path(__file__).resolve().parents[1]/'unity-client/Tools/build_strategic_map.py'
GROUND = 'ground:source-verified'
BRIDGE = 'bridge:ROAD_010'
CELL = 50
COUNT = 12000//CELL
VISUAL = {('grid', 'Marking'), ('routes', 'Asphalt'), ('routes', 'Marking')}


def polygon(x0, z0, x1, z1):
    return [[x0,z0],[x1,z0],[x1,z1],[x0,z1]]


def _wire(poly):
    # All exported polygons in this builder lie on the exact millimetre lattice.
    return [[int(v) if q(v).denominator == 1 else str(float(v)) for v in p] for p in poly]


def clear_cells(assets):
    """Exact integer-mm AABB exclusion, including vertical/degenerate projections.

    Every closed cell touching a non-base/non-cosmetic triangle AABB is deleted.
    AABB over-approximation can reject valid land, never approve an unknown solid.
    """
    blocked = [bytearray(COUNT) for _ in range(COUNT)]
    inventory = []
    for part in assets.manifest['parts']:
        pair = part['source_layer'], part['source_material']
        role = ('approved_base' if part['action']=='classify_base_ground' else
                'source_visual_only' if pair in VISUAL else 'exclude_projection')
        vertices, indices = assets._mesh(part['part_id'])
        inventory.append(dict(part_id=part['part_id'], role=role, triangle_count=len(indices)//3))
        if role != 'exclude_projection':
            continue
        # JSON geometry is exactly .001m serialized; do not use float comparisons.
        mm = [round(v*1000) for v in vertices]
        require(all(abs(v*1000-i) < 0.00001 for v,i in zip(vertices,mm)),
                'BATTLE_SOURCE_MAPPING_INVALID', '源资产不是已冻结毫米网格')
        for offset in range(0,len(indices),3):
            corners = indices[offset:offset+3]
            xs, zs = [mm[j*3] for j in corners], [mm[j*3+2] for j in corners]
            x0,x1 = max(0,(min(xs)-1)//(CELL*1000)), min(COUNT-1,max(xs)//(CELL*1000))
            z0,z1 = max(0,(min(zs)-1)//(CELL*1000)), min(COUNT-1,max(zs)//(CELL*1000))
            if x1 >= x0:
                for z in range(z0,z1+1):
                    blocked[z][x0:x1+1] = b'\1'*(x1-x0+1)
    return blocked, inventory


def merge_cells(blocked):
    """Lossless union of remaining closed cells, never a hull over holes."""
    active, result = {}, []
    for z,row in enumerate(list(blocked)+[bytearray(b'\1'*COUNT)]):
        spans=[]; x=0
        while x<COUNT:
            if row[x]: x+=1; continue
            left=x
            while x<COUNT and not row[x]: x+=1
            spans.append((left,x))
        next_active = {key: active.pop(key,z) for key in spans}
        result.extend(polygon(left*CELL,start*CELL,right*CELL,z*CELL)
                      for (left,right),start in sorted(active.items()))
        active = next_active
    return result


def _clear(blocked,x,z,hx,hz):
    x0,z0,x1,z1 = (x-hx)//CELL,(z-hz)//CELL,(x+hx)//CELL,(z+hz)//CELL
    return (0<=x0<=x1<COUNT and 0<=z0<=z1<COUNT and
            all(not blocked[zz][xx] for zz in range(z0,z1+1) for xx in range(x0,x1+1)))


def _component(blocked, start):
    x,z = start
    if blocked[z][x]: return set()
    queue=deque([start]); seen={start}
    while queue:
        x,z=queue.popleft()
        for xx,zz in ((x+1,z),(x-1,z),(x,z+1),(x,z-1)):
            if 0<=xx<COUNT and 0<=zz<COUNT and not blocked[zz][xx] and (xx,zz) not in seen:
                seen.add((xx,zz));queue.append((xx,zz))
    return seen


def _layer(identity, faces, domains, *, surface='open', navigation='ground'):
    return dict(surface_id=identity, navigation_layer_id=navigation,
        collision_layer_id=navigation, support_faces=faces, verified_domains=domains,
        ground_surface=surface, resolved=dict(support=True,overlays=True,solids=True,access=True),
        regions=[],overlays=[],solids=[],access=[dict(polygon=p,sides=['red','blue'],purposes=list(PURPOSES))
                                                for p in [polygon(0,0,12000,12000)]])


def _mm(catalog,surface,x,z):
    return dict(x_mm=x*1000,z_mm=z*1000,height_mm=round(catalog.height(surface,(x,z))*1000))


def _select_layout(catalog,blocked):
    """Deterministic legal selection, not moving a submitted user's config."""
    checker = MapDeployment(catalog)
    configs={side:dict(quantities=dict(recon=1,ground=1,support=1),units=[],camps=[]) for side in DEPLOYMENT}
    camps=[];facilities=[];units=[]; selections=[]
    # Real clear components linked to P02 (west) and P03/P04 (east).
    targets={'red':(4475,4375),'blue':(7600,4400)}
    for side in ('red','blue'):
        comp=_component(blocked,tuple(v//CELL for v in targets[side]))
        x0,z0,x1,z1=DEPLOYMENT[side]
        options=[(x,z) for z in range(z0+100,z1-100,100) for x in range(x0+100,x1-150,100)
                 if (x//CELL,z//CELL) in comp and _clear(blocked,x+25,z,100,60)]
        # Prefer the already source-levelled deployment marker neighbourhood.
        preferred=(1420,4300) if side=='red' else (10520,8100)
        options.sort(key=lambda p:((p[0]-preferred[0])**2+(p[1]-preferred[1])**2,p))
        chosen=[]
        for kind in ('recon','ground','support'):
            found=None
            for x,z in options:
                if any((x-a)**2+(z-b)**2<250**2 for a,b in chosen):continue
                pos=_mm(catalog,GROUND,x,z); anchor=_mm(catalog,GROUND,x+50,z)
                camp=dict(camp_id=barracks_id(side,kind),side=side,unit_type=kind,position_mm=pos,
                          surface_id=GROUND,service_anchor_mm=anchor,service_surface_id=GROUND)
                facility=dict(camp_id=camp['camp_id'],camp_position_mm=pos,surface_id=GROUND,
                    service_surface_id=GROUND,service_anchor_mm=anchor,entrance_mm=_mm(catalog,GROUND,x+40,z),
                    dock_surface_id=GROUND,dock_position_mm=_mm(catalog,GROUND,x+70,z),
                    paths=[[[x+40,z],[x+50,z]],[[x+40,z],[x+70,z]]])
                try:
                    checker.camp(camp)
                    # Full same-layer real service and dock sweeps, no unverified air flag.
                    from backend.battle_map_motion import sweep
                    catalog.footprint(GROUND,sweep((x,z),(x+50,z),4),side=side,purpose='service',kind='support')
                    for endpoint in (x+50,x+70):
                        catalog.footprint(GROUND,sweep((x+40,z),(endpoint,z),4),side=side,purpose='move',kind='support')
                    catalog.footprint(GROUND,rectangle(x+70,z,3),side=side,purpose='dock',kind='recon')
                    initial=dict(unit_id=f'{side}-{kind}-1',side=side,unit_type=kind,ordinal=1,
                        position_mm=deepcopy(facility['dock_position_mm'] if kind=='recon' else anchor),
                        surface_id=GROUND,air_state='docked' if kind=='recon' else 'ground')
                    checker.unit(initial)
                except ServiceError:
                    continue
                found=(camp,facility,initial);chosen.append((x,z));break
            require(found is not None,'BATTLE_DEPLOYMENT_INVALID','实际完整来源域中找不到合法六营布局')
            camp,facility,initial=found;camps.append(camp);facilities.append(facility);units.append(initial)
            configs[side]['camps'].append(dict(unit_type=kind,position=camp['position_mm'],surface_id=GROUND,
                service_anchor=camp['service_anchor_mm'],service_surface_id=GROUND))
            configs[side]['units'].append(dict(unit_type=kind,ordinal=1,position=initial['position_mm'],surface_id=GROUND))
            selections.append(dict(camp_id=camp['camp_id'],source_clear_component_cell_count=len(comp),
                                   reachable_target_m=list(targets[side])))
    return configs,camps,facilities,units,selections


def build_local_surfaces(assets):
    value=build_catalog(assets)
    blocked,inventory=clear_cells(assets)
    domains=merge_cells(blocked)
    faces={p['part_id']:list(range(assets.face_count(p['part_id']))) for p in assets.manifest['parts']
           if p['action']=='classify_base_ground' and p['source_layer']=='terrain'}
    ground=_layer(GROUND,faces,domains)
    bridge_bindings=build_bridge_face_bindings(assets)
    bridge_faces={}
    for binding in bridge_bindings['bindings']:
        for part,indices in binding['support_face_candidates'].items():bridge_faces.setdefault(part,set()).update(indices)
    ground['road_faces']={p['part_id']:[i for i in range(assets.face_count(p['part_id']))
                                      if i not in bridge_faces.get(p['part_id'],set())]
                         for p in assets.manifest['parts']
                         if (p['source_layer'],p['source_material'])==('routes','Asphalt')}
    ground['road_faces']={p:ids for p,ids in ground['road_faces'].items() if ids}
    bridge=next(b for b in bridge_bindings['bindings'] if b['source_road_id']=='ROAD_010')
    # Source deck full width32; exclude rails at +/-16.5, keep a4m edge margin.
    bridge_domain=polygon(5813,5788,6512,5812)
    deck=_layer(BRIDGE,bridge['support_face_candidates'],[bridge_domain],surface='bridge',
                navigation='bridge-deck:bridge:ROAD_010')
    value['layers']=[ground,deck]
    value['release_scope']='local_manual_test'
    value['capture_masks']=[]
    temp=SurfaceCatalog(assets,seal_catalog(value))
    for source in value['source_facts']['capture_points']:
        if source['support']=='bridge_deck':
            x,z,surface=6163,5800,BRIDGE
        else:
            cx,cz=round(q(source['x'])),round(q(source['z']));surface=GROUND
            options=[(x,z) for z in range(cz-150,cz+151,25) for x in range(cx-150,cx+151,25)
                     if _clear(blocked,x,z,5,5)]
            options.sort(key=lambda p:((p[0]-cx)**2+(p[1]-cz)**2,p))
            require(options,'BATTLE_CAPTURE_SURFACE_UNRESOLVED','来源圈内没有完整清晰的真实占点面')
            x,z=options[0]
        mask=_wire(rectangle(x,z,5))
        for side in DEPLOYMENT:temp.footprint(surface,mask,side=side,purpose='capture')
        value['capture_masks'].append(dict(point_id=source['point_id'],surface_id=surface,accepted=True,
            acceptance_scope='real_mask_support_and_permission_only_not_route_or_formal_scene_approval',
            source_navigation_layer_id=source['navigation_layer_id'],polygons=[mask]))
    configs,camps,facilities,units,selections=_select_layout(temp,blocked)
    value['camp_facilities']=facilities
    value['source_audit']=dict(schema_version='qinglan-local-surface-source-audit-v1',
        generator_sha256=sha256(GENERATOR.read_bytes()).hexdigest(),
        classification='approved_base_plus_exact_routes_Asphalt_projection_only',
        excluded_unknown_policy='all_nonbase_nonc_osmetic_original_triangle_closed_AABB_contact_cells_deleted',
        inventory=inventory,cell_size_m=CELL,blocked_cell_count=sum(map(sum,blocked)),
        clear_cell_count=COUNT*COUNT-sum(map(sum,blocked)),verified_domain_count=len(domains),
        closed_cell_mask_sha256=sha256(b''.join(blocked)).hexdigest(),
        grid_evidence='build_strategic_map.py:grid collection hide_render=True hide_viewport=True',
        road_evidence=dict(generator_functions=['road','ribbon','height'],
            asphalt_visual_height_formula='max(20,height(x,z)) + 0.32; bridge fixed_height=22',
            gameplay_support='original terrain GroundLow/GroundHigh triangles, not raised visual ribbon',
            regions='exact original nonbridge routes/Asphalt triangle xz projections via road_faces',
            bridge_faces_excluded=sum(len(ids) for ids in bridge_faces.values()),
            road_face_count=sum(map(len,ground['road_faces'].values())),
            no_vertical_snap_or_synthetic_connector=True),
        bridge_binding_sha256=bridge_bindings['payload_sha256'],
        blocked_connections=[dict(road_id=b['source_road_id'],reason='unproven_or_discontinuous_real_bridgehead',accepted=False)
                             for b in bridge_bindings['bindings']],
        facility_ground_proof=True,facility_airspace_proof='requires separately bound interaction audit',
        capture_proof='full original support, closed source-circle containment, both-side explicit permission',
        layout_selection=selections)
    value['acceptance'].update(production_ready=False,local_manual_test_ready=True,
        release_scope='local_manual_test',qualification='surface_component_only_requires_interaction_and_scenario_acceptance',
        missing=['formal_fairness_and_full_route_scene_acceptance','bridgehead_connections'])
    value=seal_catalog(value)
    final=SurfaceCatalog(assets,value)
    # All facility identities/paths validated through the same production component.
    checker=MapDeployment(final)
    for camp in camps:checker.camp(camp);checker._facility(camp)
    for unit in units:checker.unit(unit)
    layout=seal_catalog(dict(schema_version='qinglan-local-manual-deployment-v1',release_scope='local_manual_test',
        surface_catalog_sha256=final.digest,red=configs['red'],blue=configs['blue'],
        validation=dict(full_ground_facilities_and_initial_units=True,
            full_deployment_with_interaction='pending separately bound dock-airspace validation'),
        validation_state=dict(camps=camps,units=units)))
    return value,layout


def build_smoke_plan(catalog, layout):
    """Actual path proofs + finite-decimal legacy input; never round authority."""
    from decimal import Decimal
    from backend.battle_map_motion import sweep
    rounds=[{side:[] for side in DEPLOYMENT} for _ in range(5)]
    proof=[]
    for side in DEPLOYMENT:
        prefix='R' if side=='red' else 'B'
        for kind,number in (('ground',0),('support',3)):
            unit=next(u for u in layout[side]['units'] if u['unit_type']==kind)
            point=unit['position']; x,z=point['x_mm'],point['z_mm']
            end_x,end_z=x+20000,z-z%3
            # z scale150000 contains a factor3. Select an explicit nearby legal
            # target, not a silently rounded coordinate in the existing layout.
            start=(q(x)/1000,q(z)/1000);end=(q(end_x)/1000,q(end_z)/1000)
            evidence=catalog.footprint(unit['surface_id'],sweep(start,end,4 if kind=='support' else 3),
                side=side,purpose='move',kind=kind,direction=(start,end))
            position=dict(x=str(Decimal(end_x)/100000),y=str(Decimal(end_z)/150000))
            require(q(position['x'])*100000==end_x and q(position['y'])*150000==end_z,
                    'BATTLE_POSITION_PRECISION','计划业务坐标必须严格对应整数毫米')
            uid=prefix+('-G01' if kind=='ground' else '-S01')
            rounds[number][side].append(dict(unit_id=uid,movement=dict(action='move',position=position)))
            rounds[number+1][side].append(dict(unit_id=uid,movement=dict(action='stop'),task=dict(action='barracks',
                camp_id=prefix+('-CAMP-infantry' if kind=='ground' else '-CAMP-support'),targets=dict(energy='100'))))
            proof.append(dict(side=side,unit_id=uid,start_mm=[x,z],end_mm=[end_x,end_z],
                continuous_support=evidence.continuous_support,max_grade_squared=str(evidence.max_grade_squared),
                original_faces=[list(v) for v in evidence.face_ids]))
        rounds[0][side].append(dict(unit_id=prefix+'-U01',task=dict(action='takeoff')))
        rounds[1][side].append(dict(unit_id=prefix+'-U01',task=dict(action='stop')))
        rounds[2][side].append(dict(unit_id=prefix+'-U01',task=dict(action='land')))
    plan=dict(rounds=rounds)
    return plan,seal_catalog(dict(schema_version='qinglan-local-manual-plan-proof-v2',
        surface_catalog_sha256=catalog.digest,plan_sha256=payload_hash(plan),ground_moves=proof,
        scope='ground_full_sweeps_only_requires_isolated_execution_airspace_and_resource_validation'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog-output',type=Path,required=True)
    parser.add_argument('--deployment-output',type=Path,required=True)
    args=parser.parse_args()
    require(not args.catalog_output.exists() and not args.deployment_output.exists(),
            'BATTLE_TERRAIN_OUTPUT_EXISTS','输出已存在，拒绝覆盖')
    value,layout=build_local_surfaces(TerrainAssets.current())
    for target,content in ((args.catalog_output,value),(args.deployment_output,layout)):
        with target.open('x') as stream:json.dump(content,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(catalog_sha256=value['payload_sha256'],layout_sha256=layout['payload_sha256'],
        domains=len(value['layers'][0]['verified_domains']),camps=len(value['camp_facilities'])),ensure_ascii=False))

if __name__=='__main__':main()
