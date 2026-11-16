"""仅结构验收；不能把有限桥面检查伪称真实坡度/服务面验收。"""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from backend import battle_scenario
from backend.runtime import ServiceError


class BattleScenarioTests(TestCase):
    def test_user_confirmed_points_and_round_limits(self):
        value = battle_scenario.definition()
        self.assertEqual([p['point_id'] for p in value['capture_points']], [f'P-{i:02d}' for i in range(1, 6)])
        self.assertEqual((value['map']['width_m'], value['map']['length_m']), (12000, 12000))
        self.assertEqual((value['round_seconds'], value['maximum_rounds']), (60, 60))
        self.assertTrue(value['asymmetric_allowed'])
        self.assertFalse(value['migrate_existing_games'])
        self.assertTrue(value['mode_frozen_at_start'])

    def test_draft_blocks_unverified_terrain_and_executor(self):
        acceptance = battle_scenario.definition()['acceptance']
        self.assertEqual(acceptance['status'], 'draft_not_playable')
        self.assertTrue(acceptance['structural_checks_passed'])
        for key in ('terrain_masks_validated', 'ground_and_support_routes_validated',
                    'bridge_actual_grade_validated', 'service_surface_isolation_validated'):
            self.assertFalse(acceptance[key])
        self.assertIn('rulebook_executor', acceptance['blocks'])

    def test_content_addressed_identity_is_stable_and_does_not_mutate(self):
        value = battle_scenario.definition(); before = deepcopy(value)
        identity = battle_scenario.identity(value)
        self.assertEqual(identity, battle_scenario.identity(battle_scenario.definition()))
        value['maximum_rounds'] = 59
        self.assertNotEqual(identity, battle_scenario.identity(value))
        self.assertEqual(before, battle_scenario.definition())

    def test_changed_point_id_cannot_silently_replace_confirmed_layout(self):
        points = battle_scenario.definition()['capture_points']; points[0]['point_id'] = 'CAPTURE_A'
        with patch('backend.battle_scenario.capture_map.objectives', return_value=points):
            with self.assertRaises(ServiceError) as caught: battle_scenario.definition()
        self.assertEqual(caught.exception.code, 'BATTLE_POINT_LAYOUT_CHANGED')

    def test_overlap_or_out_of_bounds_blocks_draft(self):
        for change in ('overlap', 'outside'):
            points = battle_scenario.definition()['capture_points']
            if change == 'overlap': points[1].update(x=points[0]['x'], z=points[0]['z'])
            else: points[0]['x'] = 0
            with patch('backend.battle_scenario.capture_map.objectives', return_value=points):
                with self.assertRaises(ServiceError) as caught: battle_scenario.definition()
            self.assertEqual(caught.exception.code, 'BATTLE_CAPTURE_OVERLAP' if change == 'overlap' else 'BATTLE_CAPTURE_OUTSIDE')
