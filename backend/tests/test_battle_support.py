"""完整占地的几何证据；合成数学夹具不等于真实战局验收。"""
import json
from fractions import Fraction as F
from unittest import TestCase

from backend.battle_support import SupportIndex, convex_polygon, _intersection, signed_area
from backend.battle_terrain_assets import TerrainAssets
from backend.demo_domain import MAP_PATH, require
from backend.runtime import ServiceError


def square(x0=0, z0=0, x1=10, z1=10):
    return ((x0, z0), (x1, z0), (x1, z1), (x0, z1))


def quad(x0=0, z0=0, x1=10, z1=10, height=0, dx=0, dz=0):
    def p(x, z):
        return F(x), F(height)+F(dx)*F(x)+F(dz)*F(z), F(z)
    a, b, c, d = p(x0, z0), p(x1, z0), p(x1, z1), p(x0, z1)
    return (a, b, c), (a, c, d)


class MeshFixture:
    """只提供SupportIndex读取接口，不能注册为生产资产或Geometry。"""
    manifest = dict(playable_bounds_m=dict(min_x=0, min_z=0, max_x=12000, max_z=12000))

    def __init__(self, **parts):
        self.parts = parts

    def _part(self, part):
        require(part in self.parts, 'BATTLE_SUPPORT_INVALID', '未知夹具部件')
        return self.parts[part]

    def face_count(self, part):
        return len(self._part(part))

    def face_vertices(self, part, index):
        return tuple(tuple(F(v) for v in p) for p in self._part(part)[index])

    def part_bounds(self, part):
        vertices = [p for t in self._part(part) for p in t]
        return tuple(F(v) for v in (min(p[0] for p in vertices), min(p[2] for p in vertices),
                                    max(p[0] for p in vertices), max(p[2] for p in vertices)))


def evidence(polygon=None, parts=None, **meshes):
    return SupportIndex(MeshFixture(**meshes)).footprint(
        polygon or square(), support_parts=parts or list(meshes))


class PolygonTests(TestCase):
    def test_clockwise_and_counterclockwise_same(self):
        self.assertEqual(convex_polygon(square()), convex_polygon(tuple(reversed(square()))))

    def test_invalid_concave_crossed_degenerate_and_duplicate(self):
        for p in ((), ((0,0),(1,0),(0,0)), ((0,0),(1,1),(2,2)),
                  ((0,0),(1,1),(0,1),(1,0)), ((0,0),(2,0),(1,1),(2,2),(0,2)),
                  ((0,0),(True,0),(0,1)), ((0,0),(float('nan'),0),(0,1)),
                  ((0,0),(1,0,1),(0,1))):
            with self.subTest(p=p), self.assertRaises(ServiceError):
                convex_polygon(p)

    def test_vertex_contact_not_discarded(self):
        self.assertEqual(_intersection(convex_polygon(square()), convex_polygon(square(10,10,20,20))), ((F(10),F(10)),))

    def test_line_contact_not_discarded(self):
        clip = _intersection(convex_polygon(square()), convex_polygon(square(10,0,20,10)))
        self.assertEqual(set(clip), {(F(10),F(0)), (F(10),F(10))})
        self.assertEqual(signed_area(clip), 0)


