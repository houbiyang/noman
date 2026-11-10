"""四角色共享的只读规则工具与任务图校验，不向模型暴露隐藏全局。"""

from copy import deepcopy
import math

from pydantic import Field, ValidationError

from backend import coordination, movement, spatial
from backend.agent_contracts import CommanderPlan
from backend.contracts import Position, StrictModel
from backend.demo_domain import require
from backend.runtime import ServiceError


ROLES = ('recon', 'ground', 'support', 'commander')


def freeze_view(state, side):
    """只复制本方单位与观察，控制点和积分属于明确公开数据。"""
    result = {key: deepcopy(state[key]) for key in ('manifest', 'lifecycle', 'scores')}
    result['lifecycle'].pop('controller_session_id', None)
    result.update(side=side, units=[deepcopy(unit) for unit in state['units'] if unit['side'] == side],
                  camps=[deepcopy(camp) for camp in state['camps'] if camp['side'] == side],
                  routes=[deepcopy(route) for route in state['routes'] if any(unit['unit_id'] == route['unit_id']
                           and unit['side'] == side for unit in state['units'])],
                  observations=deepcopy(state['observations'].get(side, {})))
    if 'coordination' in state:
        shared = state['coordination']
        result['coordination'] = {key: deepcopy(shared[key]) for key in
                                 ('schema_version', 'test_only', 'rules', 'points', 'all_held', 'last_scored_step', 'limitations')}
        result['coordination']['plans'] = {side: deepcopy(shared['plans'][side])} if side in shared['plans'] else {}
    return result


class RouteQuery(StrictModel):
    """读取一个授权单位的静态路线，不泄漏未知动态障碍。"""

    unit_id: str = Field(min_length=1, max_length=40)
    position: Position | None = None
    target_point_id: str | None = Field(default=None, max_length=40)
    mode: str = Field(default='move', pattern=r'^(move|fast_move|reverse_move)$')


class ServiceQuery(StrictModel):
    """检查本方两个单位的抽象服务条件。"""

    unit_id: str = Field(min_length=1, max_length=40)
    target_unit_id: str = Field(min_length=1, max_length=40)


class EmptyQuery(StrictModel):
    """无参数的只读工具。"""


