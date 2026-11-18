"""合成目录的组合/精确状态/门禁测试，不是青岚实地图验收，不连接数据库。"""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction as F
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from backend import battle_capacity, battle_scenario, rulebook, battle_kernel as k
from backend.battle_execution import Executor, Motion
from backend.battle_map_geometry import (CatalogInteractions, MapGeometry, LocalManualMapGeometry, MapMotionCoordinator,
    seal_interactions, source_readiness, load_server_geometry)
from backend.battle_map_interactions import Interactions, ForestIndex
from backend.battle_map_surfaces import SurfaceCatalog, rectangle, seal_catalog
from backend.runtime import ServiceError
from backend.tests.test_battle_map_components import catalog, unit
from backend.tests.test_battle_execution import FixtureGeometry, state_fixture, attack
from backend.tests.test_battle_kernel import unit as state_unit


def encoded(value):
    return json.loads(json.dumps(value, default=lambda v: str(Decimal(v.numerator)/Decimal(v.denominator)) if v.denominator != 1 else int(v)))


def sources(**changes):
    base = catalog(navigation_layer_id='ground', **changes)
    scenario = battle_scenario.definition()
    payload = base.payload
    bridge = deepcopy(payload['layers'][0])
    bridge.update(surface_id='bridge', navigation_layer_id=scenario['capture_points'][-1]['navigation_layer_id'])
    payload['layers'].append(bridge)
    payload['capture_masks'] = [dict(point_id=p['point_id'], surface_id='bridge' if p['support']=='bridge_deck' else 'ground',
        accepted=True, polygons=[rectangle(F(p['x']), F(p['z']), 250)]) for p in scenario['capture_points']]
    c = SurfaceCatalog(base.assets, seal_catalog(encoded(payload)))
    definition = seal_interactions(encoded(dict(schema_version='qinglan-map-interactions-v1',
        surface_catalog_sha256=c.digest, forest_polygons=[],
        airspace=dict(allowed=[rectangle(6000, 6000, 5990)], forbidden=[], display_height_mm=30000),
        blocker_parts=[], transparent_parts=list(base.assets.part_ids()), solid_volumes_validated=True,
        capture_layers={p['point_id']: ['bridge' if p['support']=='bridge_deck' else 'ground'] for p in scenario['capture_points']},
        dock_surfaces=['ground'])))
    return c, definition, scenario


def terrain(**changes):
    return CatalogInteractions(*sources(**changes))


def at_exact(u, c, x, z):
    u['geometry_position'] = dict(catalog_sha256=c.digest, surface_id=u['surface_id'], exact_position_m=[str(x), str(z)])
    u['position_mm'].update(x_mm=round(x*1000), z_mm=round(z*1000))
    return u


