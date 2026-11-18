"""Synthetic math/contract tests != current-map acceptance. No database access."""
from copy import deepcopy
from fractions import Fraction as F
import json
from unittest import TestCase

from backend import battle_kernel as k
from backend.battle_map_surfaces import (SurfaceCatalog, build_catalog, seal_catalog,
                                        rectangle, face_id, build_bridge_face_bindings)
from backend.battle_map_motion import (MapMotion, collision_time, arc_length,
                                       record_path_segments, pending_path_energy)
from backend.battle_map_deployment import MapDeployment, _disc_polygon
from backend.battle_terrain_assets import TerrainAssets
from backend.battle_terrain import SupportTriangle
from backend.tests.test_battle_support import quad
from backend.demo_domain import MAP_PATH
from backend.runtime import ServiceError


class FixtureAssets:
    """Cannot be constructed as TerrainAssets or published by this fixture."""
    def __init__(self, parts=None):
        self.parts = parts or {'floor': quad(0, 0, 12000, 12000)}
        self.source_definition = json.loads(MAP_PATH.read_bytes())
        self.manifest = dict(map_id='synthetic-not-accepted', definition_sha256='synthetic-definition',
            geometry_sha256='synthetic-geometry', payload_sha256='synthetic-annotations',
            playable_bounds_m=dict(min_x=0, min_z=0, max_x=12000, max_z=12000),
            parts=[dict(part_id=p, source_layer='fixture', source_material='fixture', action='preserve_existing')
                   for p in sorted(self.parts)])

    def part_ids(self): return tuple(sorted(self.parts))
    def face_count(self, part): return len(self.parts[part])
    def face_vertices(self, part, index): return self.parts[part][index]
    def triangle(self, part, index): return SupportTriangle(self.face_vertices(part, index))
    def part_bounds(self, part):
        points = [v for triangle in self.parts[part] for v in triangle]
        return min(v[0] for v in points), min(v[2] for v in points), max(v[0] for v in points), max(v[2] for v in points)


def layer(assets, **changes):
    result = dict(surface_id='ground', support_faces={p: list(range(assets.face_count(p))) for p in assets.part_ids()},
        ground_surface='road', resolved=dict(support=True, overlays=True, solids=True, access=True),
        regions=[], overlays=[], solids=[], access=[dict(polygon=((1, 1), (11999, 1), (11999, 11999), (1, 11999)),
            sides=['blue', 'red'], purposes=['move', 'stop', 'deploy', 'camp', 'service', 'dock', 'capture'])])
    result.update(changes)
    return json.loads(json.dumps(result, default=lambda v: int(v) if v.denominator == 1 else float(v)))


def catalog(assets=None, **changes):
    assets = assets or FixtureAssets()
    payload = build_catalog(assets)
    payload['layers'] = [layer(assets, **changes)]
    return SurfaceCatalog(assets, seal_catalog(payload))


def unit(identity='u', side='blue', kind='ground', x=1000, z=1000, energy='100'):
    return dict(unit_id=identity, side=side, unit_type=kind, position_mm=dict(x_mm=x*1000, z_mm=z*1000, height_mm=0),
                surface_id='ground', durability='100', energy=energy, air_state='docked', movement=dict(action='move'), task=None)


def legs(x=1100, z=1000, surface='ground'):
    return [dict(surface_id=surface, end_m=[x, z])]


class CatalogTests(TestCase):
    def test_digest_binds_complete_source_inventory_and_no_permissions(self):
        a = FixtureAssets(); payload = build_catalog(a)
        self.assertFalse(payload['acceptance']['production_ready'])
        self.assertEqual(payload['layers'], [])
        self.assertEqual(len(payload['source_facts']['capture_points']), 5)
        c = SurfaceCatalog(a, payload)
        with self.assertRaises(ServiceError): c.height('ground', (1000, 1000))
        for key in ('geometry_sha256', 'source_facts', 'parts'):
            bad = deepcopy(payload); bad[key] = None
            with self.assertRaises(ServiceError): SurfaceCatalog(a, seal_catalog(bad))

    def test_face_identity_binds_part_index_and_mesh_not_annotation(self):
        self.assertEqual(face_id('mesh', 'p', 3), face_id('mesh', 'p', 3))
        self.assertNotEqual(face_id('mesh', 'p', 3), face_id('mesh', 'q', 3))
        self.assertNotEqual(face_id('mesh', 'p', 3), face_id('changed', 'p', 3))

    def test_unresolved_layer_rejected_even_when_hashed(self):
        with self.assertRaises(ServiceError): catalog(resolved=dict(support=True))

    def test_own_deployment_not_enemy_free_roam(self):
        c = catalog(access=[])
        c.footprint('ground', rectangle(10500, 2000, 3), side='blue', purpose='stop')
        with self.assertRaises(ServiceError): c.footprint('ground', rectangle(1500, 4000, 3), side='blue', purpose='stop')

    def test_forest_water_solids_and_camp_types(self):
        for kind in ('forest', 'water'):
            c = catalog(overlays=[dict(kind=kind, polygon=rectangle(10500, 2000, 1))])
            with self.assertRaises(ServiceError): c.footprint('ground', rectangle(10500, 2000, 30), side='blue', purpose='camp')
        c = catalog(solids=[dict(polygon=rectangle(1005, 1000, 1))])
        with self.assertRaises(ServiceError): c.footprint('ground', rectangle(1000, 1000, 4), side='blue', purpose='move')
        for surface in ('road', 'bridge', 'field', 'sparse_forest', 'mud', 'water'):
            with self.assertRaises(ServiceError):
                catalog(ground_surface=surface).footprint('ground', rectangle(10500, 2000, 30), side='blue', purpose='camp')

    def test_overlapping_region_types_rejected(self):
        with self.assertRaises(ServiceError):
            catalog(regions=[dict(surface=s, polygon=rectangle(1000, 1000, 10)) for s in ('road', 'field')])

    def test_selected_faces_cannot_fill_hole_using_unapproved_triangle(self):
        a = FixtureAssets({'p': quad(995, 995, 1005, 1005)})
        c = catalog(a, support_faces={'p': [0]})
        with self.assertRaises(ServiceError): c.footprint('ground', rectangle(1000, 1000, 3), side='blue', purpose='stop')

    def test_bridge_step_rejected_not_averaged(self):
        a = FixtureAssets({'low': quad(990, 990, 1000, 1010), 'high': quad(1000, 990, 1010, 1010, height=2)})
        with self.assertRaises(ServiceError):
            catalog(a).footprint('ground', rectangle(1000, 1000, 3), side='blue', purpose='move')


