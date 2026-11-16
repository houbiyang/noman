using System;
using System.Linq;
using UnityEngine;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>使用真实共享配置验证据点显示和新旧计分边界，不重建底图。</summary>
    public static class SandboxCaptureChecks
    {
        public static void Run()
        {
            var map = SandboxMapFactory.LoadDefinition();
            var layout = SandboxCaptureLayout.Load(map);
            float[] xs = { 4500, 4500, 7600, 7600, 6162.963f }, zs = { 8500, 4400, 8500, 4400, 5800 };
            for (int i = 0; i < 5; i++)
            {
                var p = layout.points[i];
                Check(p.id == "P-0" + (i + 1) && Mathf.Abs(p.x - xs[i]) < .01f && p.z == zs[i], "approved coordinate " + p.id);
                var boundary = SandboxCaptureLayout.Boundary(map, p);
                Check(boundary.Length >= 5 && boundary[0] == boundary[boundary.Length - 1], "closed effective boundary " + p.id);
                if (p.support == "bridge_deck")
                {
                    Check(boundary.All(v => v.z >= 5783.999f && v.z <= 5816.001f && Mathf.Abs(v.y - 28) < .001f), "bridge width/elevation, not riverbed");
                    Check(boundary.All(v => (new Vector2(v.x - p.x, v.z - p.z)).sqrMagnitude <= 250 * 250 + 1), "bridge circle intersection");
                }
            }
            var input = new SandboxVictoryEvidence { mode = "qinglan_control", ruleVersion = SandboxBattleRules.CurrentProjectVersion,
                elapsedMinutes = 60, stages = Enumerable.Range(1, 3).Select(i => new SandboxGoalStage { stage = i, confirmed = true,
                    redGoals = 5, blueGoals = 0, evidence = "five points frozen fixture", objectives = layout.points.Select(p => new SandboxControlObjective {
                        point_id = p.id, name = p.name, owner = "red", redUnits = 1, blueUnits = 0 }).ToArray() }).ToArray() };
            var result = SandboxBattleRules.Evaluate(input);
            Check(result.final && result.redPoints == 30 && result.scoreMaximum == 30 && result.redScore100 == 100, "five-point scoring maximum");
            Check(SandboxBattleRules.Evaluate(SandboxBattleRules.ProjectPreview()).scoreMaximum == 18, "legacy replay unchanged");
            Debug.Log("[穹图] 五据点同源配置、桥面裁切及新旧计分检查通过；hash=" + SandboxCaptureLayout.ContentHash);
        }

        private static void Check(bool value, string message)
        {
            if (!value) throw new InvalidOperationException("五据点检查失败：" + message);
        }
    }
}
