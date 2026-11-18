"""Independent source exports, no database and never a scene acceptance fixture."""
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from unittest import TestCase

from backend.battle_map_source_candidates import (
    build_deployment_ground_candidates, build_overlay_candidates,
    build_building_bindings, _conflicts, _bounds, _union_boundary)
from backend.battle_map_surfaces import seal_catalog
from backend.battle_support import convex_polygon, signed_area, _intersection
from backend.battle_terrain import SupportTriangle
from backend.battle_terrain_assets import TerrainAssets
from backend.runtime import ServiceError

ROOT = Path(__file__).resolve().parents[2]


class GroundFixture:
    """2x2 source cells, both exact half triangles, not a valid production map."""
    def __init__(self):
        self.manifest = dict(map_id='synthetic',definition_sha256='s',geometry_sha256='s',
            payload_sha256='s',parts=[dict(part_id='ground',source_layer='terrain',
                source_material='GroundLow',action='classify_base_ground')])
        self.source_definition = dict(width=100,length=100,heightColumns=3,heightRows=3,
            deploymentZones=[dict(id='test',minX=0,maxX=100,minZ=0,maxZ=100)])
        self.faces=[]
        for x in (0,50):
            for z in (0,50):
                self.faces.extend([((x,0,z),(x,0,z+50),(x+50,0,z+50)),
                                   ((x,0,z),(x+50,0,z+50),(x+50,0,z))])
    def face_count(self,part): return len(self.faces)
    def triangle(self,part,index): return SupportTriangle(self.faces[index])
    def part_bounds(self,part): return 0,0,100,100


