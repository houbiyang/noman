using System;
using Qiongtu.Sandbox;

/// <summary>校验权威地图投影中的版本、坐标、角色和事件输入边界。</summary>
internal static class ServerProjectionChecks
{
    private static int checks;
    private const string GameId = "109439ed-b4d9-4b30-b464-bec7bf73d340";

    /// <summary>运行不依赖 Unity 图形环境的契约负例与显示映射。</summary>
    private static int Main()
    {
        SandboxServerView view = Fixture();
        Check(Valid(view), "完整后端投影通过");
        SandboxProjection projected = SandboxServerProjection.Convert(view);
        Check(projected.units.Length == 1 && projected.units[0].x == 25.5f && projected.units[0].z == 44, "十进制业务坐标准确映射");
        Check(projected.ownPlan.side == SandboxRole.Red && projected.ownPlan.status == SandboxPlanStatus.Pending, "本方建议准确映射");
        Check(projected.records.Length == 1 && projected.records[0].id == "event-1", "同快照事件保留身份");
        Check(projected.waitingSummary == "红方、蓝方", "等待摘要不包含私有正文");
        view.map.content_hash = "different"; Check(!Valid(view), "地图摘要不同拒绝发布"); view = Fixture();
        view.map.map_id = "different"; Check(!Valid(view), "地图身份不同拒绝发布"); view = Fixture();
        view.map.coordinate_system_id = "meter"; Check(!Valid(view), "显示米制坐标不能混入业务坐标"); view = Fixture();
        view.context.game_id = Guid.NewGuid().ToString(); Check(!Valid(view), "跨对局上下文拒绝"); view = Fixture();
        view.context.snapshot_id = "bad"; Check(!Valid(view), "快照标识无效拒绝"); view = Fixture();
        view.context.branch_id = "bad"; Check(!Valid(view), "分支标识无效拒绝"); view = Fixture();
        view.context.context_token = null; Check(!Valid(view), "缺少写入依据拒绝"); view = Fixture();
        view.units[0].side = "blue"; Check(!Valid(view), "红方不得接收隐藏蓝方单位"); view = Fixture();
        view.units[0].unit_type = "unknown"; Check(!Valid(view), "未知兵种拒绝"); view = Fixture();
        view.units[0].position.x = "NaN"; Check(!Valid(view), "非有限坐标拒绝"); view = Fixture();
        view.units[0].position.y = "81"; Check(!Valid(view), "超出演示地图范围拒绝"); view = Fixture();
        view.units[0].position.coordinate_system_id = "other"; Check(!Valid(view), "单位坐标系不一致拒绝"); view = Fixture();
        view.units = new[] { view.units[0], view.units[0] }; Check(!Valid(view), "重复单位身份拒绝"); view = Fixture();
        view.tasks[0].side = "blue"; Check(!Valid(view), "私有任务不得跨方"); view = Fixture();
        view.tasks[0].nodes = null; Check(!Valid(view), "缺失任务节点拒绝"); view = Fixture();
        view.tasks[0].nodes[0] = null; Check(!Valid(view), "空任务节点拒绝"); view = Fixture();
        view.timeline[0] = null; Check(!Valid(view), "空事件记录拒绝"); view = Fixture();
        view.conditions = null; Check(!Valid(view), "缺少事件状态拒绝"); view = Fixture();
        view.clock.ready = null; Check(!Valid(view), "缺少准备状态拒绝"); view = Fixture();
        view.clock.simulation_step = 61; Check(!Valid(view), "截止步之外拒绝"); view = Fixture();
        view.clock.phase = "unknown"; Check(!Valid(view), "未知生命周期拒绝"); view = Fixture();
        view.role = "host"; Check(!Valid(view), "主持人不得接收私有建议");
        view.decisions.own_plan = null; Check(Valid(view), "主持人摘要视图允许");
        Check(SandboxServerProjection.Convert(view).ownPlan == null, "主持人映射无私有正文");
        view.decisions.own_plan = new SandboxServerPlan();
        SandboxServerProjection.NormalizeMissingPlan(view, "{\"decisions\":{\"waiting_sides\":[]}}");
        Check(Valid(view) && view.decisions.own_plan == null, "主持人缺失字段的默认空对象被正确还原");
        view.decisions.own_plan = new SandboxServerPlan { priority = "不能出现的私有正文" };
        SandboxServerProjection.NormalizeMissingPlan(view, "{}");
        Check(!Valid(view), "缺失字段标记不能掩盖实际私有正文");
        view.decisions.own_plan = new SandboxServerPlan();
        SandboxServerProjection.NormalizeMissingPlan(view, "{\"decisions\":{\"own_plan\":{}}}");
        Check(!Valid(view), "显式私有建议字段继续拒绝");
        view = Fixture(); view.decisions.own_plan = new SandboxServerPlan();
        SandboxServerProjection.NormalizeMissingPlan(view, "{}");
        Check(!Valid(view) && view.decisions.own_plan != null, "本方建议缺失不能按主持人规则放行");
        Console.WriteLine("后端地图投影检查通过：" + checks + " 项；不替代 Unity 网络及画面验收。");
        return 0;
    }

    private static SandboxServerView Fixture()
    {
        return new SandboxServerView {
            schema_version = "game-view-v1", role = "red",
            context = new SandboxServerContext { game_id = GameId, branch_id = Guid.NewGuid().ToString(), snapshot_id = Guid.NewGuid().ToString(), context_token = "test-only" },
            game = new SandboxServerGame { game_id = GameId, title = "契约夹具", is_demo = true },
            clock = new SandboxServerClock { simulation_step = 0, phase = "preparing", ready = new SandboxServerReady() },
            map = new SandboxServerMap { map_id = "map-test", content_hash = "hash-test", coordinate_system_id = "unity-demo-120x80-v1" },
            units = new[] { new SandboxServerUnit { unit_id = "R-U01", name = "侦察", side = "red", unit_type = "recon", position = new SandboxServerPosition { x = "25.5", y = "44" } } },
            tasks = new[] { new SandboxServerTask { side = "red", title = "巡查", nodes = new[] { new SandboxServerTaskNode { node_id = "node-1", status = "not_evaluated" } } } },
            conditions = Array.Empty<SandboxServerCondition>(),
            timeline = new[] { new SandboxServerRecord { event_id = "event-1", title = "仅登记", side = "red" } },
            decisions = new SandboxServerDecisions { waiting_sides = new[] { "red", "blue" }, own_plan = new SandboxServerPlan { side = "red", status = "pending_review" } },
            capabilities = new SandboxServerCapabilities()
        };
    }

    private static bool Valid(SandboxServerView view) => SandboxServerProjection.Validate(view, GameId, "map-test", "hash-test").Length == 0;
    private static void Check(bool condition, string name) { checks++; if (!condition) throw new InvalidOperationException("检查失败：" + name); }
}
