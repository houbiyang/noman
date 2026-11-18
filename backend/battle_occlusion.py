"""冻结网格的精确线段遮挡证据（米制x/y/z），不替代完整Geometry。

地形模块7/8：观察和射击分别检测；不能因无人机看见就批准坦克开火。
调用者必须提供服务端已确认的遮挡/透明部件目录。未分类部件不能默认透明，
局部检查只能证明命中，不能证明全场景畅通。森林连通覆盖、距离与来源资格
由上层另验；还须核验锚点不在实体内部及实体厚度。不凭材质名猜兵营，也不据此发布场景或启动战斗。
"""
from dataclasses import dataclass
from fractions import Fraction as F

from backend.battle_terrain import _point
from backend.demo_domain import require


def _sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _dot(a, b):
    return sum((x*y for x, y in zip(a, b)), F(0))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _bounds(vertices):
    return tuple(min(v[i] for v in vertices) for i in range(3)) + tuple(
        max(v[i] for v in vertices) for i in range(3))


def _box_interval(start, end, bounds):
    lo, hi = F(0), F(1)
    for i in range(3):
        delta = end[i]-start[i]
        if delta == 0:
            if not bounds[i] <= start[i] <= bounds[i+3]:
                return None
        else:
            a, b = sorted(((bounds[i]-start[i])/delta, (bounds[i+3]-start[i])/delta))
            lo, hi = max(lo, a), min(hi, b)
            if lo > hi:
                return None
    return lo, hi


def triangle_interval(start, end, vertices):
    """线段和闭三角形相交参数区间。共面/擦边也精确检查，不使用epsilon。

    退化面不是透明面：上游必须先明确处理其语义，不能静默授予射击权限。
    """
    start, end = _point(start, 3), _point(end, 3)
    require(start != end, 'BATTLE_RAY_INVALID', '射线锚点不能重合')
    require(isinstance(vertices, (tuple, list)) and len(vertices) == 3,
            'BATTLE_RAY_INVALID', '遮挡面必须有三个顶点')
    a, b, c = tuple(_point(v, 3) for v in vertices)
    normal = _cross(_sub(b, a), _sub(c, a))
    require(any(normal), 'BATTLE_RAY_UNRESOLVED', '退化遮挡面尚未验收')
    direction = _sub(end, start)
    denominator = _dot(normal, direction)
    numerator = _dot(normal, _sub(a, start))
    if denominator:
        t = numerator / denominator
        if not 0 <= t <= 1:
            return None
        point = tuple(x+t*d for x, d in zip(start, direction))
        if all(_dot(normal, _cross(_sub(v, u), _sub(point, u))) >= 0
               for u, v in ((a, b), (b, c), (c, a))):
            return t, t
        return None
    if numerator:
        return None
    # 共面时按三个半平面裁剪完整线段；仅端点在三角形外不能证明不相交。
    lo, hi = F(0), F(1)
    for u, v in ((a, b), (b, c), (c, a)):
        initial = _dot(normal, _cross(_sub(v, u), _sub(start, u)))
        change = _dot(normal, _cross(_sub(v, u), direction))
        if change == 0:
            if initial < 0:
                return None
        elif change > 0:
            lo = max(lo, -initial/change)
        else:
            hi = min(hi, -initial/change)
        if lo > hi:
            return None
    return lo, hi


@dataclass(frozen=True)
class RayHit:
    part_id: str
    face_index: int
    interval: tuple


@dataclass(frozen=True)
class RayEvidence:
    hits: tuple
    checked_parts: tuple
    unresolved_parts: tuple
    geometry_sha256: str | None = None

    @property
    def blocked(self):
        return bool(self.hits)

    @property
    def mesh_clear(self):
        """仅完整目录下的静态网格畅通；不等于观察/攻击授权。"""
        return not self.hits and not self.unresolved_parts

    def public(self):
        """仅用于管理员离线验收，不直接返回阵营视图或隐蔽阻挡位置。"""
        return dict(blocked=self.blocked, mesh_clear=self.mesh_clear, geometry_sha256=self.geometry_sha256,
            checked_parts=self.checked_parts, unresolved_parts=self.unresolved_parts,
            hits=[dict(part_id=h.part_id, face_index=h.face_index,
                       interval=[str(t) for t in h.interval]) for h in self.hits],
            production_ready=False, grants_attack_permission=False,
            forest_visibility_validated=False)


