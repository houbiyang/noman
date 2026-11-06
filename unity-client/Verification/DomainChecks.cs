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
        CheckArmy();
        CheckDeleteArmy();
        CheckBarracks();
        CheckBarracksManagement();
        Console.WriteLine("前端状态检查通过：" + checks + " 项。此结果不代表 Unity 运行或画面验收。");
        return 0;
    }

    private static void CheckBarracksManagement()
    {
        var source = new DemoSandboxViewSource();
        var all = source.GetProjection(SandboxRole.Host).units;
        Check(Math.Abs(SandboxArmyLayout.ModelScale - 1.5f) < .001f, "兵营统一放大一档至1.5倍");
        foreach (SandboxRole side in new[] { SandboxRole.Red, SandboxRole.Blue })
            foreach (SandboxUnitType type in Enum.GetValues(typeof(SandboxUnitType)))
                Check(SandboxArmyLayout.UnitsForBarracks(all, side, type).Length == 1, "兵营只管理同阵营同兵种，兼容未关联初始单位");
        Check(SandboxArmyLayout.UnitsForBarracks(source.GetProjection(SandboxRole.Red).units, SandboxRole.Blue, SandboxUnitType.Recon).Length == 0, "兵营管理不扩大角色投影可见范围");
        Check(all.All(unit => unit.maxHealth == 0 && unit.health == 0), "旧演示单位血量默认未知，不推断满血");
        var saved = source.Current;
        saved.units[0].health = 65; saved.units[0].maxHealth = 100;
        Check(source.TryRestore(saved, out _), "接受有效的已上报血量");
        var units = source.GetProjection(SandboxRole.Red).units;
        Check(units[0].health == 65 && units[0].maxHealth == 100, "血量透传到本方投影");
        units[0].health = 1;
        Check(source.Current.units[0].health == 65, "血量投影保持引用隔离");
        var restored = new DemoSandboxViewSource();
        Check(restored.TryRestore(source.Current, out _) && restored.Current.units[0].health == 65, "血量可以保存恢复");
        saved.units[0].health = 101;
        Check(DemoSandboxViewSource.ValidateSession(saved).Length > 0, "拒绝超出上限的血量");
        saved.units[0].health = -1;
        Check(DemoSandboxViewSource.ValidateSession(saved).Length > 0, "拒绝负血量");
        saved.units[0].health = 1; saved.units[0].maxHealth = 0;
        Check(DemoSandboxViewSource.ValidateSession(saved).Length > 0, "未知上限不接受虚构有效血量");
        saved.units[0].health = 0; saved.units[0].maxHealth = 100;
        Check(DemoSandboxViewSource.ValidateSession(saved) == "", "明确零血量与未知血量可区分");
        source.TryDeleteArmyUnit(SandboxRole.Red, "R-U01", out _);
        Check(SandboxArmyLayout.UnitsForBarracks(source.GetProjection(SandboxRole.Red).units, SandboxRole.Red, SandboxUnitType.Recon).Length == 0, "删除后兵种数量立即归零");
        Check(SandboxArmyLayout.UnitsForBarracks(source.GetProjection(SandboxRole.Host).units, SandboxRole.Blue, SandboxUnitType.Recon).Length == 1, "删除不影响另一阵营同兵种");
        Check(SandboxArmyLayout.Overlaps(20, 20, 20.3f, 20), "放大模型的三十米间距仍被占地检测拒绝");
    }

    private static void CheckBarracks()
    {
        var source = new DemoSandboxViewSource();
        string message;
        Check(!source.TryPlaceBarracks(SandboxRole.Red, SandboxRole.Blue, SandboxUnitType.Recon, 20, 20, out message), "不能建造敌方兵营");
        Check(!source.TrySpawnArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 1, out message), "没有兵营不能生成单位");
        foreach (float x in new[] { float.NaN, float.PositiveInfinity, -1, .05f, 119.99f })
            Check(!source.TryPlaceBarracks(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, x, 20, out message), "兵营拒绝无效坐标");
        Check(!source.TryPlaceBarracks(SandboxRole.Red, SandboxRole.Red, (SandboxUnitType)99, 20, 20, out message), "兵营拒绝无效兵种");
        int revision = source.Current.revision;
        Check(source.TryPlaceBarracks(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 20, 20, out message), "只建造机库");
        var state = source.Current;
        Check(state.revision == revision + 1 && state.barracks.Length == 1 && state.units.Length == 6, "建造只更新兵营不生产单位");
        Check(source.TrySpawnArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 2, out message), "内部仿真出兵入口");
        state = source.Current;
        var spawned = state.units.Where(unit => !string.IsNullOrEmpty(unit.barracksId)).ToArray();
        Check(spawned.Length == 2 && spawned.All(unit => unit.barracksId == state.barracks[0].id && Math.Abs((unit.z - 20) * 150 - SandboxArmyLayout.RallyStart) < .001f), "新增单位归属正确兵营及出口集结区");
        Check(Math.Abs((spawned[1].x - spawned[0].x) * 100 - 6) < .001f, "新地图中相邻出兵位置间隔六米");
        Check(!source.TryPlaceBarracks(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 20, 79.95f, out message), "集结区不能超出十二公里边界");
        var nearby = new DemoSandboxViewSource();
        Check(nearby.TryPlaceBarracks(SandboxRole.Host, SandboxRole.Red, SandboxUnitType.Recon, 20, 20, out message)
            && nearby.TryPlaceBarracks(SandboxRole.Host, SandboxRole.Blue, SandboxUnitType.Ground, 21, 21, out message), "相距一百米的兵营不再被旧业务坐标间距误拒绝");
        Check(DemoSandboxViewSource.ValidateSession(nearby.Current) == "", "米制兵营间距可以保存恢复");
        Check(state.units.First(unit => unit.id == "R-U01").x == 25, "建造保留原有单位");
        Check(source.GetProjection(SandboxRole.Blue).barracks.Length == 0, "蓝方不能看到红方兵营");
        Check(source.GetProjection(SandboxRole.Host).barracks.Length == 1, "主持人看到兵营");
        state.barracks[0].x = 999;
        var projection = source.GetProjection(SandboxRole.Red); projection.barracks[0].z = 999;
        Check(source.Current.barracks[0].x == 20 && source.Current.barracks[0].z == 20, "兵营副本引用隔离");
        Check(!source.TryPlaceBarracks(SandboxRole.Host, SandboxRole.Blue, SandboxUnitType.Ground, 20.1f, 20.1f, out message), "兵营不能重叠");
        Check(source.Current.barracks.Length == 1 && source.Current.units.Length == 8, "非法兵营没有部分副作用");
        Check(source.TrySpawnArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 2, out message), "从已有机库补充单位");
        Check(source.ArmyCapacityRemaining == 0 && !source.TrySpawnArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 1, out message), "兵营出兵遵守全局十单位上限");
        Check(source.TryPlaceBarracks(SandboxRole.Host, SandboxRole.Blue, SandboxUnitType.Support, 90, 30, out message), "满员时仍可放置敌方保障兵营");
        Check(source.Current.units.Length == 10 && source.Current.barracks.Length == 2, "兵营不占单位数量且不会超额生成");
        var before = source.Current.units.Select(unit => unit.x + ":" + unit.z).ToArray();
        Check(source.TryPlaceBarracks(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 35, 20, out message), "同兵种重新选址");
        Check(source.Current.barracks.Length == 2 && before.SequenceEqual(source.Current.units.Select(unit => unit.x + ":" + unit.z)), "重新选址不复制兵营也不移动已有单位");
        Check(source.TryDeleteArmyUnit(SandboxRole.Red, "R-U02", out message), "删除兵营生成的单位");
        Check(source.TrySpawnArmy(SandboxRole.Host, SandboxRole.Blue, SandboxUnitType.Support, 1, out message), "删除释放的名额可用于对方兵营");
        Check(source.Current.units.Last().barracksId.StartsWith("B-") && Math.Abs((source.Current.units.Last().z - 30) * 150 - SandboxArmyLayout.RallyStart) < .001f, "新单位来自本兵种新兵营");
        var saved = source.Current;
        Check(DemoSandboxViewSource.ValidateSession(saved) == "", "兵营存档有效");
        var restored = new DemoSandboxViewSource();
        Check(restored.TryRestore(saved, out message) && restored.Current.barracks.Length == 2, "兵营与单位归属保存恢复");
        saved.barracks[0].x = float.NaN;
        Check(!restored.TryRestore(saved, out message), "篡改兵营坐标被拒绝");
        saved = source.Current; saved.units.Last().barracksId = saved.barracks.First(camp => camp.side == SandboxRole.Red).id;
        Check(DemoSandboxViewSource.ValidateSession(saved).Length > 0, "拒绝跨阵营兵营归属");
        var legacy = new DemoSandboxViewSource().Current; legacy.barracks = null;
        Check(restored.TryRestore(legacy, out message) && restored.Current.barracks.Length == 0, "兼容没有兵营字段的旧存档");
    }

    private static void CheckArmy()
    {
        var source = new DemoSandboxViewSource();
        Check(source.Current.units.Length == 6 && source.ArmyCapacityRemaining == 4, "初始双方六单位计入共享十单位上限");
        var original = source.Current;
        int revision = source.Current.revision;
        Check(!source.TryConfigureArmy(SandboxRole.Red, SandboxRole.Blue, new[] { 1, 1, 1 }, out _), "不能集结部署对方军队");
        foreach (int count in new[] { 0, 11, -1 })
            Check(!source.TryConfigureArmy(SandboxRole.Red, SandboxRole.Red, new[] { count, 1, 1 }, out _), "拒绝集结部署越界数量 " + count);
        Check(!source.TryConfigureArmy(SandboxRole.Red, SandboxRole.Red, null, out _), "拒绝缺少数量");
        Check(!source.TryConfigureArmy(SandboxRole.Red, SandboxRole.Red, new[] { 1, 1 }, out _), "拒绝不完整数量");
        Check(!source.TryConfigureArmy(SandboxRole.Red, SandboxRole.Red, new[] { 2, 2, 2 }, out _), "集结区部署遵守共享名额");
        Check(source.Current.revision == revision && source.Current.units.Length == 6, "失败集结部署没有部分修改");
        Check(!source.TryDeployArmy(SandboxRole.Red, SandboxRole.Blue, SandboxUnitType.Recon, 1, 40, 40, out _), "拒绝对方部署");
        Check(!source.TryDeployArmy((SandboxRole)99, SandboxRole.Red, SandboxUnitType.Recon, 1, 40, 40, out _), "拒绝未知操作者");
        Check(!source.TryDeployArmy(SandboxRole.Host, SandboxRole.Host, SandboxUnitType.Recon, 1, 40, 40, out _), "主持人不是部署阵营");
        Check(!source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, (SandboxUnitType)99, 1, 40, 40, out _), "拒绝未知模型类型");
        foreach (int count in new[] { 0, 11, -1 })
            Check(!source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, count, 40, 40, out _), "拒绝选点部署越界数量 " + count);
        Check(!source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 4, 0, 79, out _), "整队越界部署被拒绝");
        Check(!source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 1, float.NaN, 40, out _), "拒绝非数部署位置");
        Check(!source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 1, 40, float.PositiveInfinity, out _), "拒绝无穷部署位置");
        Check(source.Current.revision == revision && source.Current.units.Length == 6, "无效部署不修改地图或修订");
        Check(source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 2, 40, 40, out _), "首次追加两个地面单位");
        Check(source.Current.units.Length == 8 && source.ArmyCapacityRemaining == 2, "首次部署后共享名额剩余二");
        Check(source.Current.units.Count(u => u.side == SandboxRole.Red && u.type == SandboxUnitType.Ground) == 3, "原有同类型单位保留");
        Check(original.units.All(old => source.Current.units.Any(u => u.id == old.id && u.x == old.x && u.z == old.z && u.status == old.status)), "原有双方模型身份位置状态不变");
        Check(source.Current.units.Where(u => u.id == "R-G02" || u.id == "R-G03").Select(u => u.x + "," + u.z).Distinct().Count() == 2, "新增编队使用独立位置");
        Check(source.TryMoveArmyUnit(SandboxRole.Red, "R-G02", 60, 30, out _), "新模型可设置移动目标");
        var beforeSecond = source.Current;
        Check(source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 1, 70, 50, out _), "再次部署追加同类型模型");
        Check(source.Current.units.Length == 9 && source.Current.units.Any(u => u.id == "R-G04"), "再次部署使用新编号");
        Check(beforeSecond.units.All(old => source.Current.units.Any(u => u.id == old.id && u.x == old.x && u.z == old.z && u.status == old.status)), "再次部署保留所有旧模型和移动目标");
        Check(source.GetProjection(SandboxRole.Blue).units.Length == 3, "红方追加不改变蓝方编制");
        Check(source.GetProjection(SandboxRole.Host).units.Length == 9, "主持人看到红蓝总数");
        Check(source.TryDeployArmy(SandboxRole.Blue, SandboxRole.Blue, SandboxUnitType.Support, 1, 90, 40, out _), "蓝方使用最后一个共享名额");
        Check(source.Current.units.Length == 10 && source.ArmyCapacityRemaining == 0, "双方合计恰好十单位");
        Check(source.Current.units.Select(u => u.id).Distinct().Count() == 10, "十个模型身份唯一");
        revision = source.Current.revision;
        Check(!source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 1, 40, 40, out string fullMessage) && fullMessage.Contains("10"), "满额拒绝红方部署并提示上限");
        Check(!source.TryDeployArmy(SandboxRole.Host, SandboxRole.Blue, SandboxUnitType.Support, 1, 90, 40, out _), "主持人同样不能超额部署");
        Check(!source.TryConfigureArmy(SandboxRole.Host, SandboxRole.Red, new[] { 1, 1, 1 }, out _), "满额不能通过集结区绕过上限");
        Check(source.Current.revision == revision && source.Current.units.Length == 10, "满额拒绝不删除任何模型");
        Check(!source.TryMoveArmyUnit(SandboxRole.Blue, "R-G02", 30, 30, out _), "不能移动对方模型");
        Check(!source.TryMoveArmyUnit(SandboxRole.Red, "R-G02", float.NaN, 30, out _), "拒绝非数移动目标");
        Check(!source.TryMoveArmyUnit(SandboxRole.Red, "R-G02", 121, 30, out _), "拒绝地图外移动目标");
        Check(source.TryMoveArmyUnit(SandboxRole.Red, "R-G02", 119.9f, 30, out _), "满额仍可以移动已有模型");
        Check(DemoSandboxViewSource.ValidateSession(source.Current) == "", "十单位快照通过校验");
        var restored = new DemoSandboxViewSource();
        Check(restored.TryRestore(source.Current, out _), "可恢复包含追加单位的快照");
        Check(restored.Current.units.Length == 10 && restored.ArmyCapacityRemaining == 0, "恢复保留十个模型与共享名额");
        Check(restored.Current.units.First(u => u.id == "R-G02").x == 119.9f, "恢复保留新增模型的目标");
        var invalid = source.Current;
        var extra = invalid.units[0].Copy(); extra.id = "R-U02";
        invalid.units = invalid.units.Concat(new[] { extra }).ToArray();
        Check(DemoSandboxViewSource.ValidateSession(invalid).Contains("10"), "拒绝旧版超十单位快照");
        revision = restored.Current.revision;
        Check(!restored.TryRestore(invalid, out _), "超额存档不能绕过部署上限");
        Check(restored.Current.units.Length == 10 && restored.Current.revision == revision, "超额恢复失败保留当前地图");
        invalid = source.Current; invalid.units[0].id = "R-U11";
        Check(DemoSandboxViewSource.ValidateSession(invalid) != "", "拒绝十一号身份");
        var camp = new DemoSandboxViewSource(); var campOriginal = camp.Current;
        Check(camp.TryConfigureArmy(SandboxRole.Host, SandboxRole.Blue, new[] { 1, 1, 1 }, out _), "主持人可在蓝方集结区追加三模型");
        Check(camp.Current.units.Length == 9 && camp.ArmyCapacityRemaining == 1, "集结区部署按批次新增");
        Check(campOriginal.units.All(old => camp.Current.units.Any(u => u.id == old.id && u.x == old.x && u.z == old.z)), "集结区部署保留全部旧模型");
        Check(!camp.TryConfigureArmy(SandboxRole.Host, SandboxRole.Blue, new[] { 1, 1, 1 }, out _), "剩余一名额时拒绝整批三单位且不部分部署");
        Check(camp.Current.units.Length == 9, "失败整批部署保持九模型");
        Check(camp.TryDeployArmy(SandboxRole.Host, SandboxRole.Blue, SandboxUnitType.Recon, 1, 90, 40, out _), "剩余名额仍可用于单类型部署");
        var overflow = new DemoSandboxViewSource(); revision = overflow.Current.revision;
        Check(!overflow.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 5, 40, 40, out _), "单次请求大于剩余名额整批拒绝");
        Check(overflow.Current.units.Length == 6 && overflow.Current.revision == revision, "超额请求不会缩减数量或自动删除旧模型");
        Check(overflow.TryDeployArmy(SandboxRole.Host, SandboxRole.Red, SandboxUnitType.Recon, 4, 40, 40, out _), "一次可用完剩余四名额");
        Check(overflow.Current.units.Count(u => u.side == SandboxRole.Red && u.type == SandboxUnitType.Recon) == 5, "追加四架后保留初始侦察机");
    }

    private static void CheckDeleteArmy()
    {
        var source = new DemoSandboxViewSource();
        Check(source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 4, 40, 40, out _), "删除检查先填满地图");
        Check(source.TryMoveArmyUnit(SandboxRole.Red, "R-U02", 55, 45, out _), "删除检查包含移动中的单位");
        var before = source.Current;
        Check(!source.TryDeleteArmyUnit(SandboxRole.Blue, "R-U02", out _), "蓝方不能删除红方单位");
        Check(!source.TryDeleteArmyUnit(SandboxRole.Red, "B-S01", out _), "红方不能删除蓝方单位");
        Check(!source.TryDeleteArmyUnit((SandboxRole)99, "R-U02", out _), "未知角色不能删除");
        foreach (string id in new[] { null, "", "missing" })
            Check(!source.TryDeleteArmyUnit(SandboxRole.Host, id, out _), "无效单位删除被拒绝");
        Check(source.Current.revision == before.revision && source.Current.records.Count == before.records.Count && source.Current.units.Length == 10, "拒绝删除不改变任何状态");
        Check(source.TryDeleteArmyUnit(SandboxRole.Red, "R-U02", out string message), "可以删除本方移动中的新增单位");
        Check(source.Current.units.Length == 9 && source.ArmyCapacityRemaining == 1 && message.Contains("剩余 1"), "删除立即释放共享名额");
        Check(!source.Current.units.Any(u => u.id == "R-U02"), "删除移除指定身份");
        Check(before.units.Where(u => u.id != "R-U02").All(old => source.Current.units.Any(u => u.id == old.id && u.x == old.x && u.z == old.z && u.status == old.status)), "删除不修改其他单位");
        Check(source.Current.revision == before.revision + 1 && source.Current.records.Count == before.records.Count + 1 && source.Current.records.Last().side == SandboxRole.Red, "成功删除产生本方时间线和修订");
        Check(!source.GetProjection(SandboxRole.Blue).records.Any(r => r.title == "已删除部署单位"), "本方删除记录不泄露给对方");
        int revision = source.Current.revision;
        Check(!source.TryDeleteArmyUnit(SandboxRole.Red, "R-U02", out _) && source.Current.revision == revision && source.ArmyCapacityRemaining == 1, "重复删除不重复释放名额");
        Check(!source.TryMoveArmyUnit(SandboxRole.Red, "R-U02", 20, 20, out _), "已删除单位不能继续移动");
        Check(source.TryDeployArmy(SandboxRole.Blue, SandboxRole.Blue, SandboxUnitType.Support, 1, 90, 40, out _) && source.Current.units.Length == 10, "蓝方可使用红方删除释放的名额");
        Check(!source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 1, 40, 40, out _), "重新满额后仍限制为十个");
        Check(source.TryDeleteArmyUnit(SandboxRole.Blue, "B-S01", out _), "可以删除初始单位");
        Check(source.TryDeleteArmyUnit(SandboxRole.Blue, "B-S02", out _), "可以删除本方某类型的最后一个单位");
        Check(DemoSandboxViewSource.ValidateSession(source.Current) == "", "缺少某类型单位的快照仍有效");
        var restored = new DemoSandboxViewSource();
        Check(restored.TryRestore(source.Current, out _) && !restored.Current.units.Any(u => u.id == "R-U02" || u.type == SandboxUnitType.Support && u.side == SandboxRole.Blue), "恢复后被删除单位不会复活");
        foreach (var unit in source.Current.units)
            Check(source.TryDeleteArmyUnit(SandboxRole.Host, unit.id, out _), "主持人可删除双方单位 " + unit.id);
        Check(source.Current.units.Length == 0 && source.ArmyCapacityRemaining == 10, "全部删除后恢复十个名额");
        Check(source.GetProjection(SandboxRole.Red).units.Length == 0 && source.GetProjection(SandboxRole.Host).units.Length == 0, "空地图投影有效");
        Check(DemoSandboxViewSource.ValidateSession(source.Current) == "" && restored.TryRestore(source.Current, out _), "空地图可以保存恢复");
        var invalid = source.Current; invalid.units = null;
        Check(DemoSandboxViewSource.ValidateSession(invalid) != "", "缺失单位数组仍被拒绝");
        Check(source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 10, 40, 40, out _), "清空地图后可重新部署十个同类单位");
        Check(source.Current.units.Length == 10 && source.Current.units.Select(u => u.id).Distinct().Count() == 10 && DemoSandboxViewSource.ValidateSession(source.Current) == "", "重新部署身份唯一且快照有效");
        Check(source.TryDeleteArmyUnit(SandboxRole.Host, "R-G05", out _) && source.TryDeployArmy(SandboxRole.Host, SandboxRole.Red, SandboxUnitType.Ground, 1, 50, 40, out _), "删除后可以重新部署并复用空闲编号");
        Check(source.Current.units.Single(u => u.id == "R-G05").x == 50 && source.ArmyCapacityRemaining == 0, "复用编号使用新部署位置且总数正确");
        Check(restored.Current.units.Length == 0 && restored.TryConfigureArmy(SandboxRole.Host, SandboxRole.Blue, new[] { 1, 1, 1 }, out _), "空地图恢复后仍可通过集结区部署");
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
