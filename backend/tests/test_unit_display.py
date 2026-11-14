"""验证玩家属性展示对未知值和冻结规则的处理。"""

from copy import deepcopy
from unittest import TestCase

from backend.unit_display import enrich_units


class UnitDisplayTests(TestCase):
    """资源展示必须区分未配置与真实零值。"""

    def test_unknown_resources_remain_unknown(self):
        """旧演示的空资源不能被解释为满血或耗尽。"""
        units = [dict(unit_type="recon", durability=None, energy=None, supply=None)]
        result = enrich_units(units, {})[0]["attributes"]
        self.assertFalse(result["durability_known"])
        self.assertFalse(result["energy_known"])
        self.assertEqual(result["max_health"], 0)

    def test_zero_and_fractional_resources_are_known(self):
        """零值表示真实耗尽，小数血量保留精度。"""
        unit = dict(unit_type="ground", durability=14.25, energy=0, supply=0)
        attributes = enrich_units([unit], {})[0]["attributes"]
        self.assertTrue(attributes["durability_known"])
        self.assertTrue(attributes["energy_known"])
        self.assertTrue(attributes["supply_known"])
        self.assertEqual(unit["durability"], 14.25)

    def test_limits_come_from_frozen_profile(self):
        """展示当前快照上限，同时保留参考属性的来源标记。"""
        manifest = dict(spatial_profile=dict(unit_profiles=dict(support=dict(
            durability=75, energy=90, supply=120, speed_m_per_step=250, energy_per_km=12))))
        before = deepcopy(manifest)
        unit = dict(unit_type="support", durability=60, energy=80, supply=0)
        attributes = enrich_units([unit], manifest)[0]["attributes"]
        self.assertEqual(attributes["max_health"], 75)
        self.assertEqual(attributes["max_supply"], 120)
        self.assertEqual(attributes["speed_m_per_step"], 250)
        self.assertTrue(attributes["reference_only"])
        self.assertEqual(manifest, before)

    def test_invalid_resource_is_not_a_known_number(self):
        """非有限数和布尔值不能进入有效资源显示。"""
        unit = dict(unit_type="recon", durability=True, energy=float("nan"), supply=-1)
        attributes = enrich_units([unit], {})[0]["attributes"]
        self.assertFalse(attributes["durability_known"])
        self.assertFalse(attributes["energy_known"])
        self.assertFalse(attributes["supply_known"])
