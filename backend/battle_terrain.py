"""总册真实三角面坡度基础；不提供缺省地表、寻路或生产Geometry回退。

采用有理数计算坡度的平方，避免开平方后的浮点舍入将20.01%放行，或把
恰好20%拒绝。三角面顶点必须来自冻结地图；桥面不得改采河床高度。
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import lru_cache
import json
from pathlib import Path

from backend.demo_domain import require


MAX_GEOMETRY_DENOMINATOR = 10**36


def rational(value):
    """地图可有负坐标，但不接受布尔、NaN、无穷或任意对象。"""
    require(type(value) in (int, float, str, Decimal, Fraction),
            'BATTLE_GEOMETRY_INVALID', '几何坐标必须是有限数值')
    if isinstance(value, Fraction):
        result = value
    else:
        text = str(value)
        require(len(text) <= 80, 'BATTLE_GEOMETRY_INVALID', '几何数字过长')
        try:
            decimal = Decimal(text)
            require(decimal.is_finite(), 'BATTLE_GEOMETRY_INVALID', '几何数字必须有限')
            require(abs(decimal) <= Decimal('1e12') and decimal.as_tuple().exponent >= -18,
                    'BATTLE_GEOMETRY_INVALID', '几何数字超出支持精度或范围')
            result = Fraction(decimal)
        except (InvalidOperation, ValueError, OverflowError):
            require(False, 'BATTLE_GEOMETRY_INVALID', '几何数字无效')
    require(abs(result) <= 10**12 and result.denominator <= MAX_GEOMETRY_DENOMINATOR,
            'BATTLE_GEOMETRY_INVALID', '几何数字超出支持精度或范围')
    return result


def _point(value, dimensions):
    require(isinstance(value, (tuple, list)) and len(value) == dimensions,
            'BATTLE_GEOMETRY_INVALID', '几何坐标维度无效')
    return tuple(rational(v) for v in value)


@lru_cache(maxsize=1)
def _terrain():
    return json.loads(Path(__file__).with_name('rulebook-parameters-v1.json').read_text())['terrain']


def slope_factor(kind, grade_squared):
    """分档直接比较平方值；返回总册系数，不把禁行换成零速。"""
    require(kind in ('ground', 'support'), 'BATTLE_SLOPE_INVALID', '飞行不套地面坡度倍率')
    grade_squared = rational(grade_squared)
    require(grade_squared >= 0, 'BATTLE_SLOPE_INVALID', '坡度平方不能为负')
    for band in _terrain()['slope_bands']:
        lower, upper = band.get('min_exclusive'), band.get('max_inclusive')
        if ((lower is None or grade_squared > rational(lower)**2)
                and (upper is None or grade_squared <= rational(upper)**2)):
            value = band[kind]
            require(value is not None, 'BATTLE_SLOPE_INVALID', '超过兵种纵横坡上限')
            return rational(value)
    require(False, 'BATTLE_SLOPE_INVALID', '坡度档位缺失')


@dataclass(frozen=True)
class DirectionalSlope:
    longitudinal_squared: Fraction
    cross_squared: Fraction

    @property
    def grade_squared(self):
        return max(self.longitudinal_squared, self.cross_squared)


@dataclass(frozen=True)
class SupportTriangle:
    """同一明确支撑层中的真实三角面；坐标顺序x、高度y、z，单位米。"""
    vertices: tuple

    def __post_init__(self):
        require(isinstance(self.vertices, (list, tuple)) and len(self.vertices) == 3,
                'BATTLE_SUPPORT_INVALID', '支撑面须有三个顶点')
        object.__setattr__(self, 'vertices', tuple(_point(v, 3) for v in self.vertices))
        require(self.determinant != 0, 'BATTLE_SUPPORT_INVALID', '垂直面或退化面不能作为地面支撑')

    @property
    def determinant(self):
        a, b, c = self.vertices
        return (b[0]-a[0])*(c[2]-a[2]) - (c[0]-a[0])*(b[2]-a[2])

    @property
    def gradient(self):
        a, b, c = self.vertices
        dx = ((b[1]-a[1])*(c[2]-a[2]) - (c[1]-a[1])*(b[2]-a[2])) / self.determinant
        dz = ((b[0]-a[0])*(c[1]-a[1]) - (c[0]-a[0])*(b[1]-a[1])) / self.determinant
        return dx, dz

    def contains(self, point):
        x, z = _point(point, 2)
        signs = []
        for a, b in zip(self.vertices, self.vertices[1:] + self.vertices[:1]):
            signs.append((b[0]-a[0])*(z-a[2]) - (b[2]-a[2])*(x-a[0]))
        return min(signs) >= 0 or max(signs) <= 0

    def height(self, point):
        x, z = _point(point, 2)
        require(self.contains((x, z)), 'BATTLE_SUPPORT_INVALID', '不能在三角面外外推支撑高度')
        a = self.vertices[0]; dx, dz = self.gradient
        return a[1] + dx*(x-a[0]) + dz*(z-a[2])

    def directional_slope(self, start, end):
        """沿路线的纵坡与法向横坡；绝不只用起终点高度差判定坡度。"""
        start, end = _point(start, 2), _point(end, 2)
        vx, vz = end[0]-start[0], end[1]-start[1]
        length_squared = vx*vx + vz*vz
        require(length_squared > 0, 'BATTLE_GEOMETRY_INVALID', '零长度路线没有行驶方向')
        dx, dz = self.gradient
        return DirectionalSlope((dx*vx+dz*vz)**2 / length_squared,
                                (-dx*vz+dz*vx)**2 / length_squared)

    @property
    def maximum_local_grade_squared(self):
        """本面最陡方向，供完整占地的所有相交面做最大值汇总；不是中心点采样。"""
        dx, dz = self.gradient
        return dx*dx + dz*dz

    def route_interval(self, start, end):
        """精确裁剪直线段到投影三角面，返回闭区间t，不采样漏过窄陡坡。"""
        start, end = _point(start, 2), _point(end, 2)
        require(start != end, 'BATTLE_GEOMETRY_INVALID', '路线不能为零长度')
        low, high = Fraction(0), Fraction(1)
        orientation = 1 if self.determinant > 0 else -1
        for a, b in zip(self.vertices, self.vertices[1:] + self.vertices[:1]):
            cross = lambda p: orientation*((b[0]-a[0])*(p[1]-a[2])-(b[2]-a[2])*(p[0]-a[0]))
            initial, final = cross(start), cross(end)
            delta = final-initial
            if delta == 0:
                if initial < 0: return None
            elif delta > 0:
                low = max(low, -initial/delta)
            else:
                high = min(high, -initial/delta)
            if low > high: return None
        return low, high
