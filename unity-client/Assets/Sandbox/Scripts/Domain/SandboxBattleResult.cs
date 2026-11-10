using System;
using System.Collections.Generic;
using System.Linq;

namespace Qiongtu.Sandbox
{
    [Serializable] public sealed class SandboxGoalStage
    {
        public int stage;
        public int redGoals, blueGoals;
        public bool confirmed;
        public string evidence;
        public SandboxControlObjective[] objectives;
    }

    [Serializable] public sealed class SandboxControlObjective
    {
        public string point_id, name, owner;
        public int redUnits, blueUnits;
    }

    /// <summary>逐阶段结算证据；旧战争目标与新青岚驻守规则分别版本化。</summary>
    [Serializable] public sealed class SandboxVictoryEvidence
    {
        public string ruleVersion = SandboxBattleRules.Version;
        public string mode = "war_goals";
        public bool preview;
        public int elapsedMinutes;
        public SandboxGoalStage[] stages;
        public SandboxSurrenderEvidence surrender;
        public SandboxControlObjective[] liveObjectives;
    }

    [Serializable] public sealed class SandboxSurrenderEvidence { public string side; public int minute; public bool confirmed; }

    [Serializable] public sealed class SandboxStageScore
    {
        public int stage, minute, weight, redPoints, bluePoints, redTotal, blueTotal;
        public bool settled;
    }

    public sealed class SandboxBattleResult
    {
        public string verdict, reason;
        public bool final;
        public int redPoints, bluePoints, scoreMaximum;
        public double redScore100, blueScore100;
        // 上一版序列化字段兼容别名；显示使用百分制分值。
        public double redPercent, bluePercent;
        public string[] calculations, milestones;
        public SandboxStageScore[] stageScores;
    }

    /// <summary>上传规则的可审计子集：战争目标三阶段；未知模式与平局绝不猜测。</summary>
    public static class SandboxBattleRules
    {
        public const string Version = "broken-arrow-war-goals-2026-11-02-v1";
        public const string ProjectVersion = "qinglan-control-2026-11-06-v1";
        public const string ProjectReference = "青岚_演示结果计分规则.md / 项目适配规则（非断箭官方参数）";
        public static bool IsProject(SandboxVictoryEvidence input) => input != null && input.mode == "qinglan_control" && input.ruleVersion == ProjectVersion;
        public static bool IsSupported(SandboxVictoryEvidence input) => IsProject(input) || (input != null && input.mode == "war_goals" && input.ruleVersion == Version);
        public static int StageMinutes(SandboxVictoryEvidence input) => IsProject(input) ? 20 : 15;
        public static int GoalLimit(SandboxVictoryEvidence input) => IsProject(input) ? 3 : 6;
        public static string OwnerLabel(string owner) => owner == "red" ? "红方驻守" : owner == "blue" ? "蓝方驻守" : owner == "contested" ? "双方争夺 · 不计分" : "无人驻守 · 不计分";
        private static bool ValidObjectives(SandboxGoalStage stage)
        {
            var points = stage.objectives;
            if (points == null || points.Length != 3 || points.Any(p => p == null)
                || !points.Select(p => p.point_id).OrderBy(x => x).SequenceEqual(new[] { "TASK_A", "TASK_B", "TASK_C" })) return false;
            foreach (var p in points)
            {
                if (p.redUnits < 0 || p.blueUnits < 0) return false;
                string owner = p.redUnits > 0 && p.blueUnits > 0 ? "contested" : p.redUnits > 0 ? "red" : p.blueUnits > 0 ? "blue" : "neutral";
                if (p.owner != owner) return false;
            }
            return stage.redGoals == points.Count(p => p.owner == "red") && stage.blueGoals == points.Count(p => p.owner == "blue");
        }
        private static bool ValidProjectTimeline(SandboxVictoryEvidence input)
        {
            if (input.elapsedMinutes > 60) return false;
            bool gap = false;
            foreach (var stage in input.stages)
            {
                if (stage.confirmed)
                { if (gap || input.elapsedMinutes < stage.stage * 20) return false; }
                else
                {
                    gap = true;
                    if (stage.redGoals != 0 || stage.blueGoals != 0 || stage.objectives != null && stage.objectives.Length > 0) return false;
                }
            }
            return true;
        }
        public const string Reference = "断箭_获胜规则详解.md / 战争目标（2026-11-02）";