class CatalogInteractionTests(TestCase):
    def test_manifest_binding_and_explicit_five_masks_required(self):
        c, d, s = sources()
        bad = deepcopy(d); bad['surface_catalog_sha256'] = '0'*64
        with self.assertRaises(ServiceError): CatalogInteractions(c, seal_interactions(bad), s)
        payload = c.payload; payload['capture_masks'].pop()
        c = SurfaceCatalog(c.assets, seal_catalog(payload))
        d['surface_catalog_sha256'] = c.digest
        with self.assertRaises(ServiceError): CatalogInteractions(c, seal_interactions(d), s)

    def test_no_default_layer_or_height_fallback(self):
        t = terrain(); u = unit()
        u['surface_id'] = 'unknown'
        with self.assertRaises(ServiceError): t.rule_anchor(u)
        u['surface_id'] = 'ground'; u['position_mm']['height_mm'] = 1
        with self.assertRaises(ServiceError): t.rule_anchor(u)

    def test_cursor_is_not_an_interaction_position_or_legacy_authority(self):
        t = terrain(); u = unit()
        u['movement_progress'] = dict(catalog_sha256=t.catalog.digest, surface_id='ground',
                                     exact_position_m=['3000001/3000','1000'])
        self.assertEqual(t._point(u), (F(1000),F(1000)))
        u['geometry_position'] = deepcopy(u['movement_progress'])
        with self.assertRaises(ServiceError): t._point(u)
        u.pop('geometry_position'); u.pop('movement_progress')
        self.assertEqual(t._point(u), (F(1000),F(1000)))

    def test_capture_explicit_mask_layer_circle_and_finite_bridge(self):
        t = terrain(); p = t.capture_points[-1]
        u = unit(x=F(p['x']), z=5800); u['surface_id'] = 'bridge'
        self.assertTrue(t.capture_footprint_legal(u, p))
        u['surface_id'] = 'ground'
        self.assertFalse(t.capture_footprint_legal(u, p))
        u['surface_id'] = 'bridge'; u['position_mm']['z_mm'] += 16001
        self.assertFalse(t.capture_footprint_legal(u, p))
        u['position_mm'].update(z_mm=5800000, x_mm=round((F(p['x'])+251)*1000))
        self.assertFalse(t.capture_footprint_legal(u, p))

    def test_service_checks_whole_sweep_thin_wall_not_just_endpoints(self):
        t = terrain(solids=[dict(polygon=rectangle(1050, 1000, F(1,100), 10))])
        a, b = unit(kind='support'), unit('b',x=1100)
        self.assertTrue(t.service_footprint_legal(a)); self.assertTrue(t.service_footprint_legal(b))
        self.assertFalse(t.service_segment_clear(a,b))
        t = terrain(overlays=[dict(kind='water', polygon=rectangle(1050,1000,F(1,100),10))])
        self.assertFalse(t.service_segment_clear(a,b))

    def test_service_explicit_cross_layer_not_guessed(self):
        t = terrain(); a, b = unit(), unit('b', x=1020)
        b['surface_id'] = 'bridge'
        self.assertFalse(t.service_segment_clear(a,b))

    def test_visual_flight_height_is_not_observer_anchor(self):
        t = terrain(); u = unit(kind='recon'); u['air_state']='flying'; u['position_mm']['height_mm']=30000
        self.assertEqual(t.rule_anchor(u), (F(1000), F(2), F(1000)))
        self.assertEqual(t.flight_position(u)['height_mm'], 30000)

    def test_air_full_footprint_and_forbidden_contact(self):
        c,d,s = sources(); d['airspace']['forbidden']=[encoded(rectangle(1050,1000,F(1,100),10))]
        t = CatalogInteractions(c, seal_interactions(d), s)
        u = unit(kind='recon',x=1047); u['air_state']='flying'
        self.assertFalse(t.air_legal(u))  # 完整圆的保守外包触及窄禁飞条。
        u['position_mm']['x_mm'] = 1046000
        self.assertTrue(t.air_legal(u))


