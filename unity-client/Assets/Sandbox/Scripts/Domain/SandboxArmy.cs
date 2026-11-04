using System;
using System.Collections.Generic;
using System.Linq;

namespace Qiongtu.Sandbox
{
    [Serializable]
    public sealed class SandboxBarracksData
    {
        public string id;
        public SandboxRole side;
        public SandboxUnitType type;
        public float x, z;
        public SandboxBarracksData Copy() => (SandboxBarracksData)MemberwiseClone();
    }

    /// <summary>兵营尺寸使用世界米制，存档仍保留 120×80 业务坐标。</summary>
    public static class SandboxArmyLayout
    {
        public const float MetersPerX = 100f;
        public const float MetersPerZ = 150f;
        public const float HalfWidth = 12f;
        public const float RearClearance = 10.5f;
        public const float FrontClearance = 31f;
        public const float ExitOffset = 10.5f;
        public const float RallyStart = 16f;
        public const float RallySpacing = 6f;

        public static float ExitZ(float z) => z + ExitOffset / MetersPerZ;
        public static float RallyX(float x, int index, int quantity) => x + (index % 4 - Math.Min(quantity, 4) / 2f + .5f) * RallySpacing / MetersPerX;
        public static float RallyZ(float z, int index) => z + (RallyStart + index / 4 * RallySpacing) / MetersPerZ;
        public static bool Contains(float x, float z) =>
            x >= HalfWidth / MetersPerX && x <= 120 - HalfWidth / MetersPerX
            && z >= RearClearance / MetersPerZ && z <= 80 - FrontClearance / MetersPerZ;
        public static bool Overlaps(float x, float z, float otherX, float otherZ) =>
            Math.Abs((x - otherX) * MetersPerX) < HalfWidth * 2
            && Math.Abs((z - otherZ) * MetersPerZ) < RearClearance + FrontClearance;
    }

    public sealed partial class DemoSandboxViewSource
    {
        public const int MaxArmyUnits = 10;
        public int ArmyCapacityRemaining => Math.Max(0, MaxArmyUnits - session.units.Length);

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
            message = "只能部署本方军队，主持人可以部署双方";
            if ((side != SandboxRole.Red && side != SandboxRole.Blue) || actor != SandboxRole.Host && actor != side) return false;
            message = "每种单位的本次部署数量须为 1–10";
            if (quantities == null || quantities.Length != 3 || quantities.Any(count => count < 1 || count > MaxArmyUnits)) return false;
            if (!HasArmyCapacity(quantities.Sum(), out message)) return false;
            var units = session.units.ToList();
            foreach (SandboxUnitType type in Enum.GetValues(typeof(SandboxUnitType)))
                AddFormation(units, side, type, quantities[(int)type], side == SandboxRole.Red ? 23 : 97, 32 + (int)type * 7);
            session.units = units.ToArray();
            AddRecord("集结区新增部署", string.Join("、", quantities.Select((count, index) => ArmyName((SandboxUnitType)index) + " × " + count)), side, false, false);
            message = "已在本方集结区追加单位，原有单位保持不变";
            return true;
        }

        public bool TryDeployArmy(SandboxRole actor, SandboxRole side, SandboxUnitType type, int quantity, float x, float z, out string message)
        {
            message = "部署数量、角色或位置无效";
            if ((side != SandboxRole.Red && side != SandboxRole.Blue) || actor != SandboxRole.Host && actor != side
                || !Enum.IsDefined(typeof(SandboxUnitType), type) || quantity < 1 || quantity > MaxArmyUnits || !ValidArmyPosition(x, z)) return false;
            if (!HasArmyCapacity(quantity, out message)) return false;
            // Validate the entire batch before appending; never change an existing unit or partially deploy.
            for (int i = 0; i < quantity; i++)
                if (!ValidArmyPosition(x + (i % 4 - Math.Min(quantity, 4) / 2f + 0.5f) * 2.1f, z + (i / 4) * 2.1f))
                { message = "编队超出地图范围，请选择更靠内的位置"; return false; }
            var units = session.units.ToList();
            AddFormation(units, side, type, quantity, x, z);
            session.units = units.ToArray();
            AddRecord("地图追加部署", ArmyName(type) + " × " + quantity + "，原有单位保留", side, false, false);
            message = "已新增 " + ArmyName(type) + " × " + quantity + "，地图总计 " + session.units.Length + " / " + MaxArmyUnits;
            return true;
        }

        public static string BarracksName(SandboxUnitType type) => type == SandboxUnitType.Recon ? "无人机机库" : type == SandboxUnitType.Ground ? "履带单元兵营" : "保障车兵营";
        public static string BarracksId(SandboxRole side, SandboxUnitType type) => (side == SandboxRole.Red ? "R" : "B") + "-CAMP-" + ArmyKey(type);
        public static float RallyX(float x, int index, int quantity) => SandboxArmyLayout.RallyX(x, index, quantity);
        public static float RallyZ(float z, int index) => SandboxArmyLayout.RallyZ(z, index);

