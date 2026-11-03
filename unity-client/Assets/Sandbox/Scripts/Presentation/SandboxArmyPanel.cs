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
        private Button armyCampDeploy;
        private void OpenArmy()
        {
            CancelArmyCommand(); ClosePanel(); CloseArmy(); PausePlayback();
            armySide = role == SandboxRole.Host ? SandboxRole.Red : role;
            ReadArmyQuantities(); RenderArmy();
        }
        private void ReadArmyQuantities()
        {
            for (int i = 0; i < 3; i++) armyQuantities[i] = Mathf.Clamp(armyQuantities[i], 1, Math.Max(1, source.ArmyCapacityRemaining));
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
            AddButton(header, "×", CloseArmy, "army-close");
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
            AddText(summary, "红蓝双方共享名额", "army-footnote");
            armyCampDeploy = AddButton(footer, "集结区部署", ApplyArmy, "army-primary");
            UpdateArmyTotal();
        }
        private void BuildArmyCard(VisualElement parent, int index)
        {
            var type = (SandboxUnitType)index;
            var card = new VisualElement(); card.AddToClassList("army-card"); parent.Add(card);
            var stage = new VisualElement(); stage.AddToClassList("army-stage"); card.Add(stage);
            var image = new Image { image = Resources.Load<Texture2D>("Army/" + DemoSandboxViewSource.ArmyKey(type) + "-card"), scaleMode = ScaleMode.ScaleToFit };
            image.AddToClassList("army-model"); stage.Add(image);
            var details = new VisualElement(); details.AddToClassList("army-card-details"); card.Add(details);
            AddText(details, DemoSandboxViewSource.ArmyName(type), "army-card-title");
            AddText(details, "已部署 " + source.Current.units.Count(unit => unit.side == armySide && unit.type == type), "army-current");
            var quantity = new VisualElement(); quantity.AddToClassList("army-quantity"); details.Add(quantity);
            AddText(quantity, "新增", "army-number-hint");
            Button minus = null, plus = null; Label number = null;
            Action update = () => { number.text = armyQuantities[index].ToString(); minus.SetEnabled(source.ArmyCapacityRemaining > 0 && armyQuantities[index] > 1); plus.SetEnabled(armyQuantities[index] < source.ArmyCapacityRemaining); UpdateArmyTotal(); };
            minus = AddButton(quantity, "−", () => { armyQuantities[index]--; update(); }, "army-stepper");
            var value = new VisualElement(); value.AddToClassList("army-quantity-value"); quantity.Add(value);
            number = AddText(value, armyQuantities[index].ToString(), "army-number");
            plus = AddButton(quantity, "+", () => { armyQuantities[index]++; update(); }, "army-stepper");
            minus.SetEnabled(source.ArmyCapacityRemaining > 0 && armyQuantities[index] > 1); plus.SetEnabled(armyQuantities[index] < source.ArmyCapacityRemaining);
            var deploy = AddButton(details, source.ArmyCapacityRemaining == 0 ? "名额已满" : "选点部署", () => BeginDeploy(type, armyQuantities[index]), "army-deploy");
            deploy.SetEnabled(source.ArmyCapacityRemaining > 0);
        }
        private void UpdateArmyTotal()
        {
            if (armyTotal != null) armyTotal.text = "已部署 " + source.Current.units.Length + " / " + DemoSandboxViewSource.MaxArmyUnits + " · 剩余 " + source.ArmyCapacityRemaining;
            if (armyCampDeploy != null)
            {
                int quantity = armyQuantities.Sum();
                armyCampDeploy.text = "集结区部署 " + quantity + " 个";
                armyCampDeploy.SetEnabled(quantity <= source.ArmyCapacityRemaining);
            }
        }
        private void CloseArmy() { armyOverlay?.RemoveFromHierarchy(); armyOverlay = null; armyTotal = null; armyCampDeploy = null; }
        private void ApplyArmy()
        {
            if (!source.TryConfigureArmy(role, armySide, armyQuantities, out string message)) { Notify(message); return; }
            CloseArmy(); RefreshProjection(); Notify(message);
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
            if (count > source.ArmyCapacityRemaining) { Notify("地图名额不足，红蓝双方合计最多 10 个单位"); return; }
            pendingArmyType = type; pendingArmyQuantity = count; pendingArmySide = armySide; pendingMoveId = null;
            CloseArmy(); ClosePanel();
            ShowArmyCommand("追加部署 " + DemoSandboxViewSource.ArmyName(type) + " × " + count, "单击地图布置编队 · 拖动平移 · 滚轮缩放 · Esc 取消");
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
        private void CancelArmyCommand() { pendingArmyType = null; pendingMoveId = null; armyCommand?.RemoveFromHierarchy(); armyCommand = null; }
        private void HandleArmyMapClick(Vector3 point)
        {
            if (!world.TryBusinessPosition(point, out Vector2 position)) { Notify("请选择地图范围内的位置"); return; }
            if (pendingArmyType.HasValue)
            {
                SandboxUnitType type = pendingArmyType.Value;
                for (int i = 0; i < pendingArmyQuantity; i++)
                {
                    float x = position.x + (i % 4 - Math.Min(pendingArmyQuantity, 4) / 2f + 0.5f) * 2.1f, z = position.y + i / 4 * 2.1f;
                    if (!world.CanPlace(x, z, type)) { Notify("编队位置不可用，请避开水面、建筑与地图边界"); return; }
                }
                var previousIds = new HashSet<string>(source.Current.units.Select(unit => unit.id));
                if (!source.TryDeployArmy(role, pendingArmySide, type, pendingArmyQuantity, position.x, position.y, out string message)) { Notify(message); return; }
                string first = source.Current.units.First(unit => !previousIds.Contains(unit.id)).id;
                CancelArmyCommand(); selectedId = first; RefreshProjection(); cameraRig.Locate(first); Notify(message);
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