class MotionTests(TestCase):
    def test_road_exact_budget_and_input_unchanged(self):
        c = catalog(); u = unit(); before = deepcopy(u)
        motion = MapMotion(c).plan(u, legs())
        self.assertEqual(u, before)
        self.assertEqual(sum(F(s['length_mm']) for s in motion.segments), 33333)
        self.assertEqual(sum(F(s['length_mm']) for s in motion.budget_segments), F(100000, 3))
        self.assertEqual(k.movement_cost('ground', motion.segments), '33333/125000')
        self.assertEqual(k.segment_budget('ground', motion.budget_segments, u['energy'])['energy'], '4/15')
        self.assertEqual(motion.trace[-1]['x_mm'], 1033333)
        self.assertFalse(motion.finished)

    def test_exact_submillimeter_progress_resumes_same_arc(self):
        planner = MapMotion(catalog()); u = unit(); route = legs()
        for _ in range(3):
            motion = planner.plan(u, route)
            u['position_mm'], u['movement_progress'] = motion.trace[-1], motion.progress
        self.assertEqual(u['position_mm']['x_mm'], 1100000)
        self.assertTrue(motion.finished)

    def test_mixed_road_field_does_not_reset_time(self):
        c = catalog(regions=[dict(surface='field', polygon=((1010, 1), (11999, 1), (11999, 11999), (1010, 11999)))])
        m = MapMotion(c).plan(unit(), legs())
        self.assertEqual(sum(F(s['length_mm']) for s in m.segments), 21666)
        self.assertEqual(sum(F(s['length_mm']) for s in m.budget_segments), F(65000, 3))
        self.assertEqual(k.segment_budget('ground', m.budget_segments, '100')['energy'], '4/15')
        self.assertEqual(k.movement_cost('ground', m.segments), '8333/31250')

    def test_energy_exhaustion_and_attack_reservation(self):
        p = MapMotion(catalog())
        m = p.plan(unit(energy='0.08'), legs())
        self.assertEqual(sum(F(s['length_mm']) for s in m.segments), 10000)
        m = p.plan(unit(energy='0.08'), legs(), moving_attack=True)
        self.assertEqual(sum(F(s['length_mm']) for s in m.segments), 8000)

    def test_thin_static_wall_and_partial_stop(self):
        c = catalog(solids=[dict(polygon=((1015, 990), ('1015.001', 990), ('1015.001', 1010), (1015, 1010)))])
        m = MapMotion(c).plan(unit(), legs())
        end = F(m.progress['exact_position_m'][0])
        self.assertLess(end, 1012)
        self.assertGreater(end, F('1011.99'))

    def test_narrow_support_center_passes_but_full_width_fails(self):
        a = FixtureAssets({'p': quad(990, 998, 1200, 1002)})
        with self.assertRaises(ServiceError): MapMotion(catalog(a)).plan(unit(), legs())

    def test_cross_slope_not_replaced_by_endpoint_grade(self):
        a = FixtureAssets({'p': quad(990, 990, 1200, 1010, dz=F(36, 100))})
        u = unit(); u['position_mm']['height_mm'] = 360000
        with self.assertRaises(ServiceError): MapMotion(catalog(a)).plan(u, legs())

    def test_unknown_surface_and_unapproved_layer_transition(self):
        p = MapMotion(catalog())
        with self.assertRaises(ServiceError): p.plan(unit(), legs(surface='unknown'))
        c = catalog(); value = c.payload; other = deepcopy(value['layers'][0]); other['surface_id'] = 'other'; value['layers'].append(other)
        with self.assertRaises(ServiceError): MapMotion(SurfaceCatalog(c.assets, seal_catalog(value))).plan(unit(), legs(surface='other'))

    def test_friendly_overlap_not_blocking_enemy_head_on_is(self):
        p = MapMotion(catalog()); a, b = unit('a'), unit('b', x=1020)
        state = dict(camps=[], units=[a, b]); before = deepcopy(state)
        resolve = lambda u: legs(1100 if u['unit_id'] == 'a' else 900)
        m = p.motions(state, route_resolver=resolve, start_positive={'a', 'b'}, service_locked=set(), transitions={})
        self.assertGreater(m['a'].trace[-1]['x_mm'], m['b'].trace[-1]['x_mm'])
        state['units'][1]['side'] = 'red'
        m = p.motions(state, route_resolver=resolve, start_positive={'a', 'b'}, service_locked=set(), transitions={})
        pa, pb = (F(m[i].progress['exact_position_m'][0]) for i in ('a', 'b'))
        self.assertGreater(pb-pa, 5)
        self.assertEqual(pb-pa, F('5.002'))
        self.assertTrue(m['a'].interrupted and m['b'].interrupted)
        state['units'][1]['side'] = 'blue'; self.assertEqual(state, before)

    def test_crossing_synchronous_paths_not_only_endpoints(self):
        p = MapMotion(catalog()); a, b = unit('a'), unit('b', 'red', x=1010, z=990)
        plans = [p.details(a, legs(1020, 1000)), p.details(b, legs(1010, 1010))]
        self.assertIsNotNone(collision_time(*plans))

    def test_stopped_enemy_and_stationary_service_lock(self):
        p = MapMotion(catalog()); a, b = unit('a'), unit('b', 'red', x=1010)
        state = dict(camps=[], units=[a, b])
        m = p.motions(state, route_resolver=lambda u: legs(), start_positive={'a', 'b'}, service_locked={'b'}, transitions={})
        self.assertEqual(m['b'].trace, (b['position_mm'],))
        self.assertLess(m['a'].trace[-1]['x_mm'], 1005001)

    def test_flying_is_not_silently_stationary_and_no_extra_step(self):
        p = MapMotion(catalog()); u = unit(kind='recon'); u['air_state'] = 'flying'
        with self.assertRaises(ServiceError): p.plan(u, legs())
        with self.assertRaises(ServiceError): p.plan(unit(), legs(), seconds=6)

    def test_arc_is_conservative_not_endpoint_shortcut(self):
        self.assertEqual(arc_length((F(0), F(0)), (F(3), F(4))), 5)
        length = arc_length((F(0), F(0)), (F(1), F(1)))
        self.assertGreaterEqual(length**2, 2)
        self.assertLess((length-F(1, 10**6))**2, 2)


