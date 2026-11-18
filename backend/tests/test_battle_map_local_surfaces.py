"""Actual-source local component regressions; never DB or formal acceptance."""
from copy import deepcopy
import json
from pathlib import Path
from unittest import TestCase

from backend.battle_map_deployment import MapDeployment
from backend.battle_map_motion import sweep
from backend.battle_map_surfaces import SurfaceCatalog, rectangle, seal_catalog, _covered
from backend.battle_map_surface_build import (GROUND, BRIDGE, clear_cells, merge_cells,
                                            polygon, _clear, _component)
from backend.battle_terrain_assets import TerrainAssets
from backend.battle_terrain import rational as q
from backend.runtime import ServiceError, sha256

ROOT=Path(__file__).resolve().parents[1]


class ActualLocalSurfaceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets=TerrainAssets.current()
        cls.value=json.loads((ROOT/'battle-map-local-surfaces-v1.json').read_bytes())
        cls.layout=json.loads((ROOT/'battle-map-local-deployment-v1.json').read_bytes())
        cls.catalog=SurfaceCatalog(cls.assets,cls.value)

    def test_local_scope_does_not_publish_formal_scene(self):
        self.assertFalse(self.value['acceptance']['production_ready'])
        self.assertTrue(self.value['acceptance']['local_manual_test_ready'])
        self.assertEqual(self.value['release_scope'],'local_manual_test')
        self.assertEqual(self.layout['surface_catalog_sha256'],self.catalog.digest)
        self.assertEqual(len(self.value['connections']),0)
        self.assertEqual(len(self.value['source_audit']['inventory']),len(self.assets.part_ids()))
        self.assertTrue(all(not b['accepted'] for b in self.value['source_audit']['blocked_connections']))

    def test_domains_are_reproducible_conservative_triangle_exclusion(self):
        blocked,inventory=clear_cells(self.assets)
        self.assertEqual(merge_cells(blocked),self.value['layers'][0]['verified_domains'])
        self.assertEqual(inventory,self.value['source_audit']['inventory'])
        self.assertEqual(sum(map(sum,blocked)),self.value['source_audit']['blocked_cell_count'])
        # The source deployment region grants permission but not obstacle clearance.
        forbidden=next((x*50+25,z*50+25) for z in range(64,208) for x in range(20,38) if blocked[z][x])
        with self.assertRaises(ServiceError) as caught:
            self.catalog.footprint(GROUND,rectangle(*forbidden,3),side='red',purpose='deploy')
        self.assertEqual(caught.exception.code,'BATTLE_SURFACE_OUTSIDE_VERIFIED_DOMAIN')

    def test_actual_facilities_and_initial_units_no_airspace_assumption(self):
        checker=MapDeployment(self.catalog)
        state=deepcopy(self.layout['validation_state'])
        self.assertEqual(len(state['camps']),6)
        self.assertEqual(len(state['units']),6)
        for camp in state['camps']:
            checker.camp(camp);checker._facility(camp)
        for unit in state['units']:checker.unit(unit)
        with self.assertRaises(ServiceError) as caught:checker.validate_deployment(state)
        self.assertEqual(caught.exception.code,'BATTLE_DOCK_AIRSPACE_UNRESOLVED')

    def test_five_masks_true_support_inside_source_circles_bridge_separate(self):
        sources={p['point_id']:p for p in self.value['source_facts']['capture_points']}
        self.assertEqual(len(self.value['capture_masks']),5)
        for mask in self.value['capture_masks']:
            source=sources[mask['point_id']]
            self.assertEqual(mask['source_navigation_layer_id'],source['navigation_layer_id'])
            for raw in mask['polygons']:
                self.assertTrue(all((q(x)-q(source['x']))**2+(q(z)-q(source['z']))**2<=q(source['radius_m'])**2 for x,z in raw))
                for side in ('red','blue'):
                    self.assertTrue(self.catalog.footprint(mask['surface_id'],raw,side=side,purpose='capture').continuous_support)
            if mask['point_id']=='P-05':
                self.assertEqual(mask['surface_id'],BRIDGE)
                self.assertEqual(self.catalog.height(BRIDGE,(6163,5800)),22)
        with self.assertRaises(ServiceError):
            self.catalog.footprint(BRIDGE,rectangle(5810,5800,4),side='red',purpose='move')

    def test_road_is_exact_source_projection_not_visual_elevation_support(self):
        layer=self.catalog.layer(GROUND)
        self.assertEqual(sum(map(len,layer['road_faces'].values())),48172)
        self.assertEqual(self.catalog.center_surface(GROUND,(4500,4375)),'road')
        # Road source visual offset never replaces actual terrain support.
        expected=self.catalog.height(GROUND,(4500,4375))
        self.assertTrue(self.catalog.footprint(GROUND,rectangle(4500,4375,3),side='red',purpose='move').continuous_support)
        self.assertNotEqual(expected,q('20.32'))
        with self.assertRaises(ServiceError) as caught:
            self.catalog.footprint(GROUND,rectangle(4500,4375,30),side='red',purpose='camp')
        self.assertIn(caught.exception.code,('BATTLE_TERRAIN_FORBIDDEN','BATTLE_SURFACE_OUTSIDE_VERIFIED_DOMAIN'))

    def test_spatial_triangle_filter_preserves_original_support(self):
        bounds=(1747,4297,1773,4303)
        selected=self.catalog.triangles(GROUND,bounds=bounds)
        self.assertTrue(selected)
        self.assertLess(len(selected),30)
        self.assertEqual(self.catalog.height(GROUND,(1750,4300)),20)
        evidence=self.catalog.footprint(GROUND,sweep((1750,4300),(1770,4300),3),side='red',purpose='move')
        self.assertTrue(evidence.continuous_support)

    def test_exact_success_cache_matches_uncached_and_keys_all_permissions(self):
        poly=rectangle(1750,4300,3)
        kwargs=dict(side='red',purpose='stop',kind='ground')
        cold=self.catalog._footprint_uncached(GROUND,poly,**kwargs)
        warm=self.catalog.footprint(GROUND,poly,**kwargs)
        self.assertEqual(cold,warm)
        self.assertIs(warm,self.catalog.footprint(GROUND,poly,**kwargs))
        self.assertLessEqual(len(self.catalog._footprint_cache),2048)
        self.assertEqual(self.catalog.height(GROUND,(1750,4300)),20)
        self.assertLessEqual(len(self.catalog._height_cache),4096)
        before=len(self.catalog._footprint_cache)
        # Failure is not memoized. A cached move must not grant camp authority.
        road=rectangle(4500,4375,3)
        self.catalog.footprint(GROUND,road,side='red',purpose='move')
        for _ in range(2):
            with self.assertRaises(ServiceError):
                self.catalog.footprint(GROUND,road,side='red',purpose='camp')
        self.assertEqual(len(self.catalog._footprint_cache),before+1)
        shifted=tuple((x+q('0.001'),z) for x,z in poly)
        other=self.catalog.footprint(GROUND,shifted,**kwargs)
        self.assertIsNot(other,warm)


    def test_source_matches_is_readonly_and_public_payload_remains_detached(self):
        self.assertTrue(self.catalog.source_matches(self.value))
        changed=self.catalog.payload
        changed['release_scope']='untrusted'
        self.assertFalse(self.catalog.source_matches(changed))
        self.assertEqual(self.catalog.payload['release_scope'],'local_manual_test')
        self.assertTrue(self.catalog.source_matches(self.value))

    def test_local_layout_and_smoke_plan_are_strict_input_shapes(self):
        from backend.battle_setup import SideSetup
        from backend.battle_contracts import BattleDraftRequest
        for side in ('red','blue'):SideSetup.model_validate(self.layout[side])
        plan=json.loads((ROOT/'battle-map-local-smoke-plan-v2.json').read_bytes())
        self.assertEqual(set(plan),{'rounds'})
        self.assertEqual(len(plan['rounds']),5)
        for n,row in enumerate(plan['rounds'],1):
            for side,items in row.items():
                BattleDraftRequest.model_validate(dict(round_number=n,intents=[dict(item,life_id='fresh-life-placeholder') for item in items]))
                for item in items:
                    position=(item.get('movement') or {}).get('position')
                    if position:
                        self.assertEqual((q(position['x'])*100000).denominator,1)
                        self.assertEqual((q(position['y'])*150000).denominator,1)


class DomainUnionTests(TestCase):
    def test_cell_union_never_bridges_deleted_hole(self):
        # Tiny known pattern embedded in otherwise forbidden fixed-size domain.
        blocked=[bytearray(b'\1'*240) for _ in range(240)]
        for z in range(2,5):
            for x in range(2,5):blocked[z][x]=0
        blocked[3][3]=1
        domains=merge_cells(blocked)
        self.assertFalse(_covered(rectangle(175,175,2),[tuple(map(tuple,p)) for p in domains]))
        self.assertTrue(_clear(blocked,125,125,4,4))
        self.assertFalse(_clear(blocked,175,175,4,4))
        self.assertEqual(len(_component(blocked,(2,2))),8)
