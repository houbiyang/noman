"""发布三角面的连续线段审计；只提供几何证据，不代替完整规则地图发布。"""

from collections import defaultdict
import math


def _interval(start, end, corners):
    """按三角形三条边裁剪完整二维线段，保留真实交点和狭小缺口。"""
    determinant = ((corners[1][0] - corners[0][0]) * (corners[2][2] - corners[0][2])
                   - (corners[1][2] - corners[0][2]) * (corners[2][0] - corners[0][0]))
    if abs(determinant) < 1e-12:
        return None
    orientation = 1 if determinant > 0 else -1
    enter, leave = 0., 1.
    for first, last in zip(corners, corners[1:] + corners[:1]):
        def side(point):
            return orientation * ((last[0] - first[0]) * (point[1] - first[2])
                                  - (last[2] - first[2]) * (point[0] - first[0]))
        origin, destination = side(start), side(end)
        delta = destination - origin
        if abs(delta) < 1e-12:
            if origin < -1e-9:
                return None
            continue
        crossing = -origin / delta
        if delta > 0:
            enter = max(enter, crossing)
        else:
            leave = min(leave, crossing)
        if enter > leave + 1e-12:
            return None
    return max(0., enter), min(1., leave)


class SurfaceIndex:
    """以空间分桶检索已发布支撑三角面，保留重叠高度与每片真实坡度。

    此索引不能证明完整单位占地、动态阻挡、地表标签或路线公平性；
    调用方必须继续执行对应规则，不能因中心线连续而直接放行。
    """

    def __init__(self, parts: list, cell_m: float = 128):
        """验证输入几何后创建索引；退化投影不作为可通行地面。

        Args:
            parts: 调用方明确选择的发布片段，不自动按颜色推断地表。
            cell_m: 仅控制检索分桶尺寸，不控制几何采样精度。
        """
        if not math.isfinite(cell_m) or cell_m <= 0:
            raise ValueError('空间索引尺寸必须为有限正数')
        self.cell_m, self.triangles = cell_m, []
        self.buckets = defaultdict(list)
        for part in parts:
            vertices, triangles = part['vertices'], part['triangles']
            if (len(vertices) % 3 or len(triangles) % 3
                    or not all(type(value) in (int, float) and math.isfinite(value) for value in vertices)
                    or not all(type(index) is int and 0 <= index < len(vertices) // 3 for index in triangles)):
                raise ValueError('发布支撑面顶点或索引无效')
            for offset in range(0, len(triangles), 3):
                corners = [vertices[index * 3:index * 3 + 3] for index in triangles[offset:offset + 3]]
                first, second, third = corners
                horizontal = [second[index] - first[index] for index in range(3)]
                vertical = [third[index] - first[index] for index in range(3)]
                determinant = horizontal[0] * vertical[2] - vertical[0] * horizontal[2]
                if abs(determinant) < 1e-12:
                    continue
                gradient = ((horizontal[1] * vertical[2] - vertical[1] * horizontal[2]) / determinant,
                            (horizontal[0] * vertical[1] - vertical[0] * horizontal[1]) / determinant)
                record = dict(part=part.get('name', ''), triangle=offset // 3, corners=corners,
                              gradient=gradient, grade=math.hypot(*gradient))
                identity = len(self.triangles)
                self.triangles.append(record)
                lower = (min(point[0] for point in corners), min(point[2] for point in corners))
                upper = (max(point[0] for point in corners), max(point[2] for point in corners))
                for bucket in self._cells(lower, upper):
                    self.buckets[bucket].append(identity)

    def _cells(self, lower, upper):
        """枚举查询矩形覆盖的索引桶，边界点保留相邻面的候选资格。"""
        for column in range(math.floor(lower[0] / self.cell_m), math.floor(upper[0] / self.cell_m) + 1):
            for row in range(math.floor(lower[1] / self.cell_m), math.floor(upper[1] / self.cell_m) + 1):
                yield column, row

    def _candidates(self, lower, upper):
        """返回唯一三角面候选；分桶不会将几何简化为离散格。"""
        identities = {identity for bucket in self._cells(lower, upper) for identity in self.buckets.get(bucket, ())}
        return [self.triangles[identity] for identity in sorted(identities)]

    @staticmethod
    def _height(record, point):
        """使用支撑三角面的平面方程计算高程。"""
        origin = record['corners'][0]
        return (origin[1] + record['gradient'][0] * (point[0] - origin[0])
                + record['gradient'][1] * (point[1] - origin[2]))

    def at(self, horizontal: float, depth: float) -> list:
        """返回坐标处全部实际支撑面，保留桥上和桥下的不同高度。"""
        point = (horizontal, depth)
        return [dict(part=record['part'], triangle=record['triangle'], height=self._height(record, point),
                     grade=record['grade'], gradient=list(record['gradient']))
                for record in self._candidates(point, point) if _interval(point, point, record['corners']) is not None]

    def trace(self, start, end, start_height=None, height_tolerance_m=.01) -> dict:
        """按全部三角边交点切分线段，检查支撑覆盖、连接高度及沿线最大坡度。

        Args:
            start: 米制平面起点，依次为x、z。
            end: 米制平面终点，依次为x、z。
            start_height: 多层地表必须指定当前支撑高程，不能选取最接近目标的一层。
            height_tolerance_m: 接缝判定公差，不用于填充平面缺口。

        Returns:
            连续性、缺口参数区间、高差及分段坡度；含歧义时拒绝宣称连续。
        """
        if (len(start) != 2 or len(end) != 2
                or not all(math.isfinite(value) for value in (*start, *end))
                or not math.isfinite(height_tolerance_m) or height_tolerance_m < 0
                or (start_height is not None and not math.isfinite(start_height))):
            raise ValueError('连续地表查询参数无效')
        lower = tuple(min(left, right) for left, right in zip(start, end))
        upper = tuple(max(left, right) for left, right in zip(start, end))
        intersections = [(record, interval) for record in self._candidates(lower, upper)
                         if (interval := _interval(start, end, record['corners'])) is not None]
        breaks = sorted({0., 1., *(value for _, interval in intersections for value in interval)})
        segments, gaps, discontinuities, ambiguities = [], [], [], []
        branches = []

        def point(ratio):
            return tuple(left + (right - left) * ratio for left, right in zip(start, end))

        for enter, leave in zip(breaks, breaks[1:]):
            if leave - enter <= 1e-12:
                continue
            middle = (enter + leave) / 2
            active = [record for record, interval in intersections if interval[0] <= middle <= interval[1]]
            if not active:
                gaps.append([enter, leave])
                branches = []
                continue
            if not branches:
                heights = [self._height(record, point(enter)) for record in active]
                initial_height = start_height if enter == 0 else None
                if initial_height is None and max(heights) - min(heights) > height_tolerance_m:
                    ambiguities.append(dict(at=enter, heights_m=sorted(set(heights))))
                branches = [dict(height=min(heights) if initial_height is None else initial_height,
                                 maximum_grade=0., previous=None, segment=None)]
            following = []
            for record in active:
                altitude = self._height(record, point(enter))
                predecessors = [branch for branch in branches if abs(altitude - branch['height']) <= height_tolerance_m]
                if not predecessors:
                    continue
                previous = min(predecessors, key=lambda branch: branch['maximum_grade'])
                segment = dict(start=enter, end=leave, part=record['part'], triangle=record['triangle'],
                               height_start=altitude, height_end=self._height(record, point(leave)), grade=record['grade'])
                following.append(dict(height=segment['height_end'], maximum_grade=max(previous['maximum_grade'], record['grade']),
                                      previous=previous, segment=segment))
            if not following:
                gap = min(abs(self._height(record, point(enter)) - branch['height']) for record in active for branch in branches)
                discontinuities.append(dict(at=enter, height_gap_m=gap))
            # 保留全部可达支撑分支，只有其末端高程才能参与下一段连接。
            branches = following
        chosen = min(branches, key=lambda branch: (branch['maximum_grade'], branch['height'])) if branches else None
        while chosen and chosen['segment'] is not None:
            segments.append(chosen['segment'])
            chosen = chosen['previous']
        segments.reverse()
        return dict(continuous=bool(segments) and not (gaps or discontinuities or ambiguities), segments=segments,
                    gaps=gaps, discontinuities=discontinuities, ambiguities=ambiguities,
                    maximum_grade=max((item['grade'] for item in segments), default=None))