        public static SandboxBattleResult Evaluate(SandboxVictoryEvidence input)
        {
            var result = new SandboxBattleResult { verdict = "待裁定", reason = "缺少战争目标逐阶段结算证据，不能从步数、单位击杀或任务状态推定胜负。",
                calculations = Array.Empty<string>(), milestones = Array.Empty<string>(), stageScores = Array.Empty<SandboxStageScore>() };
            if (input == null) return result;
            bool project = IsProject(input);
            int stageMinutes = StageMinutes(input), goalLimit = GoalLimit(input);
            if (!IsSupported(input))
            { result.reason = "规则版本或模式尚未接入；征服、战役和自定义模式不能套用三阶段计分。"; return result; }
            if (input.elapsedMinutes < 0 || input.stages == null || input.stages.Length != 3)
            { result.reason = "结算输入无效：需要三个独立阶段及非负比赛时间。"; return result; }
            // Validate the entire evidence before emitting any arithmetic, including future-stage entries.
            for (int i = 0; i < 3; i++)
            {
                var stage = input.stages[i];
                if (stage == null || stage.stage != i + 1 || stage.redGoals < 0 || stage.blueGoals < 0
                    || stage.redGoals > goalLimit || stage.blueGoals > goalLimit
                    || (project && stage.confirmed && !ValidObjectives(stage))
                    || (stage.confirmed && string.IsNullOrWhiteSpace(stage.evidence)))
                { result.reason = "目标结算证据无效：阶段须为 1/2/3，每方目标数须在 0 至 " + goalLimit + " 之间，确认须附依据；项目模式须与三个点位归属一致。"; return result; }
            }
            if (project && !ValidProjectTimeline(input))
            { result.reason = "项目计分证据无效：阶段必须按时间顺序封存，未结算阶段不能包含点位分。"; return result; }
            var calculations = new List<string>(); var milestones = new List<string>();
            var scores = new List<SandboxStageScore>();
            bool complete = true; int previousLead = 0;
            for (int i = 0; i < 3; i++)
            {
                var stage = input.stages[i]; int weight = i + 1, minute = weight * stageMinutes;
                var score = new SandboxStageScore { stage = weight, minute = minute, weight = weight, redTotal = result.redPoints, blueTotal = result.bluePoints };
                scores.Add(score);
                if (input.elapsedMinutes < minute || !stage.confirmed)
                {
                    complete = false;
                    calculations.Add("阶段 " + weight + " · " + minute + " 分钟：" + (input.elapsedMinutes < minute ? "尚未到阶段末，不计分" : "缺少确认的目标证据，不计分"));
                    continue;
                }
                int red = stage.redGoals * weight, blue = stage.blueGoals * weight;
                result.redPoints += red; result.bluePoints += blue;
                score.settled = true; score.redPoints = red; score.bluePoints = blue;
                score.redTotal = result.redPoints; score.blueTotal = result.bluePoints;
                calculations.Add("阶段 " + weight + " · " + minute + " 分钟结算\n红方 " + stage.redGoals + " × " + weight + " = " + red
                    + " 分    蓝方 " + stage.blueGoals + " × " + weight + " = " + blue + " 分\n累计  " + result.redPoints + " : " + result.bluePoints + "\n依据：" + stage.evidence);
                if (project)
                    foreach (var point in stage.objectives.OrderBy(p => p.point_id))
                        calculations[calculations.Count - 1] += "\n" + point.name + " · 红" + point.redUnits + " / 蓝" + point.blueUnits + " · " + OwnerLabel(point.owner)
                            + " → 红+" + (point.owner == "red" ? weight : 0) + " / 蓝+" + (point.owner == "blue" ? weight : 0);
                int lead = Math.Sign(result.redPoints - result.bluePoints);
                string change = lead == 0 ? "累计战平" : (lead > 0 ? "红方" : "蓝方") + (previousLead != 0 && previousLead != lead ? "反超" : "领先");
                milestones.Add(minute + "′ · 阶段 " + weight + " 完成 · " + change + "（" + result.redPoints + " : " + result.bluePoints + "）");
                previousLead = lead;
            }
            result.scoreMaximum = goalLimit * (1 + 2 + 3);
            result.redScore100 = Math.Round(result.redPoints * 100d / result.scoreMaximum, 2, MidpointRounding.AwayFromZero);
            result.blueScore100 = Math.Round(result.bluePoints * 100d / result.scoreMaximum, 2, MidpointRounding.AwayFromZero);
            result.redPercent = result.redScore100; result.bluePercent = result.blueScore100;
            result.calculations = calculations.ToArray(); result.milestones = milestones.ToArray(); result.stageScores = scores.ToArray();
            if (input.surrender != null && input.surrender.confirmed && (input.surrender.side == "red" || input.surrender.side == "blue") && input.surrender.minute >= 5 && input.surrender.minute <= input.elapsedMinutes)
            {
                result.final = true; result.verdict = input.surrender.side == "red" ? "蓝方获胜" : "红方获胜";
                result.reason = "单席位队伍在对局开始 5 分钟后有效投降；不等待第三阶段。";
                milestones.Add(input.surrender.minute + "′ · " + (input.surrender.side == "red" ? "红方" : "蓝方") + "投降 · 对方获胜"); result.milestones = milestones.ToArray(); return result;
            }
            if (!complete) { result.verdict = "未结算"; result.reason = "尚未完成全部三个阶段的确认结算；已知累计分不等于最终胜负。"; return result; }
            if (project && result.redPoints == result.bluePoints) { result.final = true; result.verdict = "平局"; result.reason = "项目适配规则：60 分钟三阶段总分相同，记为平局；不启用加时或骤死。"; return result; }
            if (result.redPoints == result.bluePoints) { result.reason = "三阶段积分相同；参考文档未确认平局处理，不增加自定义加时或破平规则。"; return result; }
            result.final = true; result.verdict = result.redPoints > result.bluePoints ? "红方获胜" : "蓝方获胜";
            result.reason = "第三阶段结束后比较总胜利点；" + result.redPoints + (result.redPoints > result.bluePoints ? " > " : " < ") + result.bluePoints + "。36 分仅是理论满分，不是提前获胜门槛。";
            if (project) result.reason = "项目适配规则：60 分钟比较三阶段驻守加权总分，理论满分 18；不计击毁、资源或未实现的任务分。";
            return result;
        }