class DeploymentTests(TestCase):
    def camp(self):
        return dict(camp_id='blue-ground', side='blue', unit_type='ground', surface_id='ground',
            position_mm=dict(x_mm=10500000, z_mm=2000000, height_mm=0), service_surface_id='ground',
            service_anchor_mm=dict(x_mm=10550000, z_mm=2000000, height_mm=0))

    def test_camp_full_60m_and_service_anchor(self):
        d = MapDeployment(catalog(ground_surface='open')); c = self.camp(); before = deepcopy(c)
        polygon = d.camp(c)
        self.assertEqual(max(x for x, z in polygon)-min(x for x, z in polygon), 60)
        self.assertEqual(c, before)
        c['position_mm']['x_mm'] = 10020000
        with self.assertRaises(ServiceError): d.camp(c)

    def test_camp_full_local_five_percent_inclusive_not_center_only(self):
        for grade, passes in ((F(5, 100), True), (F(501, 10000), False)):
            a = FixtureAssets({'p': quad(10400, 1900, 10600, 2100, height=-grade*10400, dx=grade)})
            c = self.camp(); c['position_mm']['height_mm'] = round(grade*100*1000)
            c['service_anchor_mm']['height_mm'] = round(grade*150*1000)
            d = MapDeployment(catalog(a, ground_surface='open'))
            if passes: d.camp(c)
            else:
                with self.assertRaises(ServiceError): d.camp(c)

    def test_unit_full_footprint_and_dock_coverage(self):
        d = MapDeployment(catalog(ground_surface='open')); u = unit(x=10001, z=2000)
        with self.assertRaises(ServiceError): d.unit(u)
        u = unit(kind='recon', x=10500, z=2000)
        d.unit(u)
        with self.assertRaises(ServiceError): MapDeployment(catalog(ground_surface='bridge')).unit(u)

    def test_initial_missing_facilities_and_masks_remain_closed(self):
        d = MapDeployment(catalog(ground_surface='open'))
        with self.assertRaises(ServiceError): d.validate_deployment(dict(camps=[], units=[]))
        with self.assertRaises(ServiceError): d._facility(self.camp())

    def test_service_isolation_interior_and_tangent(self):
        p = rectangle(1000, 1000, 10)
        self.assertTrue(_disc_polygon((F(1000), F(1000)), F(100), p))
        self.assertTrue(_disc_polygon((F(1110), F(1000)), F(100), p))
        self.assertFalse(_disc_polygon((F(1111), F(1000)), F(100), p))


class CurrentCatalogTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = TerrainAssets.current()

    def test_export_matches_real_sources_no_fabricated_layers(self):
        c = SurfaceCatalog.current(); payload = c.payload
        self.assertEqual(payload, build_catalog(self.assets))
        self.assertEqual(len(payload['parts']), len(self.assets.part_ids()))
        self.assertEqual(len(payload['source_facts']['nodes']), 144)
        self.assertEqual(len(payload['source_facts']['roads']), 243)
        self.assertEqual(len(payload['source_facts']['buildings']), 1769)
        self.assertEqual(payload['source_facts']['capture_points'][4]['bridge_road_id'], 'ROAD_010')
        with self.assertRaises(ServiceError): c.layer('bridge-deck:bridge:ROAD_010')

    def test_bridge_source_binding_keeps_exact_original_faces_without_unlocking(self):
        from pathlib import Path
        value = json.loads((Path(__file__).resolve().parents[1]/'battle-map-source-bindings-v1.json').read_text())
        self.assertEqual(value, build_bridge_face_bindings(self.assets))
        self.assertFalse(value['production_ready'])
        self.assertEqual([v['source_road_id'] for v in value['bindings']], ['ROAD_004', 'ROAD_010', 'ROAD_016'])
        for bridge in value['bindings']:
            self.assertEqual(bridge['candidate_face_count'], 140)
            self.assertTrue(bridge['source_corridor_fully_covered'])
            self.assertFalse(bridge['connection_accepted'])
            self.assertFalse(bridge['legal_layer_created'])
            for part, indices in bridge['support_face_candidates'].items():
                for index in indices:
                    self.assertTrue(all(v[1] == 22 for v in self.assets.face_vertices(part, index)))
        current = SurfaceCatalog.current().payload
        self.assertEqual((current['layers'], current['connections']), ([], []))

    def test_every_real_face_can_be_identified_without_granting_support(self):
        payload = build_catalog(self.assets)
        self.assertTrue(all(p['face_count'] > 0 and p['semantic_status'] == 'unresolved' for p in payload['parts']))
        self.assertFalse(payload['acceptance']['production_ready'])


