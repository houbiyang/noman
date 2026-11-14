#!/usr/bin/env python3
"""只读诊断青岚 v0.1：道路图乐观下界不代表实际可执行路线或公平认证。

运行：python3 docs/rules/tools/check_qinglan_v01.py
自测：python3 docs/rules/tools/check_qinglan_v01.py --self-test
仅依赖标准库；不调用现有比赛执行器、不改地图、不启动服务。
"""
import hashlib
import heapq
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
MAP = ROOT / 'unity-client/Assets/Sandbox/Resources/Maps/QinglanValley/map-definition.json'


def rect_interval(a, b, zone):
    """闭线段落入部署区的参数区间，包含穿区但端点均在区外的线段。"""
    lo, hi = 0.0, 1.0
    for axis, low, high in ((0, zone['minX'], zone['maxX']), (1, zone['minZ'], zone['maxZ'])):
        delta = b[axis] - a[axis]
        if abs(delta) < 1e-12:
            if not low <= a[axis] <= high:
                return None
        else:
            u, v = sorted(((low-a[axis])/delta, (high-a[axis])/delta))
            lo, hi = max(lo, u), min(hi, v)
            if lo > hi:
                return None
    return lo, hi


def disk_interval(a, b, center, radius):
    delta = (b[0]-a[0], b[1]-a[1])
    rel = (a[0]-center[0], a[1]-center[1])
    aa = sum(v*v for v in delta)
    if aa == 0:
        return (0.0, 1.0) if math.dist(a, center) <= radius else None
    bb = 2*sum(u*v for u, v in zip(delta, rel))
    cc = sum(v*v for v in rel)-radius*radius
    disc = bb*bb-4*aa*cc
    if disc < -1e-7:
        return None
    root = math.sqrt(max(0, disc))
    lo, hi = max(0, (-bb-root)/(2*aa)), min(1, (-bb+root)/(2*aa))
    return (lo, hi) if lo <= hi else None


def graph(source):
    nodes = {n['id']: (n['x'], n['z']) for n in source['nodes']}
    adj = {n: [] for n in nodes}
    for road in source['roads']:
        a, b = road['startId'], road['endId']
        weight = math.dist(nodes[a], nodes[b])
        adj[a].append((b, weight))
        adj[b].append((a, weight))
    return nodes, adj


def distances_from_zone(source, zone, nodes, adj):
    distances = {n: math.inf for n in nodes}
    intervals = {}
    for road in source['roads']:
        if road['isBridge']:
            continue
        a, b = road['startId'], road['endId']
        interval = rect_interval(nodes[a], nodes[b], zone)
        if interval is None:
            continue
        intervals[road['id']] = interval
        length = math.dist(nodes[a], nodes[b])
        distances[a] = min(distances[a], length*interval[0])
        distances[b] = min(distances[b], length*(1-interval[1]))
    heap = [(value, node) for node, value in distances.items() if math.isfinite(value)]
    heapq.heapify(heap)
    while heap:
        value, node = heapq.heappop(heap)
        if value != distances[node]:
            continue
        for other, weight in adj[node]:
            if value+weight < distances[other]:
                distances[other] = value+weight
                heapq.heappush(heap, (value+weight, other))
    return distances, intervals


def arrival_lower_bound(source, zone_state, nodes, center, radius=250):
    distances, source_intervals = zone_state
    answer = math.inf
    for road in source['roads']:
        # 仅以普通道路投影近似可占领地表，不把桥面视为中央地面占点。
        if road['isBridge']:
            continue
        a, b = road['startId'], road['endId']
        interval = disk_interval(nodes[a], nodes[b], center, radius)
        if interval is None:
            continue
        lo, hi = interval
        length = math.dist(nodes[a], nodes[b])
        answer = min(answer, distances[a]+lo*length, distances[b]+(1-hi)*length)
        if road['id'] in source_intervals:
            u, v = source_intervals[road['id']]
            answer = min(answer, max(0, lo-v, u-hi)*length)
    return answer if math.isfinite(answer) else None