class SupportTests(TestCase):
    def test_two_triangles_cover_full_footprint(self):
        result = evidence(floor=quad())
        self.assertEqual(result.area_m2, 100)
        self.assertEqual(result.uncovered_area_m2, 0)
        self.assertTrue(result.continuous_support)
        self.assertEqual(result.face_ids, (('floor',0), ('floor',1)))

    def test_center_supported_but_edge_hanging_fails(self):
        result = evidence(floor=quad(0,0,9,10))
        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.uncovered_area_m2, 10)

    def test_duplicate_area_cannot_cover_hole(self):
        left = quad(0,0,5,10)
        result = evidence(floor=left+left)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.uncovered_area_m2, 50)
        self.assertFalse(result.height_conflicts)

    def test_overlapping_triangles_union_not_area_sum(self):
        result = evidence(floor=quad(0,0,7,10)+quad(3,0,10,10))
        self.assertTrue(result.continuous_support)
        self.assertEqual(result.uncovered_area_m2, 0)

    def test_sub_millimeter_gap_not_filled_by_tolerance(self):
        result = evidence(floor=quad(0,0,5,10)+quad(F('5.00001'),0,10,10))
        self.assertEqual(result.uncovered_area_m2, F('0.0001'))
        self.assertFalse(result.coverage_complete)

    def test_bridge_and_riverbed_cannot_be_implicitly_merged(self):
        index = SupportIndex(MeshFixture(bridge=quad(height=22,dx=F('.15')), riverbed=quad()))
        bridge = index.footprint(square(), support_parts=['bridge'])
        self.assertEqual((bridge.min_height_m,bridge.max_height_m), (22,F('23.5')))
        self.assertEqual(bridge.max_grade_squared, F('.15')**2)
        mixed = index.footprint(square(), support_parts=['bridge','riverbed'])
        self.assertTrue(mixed.coverage_complete)
        self.assertFalse(mixed.continuous_support)
        self.assertTrue(mixed.height_conflicts)

    def test_touching_but_height_jump_is_not_continuous(self):
        result = evidence(left=quad(0,0,5,10), right=quad(5,0,10,10,height=1))
        self.assertTrue(result.coverage_complete)
        self.assertFalse(result.continuous_support)

    def test_real_continuous_ramp_not_rejected(self):
        result = evidence(left=quad(0,0,5,10), right=quad(5,0,10,10,height=F('-0.5'),dx=F('.1')))
        self.assertTrue(result.continuous_support)
        self.assertEqual(result.max_grade_squared,F('.1')**2)

    def test_narrow_steep_face_not_lost_by_sampling(self):
        result = evidence(left=quad(0,0,5,10),
                          steep=quad(5,0,F('5.001'),10,height=F('-2'),dx=F('.4')),
                          right=quad(F('5.001'),0,10,10,height=F('.0004')))
        self.assertTrue(result.continuous_support)
        self.assertEqual(result.max_grade_squared, F('.4')**2)

    def test_face_extending_outside_query_does_not_expand_height_range(self):
        result = evidence(square(2,2,3,3), floor=quad(height=10,dx=F('.1')))
        self.assertEqual((result.min_height_m,result.max_height_m), (F('10.2'),F('10.3')))

    def test_vertical_faces_are_reported_not_used_as_flat_support(self):
        result = evidence(wall=(((0,0,0),(0,5,0),(0,0,10)),))
        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.uncovered_area_m2,100)
        self.assertEqual(result.non_support_face_ids,(('wall',0),))
        self.assertIsNone(result.max_grade_squared)

    def test_outside_map_not_legal_even_with_landscape_support(self):
        result = evidence(square(-1,0,10,10), landscape=quad(-10,-10,20,20))
        self.assertEqual(result.uncovered_area_m2,0)
        self.assertFalse(result.coverage_complete)
        self.assertFalse(result.inside_playable_bounds)

    def test_absent_support_does_not_default_to_zero_height(self):
        result = evidence(floor=quad(100,100,200,200))
        self.assertEqual(result.uncovered_area_m2,100)
        self.assertIsNone(result.min_height_m)

    def test_explicit_unique_known_part_ids_required(self):
        index = SupportIndex(MeshFixture(floor=quad()))
        for parts in ([], ['bad'], ['floor','floor'], 'floor', [True], [[]]):
            with self.subTest(parts=parts), self.assertRaises(ServiceError):
                index.footprint(square(),support_parts=parts)

    def test_result_never_grants_permission_or_resolves_overlays(self):
        result = evidence(floor=quad()).to_dict()
        for key in ('grants_route_permission','production_ready','overlays_resolved','collisions_validated'):
            self.assertIs(result[key],False)
        self.assertEqual(result['area_m2'],'100')

    def test_index_deterministic_no_request_mutation(self):
        index = SupportIndex(MeshFixture(a=quad(0,0,5,10),b=quad(5,0,10,10)))
        parts = ['b','a']; polygon = list(square())
        a = index.footprint(polygon,support_parts=parts)
        b = index.footprint(polygon,support_parts=list(reversed(parts)))
        self.assertEqual(a,b)
        self.assertEqual(parts,['b','a']); self.assertEqual(polygon,list(square()))


class CurrentMeshTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = TerrainAssets.current()
        cls.index = SupportIndex(cls.assets)
        # ROAD_004桥中段，只从真实Asphalt部件取面，不从道路元数据取高度。
        cls.polygon = square(6596,2996,6604,3004)
        cls.parts = []
        for part in cls.assets.part_ids():
            ann = cls.assets.annotation(part)
            if ann['source_layer'] == 'routes' and ann['source_material'] == 'Asphalt':
                x0,z0,x1,z1 = cls.assets.part_bounds(part)
                if x0 <= 6604 and x1 >= 6596 and z0 <= 3004 and z1 >= 2996:
                    cls.parts.append(part)

    def test_current_bridge_full_eight_meter_patch_uses_actual_mesh(self):
        self.assertTrue(self.parts)
        result = self.index.footprint(self.polygon,support_parts=self.parts)
        self.assertTrue(result.continuous_support,result.to_dict())
        self.assertEqual(result.area_m2,64)
        self.assertEqual(result.min_height_m,22)
        self.assertEqual(result.max_height_m,22)
        self.assertEqual(result.max_grade_squared,0)
        self.assertFalse(result.to_dict()['production_ready'])

    def test_current_asset_binding_and_parts_are_read_only(self):
        names = self.assets.part_ids()
        self.assertEqual(len(names),637)
        ann = self.assets.annotation(names[0]); ann['source_layer']='changed'
        self.assertNotEqual(self.assets.annotation(names[0])['source_layer'],'changed')
        self.assertEqual(self.assets.manifest['map_id'],'qinglan-strategic-v6')
        self.assertFalse(self.assets.manifest['acceptance']['production_ready'])

    def test_current_three_bridge_heads_have_discontinuous_support(self):
        # 固定资产的阻断证据，不是可发布场景验收。桥中段平坦不能掩盖桥头台阶。
        definition = json.loads(MAP_PATH.read_bytes())
        nodes = {n['id']: n for n in definition['nodes']}
        asphalt = [p for p in self.assets.part_ids()
                   if self.assets.annotation(p)['source_layer'] == 'routes'
                   and self.assets.annotation(p)['source_material'] == 'Asphalt']
        boxes = {p: self.assets.part_bounds(p) for p in asphalt}
        bridges = [r for r in definition['roads'] if r['isBridge']]
        self.assertEqual({r['id'] for r in bridges}, {'ROAD_004','ROAD_010','ROAD_016'})
        for road in bridges:
            with self.subTest(road=road['id']):
                a, b = nodes[road['startId']], nodes[road['endId']]
                self.assertEqual(a['z'], b['z'])
                x0, x1 = sorted([a['x'], b['x']]); z = a['z']
                parts = [p for p, box in boxes.items()
                         if box[0] <= x1 and box[2] >= x0 and box[1] <= z+4 and box[3] >= z-4]
                proof = self.index.footprint(square(x0,z-4,x1,z+4), support_parts=parts)
                self.assertTrue(proof.coverage_complete)
                self.assertFalse(proof.continuous_support)
                self.assertTrue(proof.height_conflicts)
                self.assertFalse(proof.to_dict()['production_ready'])
