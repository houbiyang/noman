using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>单位任务只提交意图，数值变化以服务器数据库快照为准。</summary>
    public sealed partial class SandboxHudController
    {
        private string pendingReconId;
        private VisualElement unitHover;
        private Label unitHoverTitle, unitHoverBody;

        private string UnitResourceText(SandboxUnitData unit)
        {
            string health = unit.maxHealth > 0 ? unit.health.ToString("0.#") + " / " + unit.maxHealth.ToString("0.#") : "未提供";
            string speed = unit.speedMPerMinute > 0 ? unit.speedMPerMinute.ToString("0.#") + " 米/仿真分钟（基础）" + (unit.hasCombatResources ? "\n最近已结算移速 " + unit.currentSpeedMPerMinute.ToString("0.#") + " 米/仿真分钟" : "") : "未提供";
            string resources = !unit.hasCombatResources ? "弹药 / 能源 · 未接入单位交互"
                : "能源 " + unit.energy.ToString("0.#") + " / 100 · " + (unit.maxAmmo > 0 ? "弹药 " + unit.ammo.ToString("0") + " / " + unit.maxAmmo.ToString("0") : "无武器弹药");
            return "血量 " + health + "\n移速 " + speed + "\n" + resources;
        }

        private void AddUnitInteractionSetup(VisualElement body)
        {
            if (!serverMode || serverClient.View?.spatial_test != true) return;
            if (serverClient.View.unit_interactions?.enabled == true)
            {
                AddText(body, "单位交互已启用 · 1步=1仿真分钟，内含12次五秒结算 · 移速随地形与移动方式调整。", "army-footnote");
                AddText(body, "测试几何：视线及服务接触采用保守包络，尚非v1.1正式地图验收。", "army-footnote");
                return;
            }
            AddText(body, "旧对局尚未启用攻击、侦察与补充。主持人在准备/暂停时可启用；不会重置已有位置和资源。", "army-footnote");
            if (role == SandboxRole.Host)
                AddButton(body, "启用单位交互（v1.1数值）", () => SendServerCommand("enable_unit_interactions"), "army-spawn-button")
                    .SetEnabled(serverClient.View.clock.phase == "preparing" || serverClient.View.clock.phase == "paused");
        }

        private void AddUnitInteractionActions(VisualElement card, SandboxUnitData unit)
        {
            if (ManualBattle != null) { AddManualUnitControls(card, unit); return; }
            if (!serverMode || serverClient.View.unit_interactions?.enabled != true) return;
            var actions = new VisualElement(); actions.AddToClassList("army-unit-actions"); card.Add(actions);
            bool enabled = (serverClient.View.clock.phase == "paused" || serverClient.View.clock.phase == "running") && unit.health > 0;
            if (unit.type == SandboxUnitType.Recon)
            {
                AddButton(actions, "侦察目标", () => OpenUnitTargetPanel(unit, "recon"), "army-unit-action").SetEnabled(enabled && unit.energy > 0);
                AddButton(actions, "合法停靠", () => SubmitUnitOrder(unit.id, "dock"), "army-unit-action").SetEnabled(enabled);
            }
            if (unit.type == SandboxUnitType.Ground)
                AddButton(actions, "攻击目标", () => OpenUnitTargetPanel(unit, "attack"), "army-unit-action").SetEnabled(enabled && unit.energy > 0);
            if (unit.type == SandboxUnitType.Support)
                AddButton(actions, "补充单位 / 维修", () => OpenUnitTargetPanel(unit, "resupply"), "army-unit-action").SetEnabled(enabled && unit.energy > 0);
            AddButton(actions, "兵营补充", () => SubmitUnitOrder(unit.id, "barracks"), "army-unit-action").SetEnabled(enabled);
            AddButton(actions, "取消任务", () => SubmitUnitOrder(unit.id, "stop"), "army-unit-action").SetEnabled(enabled);
        }

        private void OpenUnitTargetPanel(SandboxUnitData unit, string action)
        {
            connectionOverlay?.RemoveFromHierarchy();
            connectionOverlay = new VisualElement(); connectionOverlay.AddToClassList("modal-backdrop"); connectionOverlay.AddToClassList("ui-blocker"); root.Add(connectionOverlay);
            var card = new VisualElement(); card.AddToClassList("modal-card"); connectionOverlay.Add(card);
            bool service = action == "resupply";
            AddText(card, unit.id + " · " + (service ? "补充单位 / 维修" : action == "attack" ? "攻击目标" : "侦察目标"), "drawer-title");
            var candidates = serverClient.View.units.Concat(serverClient.View.contacts ?? Array.Empty<SandboxServerUnit>())
                .GroupBy(item => item.unit_id).Select(group => group.First())
                .Where(item => item.unit_id != unit.id && item.durability > 0 && (service ? item.side == ServerSide(unit.side) : item.side != ServerSide(unit.side))).ToArray();
            var labels = candidates.Select(item => item.unit_id + " · " + item.name).ToList();
            var targets = new DropdownField("目标单位", labels.Count == 0 ? new List<string> { "没有合法候选单位" } : labels, 0);
            targets.AddToClassList("field"); card.Add(targets);
            var resource = new DropdownField("补充项目", new List<string> { "能源", "弹药", "维修耐久" }, 0);
            if (service) { resource.AddToClassList("field"); card.Add(resource); }
            AddText(card, service ? "供需双方全步停止，距离≤100米且地表可达；每5秒补2点，扣除保障库存。飞行无人机须先合法停靠。"
                : action == "attack" ? "仅当前可见敌军可指定；600米地面/800米空中射程，独立视线校验；每10秒耗1弹造成16伤害，移动开火伤害×0.6。不会自动追击。"
                : "当前观察自动共享给本方。也可在地图指定侦察区域；目标失视后不会追踪其隐藏坐标。", "body-text");
            var submit = AddButton(card, "下达任务", () => {
                var target = candidates[targets.index];
                string kind = service && resource.index == 2 ? "repair" : action;
                SubmitUnitOrder(unit.id, kind, target, resource.index == 1 ? "ammo" : "energy");
                connectionOverlay.RemoveFromHierarchy(); connectionOverlay = null;
            }, "primary-button"); submit.SetEnabled(candidates.Length > 0);
            if (action == "recon") AddButton(card, "在地图指定侦察区域", () => {
                connectionOverlay.RemoveFromHierarchy(); connectionOverlay = null; CancelArmyCommand(); CloseArmy();
                pendingReconId = unit.id; ShowArmyCommand("侦察区域 · " + unit.id, "单击地图下达侦察任务；Esc取消。移动和发现均由后端结算。");
            }, "primary-button");
            AddButton(card, "取消", () => { connectionOverlay.RemoveFromHierarchy(); connectionOverlay = null; });
        }

        private void SubmitUnitOrder(string id, string action, SandboxServerUnit target = null, string resource = "energy")
        {
            if (ManualBattle != null) { Notify("请使用手动战斗草稿指令台"); return; }
            var unit = serverClient.View.units.FirstOrDefault(item => item.unit_id == id);
            if (unit == null) { Notify("单位已不在当前授权视图，请重新读取"); return; }
            if (!serverClient.Healthy || serverClient.HasPending || serverClient.Busy) { Notify("请等待当前同步或提交完成"); return; }
            if (target != null) SendServerCommand("unit_order", new SandboxServerTargetUnitOrder { unit_id = id, life_id = unit.life_id, action = action,
                target_id = target.unit_id, target_life_id = target.life_id, resource = resource });
            else SendServerCommand("unit_order", new SandboxServerUnitOrder { unit_id = id, life_id = unit.life_id, action = action, resource = resource });
        }

        private void UpdateUnitHover()
        {
            if (Mouse.current == null || projection == null || cameraRig == null || root.panel == null) return;
            Vector2 pointer = Mouse.current.position.ReadValue();
            Vector2 point = RuntimePanelUtils.ScreenToPanel(root.panel, new Vector2(pointer.x, Screen.height - pointer.y));
            bool blocked = IsPointerOverUi(pointer);
            string id = blocked ? null : cameraRig.PickUnit(pointer);
            // 总览下模型很小：悬浮其合法可见标签同样可查看资源，不扩大单位碰撞体。
            if (!blocked && id == null)
                id = projection.units.Where(item => mapLabels.TryGetValue(item.id, out var label)
                    && label.resolvedStyle.display != DisplayStyle.None && label.worldBound.Contains(point))
                    .OrderBy(item => Vector2.Distance(mapLabels[item.id].worldBound.center, point)).FirstOrDefault()?.id;
            var unit = projection.units.FirstOrDefault(item => item.id == id);
            if (unitHover == null)
            {
                unitHover = new VisualElement { name = "unit-resource-hover", pickingMode = PickingMode.Ignore };
                unitHover.style.position = Position.Absolute; unitHover.style.width = 320; unitHover.style.paddingTop = 12;
                unitHover.style.paddingBottom = 12; unitHover.style.paddingLeft = 16; unitHover.style.paddingRight = 16;
                unitHover.style.backgroundColor = new Color(.025f, .10f, .075f, .97f);
                unitHover.style.borderLeftWidth = 3; unitHover.style.borderLeftColor = new Color(.33f, .8f, .61f);
                unitHoverTitle = new Label { pickingMode = PickingMode.Ignore }; unitHoverTitle.AddToClassList("army-unit-title"); unitHover.Add(unitHoverTitle);
                unitHoverBody = new Label { pickingMode = PickingMode.Ignore }; unitHoverBody.AddToClassList("army-current"); unitHoverBody.style.whiteSpace = WhiteSpace.Normal; unitHover.Add(unitHoverBody);
                root.Add(unitHover);
            }
            unitHover.style.display = unit == null ? DisplayStyle.None : DisplayStyle.Flex;
            if (unit == null) return;
            unitHoverTitle.text = unit.id + " · " + unit.name;
            unitHoverBody.text = UnitResourceText(unit) + (unit.side != role && role != SandboxRole.Host ? "\n敌方 · 当前合法可见情报" : "");
            unitHover.style.left = Mathf.Clamp(point.x + 18, 8, Mathf.Max(8, root.resolvedStyle.width - 336));
            float hoverHeight = float.IsNaN(unitHover.resolvedStyle.height) ? 220 : unitHover.resolvedStyle.height;
            unitHover.style.top = Mathf.Clamp(point.y + 18, 8, Mathf.Max(8, root.resolvedStyle.height - hoverHeight - 8));
            unitHover.BringToFront();
        }
    }
}
