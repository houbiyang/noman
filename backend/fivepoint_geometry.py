"""五点规则的确定性几何原语；缺失数据不视为可通行，不读取显示贴图推断地表。"""

from fractions import Fraction
import math

from backend.fivepoint_parameters import require, number, TERRAIN_FACTORS


def segment_box_interval(start, end, lower, upper):
    """精确求线段与闭包围盒相交的参数区间，覆盖薄墙和跨越多个地形片的情况。"""
    enter, leave = Fraction(0), Fraction(1)
    require(len(start) == len(end) == len(lower) == len(upper), "GEOMETRY_INVALID", "几何维数不一致")
    for origin, destination, minimum, maximum in zip(start, end, lower, upper):
        origin, destination, minimum, maximum = map(number, (origin, destination, minimum, maximum))
        require(minimum <= maximum, "GEOMETRY_INVALID", "几何范围倒置")
        delta = destination - origin
        if not delta:
            if not minimum <= origin <= maximum:
                return None
            continue
        near, far = sorted(((minimum - origin) / delta, (maximum - origin) / delta))
        enter, leave = max(enter, near), min(leave, far)
        if enter > leave:
            return None
    return enter, leave


def union_length(intervals) -> Fraction:
    """合并射线区间，避免重叠森林重复累计长度。"""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum((end - start for start, end in merged), Fraction(0))


def distance(start, end) -> Fraction:
    """计算参考欧氏长度；最终位置及累计路径需由路线执行层按毫米网格记账。"""
    squared = sum((number(left) - number(right)) ** 2 for left, right in zip(start, end))
    numerator_root, denominator_root = math.isqrt(squared.numerator), math.isqrt(squared.denominator)
    if numerator_root ** 2 == squared.numerator and denominator_root ** 2 == squared.denominator:
        return Fraction(numerator_root, denominator_root)
    return Fraction(str(math.sqrt(float(squared))))


def line_clear(start, end, solids) -> bool:
    """检查整条三维射线是否与实体相交；不使用每50米采样代替碰撞。"""
    return all(segment_box_interval(start, end, solid["min"], solid["max"]) is None for solid in solids)


def forest_depth(start, end, target_region: str, patches: list) -> Fraction:
    """累计目标所属连通林区内的全部射线段；反复进出累计，其他林区不混入。"""
    intervals = [segment_box_interval(start, end, patch["min"], patch["max"])
                 for patch in patches if patch["region_id"] == target_region]
    return distance(start, end) * union_length([interval for interval in intervals if interval is not None])


def full_stop_allowed(kind: str, terrain: str, maximum_grade, *, docking=False, camp=False) -> bool:
    """检查完整占地已经确定的最大坡度及地表；中心坡度不能代替最大坡度。"""
    grade = abs(number(maximum_grade))
    if camp:
        return terrain in ("open", "gravel") and grade <= Fraction(1, 20)
    if docking:
        return terrain in ("road", "gravel", "open", "field") and grade <= Fraction(1, 20)
    return kind in TERRAIN_FACTORS and terrain in TERRAIN_FACTORS[kind] and grade <= Fraction(1, 10)


class RuleGeometry:
    """规则执行所需的显式地图接口；基础类拒绝执行，不能用空场景默认放行。"""

    def verify(self, map_hash: str) -> None:
        """验证冻结地图身份及完整性；实际地图适配器必须覆盖本方法。"""
        require(False, "MAP_NOT_VERIFIED", "尚未提供通过验收的规则地表、路径和遮挡数据")

    def service_energy_cost(self, unit: dict, own_view: dict):
        """估算当前合法服务成本，包含路程、等待、维持和起降；未知必须返回空值。"""
        return None

    def movement_service_energy_cost(self, unit: dict, movement: dict, own_view: dict):
        """返回拟执行整段路径上的最大服务成本；未知不得用终点或零替代。"""
        return None

    def can_observe(self, observer: dict, target: dict) -> bool:
        """检查同一规则步当前观察；适配器必须核对状态、森林、距离和实体射线。"""
        require(False, "MAP_NOT_VERIFIED", "观察几何尚未通过验收")

    def can_fire(self, shooter: dict, target: dict) -> bool:
        """检查射手自身射线及射程，不以共享观察代替弹道合法性。"""
        require(False, "MAP_NOT_VERIFIED", "攻击几何尚未通过验收")

    def in_point(self, unit: dict, point: dict) -> bool:
        """检查圆与合法地表层的交集及完整单位占地。"""
        require(False, "MAP_NOT_VERIFIED", "占点地表尚未通过验收")

    def can_service(self, source: dict, target: dict) -> bool:
        """检查100米距离、相同连续服务面、遮挡及双方完整停驻坡度。"""
        require(False, "MAP_NOT_VERIFIED", "服务通路尚未通过验收")

    def can_dock(self, unit: dict, units: list) -> bool:
        """检查合法起降面和当前敌军占地，不能只检查静态中心点。"""
        require(False, "MAP_NOT_VERIFIED", "起降场地尚未通过验收")

    def move(self, unit: dict, destination: dict, seconds: int, available_energy: Fraction, reserve_factor: Fraction, units: list) -> dict:
        """返回合法实际位置、基础能耗、是否移动；不得超过秒数与预留能源预算。"""
        require(False, "MAP_NOT_VERIFIED", "五秒路线执行器尚未通过验收")

