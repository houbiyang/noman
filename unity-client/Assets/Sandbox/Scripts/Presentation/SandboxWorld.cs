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
                Vector2 displayPosition = ProjectBusinessPosition(unit.x, unit.z);
                Vector3 position = Surface(displayPosition.x, displayPosition.y);
                view.transform.position = position + Vector3.up * (unit.type == SandboxUnitType.Recon ? 5 : 0.6f);
            }
            foreach (string unitId in new List<string>(unitViews.Keys))
            {
                if (allowed.Contains(unitId)) continue;
                // 立即禁用命中与渲染，再在帧末销毁，避免角色切换时泄露旧对象。
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
                view.gameObject.SetActive(false);
                Destroy(view.gameObject);
            }
            unitViews.Clear();
            selectionRing.gameObject.SetActive(false);
        }

        /// <summary>查找当前允许显示的单位变换。</summary>
        /// <param name="unitId">单位稳定标识。</param>
        /// <returns>当前显示对象；不存在时返回空。</returns>
        public Transform FindUnit(string unitId) => unitId != null && unitViews.TryGetValue(unitId, out SandboxUnitView view) ? view.transform : null;

        /// <summary>在选中单位的地面位置显示观察标记。</summary>
        /// <param name="unitId">当前选中单位。</param>
        public void Select(string unitId)
        {
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
            SandboxUnitView view = root.AddComponent<SandboxUnitView>();
            view.Initialize(unit.id);
            Material color = unit.side == SandboxRole.Red ? redMaterial : blueMaterial;
            if (unit.type == SandboxUnitType.Recon)
            {
                CreateBox("DroneBody", root.transform, Vector3.zero, new Vector3(1.8f, 0.55f, 1.1f), color);
                CreateBox("CrossArm", root.transform, Vector3.zero, new Vector3(3.8f, 0.15f, 0.2f), metalMaterial);
                CreateBox("LongArm", root.transform, Vector3.zero, new Vector3(0.2f, 0.15f, 3.4f), metalMaterial);
                foreach (Vector3 offset in new[] { Vector3.left * 1.9f, Vector3.right * 1.9f, Vector3.forward * 1.7f, Vector3.back * 1.7f })
                    CreateCylinder("Rotor", root.transform, offset, new Vector3(1.35f, 0.055f, 1.35f), darkMaterial);
            }
            else
            {
                CreateBox("Chassis", root.transform, Vector3.zero, new Vector3(2.9f, 0.8f, 1.7f), color);
                CreateBox("Cabin", root.transform, new Vector3(0.25f, 0.7f, 0), new Vector3(1.65f, 0.8f, 1.4f), unit.type == SandboxUnitType.Support ? metalMaterial : color);
                foreach (float positionX in new[] { -0.9f, 0.9f })
                    foreach (float positionZ in new[] { -0.9f, 0.9f })
                        CreatePrimitive("Wheel", PrimitiveType.Sphere, root.transform, new Vector3(positionX, -0.3f, positionZ), Vector3.one * 0.7f, darkMaterial, false);
                if (unit.type == SandboxUnitType.Support)
                    CreateBox("SupplyCrate", root.transform, new Vector3(-0.55f, 0.75f, 0), new Vector3(1.15f, 0.9f, 1.25f), color);
            }
            BoxCollider selectionCollider = root.AddComponent<BoxCollider>();
            selectionCollider.size = new Vector3(4.2f, 2.8f, 3.6f);
            return view;
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