class LocalManualTargetPrecisionTests(TestCase):
    def setUp(self):
        self.geometry = object.__new__(LocalManualMapGeometry)

    def test_actual_click_with_half_millimetre_is_accepted_without_mutating_input(self):
        target = dict(x='75.5166', y='46.41315')
        before = deepcopy(target)
        self.assertEqual(self.geometry.position(target),
            dict(x_mm=7551700, z_mm=6962000, height_mm=0))
        self.assertEqual(target, before)
        with self.assertRaises(ServiceError) as caught:
            MapGeometry.position(None, target)
        self.assertEqual(caught.exception.code, 'BATTLE_POSITION_PRECISION')

    def test_decimetre_targets_roundtrip_and_input_error_is_at_most_five_cm(self):
        for x, z in ((100, 100), (10243900, 8128600), (12000000, 12000000), (0, 0)):
            point = dict(x_mm=x, z_mm=z, height_mm=0)
            self.assertEqual(self.geometry.position(self.geometry.display_position(point)), point)
        for x, y in (('10.123456', '6.666666667'), ('0.000005', '0.00001'), ('0.000015', '0.00003')):
            actual = self.geometry.position(dict(x=x, y=y))
            self.assertLessEqual(abs(actual['x_mm']-F(x)*100000), 50)
            self.assertLessEqual(abs(actual['z_mm']-F(y)*150000), 50)

    def test_rounding_does_not_allow_out_of_bounds_or_invalid_input(self):
        for target in (dict(x='120.000000001', y='0'), dict(x='0', y='80.000000001'),
                       dict(x='-0.000000001', y='0'), dict(x='NaN', y='0'),
                       dict(x='0', y='Infinity'), dict(x='0.0000000001', y='0')):
            with self.subTest(target=target), self.assertRaises(ServiceError):
                self.geometry.position(target)

    def test_waypoints_submission_and_execution_use_same_snapped_target(self):
        t = terrain()
        coordinator = MapMotionCoordinator(t.catalog, t, self.geometry.position, distance_quantum_mm=100)
        self.geometry.motion = coordinator
        u = unit()
        u['movement'] = dict(action='move', position=dict(x='10.200004', y='6.666666667'),
            waypoints=[dict(x='10.100006', y='6.666666667')])
        before = deepcopy(u)
        self.geometry.validate_movement_submission(u)
        self.assertEqual(u, before)
        route = coordinator._same_layer_route(u)
        self.assertEqual(route[0]['end_m'], [F(1010), F(1000)])
        self.assertEqual(route[-1]['end_m'], [F(1020), F(1000)])
        motion = coordinator.motions(dict(units=[u], camps=[]), start_positive={'u'},
            service_locked=set(), transitions=set())['u']
        self.assertTrue(motion.finished)
        self.assertEqual(motion.trace[-1]['x_mm'], 1020000)
        self.assertEqual(motion.trace[-1]['z_mm'], 1000000)


