using System;
using System.Globalization;
using System.Linq;

namespace Qiongtu.Sandbox
{
    /// <summary>向本机接入服务申请当前地图与所选角色的会话。</summary>
    [Serializable] public sealed class SandboxServerLaunchInput
    {
        public string role, map_hash, capture_layout_sha256;
    }

    /// <summary>本机服务返回的单席位连接信息，不包含引导权限或其他角色凭据。</summary>
    [Serializable] public sealed class SandboxServerLaunch
    {
        public string api_url, game_id, role, access_token;
    }

    /// <summary>后端返回的不可自行递增的写入依据。</summary>
    [Serializable] public sealed class SandboxServerContext
    {
        public string game_id, branch_id, snapshot_id, context_token;
    }

    /// <summary>后端地图视图的数据契约，字段与 HTTP JSON 一致。</summary>
    [Serializable] public sealed class SandboxServerView
    {
        public string schema_version, role;
        public SandboxServerContext context;
        public SandboxServerGame game;
        public SandboxServerClock clock;
        public SandboxServerMap map;
        public SandboxServerUnit[] units;
        public SandboxServerUnit[] contacts;
        public SandboxServerUnitInteractions unit_interactions;
        public SandboxAttackEvent[] attack_events;
        public SandboxManualBattleView manual_battle;
        public SandboxReplayPoint[] battle_capture_points;
        public SandboxServerCamp[] camps;
        public SandboxServerTask[] tasks;
        public SandboxServerCondition[] conditions;
        public SandboxServerRecord[] timeline;
        public SandboxServerDecisions decisions;
        public SandboxServerCapabilities capabilities;
        public bool spatial_test;
        public SandboxServerMovementPath[] movement_paths;
        public SandboxVictoryEvidence victory;
        public SandboxServerBattleResult battle_result;
    }

    [Serializable] public sealed class SandboxServerBattleResult { public bool final; public string verdict, reason; public int redPoints, bluePoints, scoreMaximum; public double redScore100, blueScore100, redPercent, bluePercent; public string[] calculations, milestones; public SandboxStageScore[] stageScores; }
    [Serializable] public sealed class SandboxServerGoalDecision { public string goal_id, evidence; public bool red_completed, blue_completed; }
    [Serializable] public sealed class SandboxServerSettleStage { public int stage; public string evidence; public SandboxServerGoalDecision[] goals; }
    [Serializable] public sealed class SandboxServerArchive { public string id, title, branch_id, snapshot_id, created_at, role, phase; public int simulation_step; public bool automatic; public SandboxServerView projection; }
    [Serializable] public sealed class SandboxServerArchiveList { public SandboxServerArchive[] items; public int total, offset, limit; }
    [Serializable] public sealed class SandboxServerArchiveDetail { public SandboxServerArchive item; }

