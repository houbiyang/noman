"""真实面坡度和用户确认的基础分类；只读当前资产，不连接数据库。"""
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend import battle_kernel as k
from backend.battle_terrain import SupportTriangle, rational, slope_factor
from backend import battle_terrain_assets as a
from backend.runtime import ServiceError


def inclined(grade):
    return SupportTriangle(((0, 0, 0), (100, F(grade)*100, 0), (0, 0, 100)))


class SlopeTests(TestCase):
    def test_actual_plane_height_not_average_height_grid(self):
        t = SupportTriangle(((0, 7, 0), (10, 9, 0), (0, 8, 10)))
        self.assertEqual(t.gradient, (F(1, 5), F(1, 10)))
        self.assertEqual(t.height((2, 3)), F(77, 10))

    def test_longitudinal_and_cross_slope_not_endpoint_height_only(self):
        t = inclined('0.36')
        result = t.directional_slope((0, 0), (0, 10))
        self.assertEqual(result.longitudinal_squared, 0)
        self.assertEqual(result.cross_squared, F(36, 100)**2)
        with self.assertRaises(ServiceError): slope_factor('ground', result.grade_squared)

    def test_uphill_downhill_and_triangle_winding_identical(self):
        t = inclined('.2'); reverse = SupportTriangle(tuple(reversed(t.vertices)))
        self.assertEqual(t.directional_slope((0, 0), (10, 0)), reverse.directional_slope((10, 0), (0, 0)))
        self.assertEqual(t.gradient, reverse.gradient)

    def test_diagonal_direction_uses_both_directional_components(self):
        result = inclined('.35').directional_slope((0, 0), (1, 1))
        self.assertEqual(result.longitudinal_squared, F(49, 800))
        self.assertEqual(result.cross_squared, F(49, 800))
        self.assertEqual(slope_factor('ground', result.grade_squared), F(3, 5))

    def test_exact_10_percent_and_just_over(self):
        for kind, reduced in [('ground', F(85, 100)), ('support', F(7, 10))]:
            self.assertEqual(slope_factor(kind, F(1, 10)**2), 1)
            self.assertEqual(slope_factor(kind, F(1001, 10000)**2), reduced)

    def test_exact_20_percent_support_allowed_but_20_01_forbidden(self):
        self.assertEqual(slope_factor('support', inclined('.2').directional_slope((0, 0), (1, 0)).grade_squared), F(7, 10))
        with self.assertRaises(ServiceError): slope_factor('support', F(2001, 10000)**2)

    def test_exact_35_percent_ground_allowed_but_35_01_forbidden(self):
        self.assertEqual(slope_factor('ground', F(35, 100)**2), F(3, 5))
        with self.assertRaises(ServiceError): slope_factor('ground', F(3501, 10000)**2)

    def test_bridge_uses_own_plane_instead_of_underlying_ground(self):
        bridge = SupportTriangle(((0, 22, 0), (100, 37, 0), (0, 22, 100)))
        riverbed = SupportTriangle(((0, 0, 0), (100, 0, 0), (0, 0, 100)))
        self.assertEqual(slope_factor('ground', bridge.directional_slope((0, 0), (1, 0)).grade_squared), F(85, 100))
        self.assertEqual(slope_factor('ground', riverbed.directional_slope((0, 0), (1, 0)).grade_squared), 1)
        self.assertEqual(bridge.height((0, 0)), 22)

    def test_local_maximum_is_not_single_direction_grade(self):
        t = SupportTriangle(((0, 0, 0), (100, 3, 0), (0, 4, 100)))
        self.assertEqual(t.maximum_local_grade_squared, F(5, 100)**2)

    def test_vertical_or_degenerate_triangle_rejected(self):
        for corners in [((0,0,0),(0,10,0),(0,0,10)), ((0,0,0),(1,1,1),(2,2,2))]:
            with self.assertRaises(ServiceError): SupportTriangle(corners)

    def test_height_outside_support_rejected(self):
        with self.assertRaises(ServiceError): inclined('.1').height((101, 1))

    def test_zero_direction_rejected(self):
        with self.assertRaises(ServiceError): inclined('.1').directional_slope((0, 0), (0, 0))

    def test_nan_infinity_boolean_and_huge_precision_rejected(self):
        for value in [True, float('nan'), float('inf'), 'NaN', '-Infinity', '1e9999', '1e-9999', object()]:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ServiceError): rational(value)

    def test_negative_or_air_slope_factor_rejected(self):
        for kind, value in [('ground', -1), ('recon', 0), ('unknown', 0)]:
            with self.assertRaises(ServiceError): slope_factor(kind, value)

    def test_narrow_face_interval_detected_without_sampling(self):
        t = SupportTriangle(((50,0,0), ('50.001',1,0), (50,0,10)))
        self.assertEqual(t.route_interval((0, 1), (100, 1)), (F(1,2), F(500009,1000000)))

    def test_route_interval_winding_reversal_and_boundary(self):
        t = inclined('.1'); r = SupportTriangle(tuple(reversed(t.vertices)))
        self.assertEqual(t.route_interval((-10, 0), (110, 0)), (F(1,12), F(11,12)))
        self.assertEqual(t.route_interval((-10, 0), (110, 0)), r.route_interval((-10, 0), (110, 0)))
        self.assertEqual(t.route_interval((0,0),(-1,0)), (0,0))

    def test_route_outside_triangle_has_no_support(self):
        self.assertIsNone(inclined('.1').route_interval((-10, -1), (110, -1)))


