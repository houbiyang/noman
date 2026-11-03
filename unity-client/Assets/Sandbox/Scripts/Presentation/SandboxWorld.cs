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
        public SandboxMapPoint[] MapPoints => Definition.points;
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
            unitRoot = NewGroup("ProjectedUnits").transform;
            selectionRing = CreateLine("SelectionRing", unitRoot, Circle(Vector3.zero, 2.1f), 0.1f, new Color(0.89f, 0.88f, 0.56f));
            selectionRing.gameObject.SetActive(false);
        }

        /// <summary>按新的角色投影更新场景，移除不再允许显示的对象。</summary>
        /// <param name="projection">当前有效的角色投影。</param>
        public void ApplyProjection(SandboxProjection projection)
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
                if (relocated.Remove(unit.id)) view.Relocate(position);
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
                unitViews[unitId].ReleaseRoute();
                unitViews[unitId].gameObject.SetActive(false);
                ReleaseUnitObject(unitViews[unitId].gameObject);
                unitViews.Remove(unitId);
            }
        }

        /// <summary>清除旧角色的对象引用及选中标记。</summary>
        public void ClearProjection()
        {
            foreach (SandboxUnitView view in unitViews.Values)
            {
                view.ReleaseRoute();
                view.gameObject.SetActive(false);
                ReleaseUnitObject(view.gameObject);
            }
            unitViews.Clear();
            selectionRing.gameObject.SetActive(false);
            selectedUnit = null; relocated.Clear();
        }

        private static void ReleaseUnitObject(GameObject unit)
        {
#if UNITY_EDITOR
            if (!Application.isPlaying) { DestroyImmediate(unit); return; }
#endif
            Destroy(unit);
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
                foreach (string groupName in new[] { "markers", "settlement", "deployment", "village", "logistics", "central_village" })
                {
                    Transform group = mapRoot.transform.Find(groupName);
                    if (group != null) group.gameObject.SetActive(visible);
                }
            }
        }

        /// <summary>将演示平面坐标映射至三维地形表面。</summary>
        /// <param name="positionX">横向业务坐标。</param>
        /// <param name="positionZ">纵向业务坐标。</param>
        /// <returns>仅用于显示的三维位置。</returns>
        public static Vector3 Surface(float positionX, float positionZ) => new Vector3(positionX, Definition.SampleSurfaceHeight(positionX, positionZ), positionZ);

        /// <summary>将旧演示与存档坐标投影到非对称自然河谷，与生成器共用相同公式。</summary>
        /// <param name="positionX">原业务横坐标。</param>
        /// <param name="positionZ">原业务纵坐标。</param>
        /// <returns>实际显示地图上的平面坐标。</returns>
        private static Vector2 ProjectBusinessPosition(float positionX, float positionZ)
        {
            float mappedX = positionX + Mathf.Sin(Mathf.PI * positionX / 120) *
                (6 * Mathf.Sin(positionZ * 0.065f + 0.8f) + 2 * Mathf.Sin(positionX * 0.15f + positionZ * 0.12f));
            float mappedZ = positionZ + Mathf.Sin(Mathf.PI * positionZ / 80) * 4 * Mathf.Sin(positionX * 0.055f + 0.4f);
            return new Vector2(mappedX, mappedZ) * Definition.displayScale;
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
        public bool TryBusinessPosition(Vector3 point, out Vector2 business)
        {
            business = new Vector2(point.x, point.z) / Definition.displayScale;
            for (int i = 0; i < 12; i++)
            {
                Vector2 actual = ProjectBusinessPosition(business.x, business.y), error = actual - new Vector2(point.x, point.z);
                if (error.sqrMagnitude < 0.0001f) break;
                Vector2 dx = (ProjectBusinessPosition(business.x + 0.01f, business.y) - actual) / 0.01f;
                Vector2 dz = (ProjectBusinessPosition(business.x, business.y + 0.01f) - actual) / 0.01f;
                float determinant = dx.x * dz.y - dx.y * dz.x;
                if (Mathf.Abs(determinant) < 0.001f) return false;
                business -= new Vector2((error.x * dz.y - error.y * dz.x) / determinant, (dx.x * error.y - dx.y * error.x) / determinant);
            }
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
