using System;
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
        private readonly string[] armyDescriptions = {
            "空中侦察 · 光电感知\n四旋翼平台，适合快速观察与地图巡航。",
            "地面机动 · 履带底盘\n低重心装甲平台，沿可通行地形移动。",
            "运输保障 · 六轮驱动\n模块化载荷平台，承担物资运输与保障。"
        };

        private void OpenArmy()
        {
            CancelArmyCommand(); ClosePanel(); CloseArmy(); PausePlayback();
            armySide = role == SandboxRole.Host ? SandboxRole.Red : role;
            ReadArmyQuantities(); RenderArmy();
        }
        private void ReadArmyQuantities()
        {
            for (int i = 0; i < 3; i++) armyQuantities[i] = source.Current.units.Count(unit => unit.side == armySide && (int)unit.type == i);
        }
        private void RenderArmy()
        {
            CloseArmy();
            armyOverlay = new VisualElement { name = "armyOverlay" }; armyOverlay.AddToClassList("army-overlay"); armyOverlay.AddToClassList("ui-blocker");
            root.Add(armyOverlay);
            var panel = new VisualElement(); panel.AddToClassList("army-panel"); armyOverlay.Add(panel);
            var header = new VisualElement(); header.AddToClassList("army-header"); panel.Add(header);
            var titles = new VisualElement(); titles.style.flexGrow = 1; header.Add(titles);
            AddText(titles, "FORCE MANAGEMENT   /   03 UNIT TYPES", "army-eyebrow");
            AddText(titles, "军队配置", "army-title");
            AddText(titles, "选择装备，组织你的无人作战编队", "army-subtitle");
            AddButton(header, "×", CloseArmy, "army-close");
            var toolbar = new VisualElement(); toolbar.AddToClassList("army-toolbar"); panel.Add(toolbar);
            AddText(toolbar, SideLabel(armySide) + "编队", "army-side");
            if (role == SandboxRole.Host)
                AddButton(toolbar, armySide == SandboxRole.Red ? "切换为蓝方" : "切换为红方", () => { armySide = armySide == SandboxRole.Red ? SandboxRole.Blue : SandboxRole.Red; ReadArmyQuantities(); RenderArmy(); }, "army-switch");
            var capacity = AddText(toolbar, "每类 1–10 个  /  每方最多 30 个", "army-capacity"); capacity.style.flexGrow = 1;
            var scroll = new ScrollView(ScrollViewMode.Vertical); scroll.AddToClassList("army-scroll"); panel.Add(scroll);
            var cards = new VisualElement(); cards.AddToClassList("army-cards"); scroll.Add(cards);
            for (int i = 0; i < 3; i++) BuildArmyCard(cards, i);
            var footer = new VisualElement(); footer.AddToClassList("army-footer"); panel.Add(footer);
            var summary = new VisualElement(); summary.style.flexGrow = 1; footer.Add(summary);
            armyTotal = AddText(summary, "", "army-total"); UpdateArmyTotal();
            AddText(summary, "应用数量后在集结区补充单位；选点部署可重新布置同类编队。", "army-footnote");
            AddButton(footer, "返回沙盘", CloseArmy, "army-secondary");
            AddButton(footer, "应用编队  →", ApplyArmy, "army-primary");
        }
        private void BuildArmyCard(VisualElement parent, int index)
        {
            var type = (SandboxUnitType)index;
            var card = new VisualElement(); card.AddToClassList("army-card"); parent.Add(card);
            var top = new VisualElement(); top.AddToClassList("army-card-top"); card.Add(top);
            AddText(top, "0" + (index + 1) + "  /  " + new[] { "RECON", "GROUND", "SUPPORT" }[index], "army-card-tag");
            AddText(card, DemoSandboxViewSource.ArmyName(type), "army-card-title");
            var stage = new VisualElement(); stage.AddToClassList("army-stage"); card.Add(stage);
            var image = new Image { image = Resources.Load<Texture2D>("Army/" + DemoSandboxViewSource.ArmyKey(type) + "-card"), scaleMode = ScaleMode.ScaleToFit };
            image.AddToClassList("army-model"); stage.Add(image);
            AddText(stage, new[] { "四旋翼平台", "履带底盘", "6 × 6 运输平台" }[index], "army-model-caption");
            AddText(card, armyDescriptions[index], "army-description");
            AddText(card, "当前编制  " + source.Current.units.Count(unit => unit.side == armySide && unit.type == type).ToString("00") + "  个单位", "army-current");
            var quantity = new VisualElement(); quantity.AddToClassList("army-quantity"); card.Add(quantity);
            Button minus = null, plus = null; Label number = null;
            Action update = () => { number.text = armyQuantities[index].ToString("00"); minus.SetEnabled(armyQuantities[index] > 1); plus.SetEnabled(armyQuantities[index] < 10); UpdateArmyTotal(); };
            minus = AddButton(quantity, "−", () => { armyQuantities[index]--; update(); }, "army-stepper");
            var value = new VisualElement(); value.AddToClassList("army-quantity-value"); quantity.Add(value);
            number = AddText(value, armyQuantities[index].ToString("00"), "army-number");
            AddText(value, "部署数量 / 10", "army-number-hint");
            plus = AddButton(quantity, "+", () => { armyQuantities[index]++; update(); }, "army-stepper");
            minus.SetEnabled(armyQuantities[index] > 1); plus.SetEnabled(armyQuantities[index] < 10);
            AddButton(card, "选点部署  ↗", () => BeginDeploy(type, armyQuantities[index]), "army-deploy");
        }
        private void UpdateArmyTotal() { if (armyTotal != null) armyTotal.text = "编队总数   " + armyQuantities.Sum().ToString("00") + " / 30"; }
        private void CloseArmy() { armyOverlay?.RemoveFromHierarchy(); armyOverlay = null; armyTotal = null; }
        private void ApplyArmy()
        {
            if (!source.TryConfigureArmy(role, armySide, armyQuantities, out string message)) { Notify(message); return; }
            CloseArmy(); RefreshProjection(); Notify(message);
        }
        private void BeginDeploy(SandboxUnitType type, int count)
        {
            pendingArmyType = type; pendingArmyQuantity = count; pendingArmySide = armySide; pendingMoveId = null;
            CloseArmy(); ClosePanel();
            ShowArmyCommand("部署 " + DemoSandboxViewSource.ArmyName(type) + " × " + count, "单击地图布置编队 · 拖动平移 · 滚轮缩放 · Esc 取消");
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
                if (!source.TryDeployArmy(role, pendingArmySide, type, pendingArmyQuantity, position.x, position.y, out string message)) { Notify(message); return; }
                world.RelocateOnRefresh(source.Current.units.Where(unit => unit.side == pendingArmySide && unit.type == type).Select(unit => unit.id));
                string first = source.Current.units.First(unit => unit.side == pendingArmySide && unit.type == type).id;
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