class AirMotionTests(TestCase):
    def coordinator(self, route):
        t = terrain()
        return MapMotionCoordinator(t.catalog,t,lambda p:p,route_resolver=lambda u: route)

    def test_submission_rejects_unrepresentable_ground_waypoint_before_lock(self):
        geometry = object.__new__(MapGeometry)
        u = unit(); u['movement'] = dict(action='move', position=dict(x='11', y='10'),
                                         waypoints=[dict(x='10.33333', y='6.66666666')])
        before = deepcopy(u)
        with self.assertRaises(ServiceError) as failure:
            geometry.validate_movement_submission(u)
        self.assertEqual(failure.exception.code, 'BATTLE_POSITION_PRECISION')
        self.assertEqual(u, before)
        u['movement']['waypoints'][0]['y'] = '6.66666'
        self.assertIsNone(geometry.validate_movement_submission(u))

    def test_production_default_route_passes_fraction_objects_to_ground_planner(self):
        t=terrain(); coordinator=MapMotionCoordinator(t.catalog,t,MapGeometry.position)
        # 非绑定position helper，在此保持相同真实函数实现与坐标变换。
        coordinator.position=lambda p: MapGeometry.position(None,p)
        u=unit(); u['movement']=dict(action='move',position=dict(x='10.33333',y='6.66666666'))
        # 使用z=1000m精确业务输入，不允许在客户端制造亚毫米业务目标。
        u['movement']['position']=dict(x='10.33333',y='6.66666')
        route=coordinator._same_layer_route(u)
        self.assertIsInstance(route[0]['end_m'][0],F)
        self.assertEqual(route[0]['end_m'][0],F(1033333,1000))
        motion=coordinator.motions(dict(units=[u],camps=[]),start_positive={'u'},service_locked=set(),transitions=set())['u']
        self.assertTrue(motion.finished)
        self.assertEqual(motion.trace[-1]['x_mm'],1033333)

    def test_flying_not_sent_to_ground_and_landing_stays_fixed(self):
        coordinator = self.coordinator([dict(surface_id='ground',end_m=[1100,1000])])
        u = unit(kind='recon'); u['air_state']='landing'; u['position_mm']['height_mm']=30000
        state = dict(units=[u],camps=[])
        result = coordinator.motions(state,start_positive={'u'},service_locked=set(),transitions={'u'})
        m=result['u']; self.assertEqual(m.trace,(u['position_mm'],)); self.assertEqual(m.progress['surface_id'],'ground')
        self.assertEqual(m.progress['catalog_sha256'],coordinator.catalog.digest)
        self.assertEqual(m.progress['exact_position_m'],['1000','1000'])

    def test_air_cursor_keeps_frozen_leg_length_across_json_restore(self):
        route=[dict(surface_id='ground',end_m=[1300,1300])]
        coordinator=self.coordinator(route); u=unit(kind='recon'); u['air_state']='flying'; u['position_mm']['height_mm']=30000
        m=coordinator._air_motion(u,movable=True)
        first_length=m.progress['leg_length_m']
        u['position_mm']=m.trace[-1]; u['movement_progress']=m.progress
        u=json.loads(json.dumps(u))
        n=coordinator._air_motion(u,movable=True)
        self.assertEqual(n.progress['leg_start_m'],['1000','1000'])
        self.assertEqual(n.progress['leg_length_m'], first_length)
        self.assertEqual(F(n.progress['distance_m']), F(m.progress['distance_m'])*2)
        self.assertEqual(sum(F(s['length_mm']) for s in m.segments),66666)
        self.assertEqual(sum(F(s['length_mm']) for s in n.segments),66667)
        self.assertEqual(sum(F(s['length_mm']) for s in n.budget_segments),
                         sum(F(s['length_mm']) for s in m.budget_segments))
        self.assertEqual(n.progress['rule_position_mm'],n.trace[-1])

    def test_local_air_decimetre_cursor_survives_restore(self):
        t = terrain()
        coordinator = MapMotionCoordinator(t.catalog, t, lambda p: p, distance_quantum_mm=100,
            route_resolver=lambda u: [dict(surface_id='ground', end_m=[1300, 1300])])
        u = unit(kind='recon'); u['air_state'] = 'flying'; u['position_mm']['height_mm'] = 30000
        before = deepcopy(u)
        for expected in (66600, 66700, 66700):
            motion = coordinator._air_motion(u, movable=True)
            self.assertEqual(sum(F(s['length_mm']) for s in motion.segments), expected)
            self.assertTrue(all(p['x_mm'] % 100 == p['z_mm'] % 100 == 0 for p in motion.trace[1:]))
            self.assertLess(sum(F(s['length_mm']) for s in motion.progress['path_pending_segments']), 100)
            u.update(position_mm=motion.trace[-1], movement_progress=motion.progress)
            u = json.loads(json.dumps(u))
        self.assertEqual(before['position_mm']['x_mm'], 1000000)

    def test_submission_checks_late_air_leg_without_advancing_or_consuming(self):
        coordinator = self.coordinator([dict(surface_id='ground', end_m=[1100, 1000]),
                                        dict(surface_id='ground', end_m=[1500, 1000])])
        coordinator.terrain.air_forbidden = (tuple(map(tuple, rectangle(1400, 1000, F(1, 100), 10))),)
        u = unit(kind='recon'); u['air_state'] = 'flying'; u['position_mm']['height_mm'] = 30000
        before = deepcopy(u)
        # 前5秒本可执行；以前仅检查此步会把后续越界留到双方锁定后。
        self.assertFalse(coordinator._air_motion(u, movable=True).finished)
        with self.assertRaises(ServiceError) as failure:
            coordinator._air_motion(u, movable=True, validate_only=True)
        self.assertEqual(failure.exception.code, 'BATTLE_AIR_ROUTE_FORBIDDEN')
        self.assertEqual(u, before)

    def test_submission_validates_remaining_air_cursor_not_completed_legs(self):
        coordinator = self.coordinator([dict(surface_id='ground', end_m=[1010, 1000]),
                                        dict(surface_id='ground', end_m=[1300, 1000])])
        u = unit(kind='recon'); u['air_state'] = 'flying'; u['position_mm']['height_mm'] = 30000
        m = coordinator._air_motion(u, movable=True)
        u['position_mm'] = deepcopy(m.trace[-1]); u['movement_progress'] = deepcopy(m.progress)
        self.assertEqual(m.progress['leg_index'], 1)
        # 已走完第一腿的区域改变不应使游标倒退重新扫过去。
        coordinator.terrain.air_forbidden = (tuple(map(tuple, rectangle(1005, 1000, F(1, 100), 10))),)
        before = deepcopy(u)
        self.assertIsNone(coordinator._air_motion(json.loads(json.dumps(u)), movable=True, validate_only=True))
        self.assertEqual(u, before)
        u['movement_progress']['rule_position_mm']['x_mm'] += 1
        with self.assertRaises(ServiceError) as failure:
            coordinator._air_motion(u, movable=True, validate_only=True)
        self.assertEqual(failure.exception.code, 'BATTLE_ROUTE_STALE')

    def test_submission_rejects_late_anchor_transition_and_accepts_legal_route(self):
        route = [dict(surface_id='ground', end_m=[1100, 1000]), dict(surface_id='bridge', end_m=[1200, 1000])]
        coordinator = self.coordinator(route)
        u = unit(kind='recon'); u['air_state'] = 'flying'; u['position_mm']['height_mm'] = 30000
        with self.assertRaises(ServiceError) as failure:
            coordinator._air_motion(u, movable=True, validate_only=True)
        self.assertEqual(failure.exception.code, 'BATTLE_AIR_ANCHOR_LAYER_MISSING')
        route[-1]['surface_id'] = 'ground'
        self.assertIsNone(coordinator._air_motion(u, movable=True, validate_only=True))

    def test_flying_whole_sweep_rejects_thin_forbidden_strip(self):
        coordinator=self.coordinator([dict(surface_id='ground',end_m=[1100,1000])])
        coordinator.terrain.air_forbidden=(tuple(map(tuple,rectangle(1020,1000,F(1,100),10))),)
        u=unit(kind='recon'); u['air_state']='flying'; u['position_mm']['height_mm']=30000
        before=deepcopy(u)
        with self.assertRaises(ServiceError) as failure: coordinator._air_motion(u,movable=True)
        self.assertEqual(failure.exception.code,'BATTLE_AIR_ROUTE_FORBIDDEN')
        self.assertEqual(u,before)

    def test_no_guessed_air_anchor_layer_transition(self):
        coordinator=self.coordinator([dict(surface_id='bridge',end_m=[1100,1000])])
        u=unit(kind='recon'); u['air_state']='flying'; u['position_mm']['height_mm']=30000
        with self.assertRaises(ServiceError) as failure: coordinator._air_motion(u,movable=True)
        self.assertEqual(failure.exception.code,'BATTLE_AIR_ANCHOR_LAYER_MISSING')


