"""工作台3隔离集成：显式合成已准许层 != 实地图/真实桥头验收。

只组合真实 MapMotion 与 Executor；不注册生产场景，不修改用户数据库。
"""
from copy import deepcopy
from fractions import Fraction as F
import json
from unittest import TestCase

from backend import battle_kernel as k
from backend.battle_execution import Executor
from backend.battle_map_motion import MapMotion
from backend.battle_map_surfaces import SurfaceCatalog, seal_catalog, rectangle
from backend.tests.test_battle_execution import FixtureGeometry, state_fixture
from backend.tests.test_battle_kernel import unit
from backend.tests.test_battle_map_components import catalog


def two_layers(*, blocked=False):
    base = catalog()
    payload = base.payload
    other = deepcopy(payload['layers'][0])
    other['surface_id'] = 'fixture-other'
    if blocked:
        other['solids'] = [dict(polygon=rectangle(1010, 1000, 1))]
    payload['layers'].append(other)
    payload['connections'] = [dict(accepted=True, from_surface_id='ground',
        to_surface_id='fixture-other', sides=['blue'], portal=rectangle(1001, 1000, 10))]
    payload = json.loads(json.dumps(payload, default=lambda v: int(v) if v.denominator == 1 else str(v)))
    return SurfaceCatalog(base.assets, seal_catalog(payload))


class PlannedFixtureGeometry(FixtureGeometry):
    """其余单位/交互为显式协议夹具，只对指定单位调用真实地面规划器。"""
    def __init__(self, layers, route):
        super().__init__()
        self.catalog, self.route = layers, route
        self.planner = MapMotion(layers, surface_transition_persistence=True)
        self.seen = []
        self.mover = self.planned_mover

    def planned_mover(self, state, result, kwargs):
        u = unit(state)
        self.seen.append(deepcopy(u))
        # 静止也经真实规划器，不能用协议默认Motion掩盖余数清除缺陷。
        result[u['unit_id']] = self.planner.plan(u, self.route if u.get('movement') else [])
        return result


def moving_state():
    state = state_fixture()
    for u in state['units']:
        u['surface_id'] = 'ground'
    unit(state)['movement'] = dict(action='move', position=dict(x='11', y='10'))
    return state


def apply_motion(executor, state, seconds=5):
    return executor._motion(state, {u['unit_id'] for u in state['units'] if k.alive(u)},
                            set(), set(), [], seconds)


class ManualSurfacePersistenceTests(TestCase):
    def test_completed_transition_survives_progress_cleanup_and_next_action(self):
        state = moving_state()
        geometry = PlannedFixtureGeometry(two_layers(), [
            dict(surface_id='ground', end_m=[1001, 1000]),
            dict(surface_id='fixture-other', end_m=[1002, 1000])])
        executor = Executor(geometry)
        apply_motion(executor, state)
        moved = unit(state)
        self.assertEqual(moved['surface_id'], 'fixture-other')
        self.assertEqual(moved['position_mm'], dict(x_mm=1002000, z_mm=1000000, height_mm=0))
        self.assertNotIn('movement_progress', moved)
        self.assertIsNone(moved['movement'])
        moved['movement'] = dict(action='move', position=dict(x='12', y='10'))
        geometry.route = [dict(surface_id='fixture-other', end_m=[1003, 1000])]
        apply_motion(executor, state, 10)
        self.assertEqual(geometry.seen[-1]['surface_id'], 'fixture-other')
        self.assertEqual(unit(state)['position_mm']['x_mm'], 1003000)

    def test_blocked_after_portal_records_actual_partial_layer_and_position(self):
        state = moving_state()
        geometry = PlannedFixtureGeometry(two_layers(blocked=True), [
            dict(surface_id='ground', end_m=[1001, 1000]),
            dict(surface_id='fixture-other', end_m=[1020, 1000])])
        apply_motion(Executor(geometry), state)
        u = unit(state)
        self.assertEqual(u['surface_id'], 'fixture-other')
        assert_grid_origin(self, u)
        self.assertNotIn('movement_progress', u)
        x, z = F(u['position_mm']['x_mm'], 1000), F(u['position_mm']['z_mm'], 1000)
        self.assertGreater(x, 1001)
        self.assertLess(x, 1006)  # 半径+1米余量：不能停进墙体。
        self.assertEqual(round(geometry.catalog.height(u['surface_id'], (x, z))*1000),
                         u['position_mm']['height_mm'])


