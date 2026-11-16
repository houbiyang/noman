using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    /// <summary>主持人逐轮只读放映室；网络仅GET，历史状态只送入隔离舞台，不覆盖实时投影。</summary>
    public sealed partial class SandboxHudController
    {
        private Button recordedReplayEntry, recordedPlay, recordedLoad;
        private VisualElement recordedOverlay, recordedAnnotations;
        private Label recordedStatus, recordedTime, recordedScore;
        private Image recordedViewport;
        private ScrollView recordedFacts;
        private DropdownField recordedRound, recordedSpeed;
        private Slider recordedSeek;
        private SandboxRoundReplayStage recordedStage;
        private SandboxRoundReplayCursor recordedCursor;
        private SandboxReplayIndex recordedIndex;
        private readonly Dictionary<string, Label> recordedUnitLabels = new Dictionary<string, Label>();
        private string recordedScope;
        private int recordedGeneration, recordedFactsAt = -1;
        private bool recordedLoading;
        private float recordedRate = 1;

        private string RecordedScope => !serverMode || !serverClient || !serverClient.Healthy || serverClient.View?.role != "host"
            ? null : serverClient.ConnectionGeneration + ":" + serverClient.View.context?.game_id + ":" + serverClient.View.context?.branch_id;
        private void InitializeRecordedReplay()
        {
            var parent = root.Q("connectionAction")?.parent;
            if (parent == null) return;
            recordedReplayEntry = AddButton(parent, "逐轮回放", OpenRecordedReplay, "role-button");
            recordedReplayEntry.name = "recordedReplayEntry";
            RefreshRecordedReplayEntry();
        }
        private void RefreshRecordedReplayEntry()
        {
            if (recordedOverlay != null && (RecordedScope == null || RecordedScope != recordedScope)) CloseRecordedReplay();
            if (recordedReplayEntry == null) return;
            bool host = role == SandboxRole.Host;
            recordedReplayEntry.style.display = host ? DisplayStyle.Flex : DisplayStyle.None;
            bool ready = host && RecordedScope != null && serverClient.View.manual_battle?.completed_rounds > 0;
            recordedReplayEntry.SetEnabled(ready && !serverClient.Busy && !serverClient.HasPending);
            recordedReplayEntry.tooltip = ready ? "只读播放已完成步长，不推进战斗、不重复扣资源或计分"
                : "仅主持人可回放；总册手动新局完成首轮并保存动画后开放（旧局不生成假回放）";
        }
        private void OpenRecordedReplay()
        {
            if (RecordedScope == null || serverClient.Busy || serverClient.HasPending || serverClient.View.manual_battle?.completed_rounds < 1
                || serverClient.View.manual_battle == null) return;
            CloseRecordedReplay(); ClosePanel(); CloseArmy(); CancelArmyCommand(); CloseSelectedUnitControls(); PausePlayback();
            recordedScope = RecordedScope;
            BuildRecordedReplayPanel();
            int generation = recordedGeneration; string scope = recordedScope;
            recordedLoading = true; SetRecordedControls();
            if (!serverClient.LoadBattleReplayIndex(index =>
            {
                if (!RecordedRequestCurrent(generation, scope)) return;
                recordedLoading = false;
                try
                {
                    if (index == null || !index.read_only || index.writes_game_state || index.items == null
                        || index.completed_rounds < 1 || index.completed_rounds > 60 || index.items.Length != index.completed_rounds
                        || !index.items.Select(i => i?.round_number ?? 0).SequenceEqual(Enumerable.Range(1, index.completed_rounds)))
                        throw new ArgumentException("回放索引缺失或不连续，未加载动画");
                    recordedIndex = index;
                    recordedRound.choices = index.items.Select(i => "第 " + i.round_number + " 轮").ToList();
                    recordedRound.SetValueWithoutNotify(recordedRound.choices.Last());
                    recordedStatus.text = "已保存 " + index.completed_rounds + " 轮 · 选择单轮，或从第1轮连续观看至本次打开时的最新轮。";
                    SetRecordedControls();
                }
                catch (ArgumentException error) { RecordedFailure(error.Message); }
            })) RecordedFailure("同步忙碌，请关闭后重试；未写入战斗状态。");
        }
        private bool RecordedRequestCurrent(int generation, string scope) => recordedOverlay != null && generation == recordedGeneration
            && scope == recordedScope && scope == RecordedScope;
        private void BuildRecordedReplayPanel()
        {
            recordedOverlay = new VisualElement { name = "recorded-replay-overlay" };
            recordedOverlay.AddToClassList("modal-backdrop"); recordedOverlay.AddToClassList("ui-blocker");
            recordedOverlay.styleSheets.Add(Resources.Load<StyleSheet>("Sandbox/SandboxRoundReplay"));
            var card = new VisualElement { name = "recorded-replay-card" }; card.AddToClassList("recorded-card"); recordedOverlay.Add(card);
            var header = RecordedRow(card);
            AddText(header, "主持人 · 演示动画放映室", "recorded-title");
            AddButton(header, "关闭", CloseRecordedReplay, "army-unit-action").name = "closeRecordedReplay";
            AddText(card, "历史只读 · 1轮 = 60仿真秒（提前终局除外） · 拖动/重播不提交命令，不改变实时战局", "recorded-note");
            var toolbar = RecordedRow(card);
            recordedRound = new DropdownField("轮次", new List<string> { "等待索引" }, 0); toolbar.Add(recordedRound);
            recordedLoad = AddButton(toolbar, "观看本轮", () => LoadRecordedRounds(false), "army-unit-action");
            AddButton(toolbar, "从头至最新轮", () => LoadRecordedRounds(true), "army-unit-action").name = "recordedFromStart";
            recordedStatus = AddText(card, "正在读取已完成轮次索引…", "recorded-note");
            var body = new VisualElement(); body.AddToClassList("recorded-body"); card.Add(body);
            var viewportWrap = new VisualElement(); viewportWrap.AddToClassList("recorded-view-wrap"); body.Add(viewportWrap);
            recordedViewport = new Image { name = "recorded-viewport", scaleMode = ScaleMode.ScaleToFit };
            recordedViewport.AddToClassList("recorded-viewport"); viewportWrap.Add(recordedViewport);
            recordedAnnotations = new VisualElement { pickingMode = PickingMode.Ignore }; recordedAnnotations.AddToClassList("recorded-annotations");
            viewportWrap.Add(recordedAnnotations);
            var sidebar = new VisualElement(); sidebar.AddToClassList("recorded-sidebar"); body.Add(sidebar);
            recordedScore = AddText(sidebar, "历史状态 · 尚未加载", "recorded-score");
            recordedFacts = new ScrollView(ScrollViewMode.Vertical); recordedFacts.AddToClassList("recorded-facts"); sidebar.Add(recordedFacts);
            var controls = RecordedRow(card);
            recordedPlay = AddButton(controls, "播放", ToggleRecordedReplay, "army-unit-action");
            recordedPlay.name = "recordedPlay";
            AddButton(controls, "回到开头", () => { if (recordedCursor == null) return; recordedCursor.Pause(); SeekRecorded(recordedCursor.FirstSeconds); }, "army-unit-action").name = "recordedRewind";
            recordedSpeed = new DropdownField("倍速", new List<string> { "0.5×", "1×", "2×", "4×" }, 1);
            recordedSpeed.RegisterValueChangedCallback(e => recordedRate = e.newValue == "0.5×" ? .5f : e.newValue == "2×" ? 2 : e.newValue == "4×" ? 4 : 1);
            controls.Add(recordedSpeed);
            AddButton(controls, "全图", () => recordedStage?.Overview(), "army-unit-action");
            AddButton(controls, "拉近", () => recordedStage?.Zoom(.7f), "army-unit-action");
            AddButton(controls, "拉远", () => recordedStage?.Zoom(1.4f), "army-unit-action");
            recordedTime = AddText(controls, "0 / 0 仿真秒", "recorded-clock");
            recordedSeek = new Slider(0, 60) { name = "recordedSeek" }; card.Add(recordedSeek);
            recordedSeek.RegisterValueChangedCallback(e => { if (recordedCursor == null) return; recordedCursor.Pause(); SeekRecorded(e.newValue); });
            root.Add(recordedOverlay); SetRecordedControls();
        }
        private static VisualElement RecordedRow(VisualElement parent)
        {
            var row = new VisualElement(); row.AddToClassList("recorded-row"); parent.Add(row); return row;
        }
        private void SetRecordedControls()
        {
            bool ready = !recordedLoading && recordedIndex != null;
            recordedLoad?.SetEnabled(ready); recordedRound?.SetEnabled(ready);
            recordedOverlay?.Q<Button>("recordedFromStart")?.SetEnabled(ready);
            recordedPlay?.SetEnabled(!recordedLoading && recordedCursor != null);
            recordedSeek?.SetEnabled(!recordedLoading && recordedCursor != null);
            recordedOverlay?.Q<Button>("recordedRewind")?.SetEnabled(!recordedLoading && recordedCursor != null);
            if (recordedPlay != null) recordedPlay.text = recordedCursor?.Playing == true ? "暂停" : "播放";
        }
        private void LoadRecordedRounds(bool fromStart)
        {
            if (recordedLoading || recordedIndex == null || recordedScope != RecordedScope) return;
            recordedCursor?.Pause(); recordedLoading = true; SetRecordedControls();
            int selected = recordedRound.choices.IndexOf(recordedRound.value) + 1;
            int first = fromStart ? 1 : selected, last = fromStart ? recordedIndex.completed_rounds : selected;
            if (first < 1 || last > recordedIndex.completed_rounds) { RecordedFailure("轮次无效"); return; }
            // 逐轮GET；本次观看的最新上限冻结，后续实时轮次不会偷偷插入播放列表。
            var records = new List<SandboxReplayRecord>();
            LoadRecordedNext(first, last, recordedGeneration, recordedScope, records);
        }
        private void LoadRecordedNext(int number, int last, int generation, string scope, List<SandboxReplayRecord> records)
        {
            if (!RecordedRequestCurrent(generation, scope)) return;
            recordedStatus.text = "正在读取第 " + number + " / " + last + " 轮（只读）…";
            if (!serverClient.LoadBattleReplayRound(number, response =>
            {
                if (!RecordedRequestCurrent(generation, scope)) return;
                try
                {
                    var r = response?.record; var index = recordedIndex.items[number - 1];
                    if (response == null || !response.read_only || response.writes_game_state || r == null || r.round_number != number
                        || r.payload_sha256 != index.payload_sha256 || r.start_seconds != index.start_seconds || r.end_seconds != index.end_seconds)
                        throw new ArgumentException("记录与索引不一致或读取失败，停止播放");
                    records.Add(r);
                    if (number < last) LoadRecordedNext(number + 1, last, generation, scope, records);
                    else InstallRecordedReplay(records.ToArray());
                }
                catch (Exception error) when (error is ArgumentException || error is InvalidOperationException)
                { RecordedFailure(error.Message); }
            })) RecordedFailure("同步忙碌，未完成读取；原战局不受影响，请重试。");
        }
        private void InstallRecordedReplay(SandboxReplayRecord[] records)
        {
            var cursor = new SandboxRoundReplayCursor(records);
            ReleaseRecordedStage();
            var stageObject = new GameObject("Isolated host replay stage");
            recordedStage = stageObject.AddComponent<SandboxRoundReplayStage>(); recordedStage.Initialize(world);
            recordedCursor = cursor; recordedViewport.image = recordedStage.Texture;
            recordedSeek.lowValue = cursor.FirstSeconds; recordedSeek.highValue = cursor.LastSeconds;
            recordedFactsAt = -1; recordedLoading = false;
            recordedStatus.text = records.Length == 1 ? "第 " + records[0].round_number + " 轮 · 点击单位信息定位镜头" : "连续观看第1轮至第 " + records.Last().round_number + " 轮 · 点击单位信息定位镜头";
            SeekRecorded(cursor.FirstSeconds); SetRecordedControls();
        }
        private void ToggleRecordedReplay()
        {
            if (recordedCursor == null || recordedLoading) return;
            if (recordedCursor.Playing) recordedCursor.Pause();
            else { if (recordedCursor.Seconds >= recordedCursor.LastSeconds) recordedCursor.Seek(recordedCursor.FirstSeconds); recordedCursor.Play(); }
            SetRecordedControls();
        }
        private void SeekRecorded(double seconds)
        {
            if (recordedCursor == null) return;
            recordedCursor.Seek(seconds); SampleRecordedReplay(); SetRecordedControls();
        }
        private void TickRecordedReplay()
        {
            if (recordedOverlay == null || recordedCursor == null || recordedLoading) return;
            if (recordedScope != RecordedScope) { CloseRecordedReplay(); return; }
            recordedCursor.Advance(Time.unscaledDeltaTime * recordedRate); SampleRecordedReplay();
        }
        private void SampleRecordedReplay()
        {
            recordedStage.Sample(recordedCursor);
            recordedSeek.SetValueWithoutNotify((float)recordedCursor.Seconds);
            recordedTime.text = recordedCursor.Seconds.ToString("0.0", CultureInfo.InvariantCulture) + " / " + recordedCursor.LastSeconds + " 仿真秒";
            if (recordedFactsAt != recordedCursor.World.elapsed_seconds)
            { recordedFactsAt = recordedCursor.World.elapsed_seconds; RenderRecordedFacts(recordedCursor.World); }
            PositionRecordedLabels();
            recordedPlay.text = recordedCursor.Playing ? "暂停" : "播放";
        }
        private static string ReplayNumber(string number) => SandboxRoundReplayCursor.DisplayNumber(number).ToString("0.##", CultureInfo.InvariantCulture);
        private static string RecordedUnitText(SandboxReplayUnit u) => u.unit_id + (u.retired ? " · 已退场" : "")
            + "\n血量 " + ReplayNumber(u.durability) + "/" + ReplayNumber(u.max_durability)
            + "  弹药 " + ReplayNumber(u.ammo) + "/" + ReplayNumber(u.max_ammo)
            + "\n能源 " + ReplayNumber(u.energy) + "/" + ReplayNumber(u.max_energy)
            + "  移速 " + ReplayNumber(u.speed_m_per_minute) + " 米/分"
            + (string.IsNullOrEmpty(u.attack_target_id) ? "" : "\n攻击 → " + u.attack_target_id);
        private void RenderRecordedFacts(SandboxReplayWorld state)
        {
            recordedScore.text = "历史 " + state.elapsed_seconds + " 秒 · 蓝 " + ReplayNumber(state.blue_score) + " / 红 " + ReplayNumber(state.red_score);
            recordedFacts.Clear(); recordedAnnotations.Clear(); recordedUnitLabels.Clear();
            foreach (var point in state.capture_points)
                AddText(recordedFacts, point.point_id + " · " + (point.owner == "blue" ? "蓝方" : point.owner == "red" ? "红方" : "中立")
                    + (point.contested ? " · 争夺中" : "") + "\n" + (point.advancing_side == "none" ? "无占领推进" : (point.advancing_side == "blue" ? "蓝方" : "红方") + "推进 " + (point.progress_steps * 100 / 6) + "%"), "recorded-point");
            foreach (var unit in state.units)
            {
                var captured = unit;
                var button = AddButton(recordedFacts, RecordedUnitText(unit), () => recordedStage?.Focus(captured), "recorded-unit");
                button.AddToClassList(unit.side == "blue" ? "recorded-blue" : "recorded-red");
                var label = new Label(RecordedUnitText(unit)) { pickingMode = PickingMode.Ignore };
                label.AddToClassList("recorded-map-label"); label.AddToClassList(unit.side == "blue" ? "recorded-blue" : "recorded-red");
                recordedAnnotations.Add(label); recordedUnitLabels.Add(unit.unit_id, label);
            }
        }
        private void PositionRecordedLabels()
        {
            if (!recordedStage) return;
            float width = recordedViewport.resolvedStyle.width, height = recordedViewport.resolvedStyle.height;
            if (!(width > 0 && height > 0)) return;
            // Image使用ScaleToFit，标签必须扣除黑边，否则宽屏/窄屏会与模型错位。
            float fit = Mathf.Min(width / 1280, height / 720), w = 1280 * fit, h = 720 * fit;
            foreach (var pair in recordedUnitLabels)
            {
                Vector3 position = recordedStage.ViewPosition(pair.Key);
                bool visible = position.z > 0 && position.x > 0 && position.x < 1 && position.y > 0 && position.y < 1;
                pair.Value.style.display = visible ? DisplayStyle.Flex : DisplayStyle.None;
                pair.Value.style.left = Mathf.Clamp((width - w) / 2 + position.x * w, 0, Mathf.Max(0, width - 205));
                pair.Value.style.top = Mathf.Clamp((height - h) / 2 + (1 - position.y) * h, 0, Mathf.Max(0, height - 90));
            }
        }
        private void RecordedFailure(string message)
        {
            recordedLoading = false; recordedCursor?.Pause();
            // 不能让部分失败的下一次加载继续显示上一份动画，避免误认为新轮已成功。
            ReleaseRecordedStage(); recordedCursor = null;
            recordedFacts?.Clear(); recordedAnnotations?.Clear(); recordedUnitLabels.Clear();
            if (recordedStatus != null) recordedStatus.text = message + " · 未执行任何战斗命令。";
            if (recordedScore != null) recordedScore.text = "历史状态未加载";
            SetRecordedControls();
        }
        private void PauseRecordedReplay() { recordedCursor?.Pause(); SetRecordedControls(); }
        private void ReleaseRecordedStage()
        {
            if (recordedViewport != null) recordedViewport.image = null;
            if (!recordedStage) return;
            recordedStage.Release(); Destroy(recordedStage.gameObject); recordedStage = null;
        }
        private void CloseRecordedReplay()
        {
            recordedGeneration++; recordedCursor?.Pause(); ReleaseRecordedStage();
            recordedOverlay?.RemoveFromHierarchy(); recordedOverlay = null; recordedCursor = null; recordedIndex = null;
            recordedScope = null; recordedLoading = false; recordedFactsAt = -1; recordedRate = 1; recordedUnitLabels.Clear();
            recordedStatus = null; recordedScore = null; recordedTime = null; recordedViewport = null; recordedFacts = null;
            recordedAnnotations = null; recordedRound = null; recordedSeek = null; recordedPlay = null; recordedLoad = null; recordedSpeed = null;
        }
    }
}
