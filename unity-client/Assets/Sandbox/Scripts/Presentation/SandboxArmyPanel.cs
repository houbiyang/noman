using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        private VisualElement armyOverlay;
        private VisualElement armyCommand;
        private VisualElement barracksDragPreview;
        private EventCallback<PointerMoveEvent> barracksDragPreviewMoveHandler;
        private SandboxRole armySide;
        private SandboxUnitType? managedArmyType;
        private SandboxUnitType? pendingArmyType;
        private SandboxRole pendingArmySide;
        private string pendingMoveId;
        private Action refreshArmyManagement;
        private string movementMode = "move", pendingMovementMode = "move", deploymentMovingUnit;
        private bool pendingFormationMovement, deploymentMovingFormation;
        private int spawnQuantity = 1;
        private float nextDeploymentMovement;
        private readonly List<SandboxServerPosition> movementWaypoints = new List<SandboxServerPosition>();

        private void OpenArmy()
        {
            if (settingsOverlay != null) { RequestCloseSettings(OpenArmy); return; }
            CancelArmyCommand(); ClosePanel(); CloseArmy(); PausePlayback();
            armySide = role == SandboxRole.Host ? SandboxRole.Red : role;
            managedArmyType = null;
            RenderArmy();
        }
        private void RenderArmy()
        {
            CloseArmy();
            armyOverlay = new VisualElement { name = "armyOverlay" }; armyOverlay.AddToClassList("army-panel"); armyOverlay.AddToClassList("ui-blocker");
            root.Add(armyOverlay);
            if (managedArmyType.HasValue) { RenderBarracksManagement(); return; }
            var panel = armyOverlay;
            var header = new VisualElement(); header.AddToClassList("army-header"); panel.Add(header);
            var titles = new VisualElement(); titles.style.flexGrow = 1; header.Add(titles);
            AddText(titles, "军队配置", "army-title");
            AddButton(header, "×", () => { CloseArmy(); CancelArmyCommand(); }, "army-close");
            var toolbar = new VisualElement(); toolbar.AddToClassList("army-toolbar"); panel.Add(toolbar);
            AddText(toolbar, SideLabel(armySide) + "编队", "army-side");
            if (role == SandboxRole.Host)
                AddButton(toolbar, armySide == SandboxRole.Red ? "切换为蓝方" : "切换为红方", () => { armySide = armySide == SandboxRole.Red ? SandboxRole.Blue : SandboxRole.Red; RenderArmy(); }, "army-switch");
            var spacer = new VisualElement(); spacer.style.flexGrow = 1; toolbar.Add(spacer);
            AddButton(toolbar, "单位管理", () => {
                var selected = projection.units.FirstOrDefault(unit => unit.id == selectedId && unit.side == armySide);
                OpenArmyManagement(armySide, selected?.type ?? SandboxUnitType.Recon);
            }, "army-switch");
            var scroll = new ScrollView(ScrollViewMode.Vertical); scroll.AddToClassList("army-scroll"); panel.Add(scroll);
            var cards = new VisualElement(); cards.AddToClassList("army-cards"); scroll.Add(cards);
            for (int i = 0; i < 3; i++) BuildArmyCard(cards, i);
            var footer = new VisualElement(); footer.AddToClassList("army-footer"); panel.Add(footer);
            var summary = new VisualElement(); summary.style.flexGrow = 1; footer.Add(summary);
            AddText(summary, "点击兵营卡片 → 在地图上拖动并放置", "army-footnote");
            AddText(summary, "输入本次新增数量，从对应兵营生成单位；移动时出营", "army-footnote");
        }
        private void BuildArmyCard(VisualElement parent, int index)
        {
            var type = (SandboxUnitType)index;
            var camp = projection.barracks.FirstOrDefault(item => item.side == armySide && item.type == type);
            var card = new VisualElement(); card.AddToClassList("army-card"); parent.Add(card);
            if (pendingArmyType == type && pendingArmySide == armySide) card.AddToClassList("army-card-selected");
            var stage = new VisualElement(); stage.AddToClassList("army-stage"); card.Add(stage);
            var image = new Image { image = Resources.Load<Texture2D>("Army/" + DemoSandboxViewSource.ArmyKey(type) + "-barracks-card"), scaleMode = ScaleMode.ScaleToFit };
            image.AddToClassList("army-model"); stage.Add(image);
            stage.RegisterCallback<ClickEvent>(evt => { BeginDeploy(type, evt.position); evt.StopPropagation(); });
            var details = new VisualElement(); details.AddToClassList("army-card-details"); card.Add(details);
            AddText(details, camp == null ? DemoSandboxViewSource.BarracksName(type) : BarracksDisplayName(camp), "army-card-title");
            AddText(details, DemoSandboxViewSource.ArmyName(type), "army-troop-name");
            AddText(details, camp == null ? "尚未建造" : "已建造 · 可设置出兵数量", "army-current");
            AddButton(details, "查看兵种 / 管理单位", () => OpenArmyManagement(armySide, type), "army-manage");
        }

        private void CloseArmy()
        {
            armyOverlay?.RemoveFromHierarchy(); armyOverlay = null;
            refreshArmyManagement = null;
            world.SelectBarracks(null);
        }

        private void SelectBarracks(string id)
        {
            var camp = projection.barracks?.FirstOrDefault(item => item.id == id);
            if (camp == null) return;
            OpenArmyManagement(camp.side, camp.type);
        }

        private void OpenSelectedArmyManagement()
        {
            var selected = projection.units.FirstOrDefault(unit => unit.id == selectedId);
            if (selected == null) { OpenArmy(); return; }
            OpenArmyManagement(selected.side, selected.type);
        }

        private void OpenArmyManagement(SandboxRole side, SandboxUnitType type)
        {
            if (settingsOverlay != null) { RequestCloseSettings(() => OpenArmyManagement(side, type)); return; }
            if (role != SandboxRole.Host && side != role) return;
            CancelArmyCommand(); ClosePanel(); CloseArmy(); PausePlayback();
            armySide = side; managedArmyType = type; unitSearch = string.Empty;
            RenderArmy();
        }

        private void RenderBarracksManagement()
        {
            var type = managedArmyType.Value;
            var camp = projection.barracks.FirstOrDefault(item => item.side == armySide && item.type == type);
            bool hasCamp = camp != null;
            world.SelectBarracks(camp?.id);
            armyOverlay.name = "barracksManagement";
            var header = new VisualElement(); header.AddToClassList("army-header"); armyOverlay.Add(header);
            var titles = new VisualElement(); titles.style.flexGrow = 1; header.Add(titles);
            AddText(titles, camp == null ? DemoSandboxViewSource.BarracksName(type) : BarracksDisplayName(camp), "army-manager-title");
            AddText(titles, SideLabel(armySide) + " · " + DemoSandboxViewSource.ArmyName(type), "army-troop-name");
            AddButton(header, "×", () => { CloseArmy(); CancelArmyCommand(); }, "army-close");
            var toolbar = new VisualElement(); toolbar.AddToClassList("army-toolbar"); armyOverlay.Add(toolbar);
            AddButton(toolbar, "‹ 兵营列表", () => { managedArmyType = null; RenderArmy(); }, "army-switch");
            foreach (SandboxUnitType candidate in Enum.GetValues(typeof(SandboxUnitType)))
            {
                var tab = AddButton(toolbar, candidate == SandboxUnitType.Recon ? "侦察" : candidate == SandboxUnitType.Ground ? "地面" : "保障",
                    () => OpenArmyManagement(armySide, candidate), "army-type-tab");
                tab.EnableInClassList("army-active-tab", candidate == type);
            }
            var scroll = new ScrollView(ScrollViewMode.Vertical); scroll.AddToClassList("army-scroll"); armyOverlay.Add(scroll);
            var body = new VisualElement(); body.AddToClassList("army-manager-body"); scroll.Add(body);
            if (serverMode && serverClient.View?.spatial_test == true)
            {
                var modes = new DropdownField("移动方式", new List<string> { "普通移动", "快速移动", "倒车" }, movementMode == "fast_move" ? 1 : movementMode == "reverse_move" ? 2 : 0);
                modes.AddToClassList("field"); body.Add(modes);
                modes.RegisterValueChangedCallback(_ => { movementMode = new[] { "move", "fast_move", "reverse_move" }[modes.index];
                    refreshArmyManagement?.Invoke(); });
                if (serverClient.View.clock.phase == "preparing") AddText(body, "集群试走：同兵营单位按纵队出发，各自记录位置和测试能源；对局时间不变。Ctrl＋点击可添加途经点。", "army-footnote");
            }
            var spawn = new VisualElement(); spawn.AddToClassList("army-spawn"); body.Add(spawn);
            var quantityInput = new IntegerField("本次新增出兵数量") { value = spawnQuantity };
            quantityInput.AddToClassList("army-spawn-input"); spawn.Add(quantityInput);
            var spawnLimit = AddText(spawn, "", "army-footnote");
            var spawnButton = AddButton(spawn, "按输入数量从兵营出兵", () => SpawnFromBarracks(type, quantityInput.value), "army-spawn-button");
            Action updateSpawn = () =>
            {
                int current = SandboxArmyLayout.UnitsForBarracks(projection.units, armySide, type).Length;
                int remaining = serverMode ? Math.Max(0, 10 - current) : source.ArmyCapacityRemaining;
                bool canSpawn = hasCamp && (!serverMode || serverClient.View?.clock.phase == "preparing"
                    && serverClient.View.camps.Any(item => item.side == ServerSide(armySide) && item.unit_type == ServerType(type) && item.deployment_validated));
                spawnLimit.text = !canSpawn ? "请先建造该兵种兵营；仅准备阶段可新增单位"
                    : remaining == 0 ? "该兵种已达到当前数量上限"
                    : quantityInput.value < 1 || quantityInput.value > remaining
                        ? "本次最多可新增 " + remaining + " 个，请修改输入数量"
                        : "该兵种已有 " + current + " 个 · 本次可新增 1–" + remaining + " 个";
                spawnButton.SetEnabled(canSpawn && quantityInput.value >= 1 && quantityInput.value <= remaining);
            };
            quantityInput.RegisterValueChangedCallback(change => { spawnQuantity = change.newValue; updateSpawn(); });
            var summary = new VisualElement(); summary.name = "barracksSummary"; summary.AddToClassList("army-summary"); body.Add(summary);
            var search = new TextField("查找单位") { value = unitSearch, maxLength = 60 }; search.AddToClassList("field"); body.Add(search);
            var list = new VisualElement { name = "barracksUnits" }; body.Add(list);
            Action render = null;
            render = () =>
            {
                summary.Clear(); list.Clear();
                var units = SandboxArmyLayout.UnitsForBarracks(projection.units, armySide, type);
                updateSpawn();
                AddText(summary, "兵种数量  " + units.Length + " 个", "army-summary-count");
                var known = units.Where(unit => unit.maxHealth > 0).ToArray();
                AddText(summary, known.Length == 0 ? "血量  待接入仿真数据" :
                    "已知血量  " + known.Sum(unit => (long)unit.health) + " / " + known.Sum(unit => (long)unit.maxHealth) + " · " + known.Length + "/" + units.Length + " 单位已上报", "army-current");
                AddText(summary, "当前任务 · " + projection.taskType, "army-current");
                foreach (var state in units.GroupBy(unit => unit.status))
                    AddText(summary, state.Key + "  × " + state.Count(), "army-status-line");
                AddText(summary, hasCamp ? "当前阵营同兵种单位" : "尚未建造兵营 · 可先管理已有同兵种单位", "army-footnote");
                string moveRestriction = ArmyMoveRestriction();
                if (moveRestriction.Length > 0) AddText(summary, moveRestriction, "army-footnote");
                else if (serverMode && serverClient.View.spatial_test)
                    AddText(summary, serverClient.View.clock.phase == "preparing" ? "部署试走可随时停止，测试能源可单独重置。" : "移动目标提交后，主持人点击“单步 +1”或“继续播放”才会推进部队。", "army-footnote");
                var filtered = units.Where(unit => (unit.id + unit.name).IndexOf(unitSearch, StringComparison.OrdinalIgnoreCase) >= 0).ToArray();
                if (filtered.Length == 0) AddText(list, units.Length == 0 ? "暂无该兵种单位；可在上方输入数量出兵。" : "没有匹配的单位。", "army-empty");
                foreach (var unit in filtered)
                {
                    var card = new VisualElement { name = "barracks-unit-" + unit.id }; card.AddToClassList("army-unit-card");
                    card.EnableInClassList("army-card-selected", selectedId == unit.id); list.Add(card);
                    AddText(card, unit.id + " · " + unit.name, "army-unit-title");
                    AddText(card, "任务状态 · " + unit.status, "army-current");
                    AddText(card, unit.maxHealth > 0 ? "血量 " + unit.health + " / " + unit.maxHealth : "血量 · 未提供", "army-current");
                    if (unit.maxHealth > 0)
                    {
                        var health = new ProgressBar { lowValue = 0, highValue = unit.maxHealth, value = unit.health, title = "" };
                        health.AddToClassList("army-health"); card.Add(health);
                    }
                    AddText(card, "位置 X " + unit.x.ToString("0.00") + " / Z " + unit.z.ToString("0.00"), "army-footnote");
                    if (serverMode && serverClient.View.spatial_test)
                    {
                        var fact = serverClient.View.units.First(item => item.unit_id == unit.id);
                        AddText(card, string.IsNullOrEmpty(fact.camp_id) ? "等待配置兵营与测试资源" : "测试能源 " + fact.energy.ToString("0.###")
                            + " · 补给 " + fact.supply + " · 耐久 " + fact.durability, "army-current");
                        if (serverClient.View.clock.phase == "preparing") AddText(card, "试走步 " + fact.movement_test_steps + " · 试走能源 "
                            + (fact.movement_test_steps > 0 ? fact.movement_test_energy : fact.energy).ToString("0.###"), "army-current");
                        var route = serverClient.View.movement_paths?.FirstOrDefault(item => item.unit_id == unit.id);
                        if (route != null) AddText(card, "路线 " + route.status + " · 预计 " + route.estimated_steps.ToString("0.##") + " 步 / "
                            + route.estimated_energy.ToString("0.##") + " 能源", "army-footnote");
                    }
                    var actions = new VisualElement(); actions.AddToClassList("army-unit-actions"); card.Add(actions);
                    AddButton(actions, "选择定位", () => { selectedId = unit.id; world.Select(unit.id); cameraRig.Locate(unit.id, UnitLocateViewport()); RefreshProjection(); }, "army-unit-action");
                    bool formationMovement = serverMode && serverClient.View?.clock.phase == "preparing" && units.Length > 1
                        && movementMode != "reverse_move";
                    var move = AddButton(actions, formationMovement ? "集群部署试走" : serverMode && serverClient.View?.clock.phase == "preparing" ? "部署试走" : "地图移动",
                        () => BeginMove(unit.id, formationMovement), "army-unit-action");
                    move.SetEnabled(moveRestriction.Length == 0);
                    move.tooltip = moveRestriction;
                    AddButton(actions, "跟随观察", () => { selectedId = unit.id; world.Select(unit.id); cameraRig.Follow(unit.id); RefreshProjection(); }, "army-unit-action");
                    if (serverMode && serverClient.View?.spatial_test == true)
                    {
                        AddButton(actions, "停止移动", () => SubmitMovement(unit.id, "stop_move", formation: formationMovement), "army-unit-action").SetEnabled(moveRestriction.Length == 0);
                        AddButton(actions, "返回兵营", () => SubmitMovement(unit.id, "return_to_barracks", formation: formationMovement), "army-unit-action").SetEnabled(moveRestriction.Length == 0);
                        if (serverClient.View.clock.phase == "preparing")
                        {
                            AddButton(actions, "设置试走起点", () => { BeginMove(unit.id); pendingMovementMode = "deploy_start";
                                ShowArmyCommand("设置 " + unit.id + " 的试走起点", "部署测试定位 · 单击合法位置 · 不模拟行进 · Esc 取消"); }, "army-unit-action");
                            AddButton(actions, "继续试走", () => { var route = serverClient.View.movement_paths?.FirstOrDefault(item => item.unit_id == unit.id);
                                deploymentMovingFormation = !string.IsNullOrEmpty(route?.formation_leader_id);
                                deploymentMovingUnit = deploymentMovingFormation ? route.formation_leader_id : unit.id;
                                nextDeploymentMovement = 0; }, "army-unit-action");
                            AddButton(actions, "重置试走能源", () => SubmitMovement(unit.id, "reset_test_energy", formation: formationMovement), "army-unit-action");
                        }
                    }
                    AddButton(actions, "删除单位", () => OpenDeleteArmyUnitModal(unit), "army-unit-delete")
                        .SetEnabled(!serverMode || serverClient.View?.capabilities.can_delete_unit == true);
                }
            };
            refreshArmyManagement = render;
            search.RegisterValueChangedCallback(change => { unitSearch = change.newValue; render(); }); render();
            var footer = new VisualElement(); footer.AddToClassList("army-footer"); armyOverlay.Add(footer);
            AddButton(footer, "关闭", CloseArmy, "army-switch");
            AddText(footer, serverMode ? "当前席位数据 · 新增单位先在兵营待命，点击移动后出营" : "本机演示数据 · 可按输入数量从兵营出兵", "army-footnote");
            header.Q<Button>().Focus();
        }
        /// <summary>核对本次新增数量，再由当前席位的兵营生成单位。</summary>
        /// <param name="type">当前兵营的兵种。</param>
        /// <param name="quantity">本次输入的新增单位数。</param>
        private void SpawnFromBarracks(SandboxUnitType type, int quantity)
        {
            int current = SandboxArmyLayout.UnitsForBarracks(projection.units, armySide, type).Length;
            int remaining = serverMode ? Math.Max(0, 10 - current) : source.ArmyCapacityRemaining;
            if (quantity < 1 || quantity > remaining) { Notify("本次出兵数量须为 1–" + remaining + "，请修改输入框"); return; }
            if (serverMode)
            {
                SendServerCommand("spawn_from_barracks", new SandboxServerBarracksSpawn {
                    side = ServerSide(armySide), unit_type = ServerType(type), quantity = quantity });
                return;
            }
            if (source.TrySpawnArmy(role, armySide, type, quantity, out string message))
            { RefreshProjection(); RenderArmy(); }
            Notify(message);
        }
        private Rect UnitLocateViewport()
        {
            float width = root.resolvedStyle.width, height = root.resolvedStyle.height;
            if (!float.IsFinite(width) || !float.IsFinite(height) || width <= 0 || height <= 0) return default;
            float right = armyOverlay != null ? armyOverlay.worldBound.xMin - root.worldBound.xMin - 24 : width - 24;
            // Leave the title/command band, bottom HUD and open manager unobstructed.
            return new Rect(24 / width, 164 / height, Mathf.Max(width * .12f, right - 24) / width,
                Mathf.Max(height * .25f, height - 364) / height);
        }

        private void OpenDeleteArmyUnitModal(SandboxUnitData unit)
        {
            PausePlayback();
            SetText("modalTitle", "删除已部署单位？");
            SetText("modalMessage", unit.id + " · " + unit.name + "\n确认后移除地图模型、移动路径和当前部署，释放 1 个名额。其他单位保持不变。后续单位由仿真流程从兵营出发。");
            SetText("confirmModalButton", "确认删除单位");
            modalAction = () =>
            {
                if (serverMode)
                {
                    SendServerCommand("delete_unit", new SandboxServerDeleteUnit { unit_id = unit.id });
                    return;
                }
                if (source.TryDeleteArmyUnit(role, unit.id, out string message))
                {
                    if (selectedId == unit.id && cameraRig.IsFollowing) cameraRig.ReturnToOverview();
                    CancelArmyCommand();
                    RefreshProjection();
                }
                Notify(message);
            };
            root.Q("modalBackdrop").BringToFront();
            root.Q("modalBackdrop").RemoveFromClassList("hidden");
            root.Q<Button>("cancelModalButton").Focus();
        }
        private void BeginDeploy(SandboxUnitType type, Vector2 pointerPosition)
        {
            if (serverMode && serverClient.View?.capabilities.can_place_barracks != true) { Notify("兵营只能在准备阶段拖动放置"); return; }
            managedArmyType = null;
            pendingArmyType = type; pendingArmySide = armySide; pendingMoveId = null;
            ClosePanel(); CloseArmy();
            ShowArmyCommand("拖动放置" + DemoSandboxViewSource.BarracksName(type), "拖动中的兵营会跟随光标 · 在合法空地松开左键放置 · Esc 取消");
            ShowBarracksDragPreview(type, pointerPosition);
        }
        private void ShowBarracksDragPreview(SandboxUnitType type, Vector2 pointerPosition)
        {
            barracksDragPreview?.RemoveFromHierarchy();
            var preview = new VisualElement();
            preview.AddToClassList("army-drag-preview");
            preview.pickingMode = PickingMode.Ignore;
            var image = new Image { image = Resources.Load<Texture2D>("Army/" + DemoSandboxViewSource.ArmyKey(type) + "-barracks-card"),
                scaleMode = ScaleMode.ScaleToFit };
            image.AddToClassList("army-drag-preview-image");
            preview.Add(image);
            AddText(preview, DemoSandboxViewSource.BarracksName(type), "army-drag-preview-label");
            root.Add(preview);
            barracksDragPreview = preview;
            barracksDragPreviewMoveHandler = evt => UpdateBarracksDragPreview(evt.position);
            root.RegisterCallback(barracksDragPreviewMoveHandler, TrickleDown.TrickleDown);
            UpdateBarracksDragPreview(pointerPosition);
        }
        private void UpdateBarracksDragPreview(Vector2 panelPosition)
        {
            if (!pendingArmyType.HasValue || barracksDragPreview == null) return;
            barracksDragPreview.style.left = panelPosition.x - 76;
            barracksDragPreview.style.top = panelPosition.y - 54;
        }
        private void HideBarracksDragPreview()
        {
            if (root != null && barracksDragPreviewMoveHandler != null)
                root.UnregisterCallback(barracksDragPreviewMoveHandler, TrickleDown.TrickleDown);
            barracksDragPreviewMoveHandler = null;
            barracksDragPreview?.RemoveFromHierarchy();
            barracksDragPreview = null;
        }
        /// <summary>选择单兵或同兵营纵队的地图目标。</summary>
        /// <param name="id">当前选中的单位编号。</param>
        /// <param name="formation">准备阶段是否让同兵营单位一起试走。</param>
        private void BeginMove(string id, bool formation = false)
        {
            string restriction = ArmyMoveRestriction();
            if (restriction.Length > 0) { Notify(restriction); return; }
            PausePlayback(); pendingMoveId = id; pendingFormationMovement = formation; selectedId = id; pendingArmyType = null; world.Select(id);
            if (serverMode && serverClient.View?.units?.Any(item => item.unit_id == id && item.in_barracks) == true)
                cameraRig.Locate(id, UnitLocateViewport());
            pendingMovementMode = movementMode; movementWaypoints.Clear();
            ClosePanel(); CloseArmy();
            ShowArmyCommand((formation ? "集群移动 " : "移动 ") + id, serverMode && serverClient.View.spatial_test
                ? serverClient.View.clock.phase == "preparing" ? (formation ? "同兵营单位排队出发 · 单击目标开始 · Ctrl＋点击添加途经点 · Esc 取消" : "部署试走 · 单击目标开始 · Ctrl＋点击添加途经点 · Esc 取消") : "单击目标位置排队 · 主持人推进后移动 · Esc 取消"
                : "单击目标位置 · 地面单位自动寻路 · Esc 取消");
        }

        /// <summary>按照服务端当前阶段和移动资格说明不可移动的原因，本机演示沿用原操作。</summary>
        /// <returns>允许选择移动目标时返回空字符串，否则返回当前阶段的操作提示。</returns>
        private string ArmyMoveRestriction()
        {
            if (!serverMode) return "";
            if (serverClient.View == null) return "正在读取部队状态，请等待同步完成。";
            if (serverClient.View.spatial_test)
            {
                switch (serverClient.View.clock.phase)
                {
                    case "preparing": return serverClient.View.capabilities.can_test_movement ? "" : "当前后端未开放部署移动测试。";
                    case "awaiting_confirmation": return "等待双方采用建议后，才能安排部队移动路线。";
                }
            }
            if (serverClient.View.clock.phase == "finished") return "对局已结束，不能再移动部队。";
            return serverClient.View.capabilities.can_move_unit ? "" : "当前阶段暂不允许移动部队。";
        }
        private void ShowArmyCommand(string title, string detail)
        {
            armyCommand?.RemoveFromHierarchy(); armyCommand = new VisualElement(); armyCommand.AddToClassList("army-command"); armyCommand.AddToClassList("ui-blocker"); root.Add(armyCommand);
            var text = new VisualElement(); text.style.flexGrow = 1; armyCommand.Add(text);
            AddText(text, title, "army-command-title"); AddText(text, detail, "army-command-detail");
            AddButton(armyCommand, "取消", CancelArmyCommand, "army-secondary");
        }
        private void CancelArmyCommand()
        {
            movementWaypoints.Clear();
            pendingArmyType = null; pendingMoveId = null; pendingFormationMovement = false; armyCommand?.RemoveFromHierarchy(); armyCommand = null;
            HideBarracksDragPreview();
            armyOverlay?.Query<VisualElement>(className: "army-card-selected").ForEach(card => card.RemoveFromClassList("army-card-selected"));
        }
        private void HandleArmyMapClick(Vector3 point)
        {
            if (serverMode && (!serverClient.Healthy || serverClient.HasPending || serverClient.Busy && pendingMoveId == null))
            { Notify("请等待当前同步或提交结果，选址状态已保留"); return; }
            if (!world.TryBusinessPosition(point, out Vector2 position)) { Notify("请选择地图范围内的位置"); return; }
            if (pendingArmyType.HasValue)
            {
                SandboxUnitType type = pendingArmyType.Value;
                if (serverMode)
                {
                    var payload = new SandboxServerBarracks { side = ServerSide(pendingArmySide), unit_type = ServerType(type),
                        position = ServerPosition(position.x, position.y) };
                    CancelArmyCommand(); SendServerCommand("place_barracks", payload); return;
                }
                if (!world.CanPlaceBarracks(type, pendingArmySide, position.x, position.y, out string placementMessage)) { Notify(placementMessage); return; }
                if (!source.TryPlaceBarracks(role, pendingArmySide, type, position.x, position.y, out string message)) { Notify(message); return; }
                CancelArmyCommand(); RefreshProjection(); RenderArmy(); Notify(message);
            }
            else
            {
                string id = pendingMoveId ?? selectedId;
                SandboxUnitData unit = projection.units.FirstOrDefault(candidate => candidate.id == id);
                if (unit == null) { Notify("请先选择单位"); return; }
                if (!serverMode && !world.CanMove(id, position.x, position.y, unit.type)) { Notify("目标不可通行或没有可达路径，请另选位置"); return; }
                if (serverMode)
                {
                    string restriction = ArmyMoveRestriction();
                    if (restriction.Length > 0) { Notify(restriction); return; }
                    if (serverClient.View.spatial_test)
                    {
                        if (UnityEngine.InputSystem.Keyboard.current?.ctrlKey.isPressed == true)
                        {
                            if (movementWaypoints.Count >= 7 || pendingMovementMode == "reverse_move") { Notify("途经点最多 7 个，倒车不支持途经点"); return; }
                            movementWaypoints.Add(ServerPosition(position.x, position.y)); Notify("已添加途经点 " + movementWaypoints.Count + "，单击最终目标提交"); return;
                        }
                        SubmitMovement(id, pendingMoveId == null ? movementMode : pendingMovementMode, ServerPosition(position.x, position.y), movementWaypoints.ToArray(), pendingFormationMovement);
                        CancelArmyCommand(); return;
                    }
                    CancelArmyCommand(); SendServerCommand("move_unit", new SandboxServerMove { unit_id = id, position = ServerPosition(position.x, position.y) }); return;
                }
                if (source.TryMoveArmyUnit(role, id, position.x, position.y, out string message))
                { CancelArmyCommand(); RefreshProjection(); world.Select(id); OpenArmyManagement(unit.side, unit.type); Notify(message); }
                else Notify(message);
            }
        }

        /// <summary>提交规则移动，部署自动试走仅推进当前席位明确选择的单位。</summary>
        /// <param name="id">本方可操作单位。</param>
        /// <param name="mode">移动、停止、返营或测试能源重置模式。</param>
        /// <param name="target">可选的地图业务坐标。</param>
        /// <param name="waypoints">当前用户添加的途经点。</param>
        /// <param name="formation">是否让当前兵营的单位作为纵队执行。</param>
        private void SubmitMovement(string id, string mode, SandboxServerPosition target = null, SandboxServerPosition[] waypoints = null, bool formation = false)
        {
            if (!serverClient.Healthy || serverClient.HasPending) { Notify("请等待原指令完成后再操作，目标尚未提交"); return; }
            deploymentMovingUnit = serverClient.View.clock.phase == "preparing" && mode != "stop_move" && mode != "reset_test_energy" && mode != "deploy_start" ? id : null;
            deploymentMovingFormation = formation && deploymentMovingUnit != null;
            nextDeploymentMovement = Time.unscaledTime + .85f;
            if (target == null)
            {
                SendServerCommand("movement_order", new SandboxServerMovementOrderWithoutPosition { unit_id = id, mode = mode,
                    formation = formation,
                    waypoints = waypoints ?? Array.Empty<SandboxServerPosition>() });
                return;
            }
            SendServerCommand("movement_order", new SandboxServerMovementOrder { unit_id = id, mode = mode, position = target,
                formation = formation,
                waypoints = waypoints ?? Array.Empty<SandboxServerPosition>() });
        }

        /// <summary>用独立试走步演示部署行进，不调用正式单步或播放命令。</summary>
        private void UpdateDeploymentMovement()
        {
            if (!serverMode || deploymentMovingUnit == null || serverClient.View == null) return;
            if (serverClient.View.clock.phase != "preparing" || !serverClient.Healthy) { deploymentMovingUnit = null; return; }
            if (serverClient.Busy || serverClient.HasPending || Time.unscaledTime < nextDeploymentMovement) return;
            var routes = serverClient.View.movement_paths;
            bool active = deploymentMovingFormation
                ? routes?.Any(item => item.formation_leader_id == deploymentMovingUnit &&
                    (item.status == "queued" || item.status == "moving" || item.status == "waiting")) == true
                : routes?.Any(item => item.unit_id == deploymentMovingUnit &&
                    (item.status == "queued" || item.status == "moving" || item.status == "waiting")) == true;
            if (!active) { deploymentMovingUnit = null; return; }
            nextDeploymentMovement = Time.unscaledTime + .85f;
            SendServerCommand("advance_deployment_movement", new SandboxServerDeploymentStep { unit_id = deploymentMovingUnit,
                formation = deploymentMovingFormation });
        }

        /// <summary>兼容旧版共用测试兵营的名称，三类新兵营沿用协作者的命名。</summary>
        /// <param name="camp">当前角色投影中的兵营。</param>
        /// <returns>页面和地图标签使用的建筑名称。</returns>
        private static string BarracksDisplayName(SandboxBarracksData camp) =>
            camp.id == "red-camp" || camp.id == "blue-camp" ? "既有共用兵营" : DemoSandboxViewSource.BarracksName(camp.type);
    }
}
