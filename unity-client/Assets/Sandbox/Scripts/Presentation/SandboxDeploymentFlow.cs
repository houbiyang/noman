using System;
using System.Linq;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox
{
    public sealed partial class SandboxHudController
    {
        // Local shared-device staging. It never submits old-game army commands.
        private bool deploymentStaging, deploymentChecked, deploymentReading, deploymentJustStarted;
        private SandboxStagedDeployment stagedDeployment = new SandboxStagedDeployment();
        private VisualElement deploymentBar;
        private string deploymentMessage = "连接后端后，在军队面板逐一部署三座兵营。";
        private string DeploymentStorageKey => "qinglan-deployment-v1-" + serverClient?.View?.context?.game_id;
        private SandboxStagedArmy StagedSide(SandboxRole side) => side == SandboxRole.Blue ? stagedDeployment.blue : stagedDeployment.red;
        private bool DeploymentWritable => deploymentStaging && manualCatalogue != null && serverClient != null
            && serverClient.Healthy && !serverClient.Busy && !serverClient.HasPending && !serverClient.HasManualCreation;
        private static bool CompleteStagedSide(SandboxStagedArmy side) => ValidStagedSide(side) && side.counts.Sum() > 0
            && Enumerable.Range(0,3).All(i => side.slots[i]?.camp != null && side.generated[i]
                && side.slots[i].count == side.counts[i] && side.slots[i].units?.Length == side.counts[i]);
        private static bool ValidStagedSide(SandboxStagedArmy side) => side?.counts?.Length == 3 && side.generated?.Length == 3
            && side.slots?.Length == 3 && side.counts.All(n => n >= 0 && n <= 10);
        private bool BothDeploymentsReady => CompleteStagedSide(stagedDeployment.blue) && CompleteStagedSide(stagedDeployment.red)
            && stagedDeployment.blue.ready && stagedDeployment.red.ready;

        private static SandboxStagedDeployment ReadStagedDraft(string saved)
        {
            SandboxStagedDeployment draft = null;
            try { if (!string.IsNullOrEmpty(saved)) draft = JsonUtility.FromJson<SandboxStagedDeployment>(saved); }
            catch (Exception) { }
            if (!ValidStagedSide(draft?.blue) || !ValidStagedSide(draft?.red)) return new SandboxStagedDeployment();
            foreach (var army in new[] { draft.blue, draft.red })
                for (int i = 0; i < 3; i++)
                    if (army.slots[i]?.camp?.position == null || army.slots[i].camp.unit_type != SetupKinds[i]
                        || string.IsNullOrEmpty(army.slots[i].camp.surface_id))
                    { army.slots[i] = null; army.generated[i] = false; army.ready = false; }
            return draft;
        }

        private void EnterStagedDeployment()
        {
            if (serverClient == null || serverClient.Busy || serverClient.HasPending) { Notify("请等待同步完成"); return; }
            CloseBattleEntry(); CloseArmy(); CancelArmyCommand(); CloseSelectedUnitControls();
            deploymentChecked=true; deploymentStaging=true; deploymentReading=false; manualCatalogue=null;
            stagedDeployment=ReadStagedDraft(PlayerPrefs.GetString(DeploymentStorageKey, ""));
            PlayerPrefs.SetString("qinglan-deploying-game", serverClient.View.context.game_id); PlayerPrefs.Save();
            if(serverClient.View?.role!="host") BeginServerConnection(SandboxRole.Host);
            role=SandboxRole.Blue; deploymentMessage="新对局部署：双方各放置三座兵营，再逐营生成本局编制。";
            RefreshProjection();
        }
        private void EnsureDeploymentFlow()
        {
            if (serverClient?.View?.context == null || !serverClient.Healthy || recordedOverlay != null) return;
            string id = serverClient.View.context.game_id;
            if (deploymentJustStarted)
            {
                PlayerPrefs.SetString("qinglan-started-game", id); PlayerPrefs.DeleteKey("qinglan-deploying-game"); PlayerPrefs.Save(); deploymentJustStarted = false;
            }
            if (!deploymentChecked)
            {
                deploymentChecked = true;
                deploymentStaging = PlayerPrefs.GetString("qinglan-started-game", "") != id;
                // Never conceal an existing in-progress battle behind a fresh setup.
                if (serverClient.View.manual_battle?.completed_rounds > 0 || serverClient.View.manual_battle?.submitted?.blue == true || serverClient.View.manual_battle?.submitted?.red == true) deploymentStaging = false;
                if (PlayerPrefs.GetString("qinglan-deploying-game", "") == id) deploymentStaging = true;
                if (deploymentStaging)
                {
                    role = SandboxRole.Blue;
                    stagedDeployment = ReadStagedDraft(PlayerPrefs.GetString(DeploymentStorageKey, ""));
                    stagedDeployment.source_game = id;
                }
            }
            if (!deploymentStaging || deploymentReading || manualCatalogue != null || serverClient.Busy || !serverClient.Healthy) return;
            deploymentReading = true;
            if (!serverClient.ReadManualSetup((catalogue, error) => {
                if (error != null) { deploymentMessage = error; return; }
                deploymentReading = false;
                manualCatalogue = catalogue;
                if (!string.IsNullOrEmpty(stagedDeployment.scenario_sha256) && stagedDeployment.scenario_sha256 != catalogue.scenario_sha256)
                {
                    stagedDeployment = new SandboxStagedDeployment { source_game = id };
                    deploymentMessage = "地图验收版本已更新，请重新部署；未自动挪动旧配置。";
                }
                else deploymentMessage = "选择本方兵营，在沙盘合法位置部署，再生成固定数量单位。";
                stagedDeployment.scenario_sha256 = catalogue.scenario_sha256;
                if (catalogue.pending_creation?.payload != null)
                {
                    stagedDeployment.blue = RestoreStagedSide(catalogue.pending_creation.payload.blue);
                    stagedDeployment.red = RestoreStagedSide(catalogue.pending_creation.payload.red);
                    deploymentMessage = "上次开战结果待核对。部署已保留，请点击对战开始沿用原请求恢复。";
                }
                SaveStagedDeployment();
                if (armyOverlay != null) RenderArmy();
            })) deploymentReading = false;
        }
        private static SandboxStagedArmy RestoreStagedSide(SandboxManualSideSetup setup)
        {
            var result = new SandboxStagedArmy { counts = new[] {setup.quantities.recon, setup.quantities.ground, setup.quantities.support}, ready = true };
            for (int i=0;i<3;i++)
            {
                result.slots[i] = new SandboxCampPlacementResult { camp=setup.camps.First(c=>c.unit_type==SetupKinds[i]),
                    units=setup.units.Where(u=>u.unit_type==SetupKinds[i]).ToArray(), count=result.counts[i] };
                result.generated[i]=true;
            }
            return result;
        }
        private void SaveStagedDeployment()
        {
            PlayerPrefs.SetString(DeploymentStorageKey, JsonUtility.ToJson(stagedDeployment)); PlayerPrefs.Save();
        }
        private void InvalidateDeploymentReady()
        {
            stagedDeployment.blue.ready = stagedDeployment.red.ready = false;
            SaveStagedDeployment();
        }
        private SandboxProjection StagedProjection()
        {
            var camps = new List<SandboxBarracksData>(); var units = new List<SandboxUnitData>();
            foreach (var side in new[] { SandboxRole.Blue, SandboxRole.Red })
            {
                if (role != SandboxRole.Host && role != side) continue;
                var army = StagedSide(side);
                for (int i=0;i<3;i++)
                {
                    var slot = army.slots[i]; if (slot?.camp == null) continue;
                    string id = (side == SandboxRole.Blue ? "B" : "R") + "-" + new[] { "U", "G", "L" }[i];
                    camps.Add(new SandboxBarracksData { id = id + "-CAMP", side = side, type = (SandboxUnitType)i,
                        x = slot.camp.position.x_mm / 100000f, z = slot.camp.position.z_mm / 150000f });
                    if (!army.generated[i]) continue;
                    foreach (var unit in slot.units)
                        units.Add(new SandboxUnitData { id = id + unit.ordinal.ToString("00"), name = SetupKindLabels[i], side = side,
                            type = (SandboxUnitType)i, barracksId = id + "-CAMP", x = unit.position.x_mm / 100000f,
                            z = unit.position.z_mm / 150000f, status = "部署待开始 · 编制 " + army.counts[i], docked = i == 0 });
                }
            }
            return new SandboxProjection { role = role, title = "青岚谷 · 赛前部署", taskType = "部署阶段", step = 0,
                units = units.ToArray(), barracks = camps.ToArray(), records = Array.Empty<SandboxRecord>(), waitingSummary = "" };
        }
        private void ChangeDeploymentRole(SandboxRole next)
        {
            if (serverClient?.Busy == true) { Notify("请等待当前部署验收完成"); return; }
            CloseSelectedUnitControls(); CloseArmy(); CancelArmyCommand(); ClosePanel(); CloseBattleEntry();
            selectedId = null; role = next;
            deploymentMessage = BothDeploymentsReady ? "双方已就绪：点击战斗 → 本地手动测试 → 对战开始。"
                : next == SandboxRole.Host ? "请检查双方部署及就绪状态。"
                : StagedSide(next).ready ? SideLabel(next) + "已就绪，请切换另一方完成部署。"
                : SideLabel(next) + "尚未确认部署，请完成兵营和单位后确认。";
            cameraRig.ReturnToOverview(); RefreshProjection();
        }
        private void RefreshStagingBar()
        {
            if (!deploymentStaging) { deploymentBar?.RemoveFromHierarchy(); deploymentBar = null; return; }
            if (deploymentBar == null)
            {
                deploymentBar = new VisualElement { name = "deployment-flow-bar" };
                deploymentBar.AddToClassList("manual-battle-bar"); deploymentBar.AddToClassList("ui-blocker");
                var sheet = Resources.Load<StyleSheet>("Sandbox/SandboxManualBattle"); if (sheet != null) root.styleSheets.Add(sheet);
                root.Add(deploymentBar);
            }
            deploymentBar.Clear();
            string Summary(SandboxStagedArmy army) => army.slots.Count(s => s?.camp != null) + "/3营 · "
                + Enumerable.Range(0,3).Where(i => army.generated[i]).Sum(i => army.counts[i]) + "单位 · " + (army.ready ? "已就绪" : "未就绪");
            AddText(deploymentBar, "蓝方 " + Summary(stagedDeployment.blue) + "  |  红方 " + Summary(stagedDeployment.red), "army-unit-title");
            AddText(deploymentBar, deploymentMessage, "army-footnote");
            if (manualCatalogue == null && !serverClient.Busy)
                AddButton(deploymentBar, "重新读取部署数据", () => { deploymentReading=false; EnsureDeploymentFlow(); }, "army-unit-action");
            AddButton(deploymentBar, role == SandboxRole.Host ? "查看双方部署" : "部署本方兵营", OpenArmy, "army-unit-action");
            if (role != SandboxRole.Host)
                AddButton(deploymentBar, StagedSide(role).ready ? "本方已就绪" : "确认本方部署完成", () => {
                    if (!DeploymentWritable || !CompleteStagedSide(StagedSide(role))) { Notify("请先部署三个兵营，逐营生成设定数量的单位；每方至少一个单位"); return; }
                    ConfirmStagedDeployment(role);
                }, "army-unit-action").SetEnabled(DeploymentWritable && CompleteStagedSide(StagedSide(role)) && !StagedSide(role).ready);
            if (serverClient?.View?.manual_battle != null && role == SandboxRole.Host)
                AddButton(deploymentBar, "返回已有对局", () => {
                    if (serverClient.Busy || serverClient.HasManualCreation) return;
                    deploymentStaging = false; PlayerPrefs.DeleteKey("qinglan-deploying-game"); PlayerPrefs.SetString("qinglan-started-game", serverClient.View.context.game_id); PlayerPrefs.Save();
                    CloseArmy(); CancelArmyCommand(); RefreshProjection();
                }, "army-unit-action");
        }
        private void RenderStagedArmy()
        {
            armyOverlay = new VisualElement { name = "deploymentArmyPanel" }; armyOverlay.AddToClassList("army-panel"); armyOverlay.AddToClassList("ui-blocker"); root.Add(armyOverlay);
            var header = SettingsGroup(armyOverlay, "army-header");
            AddText(header, SideLabel(armySide) + " · 赛前兵营部署", "army-title");
            AddButton(header,"×",CloseArmy,"army-close");
            if (role == SandboxRole.Host)
                AddButton(armyOverlay, armySide == SandboxRole.Blue ? "查看红方" : "查看蓝方", () => { armySide = armySide == SandboxRole.Blue ? SandboxRole.Red : SandboxRole.Blue; RenderArmy(); }, "army-switch");
            var scroll = new ScrollView(); scroll.AddToClassList("army-scroll"); armyOverlay.Add(scroll);
            var army = StagedSide(armySide);
            for (int i=0;i<3;i++)
            {
                int kind=i;
                var card = SettingsGroup(scroll, "army-card-details");
                AddText(card, DemoSandboxViewSource.BarracksName((SandboxUnitType)i), "army-card-title");
                var count = new IntegerField("本局单位总数（0～10）") { value = army.counts[i], isDelayed = true, name = "deploymentCount" + i };
                count.AddToClassList("army-spawn-input"); card.Add(count); count.SetEnabled(DeploymentWritable && role != SandboxRole.Host);
                count.RegisterValueChangedCallback(e => {
                    if (e.newValue < 0 || e.newValue > 10) { count.SetValueWithoutNotify(army.counts[kind]); Notify("每类0～10个；不会自动缩减数量"); return; }
                    army.counts[kind]=e.newValue; army.generated[kind]=false; InvalidateDeploymentReady(); RefreshProjection(); RenderArmy();
                });
                var slot=army.slots[i];
                AddText(card, slot?.camp == null ? "尚未部署兵营" : "已部署 · " + (army.generated[i] ? "已生成 " + slot.count + " 个（开战后锁定）" : "等待生成本局编制"), "army-current");
                AddButton(card, slot?.camp == null ? "在沙盘选址部署" : "重新选择兵营位置", () => {
                    pendingArmyType=(SandboxUnitType)kind; pendingArmySide=armySide; pendingMoveId=null; CloseArmy();
                    ShowArmyCommand("部署" + DemoSandboxViewSource.BarracksName((SandboxUnitType)kind), "点击本方部署区内已核验空地 · 不压道路或建筑 · Esc取消");
                }, "army-unit-action").SetEnabled(DeploymentWritable && role != SandboxRole.Host);
                AddButton(card, (army.generated[i] ? "重新生成本局 " : "生成本局 ") + army.counts[i] + " 个单位", () => {
                    var pos=army.slots[kind]?.camp?.position; if(pos==null)return;
                    PreviewStagedCamp(armySide,kind,pos.x_mm,pos.z_mm,true);
                }, "army-spawn-button").SetEnabled(DeploymentWritable && slot?.camp != null && role != SandboxRole.Host);
                var recommended = armySide == SandboxRole.Blue ? manualCatalogue?.recommended_setup?.blue : manualCatalogue?.recommended_setup?.red;
                var hint = recommended?.camps?.FirstOrDefault(c => c.unit_type == SetupKinds[kind]);
                if (hint != null)
                {
                    AddText(card, "已核验位置参考 X " + (hint.position.x_mm/100000f).ToString("0.###") + " / Z " + (hint.position.z_mm/150000f).ToString("0.###"), "army-footnote");
                    AddButton(card, "定位参考位置", () => FocusDeploymentReference(hint), "army-unit-action");
                    AddButton(card, "选择参考位置部署", () => PreviewStagedCamp(armySide,kind,hint.position.x_mm,hint.position.z_mm,false), "army-unit-action")
                        .SetEnabled(DeploymentWritable && role != SandboxRole.Host);
                }
            }
            AddText(armyOverlay, "只在赛前生成固定编制；改动部署会取消双方就绪。战中不能增兵或改数量。", "army-footnote");
        }
        private void FocusDeploymentReference(SandboxManualCampDeployment camp)
        {
            // Use the existing camera's position focus without moving or submitting a camp.
            CloseArmy();
            cameraRig.ObserveRecordedPoint(SandboxWorld.ArmyPosition(camp.position.x_mm/100000f, camp.position.z_mm/150000f, SandboxUnitType.Ground));
        }
        private void PreviewStagedCamp(SandboxRole side,int kind,int x,int z,bool generate)
        {
            if (!DeploymentWritable) { Notify("部署数据或后端尚未就绪"); return; }
            var army=StagedSide(side);
            bool sent=serverClient.PreviewCamp(new SandboxCampPlacementRequest { side=ServerSide(side), unit_type=SetupKinds[kind],
                x_mm=x,z_mm=z,count=generate?army.counts[kind]:0,
                other_camps=army.slots.Where((slot,index)=>index!=kind && slot?.camp!=null).Select(slot=>slot.camp).ToArray(),
                other_units=army.slots.Where((slot,index)=>index!=kind && army.generated[index] && slot?.units!=null).SelectMany(slot=>slot.units).ToArray() }, (result,error) => {
                    if(error!=null) { deploymentMessage=error; Notify(error); RefreshProjection(); return; }
                    army.slots[kind]=result; army.generated[kind]=generate; InvalidateDeploymentReady();
                    deploymentMessage=generate? SideLabel(side)+"已生成"+result.count+"个"+SetupKindLabels[kind] : SideLabel(side)+"兵营部署完成，请生成本局单位。";
                    CancelArmyCommand(); RefreshProjection(); armySide=side; RenderArmy();
                });
            if(sent) { deploymentMessage="正在验收兵营位置、服务面和出兵区…"; Notify(deploymentMessage); }
        }
        private SandboxManualSideSetup StagedSetup(SandboxStagedArmy army)
        {
            return new SandboxManualSideSetup { quantities=new SandboxManualQuantities { recon=army.counts[0],ground=army.counts[1],support=army.counts[2] },
                camps=army.slots.Select(s=>s.camp).ToArray(), units=army.slots.SelectMany(s=>s.units).ToArray() };
        }
        private void ConfirmStagedDeployment(SandboxRole side)
        {
            void Finish(bool valid, string error)
            {
                if (!valid) { InvalidateDeploymentReady(); deploymentMessage=error; Notify(error); }
                else {
                    StagedSide(side).ready=true; SaveStagedDeployment();
                    deploymentMessage=BothDeploymentsReady ? "整体部署验收通过，双方已就绪：点击战斗 → 本地手动测试 → 对战开始。"
                        : SideLabel(side)+"已就绪，请切换另一方完成部署。";
                }
                RefreshProjection();
            }
            // The last side cannot confirm readiness before the complete map gate passes.
            if (CompleteStagedSide(stagedDeployment.blue) && CompleteStagedSide(stagedDeployment.red))
            {
                if (serverClient.ValidateManualDeployment(StagedPayload(), Finish))
                { deploymentMessage="正在核对六座兵营、补给通道和全部单位占地…"; RefreshStagingBar(); }
                else Notify("请等待连接同步后再确认部署");
            }
            else Finish(true,null);
        }
        private SandboxManualCreatePayload StagedPayload()
        {
            var c=manualCatalogue;
            return new SandboxManualCreatePayload { title="手动对战",mode="manual",rule_version_id=c.rule_version_id,
                scenario_version_id=c.scenario_version_id,capacity_version_id=c.capacity_version_id,rule_sha256=c.rule_sha256,
                scenario_sha256=c.scenario_sha256,capacity_sha256=c.capacity_sha256,blue=StagedSetup(stagedDeployment.blue),red=StagedSetup(stagedDeployment.red) };
        }
        private void StartStagedBattle()
        {
            if (!BothDeploymentsReady || manualCatalogue == null) { Notify("请先完成并确认双方各三座兵营与固定编制"); return; }
            if (battleMode != "manual") return;
            var payload=StagedPayload();
            Action<bool,string> complete=(ok,error)=> {
                if (!ok)
                {
                    deploymentMessage=error; if(battleStatus!=null)battleStatus.text=error;
                    // Re-read the server's creation ledger: only an explicit rejection unlocks edits.
                    serverClient.ReadManualSetup((catalogue, readError) => {
                        if (catalogue != null) manualCatalogue=catalogue;
                        if (battleOverlay != null) battleStartButton.SetEnabled(BothDeploymentsReady);
                        RefreshStagingBar(); if(armyOverlay!=null)RenderArmy();
                    });
                    RefreshStagingBar(); return;
                }
                deploymentStaging=false; deploymentJustStarted=true; CloseBattleEntry(); CloseArmy(); CancelArmyCommand(); role=SandboxRole.Host;
                Notify("对战已开始，编制和兵营已锁定；请选择蓝方或红方下达第一轮指令"); RefreshProjection();
            };
            bool sent=serverClient.CreateManualGame(payload,complete);
            if (sent) { battleStatus.text="正在验收双方完整部署并开始对战；不会推进仿真时间…"; battleStartButton.SetEnabled(false); }
            else Notify("请等待后端同步完成再开始；没有创建新对局");
        }
    }
}