class IntegrationBoundaryTests(TestCase):
    def test_server_route_fraction_strings_match_exact_internal_values(self):
        p = MapMotion(catalog())
        endpoint = F(3000001, 3000)
        encoded = p.plan(unit(), legs(str(endpoint), '1000'))
        internal = p.plan(unit(), legs(endpoint, F(1000)))
        self.assertEqual(encoded, internal)
        self.assertEqual(F(encoded.progress['exact_position_m'][0]), 1000)
        self.assertEqual(encoded.progress['path_pending_segments'], [])
        self.assertEqual(encoded.trace, (unit()['position_mm'],))
        self.assertTrue(encoded.finished)
        for value in ('1/0', '1//2', 'NaN/1', '1e2/3', '1'*81+'/3'):
            with self.assertRaises(ServiceError):
                p.plan(unit(), legs(value, '1000'))

    def test_progress_always_identifies_actual_surface_including_zero_energy(self):
        p = MapMotion(catalog())
        for energy in ('0', '0.08', '100'):
            m = p.plan(unit(energy=energy), legs())
            self.assertEqual(m.progress['surface_id'], 'ground')
            self.assertEqual(m.progress['catalog_sha256'], p.catalog.digest)
            self.assertEqual(len(m.progress['exact_position_m']), 2)
        u = unit(energy='0'); m = p.plan(u, legs())
        u['energy'], u['movement_progress'] = '100', m.progress
        self.assertGreater(p.plan(u, legs()).trace[-1]['x_mm'], 1000000)

    def test_replacing_route_clears_remainder_and_starts_at_authority_mm(self):
        p = MapMotion(catalog()); u = unit()
        m = p.plan(u, legs())
        u['position_mm'] = m.trace[-1]
        # clear_movement drops the old cursor; the new route starts at 1033.333m.
        m2 = p.plan(u, legs(1200))
        self.assertEqual(F(m2.progress['exact_position_m'][0]), F('1033.333')+F(100, 3))
        self.assertEqual(m2.trace[-1]['x_mm'], 1066666)
        u['geometry_position'] = {key: m.progress[key] for key in ('catalog_sha256', 'surface_id', 'exact_position_m')}
        with self.assertRaises(ServiceError):
            p.plan(u, legs(1200))

    def test_corrupt_progress_or_catalog_is_rejected(self):
        p = MapMotion(catalog()); u = unit()
        for value in ('NaN', '1/0', '1e999', '1'*100):
            u['geometry_position'] = dict(catalog_sha256=p.catalog.digest, surface_id='ground', exact_position_m=[value, '1000'])
            with self.assertRaises(ServiceError): p.plan(u, legs())
        u['geometry_position']['catalog_sha256'] = 'wrong'
        with self.assertRaises(ServiceError): p.plan(u, legs())

    def test_explicit_layer_transition_preserves_final_and_partial_layer(self):
        c = catalog(); value = c.payload
        other = deepcopy(value['layers'][0]); other['surface_id'] = 'bridge'
        value['layers'].append(other)
        value['connections'] = [dict(accepted=True, from_surface_id='ground', to_surface_id='bridge',
            sides=['blue', 'red'], portal=((1006, 996), (1014, 996), (1014, 1004), (1006, 1004)))]
        c = SurfaceCatalog(c.assets, seal_catalog(value)); p = MapMotion(c, surface_transition_persistence=True)
        route = [dict(surface_id='ground', end_m=[1010, 1000]), dict(surface_id='bridge', end_m=[1020, 1000])]
        for energy, expected in (('0.04', 'ground'), ('0.088', 'bridge'), ('100', 'bridge')):
            m = p.plan(unit(energy=energy), route)
            self.assertEqual(m.progress['surface_id'], expected)
        m = p.plan(unit(), route); self.assertTrue(m.finished)
        u = unit(); u.update(position_mm=m.trace[-1], surface_id='bridge')
        self.assertEqual(p.plan(u, legs(1030, surface='bridge')).progress['surface_id'], 'bridge')

    def test_no_portal_can_approve_a_height_discontinuity(self):
        a = FixtureAssets({'p': quad(990, 990, 1200, 1010), 'q': quad(990, 990, 1200, 1010, height=2)})
        value = build_catalog(a)
        value['layers'] = [layer(a, support_faces={'p': [0, 1]}),
                           layer(a, surface_id='bridge', support_faces={'q': [0, 1]})]
        value['connections'] = [dict(accepted=True, from_surface_id='ground', to_surface_id='bridge',
            sides=['blue'], portal=((1000, 990), (1020, 990), (1020, 1010), (1000, 1010)))]
        p = MapMotion(SurfaceCatalog(a, seal_catalog(value)), surface_transition_persistence=True)
        with self.assertRaises(ServiceError):
            p.plan(unit(), [dict(surface_id='ground', end_m=[1010, 1000]), dict(surface_id='bridge', end_m=[1020, 1000])])

    def test_narrow_8m_support_passes_support_vehicle_axis_only(self):
        a = FixtureAssets({'p': quad(990, 996, 1200, 1004)})
        m = MapMotion(catalog(a)).plan(unit(kind='support'), legs())
        self.assertGreater(m.trace[-1]['x_mm'], 1000000)
        a = FixtureAssets({'p': quad(990, '996.001', 1200, 1004)})
        with self.assertRaises(ServiceError): MapMotion(catalog(a)).plan(unit(kind='support'), legs())

    def test_third_unit_rechecks_against_newly_stopped_enemy(self):
        p = MapMotion(catalog())
        units = [unit('a'), unit('b', 'red', x=1020), unit('c', 'red', x=1040)]
        state = dict(units=units, camps=[])
        m = p.motions(state, route_resolver=lambda u: legs(1100 if u['side'] == 'blue' else 900),
                      start_positive={'a', 'b', 'c'}, service_locked=set(), transitions={})
        positions = {i: F(v.progress['exact_position_m'][0]) for i, v in m.items()}
        self.assertGreater(positions['b']-positions['a'], 5)
        self.assertGreater(positions['c']-positions['a'], 5)

    def test_camp_passthrough_allows_crossing_stopping_and_leaving_both_sides(self):
        for side in ('blue', 'red'):
            with self.subTest(camp_side=side):
                mover = unit(x=1000)
                camp = dict(side=side, surface_id='ground', position_mm=dict(x_mm=1045000, z_mm=1000000, height_mm=0))
                state = dict(units=[mover], camps=[camp])
                before = deepcopy(state)
                planner = MapMotion(catalog(), camp_collisions=False)
                def run(target):
                    return planner.motions(state, route_resolver=lambda _: legs(target),
                        start_positive={'u'}, service_locked=set(), transitions=set())['u']
                # Cross the camp boundary, then stop inside it without an intent.
                first = run(1020)
                self.assertTrue(first.finished)
                self.assertEqual(state, before)
                mover['position_mm'] = first.trace[-1]
                mover['movement'] = None
                stopped = run(1020)
                self.assertEqual(stopped.trace[-1]['x_mm'], 1020000)
                mover['movement'] = dict(action='move')
                for target in (1040, 1060, 1080):
                    motion = run(target)
                    self.assertTrue(motion.finished)
                    mover['position_mm'] = motion.trace[-1]
                self.assertEqual(mover['position_mm']['x_mm'], 1080000)
                # An unrelated terrain/surface error is still rejected.
                mover['surface_id'] = 'unknown-layer'
                with self.assertRaises(ServiceError): run(1100)

    def test_camp_solid_also_blocks_start_and_thin_sweep(self):
        p = MapMotion(catalog()); u = unit()
        solid = dict(surface_id='ground', polygon=rectangle(1045, 1000, 30))
        m = p.plan(u, legs(), extra_solids=[solid])
        self.assertLess(F(m.progress['exact_position_m'][0]), 1012)
        solid['polygon'] = rectangle(1000, 1000, 30)
        with self.assertRaises(ServiceError): p.plan(u, legs(), extra_solids=[solid])

    def test_service_slope_ten_percent_is_inclusive(self):
        for grade, passes in ((F(1, 10), True), (F(1001, 10000), False)):
            a = FixtureAssets({'p': quad(990, 990, 1200, 1010, dx=grade)})
            c = catalog(a)
            if passes: c.footprint('ground', rectangle(1000, 1000, 4), side='blue', purpose='service')
            else:
                with self.assertRaises(ServiceError): c.footprint('ground', rectangle(1000, 1000, 4), side='blue', purpose='service')

    def test_budget_errors_do_not_turn_into_static_partial_approval(self):
        c = catalog(); original = c.footprint
        def fail(surface_id, polygon, **kwargs):
            if kwargs['purpose'] == 'move':
                raise ServiceError(503, 'BATTLE_SUPPORT_BUDGET_EXCEEDED', 'test budget')
            return original(surface_id, polygon, **kwargs)
        c.footprint = fail
        with self.assertRaises(ServiceError) as caught: MapMotion(c).plan(unit(), legs())
        self.assertEqual(caught.exception.code, 'BATTLE_SUPPORT_BUDGET_EXCEEDED')