class RayIndex:
    """使用经资产摘要校验的TerrainAssets；部件目录必须来自服务端验收。

    不自动猜测routes、logistics或settlement的透明性。整部件排除仅用于
    已确认透明的物件；源目标支撑面例外只忽略对应单面在端点的接触。
    """
    def __init__(self, assets, *, blocker_parts, transparent_parts=()):
        self.assets = assets
        self._ids = frozenset(assets.part_ids())
        for parts in (blocker_parts, transparent_parts):
            require(isinstance(parts, (tuple, list)) and all(type(p) is str and p for p in parts)
                    and len(parts) == len(set(parts)) and set(parts) <= self._ids,
                    'BATTLE_RAY_INVALID', '遮挡目录含未知或重复部件')
        require(not set(blocker_parts) & set(transparent_parts),
                'BATTLE_RAY_INVALID', '部件不能同时遮挡和透明')
        require(all(assets.face_count(part) > 0 for part in blocker_parts),
                'BATTLE_RAY_UNRESOLVED', '遮挡部件缺少真实三角面')
        self.blockers = tuple(sorted(blocker_parts))
        self.unresolved = tuple(sorted(self._ids - set(blocker_parts) - set(transparent_parts)))
        self._bounds, self._faces = {}, {}

    def _part(self, part):
        if part not in self._faces:
            count = self.assets.face_count(part)
            require(count <= 50000, 'BATTLE_RAY_BUDGET_EXCEEDED', '部件超出射线验收预算')
            faces = tuple(self.assets.face_vertices(part, i) for i in range(count))
            self._faces[part] = tuple((vertices, _bounds(vertices)) for vertices in faces)
        return self._faces[part]

    def cast(self, start, end, *, source_faces=(), target_faces=()):
        start, end = _point(start, 3), _point(end, 3)
        require(start != end, 'BATTLE_RAY_INVALID', '射线锚点不能重合')
        for excluded in (source_faces, target_faces):
            require(isinstance(excluded, (tuple, list)) and len(excluded) <= 32,
                    'BATTLE_RAY_INVALID', '端点面目录无效')
            for identity in excluded:
                require(isinstance(identity, tuple) and len(identity) == 2
                        and identity[0] in self.blockers and type(identity[1]) is int
                        and 0 <= identity[1] < self.assets.face_count(identity[0]),
                        'BATTLE_RAY_INVALID', '只能排除已登记的端点单面')
        hits, visited = [], 0
        for part in self.blockers:
            if part not in self._bounds:
                self._bounds[part] = self.assets.part_bounds3d(part)
            if _box_interval(start, end, self._bounds[part]) is None:
                continue
            for index, (vertices, bounds) in enumerate(self._part(part)):
                visited += 1
                require(visited <= 250000, 'BATTLE_RAY_BUDGET_EXCEEDED', '射线候选面超出验收预算')
                if _box_interval(start, end, bounds) is None:
                    continue
                interval = triangle_interval(start, end, vertices)
                if interval is None:
                    continue
                if interval == (0, 0) and (part, index) in source_faces:
                    continue
                if interval == (1, 1) and (part, index) in target_faces:
                    continue
                hits.append(RayHit(part, index, interval))
        return RayEvidence(tuple(sorted(hits, key=lambda h: (h.interval[0], h.part_id, h.face_index))),
                           self.blockers, self.unresolved,
                           getattr(self.assets, 'manifest', {}).get('geometry_sha256'))
