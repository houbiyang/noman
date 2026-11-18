"""Exact reachable-prefix budgets; no database, HTTP or altered rule/map data."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from unittest import TestCase

from backend import battle_kernel as k
from backend.battle_map_motion import MapMotion, arc_length, position_m
from backend.battle_map_surfaces import SurfaceCatalog, rectangle
from backend.battle_terrain_assets import TerrainAssets
from backend.runtime import ServiceError
from backend.tests.test_battle_map_components import catalog, unit, legs


class ReachablePrefixBudgetTests(TestCase):
    @staticmethod
    def striped(start, spacing=1, count=70):
        # Same-surface regions still have exact boundary events. They exercise
        # geometric complexity without inventing new terrain/rule semantics.
        return catalog(regions=[dict(surface='road', polygon=rectangle(start+i*spacing, 1000, F(1, 100)))
                                for i in range(count)])

    def test_remote_events_do_not_exhaust_current_step(self):
        c = self.striped(1100)
        planner = MapMotion(c)
        with self.assertRaises(ServiceError) as error:
            planner._breaks('ground', (F(1000), F(1000)), (F(2000), F(1000)), 3)
        self.assertEqual(error.exception.code, 'BATTLE_MOTION_BUDGET_EXCEEDED')
        u = unit(); before = deepcopy(u)
        result = planner.plan(u, legs(2000))
        self.assertEqual(u, before)
        self.assertFalse(result.finished or result.interrupted)
        self.assertEqual(sum(F(s['length_mm']) for s in result.segments), 33333)
        self.assertEqual(F(result.progress['leg_length_m']), 1000)
        self.assertEqual(F(result.progress['distance_m']), F(100, 3))

    def test_dense_reachable_events_stop_at_proven_boundary(self):
        c = self.striped(F(1001), spacing=F(1, 10))
        planner = MapMotion(c)
        with self.assertRaises(ServiceError):
            planner._breaks('ground', (F(1000), F(1000)), (F(1030), F(1000)), 3)
        result = planner.plan(unit(), legs(2000))
        self.assertTrue(result.interrupted)
        self.assertFalse(result.finished)
        self.assertLessEqual(len(result.trace), 128)
        self.assertGreater(result.trace[-1]['x_mm'], 1000000)
        self.assertLess(result.trace[-1]['x_mm'], 1010000)
        self.assertTrue(result.progress['position_only'])
        self.assertEqual(result.progress['path_pending_segments'], [])
        c.footprint('ground', rectangle(*position_m(dict(position_mm=result.trace[-1])), 3),
                    side='blue', purpose='stop', kind='ground')

    def test_many_short_legs_stop_before_trace_budget_without_teleport(self):
        c = self.striped(F(1004), spacing=F(1, 100), count=50)
        route = [dict(surface_id='ground', end_m=[F('1004.6') if i % 2 == 0 else F('1004.1'), F(1000)])
                 for i in range(32)]
        result = MapMotion(c).plan(unit(), route)
        self.assertTrue(result.interrupted)
        self.assertFalse(result.finished)
        self.assertLessEqual(len(result.trace), 128)
        self.assertLessEqual(result.trace[-1]['x_mm'], 1004600)

    def test_diagonal_horizon_is_not_a_new_leg_or_rounding_reset(self):
        planner = MapMotion(catalog()); u = unit(); route = legs(2000, 2000)
        length = arc_length((F(1000), F(1000)), (F(2000), F(2000)))
        for _ in range(3):
            result = planner.plan(u, route)
            u.update(position_mm=result.trace[-1], movement_progress=result.progress)
        self.assertEqual(F(result.progress['leg_length_m']), length)
        self.assertEqual(F(result.progress['distance_m']), 100)
        self.assertEqual(result.progress['recorded_distance_mm'], 100000)
        self.assertFalse(result.finished or result.interrupted)

    def test_horizon_does_not_skip_wall_or_change_energy_reservation(self):
        c = catalog(solids=[dict(polygon=rectangle(1015, 1000, F(1, 1000)))])
        planner = MapMotion(c)
        result = planner.plan(unit(), legs(9000))
        self.assertTrue(result.interrupted)
        self.assertLess(F(result.progress['exact_position_m'][0]), F('1011.999'))
        result = MapMotion(catalog()).plan(unit(energy='0.08'), legs(9000), moving_attack=True)
        self.assertEqual(sum(F(s['length_mm']) for s in result.segments), 8000)
        self.assertTrue(result.interrupted)

    def test_pending_submillimeter_pieces_reserve_trace_slots(self):
        planner = MapMotion(catalog()); u = unit(); route = legs(9000)
        first = planner.plan(u, route)
        u.update(position_mm=first.trace[-1], movement_progress=deepcopy(first.progress))
        raw = u['movement_progress']['path_pending_segments'][0]
        begin, end = [tuple(F(v) for v in raw[key]) for key in ('path_start_m', 'path_end_m')]
        pending = []
        for i in range(128):
            item = deepcopy(raw)
            item['length_mm'] = str(F(raw['length_mm'])/128)
            item['path_start_m'] = [str(a+(b-a)*F(i, 128)) for a, b in zip(begin, end)]
            item['path_end_m'] = [str(a+(b-a)*F(i+1, 128)) for a, b in zip(begin, end)]
            pending.append(item)
        u['movement_progress']['path_pending_segments'] = pending
        result = planner.plan(u, route)
        self.assertTrue(result.interrupted)
        self.assertEqual(len(result.trace), 1)
        self.assertEqual(result.trace[-1], u['position_mm'])
        self.assertEqual(result.progress['path_pending_segments'], [])

    def test_rule_horizon_bounds_every_supported_rate(self):
        terrain = k._parameters()['terrain']
        for kind in ('ground', 'support'):
            horizon = MapMotion._travel_limit(kind, F(5))
            for surface, factor in terrain['surface_factors'][kind].items():
                if factor is None:
                    continue
                for band in terrain['slope_bands']:
                    if band[kind] is None:
                        continue
                    rate = F(k.profiles()[kind]['speed_m_per_minute'])*F(str(factor))*F(str(band[kind]))
                    self.assertGreaterEqual(horizon, rate/12)


class CurrentMapRetargetBudgetTests(TestCase):
    def test_actual_first_round_endpoint_can_retarget_batch_two_for_60_seconds(self):
        # Read the saved round-1 endpoint only. This is an explicit component
        # regression, not an HTTP/full-state replay or a new acceptance claim.
        root = Path(__file__).resolve().parents[2]
        progress = root/'.local/manual-completion-20261118/capture-http-v1/progress.json'
        route_path = root/'.local/manual-geometry-20261117/local-blue-p03-new-deployment-route-v2.json'
        layout_path = root/'.local/manual-geometry-20261117/local-stop-safe-deployment-v2.json'
        if not all(p.exists() for p in (progress, route_path, layout_path)):
            self.skipTest('local frozen reproduction artifacts are not installed')
        c = SurfaceCatalog(TerrainAssets.current(), json.loads((root/'backend/battle-map-local-surfaces-v1.json').read_bytes()))
        planner = MapMotion(c)
        saved = json.loads(progress.read_bytes())['rounds'][0]['unit']
        u = dict(unit(), unit_id='B-G01', surface_id=saved['surface_id'],
                 position_mm=saved['position_mm'], energy=saved['battle_values']['energy'])
        route = json.loads(route_path.read_bytes())['api_route']
        batch = route['batches'][1]
        begin, end = batch['target_indices']
        targets = [dict(surface_id=u['surface_id'], end_m=[F(t['position_mm']['x_mm'], 1000),
                   F(t['position_mm']['z_mm'], 1000)]) for t in route['targets'][begin:end+1]]
        camps = json.loads(layout_path.read_bytes())['validation_state']['camps']
        solids = [dict(surface_id=p['surface_id'], polygon=rectangle(*position_m(p), 30)) for p in camps]
        with self.assertRaises(ServiceError) as error:
            planner._breaks(u['surface_id'], position_m(u), tuple(targets[0]['end_m']), 3)
        self.assertEqual(error.exception.code, 'BATTLE_MOTION_BUDGET_EXCEEDED')
        before = deepcopy(u)
        for _ in range(12):
            result = planner.plan(u, targets, extra_solids=solids)
            self.assertFalse(result.interrupted)
            u.update(position_mm=deepcopy(result.trace[-1]), movement_progress=result.progress,
                     energy=str(F(u['energy'])-F(k.movement_cost('ground', result.segments))))
        self.assertLess(u['position_mm']['x_mm'], before['position_mm']['x_mm'])
        self.assertEqual(u['position_mm']['z_mm'], before['position_mm']['z_mm'])
        self.assertLess(F(u['energy']), F(before['energy']))
