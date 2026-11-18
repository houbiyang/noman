"""真实网格遮挡证据；夹具不向用户数据库写入，也不授予场景验收。"""
from fractions import Fraction as F
from unittest import TestCase
from unittest.mock import patch

from backend.battle_occlusion import RayIndex, triangle_interval, _bounds, _cross, _sub
from backend.battle_terrain_assets import TerrainAssets
from backend.runtime import ServiceError

WALL = ((0, 0, 0), (0, 4, 0), (0, 0, 4))


class Mesh:
    def __init__(self, **parts): self.parts = parts
    def part_ids(self): return tuple(self.parts)
    def face_count(self, part): return len(self.parts[part])
    def face_vertices(self, part, i): return tuple(tuple(F(v) for v in p) for p in self.parts[part][i])
    def part_bounds3d(self, part): return _bounds([p for f in self.parts[part] for p in f])


class TriangleTests(TestCase):
    def test_vertical_wall_in_both_windings_and_directions(self):
        for face in (WALL, tuple(reversed(WALL))):
            for a, b in (((-1,1,1),(1,1,1)), ((1,1,1),(-1,1,1))):
                self.assertEqual(triangle_interval(a, b, face), (F(1,2), F(1,2)))

    def test_segment_does_not_reach_wall(self):
        self.assertIsNone(triangle_interval((-2,1,1),(-1,1,1), WALL))

    def test_plane_crossing_outside_triangle_is_clear(self):
        self.assertIsNone(triangle_interval((-1,3,3),(1,3,3), WALL))

    def test_vertex_and_edge_touches_block(self):
        for yz in ((0,0),(2,2),(0,2)):
            self.assertIsNotNone(triangle_interval((-1,*yz),(1,*yz), WALL))

    def test_parallel_off_plane_is_clear(self):
        self.assertIsNone(triangle_interval((1,-1,1),(1,5,1), WALL))

    def test_coplanar_crossing_is_interval_not_ignored(self):
        self.assertEqual(triangle_interval((0,-1,1),(0,5,1), WALL), (F(1,6), F(2,3)))

    def test_coplanar_entirely_inside(self):
        self.assertEqual(triangle_interval((0,1,1),(0,2,1), WALL), (F(0), F(1)))

    def test_coplanar_miss(self):
        self.assertIsNone(triangle_interval((0,4,4),(0,5,5), WALL))

    def test_tiny_triangle_is_not_skipped(self):
        face = tuple(tuple(F(v,10**9) for v in p) for p in WALL)
        self.assertEqual(triangle_interval((-1,F(1,10**9),F(1,10**9)),
                                          (1,F(1,10**9),F(1,10**9)), face), (F(1,2),F(1,2)))

    def test_invalid_and_degenerate_fail_closed(self):
        cases = [((0,0,0),(0,0,0),WALL), ((True,0,0),(1,1,1),WALL),
                 ((float('nan'),0,0),(1,1,1),WALL), ((0,0),(1,1),WALL),
                 ((-1,1,1),(1,1,1),((0,0,0),(0,1,1),(0,2,2)))]
        for a,b,c in cases:
            with self.subTest(case=(a,b,c)), self.assertRaises(ServiceError):
                triangle_interval(a,b,c)


