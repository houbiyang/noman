"""Server-side ground sweep/budget component; not a complete production Geometry.

Conservative circumscribed-square footprint (rule radius + 1m) proves all of the
true disc is supported. This can refuse a tight curved passage; it never shrinks
radii. Straight path arc lengths round UP to a micrometre, then all budget,
progress and collision calculations use exact rationals. No per-step rounding
reset. Air routes and navigation search belong to the complete adapter.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext, ROUND_CEILING
from fractions import Fraction as F
from itertools import combinations
import re
from math import isqrt

from backend import battle_kernel as k
from backend.battle_execution import Motion
from backend.battle_map_surfaces import rectangle, _point_inside
from backend.battle_support import convex_polygon
from backend.battle_terrain import MAX_GEOMETRY_DENOMINATOR, rational as q
from backend.demo_domain import require
from backend.runtime import ServiceError, sha256


def _exact(value):
    require(isinstance(value, str) and len(value) <= 80 and re.fullmatch(r'-?\d+(?:/\d+)?', value),
            'BATTLE_ROUTE_STALE', '精确位置必须使用有限有理数字符串')
    try:
        return q(F(value))
    except (ValueError, ZeroDivisionError):
        require(False, 'BATTLE_ROUTE_STALE', '精确位置有理数编码损坏')


def _bounded(value, quantum):
    """Bound non-authoritative interpolation evidence before JSON encoding."""
    value = F(value)
    return F(round(value / quantum)) * quantum



def record_path_segments(segments, progress=None, *, interrupted=False, quantum_mm=1):
    """FIFO accounting on the configured distance grid (default one mm).

    Labels/geometry survive internal terrain splits. Only the sum is quantized,
    never every triangle piece. The less-than-one-grid tail belongs to this route
    and is discarded on interruption/arrival, not spent on another route.
    """
    require(type(quantum_mm) is int and quantum_mm in (1, 100),
            'BATTLE_GEOMETRY_INVALID', '移动网格配置无效')
    progress = progress or {}
    pending = deepcopy(progress.get('path_pending_segments', []))
    total_recorded = progress.get('recorded_distance_mm', 0)
    require(isinstance(pending, list) and len(pending) <= 128
            and type(total_recorded) is int and total_recorded >= 0,
            'BATTLE_ROUTE_STALE', '累计路径毫米游标损坏')
    def length(segment):
        value = k.number(segment['length_mm'])
        require(value >= 0, 'BATTLE_ROUTE_STALE', '路径长度不能为负')
        return value
    require(sum((length(v) for v in pending), F(0)) < quantum_mm,
            'BATTLE_ROUTE_STALE', '同路线尾数必须小于移动网格')
    for item in pending:
        if 'path_begin_seconds' in item:
            item['path_begin_seconds'] = item['path_end_seconds'] = '0'
    queue = pending + deepcopy(list(segments))
    amount = sum((length(v) for v in queue), F(0))
    complete = (amount // quantum_mm) * quantum_mm
    remaining, billed, tail = F(complete), [], []
    for raw in queue:
        size = length(raw)
        if not size:
            continue
        take = min(size, remaining)
        if take:
            item = deepcopy(raw); item['length_mm'] = str(take)
            if take < size and 'path_start_m' in item:
                start, end = (tuple(_exact(v) for v in item[key]) for key in ('path_start_m', 'path_end_m'))
                item['path_end_m'] = [str(_bounded(v, F(1, 1000)) if quantum_mm == 100 else v) for v in lerp(start, end, take/size)]
                if 'path_begin_seconds' in item:
                    begin, end_time = (_exact(item[key]) for key in ('path_begin_seconds', 'path_end_seconds'))
                    item['path_end_seconds'] = str(_bounded(begin+(end_time-begin)*take/size, F(1, 1000000)) if quantum_mm == 100 else begin+(end_time-begin)*take/size)
            billed.append(item); remaining -= take
        if take < size:
            item = deepcopy(raw); item['length_mm'] = str(size-take)
            if take and 'path_start_m' in item:
                item['path_start_m'] = deepcopy(billed[-1]['path_end_m'])
                if 'path_begin_seconds' in item:
                    item['path_begin_seconds'] = billed[-1]['path_end_seconds']
            tail.append(item)
    require(len(tail) <= 128, 'BATTLE_MOTION_BUDGET_EXCEEDED', '亚毫米几何尾段超限', 503)
    return tuple(billed), ([] if interrupted else tail), total_recorded+complete


def pending_path_energy(kind, progress):
    pending = (progress or {}).get('path_pending_segments', [])
    # The unrecorded tail is below the configured grid (at most 100mm).
    return F(k.movement_cost(kind, pending)) if pending else F(0)


def hull(points):
    """Exact monotone hull; no sampling between path endpoints."""
    points = sorted(set(points))
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    lower, upper = [], []
    for p in points:
        while len(lower) > 1 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) > 1 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return convex_polygon(lower[:-1]+upper[:-1])


def sweep(a, b, radius):
    return hull(rectangle(*a, radius)+rectangle(*b, radius))


def lerp(a, b, t):
    return tuple(x+(y-x)*t for x, y in zip(a, b))


def arc_length(a, b):
    squared = sum((x-y)**2 for x, y in zip(a, b))
    numerator, denominator = isqrt(squared.numerator), isqrt(squared.denominator)
    if numerator*numerator == squared.numerator and denominator*denominator == squared.denominator:
        return F(numerator, denominator)
    with localcontext() as ctx:
        ctx.prec = 80
        raw = (Decimal(squared.numerator)/Decimal(squared.denominator)).sqrt()
        length = F(int((raw*10**6).to_integral_value(rounding=ROUND_CEILING)), 10**6)
    # Decimal is used to propose an integer root, not decide which side of a
    # boundary is legal; verify/correct its square using exact arithmetic.
    while length*length < squared:
        length += F(1, 10**6)
    while length and (length-F(1, 10**6))**2 >= squared:
        length -= F(1, 10**6)
    return length


def position_m(unit):
    p = unit['position_mm']
    return q(p['x_mm'])/1000, q(p['z_mm'])/1000


def _mm(point, height, quantum_mm=1):
    # This is presentation only. Exact position is retained in movement_progress.
    return dict(x_mm=round(point[0]*1000/quantum_mm)*quantum_mm,
                z_mm=round(point[1]*1000/quantum_mm)*quantum_mm, height_mm=round(height*1000))


@dataclass(frozen=True)
class Piece:
    start: tuple
    end: tuple
    begin: F
    end_time: F
    length: F
    surface: str
    grade_squared: F
    surface_id: str
    leg_index: int
    leg_start: tuple
    leg_end: tuple
    leg_length: F
    end_distance: F


@dataclass(frozen=True)
class Plan:
    unit: dict
    origin: tuple
    pieces: tuple
    route_digest: str
    finished: bool
    progress: dict | None = None
    interrupted: bool = False
    extra_solids: tuple = ()

    def at(self, time):
        previous = self.origin
        for piece in self.pieces:
            if time <= piece.begin:
                return previous
            if time <= piece.end_time:
                return lerp(piece.start, piece.end, (time-piece.begin)/(piece.end_time-piece.begin))
            previous = piece.end
        return previous

    def truncate(self, time):
        pieces = []
        for p in self.pieces:
            if p.begin >= time:
                break
            if p.end_time <= time:
                pieces.append(p)
            else:
                ratio = (time-p.begin)/(p.end_time-p.begin)
                pieces.append(replace(p, end=lerp(p.start, p.end, ratio), end_time=time,
                    length=p.length*ratio, end_distance=p.end_distance-p.length*(1-ratio)))
                break
        return replace(self, pieces=tuple(pieces), finished=False, interrupted=True)


def collision_time(a, b, *, duration=F(5)):
    """Earliest conservative safe time for two synchronous rule discs.

    Quadratic minima are exact. Irrational contact roots get an 80-bit rational
    lower bracket, always before collision. No endpoint-only/step-size tunneling.
    """
    radius = F(k.profiles()[a.unit['unit_type']]['ground_radius_m'] +
               k.profiles()[b.unit['unit_type']]['ground_radius_m'] + 1)
    ticks = sorted({F(0), duration} | {p.end_time for p in a.pieces} | {p.end_time for p in b.pieces})
    def distance(t):
        return sum((x-y)**2 for x, y in zip(a.at(t), b.at(t))) - radius**2
    require(distance(F(0)) > 0, 'BATTLE_ENEMY_OVERLAP', '步初敌对占地不合法，不能驱逐或互穿')
    for start, end in zip(ticks, ticks[1:]):
        r0 = tuple(x-y for x, y in zip(a.at(start), b.at(start)))
        r1 = tuple(x-y for x, y in zip(a.at(end), b.at(end)))
        delta = tuple(y-x for x, y in zip(r0, r1))
        norm = sum(v*v for v in delta)
        t = max(F(0), min(F(1), -sum(x*y for x, y in zip(r0, delta))/norm)) if norm else F(0)
        middle = start+(end-start)*t
        if distance(middle) > 0:
            continue
        low, high = start, middle
        for _ in range(80):
            mid = (low+high)/2
            if distance(mid) > 0:
                low = mid
            else:
                high = mid
        # Normalize the absolute contact time to one global dyadic grid. Repeated
        # third-unit rechecks must not multiply rational denominators until they
        # overflow the geometry/JSON precision budget. Rounding is always EARLY.
        grid = 2**40
        return max(start, F((low*grid).numerator//(low*grid).denominator, grid))
    return None


class MapMotion:
    def __init__(self, catalog, *, surface_transition_persistence=False, camp_collisions=True, distance_quantum_mm=1):
        self.catalog = catalog
        self.surface_transition_persistence = surface_transition_persistence
        self.camp_collisions = camp_collisions
        self.distance_quantum_mm = distance_quantum_mm

    def _footprint(self, unit, surface_id, start, end, purpose='move'):
        radius = k.profiles()[unit['unit_type']]['ground_radius_m']+1
        return self.catalog.footprint(surface_id, sweep(start, end, radius), side=unit['side'],
            purpose=purpose, kind=unit['unit_type'], direction=(start, end) if start != end else None)

    def _midpoint_evidence(self, unit, surface_id, middle, swept):
        # Endpoints may be individually representable but their midpoint not.
        # Its footprint is contained in the verified sweep: using all swept
        # faces can only increase the maximum grade used for costing.
        if self.distance_quantum_mm == 100 and any(v.denominator > MAX_GEOMETRY_DENOMINATOR for v in middle):
            return swept
        return self._footprint(unit, surface_id, middle, middle)

    @staticmethod
    def _polygon_interval(polygon, start, end):
        low, high = F(0), F(1)
        for a, b in zip(polygon, polygon[1:]+polygon[:1]):
            def cross(p):
                return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
            initial, final = cross(start), cross(end)
            delta = final-initial
            if delta == 0:
                if initial < 0:
                    return None
            elif delta > 0:
                low = max(low, -initial/delta)
            else:
                high = min(high, -initial/delta)
            if low > high:
                return None
        return low, high

    def _breaks(self, surface_id, start, end, radius, *, safe_prefix=False):
        """Exact center AND full-footprint contact events; never samples.

        Execution may use only the first budgeted intervals, then safely stop at
        their last boundary. No contact inside that prefix is discarded. Strict
        callers still reject an over-budget complete query.
        """
        breaks = {F(0), F(1)}
        bounds = (min(start[0], end[0])-radius, min(start[1], end[1])-radius,
                  max(start[0], end[0])+radius, max(start[1], end[1])+radius)
        for _, _, tri in self.catalog.triangles(surface_id, bounds=bounds):
            interval = tri.route_interval(start, end)
            if interval is not None:
                breaks.update(interval)
            # Triangle Minkowski-summed with the complete symmetric footprint:
            # its interval marks when any part of the unit starts/stops touching
            # that triangle. This prevents charging a later slope for an entire
            # earlier piece, or missing a thin adverse cross-slope.
            expanded = hull([(v[0]+dx, v[2]+dz) for v in tri.vertices
                             for dx, dz in rectangle(0, 0, radius)])
            interval = self._polygon_interval(expanded, start, end)
            if interval is not None:
                breaks.update(interval)
        for region in self.catalog.semantic_regions(surface_id, bounds):
            interval = self._polygon_interval(region['polygon'], start, end)
            if interval is not None:
                breaks.update(interval)
        ordered = sorted(breaks)
        if safe_prefix:
            return ordered[:120]
        require(len(ordered) <= 120, 'BATTLE_MOTION_BUDGET_EXCEEDED', '路线精确分段预算超限', 503)
        return ordered

    @staticmethod
    def _travel_limit(kind, seconds):
        # A spatial broad phase only, NOT an approval or an alternate speed rule.
        # All supported frozen slope/surface factors participate in the upper
        # bound; exact segment_budget remains the sole time/energy adjudicator.
        parameters = k._parameters()
        terrain = parameters['terrain']
        surface = max(k.number(str(v)) for v in terrain['surface_factors'][kind].values() if v is not None)
        slope = max(k.number(str(b[kind])) for b in terrain['slope_bands'] if b[kind] is not None)
        return F(parameters['unit_profiles'][kind]['speed_m_per_minute'])*surface*slope*seconds/60

    def _transition(self, previous, current, point, unit):
        if previous == current:
            return
        require(self.surface_transition_persistence is True, 'BATTLE_LAYER_TRANSITION_NOT_READY',
                '须先接入最终surface_id持久化，不能在完成移动时丢失新层', 503)
        approved = [c for c in self.catalog.payload['connections'] if c.get('accepted') is True
            and c.get('from_surface_id') == previous and c.get('to_surface_id') == current
            and unit['side'] in c.get('sides', [])
            and _point_inside(point, convex_polygon(c['portal']))]
        require(approved, 'BATTLE_LAYER_CONNECTION_MISSING', '未批准层连接；不能按近高程跳层')
        # Full footprint must be supported on both sides; a portal is not a ramp.
        self._footprint(unit, previous, point, point)
        self._footprint(unit, current, point, point)
        require(self.catalog.height(previous, point) == self.catalog.height(current, point),
                'BATTLE_SUPPORT_INVALID', '层连接实际高度不连续')

    def details(self, unit, legs, *, seconds=5, moving_attack=False, extra_solids=()):
        require(unit['unit_type'] in ('ground', 'support') or
                (unit['unit_type'] == 'recon' and unit.get('air_state') != 'flying'),
                'BATTLE_AIR_GEOMETRY_NOT_READY', '飞行路线须由已验收空域组件处理', 503)
        require(isinstance(legs, (list, tuple)) and len(legs) <= 32 and 0 < q(seconds) <= 5,
                'BATTLE_ROUTE_INVALID', '路线或内部时间预算无效')
        require(all(isinstance(l, dict) and isinstance(l.get('surface_id'), str)
                    and isinstance(l.get('end_m'), (tuple, list)) and len(l['end_m']) == 2 for l in legs),
                'BATTLE_ROUTE_INVALID', '服务端路线缺少明确层或米制端点')
        require(unit['unit_type'] != 'recon' or not legs, 'BATTLE_ROUTE_FORBIDDEN', '停靠无人机不能水平移动')
        # Frozen server resolvers and JSON restoration may encode Fraction as a/b.
        # Decode once before digesting or geometry; never feed a/b to Decimal.
        legs = [dict(surface_id=l['surface_id'], end_m=[
            _exact(v) if isinstance(v, str) and '/' in v else q(v)
            for v in l['end_m']]) for l in legs]
        digest = sha256([dict(surface_id=l['surface_id'], end_m=[str(v) for v in l['end_m']]) for l in legs])
        previous = unit['surface_id']
        origin, progress = position_m(unit), unit.get('movement_progress')
        first_leg, resumed_start, resumed_distance, resumed_length = 0, None, F(0), None
        require(not unit.get('geometry_position'), 'BATTLE_ROUTE_STALE',
                '旧跨路线精确位置不得隐式迁移为权威起点')
        if progress:
            require(isinstance(progress, dict) and progress.get('catalog_sha256') == self.catalog.digest
                    and progress.get('schema_version') == 'qinglan-map-progress-v1'
                    and isinstance(progress.get('exact_position_m'), list)
                    and len(progress['exact_position_m']) == 2
                    and progress.get('surface_id') == previous,
                    'BATTLE_ROUTE_STALE', '持久路线进度缺少目录或层绑定')
            require(progress.get('rule_position_mm') == unit['position_mm'],
                    'BATTLE_ROUTE_STALE', '规则毫米点与同路线游标绑定不一致')
            if not progress.get('position_only'):
                require(not legs or progress.get('route_sha256') == digest,
                        'BATTLE_ROUTE_STALE', '新路线不能继承旧路线余数')
                origin = tuple(_exact(v) for v in progress['exact_position_m'])
                first_leg = progress['leg_index']
                resumed_start = tuple(_exact(v) for v in progress['leg_start_m'])
                resumed_distance, resumed_length = _exact(progress['distance_m']), _exact(progress['leg_length_m'])
                require(type(first_leg) is int and first_leg >= 0 and resumed_length > 0
                        and 0 <= resumed_distance <= resumed_length,
                        'BATTLE_ROUTE_STALE', '路线进度越界')
                require(not legs or (first_leg < len(legs)
                        and resumed_length == arc_length(resumed_start, tuple(legs[first_leg]['end_m']))
                        and origin == lerp(resumed_start, tuple(legs[first_leg]['end_m']), resumed_distance/resumed_length)),
                        'BATTLE_ROUTE_STALE', '路线精确起点、长度或距离损坏')
                pending = progress.get('path_pending_segments', [])
                if pending:
                    require(tuple(_exact(v) for v in pending[-1]['path_end_m']) == origin,
                            'BATTLE_ROUTE_STALE', '预算末点与未记录路径尾部不一致')
        require(round(self.catalog.height(previous, position_m(unit))*1000) == unit['position_mm']['height_mm'],
                'BATTLE_SUPPORT_INVALID', '权威毫米点高程不是当前真实支撑面')
        self._footprint(unit, previous, position_m(unit), position_m(unit), 'stop')
        self._footprint(unit, previous, origin, origin, 'stop')
        from backend.battle_map_surfaces import _touch
        radius = k.profiles()[unit['unit_type']]['ground_radius_m']+1
        require(not any(_touch(rectangle(*origin, radius), solid['polygon']) for solid in extra_solids),
                'BATTLE_STATIC_BLOCKED', '步初完整占地压在本局兵营实体上')
        reservation = F(5, 4) if moving_attack and unit['unit_type'] == 'ground' else F(1)
        time_left, energy, spent, pieces = q(seconds), max(F(0), k.number(unit['energy']) - pending_path_energy(unit['unit_type'], progress)*reservation), F(0), []
        def result(finished=False, interrupted=False):
            return Plan(deepcopy(unit), origin, tuple(pieces), digest, finished, deepcopy(progress),
                        interrupted, tuple(deepcopy(extra_solids)))
        # Previously unbilled sub-mm pieces also become trace entries when the
        # next whole mm completes. Reserve their slots instead of overflowing
        # serialization after an otherwise successful locked-step calculation.
        piece_limit = max(0, 127-len((progress or {}).get('path_pending_segments', [])))
        current = origin
        for index, leg in enumerate(legs):
            if index < first_leg:
                continue
            endpoint = tuple(q(v) for v in leg['end_m'])
            require(len(endpoint) == 2, 'BATTLE_ROUTE_INVALID', '路线坐标须为米制x、z')
            surface_id = leg['surface_id']
            if current == endpoint:
                continue
            self._transition(previous, surface_id, current, unit)
            leg_start = resumed_start if index == first_leg and resumed_start is not None else current
            length = resumed_length if index == first_leg and resumed_length is not None else arc_length(current, endpoint)
            distance_before = resumed_distance if index == first_leg and resumed_start is not None else F(0)
            remaining_length = length-distance_before
            # Far-away contact events cannot affect this step's time budget.
            # Keep the ORIGINAL leg arc length and cursor: the query endpoint is
            # not a waypoint, arrival, authority snap or a path-rounding reset.
            horizon = min(F(1), self._travel_limit(unit['unit_type'], time_left)/remaining_length)
            query_end = lerp(current, endpoint, horizon)
            breaks = [t*horizon for t in self._breaks(surface_id, current, query_end, radius, safe_prefix=True)]
            start_of_remainder = current
            for lo, hi in zip(breaks, breaks[1:]):
                if len(pieces) >= piece_limit:
                    return result(interrupted=True)
                a, b = lerp(start_of_remainder, endpoint, lo), lerp(start_of_remainder, endpoint, hi)
                surface = self.catalog.center_surface(surface_id, lerp(a, b, F(1, 2)))
                # Worst true directional slope across the full swept footprint.
                # Fail-closed static invalid candidate can still allow its legal prefix.
                radius = k.profiles()[unit['unit_type']]['ground_radius_m']+1
                def check(fraction):
                    end = lerp(a, b, fraction)
                    evidence = self._footprint(unit, surface_id, a, end)
                    self._footprint(unit, surface_id, end, end, 'stop')
                    for solid in extra_solids:
                        require(not _touch(sweep(a, end, radius), solid['polygon']),
                                'BATTLE_STATIC_BLOCKED', '完整扫掠触及本局兵营实体')
                    return evidence
                blocked = {'BATTLE_SUPPORT_INVALID', 'BATTLE_ROUTE_FORBIDDEN', 'BATTLE_STATIC_BLOCKED',
                           'BATTLE_TERRAIN_FORBIDDEN', 'BATTLE_OVERLAY_FORBIDDEN', 'BATTLE_SLOPE_INVALID'}
                if self.distance_quantum_mm == 100:
                    blocked.add('BATTLE_SURFACE_OUTSIDE_VERIFIED_DOMAIN')
                try:
                    evidence = check(F(1)); safe = F(1)
                except ServiceError as error:
                    if error.code not in blocked:
                        raise
                    # A zero-length prefix must itself be legal. Unknown data is
                    # not converted to a partial approval.
                    evidence = check(F(0))
                    low, high = F(0), F(1)
                    bracket_mm = remaining_length*(hi-lo)*1000
                    for _ in range(60):
                        if self.distance_quantum_mm == 100 and (high-low)*bracket_mm <= 100:
                            break  # retain the proven inside endpoint, never round outward
                        mid = (low+high)/2
                        try:
                            check(mid); low = mid
                        except ServiceError as error:
                            if error.code not in blocked:
                                raise
                            high = mid
                    safe = low
                    evidence = check(safe)
                middle = lerp(a, b, safe/2)
                evidence = self._midpoint_evidence(unit, surface_id, middle, evidence)
                grades = [self.catalog.assets.triangle(part, i).directional_slope(a, b).grade_squared
                          for part, i in evidence.face_ids]
                grade = max(grades)
                requested = remaining_length*(hi-lo)*safe
                segment = dict(length_mm=str(requested*1000), surface=surface, grade_squared=str(grade))
                budget = k.segment_budget(unit['unit_type'], [segment], str(max(F(0), energy-spent*reservation)),
                    seconds=str(time_left), moving_attack=moving_attack)
                actual = F(budget['lengths_mm'][0])/1000
                if actual == 0:
                    return result(interrupted=True)
                ratio = actual/(remaining_length*(hi-lo))
                end = lerp(a, b, ratio)
                self._footprint(unit, surface_id, end, end, 'stop')
                used = time_left-F(budget['remaining_seconds'])
                pieces.append(Piece(a, end, q(seconds)-time_left, q(seconds)-time_left+used,
                    actual, surface, grade, surface_id, index, leg_start, endpoint, length,
                    distance_before+remaining_length*lo+actual))
                spent += F(budget['energy']); time_left -= used
                current = end
                if ratio < 1 or safe < 1 or time_left == 0:
                    arrived = index == len(legs)-1 and current == endpoint
                    return result(arrived, not arrived and (safe < 1 or
                                  (ratio < 1 and time_left > 0) or energy-spent*reservation == 0))
            if breaks[-1] < horizon:
                # A genuinely dense reachable prefix exhausted the unchanged
                # geometry budget. Every emitted piece (including its stop
                # footprint) has been proven. Existing Executor semantics clear
                # only the sub-mm cursor on interruption, NOT the intent: it may
                # retry next step, or the user may stop/retarget next round. Do
                # not report arrival or leave the locked round unresolvable.
                return result(interrupted=True)
            previous = surface_id
        return result(bool(legs), not legs)

    def as_motion(self, plan):
        try:
            return self._as_motion(plan)
        except ServiceError as error:
            # Coarse local-grid snapping may cross a slope/support boundary
            # even though the continuous cursor was legal. Hold at the already
            # validated authority for this step, clear its tail and charge zero.
            # Unknown/corrupt data and formal-mm execution still fail closed.
            if self.distance_quantum_mm != 100 or error.code not in {
                    'BATTLE_SLOPE_INVALID', 'BATTLE_SUPPORT_INVALID',
                    'BATTLE_SURFACE_OUTSIDE_VERIFIED_DOMAIN', 'BATTLE_ROUTE_FORBIDDEN',
                    'BATTLE_STATIC_BLOCKED', 'BATTLE_TERRAIN_FORBIDDEN', 'BATTLE_OVERLAY_FORBIDDEN'}:
                raise
            return self._as_motion(plan.truncate(F(0)))

    def _as_motion(self, plan):
        continuous = tuple(dict(length_mm=str(p.length*1000), surface=p.surface,
            grade_squared=str(p.grade_squared), path_start_m=[str(v) for v in p.start],
            path_end_m=[str(v) for v in p.end], path_surface_id=p.surface_id,
            path_begin_seconds=str(_bounded(p.begin, F(1, 1000000)) if self.distance_quantum_mm == 100 else p.begin),
            path_end_seconds=str(_bounded(p.end_time, F(1, 1000000)) if self.distance_quantum_mm == 100 else p.end_time)) for p in plan.pieces)
        terminal = plan.finished or plan.interrupted
        segments, pending, recorded = record_path_segments(continuous, plan.progress, interrupted=terminal, quantum_mm=self.distance_quantum_mm)
        trace = [deepcopy(plan.unit['position_mm'])]
        physical = position_m(plan.unit)
        actual_surface = plan.unit['surface_id']
        from backend.battle_map_surfaces import _touch
        for segment in segments:
            point = tuple(_exact(v) for v in segment['path_end_m'])
            surface = segment['path_surface_id']
            mm = _mm(point, self.catalog.height(surface, point), self.distance_quantum_mm)
            snapped = (F(mm['x_mm'], 1000), F(mm['z_mm'], 1000))
            # Millimetre coding must never grant support/permission to an unsafe
            # point. Recompute true height at the authority point, not the cursor.
            mm['height_mm'] = round(self.catalog.height(surface, snapped)*1000)
            self._transition(actual_surface, surface, physical, plan.unit)
            self._footprint(plan.unit, surface, physical, snapped)
            self._footprint(plan.unit, surface, snapped, snapped, 'stop')
            radius = k.profiles()[plan.unit['unit_type']]['ground_radius_m']+1
            require(not any(_touch(sweep(physical, snapped, radius), v['polygon']) for v in plan.extra_solids),
                    'BATTLE_STATIC_BLOCKED', '毫米权威扫掠触及兵营实体')
            trace.append(mm); physical, actual_surface = snapped, surface
        progress = deepcopy(plan.progress) if plan.progress else dict(
            schema_version='qinglan-map-progress-v1', position_only=True,
            catalog_sha256=self.catalog.digest, surface_id=actual_surface,
            exact_position_m=[str(v) for v in physical])
        if plan.pieces:
            p = plan.pieces[-1]
            progress = dict(schema_version='qinglan-map-progress-v1', catalog_sha256=self.catalog.digest,
                route_sha256=plan.route_digest, surface_id=actual_surface, leg_index=p.leg_index,
                exact_position_m=[str(v) for v in p.end], leg_start_m=[str(v) for v in p.leg_start],
                distance_m=str(p.end_distance), leg_length_m=str(p.leg_length))
        require(len(trace) <= 128, 'BATTLE_MOTION_BUDGET_EXCEEDED', '毫米轨迹分段超限', 503)
        progress.update(path_pending_segments=pending, recorded_distance_mm=recorded,
                        rule_position_mm=deepcopy(trace[-1]))
        if terminal:
            progress = dict(schema_version='qinglan-map-progress-v1', position_only=True,
                catalog_sha256=self.catalog.digest, surface_id=actual_surface,
                exact_position_m=[str(v) for v in physical], path_pending_segments=[],
                recorded_distance_mm=recorded, rule_position_mm=deepcopy(trace[-1]))
        return Motion(trace=tuple(trace), segments=segments, progress=progress,
                      finished=plan.finished, surface_id=actual_surface,
                      interrupted=plan.interrupted, budget_segments=continuous)

    def plan(self, unit, legs, **kwargs):
        return self.as_motion(self.details(unit, legs, **kwargs))

    def _recorded_plan(self, plan):
        """Independently sweep the real mm trajectory, not only its budget cursor."""
        motion = self.as_motion(plan)
        physical, clock, pieces = position_m(plan.unit), F(0), []
        for point, segment in zip(motion.trace[1:], motion.segments):
            end = (F(point['x_mm'], 1000), F(point['z_mm'], 1000))
            end_time = max(clock, _exact(segment['path_end_seconds']))
            pieces.append(Piece(physical, end, clock, end_time,
                F(segment['length_mm'])/1000, segment['surface'], F(segment['grade_squared']),
                segment['path_surface_id'], 0, physical, end,
                F(segment['length_mm'])/1000, F(segment['length_mm'])/1000))
            physical, clock = end, end_time
        return replace(plan, origin=position_m(plan.unit), pieces=tuple(pieces))

    def motions(self, state, *, route_resolver, start_positive, service_locked, transitions):
        """Geometry.motions signature plus mandatory trusted route_resolver(unit).

        Returns all live units together; no state mutation. No navigation fallback.
        Start/takeoff footprints remain blocking for the whole step. Stable flying
        units require a separate accepted air implementation (not stationary lies).
        """
        plans, output = {}, {}
        # Local manual demos may traverse camp models. Omit only these dynamic
        # camp solids so start, continuous sweep and recorded-mm checks agree.
        solids = ([dict(surface_id=c['surface_id'], polygon=rectangle(*position_m(c), 30))
                   for c in state['camps']] if self.camp_collisions else [])
        for unit in state['units']:
            if not k.alive(unit):
                continue
            identity = unit['unit_id']
            if unit['unit_type'] == 'recon' and unit.get('air_state') == 'flying':
                require(False, 'BATTLE_AIR_GEOMETRY_NOT_READY', '需完整空域运动组件接入', 503)
            moving = (identity in start_positive and identity not in service_locked and identity not in transitions
                      and unit.get('movement'))
            legs = route_resolver(deepcopy(unit)) if moving else []
            plans[identity] = self.details(unit, legs, moving_attack=(unit.get('task') or {}).get('action')
                                           in ('attack', 'attack_area'), extra_solids=solids)
        # Globally earliest collision, truncate both, then recompute against all
        # resulting stopped paths. In particular a stopped leader can block a third.
        for _ in range(256):
            hits = []
            recorded = {identity: self._recorded_plan(plan) for identity, plan in plans.items()}
            for a, b in combinations(plans.values(), 2):
                if a.unit['side'] == b.unit['side']:
                    continue  # friendly overlaps are explicitly legal
                for first, second in ((a, b), (recorded[a.unit['unit_id']], recorded[b.unit['unit_id']])):
                    time = collision_time(first, second)
                    if time is not None:
                        hits.append((time, a.unit['unit_id'], b.unit['unit_id']))
            if not hits:
                break
            time, a, b = min(hits)
            plans[a], plans[b] = plans[a].truncate(time), plans[b].truncate(time)
        else:
            require(False, 'BATTLE_MOTION_BUDGET_EXCEEDED', '同步碰撞证明预算超限', 503)
        for identity, plan in plans.items():
            output[identity] = self.as_motion(plan)
        return output
