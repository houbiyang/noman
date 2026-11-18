"""隔离交互算法测试。此目录仅为测试，不登记场景或授予现地图验收。"""
from copy import deepcopy
from fractions import Fraction as F
from types import SimpleNamespace
from unittest import TestCase

from backend.battle_map_interactions import ForestIndex, Interactions
from backend.battle_execution import Motion


def rect(x0, z0, x1, z1):
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def unit(identity='a', side='blue', kind='recon', x=0, z=0, air='flying'):
    return dict(unit_id=identity, side=side, unit_type=kind, energy='10', durability='100', retired=False,
                air_state=air if kind == 'recon' else 'ground', surface_id='test',
                position_mm=dict(x_mm=x*1000, z_mm=z*1000, height_mm=0))


class ForestTests(TestCase):
    def test_inside_1000_inclusive_boundary_and_ground_unaffected(self):
        forest = ForestIndex([rect(-10, -10, 10, 10)])
        a, b = unit(x=10), unit('b', 'red', x=1010)
        self.assertTrue(forest.visible_range(a, b))
        b['position_mm']['x_mm'] += 1
        self.assertFalse(forest.visible_range(a, b))
        a['unit_type'] = 'ground'; b = unit('b', 'red', x=910)
        self.assertTrue(forest.visible_range(a, b))

    def test_connected_reentry_accumulates_and_disconnected_is_not_added(self):
        # 两根长条在射线上方连通；沿z=0射线中间虽离开森林，仍须累计。
        forest = ForestIndex([rect(100, -10, 500, 100), rect(700, -10, 1100, 100),
                              rect(100, 90, 1100, 110), rect(0, -10, 50, 10)])
        a, b = unit(x=60), unit('b', 'red', x=1000)
        self.assertEqual(forest.target_fraction((F(60), F(0)), (F(1000), F(0))), F(700, 940))
        self.assertFalse(forest.visible_range(a, b))

    def test_600_inclusive_and_overlap_union(self):
        forest = ForestIndex([rect(100, -1, 800, 1), rect(300, -1, 800, 1)])
        self.assertTrue(forest.visible_range(unit(), unit('b', 'red', x=700)))
        self.assertFalse(forest.visible_range(unit(), unit('b', 'red', x=F(700001, 1000))))
        self.assertEqual(forest.target_fraction((F(0), F(0)), (F(700), F(0))), F(6, 7))

    def test_outside_outside_no_absorption(self):
        self.assertTrue(ForestIndex([rect(1, -1, 1399, 1)]).visible_range(
            unit(), unit('b', 'red', x=1400)))

    def test_touching_at_single_point_merges(self):
        forest = ForestIndex([rect(1, 1, 3, 3), rect(3, 3, 5, 5)])
        self.assertEqual(len(set(forest.groups)), 1)
        self.assertTrue(forest.at((F(3), F(3))))

    def test_empty_explicit_forest_and_invalid_missing(self):
        self.assertTrue(ForestIndex([]).visible_range(unit(), unit('b', 'red', x=1400)))
        with self.assertRaises(Exception): ForestIndex(None)


class Terrain:
    capture_points = [dict(point_id='P-01')]
    def rule_anchor(self, u):
        p = u['position_mm']; return F(p['x_mm'], 1000), F(2), F(p['z_mm'], 1000)
    def capture_footprint_legal(self, u, p): return u['surface_id'] == 'test'
    def service_footprint_legal(self, u): return u['surface_id'] == 'test'
    def camp_service_legal(self, u): return u.get('legal', False)
    def service_segment_clear(self, a, b): return a['surface_id'] == b['surface_id']
    def dock_footprint_legal(self, u): return u['surface_id'] == 'test'
    def air_legal(self, u): return True
    def same_collision_layer(self, a, b): return a['surface_id'] == b['surface_id']


class Rays:
    def __init__(self): self.calls = []; self.block_source = None
    def cast(self, a, b):
        self.calls.append((a, b))
        return SimpleNamespace(mesh_clear=a[0] != self.block_source)


