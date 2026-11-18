"""Bounded read-only real marker diagnostics; no DB, approval or new placement."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from unittest import TestCase

from backend.battle_map_source_candidates import build_deployment_preflight
from backend.battle_map_surfaces import SurfaceCatalog, seal_catalog
from backend.battle_terrain_assets import TerrainAssets


class ExistingMarkerPreflightTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=SurfaceCatalog.current()
        cls.before=deepcopy(cls.catalog.payload)
        cls.assets=TerrainAssets.current()
        cls.report=build_deployment_preflight(cls.assets,cls.catalog)

    def test_actual_catalog_gate_and_no_implicit_bindings(self):
        d=self.report
        self.assertFalse(d['production_ready'])
        self.assertFalse(d['grants_route_permission'])
        self.assertEqual((d['accepted_layer_count'],d['accepted_connection_count'],
                          d['frozen_camp_facility_count'],d['frozen_capture_mask_count']),(0,0,0,0))
        self.assertEqual(self.catalog.payload,self.before)
        self.assertEqual(d['payload_sha256'],seal_catalog(d)['payload_sha256'])
        self.assertEqual(d['catalog_sha256'],self.catalog.digest)
        for side in d['observations']:
            self.assertEqual(side['component_gate']['code'],'BATTLE_SURFACE_UNRESOLVED')
            self.assertEqual(side['required_camp_count'],3)
            self.assertEqual(side['source_camp_marker_count'],1)
            self.assertEqual(side['approved_camp_count'],0)

    def test_source_supply_markers_are_outside_100m_and_not_moved(self):
        expected={'red':'14600','blue':'12100'}
        for side in self.report['observations']:
            self.assertEqual(side['source_marker_squared_distance_m2'],expected[side['side']])
            self.assertFalse(side['source_supply_within_100m'])
            a,b=side['source_camp_marker'],side['source_supply_marker']
            self.assertEqual(sum((F(a[k])-F(b[k]))**2 for k in ('x','z')),F(expected[side['side']]))

    def test_full_base_support_is_not_production_placement_approval(self):
        for side in self.report['observations']:
            for name,item in side['footprints'].items():
                evidence=item['base_support_evidence']
                self.assertTrue(item['base_footprint_continuous'])
                self.assertEqual(F(evidence['uncovered_area_m2']),0)
                self.assertTrue(item['base_slope_within_limit'])
                self.assertEqual(F(evidence['max_grade_squared']),0)
                self.assertTrue(evidence['face_ids'])
                self.assertFalse(item['legal_placement'])
                self.assertFalse(item['overlays_accepted'])
                self.assertFalse(item['solids_validated'])
                self.assertFalse(item['access_validated'])
                if name=='camp_60x60':self.assertEqual(F(evidence['area_m2']),3600)
                # An empty contact list is no approval of all deferred overlays.
                self.assertEqual(item['source_overlay_contacts'],[])

    def test_deferred_dependencies_and_initial_dock_not_waived(self):
        chain=self.report['minimal_dependency_chain']
        self.assertTrue(all(stage['blocked'] for stage in chain))
        self.assertTrue(self.report['deferred_work_was_not_treated_as_absent'])
        self.assertIn('building_solids_or_certified_exclusion',chain[0]['deferred_dependencies'])
        self.assertIn('dock_full_footprint_and_explicit_airspace',chain[2]['required'])
        self.assertIn('five_effective_capture_masks',chain[3]['required'])
