using System;
using System.Collections.Generic;
using System.Collections;
using UnityEngine.Networking;
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
        private Label recordedStatus, recordedTime, recordedScore, recordedPoints, recordedEventSummary;
        private DropdownField recordedUnitFocus;
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
        private bool recordedLoading, recordedAutoPending;
        private float recordedRate = 1;

        // Explicit sidecar viewer: never initializes the game client or accesses a database.
        // No payload is bundled into Resources and the ordinary game URL is unchanged.
        [Serializable] private sealed class ExportedReplay { public string source; public SandboxReplayRecord[] records; }
        private string exportedReplayPath, exportedReplaySource;
        private SandboxReplayRecord[] exportedReplayRecords;
        private bool TryInitializeExportedReplay(string absoluteUrl)
        {
            if (!Uri.TryCreate(absoluteUrl, UriKind.Absolute, out var page)) return false;
            string candidate = null; bool requested = false;
            foreach (string pair in page.Query.TrimStart('?').Split('&'))
            {
                var parts = pair.Split(new[] { '=' }, 2);
                if (parts[0] == "replayFile") { requested = true; candidate = parts.Length == 2 ? Uri.UnescapeDataString(parts[1]) : ""; }
            }
            if (!requested) return false;
            bool valid = candidate != null && System.Text.RegularExpressions.Regex.IsMatch(candidate, @"^validation/[a-z0-9-]+\.json$");
            exportedReplayPath = valid ? candidate : "invalid"; recordedScope = RecordedScope;
            BuildInMapReplayControls(); recordedLoading = true; SetRecordedControls();
            foreach (var child in root.Children().ToArray())
                if (child != recordedOverlay) child.style.display = DisplayStyle.None;
            recordedStatus.text = "独立只读历史文件加载中 · 不连接当前用户局、不执行命令。";
            if (valid) StartCoroutine(LoadExportedReplay(new Uri(page, candidate).AbsoluteUri));
            else RecordedFailure("只读路径无效；未连接游戏API");
            return true;
        }
        private IEnumerator LoadExportedReplay(string url)
        {
            using (var request = UnityWebRequest.Get(url))
            {
                request.timeout = 30;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success) { RecordedFailure("只读导出读取失败"); yield break; }
                try
                {
                    var data = JsonUtility.FromJson<ExportedReplay>(request.downloadHandler.text);
                    // Same cursor validation as normal persisted replay; no geometry or business execution.
                    _ = new SandboxRoundReplayCursor(data?.records);
                    exportedReplayRecords = data.records;
                    exportedReplaySource = string.IsNullOrEmpty(data.source) ? "独立测试历史导出" : data.source;
                    recordedIndex = new SandboxReplayIndex { read_only = true, completed_rounds = data.records.Length,
                        items = data.records.Select(r => new SandboxReplayIndexItem { round_number = r.round_number,
                            start_seconds = r.start_seconds, end_seconds = r.end_seconds, payload_sha256 = r.payload_sha256 }).ToArray() };
                    if (!recordedIndex.items.Select(r => r.round_number).SequenceEqual(Enumerable.Range(1, data.records.Length)))
                        throw new ArgumentException("只读导出须从第1轮连续开始");
                    recordedRound.choices = recordedIndex.items.Select(r => "第 " + r.round_number + " 轮").ToList();
                    recordedRound.SetValueWithoutNotify(recordedRound.choices.Last());
                    InstallRecordedReplay(data.records); recordedCursor.Pause(); SetRecordedControls();
                }
                catch (Exception error) { RecordedFailure("只读导出无效：" + error.Message); }
            }
        }

        private string RecordedScope => exportedReplayPath != null ? "exported-read-only:" + exportedReplayPath : !serverMode || !serverClient || !serverClient.Healthy || serverClient.View?.role != "host"
            ? null : serverClient.ConnectionGeneration + ":" + serverClient.View.context?.game_id + ":" + serverClient.View.context?.branch_id;
        private void InitializeRecordedReplay()
        {
            var parent = root.Q("connectionAction")?.parent;
            if (parent == null) return;
            recordedReplayEntry = AddButton(parent, "地图回放", OpenRecordedReplay, "role-button");
            recordedReplayEntry.name = "recordedReplayEntry";
            InitializeRehearsalReplay(parent);
            RefreshRecordedReplayEntry();
        }
        private void RefreshRecordedReplayEntry()
        {
            if (recordedOverlay != null && !recordedIsRehearsal && (RecordedScope == null || RecordedScope != recordedScope)) CloseRecordedReplay();
            if (recordedReplayEntry == null) return;
            if (exportedReplayPath != null) { recordedReplayEntry.style.display = DisplayStyle.None; return; }
            bool host = role == SandboxRole.Host && !deploymentStaging;
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
                    LoadRecordedRounds(false);
                }
                catch (ArgumentException error) { RecordedFailure(error.Message); }
            })) RecordedFailure("同步忙碌，请关闭后重试；未写入战斗状态。");
        }
        private bool RecordedRequestCurrent(int generation, string scope) => recordedOverlay != null && generation == recordedGeneration
            && scope == recordedScope && scope == RecordedScope;
        private void BuildRecordedReplayPanel()
        {
            if (!recordedIsRehearsal) { BuildInMapReplayControls(); return; }
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
        private void BuildInMapReplayControls()
        {
            recordedOverlay = new VisualElement { name = "recorded-replay-overlay" };
            recordedOverlay.pickingMode = PickingMode.Ignore;
            recordedOverlay.styleSheets.Add(Resources.Load<StyleSheet>("Sandbox/SandboxRoundReplay"));
            recordedOverlay.style.position = Position.Absolute;
            recordedOverlay.style.left = 0; recordedOverlay.style.top = 0;
            recordedOverlay.style.right = 0; recordedOverlay.style.bottom = 0;
            recordedAnnotations = new VisualElement { pickingMode = PickingMode.Ignore };
            recordedAnnotations.AddToClassList("recorded-annotations"); recordedOverlay.Add(recordedAnnotations);
            var card = new VisualElement { name = "recorded-replay-card" };
            card.AddToClassList("recorded-map-controls"); card.AddToClassList("ui-blocker"); recordedOverlay.Add(card);
            var row = RecordedRow(card);
            AddText(row, "原地图历史播放 · 只读", "recorded-title");
            recordedRound = new DropdownField(new List<string> { "等待索引" }, 0); recordedRound.name = "recordedRound"; row.Add(recordedRound);
            recordedLoad = AddButton(row, "单轮", () => LoadRecordedRounds(false), "army-unit-action");
            AddButton(row, "从头至最新轮", () => LoadRecordedRounds(true), "army-unit-action").name = "recordedFromStart";
            recordedPlay = AddButton(row, "播放", ToggleRecordedReplay, "army-unit-action"); recordedPlay.name = "recordedPlay";
            var close = AddButton(row, "退出并恢复最新战局", CloseRecordedReplay, "army-unit-action"); close.name = "closeRecordedReplay";
            if (exportedReplayPath != null) close.style.display = DisplayStyle.None;
            recordedUnitFocus = new DropdownField(new List<string> { "定位历史单位" }, 0); recordedUnitFocus.name = "recordedUnitFocus"; row.Add(recordedUnitFocus);
            recordedUnitFocus.RegisterValueChangedCallback(e => {
                var unit = recordedCursor?.World.units.FirstOrDefault(u => u.unit_id == e.newValue);
                if (unit != null && recordedStage) cameraRig?.ObserveRecordedPoint(recordedStage.RecordedPosition(unit.unit_id));
            });
            recordedTime = AddText(row, "0 / 0 仿真秒", "recorded-clock");
            var navigation = RecordedRow(card);
            AddButton(navigation, "回到开头", () => { recordedCursor?.Pause(); if (recordedCursor != null) SeekRecorded(recordedCursor.FirstSeconds); }, "army-unit-action").name = "recordedRewind";
            AddButton(navigation, "上一事件", () => JumpRecordedEvent(false), "army-unit-action").name = "recordedPreviousEvent";
            AddButton(navigation, "下一事件", () => JumpRecordedEvent(true), "army-unit-action").name = "recordedNextEvent";
            AddButton(navigation, "定位最近事件", FocusRecordedEvent, "army-unit-action").name = "recordedFocusEvent";
            AddButton(navigation, "全图", () => cameraRig?.ObserveRecordedBounds(new Bounds(new Vector3(6000, 0, 6000), new Vector3(12000, 200, 12000))), "army-unit-action").name = "recordedOverview";
            recordedSpeed = new DropdownField(new List<string> { "0.5×", "1×", "2×", "4×" }, 1); recordedSpeed.name = "recordedSpeed";
            recordedSpeed.RegisterValueChangedCallback(e => recordedRate = e.newValue == "0.5×" ? .5f : e.newValue == "2×" ? 2 : e.newValue == "4×" ? 4 : 1); navigation.Add(recordedSpeed);
            recordedEventSummary = AddText(card, "尚无已播放事件", "recorded-event-summary"); recordedEventSummary.name = "recordedEventSummary";
            recordedSeek = new Slider(0, 60) { name = "recordedSeek" }; card.Add(recordedSeek);
            recordedSeek.RegisterValueChangedCallback(e => { if (recordedCursor == null) return; recordedCursor.Pause(); SeekRecorded(e.newValue); });
            recordedStatus = AddText(card, "读取历史记录中；镜头仍可平移、旋转、缩放。回放期间禁止战斗命令。", "recorded-note");
            recordedScore = AddText(card, "历史状态尚未加载", "recorded-score");
            recordedPoints = AddText(card, "", "recorded-note"); recordedPoints.name = "recordedCaptureFacts";
            // Facts are kept for semantic inspection, while models and labels use the original map.
            recordedFacts = new ScrollView(); recordedFacts.style.display = DisplayStyle.None; card.Add(recordedFacts);
            root.Add(recordedOverlay);
            world?.SetHistoricalPresentation(true);
            root.Q("mapAnnotations")?.SetEnabled(false);
            if (root.Q("mapAnnotations") != null) root.Q("mapAnnotations").style.display = DisplayStyle.None;
            if (manualBattleBar != null) manualBattleBar.style.display = DisplayStyle.None;
            SetRecordedControls();
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
            foreach (string id in new[] { "recordedPreviousEvent", "recordedNextEvent", "recordedFocusEvent", "recordedOverview" })
                recordedOverlay?.Q<Button>(id)?.SetEnabled(!recordedLoading && recordedCursor != null);
            recordedSpeed?.SetEnabled(!recordedLoading && recordedCursor != null);
            if (recordedPlay != null) recordedPlay.text = recordedCursor?.Playing == true ? "暂停" : "播放";
        }
        private void LoadRecordedRounds(bool fromStart)
        {
            if (recordedLoading || recordedIndex == null || recordedScope != RecordedScope) return;
            recordedCursor?.Pause(); recordedLoading = true; SetRecordedControls();
            int selected = recordedRound.choices.IndexOf(recordedRound.value) + 1;
            int first = fromStart ? 1 : selected, last = fromStart ? recordedIndex.completed_rounds : selected;
            if (first < 1 || last > recordedIndex.completed_rounds) { RecordedFailure("轮次无效"); return; }
            if (exportedReplayRecords != null)
            {
                InstallRecordedReplay(exportedReplayRecords.Skip(first - 1).Take(last - first + 1).ToArray());
                recordedCursor.Pause(); SetRecordedControls(); return;
            }
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
            var stageObject = new GameObject("Read-only historical actors on original map");
            if (!recordedIsRehearsal)
            {
                world?.SetHistoricalPresentation(true);
                if (root.Q("mapAnnotations") != null) root.Q("mapAnnotations").style.display = DisplayStyle.None;
            }
            recordedStage = stageObject.AddComponent<SandboxRoundReplayStage>(); recordedStage.Initialize(world, !recordedIsRehearsal);
            recordedCursor = cursor; if (recordedViewport != null) recordedViewport.image = recordedStage.Texture;
            recordedSeek.lowValue = cursor.FirstSeconds; recordedSeek.highValue = cursor.LastSeconds;
            recordedFactsAt = -1; recordedLoading = false;
            recordedStatus.text = records.Length == 1 ? "第 " + records[0].round_number + " 轮 · 使用单位下拉定位；镜头可操作" : "连续观看第1轮至第 " + records.Last().round_number + " 轮 · 使用单位下拉定位；镜头可操作";
            if (exportedReplayPath != null) recordedStatus.text = exportedReplaySource + " · 独立只读导出，非当前用户局；未连接游戏API。";
            SeekRecorded(cursor.FirstSeconds);
            if (!recordedIsRehearsal)
            {
                var first = cursor.World.units.FirstOrDefault(u => !u.retired);
                if (first != null) cameraRig?.ObserveRecordedPoint(recordedStage.RecordedPosition(first.unit_id));
                cursor.Play();
            }
            SetRecordedControls();
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
            if (!recordedIsRehearsal && recordedScope != RecordedScope) { CloseRecordedReplay(); return; }
            recordedCursor.Advance(Time.unscaledDeltaTime * recordedRate); SampleRecordedReplay();
        }
        private void SampleRecordedReplay()
        {
            recordedStage.Sample(recordedCursor);
            recordedSeek.SetValueWithoutNotify((float)recordedCursor.Seconds);
            recordedTime.text = "第 " + recordedCursor.CurrentRound + " 轮 · " + recordedCursor.Seconds.ToString("0.0", CultureInfo.InvariantCulture) + " / " + recordedCursor.LastSeconds + " 仿真秒";
            if (recordedFactsAt != recordedCursor.World.elapsed_seconds)
            { recordedFactsAt = recordedCursor.World.elapsed_seconds; RenderRecordedFacts(recordedCursor.World); }
            RefreshRecordedEventSummary();
            PositionRecordedLabels();
            recordedPlay.text = recordedCursor.Playing ? "暂停" : "播放";
            if (exportedReplayPath != null) { recordedStatus.text = exportedReplaySource + " · 独立只读导出，非当前用户局；未连接游戏API。"; return; }
            if (!recordedIsRehearsal && recordedCursor.Seconds < recordedCursor.LastSeconds) recordedStatus.text = "历史只读 · 镜头可操作；单位下拉定位。退出后恢复最新战局。";
            if (!recordedIsRehearsal && recordedCursor.Seconds >= recordedCursor.LastSeconds) recordedStatus.text = "已播至最后一帧 · 保留历史暂停画面；退出后恢复最新战局。镜头可继续操作。";
        }
        private static bool IsRecordedAction(SandboxReplayEvent e) => e.kind == "shot" || e.kind == "hit"
            || e.kind == "service" || e.kind == "barracks_service" || e.kind == "takeoff" || e.kind == "land" || e.kind == "retired" || e.kind == "capture";
        private SandboxReplayEvent LatestRecordedAction() => recordedCursor?.Events.LastOrDefault(e => IsRecordedAction(e) && e.at_seconds <= recordedCursor.Seconds);
        private void JumpRecordedEvent(bool forward)
        {
            if (recordedCursor == null || recordedLoading) return;
            var times = recordedCursor.Events.Where(IsRecordedAction).Select(e => e.at_seconds).Distinct().OrderBy(t => t).ToArray();
            var candidates = forward ? times.Where(t => t > recordedCursor.Seconds).ToArray()
                : times.Where(t => t < Math.Floor(recordedCursor.Seconds)).Reverse().ToArray();
            if (candidates.Length == 0) { recordedCursor.Pause(); SetRecordedControls(); return; }
            recordedCursor.Pause(); SeekRecorded(Math.Min(recordedCursor.LastSeconds, candidates[0] + .1)); FocusRecordedEvent();
        }
        private void FocusRecordedEvent()
        {
            var e = LatestRecordedAction(); if (e == null) return;
            if (e.kind == "capture" && recordedStage && recordedStage.TryCapturePosition(e.source_id, out var point))
            { cameraRig?.ObserveRecordedPoint(point); return; }
            if (e.source_position == null || e.target_position == null) return;
            var bounds = new Bounds(SandboxRoundReplayStage.Position(e.source_position), Vector3.one * 80);
            bounds.Encapsulate(SandboxRoundReplayStage.Position(e.target_position));
            cameraRig?.ObserveRecordedBounds(bounds);
        }
        private static string RecordedEventText(SandboxReplayEvent e)
        {
            string source = e.camp_id ?? e.source_id;
            string resource = e.resource == "energy" ? "能源" : e.resource == "ammo" ? "弹药" : "维修";
            if (e.kind == "service" || e.kind == "barracks_service") return source + " → " + e.target_id + " "
                + (e.kind == "barracks_service" ? "兵营交付" : "补给交付") + resource + " +" + ReplayNumber(e.amount);
            if (e.kind == "capture") return source + " 占领权变更（以历史点位状态为准）";
            if (e.kind == "shot") return source + " → " + e.target_id + " 开火";
            if (e.kind == "hit") return source + " → " + e.target_id + " 命中 · 耐久 -" + ReplayNumber(e.amount);
            return source + (e.kind == "takeoff" ? " 起飞" : e.kind == "land" ? " 降落" : " 已退场");
        }
        private void RefreshRecordedEventSummary()
        {
            if (recordedEventSummary == null || recordedCursor == null) return;
            var latest = LatestRecordedAction();
            if (latest == null) { recordedEventSummary.text = "当前时刻尚无已记录攻击/交付/起降事件；不制造演示效果。"; return; }
            var batch = recordedCursor.Events.Where(e => IsRecordedAction(e) && e.at_seconds == latest.at_seconds).ToArray();
            recordedEventSummary.text = "最近记录 " + latest.at_seconds + " 秒 · " + string.Join("  |  ", batch.Take(3).Select(RecordedEventText))
                + (batch.Length > 3 ? " · 共" + batch.Length + "条" : "") + "（资源标签为已保存的5秒快照；绿=能源/橙=弹药/蓝=维修）";
        }
        private static string ReplayNumber(string number) => SandboxRoundReplayCursor.DisplayNumber(number).ToString("0.##", CultureInfo.InvariantCulture);
        private static string RecordedUnitText(SandboxReplayUnit u) => u.unit_id + (u.unit_type == "recon" ? (u.docked ? " · 停驻无人机" : " · 空中无人机") : u.unit_type == "support" ? " · 保障车" : " · 履带单位") + (u.retired ? " · 已退场" : "")
            + "\n血量 " + ReplayNumber(u.durability) + "/" + ReplayNumber(u.max_durability)
            + "  弹药 " + ReplayNumber(u.ammo) + "/" + ReplayNumber(u.max_ammo)
            + "\n能源 " + ReplayNumber(u.energy) + "/" + ReplayNumber(u.max_energy)
            + "  移速 " + ReplayNumber(u.speed_m_per_minute) + " 米/分"
            + (u.unit_type == "recon" && !u.docked ? "\n高度 " + (u.position.height_mm / 1000f).ToString("0.##", CultureInfo.InvariantCulture) + " 米（记录世界高度）" : "")
            + (string.IsNullOrEmpty(u.attack_target_id) ? "" : "\n攻击 → " + u.attack_target_id);
        private void RenderRecordedFacts(SandboxReplayWorld state)
        {
            if (recordedIsRehearsal) { RenderRehearsalFacts(state); return; }
            recordedScore.text = "历史 " + state.elapsed_seconds + " 秒 · 蓝 " + ReplayNumber(state.blue_score) + " / 红 " + ReplayNumber(state.red_score);
            recordedFacts.Clear(); recordedAnnotations.Clear(); recordedUnitLabels.Clear();
            if (recordedUnitFocus != null)
            {
                var choices = state.units.Where(u => !u.retired).Select(u => u.unit_id).ToList();
                recordedUnitFocus.choices = choices;
                if (!choices.Contains(recordedUnitFocus.value)) recordedUnitFocus.SetValueWithoutNotify("定位历史单位");
            }
            if (recordedPoints != null) recordedPoints.text = string.Join("  |  ", state.capture_points.Select(p => p.point_id + " "
                + (p.owner == "blue" ? "蓝" : p.owner == "red" ? "红" : "中立") + (p.contested ? "·争夺" : "")
                + (p.advancing_side == "none" ? "" : "·" + (p.advancing_side == "blue" ? "蓝" : "红") + "推进" + (p.progress_steps * 100 / 6) + "%")));
            foreach (var point in state.capture_points)
                AddText(recordedFacts, point.point_id + " · " + (point.owner == "blue" ? "蓝方" : point.owner == "red" ? "红方" : "中立")
                    + (point.contested ? " · 争夺中" : "") + "\n" + (point.advancing_side == "none" ? "无占领推进" : (point.advancing_side == "blue" ? "蓝方" : "红方") + "推进 " + (point.progress_steps * 100 / 6) + "%"), "recorded-point");
            foreach (var unit in state.units)
            {
                var captured = unit;
                var button = AddButton(recordedFacts, RecordedUnitText(unit), () => { if (recordedIsRehearsal) recordedStage?.Focus(captured); else cameraRig?.ObserveRecordedPoint(SandboxRoundReplayStage.Position(captured.position)); }, "recorded-unit");
                button.AddToClassList(unit.side == "blue" ? "recorded-blue" : "recorded-red");
                var label = new Label(RecordedUnitText(unit)) { pickingMode = PickingMode.Ignore };
                label.AddToClassList("recorded-map-label"); label.AddToClassList(unit.side == "blue" ? "recorded-blue" : "recorded-red");
                recordedAnnotations.Add(label); recordedUnitLabels.Add(unit.unit_id, label);
            }
        }
        private void PositionRecordedLabels()
        {
            if (!recordedStage) return;
            if (!recordedIsRehearsal)
            {
                float rw = root.resolvedStyle.width, rh = root.resolvedStyle.height;
                foreach (var pair in recordedUnitLabels)
                {
                    Vector3 point = recordedStage.ViewPosition(pair.Key);
                    bool visible = point.z > 0 && point.x > 0 && point.x < 1 && point.y > 0 && point.y < 1;
                    pair.Value.style.display = visible ? DisplayStyle.Flex : DisplayStyle.None;
                    pair.Value.style.left = Mathf.Clamp(point.x * rw + 12, 0, Mathf.Max(0, rw - 205));
                    pair.Value.style.top = Mathf.Clamp((1 - point.y) * rh - 65, 0, Mathf.Max(0, rh - 275));
                }
                return;
            }
            float width = recordedViewport.resolvedStyle.width, height = recordedViewport.resolvedStyle.height;
            if (!(width > 0 && height > 0)) return;
            // Image使用ScaleToFit，标签必须扣除黑边，否则宽屏/窄屏会与模型错位。
            float fit = Mathf.Min(width / 1280, height / 720), w = 1280 * fit, h = 720 * fit;
            if (recordedIsRehearsal) { PositionRehearsalLabels(width, height, w, h); return; }
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
            world?.SetHistoricalPresentation(false);
            var annotations = root?.Q("mapAnnotations");
            if (annotations != null) { annotations.style.display = DisplayStyle.Flex; annotations.SetEnabled(true); }
            if (manualBattleBar != null) manualBattleBar.style.display = DisplayStyle.None;
            recordedFacts?.Clear(); recordedAnnotations?.Clear(); recordedUnitLabels.Clear();
            if (recordedStatus != null) recordedStatus.text = message + " · 未执行任何战斗命令。";
            if (recordedScore != null) recordedScore.text = "历史状态未加载";
            if (recordedPoints != null) recordedPoints.text = "";
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
            world?.SetHistoricalPresentation(false);
            var liveAnnotations = root?.Q("mapAnnotations");
            if (liveAnnotations != null) { liveAnnotations.style.display = DisplayStyle.Flex; liveAnnotations.SetEnabled(true); }
            if (manualBattleBar != null) manualBattleBar.style.display = DisplayStyle.Flex;
            recordedOverlay?.RemoveFromHierarchy(); recordedOverlay = null; recordedCursor = null; recordedIndex = null;
            recordedIsRehearsal = false; recordedScope = null; recordedLoading = false; recordedFactsAt = -1; recordedRate = 1; recordedUnitLabels.Clear();
            recordedEventSummary = null; recordedStatus = null; recordedScore = null; recordedTime = null; recordedViewport = null; recordedFacts = null;
            recordedPoints = null; recordedUnitFocus = null; recordedAnnotations = null; recordedRound = null; recordedSeek = null; recordedPlay = null; recordedLoad = null; recordedSpeed = null;
        }
    }
}