class IndexTests(TestCase):
    def test_complete_scope_can_prove_static_mesh_clear_only(self):
        result = RayIndex(Mesh(wall=[WALL]), blocker_parts=['wall']).cast((-1,3,3),(1,3,3))
        self.assertTrue(result.mesh_clear)
        self.assertFalse(result.public()['grants_attack_permission'])
        self.assertFalse(result.public()['production_ready'])

    def test_partial_scope_never_proves_clear(self):
        result = RayIndex(Mesh(wall=[WALL],unknown=[WALL]), blocker_parts=['wall']).cast((-1,3,3),(1,3,3))
        self.assertFalse(result.mesh_clear)
        self.assertEqual(result.unresolved_parts, ('unknown',))

    def test_known_hit_is_evidence_even_with_unknowns(self):
        result = RayIndex(Mesh(wall=[WALL],unknown=[WALL]), blocker_parts=['wall']).cast((-1,1,1),(1,1,1))
        self.assertTrue(result.blocked)

    def test_explicit_transparent_parts_have_no_hit(self):
        result = RayIndex(Mesh(camp=[WALL]), blocker_parts=[], transparent_parts=['camp']).cast((-1,1,1),(1,1,1))
        self.assertTrue(result.mesh_clear)

    def test_invalid_catalogues_fail(self):
        for blockers, transparent in ((['missing'],[]), (['wall','wall'],[]), (['wall'],['wall']), ('wall',[])):
            with self.subTest(blockers=blockers), self.assertRaises(ServiceError):
                RayIndex(Mesh(wall=[WALL]), blocker_parts=blockers, transparent_parts=transparent)

    def test_endpoint_exception_does_not_hide_other_faces_of_bridge(self):
        other = tuple((2,y,z) for _,y,z in WALL)
        index = RayIndex(Mesh(bridge=[WALL,other]), blocker_parts=['bridge'])
        result = index.cast((0,1,1),(3,1,1), source_faces=(('bridge',0),))
        self.assertEqual([(h.part_id,h.face_index) for h in result.hits], [('bridge',1)])
        self.assertEqual(result.hits[0].interval, (F(2,3),F(2,3)))

    def test_target_endpoint_exception_only_at_endpoint(self):
        index = RayIndex(Mesh(wall=[WALL]), blocker_parts=['wall'])
        self.assertTrue(index.cast((-1,1,1),(0,1,1), target_faces=(('wall',0),)).mesh_clear)
        self.assertTrue(index.cast((-1,1,1),(1,1,1), target_faces=(('wall',0),)).blocked)

    def test_source_exception_cannot_hide_coplanar_overlap(self):
        index = RayIndex(Mesh(wall=[WALL]), blocker_parts=['wall'])
        self.assertTrue(index.cast((0,1,1),(0,2,1), source_faces=(('wall',0),)).blocked)

    def test_invalid_endpoint_face_fails(self):
        index = RayIndex(Mesh(wall=[WALL]), blocker_parts=['wall'])
        for faces in ((('wall',3),), (('missing',0),), (('wall',True),), ('wall',)):
            with self.subTest(faces=faces), self.assertRaises(ServiceError):
                index.cast((-1,1,1),(1,1,1), source_faces=faces)

    def test_closest_hit_order_and_3d_bounds(self):
        near = tuple((2,y,z) for _,y,z in WALL)
        far = tuple((5,y,z) for _,y,z in WALL)
        index = RayIndex(Mesh(a_far=[far],z_near=[near]), blocker_parts=['a_far','z_near'])
        self.assertEqual([h.part_id for h in index.cast((0,1,1),(6,1,1)).hits], ['z_near','a_far'])
        self.assertTrue(index.cast((0,20,1),(6,20,1)).mesh_clear)

    def test_unvalidated_degenerate_face_is_not_transparent(self):
        index = RayIndex(Mesh(wall=[((0,0,0),(0,1,1),(0,2,2))]), blocker_parts=['wall'])
        with self.assertRaises(ServiceError): index.cast((-1,1,1),(1,1,1))

    def test_missing_blocker_faces_cannot_be_treated_as_transparent(self):
        with self.assertRaises(ServiceError):
            RayIndex(Mesh(wall=[]), blocker_parts=['wall'])

    def test_budget_fail_closed(self):
        mesh = Mesh(wall=[WALL])
        index = RayIndex(mesh, blocker_parts=['wall'])
        with patch.object(mesh, 'face_count', return_value=50001), self.assertRaises(ServiceError):
            index.cast((-1,1,1),(1,1,1))


class CurrentMeshTests(TestCase):
    @classmethod
    def setUpClass(cls): cls.assets = TerrainAssets.current()

    def test_real_ground_and_building_triangles_block(self):
        for prefix in ('terrain_GroundLow_', 'settlement_Battle_Concrete_'):
            part = next(p for p in self.assets.part_ids() if p.startswith(prefix))
            vertices = self.assets.face_vertices(part, 0)
            normal = _cross(_sub(vertices[1],vertices[0]),_sub(vertices[2],vertices[0]))
            self.assertTrue(any(normal))
            center = tuple(sum(v[i] for v in vertices)/3 for i in range(3))
            normal = tuple(n / max(abs(x) for x in normal) for n in normal)
            a, b = tuple(c-n for c,n in zip(center,normal)), tuple(c+n for c,n in zip(center,normal))
            result = RayIndex(self.assets, blocker_parts=[part]).cast(a,b)
            self.assertIn((part,0), [(h.part_id,h.face_index) for h in result.hits])
            self.assertFalse(result.mesh_clear)
            self.assertTrue(result.unresolved_parts)

    def test_actual_bridge_surface_blocks_vertical_but_not_above(self):
        # 已有真实支撑证据的ROAD_004中段。只检查这一真实部件，不能称为全图通视。
        index = RayIndex(self.assets, blocker_parts=['routes_Asphalt_03_01_0'])
        result = index.cast(('6745.525',21,3000),('6745.525',23,3000))
        self.assertTrue(result.blocked)
        self.assertEqual(result.public()['geometry_sha256'], self.assets.manifest['geometry_sha256'])
        self.assertTrue(any(h.interval == (F(1,2),F(1,2)) for h in result.hits))
        above = index.cast(('6744.525',30,3000),('6746.525',30,3000))
        self.assertFalse(above.blocked)
        self.assertFalse(above.mesh_clear)  # 全场景尚有未分类部件。
