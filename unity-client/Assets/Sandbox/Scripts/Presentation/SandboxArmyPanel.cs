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
        private SandboxRole armySide;
        private SandboxUnitType? managedArmyType;
        private SandboxUnitType? pendingArmyType;
        private SandboxRole pendingArmySide;
        private string pendingMoveId;

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
            AddText(summary, "点击兵种图标 → 点击地图放置兵营", "army-footnote");
            AddText(summary, "单位由后续仿真指令从对应兵营出发", "army-footnote");
        }
        private void BuildArmyCard(VisualElement parent, int index)
        {
            var type = (SandboxUnitType)index;
            var camp = source.Current.barracks.FirstOrDefault(item => item.side == armySide && item.type == type);
            var card = new VisualElement(); card.AddToClassList("army-card"); parent.Add(card);
            if (pendingArmyType == type && pendingArmySide == armySide) card.AddToClassList("army-card-selected");
            var stage = new VisualElement(); stage.AddToClassList("army-stage"); card.Add(stage);
            var image = new Image { image = Resources.Load<Texture2D>("Army/" + DemoSandboxViewSource.ArmyKey(type) + "-barracks-card"), scaleMode = ScaleMode.ScaleToFit };
            image.AddToClassList("army-model"); stage.Add(image);
            stage.RegisterCallback<ClickEvent>(evt => { BeginDeploy(type); evt.StopPropagation(); });
            var details = new VisualElement(); details.AddToClassList("army-card-details"); card.Add(details);
            AddText(details, DemoSandboxViewSource.BarracksName(type), "army-card-title");
            AddText(details, DemoSandboxViewSource.ArmyName(type), "army-troop-name");
            AddText(details, camp == null ? "尚未建造" : "已建造 · 等待仿真指令", "army-current");
            AddButton(details, camp == null ? "选点建造兵营" : "重新选址", () => BeginDeploy(type), "army-deploy");
            AddButton(details, "查看兵种 / 管理单位", () => OpenArmyManagement(armySide, type), "army-manage");
        }
        private void CloseArmy()
        {
            armyOverlay?.RemoveFromHierarchy(); armyOverlay = null;
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
            string campId = DemoSandboxViewSource.BarracksId(armySide, type);
            bool hasCamp = projection.barracks.Any(camp => camp.id == campId);
            world.SelectBarracks(hasCamp ? campId : null);
            armyOverlay.name = "barracksManagement";
            var header = new VisualElement(); header.AddToClassList("army-header"); armyOverlay.Add(header);
            var titles = new VisualElement(); titles.style.flexGrow = 1; header.Add(titles);
            AddText(titles, DemoSandboxViewSource.BarracksName(type), "army-manager-title");
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
            var summary = new VisualElement(); summary.name = "barracksSummary"; summary.AddToClassList("army-summary"); body.Add(summary);
            var search = new TextField("查找单位") { value = unitSearch, maxLength = 60 }; search.AddToClassList("field"); body.Add(search);
            var list = new VisualElement { name = "barracksUnits" }; body.Add(list);
            Action render = null;
            render = () =>
            {
                summary.Clear(); list.Clear();
                var units = SandboxArmyLayout.UnitsForBarracks(projection.units, armySide, type);
                AddText(summary, "兵种数量  " + units.Length + " 个", "army-summary-count");
                var known = units.Where(unit => unit.maxHealth > 0).ToArray();
                AddText(summary, known.Length == 0 ? "血量  待接入仿真数据" :
                    "已知血量  " + known.Sum(unit => (long)unit.health) + " / " + known.Sum(unit => (long)unit.maxHealth) + " · " + known.Length + "/" + units.Length + " 单位已上报", "army-current");
                AddText(summary, "当前任务 · " + projection.taskType, "army-current");
                foreach (var state in units.GroupBy(unit => unit.status))
                    AddText(summary, state.Key + "  × " + state.Count(), "army-status-line");
                AddText(summary, hasCamp ? "该阵营同兵种单位，含初始演示单位" : "尚未建造兵营 · 可先管理已有同兵种单位", "army-footnote");
                var filtered = units.Where(unit => (unit.id + unit.name).IndexOf(unitSearch, StringComparison.OrdinalIgnoreCase) >= 0).ToArray();
                if (filtered.Length == 0) AddText(list, units.Length == 0 ? "暂无该兵种单位；等待后续仿真出兵。" : "没有匹配的单位。", "army-empty");
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
                    var actions = new VisualElement(); actions.AddToClassList("army-unit-actions"); card.Add(actions);
                    AddButton(actions, "选择定位", () => { selectedId = unit.id; world.Select(unit.id); cameraRig.Locate(unit.id, UnitLocateViewport()); RefreshProjection(); }, "army-unit-action");
                    AddButton(actions, "地图移动", () => BeginMove(unit.id), "army-unit-action");
                    AddButton(actions, "跟随观察", () => { selectedId = unit.id; world.Select(unit.id); cameraRig.Follow(unit.id); RefreshProjection(); }, "army-unit-action");
                    AddButton(actions, "删除单位", () => OpenDeleteArmyUnitModal(unit), "army-unit-delete");
                }
            };
            search.RegisterValueChangedCallback(change => { unitSearch = change.newValue; render(); }); render();
            var footer = new VisualElement(); footer.AddToClassList("army-footer"); armyOverlay.Add(footer);
            AddButton(footer, hasCamp ? "重新选址兵营" : "选点建造兵营", () => BeginDeploy(type), "army-deploy");
            AddText(footer, "本机演示数据 · 血量未知不推算 · 无人工出兵", "army-footnote");
            header.Q<Button>().Focus();
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
        private void BeginDeploy(SandboxUnitType type)
        {
            managedArmyType = null;
            pendingArmyType = type; pendingArmySide = armySide; pendingMoveId = null;
            ClosePanel(); RenderArmy();
            ShowArmyCommand("放置" + DemoSandboxViewSource.BarracksName(type), "点击空地建造 · 拖动平移 · Esc 取消");
        }
        private void BeginMove(string id)
        {
            PausePlayback(); pendingMoveId = id; selectedId = id; pendingArmyType = null; world.Select(id);
            ClosePanel(); CloseArmy();
            ShowArmyCommand("移动 " + id, "单击目标位置 · 地面单位自动寻路 · Esc 取消");
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
            pendingArmyType = null; pendingMoveId = null; armyCommand?.RemoveFromHierarchy(); armyCommand = null;
            armyOverlay?.Query<VisualElement>(className: "army-card-selected").ForEach(card => card.RemoveFromClassList("army-card-selected"));
        }
        private void HandleArmyMapClick(Vector3 point)
        {
            if (!world.TryBusinessPosition(point, out Vector2 position)) { Notify("请选择地图范围内的位置"); return; }
            if (pendingArmyType.HasValue)
            {
                SandboxUnitType type = pendingArmyType.Value;
                if (!world.CanPlaceBarracks(type, pendingArmySide, position.x, position.y, out string placementMessage)) { Notify(placementMessage); return; }
                if (!source.TryPlaceBarracks(role, pendingArmySide, type, position.x, position.y, out string message)) { Notify(message); return; }
                CancelArmyCommand(); RefreshProjection(); RenderArmy(); Notify(message);
            }
            else
            {
                string id = pendingMoveId ?? selectedId;
                SandboxUnitData unit = projection.units.FirstOrDefault(candidate => candidate.id == id);
                if (unit == null) { Notify("请先选择单位"); return; }
                if (!world.CanMove(id, position.x, position.y, unit.type)) { Notify("目标不可通行或没有可达路径，请另选位置"); return; }
                if (source.TryMoveArmyUnit(role, id, position.x, position.y, out string message))
                { CancelArmyCommand(); RefreshProjection(); world.Select(id); OpenArmyManagement(unit.side, unit.type); Notify(message); }
                else Notify(message);
            }
        }
    }
}