class SourceGeometryTests(TestCase):
    def test_ground_certificate_uses_both_original_cell_halves(self):
        d=build_deployment_ground_candidates(GroundFixture())
        z=d['zones'][0]
        self.assertEqual(z['certificate']['expected_cell_count'],4)
        self.assertEqual(z['certificate']['mapped_projection_area_m2'],'10000')
        self.assertTrue(z['certificate']['complete_base_projection'])
        self.assertFalse(z['deploy_accepted'])
        self.assertFalse(d['grants_route_permission'])

    def test_missing_half_is_not_complete_coverage(self):
        a=GroundFixture(); a.faces.pop()
        d=build_deployment_ground_candidates(a)
        self.assertFalse(d['zones'][0]['certificate']['complete_base_projection'])

    def test_duplicate_cell_half_rejected(self):
        a=GroundFixture(); a.faces[-1]=a.faces[-2]
        with self.assertRaises(ServiceError): build_deployment_ground_candidates(a)

    def test_shared_vertex_height_discontinuity_is_not_complete(self):
        a=GroundFixture(); a.faces[-1]=tuple((x,y+1,z) for x,y,z in a.faces[-1])
        z=build_deployment_ground_candidates(a)['zones'][0]
        self.assertFalse(z['certificate']['complete_base_projection'])
        self.assertTrue(z['certificate']['height_conflicts'])

    def test_unaligned_zone_is_not_silently_sampled(self):
        a=GroundFixture(); a.source_definition['deploymentZones'][0]['minX']=1
        with self.assertRaises(ServiceError): build_deployment_ground_candidates(a)

    def test_non_original_grid_triangle_rejected(self):
        a=GroundFixture(); a.faces[-1]=((50,0,50),(99,0,100),(100,0,50))
        with self.assertRaises(ServiceError): build_deployment_ground_candidates(a)

    @staticmethod
    def face(name,idx,points):
        poly=convex_polygon(points)
        return name,idx,poly,_bounds(poly)

    def test_broad_phase_duplicate_pairs_are_reported_once(self):
        a=self.face('a',0,((0,0),(250,0),(0,250)))
        b=self.face('b',0,((0,0),(250,0),(0,250)))
        conflicts=_conflicts([[a],[b]])
        self.assertEqual(len(conflicts),1)
        self.assertEqual(F(conflicts[0]['area_m2']),31250)
        self.assertFalse(conflicts[0]['precedence_accepted'])

    def test_boundary_contact_is_not_positive_area_conflict(self):
        a=self.face('a',0,((0,0),(100,0),(0,100)))
        b=self.face('b',0,((100,0),(100,100),(0,100)))
        self.assertEqual(_conflicts([[a],[b]]),[])

    def test_negative_bucket_and_self_overlap_are_not_lost(self):
        a=self.face('a',0,((-150,-150),(150,-150),(-150,150)))
        b=self.face('b',0,((-149,-149),(150,-149),(-149,150)))
        conflicts=_conflicts([[a,b]])
        self.assertEqual(len(conflicts),1)
        self.assertEqual(conflicts[0]['relation'],'same_source_self_overlap')
        self.assertGreater(F(conflicts[0]['area_m2']),0)

    def test_union_boundary_removes_shared_diagonal(self):
        a=self.face('a',0,((0,0),(100,0),(0,100)))
        b=self.face('b',0,((100,0),(100,100),(0,100)))
        edges=_union_boundary([a,b])
        self.assertEqual(len(edges),4)
        self.assertEqual(sum((p[0]*r[1]-p[1]*r[0])/2 for p,r in edges),10000)

    def test_union_boundary_preserves_duplicate_triangle_outer_edges(self):
        a=self.face('a',0,((0,0),(100,0),(0,100)))
        b=self.face('b',0,((0,0),(100,0),(0,100)))
        self.assertEqual(len(_union_boundary([a,b])),3)

    def test_union_boundary_splits_overlap_without_sampling_or_snapping(self):
        a=self.face('a',0,((0,0),(100,0),(0,100)))
        b=self.face('b',0,((50,0),(150,0),(150,100)))
        edges=_union_boundary([a,b])
        # Two areas 5000 minus exact overlap triangle 625.
        self.assertEqual(sum((p[0]*r[1]-p[1]*r[0])/2 for p,r in edges),9375)
        balance=Counter()
        for a,b in edges:balance[a]+=1;balance[b]-=1
        # Co-linear duplicates may partition source edges differently: no gaps,
        # but equal-side duplicate fragments must also be emitted only once.
        self.assertTrue(all(v==0 for v in balance.values()))

    def test_union_boundary_three_way_overlap_matches_inclusion_exclusion(self):
        triangles=[self.face('a',0,((0,0),(120,0),(0,120))),
                   self.face('b',0,((30,-20),(140,70),(-20,90))),
                   self.face('c',0,((10,10),(160,20),(60,160)))]
        polys=[t[2] for t in triangles]
        area=sum(signed_area(p) for p in polys)
        for a,b in ((0,1),(0,2),(1,2)):
            area-=signed_area(_intersection(polys[a],polys[b]))
        area+=signed_area(_intersection(_intersection(polys[0],polys[1]),polys[2]))
        self.assertEqual(sum((p[0]*r[1]-p[1]*r[0])/2 for p,r in _union_boundary(triangles)),area)

    def test_union_boundary_hole_retains_inner_clockwise_boundary(self):
        triangles=[]
        for idx,(x0,z0,x1,z1) in enumerate(((0,0,10,3),(0,7,10,10),(0,3,3,7),(7,3,10,7))):
            triangles.extend([self.face(str(idx),0,((x0,z0),(x1,z0),(x0,z1))),
                              self.face(str(idx),1,((x1,z0),(x1,z1),(x0,z1)))])
        edges=_union_boundary(triangles)
        self.assertEqual(sum((p[0]*r[1]-p[1]*r[0])/2 for p,r in edges),84)

    def test_sub_millimetre_source_strip_is_not_snapped_away(self):
        a=self.face('a',0,((0,0),(1,0),(0,1)))
        b=self.face('b',0,((F(1,1000000),0),(1,0),(0,1)))
        self.assertEqual(sum((p[0]*r[1]-p[1]*r[0])/2 for p,r in _union_boundary([a,b])),F(1,2))


class RealSourceExportTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets=TerrainAssets.current()
        cls.ground=build_deployment_ground_candidates(cls.assets)
        cls.overlays=build_overlay_candidates(cls.assets)
        cls.buildings=build_building_bindings(cls.assets)

    def test_exports_reproduce_frozen_artifact_and_bind_sources(self):
        for result, filename in ((self.ground,'battle-map-deployment-ground-candidates-v1.json'),
                                 (self.overlays,'battle-map-overlay-candidates-v1.json'),
                                 (self.buildings,'battle-map-building-bindings-v1.json')):
            self.assertEqual(result,json.loads((ROOT/'backend'/filename).read_bytes()))
            self.assertEqual(result['payload_sha256'],seal_catalog(result)['payload_sha256'])
            self.assertEqual(result['geometry_sha256'],self.assets.manifest['geometry_sha256'])
            self.assertFalse(result['production_ready'])
            self.assertFalse(result['legal_layers_created'])
            self.assertFalse(result['grants_route_permission'])

    def test_real_deployment_original_faces_cover_both_rectangles(self):
        expected={'RED_DEPLOY':(5184,'6480000'),'BLUE_DEPLOY':(6912,'8640000')}
        for z in self.ground['zones']:
            count,area=expected[z['source_zone']['id']]
            self.assertEqual(z['face_count'],count)
            self.assertEqual(z['certificate']['zone_area_m2'],area)
            self.assertTrue(z['certificate']['complete_base_projection'])
            self.assertEqual(z['certificate']['height_conflicts'],[])
            self.assertFalse(z['overlays_resolved'])
            self.assertFalse(z['solids_resolved'])
            self.assertFalse(z['deploy_accepted'])
            for region in z['regions']:
                original=self.assets.face_vertices(region['part_id'],region['triangle_index'])
                self.assertEqual([[str(v) for v in p] for p in original],region['vertices_xyz_m'])

    def test_all_overlay_original_faces_preserved_with_actual_boundaries(self):
        expected={('farmland','FieldGold'):816,('farmland','FieldGreen'):1622,
                  ('farmland','Mud'):23420,('logistics','FieldGold'):120,
                  ('logistics','FieldGreen'):80,('scrub','ForestFloor'):53192,
                  ('water','Water'):2512}
        self.assertEqual({(g['source_layer'],g['source_material']):g['face_count']
                          for g in self.overlays['groups']},expected)
        self.assertEqual(self.overlays['absent_source_pairs'],[['landscape','Mud']])
        for group in self.overlays['groups']:
            counts=Counter()
            for part,ids in group['source_faces'].items():
                self.assertEqual(ids,list(range(self.assets.face_count(part))))
                for index in ids:
                    poly=tuple((str(v[0]),str(v[2])) for v in self.assets.face_vertices(part,index))
                    for a,b in zip(poly,poly[1:]+poly[:1]): counts[tuple(sorted((a,b)))]+=1
            original_edges={edge for edge,n in counts.items() if n==1}
            exported_edges={tuple(sorted(tuple(v) for v in edge)) for edge in group['boundary_edges_xz_m']}
            self.assertEqual(original_edges,exported_edges)
            self.assertTrue(group['boundary_edges_xz_m'])
            self.assertTrue(group['union_boundary_edges_xz_m'])
            self.assertEqual(group['boundary_method'],
                'exact_rational_projected_triangle_union_directed_edge_fragments')

    def test_conflict_complete_batch_keeps_source_references_no_precedence(self):
        conflicts=self.overlays['positive_area_conflicts']
        self.assertEqual(len(conflicts),40008)
        seen=set()
        for c in conflicts:
            key=tuple(sorted((tuple(c['left']),tuple(c['right']))))
            self.assertNotIn(key,seen);seen.add(key)
            self.assertGreater(F(c['area_m2']),0)
            self.assertEqual(signed_area(tuple(tuple(F(v) for v in p) for p in c['polygon_xz_m'])),F(c['area_m2']))
            self.assertFalse(c['precedence_accepted'])

    def test_forest_density_is_not_guessed_and_mud_role_explicit(self):
        groups={(g['source_layer'],g['source_material']):g for g in self.overlays['groups']}
        forest=groups['scrub','ForestFloor']
        self.assertIsNone(forest['forest_density'])
        self.assertEqual(len(forest['source_faces']),27)
        self.assertEqual(groups['farmland','Mud']['generator_role'],'one_metre_field_furrows')
        self.assertEqual(set(forest['deployment_intersections']),{'RED_DEPLOY','BLUE_DEPLOY'})

    def test_all_building_triangle_ranges_partition_exact_original_settlement(self):
        b=self.buildings
        self.assertEqual((b['building_count'],b['part_count'],b['triangle_count']),(1769,120,476020))
        ranges=defaultdict(list)
        self.assertEqual(len(b['source_model_sha256']),15)
        for binding in b['bindings']:
            self.assertFalse(binding['solid_accepted'])
            for part,span in binding['baked_triangle_ranges'].items():ranges[part].append(span)
        for part,spans in ranges.items():
            stop=0
            for start,end in sorted(spans):
                self.assertEqual(start,stop)
                self.assertGreater(end,start)
                stop=end
            self.assertEqual(stop,self.assets.face_count(part))
        self.assertFalse(b['verification']['rule_geometry_tolerance_granted'])
        self.assertEqual(b['verification']['maximum_baked_vs_rounded_transform_replay_error_m'],'1/1000')

    def test_building_altered_original_index_rejected(self):
        class Broken:
            manifest=self.assets.manifest
            source_definition=self.assets.source_definition
            def _mesh(inner,part):
                vertices,indices=self.assets._mesh(part)
                indices=list(indices);indices[0],indices[1]=indices[1],indices[0]
                return vertices,indices
        with self.assertRaises(ServiceError):build_building_bindings(Broken())

    def test_production_catalog_remains_empty_and_fail_closed(self):
        current=json.loads((ROOT/'backend/battle-map-surfaces-v1.json').read_bytes())
        self.assertEqual(current['layers'],[])
        self.assertEqual(current['connections'],[])
        self.assertFalse(current['acceptance']['production_ready'])