class FullSyntheticDeploymentTests(TestCase):
    """End-to-end component validation on an EXPLICIT synthetic plane, never a real scene."""
    def setup_world(self, *, air=True):
        assets = FixtureAssets(); value = build_catalog(assets)
        ground = layer(assets, ground_surface='open', navigation_layer_id='ground')
        bridge = layer(assets, ground_surface='bridge', surface_id='synthetic-bridge',
                       navigation_layer_id='bridge-deck:bridge:ROAD_010')
        value['layers'] = [ground, bridge]
        value['camp_facilities'], value['capture_masks'] = [], []
        camps = []
        def mm(x, z): return dict(x_mm=x*1000, z_mm=z*1000, height_mm=0)
        for side, x, z in (('red', 1500, 4000), ('blue', 10300, 2000)):
            for index, kind in enumerate(('recon', 'ground', 'support')):
                zz = z+index*300
                camp = dict(camp_id=f'{side}-{kind}', side=side, unit_type=kind,
                    surface_id='ground', position_mm=mm(x, zz), service_surface_id='ground', service_anchor_mm=mm(x+50, zz))
                camps.append(camp)
                value['camp_facilities'].append(dict(camp_id=camp['camp_id'], camp_position_mm=camp['position_mm'],
                    surface_id='ground', service_surface_id='ground', service_anchor_mm=camp['service_anchor_mm'],
                    entrance_mm=mm(x+40, zz), dock_surface_id='ground', dock_position_mm=mm(x+70, zz),
                    paths=[[[x+40, zz], [x+50, zz]], [[x+40, zz], [x+70, zz]]]))
        for source in value['source_facts']['capture_points']:
            x, z = F(source['x']), F(source['z'])
            raw = rectangle(x, z, 1)
            value['capture_masks'].append(dict(point_id=source['point_id'], accepted=True,
                source_navigation_layer_id=source['navigation_layer_id'],
                surface_id='synthetic-bridge' if source['support'] == 'bridge_deck' else 'ground',
                polygons=[[[float(a), float(b)] for a, b in raw]]))
        catalog_value = SurfaceCatalog(assets, seal_catalog(value))
        checker = MapDeployment(catalog_value, dock_airspace_validator=(lambda _: True) if air else None)
        return checker, dict(camps=camps, units=[unit('blue', x=10500, z=2000), unit('red', 'red', x=1750, z=4000)])

    def test_full_synthetic_deployment_returns_true_unchanged_no_publish(self):
        checker, state = self.setup_world(); before = deepcopy(state)
        self.assertIs(checker.validate_deployment(state), True)
        self.assertEqual(state, before)
        self.assertFalse(checker.catalog.payload['acceptance']['production_ready'])

    def test_airspace_missing_refuses_even_complete_ground_geometry(self):
        checker, state = self.setup_world(air=False)
        with self.assertRaises(ServiceError) as caught: checker.validate_deployment(state)
        self.assertEqual(caught.exception.code, 'BATTLE_DOCK_AIRSPACE_UNRESOLVED')

    def test_wrong_effective_layer_or_capture_overlap_refuses(self):
        checker, state = self.setup_world(); value = checker.catalog.payload
        value['capture_masks'][4]['source_navigation_layer_id'] = 'ground'
        checker = MapDeployment(SurfaceCatalog(checker.catalog.assets, seal_catalog(value)))
        with self.assertRaises(ServiceError): checker.validate_deployment(state)

    def test_initial_friendly_overlap_forbidden_but_motion_does_not_block(self):
        checker, state = self.setup_world(); state['units'].append(deepcopy(state['units'][0]))
        state['units'][-1]['unit_id'] = 'another-blue'
        with self.assertRaises(ServiceError) as caught: checker.validate_deployment(state)
        self.assertEqual(caught.exception.code, 'BATTLE_DEPLOYMENT_OVERLAP')

    def test_local_generated_path_touching_neighbor_does_not_block_start(self):
        checker, state = self.setup_world()
        # Same 83.681m camp spacing as the reported real deployment. The 4m
        # access envelope touches the neighbor; the 3m dock remains clear.
        camp, neighbor = state['camps'][:2]
        neighbor['position_mm']['z_mm'] = camp['position_mm']['z_mm'] + 83681
        neighbor['service_anchor_mm']['z_mm'] = neighbor['position_mm']['z_mm']
        camp['service_anchor_mm'] = dict(camp['position_mm'],
            z_mm=camp['position_mm']['z_mm'] + 50000)
        value = deepcopy(checker.catalog.payload)
        value['camp_facilities'] = []
        checker = MapDeployment(SurfaceCatalog(checker.catalog.assets, seal_catalog(value)),
            dock_airspace_validator=lambda _: True, allow_derived_facilities=True)
        before = deepcopy(state)
        self.assertTrue(checker.validate_deployment(state))
        self.assertEqual(state, before)
        # Actual entity overlap must still fail in the relaxed local mode.
        state['units'][0]['position_mm'] = deepcopy(state['camps'][3]['position_mm'])
        with self.assertRaises(ServiceError) as caught:
            checker.validate_deployment(state)
        self.assertEqual(caught.exception.code, 'BATTLE_DEPLOYMENT_OVERLAP')

    def test_service_path_crossing_camp_refuses(self):
        checker, state = self.setup_world(); value = checker.catalog.payload
        facility = value['camp_facilities'][0]
        x, z = F(state['camps'][0]['position_mm']['x_mm'])/1000, F(state['camps'][0]['position_mm']['z_mm'])/1000
        facility['paths'][0].insert(1, [int(x), int(z)])
        checker = MapDeployment(SurfaceCatalog(checker.catalog.assets, seal_catalog(value)), dock_airspace_validator=lambda _: True)
        with self.assertRaises(ServiceError) as caught: checker.validate_deployment(state)
        self.assertEqual(caught.exception.code, 'BATTLE_CAMP_BLOCKS_ROUTE')