        // One persistent barracks per side/type. Repositioning never moves existing troops.
        public bool TryPlaceBarracks(SandboxRole actor, SandboxRole side, SandboxUnitType type, float x, float z, out string message)
        {
            message = "兵营角色或位置无效";
            if ((side != SandboxRole.Red && side != SandboxRole.Blue) || actor != SandboxRole.Host && actor != side
                || !Enum.IsDefined(typeof(SandboxUnitType), type)
                || !SandboxArmyLayout.Contains(x, z)) return false;
            string id = BarracksId(side, type);
            bool relocating = session.barracks.Any(camp => camp.id == id);
            if (session.barracks.Any(camp => camp.id != id && SandboxArmyLayout.Overlaps(x, z, camp.x, camp.z)))
            { message = "兵营之间需要留出建筑与出兵空间"; return false; }
            var camps = session.barracks.Where(camp => camp.id != id).ToList();
            camps.Add(new SandboxBarracksData { id = id, side = side, type = type, x = x, z = z });
            session.barracks = camps.ToArray();
            AddRecord(relocating ? "兵营重新选址" : "建造兵营", BarracksName(type) + "，等待仿真出兵指令", side, false, false);
            message = "已" + (relocating ? "移动" : "放置") + BarracksName(type) + "，已有单位保持不变";
            return true;
        }

        public bool TrySpawnArmy(SandboxRole actor, SandboxRole side, SandboxUnitType type, int quantity, out string message)
        {
            message = "只能操作本方兵营，主持人可以操作双方";
            if ((side != SandboxRole.Red && side != SandboxRole.Blue) || actor != SandboxRole.Host && actor != side || !Enum.IsDefined(typeof(SandboxUnitType), type)) return false;
            message = "请先在地图放置该兵种的兵营";
            var camp = session.barracks.FirstOrDefault(item => item.id == BarracksId(side, type));
            if (camp == null) return false;
            message = "生成数量须为 1–10";
            if (quantity < 1 || quantity > MaxArmyUnits) return false;
            if (!HasArmyCapacity(quantity, out message)) return false;
            SpawnFromBarracks(camp, quantity);
            AddRecord("兵营生成单位", BarracksName(type) + "生成 " + quantity + " 个单位", side, false, false);
            message = "已从" + BarracksName(type) + "生成 " + quantity + " 个单位 · 总计 " + session.units.Length + " / 10";
            return true;
        }

        private void SpawnFromBarracks(SandboxBarracksData camp, int quantity)
        {
            var units = session.units.ToList();
            int start = units.Count;
            AddFormation(units, camp.side, camp.type, quantity, camp.x, camp.z);
            for (int i = start; i < units.Count; i++)
            {
                int slot = int.Parse(units[i].id.Substring(3)) - 1;
                units[i].x = RallyX(camp.x, slot, 10); units[i].z = RallyZ(camp.z, slot);
                units[i].barracksId = camp.id; units[i].status = "兵营出发 · 待命";
            }
            session.units = units.ToArray();
        }

        private static string ValidateBarracks(SandboxSessionData data)
        {
            var camps = data.barracks ?? new SandboxBarracksData[0];
            if (camps.Length > 6) return "每方每种兵种只能有一个兵营";
            var ids = new HashSet<string>();
            foreach (var camp in camps)
            {
                if (camp == null || (camp.side != SandboxRole.Red && camp.side != SandboxRole.Blue)
                    || !Enum.IsDefined(typeof(SandboxUnitType), camp.type) || camp.id != BarracksId(camp.side, camp.type) || !ids.Add(camp.id)
                    || !SandboxArmyLayout.Contains(camp.x, camp.z)) return "存档兵营身份或坐标无效";
            }
            for (int i = 0; i < camps.Length; i++) for (int j = i + 1; j < camps.Length; j++)
                if (SandboxArmyLayout.Overlaps(camps[i].x, camps[i].z, camps[j].x, camps[j].z)) return "存档兵营相互重叠";
            foreach (var unit in data.units)
                if (unit != null && !string.IsNullOrEmpty(unit.barracksId) && !camps.Any(camp => camp.id == unit.barracksId && camp.side == unit.side && camp.type == unit.type)) return "存档单位与兵营归属不匹配";
            return string.Empty;
        }

        private bool HasArmyCapacity(int quantity, out string message)
        {
            message = "红蓝双方地图总计最多 " + MaxArmyUnits + " 个单位，当前剩余 " + ArmyCapacityRemaining + " 个名额";
            return quantity <= ArmyCapacityRemaining;
        }

        private static void AddFormation(List<SandboxUnitData> units, SandboxRole side, SandboxUnitType type, int quantity, float x, float z)
        {
            for (int i = 0; i < quantity; i++)
            {
                int number = Enumerable.Range(1, MaxArmyUnits).First(candidate => !units.Any(unit => unit.id == ArmyId(side, type, candidate)));
                string id = ArmyId(side, type, number);
                units.Add(new SandboxUnitData
                {
                    id = id, name = ArmyName(type) + " " + number.ToString("00"), side = side, type = type,
                    x = x + (i % 4 - Math.Min(quantity, 4) / 2f + 0.5f) * 2.1f, z = z + (i / 4) * 2.1f,
                    status = "已部署 · 待命"
                });
            }
        }

        public bool TryDeleteArmyUnit(SandboxRole actor, string id, out string message)
        {
            SandboxUnitData unit = session.units.FirstOrDefault(candidate => candidate.id == id);
            message = "只能删除本方已部署单位，主持人可以删除双方单位";
            if (unit == null || actor != SandboxRole.Host && actor != unit.side) return false;
            session.units = session.units.Where(candidate => candidate.id != id).ToArray();
            AddRecord("已删除部署单位", unit.id + " · " + unit.name, unit.side, false, false);
            message = "已删除 " + unit.id + "，地图总计 " + session.units.Length + " / " + MaxArmyUnits + "，剩余 " + ArmyCapacityRemaining + " 个名额";
            return true;
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
