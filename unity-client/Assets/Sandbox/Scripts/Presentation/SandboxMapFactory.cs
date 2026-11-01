using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace Qiongtu.Sandbox
{
    /// <summary>从同源网格创建实际 Unity 地图，不依赖外部模型导入插件。</summary>
    public static class SandboxMapFactory
    {
        public const string ResourceRoot = "Maps/QinglanValley/";

        /// <summary>读取并校验固定地图定义。</summary>
        /// <returns>可供显示高度和标记共同使用的布局。</returns>
        public static SandboxMapData LoadDefinition()
        {
            TextAsset asset = Resources.Load<TextAsset>(ResourceRoot + "map-definition");
            if (asset == null) throw new InvalidOperationException("缺少青岚河谷地图定义，请先运行 Tools/build_qinglan_map.py");
            SandboxMapData definition = JsonUtility.FromJson<SandboxMapData>(asset.text);
            string error = definition == null ? "地图定义不能解析" : definition.Validate();
            if (error.Length > 0) throw new InvalidOperationException(error);
            return definition;
        }

        /// <summary>按布局版本读取可复用的静态网格。</summary>
        /// <param name="definition">当前布局。</param>
        /// <returns>已验证的网格分块。</returns>
        public static SandboxMapGeometryData LoadGeometry(SandboxMapData definition)
        {
            TextAsset asset = Resources.Load<TextAsset>(ResourceRoot + "map-geometry");
            if (asset == null) throw new InvalidOperationException("缺少青岚河谷网格资源");
            SandboxMapGeometryData geometry = JsonUtility.FromJson<SandboxMapGeometryData>(asset.text);
            string error = geometry == null ? "地图网格不能解析" : geometry.Validate(definition);
            if (error.Length > 0) throw new InvalidOperationException(error);
            return geometry;
        }

        /// <summary>创建公开静态地图，编辑器可将网格和材质持久化。</summary>
        /// <param name="definition">布局及颜色定义。</param>
        /// <param name="geometry">同版本网格。</param>
        /// <param name="meshes">记录新建网格以便持久化或释放。</param>
        /// <param name="materials">记录新建材质以便持久化或释放。</param>
        /// <returns>具有版本标记的实际场景对象。</returns>
        public static GameObject Build(SandboxMapData definition, SandboxMapGeometryData geometry, List<Mesh> meshes, List<Material> materials)
        {
            string error = geometry.Validate(definition);
            if (error.Length > 0) throw new InvalidOperationException(error);
            var root = new GameObject("QinglanValleyMap");
            SandboxMapAnchor anchor = root.AddComponent<SandboxMapAnchor>();
            anchor.mapId = definition.mapId;
            anchor.contentHash = definition.contentHash;
            var palette = new Dictionary<string, Material>();
            Shader shader = Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null) throw new InvalidOperationException("未找到本工程 URP Lit 着色器");
            foreach (SandboxMapMaterial color in definition.materials)
            {
                Material material = SandboxBattlefieldMaterials.Create(color.id, shader, new Color(color.red, color.green, color.blue));
                palette.Add(color.id, material);
                materials.Add(material);
            }
            var groups = new Dictionary<string, Transform>();
            Dictionary<Vector3, Vector3> surfaceNormals = BuildSurfaceNormals(geometry);
            foreach (SandboxMapMeshData part in geometry.parts)
            {
                if (!groups.TryGetValue(part.layer, out Transform group))
                {
                    var groupObject = new GameObject(part.layer);
                    groupObject.transform.SetParent(root.transform, false);
                    group = groupObject.transform;
                    groups.Add(part.layer, group);
                }
                var vertices = new Vector3[part.vertices.Length / 3];
                for (int index = 0; index < vertices.Length; index++)
                    vertices[index] = new Vector3(part.vertices[index * 3], part.vertices[index * 3 + 1], part.vertices[index * 3 + 2]);
                var mesh = new Mesh { name = part.name, vertices = vertices, triangles = part.triangles };
                if (vertices.Length > 65535) throw new InvalidOperationException("地图分块超过当前索引范围");
                mesh.RecalculateNormals();
                if (SmoothSurface(part.layer))
                {
                    var normals = new Vector3[vertices.Length];
                    for (int index = 0; index < vertices.Length; index++) normals[index] = surfaceNormals[vertices[index]].normalized;
                    mesh.normals = normals;
                }
                mesh.uv = BuildSurfaceUv(vertices, mesh.normals);
                mesh.RecalculateTangents();
                mesh.RecalculateBounds();
                meshes.Add(mesh);
                var instance = new GameObject(part.name);
                instance.transform.SetParent(group, false);
                instance.AddComponent<MeshFilter>().sharedMesh = mesh;
                MeshRenderer renderer = instance.AddComponent<MeshRenderer>();
                renderer.sharedMaterial = palette[part.material];
                // 贴地薄片接收建筑阴影，但不作为悬空实体向地面投下黑边。
                bool surfaceOverlay = part.layer == "grid" || part.layer == "water" || part.layer == "routes" || part.layer == "farmland"
                    || part.layer == "ditches" || part.layer == "riverbanks" || part.layer == "damage" || part.layer == "ponds";
                renderer.shadowCastingMode = surfaceOverlay ? ShadowCastingMode.Off : ShadowCastingMode.On;
                if (part.layer == "terrain") instance.AddComponent<MeshCollider>().sharedMesh = mesh;
            }
            BuildBuildings(definition, root.transform, groups, shader, materials);
            return root;
        }

        /// <summary>为同位置地表顶点汇总法线，消除网格分块和三角形造成的硬折痕。</summary>
        /// <param name="geometry">地图网格数据。</param>
        /// <returns>位置到面积加权法线的映射。</returns>
        private static Dictionary<Vector3, Vector3> BuildSurfaceNormals(SandboxMapGeometryData geometry)
        {
            var result = new Dictionary<Vector3, Vector3>();
            foreach (SandboxMapMeshData part in geometry.parts)
            {
                if (!SmoothSurface(part.layer)) continue;
                for (int triangle = 0; triangle < part.triangles.Length; triangle += 3)
                {
                    var positions = new Vector3[3];
                    for (int corner = 0; corner < 3; corner++)
                    {
                        int offset = part.triangles[triangle + corner] * 3;
                        positions[corner] = new Vector3(part.vertices[offset], part.vertices[offset + 1], part.vertices[offset + 2]);
                    }
                    Vector3 normal = Vector3.Cross(positions[1] - positions[0], positions[2] - positions[0]);
                    foreach (Vector3 position in positions)
                    {
                        result.TryGetValue(position, out Vector3 previous);
                        result[position] = previous + normal;
                    }
                }
            }
            return result;
        }

        /// <summary>确认需要跨面平滑的自然表面。</summary>
        /// <param name="layer">地图图层。</param>
        /// <returns>地形、远景和树冠需要平滑。</returns>
        private static bool SmoothSurface(string layer) => layer == "terrain" || layer == "landscape" || layer == "environment";

        /// <summary>按主法线方向生成米制平铺 UV，垂直墙面不使用地面投影。</summary>
        /// <param name="vertices">场景单位顶点。</param>
        /// <param name="normals">每顶点法线。</param>
        /// <returns>供底色和法线共用的 UV。</returns>
        private static Vector2[] BuildSurfaceUv(Vector3[] vertices, Vector3[] normals)
        {
            var coordinates = new Vector2[vertices.Length];
            for (int index = 0; index < vertices.Length; index++)
            {
                Vector3 vertex = vertices[index];
                Vector3 normal = normals[index];
                coordinates[index] = Mathf.Abs(normal.y) > 0.6f ? new Vector2(vertex.x, vertex.z) / 5
                    : Mathf.Abs(normal.z) > Mathf.Abs(normal.x) ? new Vector2(vertex.x, vertex.y) / 3 : new Vector2(vertex.z, vertex.y) / 3;
            }
            return coordinates;
        }

        /// <summary>放置共享 Blender 模型，并用工程表面材质替换导入器默认材质。</summary>
        /// <param name="definition">与地表共用坐标的建筑布局。</param>
        /// <param name="root">地图根节点。</param>
        /// <param name="groups">已有图层节点。</param>
        /// <param name="shader">URP 着色器。</param>
        /// <param name="materials">记录新建共享材质。</param>
        private static void BuildBuildings(SandboxMapData definition, Transform root, Dictionary<string, Transform> groups, Shader shader, List<Material> materials)
        {
            var models = new Dictionary<string, GameObject>();
            var surfaces = new Dictionary<string, Material>();
            foreach (SandboxMapBuilding building in definition.buildings)
            {
                if (!groups.TryGetValue(building.layer, out Transform group))
                {
                    var groupObject = new GameObject(building.layer);
                    groupObject.transform.SetParent(root, false);
                    group = groupObject.transform;
                    groups.Add(building.layer, group);
                }
                if (!models.TryGetValue(building.model, out GameObject model))
                {
                    model = Resources.Load<GameObject>("Battlefield/Models/" + building.model);
                    if (model == null) throw new InvalidOperationException("缺少 Blender 建筑模型：" + building.model);
                    models.Add(building.model, model);
                }
                GameObject instance = UnityEngine.Object.Instantiate(model, group);
                instance.name = building.id + "_" + building.model;
                instance.transform.localPosition = new Vector3(building.x, building.y, building.z);
                instance.transform.localRotation = Quaternion.Euler(0, building.yaw, 0);
                instance.transform.localScale = new Vector3(building.scaleX, building.scaleY, building.scaleZ);
                foreach (MeshRenderer renderer in instance.GetComponentsInChildren<MeshRenderer>())
                {
                    Material[] replacements = renderer.sharedMaterials;
                    for (int index = 0; index < replacements.Length; index++)
                    {
                        string name = replacements[index].name;
                        if (!surfaces.TryGetValue(name, out Material surface))
                        {
                            surface = SandboxBattlefieldMaterials.Create(name, shader, Color.white);
                            surfaces.Add(name, surface);
                            materials.Add(surface);
                        }
                        replacements[index] = surface;
                    }
                    renderer.sharedMaterials = replacements;
                    renderer.shadowCastingMode = ShadowCastingMode.On;
                    renderer.receiveShadows = true;
                }
            }
        }
    }
}
