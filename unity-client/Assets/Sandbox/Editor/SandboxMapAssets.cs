using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>将地图几何持久化为原生 Mesh 和 Material，供编辑器场景直接查看。</summary>
    public static class SandboxMapAssets
    {
        private const string AssetRoot = "Assets/Sandbox/Maps/QinglanValley";

        /// <summary>创建地图并保存网格材质；重复生成时保留现有资源身份。</summary>
        /// <returns>带版本标记的场景地图根对象。</returns>
        public static GameObject CreateSceneMap()
        {
            Directory.CreateDirectory(AssetRoot + "/Meshes");
            Directory.CreateDirectory(AssetRoot + "/Materials");
            // 先导入磁盘上的新布局，再读取 Resources，避免场景沿用上次缓存的地图版本。
            AssetDatabase.Refresh();
            SandboxMapData definition = SandboxMapFactory.LoadDefinition();
            SandboxMapGeometryData geometry = SandboxMapFactory.LoadGeometry(definition);
            var meshes = new List<Mesh>();
            var materials = new List<Material>();
            GameObject root = SandboxMapFactory.Build(definition, geometry, meshes, materials);
            ValidateImportedBuildings(root, definition);
            var materialMap = new Dictionary<Material, Material>();
            foreach (Material material in materials)
                materialMap.Add(material, Persist(material, AssetRoot + "/Materials/" + material.name + ".mat"));
            foreach (MeshRenderer renderer in root.GetComponentsInChildren<MeshRenderer>())
            {
                Material[] replacements = renderer.sharedMaterials;
                for (int index = 0; index < replacements.Length; index++) replacements[index] = materialMap[replacements[index]];
                renderer.sharedMaterials = replacements;
            }
            foreach (MeshFilter filter in root.GetComponentsInChildren<MeshFilter>())
            {
                Mesh temporary = filter.sharedMesh;
                // FBX 的导入网格已经是持久资源，不复制、销毁或覆盖模型子资源。
                if (!meshes.Contains(temporary)) continue;
                Mesh persistent = Persist(temporary, AssetRoot + "/Meshes/" + temporary.name + ".asset");
                filter.sharedMesh = persistent;
                if (filter.TryGetComponent(out MeshCollider collider)) collider.sharedMesh = persistent;
                if (persistent != temporary) Object.DestroyImmediate(temporary);
            }
            foreach (Material temporary in materials)
                if (materialMap[temporary] != temporary) Object.DestroyImmediate(temporary);
            AssetDatabase.SaveAssets();
            return root;
        }

        /// <summary>检查实际导入的建筑比例、UV、法线及材质绑定，拒绝空模型或错误轴向。</summary>
        /// <param name="root">已经放置建筑的地图根对象。</param>
        /// <param name="definition">当前同源建筑布局。</param>
        private static void ValidateImportedBuildings(GameObject root, SandboxMapData definition)
        {
            if (definition.buildingsBaked)
            {
                var checkedModels = new HashSet<string>();
                foreach (SandboxMapBuilding building in definition.buildings)
                {
                    if (!checkedModels.Add(building.model)) continue;
                    GameObject prototype = Resources.Load<GameObject>("Battlefield/Models/" + building.model);
                    if (prototype == null) throw new System.InvalidOperationException("缺少原创 Blender 原型：" + building.model);
                    MeshFilter[] filters = prototype.GetComponentsInChildren<MeshFilter>();
                    if (filters.Length == 0) throw new System.InvalidOperationException("原创模型没有网格：" + building.model);
                    foreach (MeshFilter filter in filters)
                    {
                        Mesh mesh = filter.sharedMesh;
                        if (mesh == null || mesh.vertexCount == 0 || !AssetDatabase.Contains(mesh)
                            || !mesh.HasVertexAttribute(UnityEngine.Rendering.VertexAttribute.TexCoord0)
                            || !mesh.HasVertexAttribute(UnityEngine.Rendering.VertexAttribute.Normal)
                            || mesh.bounds.size.y < 2 || mesh.bounds.size.y > 80)
                            throw new System.InvalidOperationException("原创模型比例、竖轴或 UV 无效：" + building.model);
                    }
                }
                Transform buildings = root.transform.Find("settlement");
                if (buildings == null || buildings.GetComponentsInChildren<MeshRenderer>().Length == 0)
                    throw new System.InvalidOperationException("策略地图缺少实际合批建筑");
                Debug.Log("[穹图] 十二公里 Blender 地图检查通过：" + checkedModels.Count + " 类原型，" + definition.buildings.Length + " 栋合批建筑");
                return;
            }
            foreach (SandboxMapBuilding building in definition.buildings)
            {
                Transform instance = root.transform.Find(building.layer + "/" + building.id + "_" + building.model);
                if (instance == null) throw new System.InvalidOperationException("建筑未放入场景：" + building.id);
                MeshRenderer[] renderers = instance.GetComponentsInChildren<MeshRenderer>();
                if (renderers.Length == 0) throw new System.InvalidOperationException("建筑缺少实际渲染网格：" + building.id);
                Bounds bounds = renderers[0].bounds;
                foreach (MeshRenderer renderer in renderers)
                {
                    bounds.Encapsulate(renderer.bounds);
                    Mesh mesh = renderer.GetComponent<MeshFilter>().sharedMesh;
                    if (mesh == null || mesh.vertexCount == 0 || !AssetDatabase.Contains(mesh)
                        || !mesh.HasVertexAttribute(UnityEngine.Rendering.VertexAttribute.TexCoord0)
                        || !mesh.HasVertexAttribute(UnityEngine.Rendering.VertexAttribute.Normal)
                        || !mesh.HasVertexAttribute(UnityEngine.Rendering.VertexAttribute.Tangent))
                        throw new System.InvalidOperationException("建筑 FBX 导入数据不完整：" + building.id);
                    foreach (Material material in renderer.sharedMaterials)
                        if (material.GetTexture("_BaseMap") == null || material.GetTexture("_BumpMap") == null || !material.IsKeywordEnabled("_NORMALMAP"))
                            throw new System.InvalidOperationException("建筑表面贴图绑定不完整：" + building.id);
                }
                if (bounds.size.y < 2 || bounds.size.y > 14 || bounds.max.y < instance.position.y + 2)
                    throw new System.InvalidOperationException("建筑米制比例或竖轴无效：" + building.id);
            }
            Debug.Log("[穹图] Blender 建筑实际导入检查通过：" + definition.buildings.Length + " 栋；比例、UV、法线、切线与贴图绑定有效");
        }

        /// <summary>创建或更新已存在的资源，不删除或更换其 GUID。</summary>
        /// <typeparam name="T">Unity 资源类型。</typeparam>
        /// <param name="source">本次生成的临时对象。</param>
        /// <param name="path">本地图资产目录中的目标路径。</param>
        /// <returns>可序列化到场景的持久资源。</returns>
        private static T Persist<T>(T source, string path) where T : Object
        {
            T existing = AssetDatabase.LoadAssetAtPath<T>(path);
            if (existing == null)
            {
                AssetDatabase.CreateAsset(source, path);
                return source;
            }
            EditorUtility.CopySerialized(source, existing);
            EditorUtility.SetDirty(existing);
            return existing;
        }
    }
}
