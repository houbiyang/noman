"""青岚项目适配规则：三阶段驻守评分，不冒充《断箭》正式占领/交战机制。"""
from copy import deepcopy
from decimal import Decimal

from backend.map_catalog import current_map
from backend.demo_domain import require

VERSION = 'qinglan-control-2026-11-06-v1'
MODE = 'qinglan_control'
POINT_IDS = ('TASK_A', 'TASK_B', 'TASK_C')
_points = {p['point_id']: p for p in current_map()['points']}
DEFINITION = dict(mode=MODE, rule_version=VERSION, stage_minutes=20, stages=3,
                  weights=[1, 2, 3], radius_m=400, eligible_types=['ground', 'support'],
                  tie_breaker='draw', adjudication='server_position_snapshot',
                  objectives=[dict(point_id=k, name=_points[k]['name'], x=_points[k]['position']['x'],
                                   z=_points[k]['position']['z']) for k in POINT_IDS])


def enabled(state):
    return state['manifest'].get('victory_rules') == DEFINITION


def initialize(state):
    require('spatial_profile' in state['manifest'], 'SPATIAL_MODE_REQUIRED', '青岚驻守计分必须启用空间测试', 422)
    state['manifest']['victory_rules'] = deepcopy(DEFINITION)
    state['lifecycle']['deadline_step'] = 60
    state['victory'] = dict(mode=MODE, ruleVersion=VERSION, preview=False, elapsedMinutes=0,
        stages=[dict(stage=i, redGoals=0, blueGoals=0, confirmed=False, evidence='', objectives=[]) for i in range(1,4)])
    # 新模式为初始编队明确初始化冻结的测试耐久/能源；不修补旧模式未知资源。
    for unit in state['units']:
        profile = state['manifest']['spatial_profile']['unit_profiles'][unit['unit_type']]
        for field in ('energy', 'supply', 'durability'):
            if unit.get(field) is None: unit[field] = profile[field]


def observe(state):
    """Decimal米制距离；只公开目标内人数/归属，不公开敌方坐标或单位编号。"""
    # 业务坐标仅存9位小数，先恢复到微米，消除换算误差而不扩大400米半径。
    observed = []
    for point in state['manifest']['victory_rules']['objectives']:
        counts = dict(red=0, blue=0)
        for unit in state['units']:
            if unit['unit_type'] not in DEFINITION['eligible_types'] or (unit.get('durability') or 0) <= 0:
                continue
            x = (Decimal(unit['position']['x']) * 100).quantize(Decimal('0.000001')) - Decimal(str(point['x']))
            z = (Decimal(unit['position']['y']) * 150).quantize(Decimal('0.000001')) - Decimal(str(point['z']))
            if x*x + z*z <= Decimal(DEFINITION['radius_m'])**2:
                counts[unit['side']] += 1
        owner = 'contested' if all(counts.values()) else 'red' if counts['red'] else 'blue' if counts['blue'] else 'neutral'
        observed.append(dict(point_id=point['point_id'], name=point['name'], owner=owner,
                             redUnits=counts['red'], blueUnits=counts['blue']))
    return observed


def valid_stage(stage):
    """结果读端同样核对逐点证据，不能仅信任客户端/档案汇总数。"""
    points = stage.get('objectives')
    if not isinstance(points, list) or len(points) != 3 or {p.get('point_id') for p in points if isinstance(p, dict)} != set(POINT_IDS):
        return False
    for p in points:
        if any(type(p.get(k)) is not int or p[k] < 0 for k in ('redUnits','blueUnits')):
            return False
        owner = 'contested' if p['redUnits'] and p['blueUnits'] else 'red' if p['redUnits'] else 'blue' if p['blueUnits'] else 'neutral'
        if p.get('owner') != owner:
            return False
    return stage['redGoals'] == sum(p['owner']=='red' for p in points) and stage['blueGoals'] == sum(p['owner']=='blue' for p in points)


def valid_evidence(value):
    """只有按时间顺序封存的阶段可计分；未结算阶段不能夹带目标或汇总。"""
    elapsed = value.get('elapsedMinutes')
    if type(elapsed) is not int or not 0 <= elapsed <= 60:
        return False
    gap = False
    for stage in value['stages']:
        if stage['confirmed']:
            if gap or elapsed < stage['stage'] * 20:
                return False
        else:
            gap = True
            if stage['redGoals'] or stage['blueGoals'] or stage.get('objectives'):
                return False
    return True


def after_advance(state):
    if not enabled(state): return
    clock = state['lifecycle']
    minute = clock['simulation_step']
    state['victory']['elapsedMinutes'] = minute
    if minute not in (20,40,60): return
    stage = state['victory']['stages'][minute//20-1]
    require(not stage['confirmed'], 'STAGE_ALREADY_SETTLED', '阶段驻守证据不可重复覆盖')
    points = observe(state)
    labels = dict(red='红方驻守',blue='蓝方驻守',contested='双方争夺，不计分',neutral='无人驻守，不计分')
    stage.update(confirmed=True, redGoals=sum(p['owner']=='red' for p in points),
                 blueGoals=sum(p['owner']=='blue' for p in points), objectives=points,
                 evidence='；'.join(p['name']+'：'+labels[p['owner']] for p in points),
                 adjudication=dict(source='server_position_snapshot', minute=minute))
    clock.update(phase='finished' if minute==60 else 'paused', controller_session_id=None)