class KernelMeshEvidenceTests(TestCase):
    def budget(self, kind, surface='open', **evidence):
        return k.segment_budget(kind, [dict(surface=surface, length_mm='100000', **evidence)], '1000')

    def test_open_ground_speed_and_energy_use_total_rulebook(self):
        result = self.budget('ground', grade_squared='0')
        self.assertEqual(result['lengths_mm'], ['65000/3'])
        self.assertEqual(result['energy'], '4/15')

    def test_open_support_speed_and_energy_use_total_rulebook(self):
        result = self.budget('support', grade_squared='0')
        self.assertEqual(result['lengths_mm'], ['11250'])
        self.assertEqual(result['energy'], '1/4')

    def test_squared_evidence_matches_legacy_scalar_grade(self):
        for kind in ('ground', 'support'):
            for surface in ('open','field','road','bridge'):
                self.assertEqual(self.budget(kind, surface, grade_squared='0.0225'),
                                 self.budget(kind, surface, grade='0.15'))

    def test_mesh_slope_controls_kernel_not_visual_bridge_label(self):
        t = inclined('.2001')
        squared = k.exact(t.directional_slope((0,0),(1,0)).grade_squared)
        with self.assertRaises(ServiceError): self.budget('support', 'bridge', grade_squared=squared)

    def test_missing_or_conflicting_slope_evidence_rejected(self):
        for evidence in ({}, {'grade': '0', 'grade_squared': '0'}):
            with self.assertRaises(ServiceError): self.budget('ground', **evidence)

    def test_unknown_water_and_dense_forest_still_forbidden(self):
        for surface in ('unknown','water','dense_forest','mud','obstacle'):
            with self.assertRaises(ServiceError): self.budget('ground', surface, grade_squared='0')


