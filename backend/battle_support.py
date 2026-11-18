"""真实网格的完整凸多边形支撑证据（只读，不是生产Geometry）。

面积用有理数精确裁剪/差集；重叠三角形不能重复计面积来补洞。明确选取
支撑部件，遇多层高差不按ID选面。结果不代表覆盖层、圆形占地、碰撞、
服务接入、桥头连接或道路权限已验收，绝不自动注入执行器。
"""
from dataclasses import dataclass
from fractions import Fraction as F
from itertools import combinations

from backend.battle_terrain import SupportTriangle, rational
from backend.battle_terrain_assets import TerrainAssets
from backend.demo_domain import require


def _cross(a, b, p):
    return (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0])


def _clean(points):
    result = []
    for p in points:
        if not result or p != result[-1]:
            result.append(p)
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    return tuple(result)


def signed_area(points):
    if len(points) < 3:
        return F(0)
    return sum((a[0]*b[1]-a[1]*b[0] for a, b in zip(points, points[1:]+points[:1])), F(0)) / 2


def convex_polygon(points):
    require(isinstance(points, (list, tuple)) and 3 <= len(points) <= 16,
            'BATTLE_FOOTPRINT_INVALID', '占地须有3至16个有序凸多边形顶点')
    require(all(isinstance(p, (list, tuple)) and len(p) == 2 for p in points),
            'BATTLE_FOOTPRINT_INVALID', '占地坐标须为x、z米')
    points = tuple(tuple(rational(v) for v in p) for p in points)
    area = signed_area(points)
    require(area != 0 and len(set(points)) == len(points),
            'BATTLE_FOOTPRINT_INVALID', '占地不能退化或重复顶点')
    if area < 0:
        points = tuple(reversed(points))
    require(all(_cross(a, b, p) >= 0 for a, b in zip(points, points[1:]+points[:1]) for p in points),
            'BATTLE_FOOTPRINT_INVALID', '占地必须是无自交的凸多边形')
    return points


def _halfplane(points, a, b, inside=True):
    """闭半平面精确裁剪；保留线/点接触，检测高差时不能丢掉共边。"""
    result = []
    if not points:
        return ()
    sign = 1 if inside else -1
    for p, q in zip(points, points[1:]+points[:1]):
        dp, dq = sign*_cross(a, b, p), sign*_cross(a, b, q)
        if dp >= 0:
            result.append(p)
        if (dp < 0 < dq) or (dq < 0 < dp):
            t = dp/(dp-dq)
            result.append((p[0]+t*(q[0]-p[0]), p[1]+t*(q[1]-p[1])))
    return _clean(result)


def _intersection(subject, clip):
    for a, b in zip(clip, clip[1:]+clip[:1]):
        subject = _halfplane(subject, a, b)
        if not subject:
            break
    return subject


def _subtract(subject, clip):
    """凸多边形减去一个凸面，输出互不重叠的正面积凸碎片。"""
    remaining, result = subject, []
    for a, b in zip(clip, clip[1:]+clip[:1]):
        outside = _halfplane(remaining, a, b, inside=False)
        if signed_area(outside) > 0:
            result.append(outside)
        remaining = _halfplane(remaining, a, b)
        if signed_area(remaining) == 0:
            break
    return result


