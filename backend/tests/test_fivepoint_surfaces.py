"""按发布三角面验证完整线段、高程和坡度，避免离散采样漏过狭小缺口。"""

import unittest

from backend.fivepoint_surfaces import SurfaceIndex


def rectangle(minimum_x, maximum_x, minimum_z, maximum_z, height=0):
    """生成带固定材质和标识的四顶点发布面。"""
    return dict(name='road', layer='routes', material='Asphalt',
                vertices=[minimum_x, height, minimum_z, maximum_x, height, minimum_z,
                          maximum_x, height, maximum_z, minimum_x, height, maximum_z],
                triangles=[0, 1, 2, 0, 2, 3])


class SurfaceIndexTests(unittest.TestCase):
    """完整线段不得跳过缺口，层叠地表不得在交叠坐标间瞬移。"""

    def test_triangle_height_and_gradient(self):
        part = rectangle(0, 100, 0, 20)
        part['vertices'][4] = part['vertices'][7] = 10
        surfaces = SurfaceIndex([part])
        hits = surfaces.at(50, 10)
        self.assertTrue(hits)
        self.assertTrue(all(abs(hit['height'] - 5) < 1e-9 for hit in hits))
        self.assertTrue(all(abs(hit['grade'] - .1) < 1e-9 for hit in hits))
        self.assertEqual(surfaces.at(101, 10), [])

    def test_thin_gap_cannot_be_skipped_between_samples(self):
        surfaces = SurfaceIndex([rectangle(0, 49.999, 0, 20), rectangle(50.001, 100, 0, 20)])
        result = surfaces.trace((0, 10), (100, 10))
        self.assertFalse(result['continuous'])
        self.assertAlmostEqual(result['gaps'][0][0], .49999)
        self.assertAlmostEqual(result['gaps'][0][1], .50001)

    def test_split_triangles_and_shared_edges_have_no_gap(self):
        result = SurfaceIndex([rectangle(0, 100, 0, 20)]).trace((0, 10), (100, 10))
        self.assertTrue(result['continuous'])
        self.assertAlmostEqual(sum(item['end'] - item['start'] for item in result['segments']), 1)

    def test_height_jump_is_not_a_continuous_road(self):
        result = SurfaceIndex([rectangle(0, 50, 0, 20), rectangle(50, 100, 0, 20, 2)]).trace((0, 10), (100, 10))
        self.assertFalse(result['continuous'])
        self.assertEqual(result['discontinuities'][0]['height_gap_m'], 2)

    def test_parallel_bridge_and_ground_remain_distinct(self):
        surfaces = SurfaceIndex([rectangle(0, 100, 0, 20), rectangle(0, 100, 0, 20, 22)])
        self.assertFalse(surfaces.trace((0, 10), (100, 10))['continuous'])
        bridge = surfaces.trace((0, 10), (100, 10), start_height=22)
        self.assertTrue(bridge['continuous'])
        self.assertTrue(all(item['height_start'] == 22 for item in bridge['segments']))

    def test_middle_slope_cannot_be_hidden_by_flat_endpoints(self):
        part = rectangle(40, 60, 0, 20)
        part['vertices'][4] = part['vertices'][7] = 10
        result = SurfaceIndex([rectangle(0, 40, 0, 20), part, rectangle(60, 100, 0, 20, 10)]).trace((0, 10), (100, 10))
        self.assertTrue(result['continuous'])
        self.assertEqual(result['maximum_grade'], .5)

    def test_continuous_branch_does_not_require_all_other_surfaces_to_have_same_end_height(self):
        flat = rectangle(0, 100, 0, 20)
        ramp = rectangle(0, 100, 0, 20)
        ramp['vertices'][4] = ramp['vertices'][7] = 1
        result = SurfaceIndex([flat, ramp]).trace((0, 10), (100, 10), start_height=0)
        self.assertTrue(result['continuous'])
        self.assertEqual(result['segments'][-1]['height_end'], 0)

    def test_short_steep_branch_does_not_hide_a_complete_flat_route(self):
        flat = rectangle(0, 100, 0, 20)
        ramp = rectangle(0, 50, 0, 20)
        ramp['vertices'][4] = ramp['vertices'][7] = 5
        result = SurfaceIndex([flat, ramp]).trace((0, 10), (100, 10), start_height=0)
        self.assertTrue(result['continuous'])
        self.assertEqual(result['maximum_grade'], 0)

    def test_degenerate_and_malformed_geometry_are_not_passable(self):
        part = rectangle(0, 100, 0, 20)
        part['triangles'] = [0, 0, 0]
        self.assertFalse(SurfaceIndex([part]).trace((0, 10), (100, 10))['continuous'])
        part['triangles'] = [0, 1, 999]
        with self.assertRaises(ValueError):
            SurfaceIndex([part])

    def test_zero_length_trace_still_requires_support(self):
        surfaces = SurfaceIndex([rectangle(0, 100, 0, 20)])
        self.assertTrue(surfaces.trace((10, 10), (10, 10))['continuous'])
        self.assertFalse(surfaces.trace((110, 10), (110, 10))['continuous'])
