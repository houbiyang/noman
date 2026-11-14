using System;
using System.Globalization;

namespace Qiongtu.Sandbox
{
    /// <summary>五点完整步的角色投影，所有显示数据必须来自同一帧。</summary>
    [Serializable] public sealed class SandboxFivepointView
    {
        public string rule_version, phase, control_mode, planning_source, planning_candidate_json;
        public int step, second, playback_origin_step;
        public bool planning_active, planning_completed;
        public string[] waiting_sides;
        public SandboxFivepointPoint[] points;
        public SandboxFivepointContact[] display_contacts;
        public SandboxFivepointTask[] own_tasks;
        public SandboxFivepointTask[] planning_candidate;
        public SandboxFivepointScore own_score;
        public SandboxFivepointResources own_resources;
        public SandboxFivepointControl public_control;
        public SandboxFivepointResult result;

        /// <summary>把精确分数转换成显示文字，不以显示舍入参与胜负计算。</summary>
        /// <param name="value">服务端有理数字符串。</param>
        /// <returns>保留两位小数的显示值，非法或缺失值显示未知。</returns>
        public static string Display(string value)
        {
            if (string.IsNullOrEmpty(value)) return "未知";
            string[] parts = value.Split('/');
            if (parts.Length > 2 || !double.TryParse(parts[0], NumberStyles.Float, CultureInfo.InvariantCulture, out double numerator)) return "未知";
            double denominator = 1;
            if (parts.Length == 2 && (!double.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out denominator) || denominator == 0)) return "未知";
            double result = numerator / denominator;
            return double.IsNaN(result) || double.IsInfinity(result) ? "未知" : result.ToString("0.00", CultureInfo.InvariantCulture);
        }
    }

    /// <summary>五点归属及本方合法可见的阶段进度。</summary>
    [Serializable] public sealed class SandboxFivepointPoint { public string point_id, owner, candidate, layer; public float[] xy; public int progress; public bool contested; }
    /// <summary>合法观察或冻结历史中的目标；历史标记不得跟随隐藏单位移动。</summary>
    [Serializable] public sealed class SandboxFivepointContact { public string unit_id, unit_type, hp; public float x, z; public int observed_second; public bool current; }
    /// <summary>实际任务状态，完成由后端事实判定。</summary>
    [Serializable] public sealed class SandboxFivepointTask { public string task_id, unit_id, status, reason; public int active_steps; }
    /// <summary>精确分项得分，不复用旧七点比分结构。</summary>
    [Serializable] public sealed class SandboxFivepointScore { public string control, destruction, resource, total; }
    /// <summary>本方余额，敌方库存不得进入此投影。</summary>
    [Serializable] public sealed class SandboxFivepointResources { public string energy, ammo, material; }
    /// <summary>双方公开占点分。</summary>
    [Serializable] public sealed class SandboxFivepointControl { public string red, blue; }
    /// <summary>正式终局的双方完整得分。</summary>
    [Serializable] public sealed class SandboxFivepointScores { public SandboxFivepointScore red, blue; }
    /// <summary>与播放时刻一致的终局事实；提前权威终局不提前显示。</summary>
    [Serializable] public sealed class SandboxFivepointResult { public string winner, reason; public int second; public SandboxFivepointScores scores; }
    /// <summary>本方明确采用或驳回服务器保存的当前候选。</summary>
    [Serializable] public sealed class SandboxFivepointDecision { public int expected_step; public string decision = "adopt"; public bool use_candidate; }
    /// <summary>准备阶段明确冻结人工确认或自动模式。</summary>
    [Serializable] public sealed class SandboxFivepointEnable { public string control_mode; }
    /// <summary>完整轮推进回执，业务中断不能当作成功连续重试。</summary>
    [Serializable] public sealed class SandboxFivepointProductionResult { public string status; public int completed_steps; public SandboxServerError error; }
}
