"""Source-proven local movement/engagement routes; no scenario or DB writes."""
import argparse
from collections import deque
from fractions import Fraction as F
import json
from pathlib import Path

from backend.battle_map_surface_build import GROUND, CELL, COUNT, clear_cells
from backend.battle_map_surfaces import SurfaceCatalog, rectangle, seal_catalog
from backend.battle_map_motion import sweep
from backend.battle_terrain_assets import TerrainAssets
from backend.battle_occlusion import RayIndex
from backend.demo_domain import require, ServiceError
from backend.runtime import sha256


def grade_cells(assets, blocked, *, limit=F(35,100)):
    """Conservatively reject cells exceeding true longitudinal/cross slopes on cardinal legs.

    Each final route sweep is independently checked, including non-cardinal
    endpoint connectors. This is not permission for arbitrary heading or stops."""
    result=[bytearray(row) for row in blocked]
    numerator,denominator=limit.numerator**2,limit.denominator**2
    for p in assets.manifest['parts']:
        if p['source_layer']!='terrain' or p['action']!='classify_base_ground':continue
        vertices,indices=assets._mesh(p['part_id']);mm=[round(v*1000) for v in vertices]
        for i in range(0,len(indices),3):
            a,b,c=[mm[n*3:n*3+3] for n in indices[i:i+3]]
            ux,uy,uz=[b[j]-a[j] for j in range(3)]
            vx,vy,vz=[c[j]-a[j] for j in range(3)]
            det=ux*vz-vx*uz
            if denominator*max((uy*vz-vy*uz)**2,(ux*vy-vx*uy)**2) <= numerator*det**2:
                continue
            x,z=min(a[0],b[0],c[0])//(CELL*1000),min(a[2],b[2],c[2])//(CELL*1000)
            if 0<=x<COUNT and 0<=z<COUNT:result[z][x]=1
    return result


def directional_cells(assets, blocked, *, limit=F(35,100)):
    """Two exact heading masks, not a relaxed slope or ground permission."""
    cardinal=[bytearray(row) for row in blocked]
    diagonal=[bytearray(row) for row in blocked]
    numerator,denominator=limit.numerator**2,limit.denominator**2
    for part in assets.manifest['parts']:
        if part['source_layer']!='terrain' or part['action']!='classify_base_ground':continue
        vertices,indices=assets._mesh(part['part_id']);mm=[round(v*1000) for v in vertices]
        for i in range(0,len(indices),3):
            a,b,c=[mm[n*3:n*3+3] for n in indices[i:i+3]]
            ux,uy,uz=[b[j]-a[j] for j in range(3)]
            vx,vy,vz=[c[j]-a[j] for j in range(3)]
            det=ux*vz-vx*uz;gx=uy*vz-vy*uz;gz=ux*vy-vx*uy
            x,z=min(a[0],b[0],c[0])//(CELL*1000),min(a[2],b[2],c[2])//(CELL*1000)
            if not (0<=x<COUNT and 0<=z<COUNT):continue
            if denominator*max(gx*gx,gz*gz)>numerator*det*det:cardinal[z][x]=1
            if denominator*max((gx+gz)**2,(gx-gz)**2)>2*numerator*det*det:diagonal[z][x]=1
    return cardinal,diagonal


def directional_parents(cardinal,diagonal,start):
    """Only edges whose entire closed swept grid corridor passes its heading."""
    queue=deque([start]);previous={start:None}
    while queue:
        x,z=queue.popleft()
        for dx,dz in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            xx,zz=x+dx,z+dz
            if not (0<=xx<COUNT and 0<=zz<COUNT) or (xx,zz) in previous:continue
            grid=diagonal if dx and dz else cardinal
            if any(grid[j][i] for j in set((z,zz)) for i in set((x,xx))):continue
            previous[(xx,zz)]=(x,z);queue.append((xx,zz))
    return previous


