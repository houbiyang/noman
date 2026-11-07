"""现有 Unity 功能对应的严格输入契约；演示扩展不授予正式作战能力。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints


Role = Literal["host", "red", "blue"]
Side = Literal["red", "blue"]
UnitType = Literal["recon", "ground", "support"]
TaskType = Literal["patrol", "escort", "protect", "evacuate"]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class StrictModel(BaseModel):
    """拒绝客户端提交未声明字段。"""

    model_config = ConfigDict(extra="forbid")


class CreateGame(StrictModel):
    """创建明确标记的本机演示局；正式模式在当前范围内不可执行。"""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    task_type: TaskType = "patrol"
    is_demo: StrictBool
    spatial_test: StrictBool = False
    scenario_version_id: UUID | None = None
    rule_version_id: UUID | None = None


class SessionRequest(StrictModel):
    """由独立引导权限签发一个席位，不一次返回三方凭据。"""

    role: Role


class StateContext(StrictModel):
    """签名上下文只表达依据，不代替 Bearer 身份。"""

    game_id: UUID
    branch_id: UUID
    snapshot_id: UUID
    context_token: str = Field(min_length=1, max_length=4096)


class CommandRequest(StrictModel):
    """统一变更入口；载荷再按类型进行严格验证。"""

    context: StateContext
    command_type: str = Field(min_length=1, max_length=60)
    payload: dict


class EmptyPayload(StrictModel):
    """无业务参数的命令。"""


class ArmyQuantities(StrictModel):
    """演示配置的数量限制来自现有 Unity，不能充当正式兵力上限。"""

    recon: StrictInt = Field(ge=1, le=10)
    ground: StrictInt = Field(ge=1, le=10)
    support: StrictInt = Field(ge=1, le=10)


class ConfigureArmy(StrictModel):
    """完整替换本方演示编制数量。"""

    side: Side
    quantities: ArmyQuantities


class Position(StrictModel):
    """固定演示业务坐标，以十进制字符串传递。"""

    x: str = Field(min_length=1, max_length=32)
    y: str = Field(min_length=1, max_length=32)
    coordinate_system_id: Literal["unity-demo-120x80-v1"] = "unity-demo-120x80-v1"


class DeployArmy(StrictModel):
    """重布置本方某类演示单位，不作为正式兵营部署协议。"""

    side: Side
    unit_type: UnitType
    quantity: StrictInt = Field(ge=1, le=10)
    position: Position


class SpawnFromBarracks(StrictModel):
    """准备阶段从本方指定兵种兵营新增测试单位。"""

    side: Side
    unit_type: UnitType
    quantity: StrictInt = Field(ge=1, le=10)


class MoveUnit(StrictModel):
    """记录现有演示的机动目标，不接收坐标以外的权威能力字段。"""

    unit_id: str = Field(min_length=1, max_length=40)
    position: Position


class MovementOrder(StrictModel):
    """按版本化测试规则安排移动；部署试走使用独立资源和推进入口。"""

    unit_id: str = Field(min_length=1, max_length=40)
    mode: Literal["move", "fast_move", "reverse_move", "stop_move", "return_to_barracks", "reset_test_energy", "deploy_start"] = "move"
    position: Position | None = None
    waypoints: list[Position] = Field(default_factory=list, max_length=7)


class DeploymentMovementStep(StrictModel):
    """仅推进指定单位的部署试走，不推进对局时钟或消耗正式能源。"""

    unit_id: str = Field(min_length=1, max_length=40)


class PlaceBarracks(StrictModel):
    """保存本方指定兵种的兵营位置，已有单位保持原位置。"""

    side: Side
    unit_type: UnitType
    position: Position


class DeleteUnit(StrictModel):
    """仅删除当前席位有权管理的单个单位。"""

    unit_id: str = Field(min_length=1, max_length=40)


class TestPreparation(StrictModel):
    """整体配置测试兵营和三类单位；空位置表示撤销兵营。"""

    side: Side
    position: Position | None = None
    quantities: ArmyQuantities
    preview_token: str | None = Field(default=None, max_length=4096)


class PreparationPreview(StrictModel):
    """绑定当前上下文的测试准备预览。"""

    context: StateContext
    preparation: TestPreparation


class ReadyPayload(StrictModel):
    """本方准备状态。"""

    ready: StrictBool


class TaskPayload(StrictModel):
    """主持人在准备阶段选择既有四类任务模板。"""

    task_type: TaskType


class DemoPlanChanges(StrictModel):
    """映射现有 Unity 文本分工的演示契约，与正式结构化动作分开。"""

    priority: ShortText | None = None
    recon_assignment: ShortText | None = None
    ground_assignment: ShortText | None = None
    support_assignment: ShortText | None = None
    resource_limit: StrictInt | None = Field(default=None, ge=0, le=20)


class ReviewProposal(StrictModel):
    """本方建议的唯一最终处理。"""

    proposal_id: UUID
    decision: Literal["approve", "modify_approve", "reject"]
    changes: DemoPlanChanges = Field(default_factory=DemoPlanChanges)


class Condition(StrictModel):
    """四类现有事件；当前后端只登记其演示事实。"""

    condition_type: Literal["equipment_fault", "communication_outage", "weather_change", "task_change"]
    target_unit_id: str = Field(min_length=1, max_length=40)


class ConditionPreview(StrictModel):
    """预览绑定当前会话、输入与上下文。"""

    context: StateContext
    condition: Condition


class TriggerCondition(StrictModel):
    """只能采用同输入且未过期的预览。"""

    condition: Condition
    preview_token: str = Field(min_length=1, max_length=4096)


class SavePayload(StrictModel):
    """多个独立手动存档，不按名称覆盖。"""

    display_name: str | None = Field(default=None, max_length=80)


class RestorePayload(StrictModel):
    """读档仅引用后端已提交的存档标识。"""

    save_id: UUID


class ExitPayload(SavePayload):
    """退出前必须先暂停，保存失败不能回报退出成功。"""

    choice: Literal["save_and_exit", "without_save"]


PAYLOAD_MODELS = {
    "movement_order": MovementOrder, "advance_deployment_movement": DeploymentMovementStep,
    "place_barracks": PlaceBarracks, "spawn_from_barracks": SpawnFromBarracks, "delete_unit": DeleteUnit,
    "set_preparation": TestPreparation,
    "configure_army": ConfigureArmy, "deploy_army": DeployArmy, "move_unit": MoveUnit,
    "set_ready": ReadyPayload, "set_tasks": TaskPayload, "review_proposal": ReviewProposal,
    "trigger_condition": TriggerCondition, "create_save": SavePayload, "restore_save": RestorePayload,
    "complete_exit": ExitPayload,
    **{name: EmptyPayload for name in ("start_game", "pause", "continue", "advance_step", "begin_exit", "reload_demo_plan")},
}
