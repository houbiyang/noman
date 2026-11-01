using System;
using System.Linq;
using Qiongtu.Sandbox;

/// <summary>不依赖 Unity 运行时或第三方测试包的前端状态验证入口。</summary>
internal static class DomainChecks
{
    private static int checks;

    /// <summary>验证权限投影、建议处理、推进阻塞、预览版本与存档字段。</summary>
    /// <returns>全部通过时返回零。</returns>
    private static int Main()
    {
        var source = new DemoSandboxViewSource();
        Check(source.GetProjection(SandboxRole.Red).units.Length == 3, "红方只投影三个本方单位");
        Check(source.GetProjection(SandboxRole.Blue).units.All(unit => unit.side == SandboxRole.Blue), "蓝方投影不含红方单位");
        Check(source.GetProjection(SandboxRole.Host).units.Length == 6, "主持人可观察六个单位");
        Check(source.GetProjection(SandboxRole.Host).ownPlan == null, "主持人不获得私有建议详情");
        SandboxProjection isolated = source.GetProjection(SandboxRole.Red);
        isolated.units[0].x = 999;
        Check(source.GetProjection(SandboxRole.Red).units[0].x != 999, "投影单位与内部状态引用隔离");
        isolated.records[0].title = "改写投影";
        Check(source.GetProjection(SandboxRole.Red).records[0].title != "改写投影", "投影记录与内部状态引用隔离");
        SandboxSessionData detachedSnapshot = source.Current;
        detachedSnapshot.units[0].x = 999;
        Check(source.Current.units[0].x != 999, "保存用副本不开放内部单位引用");
        bool invalidRoleRejected = false;
        try { source.GetProjection((SandboxRole)99); }
        catch (ArgumentOutOfRangeException) { invalidRoleRejected = true; }
        Check(invalidRoleRejected, "未知角色不能读取其他阵营建议");
        Check(!source.TryAdvance(SandboxRole.Red, out _), "操作员不能推进演示");
        Check(!source.TryAdvance(SandboxRole.Host, out _), "双方待确认时主持人不能推进");

        SandboxPlanData redDraft = source.GetProjection(SandboxRole.Red).ownPlan;
        Check(!source.TryApprove(SandboxRole.Host, redDraft, out _), "主持人不能代确认");
        Check(!source.TryApprove(SandboxRole.Blue, redDraft, out _), "蓝方不能处理红方建议");
        redDraft.resourceAllowance = 21;
        Check(!source.TryApprove(SandboxRole.Red, redDraft, out _), "超预算建议被拒绝");
        redDraft.resourceAllowance = -1;
        Check(!source.TryApprove(SandboxRole.Red, redDraft, out _), "负数资源额度被拒绝");
        redDraft.resourceAllowance = 2;
        redDraft.priority = " ";
        Check(!source.TryApprove(SandboxRole.Red, redDraft, out _), "空优先级被拒绝");
        redDraft.priority = "先确认桥东检查点";
        Check(source.TryApprove(SandboxRole.Red, redDraft, out _), "有效本方草稿可以采用");
        Check(!source.TryApprove(SandboxRole.Red, redDraft, out _), "同一建议不能重复采用");
        Check(source.GetProjection(SandboxRole.Host).records.All(record => !record.isPrivatePlan), "主持人时间线不泄露私有方案");
        Check(!source.TryAdvance(SandboxRole.Host, out _), "仅一方确认仍阻塞");
        SandboxPlanData blueDraft = source.GetProjection(SandboxRole.Blue).ownPlan;
        Check(source.TryReject(SandboxRole.Blue, blueDraft.version, out _), "本方可以驳回建议");
        Check(!source.TryAdvance(SandboxRole.Host, out _), "驳回后仍阻塞推进");
        Check(source.TryReloadDemoPlan(SandboxRole.Blue, out _), "可以显式载入新的固定演示建议");
        Check(!source.TryApprove(SandboxRole.Blue, blueDraft, out _), "旧版本草稿不能覆盖新建议");
        blueDraft = source.GetProjection(SandboxRole.Blue).ownPlan;
        Check(source.TryApprove(SandboxRole.Blue, blueDraft, out _), "新版本建议可采用");
        int oldStep = source.Current.step;
        Check(source.TryAdvance(SandboxRole.Host, out _) && source.Current.step == oldStep + 1, "双方确认后恰好推进一步");

        int previewRevision = source.Current.revision;
        Check(source.TryRegisterEvent(SandboxRole.Host, "通信中断", "R-U01", previewRevision, out _), "有效事件预览可登记");
        Check(!source.TryRegisterEvent(SandboxRole.Host, "通信中断", "R-U01", previewRevision, out _), "同一预览不能重复登记");
        Check(!source.TryRegisterEvent(SandboxRole.Red, "天气变化", "R-U01", source.Current.revision, out _), "操作员不能注入事件");
        Check(!source.TryRegisterEvent(SandboxRole.Host, "不存在的事件", "R-U01", source.Current.revision, out _), "未知事件被拒绝");
        Check(!source.TryRegisterEvent(SandboxRole.Host, "设备故障", "unknown", source.Current.revision, out _), "不存在的目标被拒绝");
        Check(source.GetProjection(SandboxRole.Blue).records.All(record => record.title != "通信中断已登记"), "非公开红方事件不投影给蓝方");

        Check(DemoSandboxViewSource.ValidateSession(source.Current) == string.Empty, "完整前端状态通过校验");
        SandboxSessionData invalid = source.Current;
        invalid.units[0].x = float.NaN;
        Check(DemoSandboxViewSource.ValidateSession(invalid).Length > 0, "损坏坐标被拒绝");
        var restoredSource = new DemoSandboxViewSource();
        SandboxSessionData valid = restoredSource.Current;
        valid.step = 60;
        Check(restoredSource.TryRestore(valid, out _), "可以显式恢复合法旧状态");
        valid.units[0].x = 999;
        Check(restoredSource.Current.units[0].x != 999, "恢复后与原存档引用隔离");
        Check(!restoredSource.TryAdvance(SandboxRole.Host, out _), "第六十步不能继续推进");
        valid = new DemoSandboxViewSource().Current;
        valid.schemaVersion = 99;
        Check(!restoredSource.TryRestore(valid, out _), "不兼容的存档版本被拒绝");
        valid = new DemoSandboxViewSource().Current;
        valid.units[1].id = valid.units[0].id;
        Check(DemoSandboxViewSource.ValidateSession(valid).Length > 0, "重复单位身份被拒绝");
        valid = new DemoSandboxViewSource().Current;
        valid.units[0].side = SandboxRole.Blue;
        Check(DemoSandboxViewSource.ValidateSession(valid).Length > 0, "篡改单位归属被拒绝");
        valid = new DemoSandboxViewSource().Current;
        valid.records[0].step = 61;
        Check(DemoSandboxViewSource.ValidateSession(valid).Length > 0, "超出当前步的时间线被拒绝");
        Console.WriteLine("前端状态检查通过：" + checks + " 项。此结果不代表 Unity 运行或画面验收。");
        return 0;
    }

    /// <summary>核对一项独立预期，失败时返回明确位置。</summary>
    /// <param name="condition">实际结果是否符合预期。</param>
    /// <param name="description">本项预期。</param>
    private static void Check(bool condition, string description)
    {
        if (!condition) throw new InvalidOperationException("检查失败：" + description);
        checks++;
    }
}