def diagnose():
    raw = MAP.read_bytes()
    source = json.loads(raw)
    nodes, adj = graph(source)
    zones = {z['side']: z for z in source['deploymentZones']}
    zone_states = {side: distances_from_zone(source, zone, nodes, adj) for side, zone in zones.items()}
    remaining, components = set(nodes), []
    while remaining:
        stack, visited = [next(iter(remaining))], set()
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            stack.extend(n for n, _ in adj[node] if n not in visited)
        remaining -= visited
        components.append(len(visited))
    areas = {s: (z['maxX']-z['minX'])*(z['maxZ']-z['minZ']) for s, z in zones.items()}
    # 全部只是诊断探针，不是正式据点；不写回地图。
    probes = [(p['id'], (p['x'], p['z']), 'legacy_not_approved') for p in source['points'] if p['kind'] == 'task']
    for road in source['roads']:
        if not road['isBridge']:
            continue
        a, b = nodes[road['startId']], nodes[road['endId']]
        length = math.dist(a, b)
        unit = ((b[0]-a[0])/length, (b[1]-a[1])/length)
        probes += [(road['id']+'_west_land_probe', (a[0]-170*unit[0], a[1]-170*unit[1]), 'unapproved_probe'),
                   (road['id']+'_east_land_probe', (b[0]+170*unit[0], b[1]+170*unit[1]), 'unapproved_probe')]
    probes += [('DRAFT_W_LOWER', (2900,4400), 'unapproved_probe'),
               ('DRAFT_E_LOWER', (9000,4400), 'unapproved_probe'),
               ('DRAFT_W_UPPER', (2900,8500), 'unapproved_probe'),
               ('DRAFT_E_UPPER', (9000,8500), 'unapproved_probe')]
    results = []
    for name, center, status in probes:
        lengths = {side: arrival_lower_bound(source, state, nodes, center) for side, state in zone_states.items()}
        seconds = {side: (value*60/400 if value is not None else None) for side, value in lengths.items()}
        valid = all(value is not None for value in seconds.values())
        gap = abs(seconds['red']-seconds['blue']) if valid else None
        allowance = max(5, .05*min(seconds.values())) if valid else None
        results.append(dict(id=name, status=status, center_xz=list(center), capture_radius_m=250,
            ground_optimistic_arrival_seconds=seconds, absolute_gap_seconds=gap,
            permitted_gap_seconds=allowance, diagnostic_within_threshold=(gap <= allowance if valid else None)))
    by_id = {p['id']: p for p in results}
    pairs = []
    for west, east in [('TASK_A','TASK_C'), ('DRAFT_W_LOWER','DRAFT_E_LOWER'), ('DRAFT_W_UPPER','DRAFT_E_UPPER')]:
        red = by_id[west]['ground_optimistic_arrival_seconds']['red']
        blue = by_id[east]['ground_optimistic_arrival_seconds']['blue']
        valid = red is not None and blue is not None
        gap = abs(red-blue) if valid else None
        allowance = max(5,.05*min(red,blue)) if valid else None
        pairs.append(dict(west_id=west,east_id=east,red_to_own_seconds=red,blue_to_own_seconds=blue,
            absolute_gap_seconds=gap,permitted_gap_seconds=allowance,
            diagnostic_within_threshold=(gap <= allowance if valid else None)))
    hp, shot, capacity = 1500, 10, 60
    return dict(
        analysis_date='2026-11-11', rule_version='qinglan-rules-v0.1',
        map_relative_path=str(MAP.relative_to(ROOT)), map_file_sha256=hashlib.sha256(raw).hexdigest(),
        source_content_hash=source.get('contentHash'),
        limits=['平面无向道路图；未校验道路合法性、坡度、建筑、树林、水域、桥头连接或单位占地。',
                '假定可从部署区与道路相交处任意合法预部署；区外经批准接入段尚未加入。',
                '只计算圆盘与非桥道路交集，未使用最终地表掩码或森林禁停规则。',
                '因此这些是当前道路子模型的乐观到达估计，不是完整新导航模型的严格下界。',
                '同圆内不同入口独立取最早到达；未证明两个入口之间可通达。',
                '探针未通过几何审核，不是正式据点，不代表已穷举地图可行性。',
                '诊断过线不是公平通过，诊断不过线也不是整张地图无解证明。',
                '单探针双方时间差只对中央点公平筛查有意义；近点必须看paired_near_point_diagnostics。',
                '时间未量化到5秒，正式验收须使用实际单位占地、完整导航和离散步结算。'],
        inventory=dict(width_m=source['width'], length_m=source['length'], nodes=len(nodes), roads=len(source['roads']),
            bridges=sum(r['isBridge'] for r in source['roads']), buildings=len(source['buildings']),
            height_shape=[source['heightColumns'], source['heightRows']], graph_component_sizes=sorted(components),
            legacy_task_count=sum(p['kind']=='task' for p in source['points']),
            zero_length_roads=[r['id'] for r in source['roads'] if nodes[r['startId']]==nodes[r['endId']]],
            building_collision_extents_explicit=all(all(k in b for k in ('width','length','height')) for b in source['buildings'])),
        deployment=dict(zones=zones, areas_m2=areas, blue_to_red_area_ratio=areas['blue']/areas['red']),
        point_probe_diagnostics=results, paired_near_point_diagnostics=pairs,
        arithmetic=dict(step_seconds=5, match_seconds=3600, max_steps=720, points_per_owned_point_per_minute=12,
            uncontested_income_per_minute={str(n): n*12 for n in range(1,6)},
            score_only_minutes_after_income_begins={str(n): 1500/(12*n) for n in range(1,6)},
            full_enemy_roster_kill_points=4*5+12*10+4*8,
            core_ground_hits_required=math.ceil(hp/shot), one_ground_full_magazine_core_damage=capacity*shot,
            minimum_full_magazines_total=math.ceil(hp/(shot*capacity)),
            ground_one_ammo_refill_from_empty_seconds=math.ceil(60/2)*5,
            recon_continuous_flight_no_movement_energy_minutes=100/2,
            moving_ground_5s_distance_m=400/12, moving_ground_hit_damage=16*.6,
            moving_ground_core_damage=10*.6,
            base_hp_rate_per_unit_per_second=50/5, field_hp_rate_per_truck_per_second=2/5),
        release_gates=dict(five_point_geometry_approved=False, fixed_core_geometry_approved=False,
            collision_and_occlusion_geometry_verified=False, new_rules_engine_verified=False,
            single_match_geographic_fairness_verified=False),
        overall='规则草案可定稿；地图与运行实现不具备新模式公平验收结论。')


