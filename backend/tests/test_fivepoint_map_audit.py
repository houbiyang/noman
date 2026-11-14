"""正式地图前置检查直接读取发布三角面，不使用平地或道路连通替身放行。"""

import unittest

from backend.fivepoint_map_audit import inspect_map, surface_heights


def road_part(height):
    """生成覆盖桥头的小块铺装面，便于独立核对高差。"""
    return dict(layer='routes', material='Asphalt', vertices=[0, height, 0, 10, height, 0, 0, height, 10], triangles=[0, 1, 2])


class FivepointMapAuditTests(unittest.TestCase):
    """桥头、版本、退化三角面和缺失语义都不能静默通过。"""

    def fixture(self):
        """构造同版本、两端均位于三角形内的单桥地图。"""
        definition = dict(mapId='test', contentHash='identity', nodes=[dict(id='a', x=1, z=1), dict(id='b', x=2, z=2)],
                          roads=[dict(id='bridge', startId='a', endId='b', isBridge=True, surfaceHeight=22)])
        geometry = dict(mapId='test', contentHash='identity', parts=[road_part(22), road_part(20.32)])
        return definition, geometry

    def test_discontinuous_bridge_uses_rendered_surfaces(self):
        definition, geometry = self.fixture()
        result = inspect_map(definition, geometry)
        self.assertFalse(result['geometry_ready'])
        self.assertEqual(len(result['bridge_discontinuities']), 2)
        self.assertAlmostEqual(result['bridge_discontinuities'][0]['height_gap_m'], 1.68)

    def test_matching_bridge_is_continuous_but_missing_semantics_still_blocks(self):
        definition, geometry = self.fixture()
        geometry['parts'] = [road_part(22)]
        result = inspect_map(definition, geometry)
        self.assertTrue(result['geometry_ready'])
        self.assertFalse(result['release_ready'])
        self.assertIn('support_layers', result['missing_semantics'])

    def test_missing_bridge_surface_is_not_flat_ground(self):
        definition, geometry = self.fixture()
        geometry['parts'] = []
        self.assertEqual(inspect_map(definition, geometry)['bridge_discontinuities'][0]['code'], 'BRIDGE_SURFACE_MISSING')

    def test_version_mismatch_blocks(self):
        definition, geometry = self.fixture()
        geometry['contentHash'] = 'another-map'
        self.assertFalse(inspect_map(definition, geometry)['identity_matches'])

    def test_triangle_interpolation_and_outside(self):
        part = road_part(0)
        part['vertices'][4] = 10
        self.assertEqual(surface_heights([part], 2, 2), [2.0])
        self.assertEqual(surface_heights([part], 8, 8), [])
        part['triangles'] = [0, 0, 0]
        self.assertEqual(surface_heights([part], 0, 0), [])
