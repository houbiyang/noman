"""地图高程与完整道路目录的独立几何及无损转换检查。"""

from copy import deepcopy
import json
import unittest

from backend.demo_domain import MAP_PATH
from backend.map_catalog import current_map, convert, road_profile, terrain_height, unique_object, validate_source
from backend.runtime import ServiceError


class MapCatalogTests(unittest.TestCase):
    """不安装依赖、不连接数据库，验证真实地图及独立非平面夹具。"""

    def test_complete_source_preserved(self):
        """全部源字段、道路和高度逐项保留，建筑材质不遗漏。"""
        source = json.loads(MAP_PATH.read_bytes())
        result = current_map()
        self.assertEqual(result["source_payload"], source)
        self.assertEqual(result["elevation_grid"]["values"], source["heights"])
        self.assertEqual([edge["source_record"] for edge in result["road_edges"]], source["roads"])
        self.assertEqual(result["buildings"], source["buildings"])
        self.assertEqual(result["materials"], source["materials"])

    def test_triangle_interpolation(self):
        """非平面单格按三角面插值，不误用双线性中心高度。"""
        source = dict(width=10, length=10, heightColumns=2, heightRows=2, heights=[0, 0, 0, 10])
        self.assertEqual(terrain_height(source, 5, 5), 5)
        self.assertEqual(terrain_height(source, 2, 8), 2)
        self.assertEqual(terrain_height(source, 8, 2), 2)
        self.assertEqual(terrain_height(source, 10, 10), 10)
        with self.assertRaises(ServiceError):
            terrain_height(source, 11, 10)

    def test_profile_crosses_triangle(self):
        """经过对角线时插入中间高度，不能只用端点算直线。"""
        source = dict(width=10, length=10, heightColumns=2, heightRows=2, heights=[0, 0, 0, 10])
        path = road_profile(source, dict(x=0, z=10), dict(x=10, z=0))
        self.assertEqual(len(path), 3)
        self.assertEqual(path[1], dict(x="5", y="5", z="5"))

    def test_bridge_height_separate(self):
        """桥面节点单独派生，不改地面节点，也不自动生成合格桥头。"""
        data = current_map()
        nodes = {node["node_id"]: node for node in data["road_nodes"]}
        for edge in data["road_edges"]:
            if edge["bridge_id"]:
                self.assertTrue(nodes[edge["from_node_id"]]["derived"])
                self.assertTrue(all(point["y"] == edge["surface_elevation_m"] for point in edge["polyline"]))
                self.assertEqual(edge["usage_status"], "requires_review")
        self.assertTrue(all(not bridge["connector_node_ids"] for bridge in data["bridges"]))

    def test_reject_invalid_source(self):
        """缺高程、非有限高程、重复道路和悬空端点均拒绝。"""
        original = json.loads(MAP_PATH.read_bytes())
        for kind in ("missing_height", "nan", "duplicate", "reference", "width"):
            value = deepcopy(original)
            if kind == "missing_height": value["heights"].pop()
            elif kind == "nan": value["heights"][0] = float("nan")
            elif kind == "duplicate": value["roads"].append(deepcopy(value["roads"][0]))
            elif kind == "reference": value["roads"][0]["startId"] = "missing"
            else: value["roads"][0]["width"] = 0
            with self.subTest(kind=kind), self.assertRaises(ServiceError):
                validate_source(value)

    def test_negative_height_and_determinism(self):
        """负高程合法，相同输入的内容摘要完全一致。"""
        source = json.loads(MAP_PATH.read_bytes())
        self.assertLess(min(source["heights"]), 0)
        result = current_map()
        self.assertEqual(convert(source, result["import_audit"]["source_sha256"]), result)

    def test_duplicate_json_keys(self):
        """重复键不允许被 JSON 解析器悄悄覆盖。"""
        with self.assertRaises(ServiceError):
            json.loads('{"height":1,"height":2}', object_pairs_hook=unique_object)


if __name__ == "__main__":
    unittest.main()
