"""总册地图交互。纯服务端组件；合法支撑/接触由冻结地表目录提供，绝不按平面距离猜层。

forest_polygons为真实规则覆盖的凸分片；相接合并，区间并集不会重复计算重叠片。
长度比较使用平方有理数，无浮点epsilon，无采样步长。服务/占点/停靠证据
须由适配器核验完整占地与实际高度；缺少目录抛错而非默认合法。
"""
from fractions import Fraction as F

from backend import battle_kernel as k
from backend.battle_support import convex_polygon, _intersection, _cross
from backend.demo_domain import require


def plane_mm(value):
    point = value['position_mm']
    return F(point['x_mm']), F(point['z_mm'])


def distance2(a, b):
    return sum((x-y)**2 for x, y in zip(plane_mm(a), plane_mm(b)))


def _interval(polygon, start, end):
    lo, hi = F(0), F(1)
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        initial = _cross(a, b, start)
        change = _cross(a, b, end)-initial
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


class ForestIndex:
    """米制闭多边形；输入空列表必须是来源明确的空覆盖，不能由调用者补默认值。"""
    def __init__(self, polygons):
        require(isinstance(polygons, (list, tuple)) and len(polygons) <= 2000,
                'BATTLE_FOREST_INVALID', '缺少森林覆盖目录或覆盖超出精确预算', 503)
        self.polygons = tuple(convex_polygon(p) for p in polygons)
        parent = list(range(len(self.polygons)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, a in enumerate(self.polygons):
            for j, b in enumerate(self.polygons[:i]):
                if _intersection(a, b):
                    parent[root(i)] = root(j)
        self.groups = tuple(root(i) for i in range(len(parent)))

    def at(self, point):
        return frozenset(self.groups[i] for i, polygon in enumerate(self.polygons)
                         if _interval(polygon, point, point) is not None)

    def target_fraction(self, start, end):
        """沿完整观察线穿过目标所属连通林区的参数长度并集，反复进出不重置。"""
        groups = self.at(end)
        intervals = sorted(interval for i, polygon in enumerate(self.polygons)
            if self.groups[i] in groups and (interval := _interval(polygon, start, end)) is not None)
        total, previous = F(0), None
        for lo, hi in intervals:
            if previous is not None and lo <= previous[1]:
                previous = previous[0], max(previous[1], hi)
            else:
                if previous is not None:
                    total += previous[1]-previous[0]
                previous = lo, hi
        return total + (previous[1]-previous[0] if previous else 0)

    def visible_range(self, source, target):
        start, end = [tuple(v/1000 for v in plane_mm(u)) for u in (source, target)]
        length2 = distance2(source, target)/1000000
        radius = dict(recon=1400, ground=900, support=700)[source['unit_type']]
        if source['unit_type'] == 'recon':
            if self.at(start):
                radius = 1000
            elif self.at(end):
                fraction = self.target_fraction(start, end)
                if length2*fraction*fraction > 600**2:
                    return False
        return length2 <= radius**2


class Interactions:
    """terrain须实现明确的服务端证据接口，见Geometry适配器，不能来自HTTP。"""
    def __init__(self, terrain, rays, forests):
        self.terrain, self.rays, self.forests = terrain, rays, forests

    def ray_clear(self, source, target):
        # 局部手动场景只授权已核验域内的整条观察/攻击线，不能跨未知区域。
        domain_check = getattr(self.terrain, "ray_domain_clear", None)
        if domain_check is not None and domain_check(source, target) is not True:
            return False
        # 美术飞行高度不是规则锚点。目录返回该x/z下明确绑定的真实规则支撑高度+2米。
        a, b = self.terrain.rule_anchor(source), self.terrain.rule_anchor(target)
        if a == b:
            return True  # 两个已确认实体外锚点完全相同，无非零线段可被中途阻挡。
        return self.rays.cast(a, b).mesh_clear

    def observe(self, state, *, start_positive, stable_observers):
        visible = {side: set() for side in k.SIDES}
        for source in state['units']:
            if not (k.alive(source) and source['unit_id'] in start_positive
                    and source['unit_id'] in stable_observers and k.number(source['energy']) > 0
                    and (source['unit_type'] != 'recon' or source['air_state'] == 'flying')):
                continue
            for target in state['units']:
                if k.alive(target) and source['side'] != target['side'] and self.forests.visible_range(source, target) \
                        and self.ray_clear(source, target):
                    visible[source['side']].add(target['unit_id'])
        return visible

    def fire_clear(self, source, target):
        # 当前可见、步初能源、冷却/接收者角色由统一内核另外检查；不缓存上一次观察授权。
        return (k.alive(source) and k.alive(target) and source['side'] != target['side']
                and source['unit_type'] == 'ground' and k.number(source['energy']) > 0
                and distance2(source, target) <= (800000 if target['unit_type'] == 'recon'
                    and target['air_state'] != 'docked' else 600000)**2
                and self.ray_clear(source, target))

    def service_clear(self, source, target):
        if source['side'] != target['side'] or distance2(source, target) > 100000**2:
            return False
        for unit in (source, target):
            if 'camp_id' in unit:
                if self.terrain.camp_service_legal(unit) is not True:
                    return False
            elif (unit['unit_type'] == 'recon' and unit['air_state'] != 'docked') \
                    or self.terrain.service_footprint_legal(unit) is not True:
                return False
        # 不是图最短路可达：必须真实直连，跨标签可以，跨不连续支撑层不可以。
        return self.terrain.service_segment_clear(source, target) is True

    def hover_energy_clear(self, source, target):
        """例外只免接收端停靠/服务占地及地面直连；供方合法停驻仍必需。"""
        return (k.allows_hover_energy(source, target, 'energy') and source['side'] == target['side']
                and distance2(source, target) <= 100000**2
                and self.terrain.service_footprint_legal(source) is True)

    def capture(self, state, before, motions):
        presence, residents = {}, {}
        old = {u['unit_id']: u for u in before['units']}
        for point in self.terrain.capture_points:
            identity = point['point_id']
            presence[identity] = {s: set() for s in k.SIDES}
            residents[identity] = {s: set() for s in k.SIDES}
            for unit in state['units']:
                uid = unit['unit_id']
                if not (k.alive(unit) and unit['unit_type'] in ('ground', 'support')
                        and self.terrain.capture_footprint_legal(unit, point) is True):
                    continue
                presence[identity][unit['side']].add(uid)
                trace = motions[uid].trace
                if old[uid]['position_mm'] == unit['position_mm'] and trace \
                        and all(p == old[uid]['position_mm'] for p in trace) \
                        and self.terrain.capture_footprint_legal(old[uid], point) is True:
                    residents[identity][unit['side']].add(uid)
        return presence, residents

    def flight_legal(self, unit, action):
        if unit['unit_type'] != 'recon':
            return False, False
        return self.terrain.dock_footprint_legal(unit) is True, self.terrain.air_legal(unit) is True

    def landing_clear(self, unit, state):
        if self.terrain.dock_footprint_legal(unit) is not True or self.terrain.air_legal(unit) is not True:
            return False
        for enemy in state['units']:
            if not k.alive(enemy) or enemy['side'] == unit['side'] \
                    or enemy['unit_type'] == 'recon' and enemy['air_state'] == 'flying':
                continue
            radius = 3 if enemy['unit_type'] == 'support' else 2
            # 敌对地面实际层也须核对；桥上桥下不能仅凭平面接近互相阻挡。
            if distance2(unit, enemy) <= ((2+radius+1)*1000)**2 \
                    and self.terrain.same_collision_layer(unit, enemy) is True:
                return False
        return True