def assert_grid_origin(test, u):
    """中断/到达清两类余数；新路线只能以规则毫米点起步。"""
    expected = [F(u['position_mm'][key], 1000) for key in ('x_mm', 'z_mm')]
    test.assertNotIn('movement_progress', u)
    test.assertNotIn('geometry_position', u)
    return expected


class ManualMillimeterBudgetPersistenceTests(TestCase):
    def setup_case(self, route=None):
        state = moving_state()
        geometry = PlannedFixtureGeometry(catalog(), route or [dict(surface_id='ground', end_m=[1100, 1000])])
        return state, geometry, Executor(geometry)

    def advance(self, executor, state):
        after, _ = executor.step(state)
        return after

    def adopt(self, executor, state, action):
        from backend.tests.test_battle_execution import order
        movement = dict(action=action)
        if action == 'move':
            movement['position'] = dict(x='11', y='10')
        executor._adopt(state, dict(blue=[order(state, movement=movement)], red=[]))

    def assert_same_budget_result(self, left, right):
        a, b = unit(left), unit(right)
        self.assertEqual(a['position_mm'], b['position_mm'])
        self.assertEqual(a['energy'], b['energy'])
        self.assertEqual(left['battle_ledger'], right['battle_ledger'])

    def test_same_uninterrupted_route_internal_segments_same_ten_second_budget(self):
        continuous, _, first = self.setup_case()
        split, _, second = self.setup_case([
            dict(surface_id='ground', end_m=[1050, 1000]),
            dict(surface_id='ground', end_m=[1100, 1000])])
        for _ in range(2):
            continuous = self.advance(first, continuous)
            split = self.advance(second, split)
        self.assert_same_budget_result(continuous, split)
        self.assertLessEqual(abs(F(unit(split)['position_mm']['x_mm'], 1000) - F(3200, 3)), F(1, 1000))

    def test_continuous_json_restore_does_not_reset_route_budget_or_cost(self):
        reference, _, first = self.setup_case()
        restored, _, second = self.setup_case()
        reference = self.advance(first, reference)
        restored = self.advance(second, restored)
        self.assertTrue(unit(restored).get('movement_progress'))
        saved = deepcopy(unit(restored)['movement_progress'])
        restored = json.loads(json.dumps(restored))
        self.assertEqual(unit(restored)['movement_progress'], saved)
        for _ in range(2):
            reference = self.advance(first, reference)
            restored = self.advance(second, restored)
        self.assert_same_budget_result(reference, restored)

    def test_stop_clears_fraction_before_json_restore_and_new_move(self):
        state, geometry, executor = self.setup_case()
        state = self.advance(executor, state)
        stopped_position = deepcopy(unit(state)['position_mm'])
        self.adopt(executor, state, 'stop')
        self.assertNotIn('movement_progress', unit(state))
        assert_grid_origin(self, unit(state))
        state = self.advance(executor, state)
        self.assertEqual(unit(state)['position_mm'], stopped_position)
        self.assertNotIn('movement_progress', unit(state))
        assert_grid_origin(self, unit(state))
        state = json.loads(json.dumps(state))
        self.adopt(executor, state, 'move')
        state = self.advance(executor, state)
        seen = geometry.seen[-1]
        self.assertEqual(seen['position_mm'], stopped_position)
        assert_grid_origin(self, seen)
        self.assertEqual(state['battle_ledger']['elapsed_seconds'], 15)

    def test_replacement_order_cancels_old_route_remainder(self):
        state, geometry, executor = self.setup_case()
        state = self.advance(executor, state)
        self.assertTrue(unit(state).get('movement_progress'))
        self.adopt(executor, state, 'move')
        self.assertNotIn('movement_progress', unit(state))
        assert_grid_origin(self, unit(state))
        state = self.advance(executor, state)
        assert_grid_origin(self, geometry.seen[-1])

    def test_arrival_clears_budget_before_new_route(self):
        state, geometry, executor = self.setup_case()
        initial_energy = k.number(unit(state)['energy'])
        state = self.advance(executor, state)
        state = self.advance(executor, state)
        state = self.advance(executor, state)
        u = unit(state)
        self.assertIsNone(u['movement'])
        self.assertNotIn('movement_progress', u)
        self.assertEqual(u['position_mm']['x_mm'], 1100000)
        # 真实走完100米，按记录增量收费，不按3个连续参考预算重复收费。
        self.assertEqual(initial_energy-k.number(u['energy']), F(k.profiles()['ground']['energy_per_km'], 10))
        assert_grid_origin(self, u)
        geometry.route = [dict(surface_id='ground', end_m=[1200, 1000])]
        self.adopt(executor, state, 'move')
        state = self.advance(executor, json.loads(json.dumps(state)))
        assert_grid_origin(self, geometry.seen[-1])
        self.assertLessEqual(abs(F(unit(state)['position_mm']['x_mm'], 1000)-F(3400, 3)), F(1, 1000))

    def test_real_coordinator_millimeter_destination_without_test_side_conversion(self):
        from backend.battle_map_geometry import MapMotionCoordinator
        state, geometry, _ = self.setup_case()
        u = unit(state)
        u['movement'] = dict(action='move', position=geometry.display_position(
            dict(x_mm=1000001, z_mm=1000000, height_mm=0)))
        state['units'], state['camps'] = [u], []
        restored = json.loads(json.dumps(state))
        coordinator = MapMotionCoordinator(geometry.catalog, None, geometry.position)
        motion = coordinator.motions(restored, start_positive={u['unit_id']},
                                     service_locked=set(), transitions=set())[u['unit_id']]
        self.assertTrue(motion.finished)
        self.assertEqual(motion.trace[-1]['x_mm'], 1000001)