    /// <summary>对局公开身份。</summary>
    [Serializable] public sealed class SandboxServerGame { public string game_id, title; public bool is_demo; }
    /// <summary>权威时钟及双方准备状态。</summary>
    [Serializable] public sealed class SandboxServerClock { public int simulation_step, deadline_step; public string phase; public SandboxServerReady ready; }
    /// <summary>双方已持久化的准备状态。</summary>
    [Serializable] public sealed class SandboxServerReady { public bool red, blue; }
    /// <summary>地图版本和业务坐标命名空间。</summary>
    [Serializable] public sealed class SandboxServerMap { public string map_id, content_hash, coordinate_system_id; }
    /// <summary>以十进制字符串传输的业务坐标。</summary>
    [Serializable] public sealed class SandboxServerPosition { public string x, y, coordinate_system_id = "unity-demo-120x80-v1"; }
    /// <summary>经过服务端角色过滤的单位。</summary>
    [Serializable] public sealed class SandboxServerUnitInteractions { public bool enabled; public string rule_version_id, geometry_policy; public int elapsed_seconds; }
    /// <summary>已提交、按开火时可见权限过滤的纯表现事件；不能用于推导额外伤害。</summary>
    [Serializable] public sealed class SandboxAttackEndpoint
    {
        public string unit_id, life_id, unit_type, side;
        public SandboxServerPosition position;
        public bool docked;
    }
    [Serializable] public sealed class SandboxAttackEvent
    {
        public string event_id;
        public double at, amount, hp_after, ammo_after;
        public bool lethal;
        public SandboxAttackEndpoint source, target;
    }
    [Serializable] public sealed class SandboxServerStocks { public double energy, ammo, repair; }
    [Serializable] public class SandboxServerUnitOrder { public string unit_id, life_id, action, resource = "energy"; }
    [Serializable] public sealed class SandboxServerTargetUnitOrder : SandboxServerUnitOrder { public string target_id, target_life_id; }
    [Serializable] public sealed class SandboxServerReconOrder { public string unit_id, life_id, action = "recon"; public SandboxServerPosition position; }
    [Serializable] public sealed class SandboxServerUnit { public string interaction_action, interaction_target_id; public string life_id; public double max_durability, ammo, max_ammo, speed_m_per_minute, current_speed_m_per_minute; public bool docked; public SandboxServerStocks stocks; public string unit_id, side, unit_type, name, status, camp_id, formation_id; public double energy, supply, durability, movement_test_energy, heading_degrees; public int movement_test_steps; public bool in_barracks; public SandboxServerPosition position; }
    /// <summary>以世界米制表达的后端已确认路径点。</summary>
    [Serializable] public sealed class SandboxServerMovementPoint { public float x, z; }
    /// <summary>角色过滤后的移动路线、单次试走轨迹和预计成本。</summary>
    [Serializable] public sealed class SandboxServerMovementPath
    {
        public string unit_id, route_id, mode, status, formation_leader_id;
        public bool deployment_test;
        public int trace_sequence;
        public double estimated_steps, estimated_energy;
        public SandboxServerMovementPoint[] points, route_points;
    }
    /// <summary>按当前席位过滤的兵营，旧版共用兵营可省略兵种。</summary>
    [Serializable] public sealed class SandboxServerCamp { public string camp_id, side, unit_type, status; public bool deployment_validated; public SandboxServerPosition position; }
    /// <summary>任务节点的实际服务端状态。</summary>
    [Serializable] public sealed class SandboxServerTaskNode { public string node_id, status; }
    /// <summary>任务对象及节点，不根据演示步推算完成。</summary>
    [Serializable] public sealed class SandboxServerTask { public string task_id, side, task_type, title, status; public SandboxServerTaskNode[] nodes; }
    /// <summary>事件登记对象；登记状态不代表效果已执行。</summary>
    [Serializable] public sealed class SandboxServerCondition { public string condition_id, condition_type, target_unit_id, status; public int registered_step; public bool effect_applied; }
    /// <summary>与视图同一快照头内可见的时间线记录。</summary>
    [Serializable] public sealed class SandboxServerRecord { public string event_id, title, detail, side; public int simulation_step; }
    /// <summary>本方固定建议和处理状态。</summary>
    [Serializable] public sealed class SandboxServerPlan
    {
        public string proposal_id, side, status, priority, recon_assignment, ground_assignment, support_assignment;
        public int version, resource_limit;
    }
    /// <summary>主持人只取得等待摘要，不取得双方私有正文。</summary>
    [Serializable] public sealed class SandboxServerDecisions { public string[] waiting_sides; public SandboxServerPlan own_plan; }
    /// <summary>每次视图中的服务端操作资格。</summary>
    [Serializable] public sealed class SandboxServerCapabilities
    {
        public bool can_control_time, can_start_game, can_advance, can_review_proposal, can_set_ready, can_manage_units, can_move_unit;
        public bool can_place_barracks, can_delete_unit, can_settle_goal_stage, can_surrender;
        public bool can_test_movement;
    }
    /// <summary>可公开的稳定错误信息。</summary>
    [Serializable] public sealed class SandboxServerError { public string code, message; }
    /// <summary>错误或命令收据中共同的公开字段。</summary>
    [Serializable] public sealed class SandboxServerReceipt { public string status; public SandboxServerError error; }
    /// <summary>后端已提交存档的可读摘要。</summary>
    [Serializable] public sealed class SandboxServerSave
    {
        public string save_id, save_kind, display_name, branch_id, snapshot_id, saved_at, phase;
        public int simulation_step;
        public bool can_restore;
    }
    /// <summary>存档列表响应，不返回快照正文。</summary>
    [Serializable] public sealed class SandboxServerSaveList
    {
        public SandboxServerSave[] items;
        public int offset, limit, total;
    }
    /// <summary>后端手动存档输入。</summary>
    [Serializable] public sealed class SandboxServerSaveInput { public string display_name; }
    /// <summary>后端恢复存档输入。</summary>
    [Serializable] public sealed class SandboxServerRestoreInput { public string save_id; }
    /// <summary>后端保存退出输入。</summary>
    [Serializable] public sealed class SandboxServerExitInput { public string display_name, choice; }
    /// <summary>独立空间测试准备配置，参数不作为正式规则。</summary>
    [Serializable] public sealed class SandboxServerPreparation
    {
        public string side, preview_token;
        public SandboxServerPosition position;
        public SandboxServerQuantities quantities;
    }
    /// <summary>准备预览请求，绑定当前权威头。</summary>
    [Serializable] public sealed class SandboxServerPreparationRequest
    {
        public SandboxServerContext context;
        public SandboxServerPreparation preparation;
    }
    /// <summary>事件预览的权威凭据。</summary>
    [Serializable] public sealed class SandboxServerPreview { public string preview_token, summary; public bool valid, effect_applied; }
    /// <summary>事件登记输入。</summary>
    [Serializable] public sealed class SandboxServerConditionInput { public string condition_type, target_unit_id; }
    /// <summary>事件预览请求。</summary>
    [Serializable] public sealed class SandboxServerPreviewRequest { public SandboxServerContext context; public SandboxServerConditionInput condition; }
    /// <summary>预览通过后原样提交的事件输入。</summary>
    [Serializable] public sealed class SandboxServerTrigger { public SandboxServerConditionInput condition; public string preview_token; }
    /// <summary>统一输入只声明命令依据，载荷由内部类型单独序列化。</summary>
    [Serializable] public sealed class SandboxServerCommandHead { public SandboxServerContext context; public string command_type; }
    /// <summary>单位机动输入。</summary>
    [Serializable] public sealed class SandboxServerMove { public string unit_id; public SandboxServerPosition position; }
    /// <summary>三兵种规则移动命令，不接受客户端提供速度、资源或通行结论。</summary>
    [Serializable] public sealed class SandboxServerMovementOrder
    {
        public string unit_id, mode;
        public bool formation;
        public SandboxServerPosition position;
        public SandboxServerPosition[] waypoints = Array.Empty<SandboxServerPosition>();
    }
    /// <summary>停止、返营和重置试走能源不携带目标坐标。</summary>
    [Serializable] public sealed class SandboxServerMovementOrderWithoutPosition
    {
        public string unit_id, mode;
        public bool formation;
        public SandboxServerPosition[] waypoints = Array.Empty<SandboxServerPosition>();
    }
    /// <summary>推进一个指定单位的独立部署试走步。</summary>
    [Serializable] public sealed class SandboxServerDeploymentStep { public string unit_id; public bool formation; }
    /// <summary>地图选址后的单兵种兵营输入，由服务端核对完整占地和权限。</summary>
    [Serializable] public sealed class SandboxServerBarracks { public string side, unit_type; public SandboxServerPosition position; }
    /// <summary>准备阶段按本次输入数量从指定兵种兵营新增单位。</summary>
    [Serializable] public sealed class SandboxServerBarracksSpawn { public string side, unit_type; public int quantity; }
    /// <summary>单个单位的删除输入，服务端保留兵营及历史记录。</summary>
    [Serializable] public sealed class SandboxServerDeleteUnit { public string unit_id; }
    /// <summary>三类数量配置。</summary>
    [Serializable] public sealed class SandboxServerQuantities { public int recon, ground, support; }
    /// <summary>编制配置输入。</summary>
    [Serializable] public sealed class SandboxServerConfigure { public string side; public SandboxServerQuantities quantities; }
    /// <summary>地图部署输入。</summary>
    [Serializable] public sealed class SandboxServerDeploy { public string side, unit_type; public int quantity; public SandboxServerPosition position; }
    /// <summary>双方准备输入。</summary>
    [Serializable] public sealed class SandboxServerReadyInput { public bool ready; }
    /// <summary>主持人任务选择输入。</summary>
    [Serializable] public sealed class SandboxServerTaskInput { public string task_type; }
    /// <summary>本方可编辑的建议字段。</summary>
    [Serializable] public sealed class SandboxServerPlanChanges { public string priority, recon_assignment, ground_assignment, support_assignment; public int resource_limit; }
    /// <summary>本方建议最终处理输入。</summary>
    [Serializable] public sealed class SandboxServerReview { public string proposal_id, decision; public SandboxServerPlanChanges changes; }
    /// <summary>不携带修改字段的建议处理输入。</summary>
    [Serializable] public sealed class SandboxServerReviewSimple { public string proposal_id, decision; }
    /// <summary>浏览器原生网络向 Unity 返回的单次响应。</summary>
    [Serializable] public sealed class SandboxBrowserReply { public int sequence, code; public string body; }

