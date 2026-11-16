"""主持人逐轮回放格式与纯读取播放时钟。无任何战斗、计分或命令写入调用。"""
from bisect import bisect_right
from copy import deepcopy
from fractions import Fraction
import re
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from backend.contracts import StrictModel, Side, UnitType
from backend.demo_domain import require
from backend.runtime import sha256

SCHEMA = 'qinglan-battle-round-replay-v1'
HEX = r'^[0-9a-f]{64}$'


class VisualPosition(StrictModel):
    x_mm: StrictInt = Field(ge=0, le=12000000)
    z_mm: StrictInt = Field(ge=0, le=12000000)
    height_mm: StrictInt


class VisualUnit(StrictModel):
    unit_id: str = Field(min_length=1, max_length=40)
    life_id: str = Field(min_length=1, max_length=80)
    side: Side
    unit_type: UnitType
    position: VisualPosition
    durability: str = Field(max_length=80)
    energy: str = Field(max_length=80)
    ammo: str = Field(max_length=80)
    max_durability: str = Field(max_length=80)
    max_energy: str = Field(max_length=80)
    max_ammo: str = Field(max_length=80)
    base_speed_m_per_minute: str = Field(max_length=80)
    speed_m_per_minute: str = Field(max_length=80)
    retired: StrictBool
    docked: StrictBool
    attack_target_id: str | None = Field(default=None, max_length=40)

    @field_validator('durability', 'energy', 'ammo', 'max_durability', 'max_energy', 'max_ammo',
                     'base_speed_m_per_minute', 'speed_m_per_minute')
    @classmethod
    def exact_resource(cls, value):
        if not re.fullmatch(r'\d+(?:/\d+|\.\d+)?', value):
            raise ValueError('资源使用非负十进制或分数串，不接受浮点数/NaN')
        try:
            number = Fraction(value)
        except (ValueError, ZeroDivisionError):
            raise ValueError('资源分母无效') from None
        if number < 0 or number > 10000:
            raise ValueError('资源越界')
        return value


class VisualPoint(StrictModel):
    point_id: Literal['P-01', 'P-02', 'P-03', 'P-04', 'P-05']
    owner: Literal['neutral', 'blue', 'red']
    advancing_side: Literal['none', 'blue', 'red']
    progress_steps: StrictInt = Field(ge=0, le=6)
    contested: StrictBool


class VisualWorld(StrictModel):
    elapsed_seconds: StrictInt = Field(ge=0, le=3600)
    units: list[VisualUnit] = Field(max_length=60)
    capture_points: list[VisualPoint] = Field(min_length=5, max_length=5)
    # 分数组合保持精确，不将120分归一化成100分；最终裁决仍由执行器给出。
    blue_score: str = Field(max_length=80)
    red_score: str = Field(max_length=80)

    @field_validator('blue_score', 'red_score')
    @classmethod
    def exact_score(cls, value):
        return VisualUnit.exact_resource(value)

    @model_validator(mode='after')
    def identities(self):
        if len({u.unit_id for u in self.units}) != len(self.units):
            raise ValueError('单位标识重复')
        if {p.point_id for p in self.capture_points} != {f'P-{i:02d}' for i in range(1, 6)}:
            raise ValueError('回放必须保留冻结的P-01至P-05')
        return self


class VisualEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=100)
    kind: Literal['move', 'shot', 'hit', 'service', 'takeoff', 'land', 'retired', 'capture', 'barracks_service']
    at_seconds: StrictInt = Field(ge=5, le=3600)
    camp_id: str | None = Field(default=None, min_length=1, max_length=40)
    delivery_category: Literal['energy', 'ammo', 'durability', 'stock_energy', 'stock_ammo', 'stock_repair'] | None = None
    source_id: str | None = Field(default=None, max_length=40)
    target_id: str | None = Field(default=None, max_length=40)
    source_life_id: str | None = Field(default=None, max_length=80)
    target_life_id: str | None = Field(default=None, max_length=80)
    source_position: VisualPosition | None = None
    target_position: VisualPosition | None = None
    trace: list[VisualPosition] = Field(default_factory=list, max_length=128)
    resource: Literal['energy', 'ammo', 'repair'] | None = None
    amount: str | None = Field(default=None, max_length=80)

    @field_validator('amount')
    @classmethod
    def exact_amount(cls, value):
        return VisualUnit.exact_resource(value) if value is not None else value

    @model_validator(mode='after')
    def event_shape(self):
        if self.kind == 'barracks_service':
            mapping = dict(energy='energy', ammo='ammo', durability='repair',
                           stock_energy='energy', stock_ammo='ammo', stock_repair='repair')
            if (not all((self.camp_id, self.target_id, self.target_life_id, self.source_position, self.target_position))
                    or self.source_id is not None or self.source_life_id is not None
                    or self.delivery_category not in mapping or self.resource != mapping[self.delivery_category]
                    or self.amount is None or Fraction(self.amount) <= 0):
                raise ValueError('兵营交付必须绑定明确兵营、接收单位生命、端点、实际类别与正数量，不能伪装单位来源')
        elif self.camp_id is not None or self.delivery_category is not None:
            raise ValueError('只有兵营交付事件可以携带兵营或交付类别')
        if self.kind in ('shot', 'hit', 'service') and not all((self.source_id, self.target_id,
                self.source_life_id, self.target_life_id, self.source_position, self.target_position)):
            raise ValueError('交互表现必须冻结供受双方生命实例及端点，禁止实时重新选敌')
        if self.kind in ('retired', 'takeoff', 'land') and (not self.source_id or not self.source_life_id):
            raise ValueError('退场与起降必须绑定明确的单位生命实例')
        if self.kind in ('takeoff', 'land') and (self.source_position is None or self.target_position is None):
            raise ValueError('起降动画必须绑定已裁决前后位置')
        if self.kind == 'move' and (not self.source_id or not self.source_life_id or len(self.trace) < 2):
            raise ValueError('移动动画必须来自已裁决毫米轨迹，不能用前端寻路代替真实路线')
        if self.kind != 'move' and self.trace:
            raise ValueError('只有移动事件携带轨迹')
        if self.kind == 'service' and (self.resource is None or self.amount is None):
            raise ValueError('补给表现必须来自真实资源交付事件')
        return self


class ReplayFrame(StrictModel):
    at_seconds: StrictInt = Field(ge=5, le=3600)
    world: VisualWorld
    events: list[VisualEvent] = Field(default_factory=list, max_length=240)


class ReplayRound(StrictModel):
    schema_version: Literal['qinglan-battle-round-replay-v1']
    round_number: StrictInt = Field(ge=1, le=60)
    start_seconds: StrictInt = Field(ge=0, le=3595)
    end_seconds: StrictInt = Field(ge=5, le=3600)
    finished: StrictBool
    rule_sha256: str = Field(pattern=HEX)
    scenario_sha256: str = Field(pattern=HEX)
    previous_round_sha256: str | None = Field(default=None, pattern=HEX)
    before_world_sha256: str = Field(pattern=HEX)
    after_world_sha256: str = Field(pattern=HEX)
    initial_world: VisualWorld
    frames: list[ReplayFrame] = Field(min_length=1, max_length=12)
    payload_sha256: str = Field(pattern=HEX)


