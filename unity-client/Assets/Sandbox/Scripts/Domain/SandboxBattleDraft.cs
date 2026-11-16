using System;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    // 只用于读取；绝不把含所有可选字段的DTO直接序列化成严格请求。
    [Serializable] public sealed class SandboxBattleSubmitted { public bool blue, red; }
    [Serializable] public sealed class SandboxBattleIntentView
    {
        public string unit_id, life_id;
        public SandboxBattleMovementView movement;
        public SandboxBattleTaskView task;
    }
    [Serializable] public sealed class SandboxBattleMovementView { public string action; public SandboxServerPosition position; public SandboxServerPosition[] waypoints; }
    [Serializable] public sealed class SandboxBattleTaskView
    {
        public string action, target_id, target_life_id, resource, quantity, target_balance, camp_id;
        public SandboxServerPosition position;
        public int radius_m;
    }
    [Serializable] public sealed class SandboxBattleRoundPayload { public int round_number; }

    /// <summary>最小形状白名单：JsonUtility不会省略null，因此各指令单独构造通道。</summary>
    public sealed class SandboxBattleDraftPayload
    {
        private readonly string json;
        private SandboxBattleDraftPayload(int round, SandboxServerUnit unit, string channel, object value, string suffix = "")
        {
            if (round < 1 || round > 60 || unit == null || string.IsNullOrEmpty(unit.life_id)) throw new ArgumentException("轮次或单位生命实例无效");
            string identity = JsonUtility.ToJson(new Identity { unit_id = unit.unit_id, life_id = unit.life_id });
            string body = JsonUtility.ToJson(value);
            if (suffix.Length > 0) body = body.Substring(0, body.Length - 1) + suffix + "}";
            json = "{\"round_number\":" + round + ",\"intents\":[" + identity.Substring(0, identity.Length - 1)
                + ",\"" + channel + "\":" + body + "}]}";
        }
        public string ToJson() => json;
        private static string Quote(string value)
        {
            string wrapper = JsonUtility.ToJson(new Text { value = value });
            return wrapper.Substring(9, wrapper.Length - 10);
        }
        public static SandboxBattleDraftPayload Movement(int round, SandboxServerUnit unit, SandboxServerPosition point = null, SandboxServerPosition[] waypoints = null)
            => point == null ? new SandboxBattleDraftPayload(round, unit, "movement", new Action { action = "stop" })
                : new SandboxBattleDraftPayload(round, unit, "movement", new Move { position = point, waypoints = waypoints ?? Array.Empty<SandboxServerPosition>() });
        public static SandboxBattleDraftPayload Task(int round, SandboxServerUnit unit, string action)
        {
            if (action != "stop" && action != "takeoff" && action != "land") throw new ArgumentException("任务必须明确指定参数");
            return new SandboxBattleDraftPayload(round, unit, "task", new Action { action = action });
        }
        public static SandboxBattleDraftPayload TargetTask(int round, SandboxServerUnit unit, string action, SandboxServerUnit target)
        {
            if ((action != "attack" && action != "recon") || target == null) throw new ArgumentException("目标任务无效");
            return new SandboxBattleDraftPayload(round, unit, "task", new Target { action = action, target_id = target.unit_id, target_life_id = target.life_id });
        }
        public static SandboxBattleDraftPayload Service(int round, SandboxServerUnit unit, SandboxServerUnit target, string resource, string amount, bool balance)
        {
            if (target == null || (resource != "energy" && resource != "ammo" && resource != "repair")) throw new ArgumentException("服务类别无效");
            object task = resource == "repair" ? (object)new Target { action = "repair", target_id = target.unit_id, target_life_id = target.life_id }
                : new Supply { action = "resupply", target_id = target.unit_id, target_life_id = target.life_id, resource = resource };
            return new SandboxBattleDraftPayload(round, unit, "task", task, ",\"" + (balance ? "target_balance" : "quantity") + "\":" + Quote(amount));
        }
        public static SandboxBattleDraftPayload Camp(int round, SandboxServerUnit unit, string campId)
            => new SandboxBattleDraftPayload(round, unit, "task", new Barracks { camp_id = campId });
        public static SandboxBattleDraftPayload Area(int round, SandboxServerUnit unit, SandboxServerPosition point, int radius = 0)
        {
            if (point == null || radius < 0 || radius > 12000) throw new ArgumentException("区域无效");
            return radius == 0 ? new SandboxBattleDraftPayload(round, unit, "task", new Recon { position = point })
                : new SandboxBattleDraftPayload(round, unit, "task", new AreaAttack { position = point, radius_m = radius });
        }
        [Serializable] private sealed class Identity { public string unit_id, life_id; }
        [Serializable] private sealed class Text { public string value; }
        [Serializable] private sealed class Action { public string action; }
        [Serializable] private sealed class Move { public string action = "move"; public SandboxServerPosition position; public SandboxServerPosition[] waypoints; }
        [Serializable] private sealed class Target { public string action, target_id, target_life_id; }
        [Serializable] private sealed class Supply { public string action, target_id, target_life_id, resource; }
        [Serializable] private sealed class Barracks { public string action = "barracks", camp_id; }
        [Serializable] private sealed class Recon { public string action = "recon"; public SandboxServerPosition position; }
        [Serializable] private sealed class AreaAttack { public string action = "attack_area"; public SandboxServerPosition position; public int radius_m; }
    }
}
