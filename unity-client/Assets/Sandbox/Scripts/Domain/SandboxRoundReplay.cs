using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    [Serializable] public sealed class SandboxBattleScoreBreakdown { public string control, kills, resources, total; }
    [Serializable] public sealed class SandboxManualBattleView
    {
        public string schema_version, mode, stage, blue_score, red_score, winner, finish_reason;
        public int round_number, completed_rounds, elapsed_seconds, round_seconds, maximum_rounds;
        public bool engine_ready, can_edit, can_submit, can_resolve;
        public SandboxBattleScoreBreakdown blue_breakdown, red_breakdown;
        public SandboxBattleSubmitted submitted;
        public SandboxBattleIntentView[] own_drafts;
    }
    [Serializable] public sealed class SandboxReplayPosition { public int x_mm, z_mm, height_mm; }
    [Serializable] public sealed class SandboxReplayUnit
    {
        public string unit_id, life_id, side, unit_type, durability, energy, ammo;
        public string max_durability, max_energy, max_ammo, base_speed_m_per_minute, speed_m_per_minute, attack_target_id;
        public SandboxReplayPosition position;
        public bool retired, docked;
    }
    [Serializable] public sealed class SandboxReplayPoint { public string point_id, owner, advancing_side; public int progress_steps; public bool contested; }
    [Serializable] public sealed class SandboxReplayWorld
    {
        public int elapsed_seconds;
        public SandboxReplayUnit[] units;
        public SandboxReplayPoint[] capture_points;
        public string blue_score, red_score;
    }
    [Serializable] public sealed class SandboxReplayEvent
    {
        public string event_id, kind, source_id, target_id, source_life_id, target_life_id, resource, amount, camp_id, delivery_category;
        public int at_seconds;
        public SandboxReplayPosition source_position, target_position;
        public SandboxReplayPosition[] trace;
    }
    [Serializable] public sealed class SandboxReplayFrame { public int at_seconds; public SandboxReplayWorld world; public SandboxReplayEvent[] events; }
    [Serializable] public sealed class SandboxReplayRecord
    {
        public string schema_version, rule_sha256, scenario_sha256, previous_round_sha256, payload_sha256, before_world_sha256, after_world_sha256;
        public int round_number, start_seconds, end_seconds;
        public bool finished;
        public SandboxReplayWorld initial_world;
        public SandboxReplayFrame[] frames;
    }
    [Serializable] public sealed class SandboxReplayIndexItem { public int round_number, start_seconds, end_seconds; public string payload_sha256; }
    [Serializable] public sealed class SandboxReplayIndex { public SandboxReplayIndexItem[] items; public int completed_rounds; public bool read_only, writes_game_state; }
    [Serializable] public sealed class SandboxReplayResponse { public SandboxReplayRecord record; public bool read_only, writes_game_state; }

    /// <summary>只有历史数据和只读时钟；不持有ServerClient，不具备提交或扣资源的能力。</summary>
    public sealed class SandboxRoundReplayCursor
    {
        private readonly List<SandboxReplayFrame> frames = new List<SandboxReplayFrame>();
        private readonly List<SandboxReplayEvent> events = new List<SandboxReplayEvent>();
        public double Seconds { get; private set; }
        public int FirstSeconds { get; }
        public int LastSeconds { get; }
        public bool Playing { get; private set; }
        public SandboxReplayWorld World { get; private set; }
        public IReadOnlyList<SandboxReplayEvent> Events => events;
        public SandboxRoundReplayCursor(SandboxReplayRecord[] records)
        {
            if (records == null || records.Length == 0) throw new ArgumentException("还没有已完成轮次");
            // 所有记录先复制；播放、跳转不能修改网络响应或调用方持有的账本。
            records = records.Select(r => r == null ? null : JsonUtility.FromJson<SandboxReplayRecord>(JsonUtility.ToJson(r))).ToArray();
            SandboxReplayRecord previous = null;
            var eventIds = new HashSet<string>();
            foreach (var r in records)
            {
                if (r == null || r.round_number < 1 || r.round_number > 60 || r.schema_version != "qinglan-battle-round-replay-v1" || r.frames == null || r.frames.Length == 0
                    || r.start_seconds != (r.round_number - 1) * 60 || r.initial_world?.elapsed_seconds != r.start_seconds
                    || r.end_seconds <= r.start_seconds || r.end_seconds - r.start_seconds > 60
                    || (r.end_seconds - r.start_seconds != 60 && !r.finished)
                    || (r.end_seconds >= 3600 && !r.finished)) throw new ArgumentException("回放时间或版本无效");
                if (!Hash(r.rule_sha256) || !Hash(r.scenario_sha256) || !Hash(r.payload_sha256)
                    || !Hash(r.before_world_sha256) || !Hash(r.after_world_sha256)
                    || (r.round_number == 1 ? !string.IsNullOrEmpty(r.previous_round_sha256) : !Hash(r.previous_round_sha256)))
                    throw new ArgumentException("回放摘要或前驱无效");
                ValidateWorld(r.initial_world);
                string profile = Profile(r.initial_world);
                if (previous != null && (previous.finished || previous.end_seconds != r.start_seconds
                    || r.round_number != previous.round_number + 1 || r.previous_round_sha256 != previous.payload_sha256
                    || r.rule_sha256 != previous.rule_sha256 || r.scenario_sha256 != previous.scenario_sha256
                    || r.before_world_sha256 != previous.after_world_sha256
                    || WorldKey(r.initial_world) != WorldKey(previous.frames.Last().world)))
                    throw new ArgumentException("回放链不连续");
                int expected = r.start_seconds;
                foreach (var frame in r.frames)
                {
                    expected += 5;
                    if (frame == null || frame.at_seconds != expected || frame.world?.elapsed_seconds != expected)
                        throw new ArgumentException("回放帧不连续");
                    ValidateWorld(frame.world);
                    if (Profile(frame.world) != profile) throw new ArgumentException("回放中单位生命或冻结参数改变");
                    foreach (var e in frame.events ?? Array.Empty<SandboxReplayEvent>())
                    {
                        if (e == null || e.at_seconds != expected || string.IsNullOrEmpty(e.event_id) || !eventIds.Add(e.event_id))
                            throw new ArgumentException("重复或错时事件");
                        if (e.kind == "move" && (e.trace == null || e.trace.Length < 2 || e.trace.Length > 128
                            || e.trace.Any(p => p == null) || !frame.world.units.Any(u => u.unit_id == e.source_id && u.life_id == e.source_life_id)))
                            throw new ArgumentException("回放移动轨迹无效");
                        if (e.kind == "barracks_service" && (string.IsNullOrEmpty(e.camp_id) || !string.IsNullOrEmpty(e.source_id)
                            || !string.IsNullOrEmpty(e.source_life_id) || e.source_position == null || e.target_position == null
                            || !frame.world.units.Any(u => u.unit_id == e.target_id && u.life_id == e.target_life_id && !u.retired
                                && u.position.x_mm == e.target_position.x_mm && u.position.z_mm == e.target_position.z_mm
                                && u.position.height_mm == e.target_position.height_mm)
                            || DisplayNumber(e.amount) <= 0 || !CampCategoryMatches(e.delivery_category, e.resource)))
                            throw new ArgumentException("兵营交付回放身份、类别或端点无效");
                        events.Add(e);
                    }
                }
                if (expected != r.end_seconds) throw new ArgumentException("回放末帧不一致");
                if (previous == null) frames.Add(new SandboxReplayFrame { at_seconds = r.start_seconds, world = r.initial_world });
                frames.AddRange(r.frames); previous = r;
            }
            FirstSeconds = records[0].start_seconds; LastSeconds = records.Last().end_seconds;
            Seek(FirstSeconds);
        }
        private static bool CampCategoryMatches(string category, string resource) => resource == "energy" ? category == "energy" || category == "stock_energy"
            : resource == "ammo" ? category == "ammo" || category == "stock_ammo"
            : resource == "repair" && (category == "durability" || category == "stock_repair");
        public void Seek(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value) || value < FirstSeconds || value > LastSeconds)
                throw new ArgumentOutOfRangeException(nameof(value));
            Seconds = value;
            World = frames.Last(f => f.at_seconds <= value).world;
            if (value >= LastSeconds) Playing = false;
        }
        public void Play() { if (Seconds < LastSeconds) Playing = true; }
        public void Pause() { Playing = false; }
        public void Advance(double delta)
        {
            if (double.IsNaN(delta) || double.IsInfinity(delta) || delta < 0) throw new ArgumentOutOfRangeException(nameof(delta));
            if (Playing) Seek(Math.Min(LastSeconds, Seconds + delta));
        }
        private static bool Hash(string value) => value != null && Regex.IsMatch(value, "^[0-9a-f]{64}$");
        private static string Profile(SandboxReplayWorld w) => string.Join("|", w.units.OrderBy(u => u.unit_id, StringComparer.Ordinal)
            .Select(u => string.Join("/", u.unit_id, u.life_id, u.side, u.unit_type, u.max_durability, u.max_energy, u.max_ammo, u.base_speed_m_per_minute)));
        private static string WorldKey(SandboxReplayWorld w) => JsonUtility.ToJson(new SandboxReplayWorld {
            elapsed_seconds = w.elapsed_seconds, blue_score = w.blue_score, red_score = w.red_score,
            units = w.units.OrderBy(u => u.unit_id, StringComparer.Ordinal).ToArray(),
            capture_points = w.capture_points.OrderBy(p => p.point_id, StringComparer.Ordinal).ToArray() });
        private static void ValidateWorld(SandboxReplayWorld w)
        {
            if (w == null || w.units == null || w.units.Length > 60 || w.units.Any(u => u == null)
                || w.units.Select(u => u.unit_id).Distinct().Count() != w.units.Length
                || w.capture_points == null || w.capture_points.Any(p => p == null)
                || !w.capture_points.Select(p => p.point_id).OrderBy(x => x, StringComparer.Ordinal)
                    .SequenceEqual(new[] { "P-01", "P-02", "P-03", "P-04", "P-05" })) throw new ArgumentException("回放单位或据点无效");
            DisplayNumber(w.blue_score); DisplayNumber(w.red_score);
            foreach (var u in w.units)
            {
                if (string.IsNullOrEmpty(u.unit_id) || string.IsNullOrEmpty(u.life_id) || (u.side != "blue" && u.side != "red")
                    || (u.unit_type != "ground" && u.unit_type != "recon" && u.unit_type != "support") || u.position == null
                    || u.position.x_mm < 0 || u.position.x_mm > 12000000 || u.position.z_mm < 0 || u.position.z_mm > 12000000
                    || DisplayNumber(u.durability) > DisplayNumber(u.max_durability)
                    || DisplayNumber(u.energy) > DisplayNumber(u.max_energy) || DisplayNumber(u.ammo) > DisplayNumber(u.max_ammo))
                    throw new ArgumentException("回放资源或单位状态无效");
                DisplayNumber(u.base_speed_m_per_minute); DisplayNumber(u.speed_m_per_minute);
            }
        }
        // 仅在文字/模型显示边界换算；精确字符串原样保留，永不回写数据库。
        public static double DisplayNumber(string value)
        {
            if (value == null || value.Length > 80 || !Regex.IsMatch(value, @"^\d+(?:/\d+|\.\d+)?$"))
                throw new ArgumentException("回放显示数值无效");
            string[] parts = value.Split('/');
            double number = double.Parse(parts[0], CultureInfo.InvariantCulture);
            if (parts.Length == 2) number /= double.Parse(parts[1], CultureInfo.InvariantCulture);
            if (double.IsNaN(number) || double.IsInfinity(number) || number < 0 || number > 10000)
                throw new ArgumentException("回放显示数值无效");
            return number;
        }
    }
}
