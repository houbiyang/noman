"""Independent real frozen-map component evidence; no PG writes or synthetic assets.

Controlled positions are NOT paths from the recommended HTTP deployment. Each passed
case is emitted only after its actual assertions; failures are retained separately.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import battle_kernel as k, battle_setup, battle_scenario
from backend.battle_execution import Executor
from backend.battle_map_geometry import load_acceptance_geometry
from backend.runtime import ServiceError


def need(value, label):
    if not value:
        raise AssertionError(label)


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+'\n')


def rejected(callback):
    try:
        callback()
    except ServiceError as error:
        return error.code
    raise AssertionError('invalid operation unexpectedly accepted')


def pick(state, kind, side='blue'):
    return next(u for u in state['units'] if u['unit_type']==kind and u['side']==side)


def place(g, unit, x, z, surface=None):
    unit.pop('movement_progress', None)
    unit.pop('geometry_position', None)
    unit['movement'] = unit['task'] = None
    unit['surface_id'] = surface or unit['surface_id']
    height = g.catalog.height(unit['surface_id'], (F(x), F(z)))
    need((F(x)*1000).denominator == (F(z)*1000).denominator == 1, 'exact millimeters')
    unit['position_mm'] = dict(x_mm=int(F(x)*1000), z_mm=int(F(z)*1000), height_mm=round(height*1000))


def service(g, state, source, target, resource='energy', quantity='3'):
    request=dict(source_id=source['unit_id'], source_life_id=source['life_id'],
                 target_id=target['unit_id'], target_life_id=target['life_id'], resource=resource, quantity=quantity)
    return k.service_step(state, [request], seconds=5,
        stationary={u['unit_id'] for u in state['units']},
        start_positive={u['unit_id'] for u in state['units'] if F(u['energy'])>0},
        service_clear=g.service_clear, hover_energy_clear=g.hover_energy_clear)


def deployment(g, state):
    need(len(state['units'])==6 and len(state['camps'])==6, '6 units + 6 camps')
    need(g.validate_deployment(state) is True, 'all complete deployment footprints')
    bad=deepcopy(state); bad['units'][0]['position_mm']['height_mm']+=1
    height=rejected(lambda:g.validate_deployment(bad))
    bad=deepcopy(state); bad['units'][0]['surface_id']='undeclared:never-authorized'
    unknown=rejected(lambda:g.validate_deployment(bad))
    return dict(full_roster_and_camps=True, full_footprints=True, wrong_height_rejected=True, unknown_surface_rejected=True), dict(
        units=state['units'], camps=state['camps'], wrong_height_code=height, unknown_surface_code=unknown)


def rays(g, state):
    s=deepcopy(state); red=pick(s,'ground','red'); blue=pick(s,'ground')
    # W1 original-ground candidates. Each is independently checked here, not trusted.
    place(g,red,6575,3875); place(g,blue,6975,3875)
    g.verify(s)
    a,b=g.terrain.rule_anchor(red),g.terrain.rule_anchor(blue)
    need(g.terrain.ray_domain_clear(red,blue), 'whole ray known domain')
    mesh=g.interactions.rays.cast(a,b)
    need(mesh.mesh_clear, 'actual source mesh clear')
    visible=g.observe(s,start_positive={u['unit_id'] for u in s['units']},stable_observers={red['unit_id'],blue['unit_id']})
    need(blue['unit_id'] in visible['red'] and red['unit_id'] in visible['blue'], 'actual observers')
    need(g.fire_clear(red,blue) and g.fire_clear(blue,red), 'separate shooter rays')
    red['task']=dict(action='attack',target_id=blue['unit_id'],target_life_id=blue['life_id'])
    before=deepcopy(s)
    after,shots=k.resolve_fire(s,seconds=5,start_positive={red['unit_id']},moved_energy={},observed=visible,receivers=set(),fire_clear=g.fire_clear)
    r=pick(after,'ground','red'); b=pick(after,'ground')
    need(len(shots)==1 and F(r['ammo'])==F(red['ammo'])-1 and F(b['durability'])==F(blue['durability'])-16,'actual damage and ammunition')
    need(s==before,'pure source state')
    outside=deepcopy(blue); outside['position_mm'].update(x_mm=0,z_mm=0)
    need(not g.terrain.ray_domain_clear(red,outside) and not g.interactions.ray_clear(red,outside),'unknown domain rejected')
    return dict(real_mesh_ray=True,observer_and_shooter_separate=True,actual_shot_damage_and_ammo=True,unknown_domain_rejected=True),dict(
        controlled_positions_not_deployment_routes=True,source=red,target=blue,anchors=[a,b],mesh=mesh.public(),visible={s:sorted(v) for s,v in visible.items()},shots=shots,
        before_ammo=red['ammo'],after_ammo=r['ammo'],before_hp=blue['durability'],after_hp=b['durability'],unknown_domain_not_forest_acceptance=True)


def services(g,state):
    s=deepcopy(state); source=pick(s,'support'); target=pick(s,'ground')
    # Place receiver 20m along an independently checked original-road footprint.
    p=source['position_mm']; place(g,target,F(p['x_mm'],1000)+20,F(p['z_mm'],1000))
    g.verify(s)
    need(g.service_clear(source,target),'continuous service hull')
    camp=next(c for c in s['camps'] if c['side']=='blue' and c['unit_type']=='support')
    need(g.service_clear(camp,source),'real camp service anchor')
    k.consume_energy(s,target,F(10)); k.validate(s)
    before=deepcopy(s); after,events=service(g,s,source,target)
    delivered=sum(F(e['amount']) for e in events)
    a=pick(after,'support'); b=pick(after,'ground')
    need(delivered>0 and F(b['energy'])==F(target['energy'])+delivered and F(a['cargo']['energy'])==F(source['cargo']['energy'])-delivered,'actual service inventory')
    need(after['battle_ledger']==s['battle_ledger'] and s==before,'transfer conservation')
    far=deepcopy(target); far['position_mm']['x_mm']=p['x_mm']+100001
    need(not g.service_clear(source,far),'100m+1mm refuses')
    disconnected=deepcopy(target); disconnected['surface_id']='undeclared:other-layer'
    need(g.terrain.service_segment_clear(source,disconnected) is False,'different layer refused')
    code='BATTLE_SERVICE_DISCONNECTED returned false'
    return dict(continuous_surface_hull=True,real_camp_anchor=True,inventory_and_energy_transfer=True,out_of_range_rejected=True,unknown_layer_fail_closed=True),dict(
        controlled_positions_not_deployment_routes=True,source=source,target=target,camp=camp,deliveries=events,
        before_cargo=source['cargo'],after_cargo=a['cargo'],before_energy=target['energy'],after_energy=b['energy'],different_layer_code=code)


def airspace(g,state):
    s=deepcopy(state); recon=pick(s,'recon')
    need(g.flight_legal(recon,'takeoff')==(True,True),'real dock takeoff')
    recon['air_state']='flying'
    need(g.terrain.air_legal(recon) and g.landing_clear(recon,s),'air + empty landing')
    enemy=pick(s,'ground','red'); enemy['position_mm']=deepcopy(recon['position_mm']); enemy['surface_id']=recon['surface_id']
    need(not g.landing_clear(recon,s),'hostile same-layer conflict')
    outside=deepcopy(recon); outside['position_mm'].update(x_mm=0,z_mm=0)
    need(not g.terrain.air_legal(outside),'unknown airspace refusal')
    return dict(real_dock_takeoff=True,hover_allowed=True,empty_landing_allowed=True,hostile_same_layer_landing_refused=True,unknown_airspace_refused=True),dict(
        controlled_conflict_not_enemy_route=True,recon=recon,enemy=enemy,unknown_position=outside['position_mm'])


def hover(g,state):
    s=deepcopy(state); source=pick(s,'support'); target=pick(s,'recon')
    p=source['position_mm']; place(g,target,F(p['x_mm'],1000)+100,F(p['z_mm'],1000))
    target['air_state']='flying'
    g.verify(s)
    need(g.hover_energy_clear(source,target),'exact 100m real source and air')
    k.consume_energy(s,target,F(20)); k.validate(s)
    before=deepcopy(s); after,events=service(g,s,source,target)
    amount=sum(F(e['amount']) for e in events); a=pick(after,'support'); b=pick(after,'recon')
    need(amount>0 and F(b['energy'])==F(target['energy'])+amount and F(a['cargo']['energy'])==F(source['cargo']['energy'])-amount,'actual hover energy inventory')
    need(b['air_state']=='flying' and after['battle_ledger']==s['battle_ledger'] and s==before,'still airborne conservation')
    far=deepcopy(s); pick(far,'recon')['position_mm']['x_mm']+=1
    far_after,far_events=service(g,far,pick(far,'support'),pick(far,'recon'))
    need(not far_events and far_after==far,'100m+1mm no delivery')
    blocked={}
    need(not g.service_clear(source,target),'no general airborne ground service')
    blocked['ammo']=rejected(lambda:service(g,s,source,target,'ammo'))
    repair=deepcopy(s); pick(repair,'recon')['durability']=str(F(pick(repair,'recon')['durability'])-1)
    k.validate(repair)
    repair_after,ev=service(g,repair,pick(repair,'support'),pick(repair,'recon'),'repair')
    need(not ev and repair_after==repair,'damaged flying receiver cannot repair'); blocked['repair']=ev
    return dict(actual_hover_energy=True,exact_100m=True,beyond_100m_rejected=True,still_flying=True,stock_conserved=True,ammo_repair_not_authorized=True),dict(
        controlled_positions_not_deployment_routes=True,source=source,target=target,deliveries=events,amount=str(amount),
        before_cargo=source['cargo'],after_cargo=a['cargo'],before_energy=target['energy'],after_energy=b['energy'],out_of_range_deliveries=far_events,blocked=blocked,
        flight_maintenance_boundary='isolated 5-second service transfer; HTTP takeoff/hover/landing measured separately')


def ground_capture(g,state):
    from backend.battle_execution import Motion
    from backend.battle_map_motion import sweep
    plan_path=ROOT/'backend/battle-map-local-smoke-plan-v2.json'
    plan=json.loads(plan_path.read_bytes()); proof=[]
    for row in plan['rounds']:
        for side,items in row.items():
            for item in items:
                movement=item.get('movement',{})
                if movement.get('action')!='move': continue
                s=deepcopy(state); unit=next(u for u in s['units'] if u['unit_id']==item['unit_id'])
                target=g.position(movement['position']); a=g.terrain._point(unit)
                b=(F(target['x_mm'],1000),F(target['z_mm'],1000))
                radius=k.profiles()[unit['unit_type']]['ground_radius_m']+1
                footprint=g.catalog.footprint(unit['surface_id'],sweep(a,b,radius),side=side,purpose='move',kind=unit['unit_type'],direction=(a,b))
                motions=[]; costs=[]; initial_energy=F(unit['energy'])
                initial_consumed=F(s['battle_ledger'][side]['consumed']['energy'])
                for tick in range(24):
                    motion=g.motion.ground.plan(unit,[dict(surface_id=unit['surface_id'],end_m=b)])
                    cost=F(k.movement_cost(unit['unit_type'],motion.segments))
                    k.consume_energy(s,unit,cost); unit['position_mm']=deepcopy(motion.trace[-1]); unit['surface_id']=motion.surface_id
                    if motion.progress: unit['movement_progress']=deepcopy(motion.progress)
                    k.validate(s); g.verify(s)
                    motions.append(dict(trace=motion.trace,segments=motion.segments,finished=motion.finished)); costs.append(cost)
                    if motion.finished: break
                need(motion.finished and motion.trace[-1]['x_mm']==target['x_mm'] and motion.trace[-1]['z_mm']==target['z_mm'],'actual target reached exactly')
                cost=sum(costs,F(0)); need(cost>0,'real cumulative path billed')
                need(F(unit['energy'])==initial_energy-cost and F(s['battle_ledger'][side]['consumed']['energy'])==initial_consumed+cost,'exact debit and ledger')
                proof.append(dict(unit_id=unit['unit_id'],start_m=a,end_m=b,motions=motions,cost=str(cost),energy_before=str(initial_energy),energy_after=unit['energy'],full_sweep=footprint))
    need(len(proof)==4,'all four real smoke sweeps')
    # An independent, legally placed stationer: this is NOT proof of a bridgehead route.
    candidate=None; rejected_points=[]
    for point in g.terrain.capture_points:
        s=deepcopy(state); unit=pick(s,'ground'); mask=g.terrain.capture_masks[point['point_id']]
        try:
            place(g,unit,F(point['x']),F(point['z']),mask['surface_id'])
            if not g.terrain.capture_footprint_legal(unit,point):
                rejected_points.append(point['point_id']); continue
            g.verify(s); candidate=(s,unit,point); break
        except ServiceError as error:
            rejected_points.append(dict(point_id=point['point_id'],code=error.code))
    need(candidate is not None,'one actual capture footprint')
    s,unit,point=candidate; history=[]
    for step in range(6):
        motions={u['unit_id']:Motion(trace=(deepcopy(u['position_mm']),deepcopy(u['position_mm']))) for u in s['units']}
        presence,residents=g.capture(s,deepcopy(s),motions)
        need(unit['unit_id'] in presence[point['point_id']]['blue'] and unit['unit_id'] in residents[point['point_id']]['blue'],'real full footprint and residence')
        s,points=k.capture_step(s,s['battle_points'],presence=presence,residents=residents); s['battle_points']=points
        history.append(deepcopy(next(p for p in points if p['point_id']==point['point_id'])))
    need(history[-1]['owner']=='blue' and history[-1]['progress_steps']==0,'six actual steps capture')
    return dict(four_exact_full_sweeps=True,cumulative_path_energy_debited=True,conserved_energy_ledger=True,real_capture_presence_and_residence=True,six_step_capture_state=True),dict(
        smoke_plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),movements=proof,capture=dict(point=point,stationer=unit,history=history,other_centers_rejected=rejected_points),
        boundary='capture starts at independently verified point; no deployment-to-capture or bridgehead connectivity claim; HTTP smoke independently tests adopted moves')


CASES={'ground_sweeps_and_energy':ground_capture,'deployment_full_footprints':deployment,'observer_and_shooter_rays':rays,
       'service_continuous_surface':services,'airspace_and_landing_conflict':airspace,
       battle_scenario.HOVER_ENERGY_CASE:hover}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('surfaces','interactions','scenario','request','output'):
        p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    inputs={str(v):hashlib.sha256(v.read_bytes()).hexdigest() for v in (args.surfaces,args.interactions,args.scenario,args.request)}
    g=load_acceptance_geometry(args.surfaces,args.interactions,args.scenario)
    request=battle_setup.CreateManualBattle.model_validate_json(args.request.read_bytes())
    state=battle_setup.build(request,g.rules,g.scenario,g.capacity,g)
    Executor(g).prepare_new_game(state)
    summary=dict(source='real_map',geometry_binding=g.binding,passed=[],failed={},no_database_writes=True,formal_acceptance_claimed=False,input_sha256=inputs)
    for name,callback in CASES.items():
        try:
            assertions,observations=callback(g,state)
            need(assertions and all(v is True for v in assertions.values()),'all actual assertions')
            need(all(hashlib.sha256(Path(f).read_bytes()).hexdigest()==h for f,h in inputs.items()),'input drift')
            record=dict(case_id=name,passed=True,source='real_map',geometry_binding=g.binding,map_source_sha256=g.scenario['map']['source_sha256'],
                field_energy_service_policy=g.scenario.get('field_energy_service_policy'),assertions=assertions,observations=observations)
            dump(args.output/(name+'.json'),record); summary['passed'].append(name)
            print(json.dumps(dict(case_id=name,passed=True)),flush=True)
        except Exception as error:
            failure=dict(exception_type=type(error).__name__,code=getattr(error,'code',str(error) if isinstance(error,AssertionError) else 'COMPONENT_EXECUTION_FAILED'))
            summary['failed'][name]=failure; print(json.dumps(dict(case_id=name,passed=False,**failure)),flush=True)
    dump(args.output/'summary.json',summary)
    return bool(summary['failed'])


if __name__=='__main__':
    raise SystemExit(main())
