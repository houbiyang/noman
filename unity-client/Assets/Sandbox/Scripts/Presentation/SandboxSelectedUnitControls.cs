using System;
using System.Linq;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>显式选中单位的底部指令台；仅复用授权命令，不在前端修改任何战斗数值。</summary>
    public sealed partial class SandboxHudController
    {
        private VisualElement selectedUnitControls;
        private string controlledUnitId;

        private void CloseSelectedUnitControls()
        {
            controlledUnitId = null;
            selectedUnitControls?.RemoveFromHierarchy();
            selectedUnitControls = null;
        }

        private void OpenSelectedUnitControls(SandboxUnitData unit)
        {
            ClosePanel(); CloseArmy(); CancelArmyCommand();
            controlledUnitId = unit.id;
            RefreshSelectedUnitControls();
        }

        private void RefreshSelectedUnitControls()
        {
            if (controlledUnitId == null || root == null) return;
            var unit = projection?.units.FirstOrDefault(item => item.id == controlledUnitId);
            if (unit == null) { CloseSelectedUnitControls(); return; }
            if (selectedUnitControls == null)
            {
                selectedUnitControls = new ScrollView(ScrollViewMode.Vertical) { name = "selected-unit-controls" };
                selectedUnitControls.AddToClassList("selected-unit-controls");
                selectedUnitControls.AddToClassList("ui-blocker");
                var style = UnityEngine.Resources.Load<StyleSheet>("Sandbox/SandboxUnitControls");
                if (style != null) selectedUnitControls.styleSheets.Add(style);
                root.Add(selectedUnitControls);
            }
            selectedUnitControls.Clear();
            var heading = new VisualElement(); heading.AddToClassList("selected-unit-heading"); selectedUnitControls.Add(heading);
            AddText(heading, unit.id + " · " + unit.name, "army-unit-title");
            AddButton(heading, "收起", () => { CancelArmyCommand(); CloseSelectedUnitControls(); }, "army-unit-action");
            AddText(selectedUnitControls, UnitResourceText(unit), "selected-unit-resources");
            if (ManualBattle != null)
            {
                AddManualUnitControls(selectedUnitControls, unit);
                return;
            }
            bool own = role == SandboxRole.Host || unit.side == role;
            if (!own)
            {
                AddText(selectedUnitControls, "敌方 · 仅展示当前授权情报，不能下达指令。", "army-footnote");
                AddButton(selectedUnitControls, "跟随观察", () => cameraRig.Follow(unit.id), "army-unit-action");
                return;
            }
            bool synchronized = !serverMode || (serverClient.Healthy && !serverClient.Busy && !serverClient.HasPending);
            bool alive = !serverMode || unit.health > 0 || !unit.hasCombatResources;
            var actions = new VisualElement(); actions.AddToClassList("selected-unit-actions"); selectedUnitControls.Add(actions);
            string restriction = ArmyMoveRestriction();
            var move = AddButton(actions, "移动到…", () => { movementMode = "move"; BeginMove(unit.id); }, "army-unit-action");
            move.SetEnabled(synchronized && alive && restriction.Length == 0);
            move.tooltip = restriction;
            if (serverMode && serverClient.View?.spatial_test == true)
            {
                AddButton(actions, "停止移动", () => SubmitMovement(unit.id, "stop_move"), "army-unit-action")
                    .SetEnabled(synchronized && alive && restriction.Length == 0);
                AddButton(actions, "返回兵营", () => SubmitMovement(unit.id, "return_to_barracks"), "army-unit-action")
                    .SetEnabled(synchronized && alive && restriction.Length == 0);
            }
            var interactions = new VisualElement(); selectedUnitControls.Add(interactions);
            AddUnitInteractionActions(interactions, unit);
            interactions.SetEnabled(synchronized && alive);
            var observation = new VisualElement(); observation.AddToClassList("selected-unit-actions"); selectedUnitControls.Add(observation);
            AddButton(observation, "跟随观察", () => cameraRig.Follow(unit.id), "army-unit-action");
            AddButton(observation, "部队管理", () => { CloseSelectedUnitControls(); OpenArmyManagement(unit.side, unit.type); }, "army-unit-action");
            AddText(selectedUnitControls, !serverMode ? "离线预览 · 不产生数据库战斗裁决。"
                : serverClient.View?.unit_interactions?.enabled == true
                    ? "旧版单位交互 · 指令即时登记，推进后执行；不是总册手动双提交模式。"
                    : "部署阶段可试走；总册手动战斗尚待完整执行器验收，不会调用旧裁决冒充新模式。", "army-footnote");
            // 不BringToFront：后来打开的设置、目标选择及确认遮罩必须覆盖整个指令台。
        }
    }
}
