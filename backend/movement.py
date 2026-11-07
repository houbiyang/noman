"""青岚三兵种移动测试：版本化通行、模式、分段成本与部署试走。"""

import base64
from copy import deepcopy
from functools import lru_cache
import hashlib
import heapq
import json
import math
from pathlib import Path
import zlib

from backend.demo_domain import require, reset_plan
from backend.runtime import sha256
from backend import spatial

FOLDER = Path(__file__).parent


def is_garrisoned(state, unit):
    """识别尚未出营的部署单位，兼容旧快照中没有出营标记的单位。"""
    return (state['lifecycle']['phase'] == 'preparing' and not unit.get('movement_departed')
            and not unit.get('movement_test_steps',0)
            and any(camp['camp_id']==unit.get('camp_id') and camp.get('position') for camp in state['camps']))


@lru_cache(maxsize=1)
def profile():
    """读取独立移动参数，不覆盖既有 S2 冻结配置。"""
    return json.loads((FOLDER / 'movement-test-profile.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def navigation():
    """核对语义网格完整性，保留发布时的源文件摘要。"""
    data = json.loads((FOLDER / 'movement-map.json').read_text(encoding='utf-8'))
    grid = zlib.decompress(base64.b64decode(data.pop('grid_zlib_base64')))
    require(len(grid) == data['columns'] * data['rows'] and hashlib.sha256(grid).hexdigest() == data['grid_sha256'],
            'MOVEMENT_MAP_INVALID', '移动测试地图完整性检查失败')
    return data, grid


class Navigator:
    """为一次命令提供同一场景、规则与单位占地的完整通行检查。"""

    def __init__(self, state, unit, mode):
        """绑定当前地图及规则，当前单位所属兵营只开放出入口走廊。"""
        self.state, self.unit, self.mode = state, unit, mode
        self.data = spatial.map_data(state)
        self.meta, self.grid = navigation()
        require(self.meta['source_sha256'] == state['manifest']['map_source_sha256'],
                'MOVEMENT_MAP_STALE', '移动测试区域与当前地图不一致，请重新发布通行数据')
        self.rules = profile()
        self.kind = unit['unit_type']
        self.parameters = self.rules['unit_profiles'][self.kind]
        self.cell, self.columns, self.rows = self.meta['cell_m'], self.meta['columns'], self.meta['rows']
        self.radius = self.parameters['radius_m'] + self.rules['safety_margin_m']
        self.cache = {}
        self.camps = [(camp, spatial.meters(camp['position'])) for camp in state['camps'] if camp.get('position')]

    def terrain(self, point):
        """查询显式测试地表，越界返回未知。"""
        if not (0 <= point[0] < self.columns*self.cell and 0 <= point[1] < self.rows*self.cell):
            return 'unknown'
        return self.meta['terrain_names'][self.grid[int(point[1]/self.cell)*self.columns+int(point[0]/self.cell)]]

    def clear(self, point):
        """在单次寻路内复用同一占地检查，避免网格邻接搜索重复扫描。"""
        key=tuple(point)
        if key not in self.cache:
            self.cache[key]=self._clear(point)
        return self.cache[key]

    def _clear(self, point):
        """检查完整圆形占地与网格包络，空中执行独立的二维禁飞规则。"""
        if not (self.radius <= point[0] <= self.data['width']-self.radius
                and self.radius <= point[1] <= self.data['length']-self.radius):
            return False
        if self.kind == 'recon':
            for polygon in self.meta['no_fly_polygons']:
                inside = False
                previous = polygon[-1]
                for current in polygon:
                    if (current[1] > point[1]) != (previous[1] > point[1]) and point[0] < (previous[0]-current[0])*(point[1]-current[1])/(previous[1]-current[1])+current[0]:
                        inside = not inside
                    previous = current
                if inside:
                    return False
            return True
        allowed = self.rules['terrain_factors'][self.kind]
        column, row = int(point[0]/self.cell), int(point[1]/self.cell)
        for dz in (-1, 0, 1):
            for dx in (-1, 0, 1):
                left, top = (column+dx)*self.cell, (row+dz)*self.cell
                nearest = (max(left, min(point[0], left+self.cell)), max(top, min(point[1], top+self.cell)))
                if math.dist(point, nearest) < self.radius and self.terrain((left+self.cell/2, top+self.cell/2)) not in allowed:
                    return False
        for camp, center in self.camps:
            extent = camp.get('half_extent_m', 30)
            # 本单位兵营保留从中心到前方出入口的测试通道，其他占地始终阻挡。
            if camp['camp_id'] == self.unit.get('camp_id') and abs(point[0]-center[0]) <= self.radius and point[1] >= center[1]:
                continue
            if abs(point[0]-center[0]) < extent+self.radius and abs(point[1]-center[1]) < extent+self.radius:
                return False
        return True

    def grade(self, start, end):
        """桥面按独立层取平面坡度，地面按冻结高程检查局部坡度。"""
        if self.terrain(start) == 'bridge' or self.terrain(end) == 'bridge':
            return 0
        length = math.dist(start, end)
        return abs(spatial.height(self.data, end)-spatial.height(self.data, start))/length if length else 0

    def segment(self, start, end):
        """采样完整路径，返回通行成本；不可通行返回空。"""
        distance = math.dist(start, end)
        count = max(1, math.ceil(distance/(self.cell/2)))
        pieces = []
        previous = tuple(start)
        for index in range(1, count+1):
            point = tuple(start[axis]+(end[axis]-start[axis])*index/count for axis in (0, 1))
            middle = tuple((previous[axis]+point[axis])/2 for axis in (0, 1))
            if not self.clear(point) or not self.clear(middle):
                return None
            terrain = 'air' if self.kind == 'recon' else self.terrain(middle)
            factor = 1 if self.kind == 'recon' else self.rules['terrain_factors'][self.kind].get(terrain, 0)
            grade = 0 if self.kind == 'recon' else self.grade(previous, point)
            slope = 1 if grade <= .10 else (.85 if self.kind == 'ground' else .70) if grade <= .20 else .60 if self.kind == 'ground' and grade <= .35 else 0
            if not factor or not slope:
                return None
            mode_speed, mode_energy = 1, 1
            if self.mode == 'fast_move' and (self.kind == 'recon' or terrain in ('road', 'bridge', 'gravel')):
                mode_speed, mode_energy = self.rules['fast_speed_factor'], self.rules['fast_energy_factors'][self.kind]
            elif self.mode == 'reverse_move':
                mode_speed, mode_energy = self.rules['reverse_speed_factors'][self.kind], self.rules['reverse_energy_factor']
            length = math.dist(previous, point)
            speed = self.parameters['speed_m_per_step']*factor*slope*mode_speed
            energy = self.parameters['energy_per_km']/1000*mode_energy/(factor*slope)
            pieces.append(dict(start=list(previous), end=list(point), length_m=length,
                               speed_m_per_step=speed, energy_per_m=energy, terrain=terrain))
            previous = point
        return pieces

    def grid_path(self, start, end):
        """在有界局部搜索中按通行时间寻路，不以直线代替搜索失败。"""
        origin = (int(start[0]/self.cell), int(start[1]/self.cell))
        destination = (int(end[0]/self.cell), int(end[1]/self.cell))
        center = lambda item: ((item[0]+.5)*self.cell, (item[1]+.5)*self.cell)
        queue = [(0, origin)]
        costs, parents, closed = {origin: 0}, {}, set()
        while queue:
            _, current = heapq.heappop(queue)
            if current in closed:
                continue
            if current == destination:
                chain = [tuple(end)]
                while current != origin:
                    chain.append(center(current)); current = parents[current]
                chain.append(tuple(start)); chain.reverse()
                if self.segments(chain) is not None:
                    return chain
                return None
            closed.add(current)
            require(len(closed) <= 180000, 'ROUTE_SEARCH_LIMIT', '路线搜索范围过大，请添加途经点分段测试')
            for dx, dz in ((-1,0),(0,-1),(0,1),(1,0),(-1,-1),(-1,1),(1,-1),(1,1)):
                next_cell = (current[0]+dx, current[1]+dz)
                if next_cell in closed or not (0 <= next_cell[0] < self.columns and 0 <= next_cell[1] < self.rows):
                    continue
                start_point = tuple(start) if current == origin else center(current)
                end_point = tuple(end) if next_cell == destination else center(next_cell)
                pieces = self.segment(start_point, end_point)
                if pieces is None:
                    continue
                candidate = costs[current]+sum(piece['length_m']/piece['speed_m_per_step'] for piece in pieces)
                if candidate+1e-12 >= costs.get(next_cell, math.inf):
                    continue
                costs[next_cell], parents[next_cell] = candidate, current
                heuristic = math.dist(end_point, end)/(self.parameters['speed_m_per_step']*1.25)
                heapq.heappush(queue, (candidate+heuristic, next_cell))
        return None

    def segments(self, points):
        """检查并拼接每个途经段，任何中段非法都会拒绝整条路线。"""
        pieces = []
        for start, end in zip(points, points[1:]):
            part = self.segment(start, end)
            if part is None:
                return None
            pieces.extend(part)
        merged = []
        for piece in pieces:
            if piece['length_m'] <= 1e-6:
                continue
            if merged:
                previous = merged[-1]
                same_rule = (previous['terrain'] == piece['terrain']
                             and abs(previous['speed_m_per_step']-piece['speed_m_per_step']) <= 1e-9
                             and abs(previous['energy_per_m']-piece['energy_per_m']) <= 1e-12)
                previous_direction = (previous['end'][0]-previous['start'][0], previous['end'][1]-previous['start'][1])
                current_direction = (piece['end'][0]-piece['start'][0], piece['end'][1]-piece['start'][1])
                same_line = (abs(previous_direction[0]*current_direction[1]-previous_direction[1]*current_direction[0]) <= 1e-7
                             and previous_direction[0]*current_direction[0]+previous_direction[1]*current_direction[1] > 0
                             and math.dist(previous['end'],piece['start']) <= 1e-6)
                if same_rule and same_line:
                    previous['end'] = list(piece['end'])
                    previous['length_m'] = math.dist(previous['start'], previous['end'])
                    continue
            merged.append(piece)
        return merged

    def road_path(self, start, end, required=True):
        """通过道路边投影接入道路图，校验桥梁和两端完整接入段。"""
        nodes = {node['id']:(node['x'],node['z']) for node in self.data['nodes']}
        graph = {key:[] for key in nodes}
        candidates = [[], []]
        for road in self.data['roads']:
            first, last = nodes[road['startId']], nodes[road['endId']]
            if road['width'] < 2*self.radius:
                continue
            # 道路几何只在搜索访问到对应边时校验，避免每条指令预扫整张地图。
            graph[road['startId']].append((road['endId'], None, [first,last]))
            graph[road['endId']].append((road['startId'], None, [last,first]))
            delta = (last[0]-first[0], last[1]-first[1])
            squared = sum(value*value for value in delta)
            for side, point in enumerate((start, end)):
                ratio = max(0,min(1,sum((point[axis]-first[axis])*delta[axis] for axis in (0,1))/squared))
                projected = tuple(first[axis]+ratio*delta[axis] for axis in (0,1))
                if math.dist(point,projected) <= self.rules['road_access_limit_m']:
                    access = self.segment(point,projected)
                    if access is not None:
                        candidates[side].append((math.dist(point,projected),road,projected))
        if not all(candidates):
            require(not required, 'ROAD_ACCESS_UNAVAILABLE', '150 米内没有合法道路接入，请改用普通移动或重新选点')
            return None
        graph['@start'], graph['@end'] = [], []
        for side, label in ((0,'@start'),(1,'@end')):
            point = start if side == 0 else end
            for _, road, projected in sorted(candidates[side],key=lambda item:(item[0],item[1]['id']))[:8]:
                for node_id in (road['startId'],road['endId']):
                    points = [point,projected,nodes[node_id]]
                    pieces = self.segments(points)
                    if pieces is None:
                        continue
                    cost = sum(piece['length_m']/piece['speed_m_per_step'] for piece in pieces)
                    if side == 0:
                        graph[label].append((node_id,cost,points))
                    else:
                        graph[node_id].append((label,cost,list(reversed(points))))
        for _, first_road, first in candidates[0]:
            for _, last_road, last in candidates[1]:
                if first_road['id'] == last_road['id']:
                    points = [start,first,last,end]
                    pieces = self.segments(points)
                    if pieces is not None:
                        graph['@start'].append(('@end',sum(piece['length_m']/piece['speed_m_per_step'] for piece in pieces),points))
        queue, costs, previous = [(0,'@start')], {'@start':0}, {}
        edge_costs={}
        while queue:
            cost, current = heapq.heappop(queue)
            if cost > costs[current]:
                continue
            if current == '@end':
                chunks = []
                while current != '@start':
                    current, points = previous[current]; chunks.append(points)
                return [point for chunk in reversed(chunks) for point in chunk]
            for next_id, weight, points in sorted(graph[current],key=lambda item:item[0]):
                if weight is None:
                    key=tuple(sorted((current,next_id)))
                    if key not in edge_costs:
                        pieces=self.segments(points)
                        edge_costs[key]=None if pieces is None else sum(piece['length_m']/piece['speed_m_per_step'] for piece in pieces)
                    weight=edge_costs[key]
                    if weight is None:
                        continue
                if cost+weight < costs.get(next_id,math.inf)-1e-12:
                    costs[next_id],previous[next_id] = cost+weight,(current,points)
                    heapq.heappush(queue,(cost+weight,next_id))
        return None


def plan(state, unit, target, mode='move', waypoints=None):
    """生成含冻结参数、完整通行证据和分段成本的新移动测试路线。"""
    navigator = Navigator(state,unit,mode)
    start, end = spatial.meters(unit['position']), spatial.meters(target)
    require(navigator.clear(start), 'START_IMPASSABLE', '当前初始位置处于该兵种禁行区；部署阶段请先设置合法试走起点')
    require(navigator.clear(end), 'TARGET_IMPASSABLE', '目标位于该兵种禁行区、建筑占地或地图边界，请另选位置')
    points = [start]
    if mode == 'reverse_move':
        require(unit['unit_type'] != 'recon', 'MODE_UNAVAILABLE', '侦察无人机没有车辆倒车模式',422)
        require(not waypoints and math.dist(start,end) <= navigator.rules['reverse_limit_m']+.000001,
                'REVERSE_LIMIT', '倒车只允许 100 米内的单个直线目标')
        direction = math.degrees(math.atan2(end[0]-start[0],end[1]-start[1])) % 360
        heading = unit.get('heading_degrees',75 if unit['side']=='red' else 285)
        require(abs((direction-heading-180+180)%360-180) <= navigator.rules['reverse_angle_degrees'],
                'REVERSE_DIRECTION', '倒车目标须在车头后方 15 度范围内')
    for destination in [spatial.meters(value) for value in (waypoints or [])]+[end]:
        origin = points[-1]
        if unit['unit_type'] == 'recon' or mode == 'reverse_move':
            path = [origin,destination] if navigator.segment(origin,destination) is not None else None
        elif mode == 'fast_move':
            path = navigator.road_path(origin,destination)
        else:
            # 比较可验证直达与道路路线；缺少可用候选时执行完整局部搜索。
            direct = navigator.segment(origin,destination)
            road = navigator.road_path(origin,destination,required=False) if math.dist(origin,destination)>150 else None
            candidates = ([([origin,destination],direct)] if direct is not None else [])
            if road is not None:
                candidates.append((road,navigator.segments(road)))
            path = min(candidates,key=lambda candidate:(sum(piece['length_m']/piece['speed_m_per_step'] for piece in candidate[1]),
                sum(piece['length_m']*piece['energy_per_m'] for piece in candidate[1]),candidate[0]))[0] if candidates else navigator.grid_path(origin,destination)
        require(path is not None, 'ROUTE_UNAVAILABLE', '没有符合该兵种地表、坡度或宽度限制的完整路线')
        points.extend(path[1:])
    segments = navigator.segments(points)
    require(segments is not None, 'ROUTE_UNAVAILABLE', '路线接入或中间路段不可通行')
    energy = sum(piece['length_m']*piece['energy_per_m'] for piece in segments)
    available = unit.get('movement_test_energy',unit.get('energy',0)) if state['lifecycle']['phase']=='preparing' else unit.get('energy',0)
    require(energy <= available+.000001, 'MOVEMENT_ENERGY_INSUFFICIENT', '当前能源不足以完成整条路线，请缩短目标或重置测试能源')
    return dict(route_id=sha256(dict(unit_id=unit['unit_id'],start=start,target=target,mode=mode,points=points,
                                    map=navigator.meta['grid_sha256'],profile=navigator.rules)),
                unit_id=unit['unit_id'], points=[list(point) for point in points], segments=segments, segment_index=0,
                distance_m=round(sum(piece['length_m'] for piece in segments),6), traveled_m=0,
                estimated_steps=sum(piece['length_m']/piece['speed_m_per_step'] for piece in segments),
                estimated_energy=energy, movement_type='air' if unit['unit_type']=='recon' else 'ground',
                mode=mode,status='queued',test_only=True,deployment_test=state['lifecycle']['phase']=='preparing',
                movement_profile=deepcopy(navigator.rules),movement_map_sha256=navigator.meta['grid_sha256'],
                trace_sequence=0,last_trace=[])


def advance_unit(state, unit, active, deployment=False):
    """推进一份独立移动时间预算；部署试走只扣测试能源，不改变对局时钟。"""
    remaining = 1.0
    current = spatial.meters(unit['position'])
    trace = [list(current)]
    energy_field = 'movement_test_energy' if deployment else 'energy'
    energy = unit.get(energy_field,unit.get('energy',0))
    if unit['durability'] <= 0:
        active['status']='blocked'; unit['status']='单位已经失效'; return
    moved = 0
    active['status']='moving'
    while remaining > 1e-10 and active['segment_index'] < len(active['segments']):
        piece = active['segments'][active['segment_index']]
        distance = math.dist(current,piece['end'])
        if distance < 1e-8:
            active['segment_index'] += 1; continue
        amount = min(distance,remaining*piece['speed_m_per_step'],energy/piece['energy_per_m'] if piece['energy_per_m'] else math.inf)
        if amount <= 1e-9:
            active['status']='blocked'; unit['status']='移动能源耗尽'; break
        next_point = tuple(current[axis]+(piece['end'][axis]-current[axis])*amount/distance for axis in (0,1))
        radius = active['movement_profile']['unit_profiles'][unit['unit_type']]['radius_m']
        blocked = False
        if unit['unit_type'] != 'recon':
            for other in state['units']:
                if other['unit_id']==unit['unit_id'] or other['unit_type']=='recon' or is_garrisoned(state,other):
                    continue
                other_point=spatial.meters(other['position'])
                separation=radius+active['movement_profile']['unit_profiles'][other['unit_type']]['radius_m']+1
                previous_distance=math.dist(current,other_point)
                if (previous_distance >= separation and spatial.segment_distance(other_point,current,next_point)<separation
                    or previous_distance < separation and math.dist(next_point,other_point)<previous_distance-1e-6):
                    blocked=True; break
        if blocked:
            active['status']='waiting'; unit['status']='前方受阻，等待通行'; break
        if active['mode'] != 'reverse_move':
            unit['heading_degrees']=round(math.degrees(math.atan2(next_point[0]-current[0],next_point[1]-current[1]))%360,6)
        current=next_point; trace.append(list(current))
        moved += amount; energy -= amount*piece['energy_per_m']; remaining -= amount/piece['speed_m_per_step']
        if amount >= distance-1e-8:
            active['segment_index'] += 1
    unit['position']=spatial.business(current)
    unit[energy_field]=round(max(0,energy),6)
    active['traveled_m']=round(active['traveled_m']+moved,6)
    active['last_trace']=trace; active['trace_sequence']+=1
    active['waiting_steps']=0 if moved else active.get('waiting_steps',0)+1
    if active['segment_index'] == len(active['segments']):
        active['status']='arrived'; unit['status']='部署试走已到达' if deployment else '移动已到达'
    elif active['status'] not in ('waiting','blocked'):
        active['status']='moving'; unit['status']='部署试走中' if deployment else '沿规则路线移动'
    if deployment:
        unit['movement_test_steps']=unit.get('movement_test_steps',0)+1


def order(state, actor, payload):
    """统一处理部署与开局后的模式移动，权限和阶段均由服务端判断。"""
    require('spatial_profile' in state['manifest'],'SPATIAL_TEST_REQUIRED','此局未启用空间测试')
    require(state['lifecycle']['phase'] in ('preparing','paused','running'),'PHASE_CONFLICT','当前阶段不能安排移动')
    unit=next((value for value in state['units'] if value['unit_id']==payload['unit_id'] and (actor=='host' or value['side']==actor)),None)
    require(unit is not None,'UNIT_NOT_FOUND','单位不存在或无权操作',404)
    require(unit.get('durability') is not None and unit.get('energy') is not None,
            'PREPARATION_REQUIRED','请先完成兵营与编制资源配置')
    require(unit['durability']>0,'UNIT_UNAVAILABLE','单位已经失效')
    mode=payload['mode']
    if mode in ('stop_move','reset_test_energy'):
        if mode=='reset_test_energy':
            require(state['lifecycle']['phase']=='preparing','PHASE_CONFLICT','只有部署阶段可以重置试走能源')
            unit['movement_test_energy']=unit.get('energy',0); unit['movement_test_steps']=0
        state['routes']=[route for route in state['routes'] if route['unit_id']!=unit['unit_id']]
        unit['status']='部署试走已停止' if state['lifecycle']['phase']=='preparing' else '移动已停止'
        return unit
    target=payload.get('position')
    if mode=='deploy_start':
        require(state['lifecycle']['phase']=='preparing','PHASE_CONFLICT','只有部署阶段可以设置试走起点')
        require(target is not None,'TARGET_REQUIRED','请选择试走起点',422)
        navigator=Navigator(state,unit,'move')
        require(navigator.clear(spatial.meters(target)),'TARGET_IMPASSABLE','试走起点位于该兵种禁行区，请重新选择')
        state['routes']=[route for route in state['routes'] if route['unit_id']!=unit['unit_id']]
        unit['position']=spatial.business(spatial.meters(target)); unit['status']='部署试走起点已设置'
        unit['movement_departed']=True
        state['lifecycle']['ready'][unit['side']]=False; reset_plan(state,unit['side'])
        return unit
    if mode=='return_to_barracks':
        camp=next((value for value in state['camps'] if value['camp_id']==unit.get('camp_id') and value.get('position')),None)
        require(camp is not None,'CAMP_REQUIRED','没有有效的所属兵营接近点')
        center=spatial.meters(camp['position'])
        target=spatial.business((center[0],center[1]+camp.get('half_extent_m',30)+10))
        mode='move'
    require(target is not None,'TARGET_REQUIRED','请选择移动目标',422)
    candidate=deepcopy(unit)
    waypoints=list(payload.get('waypoints') or [])
    departing=is_garrisoned(state,unit)
    if departing:
        require(mode!='reverse_move','MODE_UNAVAILABLE','单位尚在兵营内，请先用普通或快速移动出营')
        camp=next(value for value in state['camps'] if value['camp_id']==unit['camp_id'])
        center=spatial.meters(camp['position'])
        candidate['position']=spatial.business(center)
        waypoints.insert(0,spatial.business((center[0],center[1]+camp.get('half_extent_m',30)+10)))
    active=plan(state,candidate,target,mode,waypoints)
    if departing:
        unit['position']=candidate['position']
    unit['movement_departed']=True
    state['routes']=[route for route in state['routes'] if route['unit_id']!=unit['unit_id']]+[active]
    unit['status']='部署试走已排队' if active['deployment_test'] else '规则路线已排队'
    if active['deployment_test']:
        state['lifecycle']['ready'][unit['side']]=False
        reset_plan(state,unit['side'])
        advance_unit(state,unit,active,deployment=True)
    return unit