def validate_record(value):
    """不规范化后重新签名：原始持久化内容任何变化都会失败。"""
    record = ReplayRound.model_validate(value)
    body = {key: item for key, item in value.items() if key != 'payload_sha256'}
    require(sha256(body) == record.payload_sha256, 'REPLAY_HASH_MISMATCH', '回放摘要校验失败')
    require(record.start_seconds == (record.round_number - 1) * 60,
            'REPLAY_TIME_INVALID', '回放轮次与起始时间不一致')
    require(record.initial_world.elapsed_seconds == record.start_seconds
            and record.end_seconds == record.frames[-1].at_seconds,
            'REPLAY_TIME_INVALID', '回放起止世界时间不一致')
    duration = record.end_seconds - record.start_seconds
    require(0 < duration <= 60 and duration % 5 == 0 and (duration == 60 or record.finished),
            'REPLAY_TIME_INVALID', '只有提前终局能少于60秒')
    require(record.end_seconds < 3600 or record.finished, 'REPLAY_TIME_INVALID', '3600秒必须终局')
    require(record.previous_round_sha256 is None if record.round_number == 1 else record.previous_round_sha256 is not None,
            'REPLAY_CHAIN_INVALID', '回放链前驱缺失或首轮包含前驱')
    require(sha256(value['initial_world']) == record.before_world_sha256
            and sha256(value['frames'][-1]['world']) == record.after_world_sha256,
            'REPLAY_HASH_MISMATCH', '回放世界摘要校验失败')
    identities = {u.unit_id: (u.life_id, u.side, u.unit_type) for u in record.initial_world.units}
    profiles = {u.unit_id: (u.max_durability, u.max_energy, u.max_ammo, u.base_speed_m_per_minute)
                for u in record.initial_world.units}
    def capacities(world):
        for unit in world.units:
            require(Fraction(unit.durability) <= Fraction(unit.max_durability)
                    and Fraction(unit.energy) <= Fraction(unit.max_energy)
                    and Fraction(unit.ammo) <= Fraction(unit.max_ammo), 'REPLAY_RESOURCE_INVALID', '回放资源不能超过冻结容量')
    capacities(record.initial_world)
    previous_world = record.initial_world
    events = set()
    expected = record.start_seconds
    for frame in record.frames:
        expected += 5
        require(frame.at_seconds == expected and frame.world.elapsed_seconds == expected,
                'REPLAY_TIME_INVALID', '内部帧必须连续，不能漏帧、重复或倒序')
        require({u.unit_id: (u.life_id, u.side, u.unit_type) for u in frame.world.units} == identities,
                'REPLAY_IDENTITY_CHANGED', '战中不能增援、复活、换阵营或更换生命实例')
        require({u.unit_id: (u.max_durability, u.max_energy, u.max_ammo, u.base_speed_m_per_minute)
                 for u in frame.world.units} == profiles, 'REPLAY_PROFILE_CHANGED', '回放不能更换单位冻结参数')
        capacities(frame.world)
        moved = set()
        for event in frame.events:
            require(event.at_seconds == expected and event.event_id not in events,
                    'REPLAY_EVENT_INVALID', '事件必须唯一且属于本帧')
            events.add(event.event_id)
            if event.kind in ('move', 'takeoff', 'land'):
                before = next((u for u in previous_world.units if u.unit_id == event.source_id), None)
                after = next((u for u in frame.world.units if u.unit_id == event.source_id), None)
                require(before is not None and after is not None and event.source_id not in moved
                        and (event.trace[0] if event.kind == 'move' else event.source_position) == before.position
                        and (event.trace[-1] if event.kind == 'move' else event.target_position) == after.position,
                        'REPLAY_TRACE_INVALID', '同单位每帧只能一条移动轨迹，且必须衔接前后权威位置')
                moved.add(event.source_id)
            if event.kind == 'barracks_service':
                target = next((u for u in frame.world.units if u.unit_id == event.target_id), None)
                require(target is not None and not target.retired and target.position == event.target_position,
                        'REPLAY_EVENT_INVALID', '兵营交付接收端必须是本帧存活单位的权威位置')
            for unit_id, life_id in ((event.source_id, event.source_life_id), (event.target_id, event.target_life_id)):
                if unit_id is not None:
                    if event.kind == 'capture' and unit_id in {p.point_id for p in frame.world.capture_points}:
                        continue
                    require(unit_id in identities and identities[unit_id][0] == life_id,
                            'REPLAY_EVENT_INVALID', '事件不能引用不存在的单位生命实例')
        require(all(u.position == next(v for v in frame.world.units if v.unit_id == u.unit_id).position
                    or u.unit_id in moved for u in previous_world.units),
                'REPLAY_TRACE_INVALID', '任何位置变化都必须有衔接首尾的权威移动或起降事件')
        require(all(not u.retired or next(v for v in frame.world.units if v.unit_id == u.unit_id).retired
                    for u in previous_world.units), 'REPLAY_IDENTITY_CHANGED', '已退场生命实例不能复活')
        previous_world = frame.world
    return record


