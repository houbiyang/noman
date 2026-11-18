"""总册独立手动新局配置：不迁移旧局、不替换坐标、不从客户端接收资源或验收证明。"""
from collections import Counter
from copy import deepcopy
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, StringConstraints, model_validator

from backend import battle_capacity, battle_kernel, battle_scenario, manual_battle, rulebook
from backend.battle_contracts import BattleArmyQuantities
from backend.battle_replay import VisualPosition
from backend.contracts import StrictModel, UnitType
from backend.demo_domain import barracks_id, new_unit, validate_state
from backend.runtime import sha256

Digest = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
Surface = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Deployment(StrictModel):
    unit_type: UnitType
    ordinal: Annotated[int, Field(strict=True, ge=1, le=10)]
    position: VisualPosition
    surface_id: Surface


class CampDeployment(StrictModel):
    unit_type: UnitType
    position: VisualPosition
    surface_id: Surface
    service_anchor: VisualPosition
    service_surface_id: Surface


class SideSetup(StrictModel):
    quantities: BattleArmyQuantities
    units: list[Deployment] = Field(min_length=1, max_length=30)
    camps: list[CampDeployment] = Field(min_length=3, max_length=3)

    @model_validator(mode='after')
    def complete_roster(self):
        expected = {(kind, i) for kind, n in self.quantities.model_dump().items() for i in range(1, n + 1)}
        actual = [(u.unit_type, u.ordinal) for u in self.units]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError('必须逐一提供完整名册的预部署位置，不能重复、漏填或自动缩减数量')
        if Counter(c.unit_type for c in self.camps) != Counter(('recon', 'ground', 'support')):
            raise ValueError('每方必须明确配置三座不同兵种兵营，不因该兵种数量为0而省略')
        return self


class CreateManualBattle(StrictModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    mode: Literal['manual']
    rule_version_id: UUID
    scenario_version_id: UUID
    capacity_version_id: UUID
    rule_sha256: Digest
    scenario_sha256: Digest
    capacity_sha256: Digest
    blue: SideSetup
    red: SideSetup


def sources(*, hover_energy=False):
    scenario = battle_scenario.hover_energy_definition() if hover_energy else battle_scenario.definition()
    rules, capacity = rulebook.definition(), battle_capacity.definition()
    return rules, scenario, capacity


def versions(rules, scenario, capacity):
    return dict(rule_version_id=rulebook.identity(rules), scenario_version_id=battle_scenario.identity(scenario),
        capacity_version_id=battle_capacity.identity(capacity), rule_sha256=sha256(rules),
        scenario_sha256=sha256(scenario), capacity_sha256=sha256(capacity))


def warnings(request):
    return [dict(side=side, code='NO_CAPTURE_CAPABILITY', message='本方仅有无人机，缺乏占点能力；不会因此自动判负。')
            for side in ('blue', 'red') if getattr(request, side).quantities.ground + getattr(request, side).quantities.support == 0]


def build(request, rules, scenario, capacity, geometry):
    """纯新局工厂；几何合法性还须由执行器prepare_new_game严格验收，调用方事务保存。"""
    from backend.demo_domain import require
    binding = versions(rules, scenario, capacity)
    require(all(str(getattr(request, key)) == value for key, value in binding.items()),
            'BATTLE_VERSION_CHANGED', '配置版本或摘要已变化，请重新读取配置；不会自动升级', 409)
    require(scenario['rule_version_id'] == binding['rule_version_id']
            and scenario['rule_payload_sha256'] == binding['rule_sha256']
            and scenario['capacity_payload_sha256'] == binding['capacity_sha256']
            and capacity['rulebook']['rule_version_id'] == binding['rule_version_id']
            and capacity['rulebook']['payload_sha256'] == binding['rule_sha256'],
            'BATTLE_VERSION_CHANGED', '场景、总册和容量补充的来源绑定不一致', 409)
    require(scenario['round_seconds'] == 60 and scenario['maximum_rounds'] == 60,
            'BATTLE_VERSION_CHANGED', '本地手动演示须为60秒一轮、最多60轮', 409)
    require(battle_scenario.service_policy_valid(scenario), 'BATTLE_SERVICE_POLICY_INVALID',
            '空中补能例外须绑定受支持的新场景版本', 409)
    setup = request.model_dump(mode='json')
    # 列表顺序属于创建请求；不会自动重排输入来掩盖同键异参。
    state = dict(manifest=dict(schema_version='unity-demo-world-v1', battle_mode='manual',
        scenario_version_id=binding['scenario_version_id'], rule_version_id=binding['rule_version_id'],
        battle_rule_sha256=binding['rule_sha256'], battle_scenario_sha256=binding['scenario_sha256'],
        battle_capacity_version_id=binding['capacity_version_id'], battle_capacity_sha256=binding['capacity_sha256'],
        capture_layout_sha256=scenario['capture_layout_sha256'], map_source_sha256=scenario['map']['source_sha256'],
        coordinate_system_id='unity-demo-120x80-v1', battle_setup=deepcopy(setup), battle_setup_sha256=sha256(setup),
        control_configuration=dict(mode='local_manual', real_ai=False, models={}, tools=[], budget=0)),
        lifecycle=dict(phase='preparing', simulation_step=0, deadline_step=60, step_minutes=1,
            ready=dict(blue=False, red=False), pause_reasons=[], controller_session_id=None),
        units=[], camps=[], tasks=[], routes=[], formations=[],
        observations={side: dict(contacts=[], last_seen_records=[]) for side in ('blue', 'red')},
        scores=dict(source='rulebook_battle_ledger', authoritative=False),
        logistics=dict(source='rulebook', formal_rules_available=True),
        conditions=dict(instances=[], map_overrides=[]),
        decisions=dict(plans={side: dict(side=side, resource_limit=0, status='not_required', source='local_manual')
                              for side in ('blue', 'red')}),
        battle_points=[dict(point_id=p['point_id'], owner='neutral', advancing_side='none',
            progress_steps=0, contested=False) for p in scenario['capture_points']])
    for key in ('release_scope', 'competitive_ready', 'limitations'):
        if key in scenario:
            state['manifest'][key] = deepcopy(scenario[key])
    if 'field_energy_service_policy' in scenario:
        state['manifest']['field_energy_service_policy'] = scenario['field_energy_service_policy']
    for side in ('blue', 'red'):
        config = getattr(request, side)
        for item in config.units:
            location = item.position.model_dump()
            unit = new_unit(side, item.unit_type, item.ordinal, geometry.display_position(deepcopy(location)))
            # 资源只由总册内核赋值，不能沿用旧演示的None或客户端提供的数值。
            profile = battle_kernel.profiles()[item.unit_type]
            for key in ('durability', 'energy', 'ammo'):
                unit[key] = str(profile[key])
            unit.update(life_id=str(uuid4()), position_mm=location, surface_id=item.surface_id,
                task=None, movement=None, speed_m_per_minute='0', navigation_status='rulebook_frozen')
            state['units'].append(unit)
        for item in config.camps:
            location = item.position.model_dump()
            state['camps'].append(dict(camp_id=barracks_id(side, item.unit_type), side=side, unit_type=item.unit_type,
                position=geometry.display_position(deepcopy(location)), position_mm=location, surface_id=item.surface_id,
                service_anchor_mm=item.service_anchor.model_dump(), service_surface_id=item.service_surface_id,
                status='frozen', deployment_validated=False))
    battle_capacity.validate_roster(state['units'])
    state = battle_kernel.initialize(state)
    manual_battle.initialize(state)
    validate_state(state)
    return state
