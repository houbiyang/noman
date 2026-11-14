"""为角色过滤后的单位补充玩家展示属性。"""

import math

# 仅用于玩家界面展示的设计参考值，不参与命令校验或战斗结算。
REFERENCE_ATTRIBUTES = dict(vision_m=dict(recon=1600, ground=800, support=600),
    base_damage=20, defense=dict(recon=0, ground=0.25, support=0.1),
    attack_range_m=1000, attack_supply=5, service_range_m=100, service_per_step=20)

def enrich_units(units: list[dict], manifest: dict) -> list[dict]:
    """在视图副本中添加冻结资源上限与新版参考属性。

    Args:
        units: 已按席位过滤并复制的单位。
        manifest: 当前快照冻结的规则清单。

    Returns:
        带展示属性的单位副本列表。
    """
    profiles = manifest.get("spatial_profile", {}).get("unit_profiles", {})
    rules = REFERENCE_ATTRIBUTES
    for unit in units:
        kind = unit["unit_type"]
        limits = profiles.get(kind, {})
        known = {
            name: isinstance(unit.get(name), (int, float))
            and not isinstance(unit.get(name), bool) and math.isfinite(unit[name]) and unit[name] >= 0
            for name in ("durability", "energy", "supply")
        }
        unit["attributes"] = dict(
            vision_m=rules["vision_m"][kind], damage=rules["base_damage"] if kind == "ground" else 0,
            defense_percent=rules["defense"][kind] * 100,
            attack_range_m=rules["attack_range_m"] if kind == "ground" else 0,
            reference_only=True,
            durability_known=known["durability"], energy_known=known["energy"], supply_known=known["supply"],
            max_health=limits.get("durability", 0), max_energy=limits.get("energy", 0),
            max_supply=limits.get("supply", 0), speed_m_per_step=limits.get("speed_m_per_step", 0),
            energy_per_km=limits.get("energy_per_km", 0), attack_interval_steps=1,
            attack_supply_cost=rules["attack_supply"], service_range_m=rules["service_range_m"],
            service_amount=rules["service_per_step"],
        )
    return units