def seal_record(*, round_number, rule_sha256, scenario_sha256, previous_round_sha256,
                initial_world, frames, finished=False):
    """执行器在同一事务中生成记录，数据库仍只允许INSERT，不能覆盖旧轮。"""
    value = deepcopy(dict(schema_version=SCHEMA, round_number=round_number,
        start_seconds=initial_world['elapsed_seconds'], end_seconds=frames[-1]['at_seconds'], finished=finished,
        rule_sha256=rule_sha256, scenario_sha256=scenario_sha256, previous_round_sha256=previous_round_sha256,
        before_world_sha256=sha256(initial_world), after_world_sha256=sha256(frames[-1]['world']),
        initial_world=initial_world, frames=frames))
    value['payload_sha256'] = sha256(value)
    validate_record(value)
    return value


class ReplayCursor:
    """确定性纯播放游标，支持单轮、串联、暂停、跳转；从不向后端发命令。"""
    def __init__(self, records):
        require(bool(records), 'REPLAY_EMPTY', '还没有已完成轮次')
        self.records = deepcopy(records)
        for item in self.records:
            validate_record(item)
        previous = None
        event_ids = set()
        for record in self.records:
            for frame in record['frames']:
                for event in frame['events']:
                    require(event['event_id'] not in event_ids, 'REPLAY_EVENT_INVALID', '跨轮回放事件标识重复')
                    event_ids.add(event['event_id'])
            if previous:
                require(not previous['finished'] and record['round_number'] == previous['round_number'] + 1
                        and record['start_seconds'] == previous['end_seconds']
                        and record['previous_round_sha256'] == previous['payload_sha256']
                        and record['rule_sha256'] == previous['rule_sha256']
                        and record['scenario_sha256'] == previous['scenario_sha256']
                        and record['initial_world'] == previous['frames'][-1]['world'],
                        'REPLAY_CHAIN_INVALID', '轮次、世界或冻结版本不连续，拒绝拼接')
            previous = record
        self.first_seconds = self.records[0]['start_seconds']
        self.last_seconds = self.records[-1]['end_seconds']
        self.frames = [dict(at_seconds=self.first_seconds, world=self.records[0]['initial_world'], events=[])]
        for record in self.records:
            self.frames.extend(record['frames'])
        self.times = [f['at_seconds'] for f in self.frames]
        self.seconds = self.first_seconds
        self.playing = False

    def seek(self, seconds):
        require(type(seconds) in (int, float) and self.first_seconds <= seconds <= self.last_seconds,
                'REPLAY_SEEK_INVALID', '只能跳转到已完成的回放区间')
        self.seconds = seconds
        if seconds == self.last_seconds:
            self.playing = False
        frame = self.frames[bisect_right(self.times, seconds) - 1]
        # 只显示当前时刻已发生的状态，不先Apply整轮最终血量/分数。
        return deepcopy(frame['world'])

    def play(self):
        if self.seconds < self.last_seconds:
            self.playing = True

    def pause(self):
        self.playing = False

    def advance(self, seconds):
        require(type(seconds) in (int, float) and seconds >= 0 and seconds < float('inf'),
                'REPLAY_DELTA_INVALID', '播放增量必须是有限非负秒数')
        start = self.seconds
        end = min(self.last_seconds, start + seconds) if self.playing else start
        events = [deepcopy(e) for frame in self.frames if start < frame['at_seconds'] <= end for e in frame['events']]
        return dict(world=self.seek(end), events=events, seconds=self.seconds, playing=self.playing)
