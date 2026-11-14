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
        view = Fixture();
        view.camps = new[] { new SandboxServerCamp { camp_id = "R-CAMP-recon", side = "red", unit_type = "recon",
            position = new SandboxServerPosition { x = "10.3", y = "26.2" } } };
        view.units[0].camp_id = "R-CAMP-recon";
        Check(Valid(view), "同阵营兵种兵营允许发布");
        projected = SandboxServerProjection.Convert(view);
        Check(projected.barracks.Length == 1 && projected.barracks[0].type == SandboxUnitType.Recon
            && projected.barracks[0].x == 10.3f, "兵营按实际兵种和业务坐标映射");
        Check(projected.units[0].barracksId == "R-CAMP-recon", "单位保留后端兵营归属");
        view.camps[0].side = "blue"; Check(!Valid(view), "另一阵营兵营不能进入当前投影");
        view.camps[0].side = "red"; view.camps[0].position.x = "NaN"; Check(!Valid(view), "兵营非有限坐标拒绝");
        view.camps[0].position.x = "10.3"; view.camps[0].unit_type = "unknown"; Check(!Valid(view), "未知兵营类型拒绝");
        view.camps[0].unit_type = "ground"; view.camps[0].position = null; Check(!Valid(view), "兵种兵营缺少位置拒绝");
        view.camps[0].unit_type = null; Check(Valid(view), "旧版固定兵营可省略兵种和位置");
        Check(SandboxServerProjection.Convert(view).barracks.Length == 0, "未提供位置的旧兵营不猜测坐标");
        view.camps[0].position = new SandboxServerPosition { x = "10.3", y = "26.2" };
        Check(Valid(view) && SandboxServerProjection.Convert(view).barracks.Length == 1, "旧版已放置共用兵营仍可显示");
        view.camps = new[] { view.camps[0], view.camps[0] }; Check(!Valid(view), "重复兵营身份拒绝");
        view = Fixture(); view.fivepoint = new SandboxFivepointView { step = 36, second = 180 };
        view.clock.simulation_step = 36; view.decisions.own_plan = null;
        Check(Valid(view), "五点模式允许独立任务投影，不要求旧演示建议");
        view.clock.simulation_step = 720; Check(Valid(view), "五点允许720步完整终局");
        view.clock.simulation_step = 721; Check(!Valid(view), "五点仍拒绝越过720步");
        Check(SandboxFivepointView.Display("833/60") == "13.88", "精确有理数只在显示层舍入");
        Check(SandboxFivepointView.Display("0/0") == "未知", "非法分母不显示有效比分");
        var playback = new SandboxFivepointPlayback();
        view.fivepoint.step = 24;
        Check(playback.TargetStep(view, 0) == 0 && playback.Rate == 0, "初始120秒不足开播门槛");
        view.fivepoint.step = 36;
        Check(playback.TargetStep(view, 0) == 0 && playback.Rate == 2, "180秒缓存以2倍准备播放");
        Check(playback.TargetStep(view, 2.49) == 0, "播放步未结束不提前显示结果");
        Check(playback.TargetStep(view, 2.5) == 1, "播放步结束切换同一完整帧");
        Check(playback.TargetStep(view, 20) == 1 && playback.PlayedStep == 0, "失败请求持续重试同一步，不推进观看位置");
        playback.ConfirmFrame("过期分支", 1, 36, 20);
        Check(playback.PlayedStep == 0, "其他分支响应不能推进游标");
        playback.ConfirmFrame(view.context.branch_id, 1, 36, 20);
        Check(playback.PlayedStep == 1 && playback.TargetStep(view, 20) == 1, "确认有效帧后开始下一播放步计时");
        view.context.branch_id = Guid.NewGuid().ToString(); view.fivepoint.step = 1;
        Check(playback.TargetStep(view, 3) == 0, "恢复分支清空旧播放游标");
        view.fivepoint.result = new SandboxFivepointResult { reason = "elimination" };
        Check(playback.TargetStep(view, 3) == 0 && playback.Rate == 0.5, "提前终局不等待180秒缓存");
        Check(playback.TargetStep(view, 13) == 1, "终局排空最后确认步");
        view.context.branch_id = Guid.NewGuid().ToString();
        view.fivepoint.result = null; view.fivepoint.step = 15; view.fivepoint.playback_origin_step = 15;
        Check(playback.TargetStep(view, 14) == 15 && playback.Rate == 0, "读档从实际完整步显示，不请求不存在的第0帧");
        view.fivepoint.step = 48;
        Check(playback.TargetStep(view, 15) == 15 && playback.Rate == 2, "非60秒边界读档使用恢复缓冲，避免无法凑满180秒");
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