    /// <summary>把服务端 JSON 对象校验并映射为现有三维显示投影。</summary>
    public static class SandboxServerProjection
    {
        /// <summary>还原Unity为缺失的可选协议/建议字段生成的空对象；显式字段继续接受权限检查。</summary>
        /// <param name="view">刚完成反序列化的服务端视图。</param>
        /// <param name="json">同一次请求的原始 JSON，仅用于判断可选字段是否出现。</param>
        public static void NormalizeMissingPlan(SandboxServerView view, string json)
        {
            // JsonUtility会为未出现在旧HTTP响应中的嵌套对象创建空实例。
            // 没有此还原，旧局也会被误认为新手动局并停止同步。
            if (view?.manual_battle != null && json != null && !json.Contains("\"manual_battle\"")
                && string.IsNullOrEmpty(view.manual_battle.mode) && string.IsNullOrEmpty(view.manual_battle.schema_version))
                view.manual_battle = null;
            SandboxServerPlan plan = view?.decisions?.own_plan;
            if ((view?.role != "host" && view?.manual_battle?.mode != "manual") || plan == null || json == null || json.Contains("\"own_plan\"")) return;
            if (plan.version == 0 && plan.resource_limit == 0 && string.IsNullOrEmpty(plan.proposal_id)
                && string.IsNullOrEmpty(plan.side) && string.IsNullOrEmpty(plan.status) && string.IsNullOrEmpty(plan.priority)
                && string.IsNullOrEmpty(plan.recon_assignment) && string.IsNullOrEmpty(plan.ground_assignment)
                && string.IsNullOrEmpty(plan.support_assignment)) view.decisions.own_plan = null;
        }

