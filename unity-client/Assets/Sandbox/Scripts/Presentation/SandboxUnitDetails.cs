using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>显示当前席位单位的资源、兵种属性与参考范围。</summary>
    public sealed partial class SandboxHudController
    {
        private readonly HashSet<string> expandedUnitAttributes = new HashSet<string>();
        private bool showUnitRanges;

        /// <summary>格式化已知资源，缺失上限时只显示当前值。</summary>
        /// <param name="caption">玩家使用的资源名称。</param>
        /// <param name="current">当前快照资源。</param>
        /// <param name="maximum">当前规则的资源上限。</param>
        /// <param name="known">资源是否由服务端明确提供。</param>
        /// <returns>单位卡片的资源文字。</returns>
        private static string ResourceText(string caption, double current, double maximum, bool known)
        {
            if (!known) return caption + " · 未配置";
            return caption + " " + current.ToString("0.##") + (maximum > 0 ? " / " + maximum.ToString("0.##") : "");
        }

        /// <summary>按兵种展示主要参数，并提供可保持展开状态的详细属性。</summary>
        /// <param name="card">当前单位卡片。</param>
        /// <param name="unit">当前角色可见的单位。</param>
        private void BuildUnitAttributes(VisualElement card, SandboxUnitData unit)
        {
            SandboxServerUnitAttributes attributes = unit.attributes;
            if (attributes == null)
            {
                AddText(card, "兵种属性 · 等待规则配置", "army-footnote");
                return;
            }
            AddText(card, attributes.speed_m_per_step > 0 ? "基础移动 " + attributes.speed_m_per_step.ToString("0.##") + " 米／步" : "基础移动 · 未配置", "army-current");
            AddText(card, attributes.reference_only ? "新版属性参考 · 以本局已启用能力为准" : "本局兵种属性", "army-attribute-caption");
            AddText(card, "视野半径 " + attributes.vision_m.ToString("0") + " 米", "army-current");
            if (unit.type == SandboxUnitType.Ground)
            {
                AddText(card, "单次伤害 " + attributes.damage.ToString("0.##") + " · 防御减伤 "
                    + attributes.defense_percent.ToString("0.##") + "%", "army-current");
                AddText(card, "攻击距离 " + attributes.attack_range_m.ToString("0") + " 米", "army-current");
            }
            else if (unit.type == SandboxUnitType.Support)
            {
                AddText(card, "服务距离 " + attributes.service_range_m.ToString("0") + " 米 · 每步最多 "
                    + attributes.service_amount.ToString("0.##") + " 点", "army-current");
            }
            else
            {
                SandboxServerUnit fact = serverClient?.View?.units?.FirstOrDefault(item => item.unit_id == unit.id);
                string docking = fact?.in_barracks == true ? "兵营待命" : fact?.flight_state == "landed" || fact?.flight_state == "docked" ? "已停靠"
                    : fact?.flight_state == "airborne" ? "飞行中" : "未上报";
                AddText(card, "停靠状态 · " + docking, "army-current");
            }
            var details = new Foldout { text = "详细属性", value = expandedUnitAttributes.Contains(unit.id) };
            details.AddToClassList("army-attribute-details");
            details.RegisterValueChangedCallback(change =>
            {
                if (change.newValue) expandedUnitAttributes.Add(unit.id);
                else expandedUnitAttributes.Remove(unit.id);
            });
            card.Add(details);
            bool fivepoint = serverClient?.View?.fivepoint != null;
            if (fivepoint)
            {
                SandboxServerUnit fact = serverClient.View.units.FirstOrDefault(item => item.unit_id == unit.id);
                if (unit.type == SandboxUnitType.Support && fact?.resource_cargo != null)
                    AddText(details, "车载库存 · 能源 " + SandboxFivepointView.Display(fact.resource_cargo.energy)
                        + " · 弹药 " + SandboxFivepointView.Display(fact.resource_cargo.ammo)
                        + " · 材料 " + SandboxFivepointView.Display(fact.resource_cargo.material), "army-footnote");
                if (!string.IsNullOrEmpty(fact?.low_energy_reason) && fact.low_energy_reason != "ready")
                    AddText(details, "移动能源保护 · " + fact.low_energy_reason, "army-footnote");
            }
            AddText(details, attributes.speed_m_per_step > 0 ? "移动能耗 " + attributes.energy_per_km.ToString("0.##") + " 点／公里" : "移动能耗 · 未配置", "army-footnote");
            string scoringMode = serverClient?.View?.victory?.mode;
            if (scoringMode == "qinglan_control")
                AddText(details, unit.type == SandboxUnitType.Recon ? "本局资格 · 观察" : "本局资格 · 可参与点位计分", "army-footnote");
            if (unit.type == SandboxUnitType.Ground)
            {
                AddText(details, "攻击间隔 " + attributes.attack_interval_steps.ToString("0.##") + " 步 · 每次消耗 "
                    + attributes.attack_supply_cost.ToString("0.##") + " 点补给", "army-footnote");
                AddText(details, fivepoint ? "允许攻击对象 · 当前合法可见敌军；地面600米，空中800米" : "允许攻击对象 · 敌方履带地面单元", "army-footnote");
            }
            if (unit.type == SandboxUnitType.Support)
                AddText(details, fivepoint ? "每步一个目标、一类资源；补自身能源、坦克弹药或维修耐久；无人机须停靠" : "一次服务一个本方对象 · 无人机补能源，履带单位补自身补给 · 无人机需停靠", "army-footnote");
            AddText(details, "移动、主动观察、攻击与服务每步占用一个主要动作", "army-footnote");
            if (selectedId == unit.id)
            {
                var ranges = new Toggle(attributes.reference_only ? "显示参考范围圈" : "显示范围圈") { value = showUnitRanges };
                ranges.AddToClassList("army-range-toggle");
                card.Add(ranges);
                ranges.RegisterValueChangedCallback(change => { showUnitRanges = change.newValue; RefreshUnitRanges(); });
                AddText(card, "青色为视野，橙色为射程，绿色为服务范围", "army-footnote");
            }
        }

        /// <summary>在选中单位周围显示已提供的范围，角色切换时清理旧范围。</summary>
        private void RefreshUnitRanges()
        {
            SandboxUnitData selected = projection?.units?.FirstOrDefault(unit => unit.id == selectedId);
            SandboxServerUnitAttributes attributes = selected?.attributes;
            world.SetUnitRanges(showUnitRanges && attributes != null,
                (float)(attributes?.vision_m ?? 0),
                selected?.type == SandboxUnitType.Ground ? (float)(attributes?.attack_range_m ?? 0) : 0,
                selected?.type == SandboxUnitType.Support ? (float)(attributes?.service_range_m ?? 0) : 0);
        }

        /// <summary>生成地图上的单位名称、动作与需处理的资源状态。</summary>
        /// <param name="unit">当前角色可见的单位。</param>
        /// <returns>地图标签显示内容。</returns>
        private string UnitMapCaption(SandboxUnitData unit)
        {
            string caption = unit.id + " · " + DemoSandboxViewSource.ArmyName(unit.type) + "\n" + unit.status;
            SandboxServerUnit fact = serverClient?.View?.units?.FirstOrDefault(item => item.unit_id == unit.id);
            if (fact?.attributes == null) return caption;
            if (fact.attributes.durability_known && fact.durability <= 0) return caption + " · 已失效";
            if (fact.attributes.energy_known && fact.energy <= 0) caption += " · 能源耗尽";
            if (fact.attributes.supply_known && unit.type == SandboxUnitType.Ground && fact.supply < fact.attributes.attack_supply_cost)
                caption += " · 补给不足";
            if (fact.attributes.supply_known && unit.type == SandboxUnitType.Support && fact.supply <= 0)
                caption += " · 库存耗尽";
            return caption;
        }
    }
}
