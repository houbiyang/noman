using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>Import authored Blender parts, batch static geometry, and render the actual Unity prefabs for army cards.</summary>
    public static class SandboxArmyAssets
    {
        private const string Folder = "Assets/Sandbox/Resources/Army";
        [Serializable] private class MaterialList { public MaterialEntry[] materials = null; public PartEntry[] parts = null; }
        [Serializable] private class PartEntry { public string name = null; public string material = null; }
        [Serializable] private class MaterialEntry { public string name = null; public float[] color = null; public float metallic = 0; public float roughness = 0; }

        [MenuItem("穹图/军队/导入模型与渲染卡牌")]
        public static void Prepare()
        {
            Directory.CreateDirectory(Folder + "/Materials");
            Directory.CreateDirectory(Folder + "/Meshes");
            AssetDatabase.Refresh();
            var entries = JsonUtility.FromJson<MaterialList>(File.ReadAllText("Assets/Sandbox/Units/Source/materials.json"));
            var materials = new Dictionary<string, Material>();
            foreach (MaterialEntry entry in entries.materials)
            {
                string path = Folder + "/Materials/" + entry.name + ".mat";
                Material material = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (material == null) { material = new Material(Shader.Find("Universal Render Pipeline/Lit")); AssetDatabase.CreateAsset(material, path); }
                // Blender values are linear. Unity's material inspector stores the display color in sRGB.
                Color color = new Color(entry.color[0], entry.color[1], entry.color[2], 1).gamma;
                if (entry.name == "TeamAccent") color = new Color(0.55f, 0.64f, 0.36f);
                material.SetColor("_BaseColor", color);
                material.SetFloat("_Metallic", entry.metallic);
                material.SetFloat("_Smoothness", 1 - entry.roughness);
                material.enableInstancing = true;
                if (entry.name == "Sensor_Glass")
                {
                    material.EnableKeyword("_EMISSION");
                    material.SetColor("_EmissionColor", new Color(0.02f, 0.28f, 0.32f));
                }
                EditorUtility.SetDirty(material);
                materials.Add(entry.name, material);
            }
            foreach (string key in new[] { "recon", "infantry", "support" })
            {
                string path = "Assets/Sandbox/Units/Source/" + key + ".fbx";
                ModelImporter importer = (ModelImporter)AssetImporter.GetAtPath(path);
                if (importer == null) throw new InvalidOperationException("Missing army FBX: " + path);
                if (!importer.isReadable || importer.materialImportMode != ModelImporterMaterialImportMode.None)
                {
                    importer.isReadable = true;
                    importer.materialImportMode = ModelImporterMaterialImportMode.None;
                    importer.importCameras = false; importer.importLights = false; importer.importAnimation = false;
                    importer.SaveAndReimport();
                }
                // The Blender manifest supplies each imported part's original material.
                GameObject model = UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(path));
                var prefab = new GameObject(key);
                var pivots = new Dictionary<string, Transform>();
                var filters = model.GetComponentsInChildren<MeshFilter>();
                var batches = new Dictionary<string, List<CombineInstance>>();
                foreach (MeshFilter filter in filters)
                {
                    string partName = filter.name;
                    Transform rotorRoot = filter.transform;
                    while (rotorRoot != null && !rotorRoot.name.StartsWith("Rotor_")) rotorRoot = rotorRoot.parent;
                    bool rotor = rotorRoot != null;
                    bool wheel = partName.StartsWith("All terrain tyre") || partName.StartsWith("Wheel hub") || partName.StartsWith("Hub marker") || partName.StartsWith("Road wheel");
                    // The source model uses one named material per mechanical part; the material manifest is authoritative.
                    string materialName = entries.parts.FirstOrDefault(part => part.name == partName)?.material ?? MaterialForPart(partName);
                    Matrix4x4 matrix = prefab.transform.worldToLocalMatrix * filter.transform.localToWorldMatrix;
                    if (rotor || wheel)
                    {
                        Vector3 pivot; string pivotName;
                        if (rotor) { pivot = rotorRoot.position; pivotName = "RotorPivot_" + rotorRoot.name; }
                        else
                        {
                            MeshFilter axle = filter;
                            if (partName.StartsWith("Wheel hub") || partName.StartsWith("Hub marker"))
                                axle = filters.Where(item => item.name.StartsWith("All terrain tyre"))
                                    .OrderBy(item => (item.transform.TransformPoint(item.sharedMesh.bounds.center) - filter.transform.TransformPoint(filter.sharedMesh.bounds.center)).sqrMagnitude).First();
                            pivot = axle.transform.TransformPoint(axle.sharedMesh.bounds.center); pivotName = "WheelPivot_" + axle.name;
                        }
                        if (!pivots.TryGetValue(pivotName, out Transform moving))
                        {
                            moving = new GameObject(pivotName).transform; moving.SetParent(prefab.transform, false); moving.localPosition = pivot; pivots.Add(pivotName, moving);
                        }
                        var part = new GameObject(partName); part.transform.SetParent(moving, false);
                        Mesh mesh = BakeMesh(key + "_" + partName, filter.sharedMesh, Matrix4x4.Translate(-pivot) * matrix);
                        part.AddComponent<MeshFilter>().sharedMesh = mesh;
                        part.AddComponent<MeshRenderer>().sharedMaterial = materials[materialName];
                    }
                    else
                    {
                        if (!batches.ContainsKey(materialName)) batches[materialName] = new List<CombineInstance>();
                        batches[materialName].Add(new CombineInstance { mesh = filter.sharedMesh, transform = matrix });
                    }
                }
                foreach (var batch in batches)
                {
                    var group = new GameObject(batch.Key); group.transform.SetParent(prefab.transform, false);
                    var mesh = new Mesh { name = key + "_" + batch.Key };
                    mesh.CombineMeshes(batch.Value.ToArray(), true, true);
                    group.AddComponent<MeshFilter>().sharedMesh = SaveMesh(mesh);
                    group.AddComponent<MeshRenderer>().sharedMaterial = materials[batch.Key];
                }
                UnityEngine.Object.DestroyImmediate(model);
                foreach (MeshRenderer renderer in prefab.GetComponentsInChildren<MeshRenderer>())
                { renderer.shadowCastingMode = ShadowCastingMode.On; renderer.receiveShadows = true; }
                Bounds bounds = new Bounds(Vector3.zero, Vector3.zero);
                foreach (Renderer renderer in prefab.GetComponentsInChildren<Renderer>()) bounds.Encapsulate(renderer.bounds);
                Debug.Log("[军队] " + key + " bounds=" + bounds.size + ", renderers=" + prefab.GetComponentsInChildren<Renderer>().Length);
                PrefabUtility.SaveAsPrefabAsset(prefab, Folder + "/" + key + ".prefab");
                RenderCard(prefab, key, bounds);
                UnityEngine.Object.DestroyImmediate(prefab);
            }
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            foreach (string key in new[] { "recon", "infantry", "support" })
            {
                var texture = (TextureImporter)AssetImporter.GetAtPath(Folder + "/" + key + "-card.png");
                texture.npotScale = TextureImporterNPOTScale.None; texture.mipmapEnabled = false;
                texture.alphaIsTransparency = true; texture.maxTextureSize = 1024; texture.SaveAndReimport();
            }
            Debug.Log("[军队] 三套 Unity 模型、URP 材质、机械动画枢轴与卡牌渲染完成");
        }

        private static string MaterialForPart(string name)
        {
            if (name.StartsWith("Team") || name == "Cab team roof") return "TeamAccent";
            if (name.Contains("tyre") || name.Contains("rubber track")) return "Rubber_Tread";
            if (name.Contains("lens") || name.Contains("optics") || name.Contains("visor") || name.Contains("Sensor face") || name == "Link lamp") return "Sensor_Glass";
            if (name.Contains("beacon") && name.StartsWith("Amber")) return "Service_Amber";
            if (name.StartsWith("Service plus") || name.StartsWith("Front lamp") || name.StartsWith("Hub marker")) return "Marking_White";
            if (name.Contains("hull") || name.Contains("cab") || name == "Sensor fuselage" || name == "Unmanned turret" || name.StartsWith("Modular supply case") || name == "Top panel" || name == "Sensor head") return "Ceramic_Armor";
            if (name.Contains("hub") || name.Contains("wheel") || name.StartsWith("Rotor cap") || name.StartsWith("Track top") || name.StartsWith("Landing") || name.Contains("bearing") || name.Contains("lid") || name.StartsWith("Mission")) return "Machined_Metal";
            return "Graphite_Chassis";
        }

        private static Mesh BakeMesh(string name, Mesh source, Matrix4x4 matrix)
        { var mesh = new Mesh { name = name }; mesh.CombineMeshes(new[] { new CombineInstance { mesh = source, transform = matrix } }, true, true); return SaveMesh(mesh); }
        private static Mesh SaveMesh(Mesh mesh)
        {
            string path = Folder + "/Meshes/" + mesh.name.Replace(" ", "_") + ".asset";
            Mesh existing = AssetDatabase.LoadAssetAtPath<Mesh>(path);
            if (existing == null) { AssetDatabase.CreateAsset(mesh, path); return mesh; }
            EditorUtility.CopySerialized(mesh, existing); UnityEngine.Object.DestroyImmediate(mesh); return existing;
        }

        private static void RenderCard(GameObject model, string key, Bounds bounds)
        {
            var ambientMode = RenderSettings.ambientMode; Color ambientLight = RenderSettings.ambientLight; float ambientIntensity = RenderSettings.ambientIntensity;
            RenderSettings.ambientMode = AmbientMode.Flat; RenderSettings.ambientLight = new Color(0.3f, 0.34f, 0.36f); RenderSettings.ambientIntensity = 0.7f;
            const int layer = 30;
            foreach (Transform child in model.GetComponentsInChildren<Transform>()) child.gameObject.layer = layer;
            Light[] sceneLights = UnityEngine.Object.FindObjectsByType<Light>().Where(item => item.enabled).ToArray();
            foreach (Light sceneLight in sceneLights) sceneLight.enabled = false;
            Light previousSun = RenderSettings.sun;
            var rig = new GameObject("ArmyCardRender");
            Camera camera = rig.AddComponent<Camera>(); camera.enabled = false;
            camera.clearFlags = CameraClearFlags.SolidColor; camera.backgroundColor = new Color(0.13f, 0.18f, 0.18f, 0);
            camera.cullingMask = 1 << layer; camera.orthographic = true;
            float size = Mathf.Max(bounds.size.x, bounds.size.z, bounds.size.y);
            camera.orthographicSize = size * 0.53f; camera.aspect = 1.3f;
            Vector3 center = bounds.center;
            camera.transform.position = center + new Vector3(4, 3, -5).normalized * size * 3;
            camera.transform.LookAt(center);
            camera.nearClipPlane = 0.01f; camera.farClipPlane = 100;
            camera.GetUniversalAdditionalCameraData().renderPostProcessing = false;
            var keyLight = new GameObject("ArmyStudioKey"); Light light = keyLight.AddComponent<Light>();
            light.type = LightType.Directional; light.intensity = 1.4f; light.color = new Color(1, 0.94f, 0.81f); light.cullingMask = 1 << layer;
            keyLight.transform.rotation = Quaternion.Euler(45, -30, 0);
            RenderSettings.sun = light;
            var fillLight = new GameObject("ArmyStudioFill"); Light fill = fillLight.AddComponent<Light>();
            fill.type = LightType.Directional; fill.intensity = 0.65f; fill.color = new Color(0.66f, 0.82f, 1); fill.cullingMask = 1 << layer;
            fillLight.transform.rotation = Quaternion.Euler(30, 140, 0);
            RenderTexture target = RenderTexture.GetTemporary(780, 600, 24, RenderTextureFormat.ARGB32);
            var request = new UniversalRenderPipeline.SingleCameraRequest { destination = target };
            RenderPipeline.SubmitRenderRequest(camera, request);
            RenderTexture previous = RenderTexture.active; RenderTexture.active = target;
            var texture = new Texture2D(780, 600, TextureFormat.RGBA32, false);
            texture.ReadPixels(new Rect(0, 0, 780, 600), 0, 0); texture.Apply();
            File.WriteAllBytes(Folder + "/" + key + "-card.png", texture.EncodeToPNG());
            RenderTexture.active = previous; RenderTexture.ReleaseTemporary(target);
            foreach (Light sceneLight in sceneLights) sceneLight.enabled = true;
            RenderSettings.sun = previousSun;
            RenderSettings.ambientMode = ambientMode; RenderSettings.ambientLight = ambientLight; RenderSettings.ambientIntensity = ambientIntensity;
            UnityEngine.Object.DestroyImmediate(texture); UnityEngine.Object.DestroyImmediate(rig);
            UnityEngine.Object.DestroyImmediate(keyLight); UnityEngine.Object.DestroyImmediate(fillLight);
            foreach (Transform child in model.GetComponentsInChildren<Transform>()) child.gameObject.layer = 0;
        }
    }
}