class RuleTools:
    """工具白名单绑定本方和角色；所有输入经过严格契约验证。"""

    def __init__(self, view, role):
        """只接收已经投影的副本，不持有数据库或原始全局。"""
        self.view, self.role = deepcopy(view), role
        definitions = [('read_state', '读取冻结的本方状态、已知观察及未知项', EmptyQuery),
                       ('read_rules', '读取本局测试规则、单位能力和限制', EmptyQuery),
                       ('preview_route', '检查本方授权单位路线、预计步数和能源，不移动单位', RouteQuery)]
        if role in ('commander', 'support'):
            definitions.append(('preview_service', '检查本方保障单位服务距离、库存和预计步数', ServiceQuery))
        self.models = {name: model for name, _, model in definitions}
        self.schemas = [dict(type='function', function=dict(name=name, description=description,
                         parameters=model.model_json_schema())) for name, description, model in definitions]

    def unit(self, identity):
        """后端检查对象归属和角色，不能仅依赖提示词限制。"""
        unit = next((unit for unit in self.view['units'] if unit['unit_id'] == identity), None)
        require(unit is not None, 'UNIT_NOT_FOUND', '单位不存在或无权读取', 404)
        require(self.role == 'commander' or unit['unit_type'] == self.role,
                'AGENT_ROLE_FORBIDDEN', '本角色不能安排其他兵种单位', 403)
        return unit

    def call(self, name, arguments):
        """将可公开的校验失败作为工具结果，不回传异常原文。"""
        try:
            require(name in self.models, 'TOOL_FORBIDDEN', '本角色未授权此工具', 403)
            checked = self.models[name].model_validate(arguments).model_dump(mode='json', exclude_none=True)
            if name == 'read_state':
                return dict(status='ok', data=self.role_view())
            if name == 'read_rules':
                return dict(status='ok', data=dict(coordination=self.view.get('coordination'),
                            movement=movement.profile(), test_only=True,
                            constraints=['每单位每步一个主动作', '首次和重大调整需要人工确认',
                                         '无人机不占点，保障车不占点', '未发现对象不能当成当前观察',
                                         '交战、维修和运输未启用']))
            if name == 'preview_route':
                return dict(status='ok', data=self.route(checked))
            return dict(status='ok', data=self.service(checked))
        except ValidationError:
            return dict(status='rejected', code='TOOL_INPUT_INVALID', message='工具参数不符合契约')
        except ServiceError as error:
            return dict(status='rejected', code=error.code, message=error.message)

    def role_view(self):
        """兵种角色只取得所管单位；本方协作摘要不含其他方私有数据。"""
        result = deepcopy(self.view)
        if self.role != 'commander':
            result['units'] = [unit for unit in result['units'] if unit['unit_type'] == self.role]
            allowed = {unit['unit_id'] for unit in result['units']}
            result['routes'] = [route for route in result['routes'] if route['unit_id'] in allowed]
        return result

    def route(self, query):
        """使用现有移动内核和已知静态地图计算候选，不推进世界。"""
        unit = deepcopy(self.unit(query['unit_id']))
        require('spatial_profile' in self.view['manifest'], 'SPATIAL_TEST_REQUIRED', '本局没有空间测试规则')
        target = query.get('position') or coordination.task_destination(self.view, query)
        planning_state = deepcopy(self.view)
        waypoints = []
        if movement.is_garrisoned(self.view, unit):
            camp = next(camp for camp in self.view['camps'] if camp['camp_id'] == unit['camp_id'])
            unit['position'] = deepcopy(camp['position'])
            center = spatial.meters(camp['position'])
            waypoints.append(spatial.business((center[0], center[1] + camp.get('half_extent_m', 30) + 10)))
        planning_state['lifecycle']['phase'] = 'paused'
        active = movement.plan(planning_state, unit, target, query.get('mode', 'move'), waypoints)
        return dict(status='executable', test_only=True, distance_m=active['distance_m'],
                    estimated_steps=math.ceil(active['estimated_steps']), estimated_energy=active['estimated_energy'],
                    points=active['points'], dynamic_conditions='仅校验公开静态条件；执行时重新检查动态占用')

    def service(self, query):
        """不读取其他方库存，服务能力来自冻结测试参数。"""
        source = self.unit(query['unit_id'])
        target = next((unit for unit in self.view['units'] if unit['unit_id'] == query['target_unit_id']), None)
        require(source['unit_type'] == 'support' and target is not None and source['unit_id'] != target['unit_id'],
                'SERVICE_TARGET_INVALID', '服务必须由保障单位提供给其他本方单位', 422)
        require('coordination' in self.view, 'COORDINATION_REQUIRED', '本局尚未启用协同测试规则')
        require(source.get('supply') is not None and target.get('energy') is not None, 'RESOURCE_UNKNOWN', '服务资源未知')
        rules = self.view['coordination']['rules']
        maximum = self.view['manifest']['spatial_profile']['unit_profiles'][target['unit_type']]['energy']
        shortage = max(0, maximum - target['energy'])
        return dict(test_only=True, distance_m=math.dist(spatial.meters(source['position']), spatial.meters(target['position'])),
                    service_radius_m=rules['service_radius_m'], supply_available=source['supply'], energy_needed=shortage,
                    estimated_steps=math.ceil(shortage / rules['service_per_step']), conversion='一单位补给转换一单位能源')


