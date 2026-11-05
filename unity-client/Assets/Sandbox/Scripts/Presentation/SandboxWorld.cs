using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace Qiongtu.Sandbox
{
    /// <summary>构建独立的低多边形三维沙盘，只负责静态场景与角色投影表现。</summary>
    public sealed class SandboxWorld : MonoBehaviour
    {
        private readonly Dictionary<string, SandboxUnitView> unitViews = new Dictionary<string, SandboxUnitView>();
        private readonly List<Material> ownedMaterials = new List<Material>();
        private readonly List<Mesh> ownedMeshes = new List<Mesh>();
        private Transform unitRoot;
        private GameObject routes;
        private GameObject grid;
        private GameObject facilities;
        private GameObject captureMarkers;
        private GameObject deploymentMarkers;
        private SandboxMapPoint[] mapPoints;
        /// <summary>按两侧各两处近端目标及北、中、南三处争夺目标安排公开占点。</summary>
        private static readonly SandboxMapPoint[] CapturePoints =
        {
            new SandboxMapPoint { id = "CAPTURE_A", name = "A · 西南镇", kind = "capture", x = 3000, z = 3000 },
            new SandboxMapPoint { id = "CAPTURE_B", name = "B · 城市西区", kind = "capture", x = 4140, z = 5000 },
            new SandboxMapPoint { id = "CAPTURE_C", name = "C · 北岸镇", kind = "capture", x = 4500, z = 8500 },
            new SandboxMapPoint { id = "CAPTURE_D", name = "D · 中央桥西", kind = "capture", x = 5500, z = 5800 },
            new SandboxMapPoint { id = "CAPTURE_E", name = "E · 南桥东岸", kind = "capture", x = 7350, z = 3000 },
            new SandboxMapPoint { id = "CAPTURE_F", name = "F · 工业货场北侧", kind = "capture", x = 8200, z = 5800 },
            new SandboxMapPoint { id = "CAPTURE_G", name = "G · 机场入口", kind = "capture", x = 8400, z = 9300 }
        };
        private LineRenderer selectionRing;
        private Material redMaterial;
        private Material blueMaterial;
        private Material darkMaterial;
        private Material metalMaterial;
        private static SandboxMapData mapDefinition;
        private GameObject mapRoot;
        private SandboxArmyNavigation navigation;
        private string selectedUnit;
        private readonly HashSet<string> relocated = new HashSet<string>();

        /// <summary>当前公开的稳定地图标记。</summary>
        public SandboxMapPoint[] MapPoints => mapPoints ?? Definition.points;
        /// <summary>实际显示地图范围，供相机取景与边界限制共用。</summary>
        public Vector2 MapSize => new Vector2(Definition.width, Definition.length);

        /// <summary>设施及其文字标记是否可见。</summary>
        public bool FacilitiesVisible { get; private set; } = true;

        /// <summary>加载当前固定地图并共用同一套高度与标记。</summary>
        private static SandboxMapData Definition => mapDefinition ?? (mapDefinition = SandboxMapFactory.LoadDefinition());

        /// <summary>装配同源地图资源与单位显示层，支持复用编辑器保存的地图。</summary>
        /// <param name="existingMap">已保存的静态地图；为空时从网格数据创建。</param>
        public void Initialize(GameObject existingMap = null)
        {
            redMaterial = CreateMaterial("RedUnit", new Color(0.86f, 0.38f, 0.27f));
            blueMaterial = CreateMaterial("BlueUnit", new Color(0.27f, 0.57f, 0.76f));
            darkMaterial = CreateMaterial("Graphite", new Color(0.11f, 0.15f, 0.15f));
            metalMaterial = CreateMaterial("SoftMetal", new Color(0.64f, 0.68f, 0.61f));
            SandboxMapData definition = Definition;
            if (existingMap != null)
            {
                SandboxMapAnchor anchor = existingMap.GetComponent<SandboxMapAnchor>();
                if (anchor == null || anchor.mapId != definition.mapId || anchor.contentHash != definition.contentHash)
                    throw new InvalidOperationException("场景地图版本与资源不同，请重新生成作战沙盘场景");
                mapRoot = existingMap;
            }
            else
            {
                SandboxMapGeometryData geometry = SandboxMapFactory.LoadGeometry(definition);
                mapRoot = SandboxMapFactory.Build(definition, geometry, ownedMeshes, ownedMaterials);
            }
            mapRoot.transform.SetParent(transform, false);
            routes = mapRoot.transform.Find("routes").gameObject;
            grid = mapRoot.transform.Find("grid").gameObject;
            facilities = mapRoot.transform.Find("facilities").gameObject;
            BuildCaptureMarkers(definition);
            BuildDeploymentMarkers(definition);
            unitRoot = NewGroup("ProjectedUnits").transform;
            selectionRing = CreateLine("SelectionRing", unitRoot, Circle(Vector3.zero, 2.1f), 0.1f, new Color(0.89f, 0.88f, 0.56f));
            selectionRing.gameObject.SetActive(false);
        }

        /// <summary>按新的角色投影更新场景，移除不再允许显示的对象。</summary>
        /// <param name="projection">当前有效的角色投影。</param>
        public void ApplyProjection(SandboxProjection projection, bool authoritative = false)
        {
            var allowed = new HashSet<string>();
            foreach (SandboxUnitData unit in projection.units)
            {
                allowed.Add(unit.id);
                if (!unitViews.TryGetValue(unit.id, out SandboxUnitView view))
                {
                    view = BuildUnit(unit);
                    unitViews.Add(unit.id, view);
                }
                Vector3 position = ArmyPosition(unit.x, unit.z, unit.type);
                if (authoritative || relocated.Remove(unit.id)) view.Relocate(position);
                else if (!view.HasTarget(position))
                {
                    if (!view.HasPosition || unit.type == SandboxUnitType.Recon) view.SetTarget(position);
                    else
                    {
                        List<Vector3> path = Navigation.FindPath(view.transform.position, position);
                        // An unreachable demo target must not produce a straight line through water or buildings.
                        if (path != null) view.SetTarget(position, path);
                    }
                }
            }
            foreach (string unitId in new List<string>(unitViews.Keys))
            {
                if (allowed.Contains(unitId)) continue;
                // 立即禁用命中与渲染，再在帧末销毁，避免角色切换时泄露旧对象。
                unitViews[unitId].HideRoute();
                unitViews[unitId].gameObject.SetActive(false);
                Destroy(unitViews[unitId].gameObject);
                unitViews.Remove(unitId);
            }
        }

        /// <summary>清除旧角色的对象引用及选中标记。</summary>
        public void ClearProjection()
        {
            foreach (SandboxUnitView view in unitViews.Values)
            {
                view.HideRoute();
                view.gameObject.SetActive(false);
                Destroy(view.gameObject);
            }
            unitViews.Clear();
            selectionRing.gameObject.SetActive(false);
            selectedUnit = null; relocated.Clear();
        }

        /// <summary>查找当前允许显示的单位变换。</summary>
        /// <param name="unitId">单位稳定标识。</param>
        /// <returns>当前显示对象；不存在时返回空。</returns>
        public Transform FindUnit(string unitId) => unitId != null && unitViews.TryGetValue(unitId, out SandboxUnitView view) ? view.transform : null;

        /// <summary>在选中单位的地面位置显示观察标记。</summary>
        /// <param name="unitId">当前选中单位。</param>
        public void Select(string unitId)
        {
            selectedUnit = unitId;
            foreach (var entry in unitViews) entry.Value.ShowRoute(entry.Key == unitId);
            Transform target = FindUnit(unitId);
            selectionRing.gameObject.SetActive(target != null);
            if (target == null) return;
            Vector3 center = Surface(target.position.x, target.position.z) + Vector3.up * 0.3f;
            selectionRing.SetPositions(Circle(center, 2.1f));
        }

        /// <summary>调整纯显示图层，不改变演示状态。</summary>
        /// <param name="layer">图层名称。</param>
        /// <param name="visible">是否可见。</param>
        public void SetLayer(string layer, bool visible)
        {
            GameObject target = layer == "grid" ? grid : layer == "routes" ? routes : facilities;
            target.SetActive(visible);
            if (layer == "facilities")
            {
                FacilitiesVisible = visible;
                captureMarkers.SetActive(visible);
                deploymentMarkers.SetActive(visible);
                foreach (string groupName in new[] { "markers", "settlement", "deployment", "village", "logistics", "central_village" })
                {
                    Transform group = mapRoot.transform.Find(groupName);
                    if (group != null) group.gameObject.SetActive(visible);
                }
            }
        }

        /// <summary>叠加七处公开占点的边界，不生成占领归属、进度或计分状态。</summary>
        /// <param name="definition">当前米制地图，原有设施和任务标记保持原样。</param>
        private void BuildCaptureMarkers(SandboxMapData definition)
        {
            captureMarkers = NewGroup("CaptureMarkers");
            mapPoints = new SandboxMapPoint[definition.points.Length + CapturePoints.Length + definition.deploymentZones.Length];
            Array.Copy(definition.points, mapPoints, definition.points.Length);
            Array.Copy(CapturePoints, 0, mapPoints, definition.points.Length, CapturePoints.Length);
            foreach (SandboxMapPoint point in CapturePoints)
            {
                Vector3[] boundary = CaptureBoundary(point, 200);
                CreateLine(point.id + "Outline", captureMarkers.transform, boundary, 12, new Color(0.12f, 0.15f, 0.12f));
                LineRenderer ring = CreateLine(point.id, captureMarkers.transform, boundary, 6, new Color(0.91f, 0.82f, 0.47f));
                ring.sharedMaterial.EnableKeyword("_EMISSION");
                ring.sharedMaterial.SetColor("_EmissionColor", new Color(0.45f, 0.36f, 0.12f));
                ring.sortingOrder = 1;
            }
        }

        /// <summary>逐点采样占点边界高度，使圆圈沿地形表面连续显示。</summary>
        /// <param name="point">占点中心及其稳定身份。</param>
        /// <param name="radius">显示半径，单位为米，不作为后端判定范围。</param>
        /// <returns>包含重复首尾顶点的闭合边界。</returns>
        private static Vector3[] CaptureBoundary(SandboxMapPoint point, float radius)
        {
            var boundary = new Vector3[97];
            for (int index = 0; index < boundary.Length; index++)
            {
                float angle = index * Mathf.PI * 2 / (boundary.Length - 1);
                boundary[index] = Surface(point.x + Mathf.Cos(angle) * radius, point.z + Mathf.Sin(angle) * radius) + Vector3.up * 6;
            }
            return boundary;
        }

        /// <summary>从后端共用的地图定义标注双方兵营部署范围，不另建一套坐标边界。</summary>
        /// <param name="definition">包含双方部署矩形的当前地图定义。</param>
        private void BuildDeploymentMarkers(SandboxMapData definition)
        {
            deploymentMarkers = NewGroup("DeploymentMarkers");
            for (int index = 0; index < definition.deploymentZones.Length; index++)
            {
                SandboxDeploymentZone zone = definition.deploymentZones[index];
                bool redSide = zone.side == "red";
                Color color = redSide ? new Color(0.95f, 0.36f, 0.25f) : new Color(0.28f, 0.65f, 0.96f);
                Vector3[] boundary = DeploymentBoundary(zone);
                CreateLine(zone.id + "Outline", deploymentMarkers.transform, boundary, 24, new Color(0.1f, 0.13f, 0.13f));
                LineRenderer border = CreateLine(zone.id + "Border", deploymentMarkers.transform, boundary, 12, color);
                border.sharedMaterial.EnableKeyword("_EMISSION");
                border.sharedMaterial.SetColor("_EmissionColor", color * 0.5f);
                border.sortingOrder = 1;
                BuildDeploymentFill(zone, color);
                mapPoints[definition.points.Length + CapturePoints.Length + index] = new SandboxMapPoint
                {
                    id = zone.id + "_LABEL",
                    name = (redSide ? "红方" : "蓝方") + "兵营部署区\n" + (zone.maxX - zone.minX).ToString("0")
                        + " × " + (zone.maxZ - zone.minZ).ToString("0") + " m",
                    kind = redSide ? "deployment_red" : "deployment_blue",
                    x = (zone.minX + zone.maxX) / 2,
                    z = (zone.minZ + zone.maxZ) / 2
                };
            }
        }

        /// <summary>沿矩形四边逐段贴地采样，使范围线随真实显示地形起伏。</summary>
        /// <param name="zone">地图已有的部署矩形。</param>
        /// <returns>按顺序闭合的边界顶点，不参与业务碰撞。</returns>
        private static Vector3[] DeploymentBoundary(SandboxDeploymentZone zone)
        {
            var corners = new[]
            {
                new Vector2(zone.minX, zone.minZ), new Vector2(zone.maxX, zone.minZ),
                new Vector2(zone.maxX, zone.maxZ), new Vector2(zone.minX, zone.maxZ)
            };
            var boundary = new List<Vector3>();
            for (int side = 0; side < corners.Length; side++)
            {
                Vector2 start = corners[side];
                Vector2 end = corners[(side + 1) % corners.Length];
                int segments = Mathf.CeilToInt(Vector2.Distance(start, end) / 50);
                for (int segment = 0; segment < segments; segment++)
                {
                    Vector2 position = Vector2.Lerp(start, end, (float)segment / segments);
                    boundary.Add(Surface(position.x, position.y) + Vector3.up * 9);
                }
            }
            boundary.Add(boundary[0]);
            return boundary.ToArray();
        }

        /// <summary>使用已有透明材质绘制贴地浅色填充，不为部署区增加碰撞或通行规则。</summary>
        /// <param name="zone">与后端共用的部署矩形。</param>
        /// <param name="color">对应阵营的基础颜色。</param>
        private void BuildDeploymentFill(SandboxDeploymentZone zone, Color color)
        {
            int columns = Mathf.CeilToInt((zone.maxX - zone.minX) / 50);
            int rows = Mathf.CeilToInt((zone.maxZ - zone.minZ) / 50);
            var vertices = new Vector3[(columns + 1) * (rows + 1)];
            var triangles = new int[columns * rows * 6];
            for (int row = 0; row <= rows; row++)
            {
                for (int column = 0; column <= columns; column++)
                {
                    float positionX = Mathf.Lerp(zone.minX, zone.maxX, (float)column / columns);
                    float positionZ = Mathf.Lerp(zone.minZ, zone.maxZ, (float)row / rows);
                    vertices[row * (columns + 1) + column] = Surface(positionX, positionZ) + Vector3.up * 2;
                    if (row == rows || column == columns) continue;
                    int lowerLeft = row * (columns + 1) + column;
                    int upperLeft = lowerLeft + columns + 1;
                    int offset = (row * columns + column) * 6;
                    triangles[offset] = lowerLeft; triangles[offset + 1] = upperLeft; triangles[offset + 2] = upperLeft + 1;
                    triangles[offset + 3] = lowerLeft; triangles[offset + 4] = upperLeft + 1; triangles[offset + 5] = lowerLeft + 1;
                }
            }
            var mesh = new Mesh { name = zone.id + "Fill", vertices = vertices, triangles = triangles };
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            ownedMeshes.Add(mesh);
            Material template = Resources.Load<Material>("Sandbox/Cloud");
            Material material = template != null ? new Material(template) : SandboxCloudLayer.CreateCloudMaterial();
            material.name = zone.id + "FillMaterial";
            material.SetColor("_BaseColor", new Color(color.r, color.g, color.b, 0.16f));
            ownedMaterials.Add(material);
            var fill = new GameObject(zone.id + "Fill");
            fill.transform.SetParent(deploymentMarkers.transform, false);
            fill.AddComponent<MeshFilter>().sharedMesh = mesh;
            MeshRenderer renderer = fill.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = material;
            renderer.shadowCastingMode = ShadowCastingMode.Off;
            renderer.receiveShadows = false;
        }

        /// <summary>将演示平面坐标映射至三维地形表面。</summary>
        /// <param name="positionX">横向业务坐标。</param>
        /// <param name="positionZ">纵向业务坐标。</param>
        /// <returns>仅用于显示的三维位置。</returns>
        public static Vector3 Surface(float positionX, float positionZ) => new Vector3(positionX, Definition.SampleSurfaceHeight(positionX, positionZ), positionZ);

        /// <summary>将保留的旧演示坐标投影到新的十二公里米制地图。</summary>
        /// <param name="positionX">原业务横坐标。</param>
        /// <param name="positionZ">原业务纵坐标。</param>
        /// <returns>实际显示地图上的平面坐标。</returns>
        private static Vector2 ProjectBusinessPosition(float positionX, float positionZ)
        {
            // 旧业务坐标范围为 120×80；按米制新地图等比例铺开，不改变存档字段。
            float mappedX = Mathf.Clamp(positionX * 100, 0, Definition.width);
            float mappedZ = Mathf.Clamp(positionZ * 150, 0, Definition.length);
            return new Vector2(mappedX, mappedZ);
        }

        /// <summary>构建当前角色可见的简化三维单位。</summary>
        /// <param name="unit">已投影的单位数据。</param>
        /// <returns>可点选的单位显示组件。</returns>
        private SandboxUnitView BuildUnit(SandboxUnitData unit)
        {
            var root = new GameObject(unit.id);
            root.transform.SetParent(unitRoot, false);
            var visual = new GameObject("AnimatedModel"); visual.transform.SetParent(root.transform, false);
            GameObject prefab = Resources.Load<GameObject>("Army/" + DemoSandboxViewSource.ArmyKey(unit.type));
            if (prefab == null) throw new InvalidOperationException("缺少已导入军队模型，请执行军队资源生成");
            GameObject model = Instantiate(prefab, visual.transform, false);
            // Preserve baked mesh dimensions and put the lowest point on the sampled terrain.
            Bounds bounds = new Bounds(); bool first = true;
            foreach (Renderer renderer in model.GetComponentsInChildren<Renderer>())
            { if (first) { bounds = renderer.bounds; first = false; } else bounds.Encapsulate(renderer.bounds); }
            model.transform.localPosition = new Vector3(0, -bounds.min.y, 0);
            var collider = root.AddComponent<BoxCollider>();
            collider.center = new Vector3(bounds.center.x, bounds.size.y / 2, bounds.center.z);
            collider.size = Vector3.Max(bounds.size, new Vector3(3, 1.8f, 3));
            var view = root.AddComponent<SandboxUnitView>();
            view.Initialize(unit.id, unit.type, visual.transform, unit.side == SandboxRole.Red ? redMaterial.color : blueMaterial.color);
            var line = CreateLine("MovementRoute", unitRoot, new Vector3[0], 0.13f, new Color(0.88f, 0.77f, 0.35f));
            line.gameObject.SetActive(false); view.AttachRoute(line);
            return view;
        }

        private SandboxArmyNavigation Navigation => navigation ?? (navigation = new SandboxArmyNavigation(mapRoot, Definition));
        public static Vector3 ArmyPosition(float x, float z, SandboxUnitType type)
        {
            Vector2 mapped = ProjectBusinessPosition(x, z);
            return Surface(mapped.x, mapped.y) + Vector3.up * (type == SandboxUnitType.Recon ? 6 : 0.08f);
        }
        public bool CanPlace(float x, float z, SandboxUnitType type) => x >= 0 && x <= 120 && z >= 0 && z <= 80 && (type == SandboxUnitType.Recon || Navigation.IsWalkable(ArmyPosition(x, z, type)));
        public bool CanMove(string id, float x, float z, SandboxUnitType type)
        {
            Transform unit = FindUnit(id);
            return unit != null && CanPlace(x, z, type) && (type == SandboxUnitType.Recon || Navigation.FindPath(unit.position, ArmyPosition(x, z, type)) != null);
        }
        public void RelocateOnRefresh(IEnumerable<string> ids) { foreach (string id in ids) relocated.Add(id); }

        /// <summary>将十二公里地图上的选点还原为旧存档使用的业务坐标。</summary>
        /// <param name="point">地图中的米制世界位置。</param>
        /// <param name="business">还原后的 120×80 业务坐标。</param>
        /// <returns>位置有限且位于可操作范围内时返回真。</returns>
        public bool TryBusinessPosition(Vector3 point, out Vector2 business)
        {
            business = new Vector2(point.x / 100f, point.z / 150f);
            return business.x >= 0 && business.x <= 120 && business.y >= 0 && business.y <= 80;
        }
        private void LateUpdate()
        {
            Transform target = FindUnit(selectedUnit);
            selectionRing.gameObject.SetActive(target != null);
            if (target != null) selectionRing.SetPositions(Circle(Surface(target.position.x, target.position.z) + Vector3.up * 0.3f, 2.5f));
        }

        /// <summary>创建归属于场景的 URP 兼容材质。</summary>
        /// <param name="name">材质名称。</param>
        /// <param name="color">基础颜色。</param>
        /// <returns>在销毁时统一释放的材质。</returns>
        private Material CreateMaterial(string name, Color color)
        {
            // 地图的持久材质已引用此着色器；URP 默认材质接口在非编辑器构建中返回空。
            Shader shader = Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null) throw new InvalidOperationException("缺少已随地图打包的 URP Lit 着色器");
            var material = new Material(shader) { name = name };
            material.SetColor("_BaseColor", color);
            if (material.HasProperty("_Smoothness")) material.SetFloat("_Smoothness", 0.15f);
            ownedMaterials.Add(material);
            return material;
        }

        /// <summary>创建有名称的场景子组。</summary>
        /// <param name="name">子组名称。</param>
        /// <returns>新建子组。</returns>
        private GameObject NewGroup(string name)
        {
            var group = new GameObject(name);
            group.transform.SetParent(transform, false);
            return group;
        }

        /// <summary>创建一段具有真实世界宽度的显示线。</summary>
        /// <param name="name">线名称。</param>
        /// <param name="parent">父节点。</param>
        /// <param name="points">世界坐标。</param>
        /// <param name="width">显示宽度。</param>
        /// <param name="color">显示颜色。</param>
        /// <returns>线组件。</returns>
        private LineRenderer CreateLine(string name, Transform parent, Vector3[] points, float width, Color color)
        {
            var lineObject = new GameObject(name);
            lineObject.transform.SetParent(parent, false);
            LineRenderer line = lineObject.AddComponent<LineRenderer>();
            line.sharedMaterial = CreateMaterial(name + "Material", color);
            line.positionCount = points.Length;
            line.SetPositions(points);
            line.widthMultiplier = width;
            line.generateLightingData = true;
            line.shadowCastingMode = ShadowCastingMode.Off;
            line.receiveShadows = false;
            return line;
        }

        /// <summary>创建基本方盒形表现对象。</summary>
        private GameObject CreateBox(string name, Transform parent, Vector3 position, Vector3 scale, Material material)
            => CreatePrimitive(name, PrimitiveType.Cube, parent, position, scale, material, false);

        /// <summary>创建基本圆柱形表现对象。</summary>
        private GameObject CreateCylinder(string name, Transform parent, Vector3 position, Vector3 scale, Material material)
            => CreatePrimitive(name, PrimitiveType.Cylinder, parent, position, scale, material, false);

        /// <summary>创建使用父节点局部坐标的几何体，显示对象默认不参与规则碰撞。</summary>
        private GameObject CreatePrimitive(string name, PrimitiveType type, Transform parent, Vector3 position, Vector3 scale, Material material, bool collider)
        {
            GameObject item = GameObject.CreatePrimitive(type);
            item.name = name;
            item.transform.SetParent(parent, false);
            item.transform.localPosition = position;
            item.transform.localScale = scale;
            item.GetComponent<Renderer>().sharedMaterial = material;
            if (!collider) Destroy(item.GetComponent<Collider>());
            return item;
        }

        /// <summary>为地面选中框生成闭合圆。</summary>
        private static Vector3[] Circle(Vector3 center, float radius)
        {
            var points = new Vector3[49];
            for (int index = 0; index < points.Length; index++)
            {
                float angle = index * Mathf.PI * 2 / (points.Length - 1);
                points[index] = center + new Vector3(Mathf.Cos(angle), 0, Mathf.Sin(angle)) * radius;
            }
            return points;
        }

        /// <summary>释放运行时创建的材质和网格。</summary>
        private void OnDestroy()
        {
            foreach (Material material in ownedMaterials) Destroy(material);
            foreach (Mesh mesh in ownedMeshes) Destroy(mesh);
        }
    }
}
