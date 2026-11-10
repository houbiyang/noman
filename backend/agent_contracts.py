"""多智能体的配置、候选和协同任务契约，拒绝模型越权字段。"""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from backend.contracts import Position, StateContext, StrictModel


AgentRole = Literal["commander", "recon", "ground", "support"]


class ModelConfiguration(StrictModel):
    """每方独立的兼容消息接口配置；只保存密钥环境变量名。"""

    enabled: bool = False
    base_url: str = Field(default="", max_length=300)
    model: str = Field(default="", max_length=120)
    credential_ref: str = Field(default="", pattern=r"^$|^[A-Z][A-Z0-9_]{0,79}$")
    temperature: float | None = Field(default=0.2, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=2048, ge=256, le=8192, strict=True)
    timeout_seconds: int = Field(default=45, ge=1, le=120, strict=True)
    output_token_parameter: Literal['max_tokens', 'max_completion_tokens'] = 'max_tokens'
    reasoning_effort: Literal['low', 'high', 'max'] | None = None

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value):
        """允许 HTTPS 或本机 HTTP，禁止在地址中携带认证信息。"""
        from urllib.parse import urlsplit

        if not value:
            return value
        parsed = urlsplit(value)
        valid = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))
        if not valid or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("服务地址须为 HTTPS 或本机 HTTP，不能包含凭据、查询或片段")
        return value.rstrip("/")


class AgentPolicy(StrictModel):
    """策略偏好仅影响合法候选的选择，不修改单位和规则属性。"""

    risk_preference: Literal["conservative", "balanced", "active"] = "balanced"
    reserve_energy: float = Field(default=20, ge=0, le=100, allow_inf_nan=False)
    information_max_age_steps: int = Field(default=2, ge=0, le=60, strict=True)
    planning_horizon_steps: int = Field(default=10, ge=1, le=60, strict=True)
    max_tool_calls: int = Field(default=6, ge=0, le=12, strict=True)
    max_model_calls: int = Field(default=3, ge=1, le=5, strict=True)


class AgentConfiguration(StrictModel):
    """红蓝分别保存的模型和四角色参数，不存实际密钥。"""

    model: ModelConfiguration = Field(default_factory=ModelConfiguration)
    prompt_version: str = Field(default="coordination-v1", min_length=1, max_length=80)
    max_total_tokens: int = Field(default=24000, ge=1024, le=200000, strict=True)
    commander: AgentPolicy = Field(default_factory=AgentPolicy)
    recon: AgentPolicy = Field(default_factory=AgentPolicy)
    ground: AgentPolicy = Field(default_factory=AgentPolicy)
    support: AgentPolicy = Field(default_factory=AgentPolicy)


class AgentRunRequest(StrictModel):
    """冻结当前本方输入后，启动一次有界四角色协同分析。"""

    context: StateContext
    reason: str = Field(default="制定本方占点协同方案", min_length=1, max_length=300)


class CoordinationTask(StrictModel):
    """一个单位的一项主动作；任务依赖由程序检查环路和冲突。"""

    task_id: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    unit_id: str = Field(min_length=1, max_length=40)
    action: Literal["move", "observe", "capture", "hold", "resupply", "wait"]
    target_point_id: str | None = Field(default=None, max_length=40)
    target_unit_id: str | None = Field(default=None, max_length=40)
    position: Position | None = None
    movement_mode: Literal["move", "fast_move", "reverse_move"] = "move"
    depends_on: list[str] = Field(default_factory=list, max_length=30)
    timeout_steps: int = Field(default=15, ge=1, le=60, strict=True)
    duration_steps: int = Field(default=1, ge=1, le=60, strict=True)
    reason: str = Field(default="", max_length=300)

    @model_validator(mode='after')
    def validate_action_fields(self):
        """动作参数必须具备可执行语义，不接受缺少目标的移动和服务。"""
        if self.action == 'move' and (bool(self.position) == bool(self.target_point_id)):
            raise ValueError('移动须且只能指定坐标或控制点之一')
        if self.action in ('capture', 'hold') and not self.target_point_id:
            raise ValueError('占领或守点必须指定控制点')
        if self.action == 'resupply' and not self.target_unit_id:
            raise ValueError('补给必须指定本方服务对象')
        if self.action != 'move' and self.position:
            raise ValueError('只有移动动作接受目标坐标')
        if self.action in ('hold', 'wait') and self.duration_steps > self.timeout_steps:
            raise ValueError('任务持续步数不能超过其超时步数')
        return self


class AgentReport(StrictModel):
    """兵种模型输出的是建议；事实仍取自冻结输入和工具结果。"""

    summary: str = Field(min_length=1, max_length=1500)
    suggested_tasks: list[CoordinationTask] = Field(default_factory=list, max_length=30)
    support_requests: list[str] = Field(default_factory=list, max_length=15)
    uncertainties: list[str] = Field(default_factory=list, max_length=15)


class CommanderPlan(StrictModel):
    """总指挥生成的待核验任务图，不允许输出分数或世界状态。"""

    summary: str = Field(min_length=1, max_length=1500)
    tasks: list[CoordinationTask] = Field(min_length=1, max_length=60)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)


class ReviewAgentPlan(StrictModel):
    """本方操作员明确批准或驳回一次分析，不自动执行草稿。"""

    run_id: str = Field(min_length=36, max_length=36)
    decision: Literal["approve", "reject"]


class CoordinationRules(StrictModel):
    """显式启用的协同测试参数，与既有演示局隔离。"""

    capture_steps: int = Field(default=2, ge=1, le=20, strict=True)
    neutralize_steps: int = Field(default=2, ge=1, le=20, strict=True)
    capture_radius_m: float = Field(default=150, gt=0, le=500, allow_inf_nan=False)
    victory_points: int = Field(default=60, ge=1, le=1000, strict=True)
    all_points_steps: int = Field(default=3, ge=1, le=20, strict=True)
    observation_radius_m: float = Field(default=800, gt=0, le=3000, allow_inf_nan=False)
    observer_height_m: float = Field(default=80, gt=0, le=500, allow_inf_nan=False)
    service_radius_m: float = Field(default=75, gt=0, le=300, allow_inf_nan=False)
    service_per_step: float = Field(default=20, gt=0, le=100, allow_inf_nan=False)


class EnableCoordination(StrictModel):
    """主持人仅在准备阶段显式启用测试规则，不修改原场景点位。"""

    rules: CoordinationRules = Field(default_factory=CoordinationRules)
