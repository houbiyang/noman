using System.Linq;
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
        private readonly Dictionary<string, GameObject> barracksViews = new Dictionary<string, GameObject>();
        private LineRenderer barracksSelectionRing;
        private SandboxBarracksData[] visibleBarracks = new SandboxBarracksData[0];
        private readonly HashSet<string> spawnFromBarracks = new HashSet<string>();
        public Transform FindBarracks(string id) => barracksViews.TryGetValue(id, out var camp) ? camp.transform : null;
        public void AnimateBarracksSpawns(IEnumerable<string> ids) { foreach (string id in ids) spawnFromBarracks.Add(id); }
        private GameObject routes;
        private GameObject grid;
        private GameObject facilities;
        private LineRenderer selectionRing;
        private LineRenderer selectionProjection;
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
            selectionProjection = CreateLine("SelectionProjection", unitRoot, new Vector3[2], .09f, new Color(.89f, .88f, .56f));
            selectionProjection.gameObject.SetActive(false);
            barracksSelectionRing = CreateLine("BarracksSelectionRing", unitRoot, Circle(Vector3.zero, 15), .35f, new Color(.89f, .88f, .56f));
            barracksSelectionRing.gameObject.SetActive(false);
        }

        /// <summary>按新的角色投影更新场景，移除不再允许显示的对象。</summary>
        /// <param name="projection">当前有效的角色投影。</param>
        public void ApplyProjection(SandboxProjection projection)
        {
            bool layoutChanged = ApplyBarracks(projection.barracks ?? new SandboxBarracksData[0]);
            var allowed = new HashSet<string>();
            int spawnIndex = 0;
            foreach (SandboxUnitData unit in projection.units)
            {
                allowed.Add(unit.id);
                if (!unitViews.TryGetValue(unit.id, out SandboxUnitView view))
                {
                    view = BuildUnit(unit);
                    unitViews.Add(unit.id, view);
                    if (spawnFromBarracks.Remove(unit.id))
                    {
                        var camp = System.Array.Find(visibleBarracks, item => item.id == unit.barracksId);
                        if (camp != null) view.SpawnAt(ArmyPosition(camp.x, SandboxArmyLayout.ExitZ(camp.z), unit.type), spawnIndex++ * .45f);
                    }
                }
                Vector3 position = ArmyPosition(unit.x, unit.z, unit.type);
                if (relocated.Remove(unit.id)) view.Relocate(position);
                else if (!view.HasTarget(position) || layoutChanged && unit.type != SandboxUnitType.Recon)
                {
                    if (!view.HasPosition || unit.type == SandboxUnitType.Recon) view.SetTarget(position);
                    else
                    {
                        List<Vector3> path = Navigation.FindPath(view.transform.position, position);
                        // An unreachable demo target must not produce a straight line through water or buildings.
                        if (path != null) view.SetTarget(position, path, layoutChanged);
                        else if (layoutChanged) view.HoldPosition();
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

        private bool ApplyBarracks(SandboxBarracksData[] camps)
        {
            bool changed = camps.Length != visibleBarracks.Length || System.Array.Exists(camps, camp => !System.Array.Exists(visibleBarracks, old => old.id == camp.id && old.x == camp.x && old.z == camp.z));
            visibleBarracks = camps;
            var allowed = new HashSet<string>(); var footprints = new List<Bounds>();
            foreach (var camp in camps)
            {
                allowed.Add(camp.id);
                if (!barracksViews.TryGetValue(camp.id, out var model))
                {
                    model = SandboxBarracksModel.Build(camp.type, camp.side == SandboxRole.Red ? redMaterial : blueMaterial, metalMaterial, darkMaterial, metalMaterial);
                    model.name = camp.id; model.transform.SetParent(unitRoot, false); barracksViews.Add(camp.id, model);
                    // One stable pick volume; decorative parts keep their colliders disabled.
                    var pick = model.AddComponent<BoxCollider>();
                    pick.center = new Vector3(0, 4, 0); pick.size = new Vector3(17, 8, 17);
                }
                Vector3 position = ArmyPosition(camp.x, camp.z, SandboxUnitType.Ground);
                model.transform.position = position;
                footprints.Add(new Bounds(position + new Vector3(0, 2, -1) * SandboxArmyLayout.ModelScale, new Vector3(14, 8, 9) * SandboxArmyLayout.ModelScale));
            }
            foreach (string id in new List<string>(barracksViews.Keys))
                if (!allowed.Contains(id)) { barracksViews[id].SetActive(false); ReleaseUnitObject(barracksViews[id]); barracksViews.Remove(id); }
            Navigation.SetBarracks(footprints);
            return changed;
        }

        // Test terrain and the complete ten-slot rally area even for aerial units: buildings need dry ground.
        public bool CanPlaceBarracks(SandboxUnitType type, SandboxRole side, float x, float z, out string message)
        {
            message = "请选择平坦空地，避开水面、建筑、单位和地图边界";
            if (!SandboxArmyLayout.Contains(x, z)) return false;
            string own = DemoSandboxViewSource.BarracksId(side, type);
            Vector3 center = ArmyPosition(x, z, SandboxUnitType.Ground);
            foreach (var camp in visibleBarracks)
                if (camp.id != own && SandboxArmyLayout.Overlaps(x, z, camp.x, camp.z)) return false;
            float extent = 10 * SandboxArmyLayout.ModelScale;
            for (float dx = -extent; dx <= extent; dx += 2.5f) for (float dz = -extent; dz <= extent; dz += 2.5f)
            {
                Vector3 p = ArmyPosition(x + dx / SandboxArmyLayout.MetersPerX, z + dz / SandboxArmyLayout.MetersPerZ, SandboxUnitType.Ground);
                if (!Navigation.IsTerrainWalkable(p) || Mathf.Abs(p.y - center.y) > 2.8f) return false;
            }
            foreach (var unit in unitViews.Values)
            {
                Vector3 delta = unit.transform.position - center;
                if (Mathf.Abs(delta.x) < extent && Mathf.Abs(delta.z) < extent) return false;
            }
            Vector3 exit = ArmyPosition(x, SandboxArmyLayout.ExitZ(z), SandboxUnitType.Ground);
            if (!Navigation.IsTerrainWalkable(exit)) return false;
            for (int i = 0; i < 10; i++)
            {
                Vector3 rally = ArmyPosition(DemoSandboxViewSource.RallyX(x, i, 10), DemoSandboxViewSource.RallyZ(z, i), SandboxUnitType.Ground);
                if (!Navigation.IsTerrainWalkable(rally) || type != SandboxUnitType.Recon && Navigation.FindPath(exit, rally) == null) return false;
            }
            message = ""; return true;
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
            foreach (var camp in barracksViews.Values) { camp.SetActive(false); ReleaseUnitObject(camp); }
            barracksViews.Clear(); visibleBarracks = new SandboxBarracksData[0]; spawnFromBarracks.Clear();
            Navigation.SetBarracks(new List<Bounds>());
            selectionRing.gameObject.SetActive(false);
            selectionProjection.gameObject.SetActive(false);
            SelectBarracks(null);
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
            UpdateSelectionProjection(target);
            if (target == null) return;
            Vector3 center = Surface(target.position.x, target.position.z) + Vector3.up * 0.3f;
            selectionRing.SetPositions(Circle(center, 2.1f * SandboxUnitView.ModelScale));
        }

        public string BarracksAt(Collider collider) => barracksViews.FirstOrDefault(entry => entry.Value.GetComponent<Collider>() == collider).Key;

        public void SelectBarracks(string id)
        {
            Transform target = id != null ? FindBarracks(id) : null;
            barracksSelectionRing.gameObject.SetActive(target != null);
            if (target != null)
                barracksSelectionRing.SetPositions(Circle(Surface(target.position.x, target.position.z) + Vector3.up * .5f, 15 * SandboxArmyLayout.ModelScale));
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

        /// <summary>Find the highest triangulated terrain/bridge surface under an animated model's world bounds.</summary>
        public static float HighestSurface(Bounds bounds)
        {
            float dx = Definition.width / (Definition.heightColumns - 1f);
            float dz = Definition.length / (Definition.heightRows - 1f);
            var xs = new List<float> { bounds.min.x, bounds.max.x };
            var zs = new List<float> { bounds.min.z, bounds.max.z };
            for (float x = Mathf.Ceil(bounds.min.x / dx) * dx; x < bounds.max.x; x += dx) xs.Add(x);
            for (float z = Mathf.Ceil(bounds.min.z / dz) * dz; z < bounds.max.z; z += dz) zs.Add(z);
            float height = float.NegativeInfinity;
            foreach (float x in xs) foreach (float z in zs) height = Mathf.Max(height, Surface(x, z).y);
            // A terrain triangle is planar. Its maximum inside a rectangle is at a rectangle
            // corner, a grid vertex/edge, or where its diagonal crosses the rectangle boundary.
            foreach (float x in new[] { bounds.min.x, bounds.max.x })
                for (int row = Mathf.FloorToInt(bounds.min.z / dz); row <= Mathf.FloorToInt(bounds.max.z / dz); row++)
                {
                    float z = (row + x / dx - Mathf.Floor(x / dx)) * dz;
                    if (z >= bounds.min.z && z <= bounds.max.z) height = Mathf.Max(height, Surface(x, z).y);
                }
            foreach (float z in new[] { bounds.min.z, bounds.max.z })
                for (int col = Mathf.FloorToInt(bounds.min.x / dx); col <= Mathf.FloorToInt(bounds.max.x / dx); col++)
                {
                    float x = (col + z / dz - Mathf.Floor(z / dz)) * dx;
                    if (x >= bounds.min.x && x <= bounds.max.x) height = Mathf.Max(height, Surface(x, z).y);
                }
            // Include bridge decks even when a narrow overlap falls between terrain sample points.
            foreach (SandboxMapRoad road in Definition.roads)
            {
                if (!road.isBridge) continue;
                var a = Array.Find(Definition.nodes, node => node.id == road.startId);
                var b = Array.Find(Definition.nodes, node => node.id == road.endId);
                Vector2 direction = new Vector2(b.x - a.x, b.z - a.z);
                float halfLength = direction.magnitude * .5f; direction.Normalize();
                Vector2 normal = new Vector2(-direction.y, direction.x);
                Vector2 offset = new Vector2(bounds.center.x - (a.x + b.x) * .5f, bounds.center.z - (a.z + b.z) * .5f);
                Vector2 extent = new Vector2(bounds.extents.x, bounds.extents.z);
                float half = road.width * .5f;
                if (Mathf.Abs(offset.x) <= extent.x + Mathf.Abs(direction.x) * halfLength + Mathf.Abs(normal.x) * half
                    && Mathf.Abs(offset.y) <= extent.y + Mathf.Abs(direction.y) * halfLength + Mathf.Abs(normal.y) * half
                    && Mathf.Abs(Vector2.Dot(offset, direction)) <= halfLength + extent.x * Mathf.Abs(direction.x) + extent.y * Mathf.Abs(direction.y)
                    && Mathf.Abs(Vector2.Dot(offset, normal)) <= half + extent.x * Mathf.Abs(normal.x) + extent.y * Mathf.Abs(normal.y))
                    height = Mathf.Max(height, road.surfaceHeight);
            }
            return height;
        }

        /// <summary>将保留的旧演示坐标投影到新的十二公里米制地图。</summary>
        /// <param name="positionX">原业务横坐标。</param>
        /// <param name="positionZ">原业务纵坐标。</param>
        /// <returns>实际显示地图上的平面坐标。</returns>
        private static Vector2 ProjectBusinessPosition(float positionX, float positionZ)
        {
            // 旧业务坐标范围为 120×80；按米制新地图等比例铺开，不改变存档字段。
            float mappedX = Mathf.Clamp(positionX * SandboxArmyLayout.MetersPerX, 0, Definition.width);
            float mappedZ = Mathf.Clamp(positionZ * SandboxArmyLayout.MetersPerZ, 0, Definition.length);
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
            return Surface(mapped.x, mapped.y) + Vector3.up * (type == SandboxUnitType.Recon ? SandboxUnitView.ReconAltitude : SandboxUnitView.GroundClearance);
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
            business = new Vector2(point.x / SandboxArmyLayout.MetersPerX, point.z / SandboxArmyLayout.MetersPerZ);
            return business.x >= 0 && business.x <= 120 && business.y >= 0 && business.y <= 80;
        }
        private void LateUpdate()
        {
            Transform target = FindUnit(selectedUnit);
            selectionRing.gameObject.SetActive(target != null);
            UpdateSelectionProjection(target);
            if (target != null) selectionRing.SetPositions(Circle(Surface(target.position.x, target.position.z) + Vector3.up * 0.3f, 2.5f * SandboxUnitView.ModelScale));
        }

        private void UpdateSelectionProjection(Transform target)
        {
            var view = target != null ? target.GetComponent<SandboxUnitView>() : null;
            selectionProjection.gameObject.SetActive(view != null && view.IsAirborne);
            if (view == null || !view.IsAirborne) return;
            Vector3 ground = Surface(target.position.x, target.position.z) + Vector3.up * .3f;
            selectionProjection.SetPosition(0, ground);
            selectionProjection.SetPosition(1, new Vector3(ground.x, view.VisualBounds.min.y, ground.z));
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