        public static SandboxVictoryEvidence ProjectPreview(int scenario = 0)
        {
            string[][] owners = scenario == 1
                ? new[] { new[] { "red", "red", "blue" }, new[] { "red", "red", "blue" }, new[] { "red", "blue", "blue" } }
                : new[] { new[] { "red", "red", "blue" }, new[] { "red", "contested", "blue" }, new[] { "red", "blue", "blue" } };
            var names = new[] { "西岸检查点", "中央桥梁", "东岸检查点" };
            return new SandboxVictoryEvidence { mode = "qinglan_control", ruleVersion = ProjectVersion, preview = true,
                elapsedMinutes = scenario == 2 ? 40 : 60, stages = Enumerable.Range(0, 3).Select(i => {
                    var points = Enumerable.Range(0, 3).Select(j => new SandboxControlObjective { point_id = "TASK_" + (char)('A' + j), name = names[j], owner = owners[i][j],
                        redUnits = owners[i][j] == "red" || owners[i][j] == "contested" ? 1 : 0, blueUnits = owners[i][j] == "blue" || owners[i][j] == "contested" ? 1 : 0 }).ToArray();
                    bool confirmed = scenario != 2 || i < 2;
                    return new SandboxGoalStage { stage = i + 1, confirmed = confirmed, redGoals = confirmed ? points.Count(p => p.owner == "red") : 0,
                        blueGoals = confirmed ? points.Count(p => p.owner == "blue") : 0, objectives = confirmed ? points : Array.Empty<SandboxControlObjective>(),
                        evidence = confirmed ? "项目规则预演 · 非真实战果：" + string.Join("；", points.Select(p => p.name + "：" + OwnerLabel(p.owner))) : "" };
                }).ToArray() };
        }

        public static SandboxVictoryEvidence Preview(int scenario = 0)
        {
            var evidence = new SandboxVictoryEvidence { preview = true, elapsedMinutes = scenario == 2 ? 30 : 45,
                stages = new[] {
                    new SandboxGoalStage { stage = 1, redGoals = 4, blueGoals = 2, confirmed = true, evidence = "文档算例：红方完成 4 个，蓝方完成 2 个目标（预演数据）" },
                    new SandboxGoalStage { stage = 2, redGoals = 2, blueGoals = 4, confirmed = true, evidence = "文档算例：红方完成 2 个，蓝方完成 4 个目标（预演数据）" },
                    new SandboxGoalStage { stage = 3, redGoals = 4, blueGoals = 2, confirmed = true, evidence = "文档算例：红方完成 4 个，蓝方完成 2 个目标（预演数据）" }
                } };
            if (scenario == 1) foreach (var stage in evidence.stages)
            { stage.redGoals = stage.blueGoals = 3; stage.evidence = "边界测试：双方各完成 3 个目标（非文档原始算例）"; }
            return evidence;
        }
    }
}
