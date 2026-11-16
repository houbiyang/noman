using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>Isolated presentation acceptance. Does not connect, start or advance the user's game.</summary>
    public static class SandboxAttackAnimationChecks
    {
        private static int checks;
        private static void Check(bool condition, string name)
        {
            if (!condition) throw new Exception("[AttackAnimationChecks] FAIL " + name);
            checks++; Debug.Log("[AttackAnimationChecks] " + name);
        }
        private static object Call(object target, string method) => target.GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(target, null);
        public static void Run()
        {
            var view = new SandboxServerView {
                role = "red", context = new SandboxServerContext { game_id = "isolated", branch_id = "branch" },
                unit_interactions = new SandboxServerUnitInteractions { enabled = true }
            };
            var e = new SandboxAttackEvent { event_id = "committed", at = 5, amount = 16, hp_after = 84, ammo_after = 59,
                source = new SandboxAttackEndpoint { unit_id = "R-G01", life_id = "r-life", unit_type = "ground", position = new SandboxServerPosition { x = "5", y = "5" } },
                target = new SandboxAttackEndpoint { unit_id = "B-G01", life_id = "b-life", unit_type = "ground", position = new SandboxServerPosition { x = "9", y = "5" } }
            };
            var cursor = new SandboxAttackCursor();
            view.attack_events = new[] { e };
            Check(cursor.Read(view).Length == 0 && cursor.Reset, "first connection baselines without replay");
            view.unit_interactions.elapsed_seconds = 60;
            Check(cursor.Read(view).Length == 1, "new committed shot accepted");
            Check(cursor.Read(view).Length == 0, "duplicate polling cannot fire again");
            view.role = "blue"; Check(cursor.Read(view).Length == 0 && cursor.Reset, "role change clears old stream");
            view.context.branch_id = "restored"; Check(cursor.Read(view).Length == 0 && cursor.Reset, "restored branch never replays history");
            view.unit_interactions.elapsed_seconds = 0;
            Check(cursor.Read(view).Length == 0 && cursor.Reset, "clock rewind resets cursor");
            view.attack_events = new[] { e, e }; view.unit_interactions.elapsed_seconds = 60;
            Check(cursor.Read(view).Length == 1, "duplicate event IDs in one response deduplicated");
            e.at = 120; view.unit_interactions.elapsed_seconds = 90;
            Check(cursor.Read(view).Length == 0, "future event not rendered"); e.at = 5;

            var root = new GameObject("Isolated production attack acceptance");
            RenderTexture texture = null; Texture2D image = null;
            try
            {
                var prefab = Resources.Load<GameObject>("Combat/infantry");
                Check(prefab != null, "original rehearsal articulated model imported");
                Check(prefab.transform.Find("TurretYaw/GunPitch/GunRecoil/Muzzle") != null, "yaw, elevation, recoil and muzzle rig intact");
                Check(prefab.GetComponentsInChildren<Renderer>().All(r => r.sharedMaterial && r.GetComponent<MeshFilter>().sharedMesh), "all rig mesh and material GUIDs resolve");
                foreach (var key in new[] { "Glow", "Smoke" })
                    Check(Resources.Load<Material>("Combat/" + key).shader.isSupported, key + " shader supported");
                var source = Object.Instantiate(prefab, root.transform); source.transform.localPosition = new Vector3(-14, 0, -7); source.transform.localScale = Vector3.one * 2;
                var a = source.GetComponent<SandboxCombatActor>(); a.Configure(new Color(.87f,.24f,.17f), false);
                var target = Object.Instantiate(Resources.Load<GameObject>("Army/support"), root.transform); target.transform.localPosition = new Vector3(14, 0, 7); target.transform.localScale = Vector3.one * 2;
                var b = target.AddComponent<SandboxCombatActor>(); b.Configure(new Color(.15f,.48f,.83f), false);
                Vector3 originalPosition = source.transform.position, targetPosition = target.transform.position;
                a.Aim(b.Center); Call(a,"LateUpdate"); Call(b,"LateUpdate");
                a.Fire(b.Center); Call(a,"LateUpdate");
                Check(source.transform.Find("TurretYaw/GunPitch/GunRecoil").localPosition.z < 0, "real articulated barrel recoils");
                Check(a.Muzzle != source.transform.position, "projectile starts at actual muzzle");
                var fx = root.AddComponent<SandboxAttackPlayback>();
                fx.Initialize((id,life) => id == "R-G01" && life == "r-life" ? a : id == "B-G01" && life == "b-life" ? b : null);
                view.unit_interactions.elapsed_seconds = 0; fx.Apply(view);
                view.unit_interactions.elapsed_seconds = 60; fx.Apply(view); fx.Apply(view);
                Check(fx.ActiveShots == 1, "world adapter queues authority only once");
                var shots = (IList)typeof(SandboxAttackPlayback).GetField("shots",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(fx);
                object shot = shots[0]; var timeField = shot.GetType().GetField("start");
                timeField.SetValue(shot, Time.time - .32f); Call(fx,"Update");
                Check(root.GetComponentsInChildren<MeshRenderer>().Any(r => r.name == "Pooled authoritative attack VFX" && r.gameObject.activeSelf), "bright projectile and tapered trail generated");
                Check(root.GetComponentsInChildren<Collider>().Length == 0, "VFX has no gameplay collision or damage");
                Check(e.hp_after == 84 && e.ammo_after == 59 && e.amount == 16, "animation never mutates authoritative resources");
                Check(source.transform.position == originalPosition && target.transform.position == targetPosition, "VFX never overwrites authoritative transforms");
                var floor = GameObject.CreatePrimitive(PrimitiveType.Cube); floor.transform.SetParent(root.transform,false); floor.transform.position = new Vector3(0,-.65f,0); floor.transform.localScale = new Vector3(100,1,100);
                var floorMaterial = new Material(Shader.Find("Universal Render Pipeline/Lit")); floorMaterial.color=new Color(.25f,.32f,.29f);
                floor.GetComponent<Renderer>().sharedMaterial=floorMaterial;
                var light = new GameObject("Acceptance light"); light.transform.SetParent(root.transform); light.transform.rotation = Quaternion.Euler(48,-35,0); var sun=light.AddComponent<Light>(); sun.type=LightType.Directional; sun.intensity=2;
                var cameraObject = new GameObject("Acceptance camera"); cameraObject.transform.SetParent(root.transform);
                var camera=cameraObject.AddComponent<Camera>(); camera.transform.position=new Vector3(35,30,-48); camera.transform.LookAt(new Vector3(0,2,0)); camera.fieldOfView=48;
                camera.clearFlags=CameraClearFlags.SolidColor; camera.backgroundColor=new Color(.17f,.23f,.22f);
                foreach (Transform child in root.GetComponentsInChildren<Transform>()) child.gameObject.layer=30;
                camera.cullingMask=1<<30; sun.cullingMask=1<<30;
                texture=new RenderTexture(1440,900,24); camera.targetTexture=texture;
                Action<string> capture = name => {
                    foreach (Transform child in root.GetComponentsInChildren<Transform>()) child.gameObject.layer=30;
                    camera.Render(); RenderTexture previous=RenderTexture.active; RenderTexture.active=texture;
                    image=new Texture2D(1440,900,TextureFormat.RGB24,false); image.ReadPixels(new Rect(0,0,1440,900),0,0); image.Apply();
                    Directory.CreateDirectory("../.local/attack-animation"); File.WriteAllBytes("../.local/attack-animation/"+name+".png",image.EncodeToPNG());
                    RenderTexture.active=previous; Object.DestroyImmediate(image); image=null;
                };
                capture("projectile");
                timeField.SetValue(shot, Time.time - .79f); Call(fx,"Update"); Call(b,"LateUpdate"); capture("impact");
                Check(!b.Destroyed, "nonlethal hit does not destroy actor");
                b.Hit(true); Call(b,"LateUpdate"); Check(b.Destroyed,"lethal hit applies visual wreck state");
                b.SetDestroyed(false); Call(b,"LateUpdate"); Check(!b.Destroyed,"new life/rewind can clear wreck pose");
                Object.DestroyImmediate(floorMaterial);
                fx.Clear(); Check(fx.ActiveShots==0,"role/branch reset removes queued VFX");
                Check(root.GetComponentsInChildren<MeshRenderer>().Where(r=>r.name=="Pooled authoritative attack VFX").All(r=>!r.gameObject.activeSelf),"reset immediately hides old role effects");
                Debug.Log("[AttackAnimationChecks] PASS " + checks + " checks; production rig/VFX rendered; no user game writes");
                SandboxUnitInteractionChecks.Run();
            }
            finally
            {
                if (texture) { texture.Release(); Object.DestroyImmediate(texture); }
                if (image) Object.DestroyImmediate(image);
                Object.DestroyImmediate(root);
            }
        }
    }
}
