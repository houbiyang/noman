"""已确认五点规则的精确参数；独立于历史演示参数，全部用于虚构游戏。"""

from fractions import Fraction


VERSION = "qinglan-fivepoint-custom-v1.1"
TERRAIN_VERSION = "qinglan-terrain-v1.0"
STEP_SECONDS = 5
DURATION_SECONDS = 3600
PLAN_SECONDS = 60
CONTROL_CAPACITY = 3600
SIDES = ("red", "blue")
UNIT_PROFILES = {
    "recon": dict(hp=40, energy=100, ammo=0, value=5, speed=800, cost=5, vision=1400, radius=2),
    "ground": dict(hp=100, energy=100, ammo=60, value=10, speed=400, cost=8, vision=900, radius=2),
    "support": dict(hp=80, energy=100, ammo=0, value=8, speed=300, cost=10, vision=700, radius=3),
}
PUBLIC_RESOURCES = dict(energy=1200, ammo=600, material=200)
CARGO_CAPACITY = dict(energy=200, ammo=120, material=100)
RESOURCE_WEIGHTS = dict(energy=15, ammo=9, material=6)
TERRAIN_FACTORS = {
    "ground": dict(road=Fraction(1), bridge=Fraction(1), gravel=Fraction(4, 5),
                   open=Fraction(13, 20), field=Fraction(1, 2), sparse_forest=Fraction(2, 5)),
    "support": dict(road=Fraction(1), bridge=Fraction(1), gravel=Fraction(7, 10),
                    open=Fraction(9, 20), field=Fraction(3, 10)),
}


class RuleViolation(ValueError):
    """携带稳定错误码的规则拒绝；调用方须回滚整个未提交步骤。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, code: str, message: str) -> None:
    """拒绝非法参数，不通过截断、填零或者补资源修复输入。"""
    if not condition:
        raise RuleViolation(code, message)


def number(value) -> Fraction:
    """读取有理数或有限十进制；布尔值、空值、非数及无穷大均拒绝。"""
    require(not isinstance(value, bool) and isinstance(value, (int, float, str, Fraction)),
            "NUMBER_INVALID", "规则数值类型无效")
    try:
        return value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise RuleViolation("NUMBER_INVALID", "规则数值必须有限") from error


def roster_baseline(quantities: dict) -> dict:
    """从玩家填写的三类数量计算资源与击毁分母，不设置默认兵力。"""
    require(set(quantities) == set(UNIT_PROFILES), "ROSTER_INVALID", "须提供三类兵种的数量")
    require(all(type(count) is int and count >= 0 for count in quantities.values()),
            "ROSTER_INVALID", "数量须为非负整数")
    total = sum(quantities.values())
    require(total > 0, "ROSTER_EMPTY", "每方至少配置一个单位")
    return dict(count=total, energy=100 * total + 200 * quantities["support"] + 1200,
                ammo=60 * quantities["ground"] + 120 * quantities["support"] + 600,
                material=100 * quantities["support"] + 200,
                value=sum(UNIT_PROFILES[kind]["value"] * count for kind, count in quantities.items()))


def slope_factor(kind: str, longitudinal, cross) -> Fraction:
    """按纵横坡绝对值较大者分档；桥面同样传入实际坡度。"""
    require(kind in ("ground", "support"), "UNIT_TYPE_INVALID", "地面坡度仅用于地面兵种")
    grade = max(abs(number(longitudinal)), abs(number(cross)))
    if grade <= Fraction(1, 10):
        return Fraction(1)
    if grade <= Fraction(1, 5):
        return Fraction(17, 20) if kind == "ground" else Fraction(7, 10)
    if grade <= Fraction(7, 20) and kind == "ground":
        return Fraction(3, 5)
    raise RuleViolation("SLOPE_BLOCKED", "坡度超过该兵种通行上限")


def segment_cost(kind: str, terrain: str, length, longitudinal=0, cross=0) -> dict:
    """计算已验证通路的连续参考时间和能源；不替代占地、空域及地表验证。"""
    require(kind in UNIT_PROFILES, "UNIT_TYPE_INVALID", "兵种无效")
    distance = number(length)
    require(distance >= 0, "DISTANCE_INVALID", "长度不能为负")
    factor = Fraction(1)
    if kind != "recon":
        require(terrain in TERRAIN_FACTORS[kind], "TERRAIN_BLOCKED", "该兵种不能通过此地表")
        factor = TERRAIN_FACTORS[kind][terrain] * slope_factor(kind, longitudinal, cross)
    profile = UNIT_PROFILES[kind]
    return dict(seconds=distance * 60 / (profile["speed"] * factor),
                energy=distance * profile["cost"] / (1000 * factor))


def score(control_steps: int, kill_value: int, enemy_value: int, remaining: dict, baseline: dict) -> dict:
    """使用未舍入有理数计分；显示格式由调用方处理，120分不是提前获胜线。"""
    require(type(control_steps) is int and 0 <= control_steps <= CONTROL_CAPACITY,
            "CONTROL_INVALID", "控制量须为合法点步整数")
    require(type(enemy_value) is int and enemy_value > 0 and type(kill_value) is int
            and 0 <= kill_value <= enemy_value, "KILL_VALUE_INVALID", "击毁分母或累计价值无效")
    resources = {}
    for category, weight in RESOURCE_WEIGHTS.items():
        initial, current = number(baseline[category]), number(remaining[category])
        require(initial > 0 and 0 <= current <= initial, "RESOURCE_INVALID", "资源基准或余额无效")
        resources[category] = weight * current / initial
    control = Fraction(70 * control_steps, CONTROL_CAPACITY)
    destruction = Fraction(20 * kill_value, enemy_value)
    return dict(control=control, destruction=destruction, resources=resources,
                resource=sum(resources.values()), total=control + destruction + sum(resources.values()))