class InteractionTests(TestCase):
    def setUp(self):
        self.rays = Rays(); self.geometry = Interactions(Terrain(), self.rays, ForestIndex([]))

    def test_observer_does_not_grant_shooter_ray(self):
        scout, tank, enemy = unit(), unit('tank', kind='ground', x=10), unit('enemy', 'red', 'ground', x=20)
        self.rays.block_source = F(10)
        state = dict(units=[scout, tank, enemy])
        seen = self.geometry.observe(state, start_positive={'a', 'tank'}, stable_observers={'a', 'tank'})
        self.assertEqual(seen['blue'], {'enemy'})
        self.assertFalse(self.geometry.fire_clear(tank, enemy))
        self.assertEqual(self.rays.calls[-1][0][1], 2)

    def test_source_energy_state_and_start_qualification(self):
        for change in ('zero', 'docked', 'taking_off', 'landing', 'retired', 'not_positive', 'not_stable'):
            source, enemy = unit(), unit('enemy', 'red', x=100)
            start, stable = {'a'}, {'a'}
            if change == 'zero': source['energy'] = '0'
            elif change == 'retired': source['retired'] = True
            elif change == 'not_positive': start = set()
            elif change == 'not_stable': stable = set()
            else: source['air_state'] = change
            self.assertEqual(self.geometry.observe(dict(units=[source, enemy]),
                start_positive=start, stable_observers=stable)['blue'], set(), change)

    def test_shoot_range_target_state_inclusive(self):
        a, b = unit(kind='ground'), unit('b', 'red', 'ground', x=600)
        self.assertTrue(self.geometry.fire_clear(a, b))
        b['position_mm']['x_mm'] += 1
        self.assertFalse(self.geometry.fire_clear(a, b))
        b = unit('b', 'red', x=800)
        self.assertTrue(self.geometry.fire_clear(a, b))
        b['air_state'] = 'docked'
        self.assertFalse(self.geometry.fire_clear(a, b))

    def test_service_same_layer_distance_and_recon_docked(self):
        a, b = unit(kind='support'), unit('b', kind='ground', x=100)
        self.assertTrue(self.geometry.service_clear(a, b))
        b['surface_id'] = 'bridge'
        self.assertFalse(self.geometry.service_clear(a, b))
        b = unit('b', x=100)
        self.assertFalse(self.geometry.service_clear(a, b))
        b['air_state'] = 'docked'
        self.assertTrue(self.geometry.service_clear(a, b))
        b['position_mm']['x_mm'] += 1
        self.assertFalse(self.geometry.service_clear(a, b))

    def test_capture_leaves_and_returns_not_resident_wrong_layer_not_present(self):
        a = unit(kind='ground'); state = dict(units=[a]); old = deepcopy(state)
        trace = (a['position_mm'], dict(a['position_mm'], x_mm=1000), a['position_mm'])
        present, resident = self.geometry.capture(state, old, {'a': Motion(trace)})
        self.assertEqual(present['P-01']['blue'], {'a'})
        self.assertEqual(resident['P-01']['blue'], set())
        present, resident = self.geometry.capture(state, old, {'a': Motion((a['position_mm'],))})
        self.assertEqual(resident['P-01']['blue'], {'a'})
        a['surface_id'] = 'bridge'
        self.assertEqual(self.geometry.capture(state, old, {'a': Motion(trace)})[0]['P-01']['blue'], set())

    def test_landing_enemy_footprint_and_friends_layers(self):
        a, enemy = unit(), unit('b', 'red', 'support', x=6)
        self.assertFalse(self.geometry.landing_clear(a, dict(units=[a, enemy])))
        enemy['side'] = 'blue'
        self.assertTrue(self.geometry.landing_clear(a, dict(units=[a, enemy])))
        enemy['side'] = 'red'; enemy['surface_id'] = 'bridge'
        self.assertTrue(self.geometry.landing_clear(a, dict(units=[a, enemy])))
        enemy['surface_id'] = 'test'; enemy['position_mm']['x_mm'] = 6001
        self.assertTrue(self.geometry.landing_clear(a, dict(units=[a, enemy])))