class PublicationGateTests(TestCase):
    def test_missing_frozen_inputs_cannot_assemble_production(self):
        with TemporaryDirectory() as root:
            with self.assertRaises(ServiceError) as failure:
                load_server_geometry(Path(root)/'surface.json', Path(root)/'interactions.json', Path(root)/'scenario.json',root)
        self.assertEqual(failure.exception.code,'BATTLE_FROZEN_MANIFEST_MISSING')

    def test_complete_component_fixture_is_not_production_acceptance(self):
        c,d,s=sources()
        # 射线只用测试假实现，以便验证来源门禁，不是批准地图遮挡。
        from unittest.mock import patch
        with patch('backend.battle_map_geometry.RayIndex',return_value=type('Rays',(),{'unresolved':()})()):
            with self.assertRaises(ServiceError) as failure:
                MapGeometry(c,d,None,None,scenario=s,rules=rulebook.definition(),capacity=battle_capacity.definition())
        self.assertEqual(failure.exception.code,'BATTLE_SURFACE_ACCEPTANCE_MISSING')

    def test_existing_sources_distinguished_from_absent_semantics(self):
        r=source_readiness()
        self.assertFalse(r['production_ready']); self.assertTrue(r['read_only'])
        self.assertEqual(len(r['available_sources']['capture_points']),5)
        self.assertEqual({b['category'] for b in r['blockers']},
                         {'geometry_acceptance','source_semantics','independent_acceptance'})


