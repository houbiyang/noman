using System;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>本页显式预演，与主持人历史记录分开；不接触对局命令、档案或存档。</summary>
    public sealed partial class SandboxHudController
    {
        [Serializable] private sealed class RehearsalFile
        {
            public RehearsalMetadata metadata;
            public SandboxReplayRecord[] records;
        }
        [Serializable] private sealed class RehearsalMetadata
        {
            public string label, scope, map_id, map_hash;
            public bool surface_accepted, database_writes;
        }
        private bool recordedIsRehearsal;

        private void InitializeRehearsalReplay(VisualElement parent)
        {
            var button = AddButton(parent, "坦克与补给预演", OpenTankSupplyRehearsal, "role-button");
            button.name = "tankSupplyRehearsal";
            button.tooltip = "在当前地图页面内只读预演一轮坦克互射与无人机补给；不创建或推进对局";
        }
        private void AddRehearsalResultEntry()
        {
            AddButton(settingsContent, "在本页地图预演：坦克互射与无人机补给",
                () => RequestCloseSettings(OpenTankSupplyRehearsal), "report-primary").name = "resultTankSupplyRehearsal";
        }
        private void OpenTankSupplyRehearsal()
        {
            // Never infer an authenticated history record from this asset, even when connected.
            CloseRecordedReplay(); ClosePanel(); CloseArmy(); CloseBattleEntry();
            CancelArmyCommand(); CloseSelectedUnitControls(); PausePlayback();
            recordedIsRehearsal = true;
            BuildRecordedReplayPanel();
            recordedOverlay.name = "tank-supply-rehearsal-overlay";
            recordedOverlay.Q<Label>(className: "recorded-title").text = "本页地图预演 · 坦克互射与无人机补给";
            recordedOverlay.Q<Label>(className: "recorded-note").text =
                "预演 · 非真实战果 · 当前地图外观 + 测试支撑面/视线适配器，未完成实图合法性验收；模型放大便于观察，距离/数值不变；不写入现有对局。";
            recordedRound.style.display = DisplayStyle.None;
            recordedLoad.style.display = DisplayStyle.None;
            recordedOverlay.Q<Button>("recordedFromStart").style.display = DisplayStyle.None;
            try
            {
                var asset = Resources.Load<TextAsset>("Replays/tank-supply-preview");
                if (!asset) throw new ArgumentException("缺少单轮预演资源");
                var file = JsonUtility.FromJson<RehearsalFile>(asset.text);
                var map = SandboxMapFactory.LoadDefinition();
                if (file?.metadata == null || file.metadata.database_writes || file.metadata.surface_accepted
                    || file.metadata.scope != "synthetic_geometry_with_current_map_visuals"
                    || file.metadata.map_id != map.mapId || file.metadata.map_hash != map.contentHash
                    || file.records == null || file.records.Length != 1)
                    throw new ArgumentException("预演来源或当前地图版本不一致，未播放");
                InstallRecordedReplay(file.records);
                recordedStage.FrameRehearsal(file.records[0].initial_world.units);
                recordedStatus.text = "60秒 / 12步 · 双方各6发、每发16伤害 · 保障车每5秒交付2能源，共24；悬停自耗2。非终局。";
                recordedRate = 1;
                recordedCursor.Play(); SetRecordedControls();
            }
            catch (Exception error) when (error is ArgumentException || error is InvalidOperationException)
            { RecordedFailure(error.Message); }
        }
        private void RenderRehearsalFacts(SandboxReplayWorld state)
        {
            recordedScore.text = "预演 " + state.elapsed_seconds + " / 60 秒 · 非正式战果";
            recordedFacts.Clear(); recordedAnnotations.Clear(); recordedUnitLabels.Clear();
            double delivered = recordedCursor.Events.Where(e => e.at_seconds <= state.elapsed_seconds
                && e.kind == "service" && e.resource == "energy" && e.target_id == "blue-recon-0")
                .Sum(e => SandboxRoundReplayCursor.DisplayNumber(e.amount));
            var drone = state.units.First(u => u.unit_id == "blue-recon-0");
            double consumed = 60 + delivered - SandboxRoundReplayCursor.DisplayNumber(drone.energy);
            AddText(recordedFacts, "坦克互射\n5、15、25、35、45、55秒同时结算；每发伤害16。", "recorded-point");
            foreach (var unit in state.units.OrderBy(u => u.unit_type == "ground" ? 0 : 1).ThenBy(u => u.side))
            {
                var captured = unit;
                string name = unit.unit_type == "ground" ? (unit.side == "blue" ? "蓝方坦克" : "红方坦克")
                    : unit.unit_type == "recon" ? "蓝方悬停无人机" : "蓝方保障车";
                string text = name + (unit.unit_type == "ground"
                    ? "\n耐久 " + ReplayNumber(unit.durability) + "/100 · 弹药 " + ReplayNumber(unit.ammo) + "/60"
                    : unit.unit_type == "recon" ? "\n能源 " + ReplayNumber(unit.energy) + "/100 · 悬停30米"
                    : "\n能源库存 " + (200 - delivered).ToString("0.##") + "/200");
                var button = AddButton(recordedFacts, text, () => recordedStage?.Focus(captured), "recorded-unit");
                button.AddToClassList(unit.side == "blue" ? "recorded-blue" : "recorded-red");
                var label = new Label(text) { pickingMode = PickingMode.Ignore };
                label.AddToClassList("recorded-map-label");
                label.AddToClassList(unit.side == "blue" ? "recorded-blue" : "recorded-red");
                recordedAnnotations.Add(label); recordedUnitLabels.Add(unit.unit_id, label);
            }
            AddText(recordedFacts, "补给计算\n已交付 " + delivered.ToString("0.##") + " 能源，悬停自耗 " + consumed.ToString("0.##")
                + "。\n轮末：60 + 24 − 2 = 82；保障车200 − 24 = 176。\n水平距离50米，小于100米。", "recorded-point");
            AddText(recordedFacts, "预演条件\n只读事件来自后端执行器。支撑面、视线、部署及服务合法性为测试条件，不冒充实图验收或历史对局。", "recorded-note");
        }
        private void PositionRehearsalLabels(float width, float height, float w, float h)
        {
            var occupied = new System.Collections.Generic.List<Rect>();
            foreach (var pair in recordedUnitLabels)
            {
                Vector3 point = recordedStage.ViewPosition(pair.Key);
                bool visible = point.z > 0 && point.x > 0 && point.x < 1 && point.y > 0 && point.y < 1;
                pair.Value.style.display = visible ? DisplayStyle.Flex : DisplayStyle.None;
                if (!visible) continue;
                float x = Mathf.Clamp((width - w) / 2 + point.x * w + 15, 0, Mathf.Max(0, width - 205));
                float y = Mathf.Clamp((height - h) / 2 + (1 - point.y) * h - 70, 0, Mathf.Max(0, height - 70));
                var rect = new Rect(x, y, 205, 64);
                for (int i = 0; i < 8 && occupied.Any(r => r.Overlaps(rect)); i++)
                {
                    rect.y += 68;
                    if (rect.y > height - 64) { rect.y = 0; rect.x = Mathf.Max(0, rect.x - 215); }
                }
                occupied.Add(rect);
                pair.Value.style.left = rect.x; pair.Value.style.top = rect.y;
            }
        }
    }
}