class CoordinatedFixtureGeometry(FixtureGeometry):
    """仅指定单位用完整真实协调器；空域/层来源为显式合成。"""
    def __init__(self, kind, route):
        super().__init__()
        from backend.battle_map_geometry import MapMotionCoordinator
        from backend.tests.test_battle_map_geometry import terrain
        area = terrain()
        self.catalog = area.catalog
        self.coordinator = MapMotionCoordinator(area.catalog, area, self.position,
                                                route_resolver=lambda current: route)
        def mover(snapshot, result, kwargs):
            result.update(self.coordinator.motions(dict(units=[unit(snapshot, kind)], camps=[]), **kwargs))
            return result
        self.mover = mover


class ManualGroundAirRecordedBudgetTests(TestCase):
    """真实协调器→执行器→JSON；层/空域仍为明确合成夹具。"""
    def chain(self, kind='ground'):
        state = moving_state()
        u = unit(state, kind)
        u.update(movement=dict(action='move', position=dict(x='50', y='10')), surface_id='ground')
        if kind == 'recon':
            u['air_state'] = 'flying'
            u['position_mm']['height_mm'] = 30000
        geometry = CoordinatedFixtureGeometry(kind, [dict(surface_id='ground', end_m=[5000, 1000])])
        return state, Executor(geometry), u['unit_id']

    def test_ground_and_air_recorded_increment_time_cost_and_conservation(self):
        for kind, expected, cost_per_mm in (
                ('ground', [33333, 33333, 33334], F(8, 1000000)),
                ('recon', [66666, 66667, 66667], F(5, 1000000))):
            with self.subTest(kind=kind):
                state, executor, identity = self.chain(kind)
                start_energy = F(unit(state, kind)['energy'])
                start_consumed = F(state['battle_ledger']['blue']['consumed']['energy'])
                total = 0
                for index, amount in enumerate(expected):
                    motions, _, _ = apply_motion(executor, state, (index+1)*5)
                    motion = motions[identity]
                    lengths = sum(F(s['length_mm']) for s in motion.segments)
                    self.assertEqual(lengths, amount)
                    total += amount
                    self.assertEqual(motion.progress['recorded_distance_mm'], total)
                    pending = sum(F(s['length_mm']) for s in motion.progress['path_pending_segments'])
                    self.assertGreaterEqual(pending, 0)
                    self.assertLess(pending, 1)
                    self.assertEqual(motion.progress['rule_position_mm'], motion.trace[-1])
                    self.assertEqual(sum(F(s['length_mm']) for s in motion.budget_segments),
                                     F(100000 if kind == 'ground' else 200000, 3))
                    # 以总册率独立计算，不用生产movement_cost当测试期望。
                    self.assertEqual(start_energy-F(unit(state, kind)['energy']), total*cost_per_mm)
                    self.assertEqual(F(state['battle_ledger']['blue']['consumed']['energy'])-start_consumed,
                                     total*cost_per_mm)
                    k.validate(state)
                    state = json.loads(json.dumps(state))
                self.assertEqual(unit(state, kind)['position_mm']['x_mm'], 1000000+total)

    def test_ground_and_air_exhaustion_discards_unrecorded_tail(self):
        from backend.tests.test_battle_kernel import spend
        for kind in ('ground', 'recon'):
            with self.subTest(kind=kind):
                state, executor, identity = self.chain(kind)
                u = unit(state, kind)
                spend(state, u, 'energy', F(u['energy'])-F(1, 100000))
                motions, _, _ = apply_motion(executor, state)
                self.assertTrue(motions[identity].interrupted)
                self.assertNotIn('movement_progress', u)
                self.assertNotIn('geometry_position', u)
                self.assertEqual(u['surface_id'], 'ground')
                self.assertGreaterEqual(F(u['energy']), 0)
                k.validate(state)

    def test_landing_discards_live_air_tail_without_moving_authority(self):
        state, executor, identity = self.chain('recon')
        apply_motion(executor, state)
        u = unit(state, 'recon')
        self.assertTrue(u['movement_progress']['path_pending_segments'])
        before = deepcopy(u['position_mm'])
        u['air_state'] = 'landing'
        motions, _, _ = executor._motion(state, {v['unit_id'] for v in state['units']}, set(),
                                         {identity}, [], 10)
        self.assertTrue(motions[identity].interrupted)
        self.assertEqual(u['position_mm'], before)
        self.assertEqual(u['surface_id'], 'ground')
        self.assertNotIn('movement_progress', u)
        self.assertNotIn('geometry_position', u)

    def test_legacy_submillimeter_origin_is_rejected_not_interaction_authority(self):
        from backend.runtime import ServiceError
        state, geometry, executor = ManualMillimeterBudgetPersistenceTests().setup_case()
        unit(state)['geometry_position'] = dict(catalog_sha256=geometry.catalog.digest,
            surface_id='ground', exact_position_m=['3000001/3000', '1000'])
        with self.assertRaises(ServiceError) as caught:
            apply_motion(executor, state)
        self.assertEqual(caught.exception.code, 'BATTLE_ROUTE_STALE')

    def test_diagonal_air_json_restore_after_integral_length_empty_tail(self):
        state, executor, identity = self.chain('recon')
        executor.geometry = CoordinatedFixtureGeometry('recon', [dict(surface_id='ground', end_m=[4000, 5000])])
        totals = []
        for step in range(4):
            motions, _, _ = apply_motion(executor, state, (step+1)*5)
            totals.append(motions[identity].progress['recorded_distance_mm'])
            if step == 2:
                self.assertEqual(motions[identity].progress['path_pending_segments'], [])
                # 5km方向是3:4，整200m弧长恰好也落整数mm；另下测试走无理方向。
            state = json.loads(json.dumps(state))
        self.assertEqual(totals, [66666, 133333, 200000, 266666])
        state, executor, identity = self.chain('recon')
        executor.geometry = CoordinatedFixtureGeometry('recon', [dict(surface_id='ground', end_m=[5000, 3000])])
        for step in range(4):
            motions, _, _ = apply_motion(executor, state, (step+1)*5)
            if step == 2:
                progress = unit(state, 'recon')['movement_progress']
                self.assertEqual(progress['path_pending_segments'], [])
                self.assertTrue(any((F(v)*1000).denominator != 1 for v in progress['exact_position_m']))
            state = json.loads(json.dumps(state))
        self.assertEqual(unit(state, 'recon')['movement_progress']['recorded_distance_mm'], 266666)