class ExecutorGeometryContractTests(TestCase):
    def test_bad_exact_position_from_adapter_rejected(self):
        g=FixtureGeometry(); state=state_fixture(); identity=state_unit(state)['unit_id']
        def mover(state, result, kwargs):
            result[identity]=Motion((deepcopy(state_unit(state)['position_mm']),), progress=dict(
                catalog_sha256='a'*64,surface_id='ground',exact_position_m=['1001','1000']))
            return result
        g.mover=mover
        with self.assertRaises(ServiceError) as failure:
            Executor(g)._motion(state,{identity},set(),set(),[],5)
        self.assertEqual(failure.exception.code,'BATTLE_GEOMETRY_INVALID')

    def test_terminal_motion_clears_cursor_but_preserves_actual_surface(self):
        for terminal in ('finished','interrupted'):
            with self.subTest(terminal=terminal):
                g=FixtureGeometry(); state=state_fixture(); u=state_unit(state)
                u['surface_id']='old'; u['movement']=dict(action='move',position=dict(x='11',y='10'))
                uid=u['unit_id']
                def mover(state,result,kwargs):
                    point=state_unit(state)['position_mm']
                    result[uid]=Motion((deepcopy(point),), progress=dict(catalog_sha256='a'*64,
                        surface_id='new',exact_position_m=['1000','1000']), surface_id='new',
                        **{terminal:True})
                    return result
                g.mover=mover
                Executor(g)._motion(state,{uid},set(),set(),[],5)
                self.assertEqual(u['surface_id'],'new')
                self.assertNotIn('geometry_position',u)
                self.assertNotIn('movement_progress',u)
                if terminal=='finished': self.assertIsNone(u['movement'])
                else: self.assertIsNotNone(u['movement'])

    def test_stop_and_replacement_discard_all_route_only_remainders(self):
        from backend.tests.test_battle_execution import order
        for action in ('stop','move'):
            state=state_fixture(); u=state_unit(state); g=FixtureGeometry(); executor=Executor(g)
            u.update(surface_id='new',movement_progress={'distance_m':'1/3'},
                     geometry_position={'exact_position_m':['3000001/3000','1000']})
            before=deepcopy(u['position_mm'])
            movement=dict(action=action)
            if action=='move': movement['position']=dict(x='11',y='10')
            executor._adopt(state,dict(blue=[order(state,movement=movement)],red=[]))
            self.assertNotIn('movement_progress',u); self.assertNotIn('geometry_position',u)
            self.assertEqual(u['surface_id'],'new'); self.assertEqual(u['position_mm'],before)

    def test_observer_death_moves_current_contact_to_history(self):
        g=FixtureGeometry(); state=state_fixture(); observer=state_unit(state,'recon'); enemy=state_unit(state,side='red')
        observer['air_state']='flying'; observer['durability']='5'
        enemy['task']=dict(action='attack',target_id=observer['unit_id'],target_life_id=observer['life_id'])
        def observe(state,**kwargs):
            source=state_unit(state,'recon')
            return dict(blue={state_unit(state,side='red')['unit_id']} if k.alive(source) else set(),
                        red={source['unit_id']} if k.alive(source) else set())
        g.observe=observe
        result,_=Executor(g).step(state)
        self.assertTrue(state_unit(result,'recon')['retired'])
        self.assertEqual(result['observations']['blue']['contacts'],[])
        self.assertIn(enemy['unit_id'],[v['unit_id'] for v in result['observations']['blue']['last_seen_records']])

