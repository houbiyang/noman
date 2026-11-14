"""低能源保护必须使用本方信息，未知服务成本不能放行深入移动。"""

import unittest

from backend import fivepoint_engine as engine
from backend.fivepoint_parameters import number
from backend.tests.test_fivepoint_engine import FixtureGeometry, fixture, plan, step, location


class EnergyGuardTests(unittest.TestCase):
    """验证阈值、整段预算、未知路线和正常静止行为。"""

    def moving(self, energy):
        """建立余额守恒的单坦克移动场景。"""
        state = fixture(('ground',))
        engine.spend(state, state['units'][0], 'energy', 100 - energy)
        return plan(state, {'red-0': dict(action='move_attack', destination=location(100), target_id='blue-0')})

    def test_at_threshold_waits_but_stationary_fire_remains_legal(self):
        state = step(self.moving(30))
        self.assertEqual(state['units'][0]['position'], location(0))
        self.assertEqual(number(state['units'][0]['energy']), 30)
        self.assertEqual(state['units'][0]['ammo'], 59)
        self.assertEqual(state['units'][0]['energy_guard']['reason'], 'low_energy')

    def test_moving_shot_preserves_threshold_reserve(self):
        state = step(self.moving(30.1))
        self.assertGreater(number(state['units'][0]['position']['xy'][0]), 0)
        self.assertEqual(number(state['units'][0]['energy']), 30)

    def test_unknown_cost_waits_and_only_receives_private_view(self):
        testcase = self
        class UnknownCost(FixtureGeometry):
            def service_energy_cost(self, unit, own_view):
                testcase.assertTrue(all(item['side'] == unit['side'] for item in own_view['units']))
                testcase.assertNotIn('ledger', own_view)
                return None
        state = step(self.moving(100), UnknownCost())
        self.assertEqual(state['units'][0]['position'], location(0))
        self.assertEqual(state['units'][0]['energy_guard']['reason'], 'service_cost_unknown')

    def test_cost_over_full_energy_is_not_clamped(self):
        class ExpensiveService(FixtureGeometry):
            def service_energy_cost(self, unit, own_view):
                return 110
        state = step(self.moving(100), ExpensiveService())
        self.assertEqual(state['units'][0]['energy_guard']['trigger'], '120')
        self.assertEqual(state['units'][0]['position'], location(0))

    def test_unknown_or_high_cost_along_route_rejects_without_spending(self):
        for estimate in (None, 100):
            class RouteCost(FixtureGeometry):
                def movement_service_energy_cost(self, unit, movement, own_view):
                    return estimate
            state = step(self.moving(100), RouteCost())
            self.assertEqual(state['units'][0]['position'], location(0))
            self.assertEqual(number(state['units'][0]['energy']), 100)


if __name__ == '__main__':
    unittest.main()
