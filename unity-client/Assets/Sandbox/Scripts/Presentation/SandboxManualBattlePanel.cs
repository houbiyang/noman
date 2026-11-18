using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>总册手动模式专用UI：只编辑意图，不使用unit_order或旧自动推进接口。</summary>
    public sealed partial class SandboxHudController
    {
        private VisualElement manualBattleBar, manualBattleOverlay;
        private string manualBattleScope, pendingBattleAreaId, pendingBattleAreaScope, pendingBattleAreaLife;
        private int pendingBattleAreaRadius;
        private SandboxServerContext pendingBattleAreaBasis, pendingManualMoveBasis;
        private string pendingManualMoveLife, pendingManualMoveScope;
        private SandboxManualBattleView ManualBattle => serverMode && !deploymentStaging ? serverClient?.View?.manual_battle : null;
        private string ManualScope => ManualBattle == null ? null : serverClient.ConnectionGeneration + ":"
            + serverClient.View.context?.game_id + ":" + serverClient.View.context?.branch_id + ":"
            + serverClient.View.role + ":" + ManualBattle.round_number + ":" + ManualBattle.stage;
        private bool ManualWritable => recordedOverlay == null && serverClient != null && serverClient.Healthy && !serverClient.Busy && !serverClient.HasPending;
        private bool ManualEditable => ManualWritable && ManualBattle?.can_edit == true && role != SandboxRole.Host;

        private void OpenManualScopeDetails()
        {
            var battle = ManualBattle; if (battle == null || recordedOverlay != null) return;
            var card = ManualCard("本地手动测试范围 · 非竞技");
            RenderManualReleaseScope(card, battle.release_scope, battle.competitive_ready, battle.limitations);
            AddButton(card, "收起测试范围", CloseManualBattleOverlay, "army-unit-action");
        }
        private void CloseManualBattleOverlay()
        {
            manualBattleOverlay?.RemoveFromHierarchy(); manualBattleOverlay = null;
        }
        private void RefreshManualBattleBar()
        {
            if (manualBattleScope != ManualScope)
            {
                if (manualBattleScope != null) CancelArmyCommand();
                CloseManualBattleOverlay(); manualBattleScope = ManualScope;
            }
            var battle = ManualBattle;
            if (battle == null) { manualBattleBar?.RemoveFromHierarchy(); manualBattleBar = null; return; }
            if (manualBattleBar == null)
            {
                manualBattleBar = new VisualElement { name = "manual-battle-bar" };
                manualBattleBar.AddToClassList("manual-battle-bar"); manualBattleBar.AddToClassList("ui-blocker");
                var sheet = Resources.Load<StyleSheet>("Sandbox/SandboxManualBattle");
                if (sheet != null) root.styleSheets.Add(sheet);
                root.Add(manualBattleBar);
            }
            manualBattleBar.style.display = recordedOverlay != null ? DisplayStyle.None : DisplayStyle.Flex;
            manualBattleBar.Clear();
            string title = battle.release_scope == "local_manual_test" ? "本地手动测试" : "手动战斗";
            AddText(manualBattleBar, title + (battle.stage == "finished" ? " · 已结束" : " · 第 " + battle.round_number + " / 60 轮 · 60秒"), "army-unit-title");
            if (battle.release_scope == "local_manual_test")
            {
                AddText(manualBattleBar, "仅已验证区域；未知区域/未接通桥头不可用", "army-footnote");
                AddButton(manualBattleBar, "查看测试范围", OpenManualScopeDetails, "army-unit-action").name = "manualScopeDetails";
            }
            if (serverClient.Busy || serverClient.HasPending)
            {
                AddText(manualBattleBar, serverClient.HasPending
                    ? "等待原命令确认 · 请勿重复提交；结算耗时不等于仿真秒数"
                    : "正在同步 · 请等待权威状态返回", "army-unit-title");
                AddText(manualBattleBar, serverClient.Status + " · 如连接中断，请在连接面板核对原命令，不新建重复命令。", "army-footnote");
            }
            AddText(manualBattleBar, "已结算 " + battle.completed_rounds + " 轮 · " + battle.elapsed_seconds + " 仿真秒 · 每轮60秒", "army-footnote");
            if (!string.IsNullOrEmpty(battle.blue_score) && !string.IsNullOrEmpty(battle.red_score))
                AddText(manualBattleBar, "蓝方 " + SandboxRoundReplayCursor.DisplayNumber(battle.blue_score).ToString("0.##")
                    + " 分 / 红方 " + SandboxRoundReplayCursor.DisplayNumber(battle.red_score).ToString("0.##") + " 分"
                    + (string.IsNullOrEmpty(battle.winner) ? "" : " · " + (battle.winner == "draw" ? "平局" : battle.winner == "blue" ? "蓝方获胜" : "红方获胜")), "army-footnote");
            else
                AddText(manualBattleBar, "本方预估 " + ManualScoreText(role == SandboxRole.Blue ? battle.blue_score : battle.red_score)
                    + " 分 · 公开占点：蓝 " + ManualScoreText(battle.blue_control_score) + " / 红 " + ManualScoreText(battle.red_control_score)
                    + " · 对手总分终局后公开", "army-footnote");
            var actions = new VisualElement(); actions.AddToClassList("selected-unit-actions"); manualBattleBar.Add(actions);
            if (role == SandboxRole.Host)
            {
                AddText(manualBattleBar, "蓝方 " + (battle.submitted?.blue == true ? "已锁定" : "待提交")
                    + "  /  红方 " + (battle.submitted?.red == true ? "已锁定" : "待提交") + " · 主持人不能代发双方指令", "army-footnote");
                AddButton(actions, "统一结算本轮", OpenManualRoundConfirmation, "army-unit-action")
                    .SetEnabled(ManualWritable && battle.can_resolve && battle.engine_ready);
                AddButton(actions, "在地图播放本轮", OpenRecordedReplay, "army-unit-action")
                    .SetEnabled(ManualWritable && battle.completed_rounds > 0);
            }
            else
            {
                AddText(manualBattleBar, "本方草稿 " + (battle.own_drafts?.Length ?? 0) + " 个单位 · "
                    + (battle.can_edit ? "点击单位编排，未修改通道沿用上轮任务" : "本方已锁定或战斗结束，不能修改"), "army-footnote");
                AddButton(actions, "查看本方草稿", OpenManualDraftSummary, "army-unit-action");
                AddButton(actions, "锁定本方本轮指令", OpenManualRoundConfirmation, "army-unit-action")
                    .SetEnabled(ManualWritable && battle.can_submit && battle.engine_ready);
            }
            if (!battle.engine_ready) AddText(manualBattleBar, "执行器 / 真实地图尚未验收：可以编排草稿，暂不可锁定开战，不会调用旧版裁决。", "army-footnote");
        }
        private VisualElement ManualCard(string title)
        {
            CloseManualBattleOverlay(); CancelArmyCommand();
            manualBattleOverlay = new VisualElement { name = "manual-battle-overlay" };
            manualBattleOverlay.AddToClassList("modal-backdrop"); manualBattleOverlay.AddToClassList("ui-blocker"); root.Add(manualBattleOverlay);
            var card = new ScrollView { name = "manual-battle-card" }; card.AddToClassList("modal-card"); card.AddToClassList("manual-battle-card");
            manualBattleOverlay.Add(card); AddText(card, title, "drawer-title"); return card;
        }
        private static string ManualTaskName(string action)
        {
            switch (action)
            {
                case "attack": return "攻击目标"; case "attack_area": return "范围攻击"; case "recon": return "侦察";
                case "resupply": return "补充"; case "repair": return "修复"; case "barracks": return "兵营服务";
                case "takeoff": return "起飞"; case "land": return "降落"; case "stop": return "取消任务"; default: return "保持";
            }
        }
        private static string ManualDraftText(SandboxBattleIntentView item)
        {
            string move = item.movement == null || string.IsNullOrEmpty(item.movement.action) ? "移动沿用" : item.movement.action == "stop" ? "停止移动"
                : "移动至 (" + item.movement.position?.x + ", " + item.movement.position?.y + ")";
            var task = item.task;
            string detail = task == null || string.IsNullOrEmpty(task.action) ? "任务沿用" : ManualTaskName(task.action)
                + (string.IsNullOrEmpty(task.target_id) ? "" : " → " + task.target_id)
                + (string.IsNullOrEmpty(task.camp_id) ? "" : " → " + task.camp_id)
                + (string.IsNullOrEmpty(task.resource) ? "" : " · " + (task.resource == "ammo" ? "弹药" : "能源"))
                + (string.IsNullOrEmpty(task.quantity) ? "" : " · 数量 " + task.quantity)
                + (string.IsNullOrEmpty(task.target_balance) ? "" : " · 目标余额 " + task.target_balance)
                + (task.position == null || string.IsNullOrEmpty(task.position.x) || string.IsNullOrEmpty(task.position.y) ? "" : " · 位置 (" + task.position.x + ", " + task.position.y + ")")
                + (task.radius_m == 0 ? "" : " · 半径 " + task.radius_m + " 米");
            return item.unit_id + "  /  " + move + "  /  " + detail;
        }
        private void ManualDraftList(VisualElement card)
        {
            var drafts = ManualBattle?.own_drafts ?? Array.Empty<SandboxBattleIntentView>();
            if (drafts.Length == 0) AddText(card, "本轮无修改：持续任务沿用，其他单位待命。不会自动选敌、追击或补给。", "body-text");
            foreach (var item in drafts) AddText(card, ManualDraftText(item), "body-text");
        }
        private void OpenManualDraftSummary()
        {
            if (ManualBattle == null || role == SandboxRole.Host) return;
            var card = ManualCard("本方本轮指令草稿"); ManualDraftList(card);
            AddText(card, "移动和任务为独立通道。要撤销请选中该单位，使用“停止并取消任务”，或分别停止移动 / 取消任务。锁定后本轮不可修改。", "body-text");
            AddButton(card, "返回", CloseManualBattleOverlay);
        }
        private void OpenManualRoundConfirmation()
        {
            var battle = ManualBattle;
            if (battle == null || !ManualWritable || !battle.engine_ready) return;
            bool host = role == SandboxRole.Host;
            if (host ? !battle.can_resolve : !battle.can_submit) return;
            string scope = ManualScope; var basis = serverClient.View.context;
            var card = ManualCard(host ? "主持人统一结算本轮" : "确认锁定本方指令");
            if (host) AddText(card, "双方已锁定。提交后后端统一执行60仿真秒（若提前终局则停止），原子保存资源与动画。结算完成后直接在当前沙盘地图播放本轮；可暂停、拖动或退出恢复最新战局。", "body-text");
            else { ManualDraftList(card); AddText(card, "仅锁定本方，不推进世界。随后切换另一方完成提交，最后由主持人统一结算。锁定前会检查本方持续任务与服务角色冲突。", "body-text"); }
            AddButton(card, host ? "确认结算并保存动画" : "确认锁定", () => {
                var current = ManualBattle;
                if (scope != ManualScope || !ManualWritable || current?.engine_ready != true
                    || (host ? !current.can_resolve : !current.can_submit)) { Notify("轮次、席位或同步状态已变化，请重新确认"); return; }
                CloseManualBattleOverlay();
                SendServerCommand(host ? "battle_resolve" : "battle_submit", new SandboxBattleRoundPayload { round_number = current.round_number }, basis);
            }, "primary-button");
            AddButton(card, "取消", CloseManualBattleOverlay);
        }
        private bool ManualUnitEditable(SandboxUnitData unit) => ManualEditable && unit.side == role && unit.health > 0;
        private void AddManualUnitControls(VisualElement card, SandboxUnitData unit)
        {
            if (role == SandboxRole.Host || unit.side != role)
            {
                AddText(card, role == SandboxRole.Host ? "主持人只读：请切换红方或蓝方编排各自指令。" : "敌方仅显示合法可见情报，不能下达指令。", "army-footnote"); return;
            }
            var fact = serverClient.View.units?.FirstOrDefault(u => u.unit_id == unit.id);
            if (fact != null)
                AddText(card, (fact.movement_active ? "持续移动任务未结束（不代表此刻有速度）" : "无持续移动任务")
                    + " · " + (string.IsNullOrEmpty(fact.interaction_action) ? "无持续任务" : ManualTaskName(fact.interaction_action))
                    + "\n本轮不修改的通道沿用；停止移动与取消任务是两条独立指令。", "army-footnote").name = "manualUnitContinuity";
            var draft = ManualBattle.own_drafts?.FirstOrDefault(i => i.unit_id == unit.id);
            AddText(card, draft == null ? "本轮未修改，沿用已有持续任务。" : ManualDraftText(draft), "army-footnote");
            var actions = new VisualElement(); actions.AddToClassList("selected-unit-actions"); card.Add(actions);
            bool edit = ManualUnitEditable(unit);
            AddButton(actions, "移动到…", () => BeginMove(unit.id), "army-unit-action").SetEnabled(edit);
            AddButton(actions, "坐标路线…", () => OpenManualCoordinates(unit.id), "army-unit-action").SetEnabled(edit);
            AddButton(actions, "停止移动", () => SubmitManualMovement(unit.id), "army-unit-action").SetEnabled(edit);
            AddButton(actions, "停止并取消任务", () => SubmitManualStopAll(unit.id), "army-unit-action").SetEnabled(edit);
            AddButton(actions, "取消任务", () => SubmitManualTask(unit.id, "stop"), "army-unit-action").SetEnabled(edit);
            if (unit.type == SandboxUnitType.Ground)
            {
                AddButton(actions, "攻击目标", () => OpenManualTarget(unit.id, "attack"), "army-unit-action").SetEnabled(edit);
                AddButton(actions, "范围攻击…", () => OpenManualArea(unit.id), "army-unit-action").SetEnabled(edit);
            }
            if (unit.type == SandboxUnitType.Recon)
            {
                AddButton(actions, "侦察目标", () => OpenManualTarget(unit.id, "recon"), "army-unit-action").SetEnabled(edit);
                AddButton(actions, "侦察位置…", () => BeginManualArea(unit.id, 0), "army-unit-action").SetEnabled(edit);
                AddButton(actions, "起飞", () => SubmitManualTask(unit.id, "takeoff"), "army-unit-action").SetEnabled(edit);
                AddButton(actions, "降落", () => SubmitManualTask(unit.id, "land"), "army-unit-action").SetEnabled(edit);
            }
            if (unit.type == SandboxUnitType.Support)
                AddButton(actions, "补充 / 修复单位", () => OpenManualTarget(unit.id, "service"), "army-unit-action").SetEnabled(edit);
            AddButton(actions, "兵营补充…", () => OpenManualCamp(unit.id), "army-unit-action").SetEnabled(edit);
            AddText(card, "指令仅暂存，双方锁定后由主持人结算。服务供受双方须先停止移动；起降同样不得带持续移动。", "army-footnote");
        }
        private SandboxServerUnit ManualUnit(string id)
        {
            var unit = serverClient?.View?.units?.FirstOrDefault(u => u.unit_id == id);
            if (!ManualEditable || unit == null || unit.side != serverClient.View.role || unit.durability <= 0) { Notify("本方已锁定、单位失效或同步未就绪"); return null; }
            return unit;
        }
        private void SubmitManualMovement(string id, SandboxServerPosition point = null, SandboxServerPosition[] waypoints = null, SandboxServerContext basis = null)
        {
            var unit = ManualUnit(id); if (unit == null) return;
            CancelArmyCommand();
            SendServerCommand("battle_draft", SandboxBattleDraftPayload.Movement(ManualBattle.round_number, unit, point, waypoints), basis);
        }
        private void SubmitManualTask(string id, string action)
        {
            var unit = ManualUnit(id); if (unit == null) return;
            CancelArmyCommand();
            SendServerCommand("battle_draft", SandboxBattleDraftPayload.Task(ManualBattle.round_number, unit, action));
        }
        private void SubmitManualStopAll(string id)
        {
            var unit = ManualUnit(id); if (unit == null) return;
            CancelArmyCommand();
            SendServerCommand("battle_draft", SandboxBattleDraftPayload.StopAll(ManualBattle.round_number, unit));
        }
        // Esc cancels only unsubmitted local input; it never modifies server drafts.
        private bool CancelManualInput()
        {
            if (manualBattleOverlay != null) { CloseManualBattleOverlay(); return true; }
            if (pendingBattleAreaId == null) return false;
            CancelArmyCommand(); return true;
        }
        private static bool IsUndockedRecon(SandboxServerUnit unit) => unit.unit_type == "recon" && !unit.docked;
        private bool AllowsManualHoverEnergy(SandboxServerUnit source)
            => source.unit_type == "support" && ManualBattle?.AllowsHoverEnergy == true;

        private void OpenManualCoordinates(string id)
        {
            var unit = ManualUnit(id); if (unit == null) return;
            string scope = ManualScope, life = unit.life_id; var basis = serverClient.View.context;
            var card = ManualCard("精确业务坐标路线 · " + id);
            AddText(card, "坐标系 unity-demo-120x80-v1：X 0～120、Y 0～80，均为业务坐标而非米。地图米制 X=业务X×100，Z=业务Y×150。每行 X,Y；也可用分号分隔。最多7个途经点，最后一个点为终点。", "body-text");
            var route = new TextField("有序坐标") { multiline = true, maxLength = 800 }; route.name = "manualRouteInput";
            route.style.minHeight = 100; card.Add(route);
            var preview = AddText(card, "仅暂存移动通道，不更改当前任务；不自动寻路。地表、占地与完整扫掠由后端验证，输入范围正确不代表路线合法。", "body-text"); preview.name = "manualRoutePreview";
            SandboxServerPosition[] ReadRoute()
            {
                try {
                    var points = SandboxManualRouteInput.Parse(route.value);
                    preview.text = "途经点 " + (points.Length - 1) + " 个 → 终点 (" + points.Last().x + ", " + points.Last().y + ")\n"
                        + string.Join(" → ", points.Select(p => "(" + p.x + ", " + p.y + ")"))
                        + "\n未寻路、未验证通行性；暂存后仍需双方锁定与主持人结算。";
                    return points;
                } catch (ArgumentException error) { preview.text = error.Message; return null; }
            }
            AddButton(card, "检查输入", () => ReadRoute(), "army-unit-action");
            AddButton(card, "暂存坐标路线", () => {
                var current = ManualUnit(id);
                if (current == null || scope != ManualScope || life != current.life_id) { preview.text = "状态已变化，请重新打开路线输入"; return; }
                var points = ReadRoute(); if (points == null) return;
                CloseManualBattleOverlay(); SubmitManualMovement(id, points.Last(), points.Take(points.Length - 1).ToArray(), basis);
            }, "primary-button");
            AddButton(card, "取消", CloseManualBattleOverlay);
        }
        private void OpenManualTarget(string id, string action)
        {
            var sourceUnit = ManualUnit(id); if (sourceUnit == null) return;
            bool service = action == "service";
            bool hoverEnergy = service && AllowsManualHoverEnergy(sourceUnit);
            var candidates = (service ? serverClient.View.units : serverClient.View.contacts ?? Array.Empty<SandboxServerUnit>())
                .Where(u => u.unit_id != id && u.durability > 0 && !string.IsNullOrEmpty(u.life_id)
                    && (service ? u.side == sourceUnit.side && (!IsUndockedRecon(u) || hoverEnergy) : u.side != sourceUnit.side))
                .OrderBy(u => u.unit_id).ToArray();
            string scope = ManualScope; string sourceLife = sourceUnit.life_id; var basis = serverClient.View.context;
            var card = ManualCard(service ? "补充 / 修复单位" : ManualTaskName(action));
            var target = new DropdownField("目标", candidates.Select(u => u.unit_id).DefaultIfEmpty("暂无合法目标").ToList(), 0); card.Add(target);
            var category = new DropdownField("类别", new List<string> { "能源", "弹药", "修复耐久" }, 0);
            var method = new DropdownField("额度", new List<string> { "累计补充数量", "补至目标余额" }, 0);
            var amount = new TextField("数值") { value = "10" };
            var summary = AddText(card, "", "body-text"); summary.name = "manualTargetSummary";
            var warning = AddText(card, "", "body-text"); warning.name = "manualTargetWarning";
            if (service)
            {
                card.Add(category); card.Add(method); card.Add(amount);
                void RefreshCategories(string selected)
                {
                    var receiver = candidates.FirstOrDefault(u => u.unit_id == selected);
                    bool flying = receiver != null && IsUndockedRecon(receiver);
                    category.choices = flying ? new List<string> { "能源" } : receiver?.unit_type == "ground"
                        ? new List<string> { "能源", "弹药", "修复耐久" } : new List<string> { "能源", "修复耐久" };
                    category.index = 0;
                }
                target.RegisterValueChangedCallback(e => RefreshCategories(e.newValue));
                RefreshCategories(target.value);
            }
            void RefreshTargetSummary()
            {
                var chosen = candidates.FirstOrDefault(u => u.unit_id == target.value);
                summary.text = chosen == null ? "当前没有合法可见的目标。" : chosen.unit_id + " · "
                    + (chosen.unit_type == "ground" ? "履带地面单位" : chosen.unit_type == "support" ? "保障车" : chosen.docked ? "停驻无人机" : "空中无人机")
                    + " · 耐久 " + chosen.durability.ToString("0.##", CultureInfo.InvariantCulture) + "/" + chosen.max_durability.ToString("0.##", CultureInfo.InvariantCulture)
                    + (service ? " · 能源 " + chosen.energy.ToString("0.##", CultureInfo.InvariantCulture) + " · 弹药 " + chosen.ammo.ToString("0.##", CultureInfo.InvariantCulture) : "")
                    + "\n仅显示当前席位获准情报；不会自动接近目标。";
                warning.text = "";
            }
            target.RegisterValueChangedCallback(_ => RefreshTargetSummary()); RefreshTargetSummary();
            AddText(card, service ? "不会自动移动接近目标。供受双方须停止移动，距离、地表和库存由每轮实际裁决；数量未完成会延续下轮。" : "只从本方当前合法可见情报选择，不自动追击；失去观察或射线不合法时不会开火。", "body-text");
            if (hoverEnergy)
                AddText(card, "同方保障车在水平100米内可为悬停无人机补充能源；不含维修、弹药或兵营服务，是否交付由后端裁决。", "body-text");
            AddButton(card, "暂存任务", () => {
                var current = ManualUnit(id);
                if (current == null || scope != ManualScope || current.life_id != sourceLife || candidates.Length == 0) { warning.text = "状态已变化，请关闭后重新选择"; return; }
                var chosen = candidates[target.index];
                var latest = (service ? serverClient.View.units : serverClient.View.contacts ?? Array.Empty<SandboxServerUnit>())
                    .FirstOrDefault(u => u.unit_id == chosen.unit_id && u.life_id == chosen.life_id && u.durability > 0);
                if (latest == null || (service ? latest.side != current.side : latest.side == current.side)) { warning.text = "目标情报已变化，请重新选择"; return; }
                SandboxBattleDraftPayload payload;
                if (service)
                {
                    if (IsUndockedRecon(latest) && (!AllowsManualHoverEnergy(current) || category.value != "能源"))
                    { warning.text = "当前目标状态仅可按本局规则补充空中能源，请重新选择"; return; }
                    string value = amount.value.Trim();
                    if (!decimal.TryParse(value, NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out decimal number)
                        || number <= 0 || (category.value == "弹药" && decimal.Truncate(number) != number)) { warning.text = "请输入正数，弹药须为整数"; return; }
                    if (category.value == "弹药" && latest.unit_type != "ground") { warning.text = "只有履带地面单位有自用弹药容量"; return; }
                    payload = SandboxBattleDraftPayload.Service(ManualBattle.round_number, current, latest,
                        category.value == "能源" ? "energy" : category.value == "弹药" ? "ammo" : "repair", value, method.index == 1);
                }
                else payload = SandboxBattleDraftPayload.TargetTask(ManualBattle.round_number, current, action, latest);
                CloseManualBattleOverlay(); SendServerCommand("battle_draft", payload, basis);
            }, "primary-button").SetEnabled(candidates.Length > 0);
            AddButton(card, "取消", CloseManualBattleOverlay);
        }
        private void OpenManualCamp(string id)
        {
            var unit = ManualUnit(id); if (unit == null) return;
            string scope = ManualScope, life = unit.life_id; var basis = serverClient.View.context;
            var camps = (serverClient.View.camps ?? Array.Empty<SandboxServerCamp>()).Where(c => c.side == unit.side).OrderBy(c => c.camp_id).ToArray();
            var card = ManualCard("兵营补充请求");
            var camp = new DropdownField("本方兵营", camps.Select(c => c.camp_id).DefaultIfEmpty("没有可用兵营").ToList(), 0); card.Add(camp);
            AddText(card, "明确请求所选兵营补至本单位各项容量上限，保障单位含携行库存。不自动返营、不凭空补满；只有合法停驻、库存和服务名额允许时才逐轮交付。", "body-text");
            AddButton(card, "暂存兵营请求", () => {
                var current = ManualUnit(id); if (current == null || scope != ManualScope || life != current.life_id || camps.Length == 0) return;
                var payload = SandboxBattleDraftPayload.Camp(ManualBattle.round_number, current, camps[camp.index].camp_id);
                CloseManualBattleOverlay(); SendServerCommand("battle_draft", payload, basis);
            }, "primary-button").SetEnabled(camps.Length > 0);
            AddButton(card, "取消", CloseManualBattleOverlay);
        }
        private void OpenManualArea(string id)
        {
            if (ManualUnit(id) == null) return;
            string scope = ManualScope;
            var card = ManualCard("范围攻击授权"); var radius = new IntegerField("半径（米）") { value = 250 }; card.Add(radius);
            var warning = AddText(card, "范围内仅对合法可见且在实际射程 / 射线内的敌军按规则选敌，不自动追击。", "body-text");
            AddButton(card, "在地图选择圆心", () => {
                if (scope != ManualScope || !ManualEditable) return;
                if (radius.value < 1 || radius.value > 12000) { warning.text = "半径须为1～12000米"; return; }
                int meters = radius.value; CloseManualBattleOverlay(); BeginManualArea(id, meters);
            }, "primary-button"); AddButton(card, "取消", CloseManualBattleOverlay);
        }
        private void BeginManualArea(string id, int radius)
        {
            var unit = ManualUnit(id); if (unit == null) return;
            CloseManualBattleOverlay(); CancelArmyCommand(); CloseArmy();
            pendingBattleAreaLife = unit.life_id;
            pendingBattleAreaId = id; pendingBattleAreaRadius = radius; pendingBattleAreaScope = ManualScope; pendingBattleAreaBasis = serverClient.View.context;
            ShowArmyCommand((radius == 0 ? "侦察位置 · " : "范围攻击圆心 · ") + id, "单击地图暂存草稿，不立即移动或开火；Esc取消。");
        }
        // 此页只读，不复用旧三点驻守/阶段裁定，也不把总册120分归一化为100分。
        private void BuildManualResultReport(SandboxManualBattleView battle, SandboxReplayPoint[] points, bool archived)
        {
            ReportHeading("穹图 / 总册结果审计", "演示结果", "手动战斗 · 总册120分制 · 每轮60仿真秒，最多60轮。当前得分不代表提前裁定获胜。");
            if (archived) AddText(settingsContent, "正在查看只读档案：" + resultArchiveTitle, "report-notice");
            var hero = ReportCard(settingsContent, battle.stage == "finished" ? "最终结果" : "轮末状态 / 尚未终局");
            string verdict = battle.stage != "finished" ? "进行中 · 不预判胜负"
                : battle.winner == "draw" ? "平局" : battle.winner == "blue" ? "蓝方获胜" : battle.winner == "red" ? "红方获胜" : "终局结果待核验";
            AddText(hero, verdict, "report-verdict").name = "manualResultVerdict";
            AddText(hero, "已完成 " + battle.completed_rounds + " / 60 轮 · " + battle.elapsed_seconds + " 仿真秒", "report-muted");
            if (!string.IsNullOrEmpty(battle.finish_reason))
                AddText(hero, battle.finish_reason == "time_limit" ? "达到3600仿真秒，按总册总分与同分规则裁定"
                    : battle.finish_reason == "elimination" ? "一方全部退场，按总册全歼规则结束"
                    : battle.finish_reason == "mutual_elimination" ? "双方全部退场，按总册总分与同分规则裁定" : "结束原因：" + battle.finish_reason, "report-muted");
            foreach (string side in new[] { "blue", "red" })
            {
                var score = side == "blue" ? battle.blue_breakdown : battle.red_breakdown;
                string total = side == "blue" ? battle.blue_score : battle.red_score;
                var card = ReportCard(settingsContent, (side == "blue" ? "蓝方" : "红方") + (battle.stage == "finished" ? "最终得分" : "当前得分"));
                card.AddToClassList(side == "blue" ? "report-score-blue" : "report-score-red");
                AddText(card, ManualScoreText(total) + " / 120 分", "report-score-number").name = side + "ManualScore";
                AddText(card, "占点 " + ManualScoreText(side == "blue" ? battle.blue_control_score ?? score?.control : battle.red_control_score ?? score?.control) + " / 70  ·  击毁 " + ManualScoreText(score?.kills)
                    + " / 20  ·  剩余资源 " + ManualScoreText(score?.resources) + " / 30", "report-calculation");
                AddText(card, string.IsNullOrEmpty(total) ? "对手击毁分、资源分和总分预估在终局后公开" : "后端精确总分：" + total, "report-muted");
            }
            var rules = ReportCard(settingsContent, "计分依据 / 不重算裁决");
            AddText(rules, "占点70分 + 击毁20分 + 剩余资源30分（能源15、弹药9、维修6）。提前终局仍使用完整3600秒的占点分母，不缩短分母、不归一化为100分。", "report-calculation");
            AddText(rules, "分数来自后端精确账本；页面只作显示。动画播放、暂停、跳转或重复观看不会推进世界或扣除资源。", "report-muted");
            var captures = ReportCard(settingsContent, "五据点 / 本次结果快照");
            foreach (var point in points ?? Array.Empty<SandboxReplayPoint>())
                AddText(captures, point.point_id + " · " + (point.owner == "blue" ? "蓝方" : point.owner == "red" ? "红方" : "中立")
                    + (point.contested ? " · 争夺中" : "") + (point.progress_hidden ? " · 对方占领进度不公开" : "") + (point.advancing_side == "blue" || point.advancing_side == "red"
                        ? " · " + (point.advancing_side == "blue" ? "蓝方" : "红方") + "占领进度 " + (point.progress_steps * 100 / 6) + "%" : ""), "report-muted");
            if (!archived && role == SandboxRole.Host)
            {
                var button = AddButton(settingsContent, "在地图播放本轮", OpenRecordedReplay, "report-primary");
                button.name = "openManualResultReplay"; button.SetEnabled(ManualWritable && battle.completed_rounds > 0);
            }
            if (archived)
                AddButton(settingsContent, "返回当前对局结果", () => { resultArchiveTitle = null; resultManualArchive = null;
                    resultManualArchivePoints = null; showingPreview = false; RenderSettingsSection(); }, "report-button");
        }
        private static string ManualScoreText(string value) => string.IsNullOrEmpty(value) ? "—"
            : SandboxRoundReplayCursor.DisplayNumber(value).ToString("0.###", CultureInfo.InvariantCulture);
    }
}
