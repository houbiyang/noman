"""总册战斗模式的独立输入契约；不会放宽旧演示对局的数量边界。"""
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend import battle_capacity
from backend.contracts import StrictModel, Side, Position
from backend.demo_domain import position as validate_position


class BattleArmyQuantities(StrictModel):
    """容量来自软件清单；总册允许单类0，不规定固定总数。"""
    recon: StrictInt = Field(ge=battle_capacity.MINIMUM_PER_TYPE, le=battle_capacity.MAXIMUM_PER_TYPE)
    ground: StrictInt = Field(ge=battle_capacity.MINIMUM_PER_TYPE, le=battle_capacity.MAXIMUM_PER_TYPE)
    support: StrictInt = Field(ge=battle_capacity.MINIMUM_PER_TYPE, le=battle_capacity.MAXIMUM_PER_TYPE)

    @model_validator(mode='after')
    def check_side_total(self):
        battle_capacity.validate_quantities(self.model_dump())
        return self


class ConfigureBattleArmy(StrictModel):
    """供总册执行器接入，不能拿此载荷绕过旧对局的版本验证。"""
    side: Side
    quantities: BattleArmyQuantities


class BattlePreflightRequest(StrictModel):
    """只读开战检查；模型联网需要单独显式同意，不隐式启用模式。"""
    mode: Literal['agent', 'manual']
    probe_ai: StrictBool = False
    acknowledge_cost: StrictBool = False

    @model_validator(mode='after')
    def network_requires_consent(self):
        if self.probe_ai and not self.acknowledge_cost:
            raise ValueError('模型连通测试可能计费，必须显式同意')
        return self


class BattleMovementIntent(StrictModel):
    action: Literal['move', 'stop']
    position: Position | None = None
    waypoints: list[Position] = Field(default_factory=list, max_length=7)

    @model_validator(mode='after')
    def movement_shape(self):
        if self.action == 'move' and self.position is None:
            raise ValueError('移动必须指定终点')
        if self.action == 'stop' and (self.position is not None or self.waypoints):
            raise ValueError('停止移动不能附带路线')
        for point in ([self.position] if self.position else []) + self.waypoints:
            validate_position(point.model_dump())
        return self


class BattleTaskIntent(StrictModel):
    action: Literal['attack', 'attack_area', 'recon', 'resupply', 'repair', 'barracks', 'takeoff', 'land', 'stop']
    target_id: str | None = Field(default=None, min_length=1, max_length=40)
    target_life_id: str | None = Field(default=None, min_length=1, max_length=80)
    position: Position | None = None
    radius_m: StrictInt | None = Field(default=None, ge=1, le=12000)
    resource: Literal['energy', 'ammo'] | None = None
    quantity: str | None = Field(default=None, min_length=1, max_length=80)
    target_balance: str | None = Field(default=None, min_length=1, max_length=80)
    camp_id: str | None = Field(default=None, min_length=1, max_length=40)
    targets: dict[str, str] | None = None

    @model_validator(mode='after')
    def task_shape(self):
        target = self.target_id is not None and self.target_life_id is not None
        if (self.target_id is None) != (self.target_life_id is None):
            raise ValueError('目标和生命实例必须同时提供')
        if self.action in ('attack', 'resupply', 'repair'):
            if not target or self.position is not None or self.radius_m is not None:
                raise ValueError('此任务只能指定单位目标')
        elif self.action == 'attack_area':
            if target or self.position is None or self.radius_m is None:
                raise ValueError('范围授权需要圆心和半径，不能指定单位')
        elif self.action == 'recon':
            if target == (self.position is not None) or self.radius_m is not None:
                raise ValueError('侦察须指定一个目标或位置')
        elif target or self.position is not None or self.radius_m is not None:
            raise ValueError('此任务不能附带目标或位置')
        if (self.action == 'resupply') != (self.resource is not None):
            raise ValueError('仅补给任务需要能源或弹药类型')
        from backend.battle_kernel import number as exact_number
        from backend.runtime import ServiceError
        def number(value):
            try:
                return exact_number(value)
            except ServiceError as error:
                raise ValueError(error.message) from None
        service = self.action in ('resupply', 'repair')
        if service:
            if (self.quantity is None) == (self.target_balance is None):
                raise ValueError('野外服务须指定累计数量或目标余额之一')
            amount = number(self.quantity if self.quantity is not None else self.target_balance)
            if amount <= 0 or (self.resource == 'ammo' and amount.denominator != 1):
                raise ValueError('服务数量须为正，弹药须为整数')
        elif self.quantity is not None or self.target_balance is not None:
            raise ValueError('仅野外服务可以附带数量或目标余额')
        if self.action == 'barracks':
            if self.camp_id is None:
                raise ValueError('兵营请求须明确指定本方兵营')
            if self.targets is not None:
                if not self.targets or not set(self.targets) <= {
                        'energy', 'ammo', 'durability', 'stock_energy', 'stock_ammo', 'stock_repair'}:
                    raise ValueError('兵营服务类别无效')
                for key, value in self.targets.items():
                    amount = number(value)
                    if 'ammo' in key and amount.denominator != 1:
                        raise ValueError('弹药目标余额须为整数')
        elif self.camp_id is not None or self.targets is not None:
            raise ValueError('只有兵营请求可以指定兵营或装载类别')
        if self.position:
            validate_position(self.position.model_dump())
        return self


class BattleUnitIntent(StrictModel):
    unit_id: str = Field(min_length=1, max_length=40)
    life_id: str = Field(min_length=1, max_length=80)
    movement: BattleMovementIntent | None = None
    task: BattleTaskIntent | None = None

    @model_validator(mode='after')
    def not_empty(self):
        if self.movement is None and self.task is None:
            raise ValueError('至少提供移动或任务通道；两个通道可并行，例如移动开火')
        if self.task and self.task.action in ('takeoff', 'land') and self.movement and self.movement.action == 'move':
            raise ValueError('起降不能同时安排移动')
        return self


class BattleDraftRequest(StrictModel):
    round_number: StrictInt = Field(ge=1, le=60)
    intents: list[BattleUnitIntent] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def unique_units(self):
        if len({item.unit_id for item in self.intents}) != len(self.intents):
            raise ValueError('同一批草稿不能重复登记同一单位')
        return self


class BattleRoundRequest(StrictModel):
    round_number: StrictInt = Field(ge=1, le=60)