class RecordedMillimetreTests(TestCase):
    @staticmethod
    def segment(length, surface='road', start=F(1000)):
        return dict(length_mm=str(length), surface=surface, grade_squared='0',
                    path_start_m=[str(start), '1000'], path_end_m=[str(start+F(length)/1000), '1000'],
                    path_surface_id='ground', path_begin_seconds='0', path_end_seconds='5')

    def test_three_continuous_steps_bill_completed_mm_without_drift(self):
        p = MapMotion(catalog()); u = unit(); charges = []; progress = None
        for expected in (33333, 33333, 33334):
            m = p.plan(u, legs())
            self.assertEqual(sum(F(v['length_mm']) for v in m.segments), expected)
            self.assertEqual(m.progress['rule_position_mm'], m.trace[-1])
            charges.append(F(k.movement_cost('ground', m.segments)))
            u['energy'] = str(F(u['energy'])-charges[-1])
            u['position_mm'] = m.trace[-1]
            u['movement_progress'] = m.progress
        self.assertEqual(sum(charges), F(4, 5))
        self.assertEqual(u['position_mm']['x_mm'], 1100000)
        self.assertTrue(m.finished)
        self.assertEqual(m.progress['path_pending_segments'], [])

    def test_fifo_retains_surface_cost_when_mm_crosses_boundary(self):
        first = self.segment(F(1, 3))
        billed, tail, count = record_path_segments([first])
        self.assertEqual((billed, count), ((), 0))
        progress = dict(path_pending_segments=tail, recorded_distance_mm=count)
        self.assertEqual(pending_path_energy('ground', progress), F(1, 375000))
        billed, tail, count = record_path_segments([self.segment(F(2, 3), 'field')], progress)
        self.assertEqual(sum(F(v['length_mm']) for v in billed), 1)
        self.assertEqual([v['surface'] for v in billed], ['road', 'field'])
        self.assertEqual(k.movement_cost('ground', billed), '1/75000')
        self.assertEqual((tail, count), ([], 1))

    def test_internal_splits_never_repeat_rounding(self):
        whole = record_path_segments([self.segment(F(100000, 3))])
        split = record_path_segments([self.segment(F(100000, 9)) for _ in range(3)])
        self.assertEqual(whole[2], split[2])
        self.assertEqual(sum(F(v['length_mm']) for v in whole[1]), sum(F(v['length_mm']) for v in split[1]))
        self.assertEqual(k.movement_cost('ground', whole[0]), k.movement_cost('ground', split[0]))

    def test_interruption_discards_tail_but_keeps_completed_mm(self):
        billed, tail, count = record_path_segments([self.segment(F(10, 3))], interrupted=True)
        self.assertEqual(sum(F(v['length_mm']) for v in billed), 3)
        self.assertEqual((tail, count), ([], 3))

    def test_invalid_pending_cannot_bank_full_mm(self):
        with self.assertRaises(ServiceError):
            record_path_segments([], dict(path_pending_segments=[self.segment(1)]))

    def test_budget_cursor_cannot_override_authority_mm(self):
        p = MapMotion(catalog()); u = unit(); m = p.plan(u, legs())
        u.update(position_mm=m.trace[-1], movement_progress=m.progress)
        u['position_mm']['x_mm'] += 1
        with self.assertRaises(ServiceError): p.plan(u, legs())

    def test_exhausted_blocked_and_stopped_are_interrupted(self):
        p = MapMotion(catalog())
        for energy in ('0', '0.08'):
            m = p.plan(unit(energy=energy), legs())
            self.assertTrue(m.interrupted)
            self.assertFalse(m.finished)
            self.assertEqual(m.progress['path_pending_segments'], [])
            self.assertEqual(m.progress['exact_position_m'][0], str(F(m.trace[-1]['x_mm'], 1000)))
        u = unit(); m = p.plan(u, legs()); self.assertFalse(m.interrupted)
        u.update(position_mm=m.trace[-1], movement_progress=m.progress)
        stopped = p.plan(u, [])
        self.assertTrue(stopped.interrupted)
        self.assertEqual(stopped.progress['path_pending_segments'], [])