class FrozenCampAndEvidenceTests(TestCase):
    def test_evidence_structure_rejects_malformed_and_incomplete_reports(self):
        for report in (None, [], {}, {'cases': [None]}, {'cases': [{}]*12},
                       {'cases':[dict(case_id=['unhashable'])]*12}):
            self.assertFalse(battle_scenario.acceptance_evidence_valid(report))
        cases=[dict(case_id=case,passed=True,source='real_map',evidence_sha256='a'*64)
               for case in sorted(battle_scenario.REQUIRED_ACCEPTANCE_CASES)]
        self.assertTrue(battle_scenario.acceptance_evidence_valid(dict(cases=cases)))
        for key,value in [('passed',1),('source','fixture'),('evidence_sha256','not-a-hash')]:
            bad=deepcopy(cases); bad[0][key]=value
            self.assertFalse(battle_scenario.acceptance_evidence_valid(dict(cases=bad)))
        # 结构正确不是实图证据，装配仍由MapGeometry严格核原文件与摘要。

    def test_camps_compare_frozen_setup_not_only_current_geometry(self):
        from types import SimpleNamespace
        from backend.battle_setup import barracks_id
        state=dict(manifest=dict(battle_setup={s:dict(camps=[]) for s in k.SIDES}),camps=[])
        for side in k.SIDES:
            for n,kind in enumerate(('ground','support','recon')):
                point=dict(x_mm=(n+1)*100000,z_mm=100000,height_mm=0)
                anchor=dict(point,x_mm=point['x_mm']+40000)
                item=dict(unit_type=kind,position=point,surface_id='ground',service_anchor=anchor,
                          service_surface_id='ground')
                state['manifest']['battle_setup'][side]['camps'].append(deepcopy(item))
                state['camps'].append(dict(camp_id=barracks_id(side,kind),side=side,unit_type=kind,
                    position_mm=point,surface_id='ground',service_anchor_mm=anchor,service_surface_id='ground'))
        geometry=object.__new__(MapGeometry)
        geometry.terrain=SimpleNamespace(camp_service_legal=lambda c:True,capture_masks={})
        self.assertEqual(geometry.camps(state),state['camps'])
        for field,value in [('position_mm',dict(x_mm=999000,z_mm=100000,height_mm=0)),
                            ('service_anchor_mm',dict(x_mm=999000,z_mm=100000,height_mm=0)),
                            ('service_surface_id','changed'),('side','red')]:
            bad=deepcopy(state); bad['camps'][0][field]=value
            with self.subTest(field=field), self.assertRaises(ServiceError) as error:
                geometry.camps(bad)
            self.assertEqual(error.exception.code,'BATTLE_CAMPS_CHANGED')


class RecordedMovementCostTests(TestCase):
    def test_cumulative_third_step_cost_is_not_rejected_as_over_five_seconds(self):
        segments=[dict(length_mm='33334',surface='road',grade_squared='0')]
        proof=k.segment_budget('ground',segments,'100')
        self.assertNotEqual(proof['lengths_mm'],['33334'])
        self.assertEqual(F(k.movement_cost('ground',segments)), F(33334,1000)*F(8,1000))
        self.assertEqual(sum(F(k.movement_cost('ground',[dict(length_mm=str(length),surface='road',grade_squared='0')]))
                             for length in (33333,33333,33334)), F(4,5))

    def test_internal_segmentation_keeps_exact_mixed_surface_cost(self):
        whole=[dict(length_mm='1000',surface='road',grade_squared='0')]
        split=[dict(length_mm=str(F(1000,3)),surface='road',grade_squared='0')]*3
        self.assertEqual(k.movement_cost('ground',whole),k.movement_cost('ground',split))


