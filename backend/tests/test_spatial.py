"""不依赖数据库验证空间测试规则的资源守恒及边界。"""

from copy import deepcopy
from unittest import TestCase, main

from backend import spatial
from backend.demo_domain import delete_unit, initial_state, map_manifest, validate_state
from backend.runtime import ServiceError


class SpatialTests(TestCase):
    """测试冻结参数、单位资格、空中航段及兵营完整占地。"""

    def setUp(self):
        """构造隔离内存状态，不读取或修改真实游戏。"""
        self.state = initial_state("test-scene", "test-rules", "patrol")
        self.state["manifest"].update(spatial_profile=spatial.profile(), map_source_sha256=map_manifest()["source_file_sha256"])
        self.state, _ = spatial.set_preparation(self.state, "red", dict(side="red", position=dict(x="10.3", y="26.2"), quantities=dict(recon=1, ground=1, support=1)))

    def test_air_route_and_arrival(self):
        """空中航线不调用地面节点，抵达后不继续扣费。"""
        unit = next(item for item in self.state["units"] if item["unit_id"] == "R-U01")
        target = dict(x="11.3", y="26.2")
        self.state["routes"] = [spatial.route(self.state, unit, target)]
        spatial.advance(self.state)
        self.assertEqual(self.state["routes"][0]["status"], "arrived")
        self.assertEqual(unit["energy"], 99.5)
        self.assertEqual(unit["position"]["x"], "11.3")
        spatial.advance(self.state)
        self.assertEqual(unit["energy"], 99.5)

    def test_energy_depletion_is_bounded(self):
        """不足一步的能源仅支持对应距离，不能移动完整速度或扣负数。"""
        unit = next(item for item in self.state["units"] if item["unit_id"] == "R-U01")
        unit["energy"] = 0.05
        self.state["routes"] = [spatial.route(self.state, unit, dict(x="20", y="26.2"))]
        spatial.advance(self.state)
        self.assertEqual(unit["energy"], 0)
        self.assertAlmostEqual(self.state["routes"][0]["traveled_m"], 10)
        spatial.advance(self.state)
        self.assertEqual(self.state["routes"][0]["status"], "blocked")

    def test_durability_zero_stops_action(self):
        """失效单位保留路线和位置，不消耗额外能源。"""
        unit = next(item for item in self.state["units"] if item["unit_id"] == "R-U01")
        self.state["routes"] = [spatial.route(self.state, unit, dict(x="20", y="26.2"))]
        unit["durability"] = 0
        before = deepcopy(unit)
        spatial.advance(self.state)
        self.assertEqual(unit["position"], before["position"])
        self.assertEqual(unit["energy"], before["energy"])

    def test_footprint_and_permissions(self):
        """中心在部署区边缘仍拒绝越界占地，另一方不能改配置。"""
        with self.assertRaises(ServiceError) as caught:
            spatial.camp(self.state, "red", dict(x="10", y="26"))
        self.assertEqual(caught.exception.code, "CAMP_FOOTPRINT_OUTSIDE")
        with self.assertRaises(ServiceError) as caught:
            spatial.set_preparation(self.state, "blue", dict(side="red", quantities=dict(recon=1, ground=1, support=1)))
        self.assertEqual(caught.exception.status, 403)

    def test_map_version_mismatch(self):
        """历史测试快照不能默用另一版本地图。"""
        self.state["manifest"]["map_source_sha256"] = "invalid"
        with self.assertRaises(ServiceError) as caught:
            spatial.map_data(self.state)
        self.assertEqual(caught.exception.code, "MAP_VERSION_UNAVAILABLE")

    def test_withdraw_camp_invalidates_ready(self):
        """撤销兵营不能保留悬空单位归属或准备完成状态。"""
        self.state["lifecycle"]["ready"]["red"] = True
        result, _ = spatial.set_preparation(self.state, "red", dict(side="red", quantities=dict(recon=1, ground=1, support=1)))
        self.assertFalse(result["lifecycle"]["ready"]["red"])
        self.assertFalse(any(camp["side"] == "red" for camp in result["camps"]))
        self.assertTrue(all(unit.get("camp_id") is None for unit in result["units"] if unit["side"] == "red"))
        spatial.validate(result)

    def test_delete_unit_clears_route_and_formation_but_keeps_camp(self):
        """暂停后删除最后一名同兵种单位，清理活动引用并保留兵营。"""
        self.state["lifecycle"]["phase"] = "paused"
        unit = next(item for item in self.state["units"] if item["unit_id"] == "R-G01")
        self.state["routes"] = [spatial.route(self.state, unit, dict(x="12", y="30"))]
        result, _ = delete_unit(self.state, "red", "R-G01")
        self.assertEqual(result["routes"], [])
        self.assertFalse(any("R-G01" in formation["unit_ids"] for formation in result["formations"]))
        self.assertTrue(any(camp["camp_id"] == "red-camp" for camp in result["camps"]))
        validate_state(result)


if __name__ == "__main__":
    main()
