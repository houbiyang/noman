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
        private readonly int[] armyQuantities = { 1, 1, 1 };
        private SandboxUnitType? pendingArmyType;
        private int pendingArmyQuantity;
        private SandboxRole pendingArmySide;
        private string pendingMoveId;
        private Label armyTotal;

        private void OpenArmy()
        {
            CancelArmyCommand(); ClosePanel(); CloseArmy(); PausePlayback();
            armySide = role == SandboxRole.Host ? SandboxRole.Red : role;
            ReadArmyQuantities(); RenderArmy();
        }
        private void ReadArmyQuantities()
        {
            for (int i = 0; i < 3; i++) armyQuantities[i] = Mathf.Clamp(armyQuantities[i], 1, 10);
        }
        private void RenderArmy()
        {
            CloseArmy();
            armyOverlay = new VisualElement { name = "armyOverlay" }; armyOverlay.AddToClassList("army-panel"); armyOverlay.AddToClassList("ui-blocker");
            root.Add(armyOverlay);
            var panel = armyOverlay;
            var header = new VisualElement(); header.AddToClassList("army-header"); panel.Add(header);
            var titles = new VisualElement(); titles.style.flexGrow = 1; header.Add(titles);
            AddText(titles, "军队配置", "army-title");
            AddButton(header, "×", () => { CloseArmy(); CancelArmyCommand(); }, "army-close");
            var toolbar = new VisualElement(); toolbar.AddToClassList("army-toolbar"); panel.Add(toolbar);
            AddText(toolbar, SideLabel(armySide) + "编队", "army-side");
            if (role == SandboxRole.Host)
                AddButton(toolbar, armySide == SandboxRole.Red ? "切换为蓝方" : "切换为红方", () => { armySide = armySide == SandboxRole.Red ? SandboxRole.Blue : SandboxRole.Red; ReadArmyQuantities(); RenderArmy(); }, "army-switch");
            var spacer = new VisualElement(); spacer.style.flexGrow = 1; toolbar.Add(spacer);
            AddButton(toolbar, "管理单位", () => ShowPanel("units"), "army-switch");
            var scroll = new ScrollView(ScrollViewMode.Vertical); scroll.AddToClassList("army-scroll"); panel.Add(scroll);
            var cards = new VisualElement(); cards.AddToClassList("army-cards"); scroll.Add(cards);
            for (int i = 0; i < 3; i++) BuildArmyCard(cards, i);
            var footer = new VisualElement(); footer.AddToClassList("army-footer"); panel.Add(footer);
            var summary = new VisualElement(); summary.style.flexGrow = 1; footer.Add(summary);
            armyTotal = AddText(summary, "", "army-total");
            AddText(summary, "点击兵种图标 → 点击地图放置兵营", "army-footnote");
            AddText(summary, "兵营不占名额 · 红蓝单位合计最多 10 个", "army-footnote");
            UpdateArmyTotal();
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
            stage.RegisterCallback<ClickEvent>(evt => { BeginDeploy(type, armyQuantities[index]); evt.StopPropagation(); });
            var details = new VisualElement(); details.AddToClassList("army-card-details"); card.Add(details);
            AddText(details, DemoSandboxViewSource.BarracksName(type), "army-card-title");
            AddText(details, DemoSandboxViewSource.ArmyName(type), "army-troop-name");
            AddText(details, (camp == null ? "未建造" : "已建造") + " · 单位 " + source.Current.units.Count(unit => unit.side == armySide && unit.type == type), "army-current");
            var quantity = new VisualElement(); quantity.AddToClassList("army-quantity"); details.Add(quantity);
            AddText(quantity, "出兵", "army-number-hint");
            Button minus = null, plus = null, deploy = null; Label number = null;
            Action update = () =>
            {
                if (pendingArmyType == type && pendingArmySide == armySide) pendingArmyQuantity = armyQuantities[index];
                number.text = armyQuantities[index].ToString(); minus.SetEnabled(armyQuantities[index] > 1); plus.SetEnabled(armyQuantities[index] < 10);
                if (deploy != null) { deploy.text = camp == null ? "选点建造兵营" : "生成 " + armyQuantities[index] + " 个单位"; deploy.SetEnabled(camp == null || armyQuantities[index] <= source.ArmyCapacityRemaining); }
            };
            minus = AddButton(quantity, "−", () => { armyQuantities[index]--; update(); }, "army-stepper");
            var value = new VisualElement(); value.AddToClassList("army-quantity-value"); quantity.Add(value);
            number = AddText(value, armyQuantities[index].ToString(), "army-number");
            plus = AddButton(quantity, "+", () => { armyQuantities[index]++; update(); }, "army-stepper");
            deploy = AddButton(details, "", () => { if (camp == null) BeginDeploy(type, armyQuantities[index]); else SpawnArmy(type, armyQuantities[index]); }, "army-deploy");
            if (camp != null) AddButton(details, "重新选址", () => BeginDeploy(type, armyQuantities[index]), "army-relocate");
            update();
        }
        private void UpdateArmyTotal()
        {
            if (armyTotal != null) armyTotal.text = "单位 " + source.Current.units.Length + " / " + DemoSandboxViewSource.MaxArmyUnits + " · 兵营 " + source.Current.barracks.Length;
        }
        private void CloseArmy() { armyOverlay?.RemoveFromHierarchy(); armyOverlay = null; armyTotal = null; }
        private void SpawnArmy(SandboxUnitType type, int count)
        {
            var previousIds = new HashSet<string>(source.Current.units.Select(unit => unit.id));
            if (!source.TrySpawnArmy(role, armySide, type, count, out string message)) { Notify(message); return; }
            world.AnimateBarracksSpawns(source.Current.units.Where(unit => !previousIds.Contains(unit.id)).Select(unit => unit.id));
            CancelArmyCommand(); RefreshProjection(); RenderArmy(); Notify(message);
        }
        private void OpenDeleteArmyUnitModal(SandboxUnitData unit)
        {
            PausePlayback();
            SetText("modalTitle", "删除已部署单位？");
            SetText("modalMessage", unit.id + " · " + unit.name + "\n确认后移除地图模型、移动路径和当前部署，释放 1 个名额。其他单位保持不变，可重新选择数量部署。");
            SetText("confirmModalButton", "确认删除单位");
            modalAction = () =>
            {
                if (source.TryDeleteArmyUnit(role, unit.id, out string message))
                {
                    if (selectedId == unit.id && cameraRig.IsFollowing) cameraRig.ReturnToOverview();
                    CancelArmyCommand();
                    RefreshProjection();
                    drawerContent.Clear();
                    BuildUnitsPanel();
                }
                Notify(message);
            };
            root.Q("modalBackdrop").RemoveFromClassList("hidden");
            root.Q<Button>("cancelModalButton").Focus();
        }
        private void BeginDeploy(SandboxUnitType type, int count)
        {
            pendingArmyType = type; pendingArmyQuantity = count; pendingArmySide = armySide; pendingMoveId = null;
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
                var previousIds = new HashSet<string>(source.Current.units.Select(unit => unit.id));
                if (!source.TryPlaceBarracks(role, pendingArmySide, type, pendingArmyQuantity, position.x, position.y, out string message)) { Notify(message); return; }
                var added = source.Current.units.Where(unit => !previousIds.Contains(unit.id)).Select(unit => unit.id).ToArray();
                world.AnimateBarracksSpawns(added);
                CancelArmyCommand(); RefreshProjection(); RenderArmy(); Notify(message);
            }
            else
            {
                string id = pendingMoveId ?? selectedId;
                SandboxUnitData unit = projection.units.FirstOrDefault(candidate => candidate.id == id);
                if (unit == null) { Notify("请先选择单位"); return; }
                if (!world.CanMove(id, position.x, position.y, unit.type)) { Notify("目标不可通行或没有可达路径，请另选位置"); return; }
                if (source.TryMoveArmyUnit(role, id, position.x, position.y, out string message))
                { CancelArmyCommand(); RefreshProjection(); world.Select(id); Notify(message); }
                else Notify(message);
            }
        }
    }
}
