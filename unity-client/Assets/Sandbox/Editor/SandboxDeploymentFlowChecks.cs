using System;
using System.Linq;
using System.Reflection;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    public static class SandboxDeploymentFlowChecks
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private static int checks;
        private static void Check(bool value,string message) { if(!value)throw new InvalidOperationException("Deployment flow: "+message);checks++; }
        private static void Set(object target,string name,object value)=>target.GetType().GetField(name,Private).SetValue(target,value);
        private static object Call(object target,string name,params object[] args)=>target.GetType().GetMethod(name,Private).Invoke(target,args);
        private static object Get(object target,string name)=>target.GetType().GetProperty(name,Private).GetValue(target);
        private static SandboxStagedArmy Army(int x)
        {
            var army=new SandboxStagedArmy { ready=true };
            for(int i=0;i<3;i++)
            {
                string kind=new[]{"recon","ground","support"}[i];
                var point=new SandboxReplayPosition { x_mm=x+i*200000,z_mm=4000000,height_mm=0 };
                army.generated[i]=true;
                army.slots[i]=new SandboxCampPlacementResult { count=1,
                    camp=new SandboxManualCampDeployment { unit_type=kind,position=point,surface_id="test",service_anchor=point,service_surface_id="test" },
                    units=new[]{new SandboxManualDeployment { unit_type=kind,ordinal=1,position=point,surface_id="test" }} };
            }
            return army;
        }
        public static void Run()
        {
            checks=0;var go=new GameObject("IsolatedDeploymentFlowChecks");string key="qinglan-deployment-v1-editor-"+Guid.NewGuid();
            try
            {
                var hud=go.AddComponent<SandboxHudController>();var client=go.AddComponent<SandboxServerClient>();var world=go.AddComponent<SandboxWorld>();
                var ring=new GameObject("Ring");ring.transform.parent=go.transform;Set(world,"barracksSelectionRing",ring.AddComponent<LineRenderer>());
                var root=Resources.Load<VisualTreeAsset>("Sandbox/SandboxHud").CloneTree();
                Set(hud,"root",root);Set(hud,"drawerContent",root.Q<ScrollView>("drawerContent"));Set(hud,"world",world);
                Set(hud,"serverClient",client);Set(hud,"serverMode",true);Set(hud,"deploymentStaging",true);
                var view=new SandboxServerView { role="host",context=new SandboxServerContext { game_id=key.Substring("qinglan-deployment-v1-".Length) },
                    clock=new SandboxServerClock { phase="preparing" },manual_battle=new SandboxManualBattleView { round_number=1,stage="planning" } };
                typeof(SandboxServerClient).GetProperty("View").SetValue(client,view);
                var roundtrip = typeof(SandboxHudController).GetMethod("ReadStagedDraft", BindingFlags.Static | BindingFlags.NonPublic);
                var empty = (SandboxStagedDeployment)roundtrip.Invoke(null, new object[] { JsonUtility.ToJson(new SandboxStagedDeployment()) });
                Check(empty.blue.slots.All(slot => slot == null) && empty.red.slots.All(slot => slot == null), "empty saved camps remain unplaced after reload");
                var savedArmy = new SandboxStagedDeployment { blue = Army(10000000), red = Army(1000000) };
                var restored = (SandboxStagedDeployment)roundtrip.Invoke(null, new object[] { JsonUtility.ToJson(savedArmy) });
                Check(restored.blue.ready && restored.red.ready && restored.blue.slots.Length == 3, "completed deployment survives reload");
                var draft=new SandboxStagedDeployment { blue=Army(10000000),red=Army(1000000) };Set(hud,"stagedDeployment",draft);
                Check((bool)Get(hud,"BothDeploymentsReady"),"six camps and generated counts ready");
                Check(Get(hud,"ManualBattle")==null,"old game command stage hidden while staging");
                draft.blue.generated[1]=false;Check(!(bool)Get(hud,"BothDeploymentsReady"),"unspawned roster blocks start");draft.blue.generated[1]=true;
                draft.blue.counts[1]=2;Check(!(bool)Get(hud,"BothDeploymentsReady"),"quantity mismatch blocks start");draft.blue.counts[1]=1;
                draft.red.ready=false;Check(!(bool)Get(hud,"BothDeploymentsReady"),"both confirmations required");draft.red.ready=true;
                var old=draft.blue.slots[0];draft.blue.counts[0]=0;draft.blue.slots[0]=null;
                Check(!(bool)Get(hud,"BothDeploymentsReady"),"zero-count type still requires a camp");draft.blue.slots[0]=old;draft.blue.counts[0]=1;
                foreach(var role in new[]{SandboxRole.Blue,SandboxRole.Red,SandboxRole.Host})
                {
                    Set(hud,"role",role);var projection=(SandboxProjection)Call(hud,"StagedProjection");
                    Check(projection.units.Length==(role==SandboxRole.Host?6:3),"side scoped unit count");
                    Check(projection.barracks.Length==(role==SandboxRole.Host?6:3),"side scoped camp count");
                    Check(projection.units.All(u=>role==SandboxRole.Host||u.side==role),"opposing coordinates not projected");
                    Check(projection.units.Where(u=>u.type==SandboxUnitType.Recon).All(u=>u.docked),"drone starts docked");
                }
                Call(hud,"InitializeBattleEntry");Call(hud,"OpenBattleEntry");Call(hud,"SelectBattleMode","manual");
                Check(root.Q<Button>("battleStartButton").text=="对战开始","new start label replaces configuration entry");
                Check(root.Q<Button>("battleStartButton").enabledSelf,"ready deployment opens start action");
                Call(hud,"InvalidateDeploymentReady");Check(!draft.blue.ready&&!draft.red.ready,"edit invalidates both confirmations");
                Call(hud,"SelectBattleMode","manual");Check(!root.Q<Button>("battleStartButton").enabledSelf,"unconfirmed deployment disables start");
                Call(hud,"RefreshDeploymentStageBanner");Check(root.Q<Label>("deploymentStageTitle").text=="部署阶段","permanent deployment banner");
                Check(!client.HasPending&&!client.Busy,"no database or network operations from state checks");
                Call(hud,"CloseBattleEntry");Set(hud,"deploymentStaging",false);Check(Get(hud,"ManualBattle")!=null,"after start command stage restored");
                Debug.Log("[DeploymentFlowChecks] PASS "+checks+" checks; no network or user-game writes");
            }
            finally { PlayerPrefs.DeleteKey(key);UnityEngine.Object.DestroyImmediate(go); }
        }
    }
}