def _bounds(points):
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def _overlap(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


@dataclass(frozen=True)
class Face:
    part_id: str
    triangle_index: int
    triangle: SupportTriangle
    projection: tuple
    bounds: tuple

    @property
    def identity(self):
        return self.part_id, self.triangle_index


@dataclass(frozen=True)
class FootprintEvidence:
    area_m2: F
    uncovered_area_m2: F
    inside_playable_bounds: bool
    face_ids: tuple
    non_support_face_ids: tuple
    height_conflicts: tuple
    max_grade_squared: F | None
    min_height_m: F | None
    max_height_m: F | None

    @property
    def coverage_complete(self):
        return self.inside_playable_bounds and self.uncovered_area_m2 == 0

    @property
    def continuous_support(self):
        return self.coverage_complete and not self.height_conflicts

    def to_dict(self):
        # 分数不转换为浮点；这些字段只能作证据，不能作场景发布标记。
        return dict(schema_version='qinglan-convex-support-evidence-v1',
                    area_m2=str(self.area_m2), uncovered_area_m2=str(self.uncovered_area_m2),
                    inside_playable_bounds=self.inside_playable_bounds,
                    coverage_complete=self.coverage_complete, continuous_support=self.continuous_support,
                    face_ids=self.face_ids, non_support_face_ids=self.non_support_face_ids,
                    height_conflicts=self.height_conflicts, max_grade_squared=_text(self.max_grade_squared),
                    min_height_m=_text(self.min_height_m), max_height_m=_text(self.max_height_m),
                    grants_route_permission=False, production_ready=False,
                    overlays_resolved=False, collisions_validated=False)


def _text(value):
    return None if value is None else str(value)


class SupportIndex:
    """惰性索引：只对候选部件提取三角形，不将百万装饰面全转成有理数。

    必须明确选取支撑部件；不能把桥、河床和远景混为同一张地面。
    预算超限直接拒绝证据，不能把尚未检查部分视作通过。
    """
    def __init__(self, assets):
        self.assets = assets
        bounds = assets.manifest['playable_bounds_m']
        self.playable_bounds = tuple(rational(bounds[k]) for k in ('min_x', 'min_z', 'max_x', 'max_z'))
        self._bounds = {}
        self._faces = {}

    @classmethod
    def current(cls):
        return cls(TerrainAssets.current())

    def _part(self, part_id):
        if part_id not in self._faces:
            faces, vertical = [], []
            count = self.assets.face_count(part_id)
            require(count <= 50000, 'BATTLE_SUPPORT_BUDGET_EXCEEDED', '部件超出只读支撑索引预算', 503)
            for i in range(count):
                vertices = self.assets.face_vertices(part_id, i)
                projected = tuple((v[0], v[2]) for v in vertices)
                bounds = _bounds(projected)
                area = signed_area(projected)
                if area == 0:
                    vertical.append(((part_id, i), projected, bounds))
                    continue
                if area < 0:
                    projected = tuple(reversed(projected))
                faces.append(Face(part_id, i, SupportTriangle(vertices), projected, bounds))
            self._faces[part_id] = tuple(faces), tuple(vertical)
        return self._faces[part_id]

    def footprint(self, polygon, *, support_parts, support_faces=None):
        polygon = convex_polygon(polygon)
        require(isinstance(support_parts, (list, tuple)) and 0 < len(support_parts) <= 64
                and all(isinstance(p, str) and p for p in support_parts)
                and len(set(support_parts)) == len(support_parts),
                'BATTLE_SUPPORT_INVALID', '须显式选择不重复的支撑部件，不能默认全图地面')
        if support_faces is not None:
            require(isinstance(support_faces, dict) and set(support_faces) == set(support_parts)
                    and all(isinstance(ids, (list, tuple)) and ids
                        and all(type(i) is int and 0 <= i < self.assets.face_count(part) for i in ids)
                        and len(set(ids)) == len(ids) for part, ids in support_faces.items()),
                    'BATTLE_SUPPORT_INVALID', '显式支撑面索引必须有效且不重复')
        allowed_faces = None if support_faces is None else {p: set(ids) for p, ids in support_faces.items()}
        bounds = _bounds(polygon)
        contacts, vertical_ids = [], []
        for part_id in sorted(support_parts):
            # 即使部件没有接触也必须核验ID，不能悄悄忽略拼错的支撑层。
            if part_id not in self._bounds:
                self._bounds[part_id] = self.assets.part_bounds(part_id)
            if not _overlap(bounds, self._bounds[part_id]):
                continue
            faces, vertical = self._part(part_id)
            for face in faces:
                if support_faces is not None and face.triangle_index not in allowed_faces[part_id]:
                    continue
                if _overlap(bounds, face.bounds):
                    clipped = _intersection(face.projection, polygon)
                    if clipped:
                        contacts.append((face, clipped))
            for identity, projection, face_bounds in vertical:
                if support_faces is not None and identity[1] not in allowed_faces[part_id]:
                    continue
                if _overlap(bounds, face_bounds) and _intersection(projection, polygon):
                    vertical_ids.append(identity)
        require(len(contacts) <= 2000, 'BATTLE_SUPPORT_BUDGET_EXCEEDED', '占地接触面超出精确证明预算', 503)
        fragments = [polygon]
        for face, _ in contacts:
            fragments = [piece for fragment in fragments for piece in _subtract(fragment, face.projection)]
            require(len(fragments) <= 20000, 'BATTLE_SUPPORT_BUDGET_EXCEEDED', '占地差集超出精确证明预算', 503)
            if not fragments:
                break
        conflicts = []
        # 检查交叠、共边和点接触；不能把相邻但有台阶的两个面当作连续桥坡。
        for (a, ca), (b, cb) in combinations(contacts, 2):
            if not _overlap(_bounds(ca), _bounds(cb)):
                continue
            shared = _intersection(_intersection(a.projection, b.projection), polygon)
            if shared and any(a.triangle.height(p) != b.triangle.height(p) for p in shared):
                conflicts.append((a.identity, b.identity))
        heights = [face.triangle.height(p) for face, clipped in contacts for p in clipped]
        x0, z0, x1, z1 = self.playable_bounds
        return FootprintEvidence(signed_area(polygon), sum((signed_area(p) for p in fragments), F(0)),
            all(x0 <= p[0] <= x1 and z0 <= p[1] <= z1 for p in polygon),
            tuple(face.identity for face, _ in contacts), tuple(vertical_ids), tuple(conflicts),
            max((face.triangle.maximum_local_grade_squared for face, _ in contacts), default=None),
            min(heights, default=None), max(heights, default=None))