        /// <summary>在发布到画面前校验角色、地图、坐标以及稳定身份。</summary>
        /// <param name="view">完整服务端视图。</param>
        /// <param name="gameId">当前凭据绑定的目标对局。</param>
        /// <param name="mapId">本地显示地图标识。</param>
        /// <param name="mapHash">本地显示地图内容摘要。</param>
        /// <returns>空字符串表示可安全发布，否则返回中文原因。</returns>
        public static string Validate(SandboxServerView view, string gameId, string mapId, string mapHash)
        {
            if (view == null || view.schema_version != "game-view-v1" || view.context == null || view.game == null || view.clock == null
                || view.decisions == null || view.capabilities == null || view.tasks == null || view.conditions == null || view.timeline == null)
                return "后端视图缺少必要字段，请检查接口版本";
            if (view.role != "red" && view.role != "blue" && view.role != "host") return "后端席位角色无效";
            if (view.clock.ready == null || view.clock.simulation_step < 0 || view.clock.simulation_step > 60
                || !new[] { "preparing", "awaiting_confirmation", "paused", "running", "finished" }.Contains(view.clock.phase)) return "后端生命周期无效";
            if (view.game.game_id != gameId || view.context.game_id != gameId || !Guid.TryParse(view.context.branch_id, out _)
                || !Guid.TryParse(view.context.snapshot_id, out _) || string.IsNullOrEmpty(view.context.context_token)) return "后端对局上下文不一致";
            if (view.map == null || view.map.map_id != mapId || view.map.content_hash != mapHash || view.map.coordinate_system_id != "unity-demo-120x80-v1")
                return "后端地图版本或坐标系与当前场景不一致，已停止同步";
            if (view.units == null || view.units.Any(unit => unit == null || string.IsNullOrEmpty(unit.unit_id))
                || view.units.Select(unit => unit.unit_id).Distinct().Count() != view.units.Length) return "后端单位身份缺失或重复";
            foreach (SandboxServerUnit unit in view.units)
            {
                if ((unit.side != "red" && unit.side != "blue") || (view.role != "host" && unit.side != view.role)) return "后端单位超出当前席位范围";
                if (unit.unit_type != "recon" && unit.unit_type != "ground" && unit.unit_type != "support") return "后端单位类型不支持";
                if (unit.position == null || unit.position.coordinate_system_id != "unity-demo-120x80-v1"
                    || !Coordinate(unit.position.x, 120, out _) || !Coordinate(unit.position.y, 80, out _)) return "后端单位坐标无效";
            }
            var camps = view.camps ?? Array.Empty<SandboxServerCamp>();
            if (camps.Any(camp => camp == null || string.IsNullOrEmpty(camp.camp_id))
                || camps.Select(camp => camp.camp_id).Distinct().Count() != camps.Length) return "后端兵营身份缺失或重复";
            foreach (SandboxServerCamp camp in camps)
            {
                if ((camp.side != "red" && camp.side != "blue") || (view.role != "host" && camp.side != view.role)) return "后端兵营超出当前席位范围";
                if (!string.IsNullOrEmpty(camp.unit_type) && camp.unit_type != "recon" && camp.unit_type != "ground" && camp.unit_type != "support") return "后端兵营兵种无效";
                if (camp.position != null && (camp.position.coordinate_system_id != "unity-demo-120x80-v1"
                    || !Coordinate(camp.position.x, 120, out _) || !Coordinate(camp.position.y, 80, out _))) return "后端兵营坐标无效";
                if (!string.IsNullOrEmpty(camp.unit_type) && camp.position == null) return "后端兵种兵营缺少位置";
            }
            if (view.role == "host" && view.decisions.own_plan != null) return "主持人视图包含未授权的私有建议";
            if (view.manual_battle == null && view.role != "host" && (view.decisions.own_plan == null || view.decisions.own_plan.side != view.role)) return "本方建议归属无效";
            if (view.manual_battle != null)
            {
                var battle = view.manual_battle;
                if (battle.schema_version != "qinglan-manual-planning-v1" || battle.mode != "manual"
                    || (battle.stage != "planning" && battle.stage != "finished") || battle.round_seconds != 60 || battle.maximum_rounds != 60
                    || battle.round_number < 1 || battle.round_number > 60 || battle.completed_rounds < 0 || battle.completed_rounds > 60
                    || battle.elapsed_seconds < 0 || battle.elapsed_seconds > 3600 || battle.elapsed_seconds % 5 != 0
                    || battle.submitted == null || battle.own_drafts == null || battle.own_drafts.Length > 30
                    || view.decisions.own_plan != null || battle.completed_rounds != view.clock.simulation_step)
                    return "手动战斗协议、轮次或角色投影无效";
                if (battle.stage == "planning" && (battle.completed_rounds >= 60 || battle.round_number != battle.completed_rounds + 1
                    || battle.elapsed_seconds != battle.completed_rounds * 60)) return "手动战斗时钟与轮次不一致";
                bool locked = view.role == "blue" ? battle.submitted.blue : battle.submitted.red;
                if ((view.role == "host" && (battle.own_drafts.Length > 0 || battle.can_edit || battle.can_submit))
                    || (view.role != "host" && battle.can_resolve)
                    || ((battle.can_edit || battle.can_submit) && (battle.stage != "planning" || locked))
                    || (battle.can_resolve && (!battle.engine_ready || battle.stage != "planning" || !battle.submitted.blue || !battle.submitted.red)))
                    return "手动战斗提交权限或私有草稿范围无效";
                if (battle.own_drafts.Any(item => item == null || !view.units.Any(u => u.unit_id == item.unit_id && u.life_id == item.life_id))
                    || battle.own_drafts.Select(item => item.unit_id).Distinct().Count() != battle.own_drafts.Length)
                    return "本方手动草稿单位或生命实例无效";
            }
            if (view.tasks.Any(task => task == null || task.nodes == null || (view.role != "host" && task.side != view.role))) return "任务对象或归属无效";
            if (view.tasks.Any(task => task.nodes.Any(node => node == null || string.IsNullOrEmpty(node.node_id)))
                || view.timeline.Any(record => record == null || string.IsNullOrEmpty(record.event_id))
                || view.conditions.Any(condition => condition == null || string.IsNullOrEmpty(condition.condition_id))) return "后端事件或任务节点字段无效";
            return string.Empty;
        }