class GeometryTests(unittest.TestCase):
    def test_rect_crossing(self):
        z = dict(minX=1,maxX=2,minZ=0,maxZ=2)
        self.assertEqual(rect_interval((0,1),(4,1),z),(.25,.5))
    def test_rect_outside(self):
        self.assertIsNone(rect_interval((0,3),(4,3),dict(minX=1,maxX=2,minZ=0,maxZ=2)))
    def test_disk_crossing(self):
        self.assertEqual(disk_interval((0,0),(10,0),(5,0),1),(.4,.6))
    def test_disk_tangent(self):
        self.assertEqual(disk_interval((0,0),(10,0),(5,1),1),(.5,.5))
    def test_disk_outside(self):
        self.assertIsNone(disk_interval((0,0),(10,0),(5,2),1))
    def test_disk_zero_edge(self):
        self.assertEqual(disk_interval((1,0),(1,0),(0,0),1),(0,1))
    def test_same_edge_direct_entry(self):
        src=dict(nodes=[dict(id='a',x=0,z=0),dict(id='b',x=100,z=0)],
                 roads=[dict(id='r',startId='a',endId='b',isBridge=False)])
        ns, adj=graph(src)
        state=distances_from_zone(src,dict(minX=20,maxX=30,minZ=-1,maxZ=1),ns,adj)
        self.assertAlmostEqual(arrival_lower_bound(src,state,ns,(40,0),5),5)
    def test_disconnected(self):
        src=dict(nodes=[dict(id='a',x=0,z=0),dict(id='b',x=100,z=0)], roads=[])
        ns, adj=graph(src)
        state=distances_from_zone(src,dict(minX=20,maxX=30,minZ=-1,maxZ=1),ns,adj)
        self.assertIsNone(arrival_lower_bound(src,state,ns,(40,0),5))


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        unittest.main(argv=[sys.argv[0]])
    else:
        print(json.dumps(diagnose(),ensure_ascii=False,indent=2,allow_nan=False))
