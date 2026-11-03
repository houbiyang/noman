using System;
using System.Collections.Generic;
using System.Linq;

namespace Qiongtu.Sandbox
{
    public sealed partial class DemoSandboxViewSource
    {
        public static string ArmyName(SandboxUnitType type) => type == SandboxUnitType.Recon ? "侦察无人机" : type == SandboxUnitType.Ground ? "履带地面单元" : "六轮保障车";
        public static string ArmyKey(SandboxUnitType type) => type == SandboxUnitType.Recon ? "recon" : type == SandboxUnitType.Ground ? "infantry" : "support";
        private static string ArmyId(SandboxRole side, SandboxUnitType type, int number) =>
            (side == SandboxRole.Red ? "R-" : "B-") + (type == SandboxUnitType.Recon ? "U" : type == SandboxUnitType.Ground ? "G" : "S") + number.ToString("00");

        private static bool ValidArmyIdentity(SandboxUnitData unit)
        {
            if (unit.side != SandboxRole.Red && unit.side != SandboxRole.Blue || !Enum.IsDefined(typeof(SandboxUnitType), unit.type)) return false;
            return Enumerable.Range(1, 10).Any(number => unit.id == ArmyId(unit.side, unit.type, number));
        }

        public bool TryConfigureArmy(SandboxRole actor, SandboxRole side, int[] quantities, out string message)
        {
            message = "只能配置本方军队，主持人可以配置双方";
            if ((side != SandboxRole.Red && side != SandboxRole.Blue) || actor != SandboxRole.Host && actor != side) return false;
            message = "每种单位数量须为 1–10";
            if (quantities == null || quantities.Length != 3 || quantities.Any(count => count < 1 || count > 10)) return false;
            var units = session.units.Where(unit => unit.side != side).ToList();
            foreach (SandboxUnitType type in Enum.GetValues(typeof(SandboxUnitType)))
                AddFormation(units, side, type, quantities[(int)type], side == SandboxRole.Red ? 23 : 97, 32 + (int)type * 7, false);
            session.units = units.ToArray();
            AddRecord("军队编制已更新", string.Join("、", quantities.Select((count, index) => ArmyName((SandboxUnitType)index) + " × " + count)), side, false, false);
            message = "编队已应用，新单位已在本方集结区就位";
            return true;
        }

        public bool TryDeployArmy(SandboxRole actor, SandboxRole side, SandboxUnitType type, int quantity, float x, float z, out string message)
        {
            message = "部署数量、角色或位置无效";
            if ((side != SandboxRole.Red && side != SandboxRole.Blue) || actor != SandboxRole.Host && actor != side
                || !Enum.IsDefined(typeof(SandboxUnitType), type) || quantity < 1 || quantity > 10 || !ValidArmyPosition(x, z)) return false;
            // Validate every formation offset before changing the roster; never leave a partial deployment.
            for (int i = 0; i < quantity; i++)
                if (!ValidArmyPosition(x + (i % 4 - Math.Min(quantity, 4) / 2f + 0.5f) * 2.1f, z + (i / 4) * 2.1f))
                { message = "编队超出地图范围，请选择更靠内的位置"; return false; }
            var units = session.units.Where(unit => unit.side != side || unit.type != type).ToList();
            AddFormation(units, side, type, quantity, x, z, true);
            session.units = units.ToArray();
            AddRecord("地图编队部署", ArmyName(type) + " × " + quantity + "，本机部署完成", side, false, false);
            message = ArmyName(type) + " × " + quantity + " 已部署，可选择单位后移动或跟随";
            return true;
        }

        private void AddFormation(List<SandboxUnitData> units, SandboxRole side, SandboxUnitType type, int quantity, float x, float z, bool relocate)
        {
            for (int i = 0; i < quantity; i++)
            {
                string id = ArmyId(side, type, i + 1);
                SandboxUnitData existing = session.units.FirstOrDefault(unit => unit.id == id);
                units.Add(!relocate && existing != null ? existing : new SandboxUnitData
                {
                    id = id, name = ArmyName(type) + " " + (i + 1).ToString("00"), side = side, type = type,
                    x = x + (i % 4 - Math.Min(quantity, 4) / 2f + 0.5f) * 2.1f, z = z + (i / 4) * 2.1f,
                    status = "已部署 · 待命"
                });
            }
        }

        public bool TryMoveArmyUnit(SandboxRole actor, string id, float x, float z, out string message)
        {
            SandboxUnitData unit = session.units.FirstOrDefault(candidate => candidate.id == id);
            message = "只能移动当前角色可见的本方单位";
            if (unit == null || actor != SandboxRole.Host && actor != unit.side) return false;
            message = "目标超出地图范围";
            if (!ValidArmyPosition(x, z)) return false;
            unit.x = x; unit.z = z; unit.status = "机动目标已设置";
            AddRecord("单位机动", id + " 已设置本机移动目标", unit.side, false, false);
            message = "目标已设置：" + unit.name;
            return true;
        }

        private static bool ValidArmyPosition(float x, float z) => !float.IsNaN(x) && !float.IsInfinity(x) && !float.IsNaN(z) && !float.IsInfinity(z) && x >= 0 && x <= 120 && z >= 0 && z <= 80;
    }
}