class ClassificationTests(TestCase):
    def setUp(self):
        self.geometry = dict(mapId='qinglan-strategic-v6', contentHash='fixture', parts=[
            dict(name='base', layer='terrain', material='GroundLow', vertices=[0,0,0,100,20,0,0,0,100], triangles=[0,1,2]),
            dict(name='background', layer='landscape', material='GroundHigh', vertices=[], triangles=[]),
            dict(name='water', layer='water', material='Water', vertices=[], triangles=[]),
            dict(name='forest', layer='scrub', material='ForestFloor', vertices=[], triangles=[]),
            dict(name='road', layer='routes', material='Asphalt', vertices=[], triangles=[]),
            dict(name='field', layer='farmland', material='FieldGold', vertices=[], triangles=[]),
            dict(name='building', layer='settlement', material='Battle_Concrete', vertices=[], triangles=[]),
            dict(name='unapproved', layer='settlement', material='GroundLow', vertices=[], triangles=[])])
        self.definition = dict(mapId='qinglan-strategic-v6', contentHash='fixture', coordinateUnit='meter', width=12000, length=12000)
        self.raw_definition = json.dumps(self.definition).encode()
        self.raw_geometry = json.dumps(self.geometry).encode()
        self.policy = a.policy()
        self.policy['approved_map'] = dict(map_id='qinglan-strategic-v6',
            definition_sha256=hashlib.sha256(self.raw_definition).hexdigest(),
            geometry_sha256=hashlib.sha256(self.raw_geometry).hexdigest())
        self.patcher = patch('backend.battle_terrain_assets.policy', return_value=self.policy)
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def manifest(self):
        return a.build_manifest(self.raw_definition, self.raw_geometry)

    def test_only_approved_base_materials_and_layers_are_open(self):
        parts = {p['part_id']: p for p in self.manifest()['parts']}
        self.assertEqual(parts['base']['base_surface'], 'open')
        self.assertEqual(parts['background']['base_surface'], 'open')
        self.assertEqual(parts['unapproved']['action'], 'preserve_existing')
        self.assertNotIn('base_surface', parts['unapproved'])

    def test_existing_overlays_preserved_not_repainted_open(self):
        parts = {p['part_id']: p for p in self.manifest()['parts']}
        for name in ('water','forest','road','field','building'):
            self.assertEqual(parts[name]['action'], 'preserve_existing')
            self.assertNotIn('base_surface', parts[name])

    def test_classification_never_grants_route_or_production_readiness(self):
        manifest = self.manifest()
        self.assertFalse(manifest['acceptance']['production_ready'])
        self.assertFalse(manifest['acceptance']['overlays_resolved'])
        self.assertFalse(manifest['policy']['grants_route_permission'])
        self.assertTrue(manifest['policy']['overlays_must_be_resolved_before_ground_use'])

    def test_manifest_and_source_not_mutated_and_deterministic(self):
        before = deepcopy(self.policy)
        self.assertEqual(self.manifest(), self.manifest())
        self.assertEqual(self.policy, before)

    def test_manifest_tampering_rejected(self):
        value = self.manifest(); value['parts'][0]['base_surface'] = 'road'
        with self.assertRaises(ServiceError): a.TerrainAssets(self.raw_definition, self.raw_geometry, value)

    def test_same_map_content_hash_does_not_hide_modified_asset(self):
        with self.assertRaises(ServiceError) as caught:
            a.TerrainAssets(self.raw_definition, self.raw_geometry+b' ', self.manifest())
        self.assertEqual(caught.exception.code, 'BATTLE_TERRAIN_APPROVAL_STALE')

    def test_assets_supply_actual_triangle_height_and_cross_slope(self):
        assets = a.TerrainAssets(self.raw_definition, self.raw_geometry, self.manifest())
        t = assets.triangle('base', 0)
        self.assertEqual(t.height((50,0)), 10)
        self.assertEqual(t.directional_slope((0,0),(0,1)).cross_squared, F(1,25))
        annotation = assets.annotation('base'); annotation['base_surface'] = 'road'
        self.assertEqual(assets.annotation('base')['base_surface'], 'open')

    def test_unknown_part_index_or_invalid_mesh_fail_closed(self):
        assets = a.TerrainAssets(self.raw_definition, self.raw_geometry, self.manifest())
        for part, index in [('unknown',0), ('base',-1), ('base',True), ('base',1), ('water',0)]:
            with self.assertRaises(ServiceError): assets.triangle(part,index)
        with self.assertRaises(ServiceError): assets.annotation('unknown')

    def test_duplicate_part_identity_rejected(self):
        self.geometry['parts'].append(self.geometry['parts'][0])
        raw = json.dumps(self.geometry).encode()
        self.policy['approved_map']['geometry_sha256'] = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(ServiceError): a.build_manifest(self.raw_definition,raw)


class CurrentAssetTests(TestCase):
    def test_current_manifest_and_confirmed_base_parts_match_real_assets(self):
        assets = a.TerrainAssets.current()
        base = [p for p in assets.manifest['parts'] if p['action']=='classify_base_ground']
        self.assertEqual(len(base),179)
        self.assertEqual(len(assets.manifest['parts']),637)
        terrain = [p for p in base if p['source_layer']=='terrain']
        self.assertEqual(len(terrain),51)
        for part in terrain:
            triangle = assets.triangle(part['part_id'],0)
            self.assertGreaterEqual(triangle.maximum_local_grade_squared,0)
        self.assertFalse(assets.manifest['acceptance']['production_ready'])
