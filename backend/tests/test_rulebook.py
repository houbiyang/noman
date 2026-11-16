"""总册参数、来源、精确派生值与安全导入边界。"""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend import rulebook


class RulebookTests(unittest.TestCase):
    def setUp(self):
        self.value = rulebook.definition()
        self.p = self.value['parameters']
        self.text = self.value['source']['text']

    def validate(self, p=None):
        rulebook.validate(self.p if p is None else p, self.text)

    def test_source_is_complete_and_pinned(self):
        source = self.value['source']
        self.assertEqual(source['text'], (rulebook.ROOT / source['path']).read_text())
        self.assertEqual(hashlib.sha256(source['text'].encode()).hexdigest(), source['sha256'])
        self.assertEqual(self.p['modules'], dict(main='1.1', terrain='1.0'))

    def test_source_change_refuses_import(self):
        p = deepcopy(self.p)
        p['source']['sha256'] = '0' * 64
        with patch.object(Path, 'read_text', return_value=json.dumps(p)):
            with self.assertRaisesRegex(ValueError, '原文已变化'):
                rulebook.definition()

    def test_tables_preserve_main_and_terrain(self):
        tables = self.value['source']['tables']
        self.assertEqual(len(tables), 32)
        for anchor in ('main-3', 'main-4-1', 'main-4-2', 'main-4-3', 'main-4-4',
                       'main-5-1', 'main-5-2', 'main-5-3', 'main-24-1',
                       'terrain-3-1', 'terrain-4-2', 'terrain-7-2', 'terrain-10-1'):
            self.assertIn(anchor, {t['anchor'] for t in tables})
        for table in tables:
            self.assertTrue(all(len(row) == len(table['columns']) for row in table['rows']), table['anchor'])
            self.assertTrue(table['line_start'] <= table['line_end'])

    def test_scoring_rows_match_structured_parameters(self):
        table = next(t for t in self.value['source']['tables'] if t['anchor'] == 'main-3')
        rows = {r[0]: r[2] for r in table['rows']}
        for key in ('control_max','kill_max','energy_max','ammo_max','material_max'):
            self.assertEqual(int(rows['score.' + key]), self.p['score'][key])
        self.assertEqual(int(rows['match.duration']), self.p['match']['duration_seconds'])
        self.assertEqual(int(rows['match.step']), self.p['match']['step_seconds'])
        self.assertEqual(int(rows['point.radius']), self.p['capture']['radius_m'])

    def test_profiles_match_source_table(self):
        table = next(t for t in self.value['source']['tables'] if t['anchor'] == 'main-4-1')
        rows = {r[0]: r for r in table['rows']}
        mapping = {'durability':'耐久上限','energy':'自身能源上限','speed_m_per_minute':'移动速度',
                   'energy_per_km':'移动能耗','observation_m':'基础观察半径','ammo':'自用弹药上限','kill_value':'原始击毁价值'}
        for col, kind in enumerate(('recon','ground','support'),1):
            for key, label in mapping.items():
                self.assertEqual(self.p['unit_profiles'][kind][key], int(rows[label][col]))

    def test_custom_roster_and_resource_denominators(self):
        roster = self.p['roster']
        self.assertIsNone(roster['fixed_total_per_side'])
        self.assertIsNone(roster['default_counts'])
        self.assertIsNone(roster['capacity_limit'])
        self.assertFalse(roster['production'])
        self.assertFalse(roster['respawn'])
        u,t,l = 6,10,3  # 总册22.1算例，不是初始配置默认值。
        reserve = self.p['barracks']['public_reserve_per_side']
        support = self.p['unit_profiles']['support']['stocks']
        self.assertEqual(100*(u+t+l)+support['energy']*l+reserve['energy'], 3700)
        self.assertEqual(60*t+support['ammo']*l+reserve['ammo'], 1560)
        self.assertEqual(support['repair']*l+reserve['repair'], 500)
        self.assertEqual(5*u+10*t+8*l,154)

    def test_exact_drone_and_damage_values(self):
        drone = self.p['drone']
        rational = lambda v: Fraction(v['numerator'],v['denominator'])
        self.assertEqual(rational(drone['flight_energy_per_step_exact']), Fraction(1,6))
        self.assertEqual(rational(drone['transition_total_energy_exact']), Fraction(7,6))
        attack = self.p['attack']
        self.assertEqual(Fraction(str(attack['moving_damage_factor']))*attack['damage'], Fraction(48,5))
        self.assertEqual(attack['first_shot_earliest_seconds'],5)

    def test_candidate_points_not_substituted_from_runtime_map(self):
        points = self.p['capture']['candidate_points']
        table = next(t for t in self.value['source']['tables'] if t['anchor'] == 'main-24-1')
        for point,row in zip(points,table['rows']):
            self.assertEqual(point['point_id'],row[1])
            self.assertEqual([point[k] for k in ('x_m','z_m','radius_m')],[int(row[i]) for i in (3,4,5)])
        self.assertIsNone(self.value['integration']['scenario_binding'])
        self.assertFalse(self.value['integration']['migrate_existing_games'])
        self.assertFalse(self.value['integration']['gameplay_implemented'])

    def test_surface_tables_and_slope_boundaries(self):
        terrain = self.p['terrain']
        table = next(t for t in self.value['source']['tables'] if t['anchor'] == 'terrain-3-1')
        for row in table['rows']:
            for kind,allowed_col,factor_col in [('ground',2,3),('support',4,5)]:
                expected = float(row[factor_col]) if row[allowed_col] == '是' else None
                self.assertEqual(terrain['surface_factors'][kind][row[1]],expected)
        self.assertEqual([band['max_inclusive'] for band in terrain['slope_bands']], [0.1,0.2,0.35,None])
        self.assertEqual(terrain['max_slope'],dict(ground=0.35,support=0.2))
        self.assertEqual(terrain['bridge_min_clear_width_m'],dict(ground=6,support=8))

    def test_playback_and_public_reserve(self):
        b=self.p['barracks']
        self.assertEqual(b['public_reserve_per_side'],dict(energy=1200,ammo=600,repair=200))
        self.assertTrue(b['reserve_shared_across_barracks'])
        self.assertFalse(b['automatic_regeneration'])
        playback=self.p['playback']
        self.assertEqual([playback[k] for k in ('initial_buffer_seconds','maximum_normal_buffer_seconds','resume_buffer_seconds')],[180,180,120])
        self.assertEqual([v['speed'] for v in playback['speed_bands']],[2,1,0.5,0])

    def test_bad_derived_values_rejected(self):
        for group,key,value in [('score','resource_max',31),('score','normalize_to_100',True),
                                 ('capture','total_control_capacity_steps',720),('match','maximum_steps',60),
                                 ('capture','capture_seconds',31),('roster','fixed_total_per_side',20)]:
            with self.subTest(group=group,key=key):
                p=deepcopy(self.p);p[group][key]=value
                with self.assertRaises(ValueError):self.validate(p)

    def test_invalid_numeric_domains_rejected(self):
        for bad in (-1,float('nan'),float('inf'),True):
            with self.subTest(bad=bad):
                p=deepcopy(self.p);p['unit_profiles']['ground']['durability']=bad
                with self.assertRaises(ValueError):self.validate(p)
        p=deepcopy(self.p);p['terrain']['surface_factors']['support']['sparse_forest']=0
        with self.assertRaises(ValueError):self.validate(p)

    def test_missing_source_anchor_rejected(self):
        p=deepcopy(self.p);p['provenance']['attack']=['missing-999']
        with self.assertRaisesRegex(ValueError,'来源锚点不存在'):self.validate(p)

    def test_identity_is_deterministic(self):
        self.assertEqual(rulebook.identity(self.value),rulebook.identity(rulebook.definition()))
        changed=deepcopy(self.value);changed['parameters']['score']['control_max']=71
        self.assertNotEqual(rulebook.identity(self.value),rulebook.identity(changed))


if __name__ == '__main__':
    unittest.main()