        /// <summary>只接受有限且位于业务范围内的坐标。</summary>
        public static bool Coordinate(string text, float maximum, out float result)
        {
            return float.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out result)
                && !float.IsNaN(result) && !float.IsInfinity(result) && result >= 0 && result <= maximum;
        }

        /// <summary>完成校验后映射，不自行增加单位、进度、事件或建议。</summary>
        /// <param name="view">已经验证的后端视图。</param>
        /// <returns>供现有世界与 HUD 同时使用的显示投影。</returns>
        public static SandboxProjection Convert(SandboxServerView view)
        {
            SandboxServerPlan plan = view.decisions.own_plan;
            return new SandboxProjection
            {
                role = Role(view.role), title = view.game.title, step = view.clock.simulation_step,
                taskType = view.tasks.FirstOrDefault()?.title ?? "尚未配置任务",
                units = view.units.Concat(view.contacts ?? Array.Empty<SandboxServerUnit>()).GroupBy(unit => unit.unit_id).Select(group => group.First()).Where(unit => unit.position != null).Select(unit => new SandboxUnitData {
                    id = unit.unit_id, name = unit.name, side = Role(unit.side), status = unit.status,
                    type = unit.unit_type == "recon" ? SandboxUnitType.Recon : unit.unit_type == "ground" ? SandboxUnitType.Ground : SandboxUnitType.Support,
                    x = float.Parse(unit.position.x, CultureInfo.InvariantCulture), z = float.Parse(unit.position.y, CultureInfo.InvariantCulture),
                    health = (float)unit.durability, maxHealth = (float)unit.max_durability,
                    speedMPerMinute = (float)unit.speed_m_per_minute, currentSpeedMPerMinute = (float)unit.current_speed_m_per_minute, energy = (float)unit.energy,
                    ammo = (float)unit.ammo, maxAmmo = (float)unit.max_ammo, hasCombatResources = !string.IsNullOrEmpty(unit.life_id), docked = unit.docked,
                    barracksId = unit.camp_id
                }).ToArray(),
                barracks = (view.camps ?? Array.Empty<SandboxServerCamp>()).Where(camp => camp.position != null)
                    .OrderBy(camp => string.IsNullOrEmpty(camp.unit_type)).Select(camp => new SandboxBarracksData {
                    id = camp.camp_id, side = Role(camp.side),
                    type = camp.unit_type == "recon" ? SandboxUnitType.Recon : camp.unit_type == "support" ? SandboxUnitType.Support : SandboxUnitType.Ground,
                    x = float.Parse(camp.position.x, CultureInfo.InvariantCulture), z = float.Parse(camp.position.y, CultureInfo.InvariantCulture)
                }).ToArray(),
                ownPlan = plan == null ? null : new SandboxPlanData { side = Role(plan.side), version = plan.version,
                    status = plan.status == "adopted" ? SandboxPlanStatus.Approved : plan.status == "rejected" ? SandboxPlanStatus.Rejected : SandboxPlanStatus.Pending,
                    priority = plan.priority, resourceAllowance = plan.resource_limit, reconAssignment = plan.recon_assignment,
                    groundAssignment = plan.ground_assignment, supportAssignment = plan.support_assignment },
                waitingSummary = string.Join("、", (view.decisions.waiting_sides ?? Array.Empty<string>()).Select(side => side == "red" ? "红方" : "蓝方")),
                records = view.timeline.Select(record => new SandboxRecord { id = record.event_id, step = record.simulation_step,
                    title = record.title, detail = record.detail, side = Role(record.side) }).ToArray()
            };
        }

        /// <summary>角色字符串转换仅用于通过校验的显示对象。</summary>
        public static SandboxRole Role(string role) => role == "red" ? SandboxRole.Red : role == "blue" ? SandboxRole.Blue : SandboxRole.Host;
    }
}