def parents(blocked,start):
    x,z=start
    require(not blocked[z][x],'BATTLE_ROUTE_FORBIDDEN','路线起点不是保守完整占地格')
    queue=deque([start]);previous={start:None}
    while queue:
        x,z=queue.popleft()
        for n in ((x+1,z),(x-1,z),(x,z+1),(x,z-1)):
            xx,zz=n
            if 0<=xx<COUNT and 0<=zz<COUNT and not blocked[zz][xx] and n not in previous:
                previous[n]=(x,z);queue.append(n)
    return previous


def points_for(previous,destination,start,end,*,cell=CELL):
    require(destination in previous,'BATTLE_ROUTE_FORBIDDEN','实际来源完整占地格不连通')
    cells=[];current=destination
    while current is not None:cells.append(current);current=previous[current]
    points=[tuple(start)]+[(x*cell+cell//2,z*cell+cell//2) for x,z in reversed(cells)]+[tuple(end)]
    simple=[]
    for point in points:
        if simple and point==simple[-1]:continue
        while len(simple)>=2:
            a,b=simple[-2:];u=(b[0]-a[0],b[1]-a[1]);v=(point[0]-b[0],point[1]-b[1])
            if u[0]*v[1]!=u[1]*v[0] or u[0]*v[0]+u[1]*v[1]<=0:break
            simple.pop()
        simple.append(point)
    # Small exact legs remain below the existing per-leg motion event budget.
    segmented=[simple[0]]
    for a,b in zip(simple,simple[1:]):
        n=max(1,(max(abs(b[j]-a[j]) for j in (0,1))+99)//100)
        for i in range(1,n+1):segmented.append(tuple(F(a[j])+F(i,n)*(b[j]-a[j]) for j in (0,1)))
    return segmented


def prove_route(catalog,side,previous,start,end,*,cell=CELL,stop_safe=False):
    points=points_for(previous,tuple(int(v//cell) for v in end),start,end,cell=cell)
    refs=set();grades=[]
    for a,b in zip(points,points[1:]):
        evidence=catalog.footprint(GROUND,sweep(a,b,3),side=side,purpose='move',kind='ground',direction=(a,b))
        require(evidence.continuous_support,'BATTLE_SUPPORT_INVALID','路线完整占地不连续')
        if stop_safe:
            require(evidence.max_grade_squared<=F(35,100)**2,'BATTLE_SLOPE_INVALID','完整扫掠含不可停止的原始坡面')
        refs.update(evidence.face_ids);grades.append(evidence.max_grade_squared)
    return dict(side=side,surface_id=GROUND,unit_type='ground',radius_m=3,
        waypoints_m=[[str(v) for v in p] for p in points],
        original_support_faces=[list(v) for v in sorted(refs)],
        max_grade_squared=str(max(grades)),full_sweeps_verified=True,
        route_sha256=sha256([[str(v) for v in p] for p in points]))


def build_routes(catalog,layout):
    blocked,_=clear_cells(catalog.assets)
    # Camps are actual runtime static solids. Exclude closed contact cells with
    # the complete ground radius, not just the terrain's geometry inventory.
    for camp in layout['validation_state']['camps']:
        x,z=camp['position_mm']['x_mm'],camp['position_mm']['z_mm']
        for zz in range(max(0,(z-33001)//50000),min(COUNT-1,(z+33000)//50000)+1):
            for xx in range(max(0,(x-33001)//50000),min(COUNT-1,(x+33000)//50000)+1):blocked[zz][xx]=1
    starts={side:next(u['position'] for u in layout[side]['units'] if u['unit_type']=='ground') for side in ('red','blue')}
    starts={side:(F(p['x_mm'],1000),F(p['z_mm'],1000)) for side,p in starts.items()}
    cardinal,diagonal=directional_cells(catalog.assets,blocked)
    trees={side:directional_parents(cardinal,diagonal,tuple(int(v//CELL) for v in p)) for side,p in starts.items()}
    captures=[]
    for mask in catalog.payload['capture_masks']:
        if mask['surface_id']!=GROUND:continue
        poly=mask['polygons'][0];end=(sum(F(str(p[0])) for p in poly)/len(poly),sum(F(str(p[1])) for p in poly)/len(poly))
        for side in ('red','blue'):
            if tuple(int(v//CELL) for v in end) in trees[side]:
                route=prove_route(catalog,side,trees[side],starts[side],end)
                captures.append(dict(point_id=mask['point_id'],route=route))
    candidates=[]
    for x,z in trees['red']:
        for delta in range(1,13):
            if (x+delta,z) in trees['blue']:
                candidates.append((delta,(x*CELL+25,z*CELL+25),((x+delta)*CELL+25,z*CELL+25)))
    candidates.sort()
    rayindex=RayIndex(catalog.assets,blocker_parts=list(catalog.assets.part_ids()))
    engagement=None;rejections=[]
    for distance,a,b in candidates:
        try:
            ea=catalog.footprint(GROUND,rectangle(*a,3),side='red',purpose='stop',kind='ground')
            eb=catalog.footprint(GROUND,rectangle(*b,3),side='blue',purpose='stop',kind='ground')
            anchors=[(F(x),catalog.height(GROUND,(x,z))+2,F(z)) for x,z in (a,b)]
            ray=rayindex.cast(*anchors)
            if not ray.mesh_clear:
                rejections.append(dict(red=list(a),blue=list(b),reason='original_mesh_ray_blocked',ray=ray.public()));continue
            red=prove_route(catalog,'red',trees['red'],starts['red'],a)
            blue=prove_route(catalog,'blue',trees['blue'],starts['blue'],b)
            engagement=dict(red_point_m=list(a),blue_point_m=list(b),distance_m=distance*CELL,
                rule_anchors_xyz_m=[[str(v) for v in p] for p in anchors],ray=ray.public(),routes=[red,blue],
                permission_status='requires separately verified interaction water ray-domain cover')
            break
        except ServiceError as error:
            rejections.append(dict(red=list(a),blue=list(b),reason=error.code))
    return seal_catalog(dict(schema_version='qinglan-local-real-route-evidence-v1',
        surface_catalog_sha256=catalog.digest,layout_sha256=layout['payload_sha256'],
        component_cell_counts={side:len(t) for side,t in trees.items()},
        captures=captures,engagement=engagement,engagement_rejections=rejections,
        production_ready=False,no_new_ground_permission_or_bridge_connection=True))


def _finite(value):
    """Serialize only provably finite rationals, without a rounding context."""
    value=F(value);den=value.denominator;twos=fives=0
    while den%2==0:den//=2;twos+=1
    while den%5==0:den//=5;fives+=1
    require(den==1,'BATTLE_ROUTE_INVALID','API目标坐标不是有限十进制')
    digits=max(twos,fives);scaled=value.numerator*(10**digits//value.denominator)
    sign='-' if scaled<0 else '';text=str(abs(scaled)).zfill(digits+1)
    return sign+(text[:-digits]+'.'+text[-digits:] if digits else text)


def api_route(catalog,route):
    """Choose NEW explicit integer-mm API targets, then re-prove every sweep.

    These are not rounded authoritative unit positions. Original route evidence
    remains immutable, and each changed planned target is independently checked.
    """
    points=[tuple(F(v) for v in p) for p in route['waypoints_m']]
    targets=[points[0]]
    for x,z in points[1:]:
        x_mm=round(x*1000);z_mm=3*round(z*1000/3)
        targets.append((F(x_mm,1000),F(z_mm,1000)))
    rows=[];faces=set()
    for a,b in zip(targets,targets[1:]):
        if a==b:continue
        proof=catalog.footprint(GROUND,sweep(a,b,3),side=route['side'],purpose='move',kind='ground',direction=(a,b))
        faces.update(proof.face_ids)
        if route.get('full_sweep_stop_grade_verified'):
            require(proof.max_grade_squared<=F(35,100)**2,'BATTLE_SLOPE_INVALID','显式新目标完整扫掠含不可停止坡面')
        try:
            catalog.footprint(GROUND,rectangle(*b,3),side=route['side'],purpose='stop',kind='ground')
            stop=True
        except ServiceError:stop=False
        rows.append(dict(position_mm=dict(x_mm=int(b[0]*1000),z_mm=int(b[1]*1000)),
            position=dict(x=_finite(b[0]/100),y=_finite(b[1]/150)),stop_footprint_verified=stop))
    batches=[];offset=0
    while offset<len(rows):
        last=min(len(rows),offset+8)
        # Prefer a fully verified stop as each operator handoff target.
        while last>offset and not rows[last-1]['stop_footprint_verified']:last-=1
        require(last>offset,'BATTLE_ROUTE_INVALID','八目标范围内没有可核验停止点')
        group=rows[offset:last]
        batches.append(dict(batch_number=len(batches)+1,target_indices=[offset,last-1],
            intent=dict(unit_id=('R-G01' if route['side']=='red' else 'B-G01'),
                movement=dict(action='move',waypoints=[p['position'] for p in group[:-1]],position=group[-1]['position'])),
            continue_rule='Keep this frozen intent; submit empty drafts until authoritative position_mm equals final target, then issue next batch.',
            final_target_mm=group[-1]['position_mm']))
        offset=last
    return dict(side=route['side'],surface_id=GROUND,source_route_sha256=route['route_sha256'],
        start_m=[str(v) for v in targets[0]],targets=rows,batches=batches,
        full_changed_target_sweeps_verified=True,original_support_face_count=len(faces),
        runtime_round_execution_verified=False)


def build_api_routes(catalog,evidence):
    require(evidence.get('surface_catalog_sha256')==catalog.digest and
            seal_catalog(evidence)['payload_sha256']==evidence.get('payload_sha256'),
            'BATTLE_ROUTE_INVALID','路线证据摘要或地表绑定失效')
    captures=[dict(point_id=item['point_id'],route=api_route(catalog,item['route'])) for item in evidence['captures']]
    engagement=evidence.get('engagement');result=None
    if engagement:
        routes=[api_route(catalog,r) for r in engagement['routes']]
        points=[r['targets'][-1]['position_mm'] for r in routes]
        anchors=[(F(p['x_mm'],1000),catalog.height(GROUND,(F(p['x_mm'],1000),F(p['z_mm'],1000)))+2,F(p['z_mm'],1000)) for p in points]
        ray=RayIndex(catalog.assets,blocker_parts=list(catalog.assets.part_ids())).cast(*anchors)
        require(ray.mesh_clear,'BATTLE_ROUTE_INVALID','新显式API目标的真实射线受阻')
        result=dict(routes=routes,rule_anchors_xyz_m=[[str(v) for v in p] for p in anchors],ray=ray.public(),
            interaction_permission_verified=False)
    return seal_catalog(dict(schema_version='qinglan-local-api-route-instructions-v1',
        source_route_evidence_sha256=evidence['payload_sha256'],surface_catalog_sha256=catalog.digest,
        captures=captures,engagement=result,production_ready=False,
        target_selection='Explicit NEW plan targets, never authority rounding; integer x mm and z mm divisible by 3; each actual sweep re-proven.',
        operator_rule='Only issue next batch after exact final target_mm reached. Empty drafts retain movement. This is route evidence, not round/energy/interaction acceptance.'))


def integer_triangle_box_touch(triangle,x0,z0,x1,z1):
    """Closed convex SAT, exact integer source mm; contact remains forbidden."""
    box=((x0,z0),(x1,z0),(x1,z1),(x0,z1))
    axes=[(1,0),(0,1)]+[(b[1]-a[1],a[0]-b[0])
        for a,b in zip(triangle,triangle[1:]+triangle[:1])]
    for dx,dz in axes:
        tri=[dx*x+dz*z for x,z in triangle];rect=[dx*x+dz*z for x,z in box]
        if max(tri)<min(rect) or max(rect)<min(tri):return False
    return True


def stop_safe_cells(assets,coarse,camps,*,cell=10):
    """Conservative exact triangle exclusions inside already-permitted domains.

    A fine cell's entire closed square plus 3m is excluded on contact with ANY
    real terrain face whose maximum local grade exceeds the original 35% limit.
    This only restricts a planner, never alters the surface or permission.
    """
    require(type(cell) is int and cell>=5 and CELL%cell==0,
            'BATTLE_ROUTE_INVALID','细格须整除来源50m格且不小于5m')
    count=12000//cell;ratio=CELL//cell;step=cell*1000
    grid=[bytearray(v for v in coarse[z//ratio] for _ in range(ratio)) for z in range(count)]
    for camp in camps:
        x,z=camp['position_mm']['x_mm'],camp['position_mm']['z_mm']
        for zz in range(max(0,(z-33001)//step),min(count-1,(z+33000)//step)+1):
            for xx in range(max(0,(x-33001)//step),min(count-1,(x+33000)//step)+1):grid[zz][xx]=1
    for part in assets.manifest['parts']:
        if part['action']!='classify_base_ground':continue
        vertices,indices=assets._mesh(part['part_id']);mm=[round(v*1000) for v in vertices]
        for i in range(0,len(indices),3):
            a,b,c=[mm[n*3:n*3+3] for n in indices[i:i+3]]
            ux,uy,uz=[b[j]-a[j] for j in range(3)];vx,vy,vz=[c[j]-a[j] for j in range(3)]
            det=ux*vz-vx*uz;gx=uy*vz-vy*uz;gz=ux*vy-vx*uy
            if 400*(gx*gx+gz*gz)<=49*det*det:continue
            tri=tuple((v[0],v[2]) for v in (a,b,c))
            for zz in range(max(0,(min(v[1] for v in tri)-3001)//step),min(count-1,(max(v[1] for v in tri)+3000)//step)+1):
                for xx in range(max(0,(min(v[0] for v in tri)-3001)//step),min(count-1,(max(v[0] for v in tri)+3000)//step)+1):
                    if not grid[zz][xx] and integer_triangle_box_touch(tri,xx*step-3000,zz*step-3000,(xx+1)*step+3000,(zz+1)*step+3000):grid[zz][xx]=1
    return grid


def build_stop_safe_deployment_route(catalog,layout):
    """Explicit NEW blue initial ground deployment, frozen camps/geometry intact."""
    from copy import deepcopy
    from backend.battle_map_deployment import MapDeployment
    from backend.battle_map_surfaces import DEPLOYMENT
    require(layout.get('surface_catalog_sha256')==catalog.digest and
            seal_catalog(layout)['payload_sha256']==layout.get('payload_sha256'),
            'BATTLE_ROUTE_INVALID','布局摘要或地表绑定失效')
    layout=deepcopy(layout);cell=10;coarse,_=clear_cells(catalog.assets)
    grid=stop_safe_cells(catalog.assets,coarse,layout['validation_state']['camps'],cell=cell)
    mask=next(m for m in catalog.payload['capture_masks'] if m['point_id']=='P-03')
    poly=mask['polygons'][0]
    goal=tuple(sum(F(str(p[j])) for p in poly)/len(poly) for j in (0,1))
    root=tuple(int(v//cell) for v in goal);previous={root:None};queue=deque([root]);selected=None
    count=len(grid);x0,z0,x1,z1=DEPLOYMENT['blue']
    require(not grid[root[1]][root[0]],'BATTLE_ROUTE_INVALID','P03目标细格未通过完整stop-safe证明')
    while queue:
        x,z=queue.popleft();center=(x*cell+cell//2,z*cell+cell//2)
        if x0+3<=center[0]<=x1-3 and z0+3<=center[1]<=z1-3:
            candidate=next(deepcopy(u) for u in layout['validation_state']['units'] if u['side']=='blue' and u['unit_type']=='ground')
            position=dict(x_mm=center[0]*1000,z_mm=center[1]*1000,height_mm=round(catalog.height(GROUND,center)*1000))
            candidate['position_mm']=position
            try:MapDeployment(catalog).unit(candidate)
            except ServiceError:pass
            else:selected=(x,z);break
        for dx,dz in ((-1,0),(0,1),(0,-1),(1,0),(1,1),(-1,1),(1,-1),(-1,-1)):
            xx,zz=x+dx,z+dz
            if not(0<=xx<count and 0<=zz<count) or (xx,zz) in previous:continue
            if any(grid[j][i] for j in set((z,zz)) for i in set((x,xx))):continue
            previous[(xx,zz)]=(x,z);queue.append((xx,zz))
    require(selected is not None,'BATTLE_ROUTE_INVALID','原蓝部署区内无stop-safe连通初始位置')
    chain=[];current=selected
    while current is not None:chain.append(current);current=previous[current]
    reroot={chain[0]:None}
    for a,b in zip(chain,chain[1:]):reroot[b]=a
    start=(selected[0]*cell+cell//2,selected[1]*cell+cell//2)
    route=prove_route(catalog,'blue',reroot,start,goal,cell=cell,stop_safe=True)
    route['full_sweep_stop_grade_verified']=True
    api=api_route(catalog,route)
    unit=next(u for u in layout['validation_state']['units'] if u['side']=='blue' and u['unit_type']=='ground')
    unit['position_mm']=position
    next(u for u in layout['blue']['units'] if u['unit_type']=='ground')['position']=position
    layout['previous_layout_sha256']=layout['payload_sha256']
    layout['selection_note']='Explicit NEW-game blue ground deployment selected for a strict stop-safe route to P03; existing games, all camps, map and surface catalog unchanged.'
    layout=seal_catalog(layout)
    evidence=seal_catalog(dict(schema_version='qinglan-local-stop-safe-route-v2',
        surface_catalog_sha256=catalog.digest,layout_sha256=layout['payload_sha256'],
        route=route,api_route=api,production_ready=False,actual_motion_verified=False))
    return layout,evidence


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--surface',type=Path,required=True)
    parser.add_argument('--layout',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--route-evidence',type=Path)
    parser.add_argument('--stop-safe-deployment-output',type=Path)
    args=parser.parse_args()
    require(not args.output.exists(),'BATTLE_TERRAIN_OUTPUT_EXISTS','证据输出已存在')
    catalog=SurfaceCatalog(TerrainAssets.current(),json.loads(args.surface.read_bytes()))
    if args.stop_safe_deployment_output:
        require(not args.stop_safe_deployment_output.exists(),'BATTLE_TERRAIN_OUTPUT_EXISTS','新布局输出已存在')
        layout,result=build_stop_safe_deployment_route(catalog,json.loads(args.layout.read_bytes()))
        with args.stop_safe_deployment_output.open('x') as out:json.dump(layout,out,ensure_ascii=False,indent=2);out.write('\n')
    else:
        result=(build_api_routes(catalog,json.loads(args.route_evidence.read_bytes())) if args.route_evidence
            else build_routes(catalog,json.loads(args.layout.read_bytes())))
    with args.output.open('x') as out:json.dump(result,out,ensure_ascii=False,indent=2);out.write('\n')
    print(json.dumps(dict(captures=len(result.get('captures',[])),engagement=bool(result.get('engagement')),sha256=result['payload_sha256'])))

if __name__=='__main__':main()