def validate_plan(view, payload):
    """校验无环依赖、单位权限、能力、互斥、资源和合法路线。"""
    plan = CommanderPlan.model_validate(payload).model_dump(mode='json', exclude_none=True)
    require('coordination' in view, 'COORDINATION_REQUIRED', '请先由主持人启用协同测试规则')
    units = {unit['unit_id']: unit for unit in view['units']}
    tasks = {task['task_id']: task for task in plan['tasks']}
    require(len(tasks) == len(plan['tasks']), 'TASK_ID_DUPLICATE', '任务标识重复', 422)
    ancestors, ordered = {}, []

    def visit(identity, visiting):
        """深度优先检查环路，生成稳定的拓扑顺序。"""
        require(identity in tasks, 'TASK_DEPENDENCY_MISSING', '依赖任务不存在', 422)
        require(identity not in visiting, 'TASK_DEPENDENCY_CYCLE', '任务依赖存在环路', 422)
        if identity in ancestors:
            return ancestors[identity]
        dependencies = set()
        for previous in tasks[identity]['depends_on']:
            dependencies.add(previous)
            dependencies.update(visit(previous, visiting | {identity}))
        ancestors[identity] = dependencies
        ordered.append(identity)
        return dependencies

    for identity in tasks:
        visit(identity, set())
    reserved = {}
    for identity in ordered:
        task = tasks[identity]
        unit = units.get(task['unit_id'])
        require(unit is not None and coordination.available(unit), 'TASK_UNIT_UNAVAILABLE', '任务单位不存在、未知或失效', 422)
        require(all(previous in ancestors[identity] for previous in reserved.get(unit['unit_id'], [])),
                'TASK_UNIT_CONFLICT', '同一单位的多个任务必须显式形成先后依赖', 422)
        reserved.setdefault(unit['unit_id'], []).append(identity)
        kind = task['action']
        if kind in ('capture', 'hold'):
            require(unit['unit_type'] == 'ground' and task.get('target_point_id'), 'CAPTURE_ROLE_INVALID', '只有陆兵能占领和守点', 422)
            require(coordination.point_for(view, task['target_point_id'])['navigation_status'] == 'test_surface_verified',
                    'POINT_NEEDS_VERIFICATION', '控制区导航仍待核验', 422)
            predicted_unit, predicted_state = deepcopy(unit), deepcopy(view)
            predicted_state['lifecycle']['phase'] = 'paused'
            for previous in ordered:
                if previous in ancestors[identity] and tasks[previous]['unit_id'] == unit['unit_id'] and tasks[previous]['action'] == 'move':
                    predicted_unit['position'] = coordination.task_destination(view, tasks[previous])
            require(coordination.in_point(predicted_state, predicted_unit, coordination.point_for(view, task['target_point_id'])),
                    'CAPTURE_POSITION_INVALID', '占点单位尚未在区内，且缺少抵达该区域的前置移动任务', 422)
        elif kind == 'observe':
            require(unit['unit_type'] == 'recon', 'OBSERVE_ROLE_INVALID', '只有侦察单位能执行观察主动作', 422)
        elif kind == 'resupply':
            target = units.get(task.get('target_unit_id'))
            require(unit['unit_type'] == 'support' and target is not None and target['unit_id'] != unit['unit_id'],
                    'SERVICE_TARGET_INVALID', '补给对象或保障兵种不合法', 422)
            require(unit.get('supply') is not None and unit['supply'] > 0, 'SUPPLY_UNAVAILABLE', '保障库存不足或未知', 422)
        if kind == 'move':
            preview_view = deepcopy(view)
            for previous in ordered:
                if previous in ancestors[identity] and tasks[previous]['unit_id'] == unit['unit_id'] and tasks[previous]['action'] == 'move':
                    candidate = next(item for item in preview_view['units'] if item['unit_id'] == unit['unit_id'])
                    candidate['position'] = coordination.task_destination(view, tasks[previous])
                    candidate['movement_departed'] = True
            result = RuleTools(preview_view, 'commander').route(dict(unit_id=unit['unit_id'], mode=task['movement_mode'],
                         **({'position': task['position']} if task.get('position') else {'target_point_id': task.get('target_point_id')})))
            task['preview'] = {key: value for key, value in result.items() if key != 'points'}
    plan['tasks'] = [tasks[identity] for identity in ordered]
    return plan