class CumulativeExecutorTests(TestCase):
    """合成目录+真实适配器+执行器；仍非实地图验收。"""
    def chain(self, kind='ground', target=1100):
        state=state_fixture(); u=state_unit(state,kind); g=FixtureGeometry()
        t=terrain(); coordinator=MapMotionCoordinator(t.catalog,t,lambda p:p)
        u.update(surface_id='ground', movement=dict(action='move'))
        if kind=='recon':
            u['air_state']='flying'; u['position_mm']['height_mm']=30000
        uid=u['unit_id']; route=[dict(surface_id='ground',end_m=[target,1000])]
        coordinator.route_resolver=lambda current: route
        def mover(snapshot,result,kwargs):
            current=state_unit(snapshot,kind)
            result[uid]=(coordinator._air_motion(current,movable=True) if kind=='recon'
                         else coordinator.ground.plan(current,route))
            return result
        g.mover=mover
        return state,u,Executor(g),coordinator,route

    def test_ground_three_steps_charge_recorded_mm_once_and_clear_at_arrival(self):
        state,u,executor,_,_=self.chain(); uid=u['unit_id']; start=F(u['energy']); lengths=[]
        for step in range(3):
            motions,_,_=executor._motion(state,{uid},set(),set(),[],(step+1)*5)
            motion=motions[uid]; lengths.append(sum(F(s['length_mm']) for s in motion.segments))
            state=json.loads(json.dumps(state)); u=state_unit(state)
        self.assertEqual(lengths,[33333,33333,33334])
        self.assertEqual(start-F(u['energy']),F(4,5))
        self.assertEqual(u['position_mm']['x_mm'],1100000)
        self.assertIsNone(u['movement']); self.assertNotIn('movement_progress',u)
        self.assertNotIn('geometry_position',u); self.assertEqual(u['surface_id'],'ground')

    def test_air_two_steps_and_energy_exhaustion_discards_pending(self):
        state,u,executor,_,_=self.chain('recon',1200); uid=u['unit_id']; start=F(u['energy'])
        lengths=[]
        for step in range(3):
            motions,_,_=executor._motion(state,{uid},set(),set(),[],(step+1)*5)
            lengths.append(sum(F(s['length_mm']) for s in motions[uid].segments))
        self.assertEqual(lengths,[66666,66667,66667])
        self.assertEqual(start-F(u['energy']),F(k.movement_cost('recon',[dict(length_mm='200000',surface='air',grade_squared='0')])))
        self.assertNotIn('movement_progress',u)
        state,u,executor,_,_=self.chain('recon',1200); uid=u['unit_id']
        # 精确资源守恒由fixture账本同步扣除；不足1mm尾数不得跨耗尽恢复。
        from backend.tests.test_battle_kernel import spend
        spend(state,u,'energy',F(u['energy'])-F(1,100000))
        motions,_,_=executor._motion(state,{uid},set(),set(),[],5)
        self.assertTrue(motions[uid].interrupted)
        self.assertNotIn('movement_progress',u)
        self.assertNotIn('geometry_position',u)

    def test_tampered_billed_increment_is_rejected(self):
        from dataclasses import replace
        state,u,executor,coordinator,route=self.chain(); uid=u['unit_id']
        correct=coordinator.ground.plan(u,route)
        corrupted=list(deepcopy(correct.segments)); corrupted[0]['length_mm']='33334'
        executor.geometry.mover=lambda snapshot,result,kwargs: dict(result,**{uid:replace(correct,segments=tuple(corrupted))})
        with self.assertRaises(ServiceError) as error:
            executor._motion(state,{uid},set(),set(),[],5)
        self.assertEqual(error.exception.code,'BATTLE_GEOMETRY_INVALID')

    def test_diagonal_air_cursor_continues_after_integral_path_distance(self):
        coordinator=AirMotionTests().coordinator([dict(surface_id='ground',end_m=[1400,1400])])
        u=unit(kind='recon'); u['air_state']='flying'; u['position_mm']['height_mm']=30000
        totals=[]
        for _ in range(4):
            motion=coordinator._air_motion(u,movable=True)
            u['position_mm']=deepcopy(motion.trace[-1]); u['movement_progress']=deepcopy(motion.progress)
            u=json.loads(json.dumps(u)); totals.append(motion.progress['recorded_distance_mm'])
        self.assertEqual(totals,[66666,133333,200000,266666])

    def test_air_landing_clears_live_cursor_without_advancing_authority(self):
        state,u,executor,coordinator,_=self.chain('recon',1200); uid=u['unit_id']
        executor._motion(state,{uid},set(),set(),[],5)
        before=deepcopy(u['position_mm']); self.assertTrue(u['movement_progress']['path_pending_segments'])
        u['air_state']='landing'
        m=coordinator._air_motion(u,movable=False)
        executor.geometry.mover=lambda snapshot,result,kwargs: dict(result,**{uid:m})
        executor._motion(state,{uid},set(),{uid},[],10)
        self.assertEqual(u['position_mm'],before); self.assertNotIn('movement_progress',u)
        self.assertTrue(m.interrupted); self.assertEqual(m.progress['path_pending_segments'],[])