class LocalDecimetreTests(TestCase):
    def test_route_carries_subgrid_distance_and_clears_it_on_interruption(self):
        planner = MapMotion(catalog(), distance_quantum_mm=100)
        u = unit(); charges = []
        for expected in (33300, 33300, 33400):
            motion = planner.plan(u, legs())
            self.assertEqual(sum(F(v['length_mm']) for v in motion.segments), expected)
            self.assertTrue(all(p['x_mm'] % 100 == p['z_mm'] % 100 == 0 for p in motion.trace[1:]))
            cost = F(k.movement_cost('ground', motion.segments))
            charges.append(cost)
            u.update(energy=str(F(u['energy'])-cost), position_mm=motion.trace[-1],
                     movement_progress=motion.progress)
        self.assertTrue(motion.finished)
        self.assertEqual(u['position_mm']['x_mm'], 1100000)
        self.assertEqual(sum(charges), F(4, 5))
        self.assertEqual(motion.progress['path_pending_segments'], [])
        segment = RecordedMillimetreTests.segment
        billed, tail, count = record_path_segments([segment(F(499, 2))], quantum_mm=100, interrupted=True)
        self.assertEqual((sum(F(v['length_mm']) for v in billed), tail, count), (200, [], 200))

    def test_subgrid_split_retains_terrain_rates(self):
        segment = RecordedMillimetreTests.segment
        billed, tail, count = record_path_segments([segment(F(100, 3))], quantum_mm=100)
        self.assertEqual((billed, count), ((), 0))
        billed, tail, count = record_path_segments([segment(F(200, 3), 'field')],
            dict(path_pending_segments=tail, recorded_distance_mm=count), quantum_mm=100)
        self.assertEqual((tail, count), ([], 100))
        self.assertEqual([v['surface'] for v in billed], ['road', 'field'])
        self.assertEqual(F(k.movement_cost('ground', billed)), F(1, 750))

    def test_bounded_energy_charge_conserves_resources_across_coprime_lengths(self):
        from backend.tests.test_battle_kernel import fixture, unit as ledger_unit
        state = fixture(); u = ledger_unit(state, 'ground', 'blue')
        quantum = F(1, 1000000)
        denominators = [1000000007, 1000000009, 1000000033, 1000000087,
                        1000000093, 1000000097, 1000000103, 1000000123]
        for _ in range(60):
            for denominator in denominators:
                segments = [RecordedMillimetreTests.segment(100 + F(100000, denominator))]
                raw = F(k.movement_cost('ground', segments))
                cost = F(k.movement_cost('ground', segments, quantum=quantum))
                self.assertLessEqual(cost, raw)
                self.assertLess(raw-cost, quantum)
                before = F(u['energy']); consumed = F(state['battle_ledger']['blue']['consumed']['energy'])
                k.consume_energy(state, u, cost)
                self.assertEqual(before-F(u['energy']), F(state['battle_ledger']['blue']['consumed']['energy'])-consumed)
                k.validate(state)
        self.assertLessEqual(len(state['battle_ledger']['blue']['consumed']['energy']), 80)

    def test_quantization_precedes_protocol_serialization(self):
        # Individually legal terrain splits can produce an oversized exact sum.
        denominators = [1000000007, 1000000009, 1000000033, 1000000087,
                        1000000093, 1000000097, 1000000103, 1000000123]
        segments = [RecordedMillimetreTests.segment(100 + F(1, d), 'field') for d in denominators]
        with self.assertRaises(ServiceError) as caught:
            k.movement_cost('ground', segments)
        self.assertEqual(caught.exception.code, 'BATTLE_NUMBER_INVALID')
        cost = k.movement_cost('ground', segments, quantum=F(1, 1000000))
        self.assertGreater(F(cost), 0)
        self.assertLessEqual(len(cost), 80)

    def test_unrepresentable_midpoint_keeps_full_verified_sweep(self):
        from backend.battle_terrain import MAX_GEOMETRY_DENOMINATOR
        planner = MapMotion(catalog(), distance_quantum_mm=100)
        a = (F(1000), 1100 + F(1, 10**19+1))
        b = (F(1001), 1100 + F(1, 10**19+3))
        mid = tuple((v+w)/2 for v, w in zip(a, b))
        self.assertGreater(mid[1].denominator, MAX_GEOMETRY_DENOMINATOR)
        swept = planner._footprint(unit(), 'ground', a, b)
        self.assertIs(planner._midpoint_evidence(unit(), 'ground', mid, swept), swept)
        planner.distance_quantum_mm = 1
        with self.assertRaises(ServiceError) as error:
            planner._midpoint_evidence(unit(), 'ground', mid, swept)
        self.assertEqual(error.exception.code, 'BATTLE_GEOMETRY_INVALID')

    def test_local_domain_edge_stops_inside_without_unlocking_unverified_ground(self):
        c = catalog(verified_domains=[[[990, 990], [1017, 990], [1017, 1010], [990, 1010]]])
        planner = MapMotion(c, distance_quantum_mm=100)
        motion = planner.plan(unit(), legs())
        self.assertTrue(motion.interrupted)
        self.assertGreater(motion.trace[-1]['x_mm'], 1000000)
        self.assertLessEqual(motion.trace[-1]['x_mm'], 1014000)  # 3m full footprint
        with self.assertRaises(ServiceError) as error:
            planner.plan(unit(x=1020), legs())
        self.assertEqual(error.exception.code, 'BATTLE_SURFACE_OUTSIDE_VERIFIED_DOMAIN')

    def test_unsafe_coarse_snap_stops_without_spending_or_keeping_tail(self):
        from unittest.mock import patch
        planner = MapMotion(catalog(), distance_quantum_mm=100)
        u = unit(); before = deepcopy(u)
        plan = planner.details(u, legs())
        with patch.object(planner, '_footprint', side_effect=ServiceError(400, 'BATTLE_SLOPE_INVALID', 'blocked snap')):
            motion = planner.as_motion(plan)
        self.assertEqual(motion.trace, (u['position_mm'],))
        self.assertEqual(motion.segments, ())
        self.assertEqual(motion.budget_segments, ())
        self.assertTrue(motion.interrupted)
        self.assertEqual(motion.progress['path_pending_segments'], [])
        self.assertEqual(u, before)
        planner.distance_quantum_mm = 1
        with patch.object(planner, '_footprint', side_effect=ServiceError(400, 'BATTLE_SLOPE_INVALID', 'blocked snap')):
            with self.assertRaises(ServiceError):
                planner.as_motion(plan)

    def test_local_interpolation_evidence_has_bounded_denominators(self):
        segment = RecordedMillimetreTests.segment(F(200000, 3))
        segment['path_end_seconds'] = str(F(10**37+1, 10**37))
        # Inputs to record_path_segments are bounded by the local motion adapter.
        from backend.battle_map_motion import _bounded
        segment['path_end_seconds'] = str(_bounded(F(segment['path_end_seconds']), F(1, 1000000)))
        billed, tail, _ = record_path_segments([segment], quantum_mm=100)
        self.assertLessEqual(F(billed[-1]['path_end_seconds']).denominator, 1000000)
        self.assertTrue(all(F(v).denominator <= 1000 for v in billed[-1]['path_end_m']))
        self.assertLess(sum(F(v['length_mm']) for v in tail), 100)
