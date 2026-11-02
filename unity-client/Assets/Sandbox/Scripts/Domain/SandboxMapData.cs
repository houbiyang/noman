using System;
using System.Collections.Generic;
using System.Linq;

namespace Qiongtu.Sandbox
{
    /// <summary>公开地图节点或标记的稳定身份及显示坐标。</summary>
    [Serializable]
    public sealed class SandboxMapPoint
    {
        public string id;
        public string name;
        public string kind;
        public string partnerId;
        public float x;
        public float z;
    }

    /// <summary>道路连接数据；不替代后端的通行规则或寻路结论。</summary>
    [Serializable]
    public sealed class SandboxMapRoad
    {
        public string id;
        public string startId;
        public string endId;
        public bool isBridge;
        public float width;
        public float surfaceHeight;
    }

    /// <summary>部署区的平面范围，当前数值仅作为固定虚构场景配置。</summary>
    [Serializable]
    public sealed class SandboxDeploymentZone
    {
        public string id;
        public string side;
        public float minX;
        public float maxX;
        public float minZ;
        public float maxZ;
    }

    /// <summary>地图网格共用的基础颜色定义。</summary>
    [Serializable]
    public sealed class SandboxMapMaterial
    {
        public string id;
        public float red;
        public float green;
        public float blue;
    }

    /// <summary>Blender 建筑的显示放置参数；损坏程度只影响模型，不参与业务裁决。</summary>
    [Serializable]
    public sealed class SandboxMapBuilding
    {
        public string id;
        public string layer;
        public string model;
        public int damage;
        public float x;
        public float y;
        public float z;
        public float yaw;
        public float scaleX;
        public float scaleY;
        public float scaleZ;
    }

    /// <summary>版本化固定地图及其显示高度采样。</summary>
    [Serializable]
    public sealed class SandboxMapData
    {
        public int schemaVersion;
        public string mapId;
        public string name;
        public string contentHash;
        public string coordinateUnit;
        public float width;
        public float length;
        public float displayScale;
        public int heightColumns;
        public int heightRows;
        public float[] heights;
        public SandboxMapPoint[] nodes;
        public SandboxMapRoad[] roads;
        public SandboxMapPoint[] points;
        public SandboxMapMaterial[] materials;
        public SandboxDeploymentZone[] deploymentZones;
        public SandboxMapBuilding[] buildings;

        /// <summary>从同源高度采样中插值三维显示高度。</summary>
        /// <param name="positionX">平面横坐标。</param>
        /// <param name="positionZ">平面纵坐标。</param>
        /// <returns>用于贴地显示的高度，不参与业务结算。</returns>
        public float SampleHeight(float positionX, float positionZ)
        {
            float column = Math.Max(0, Math.Min(width, positionX)) / width * (heightColumns - 1);
            float row = Math.Max(0, Math.Min(length, positionZ)) / length * (heightRows - 1);
            int lowerColumn = Math.Min((int)column, heightColumns - 2);
            int lowerRow = Math.Min((int)row, heightRows - 2);
            float horizontal = column - lowerColumn;
            float vertical = row - lowerRow;
            int offset = lowerRow * heightColumns + lowerColumn;
            float lower = heights[offset] * (1 - horizontal) + heights[offset + 1] * horizontal;
            float upper = heights[offset + heightColumns] * (1 - horizontal) + heights[offset + heightColumns + 1] * horizontal;
            return lower * (1 - vertical) + upper * vertical;
        }

        /// <summary>为显示对象提供桥面或地表高度，不决定路径是否合法。</summary>
        /// <param name="positionX">平面横坐标。</param>
        /// <param name="positionZ">平面纵坐标。</param>
        /// <returns>同源桥面或地表的显示高度。</returns>
        public float SampleSurfaceHeight(float positionX, float positionZ)
        {
            foreach (SandboxMapRoad road in roads.Where(candidate => candidate.isBridge))
            {
                SandboxMapPoint start = nodes.First(node => node.id == road.startId);
                SandboxMapPoint end = nodes.First(node => node.id == road.endId);
                float directionX = end.x - start.x;
                float directionZ = end.z - start.z;
                float parameter = ((positionX - start.x) * directionX + (positionZ - start.z) * directionZ) / (directionX * directionX + directionZ * directionZ);
                if (parameter < 0 || parameter > 1) continue;
                float offsetX = positionX - start.x - parameter * directionX;
                float offsetZ = positionZ - start.z - parameter * directionZ;
                if (offsetX * offsetX + offsetZ * offsetZ <= road.width * road.width / 4) return road.surfaceHeight;
            }
            return SampleHeight(positionX, positionZ);
        }

        /// <summary>校验地图身份、节点引用和完整道路连通性。</summary>
        /// <returns>空字符串表示字段与拓扑检查通过，不代表四任务业务验收。</returns>
        public string Validate()
        {
            if (schemaVersion != 1 || mapId != "qinglan-valley-v5" || string.IsNullOrWhiteSpace(name) || contentHash == null || contentHash.Length != 64) return "地图身份或版本无效";
            if (width != 360 || length != 240 || displayScale != 3 || coordinateUnit != "scene-unit" || heightColumns < 2 || heightRows < 2
                || heights == null || heights.Length != heightColumns * heightRows || heights.Any(value => float.IsNaN(value) || float.IsInfinity(value))) return "地图尺寸或高度数据无效";
            if (nodes == null || nodes.Length == 0 || roads == null || points == null || materials == null || deploymentZones == null) return "地图对象不完整";
            var nodeIds = new HashSet<string>();
            foreach (SandboxMapPoint node in nodes)
                if (!ValidPoint(node) || !nodeIds.Add(node.id)) return "道路节点身份或坐标无效";
            var roadIds = new HashSet<string>();
            var adjacency = nodes.ToDictionary(node => node.id, node => new List<string>());
            foreach (SandboxMapRoad road in roads)
            {
                if (road == null || string.IsNullOrWhiteSpace(road.id) || !roadIds.Add(road.id) || road.startId == null || road.endId == null
                    || !nodeIds.Contains(road.startId) || !nodeIds.Contains(road.endId) || road.startId == road.endId
                    || !Finite(road.width) || road.width <= 0 || !Finite(road.surfaceHeight)) return "道路引用或身份无效";
                adjacency[road.startId].Add(road.endId);
                adjacency[road.endId].Add(road.startId);
            }
            var visited = new HashSet<string>();
            var pending = new Queue<string>();
            pending.Enqueue(nodes[0].id);
            while (pending.Count > 0)
            {
                string current = pending.Dequeue();
                if (!visited.Add(current)) continue;
                foreach (string neighbor in adjacency[current]) pending.Enqueue(neighbor);
            }
            if (visited.Count != nodes.Length || roads.Count(road => road.isBridge) != 3) return "道路未全部连通或缺少三处桥梁";
            var pointIds = new HashSet<string>();
            if (points.Any(point => !ValidPoint(point) || !pointIds.Add(point.id))) return "地图标记身份或坐标无效";
            if (materials.Length == 0 || materials.Any(material => material == null || string.IsNullOrWhiteSpace(material.id)
                || !ValidColor(material.red) || !ValidColor(material.green) || !ValidColor(material.blue)) || materials.Select(material => material.id).Distinct().Count() != materials.Length) return "地图材质无效";
            if (deploymentZones.Length != 2 || deploymentZones.Any(zone => zone == null || !Finite(zone.minX) || !Finite(zone.maxX) || !Finite(zone.minZ) || !Finite(zone.maxZ) || zone.minX < 0 || zone.maxX > width || zone.minZ < 0 || zone.maxZ > length
                || zone.minX >= zone.maxX || zone.minZ >= zone.maxZ) || deploymentZones.Select(zone => zone.side).Distinct().Count() != 2) return "部署区域无效";
            var buildingIds = new HashSet<string>();
            if (buildings == null || buildings.Length == 0 || buildings.Any(building => building == null
                || string.IsNullOrWhiteSpace(building.id) || !buildingIds.Add(building.id)
                || !new[] { "settlement", "village", "central_village" }.Contains(building.layer)
                || building.damage < 0 || building.damage > 3 || building.model != "House_" + building.damage
                || !Finite(building.x) || !Finite(building.y) || !Finite(building.z) || !Finite(building.yaw)
                || building.x < 0 || building.x > width || building.z < 0 || building.z > length
                || !Finite(building.scaleX) || !Finite(building.scaleY) || !Finite(building.scaleZ)
                || building.scaleX <= 0 || building.scaleY <= 0 || building.scaleZ <= 0)) return "战损建筑模型或放置参数无效";
            return string.Empty;
        }

        /// <summary>校验公开对象坐标，不将未知或无限值当作零。</summary>
        private bool ValidPoint(SandboxMapPoint point) => point != null && !string.IsNullOrWhiteSpace(point.id) && !string.IsNullOrWhiteSpace(point.name)
            && !float.IsNaN(point.x) && !float.IsInfinity(point.x) && !float.IsNaN(point.z) && !float.IsInfinity(point.z)
            && point.x >= 0 && point.x <= width && point.z >= 0 && point.z <= length;

        /// <summary>核对空间量值是否有限。</summary>
        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        /// <summary>核对有限的归一化颜色分量。</summary>
        private static bool ValidColor(float value) => !float.IsNaN(value) && value >= 0 && value <= 1;
    }

    /// <summary>同源导出的一个材质与图层网格分块。</summary>
    [Serializable]
    public sealed class SandboxMapMeshData
    {
        public string name;
        public string layer;
        public string material;
        public float[] vertices;
        public int[] triangles;
    }

    /// <summary>与地图定义绑定相同版本和哈希的静态网格。</summary>
    [Serializable]
    public sealed class SandboxMapGeometryData
    {
        public int schemaVersion;
        public string mapId;
        public string contentHash;
        public SandboxMapMeshData[] parts;

        /// <summary>拒绝混用的版本、非法索引和非有限网格坐标。</summary>
        /// <param name="definition">对应布局定义。</param>
        /// <returns>空字符串表示静态网格字段有效。</returns>
        public string Validate(SandboxMapData definition)
        {
            if (definition == null || schemaVersion != 1 || mapId != definition.mapId || contentHash != definition.contentHash || parts == null || parts.Length == 0) return "地图网格与布局版本不匹配";
            var names = new HashSet<string>();
            foreach (SandboxMapMeshData part in parts)
            {
                if (part == null || string.IsNullOrWhiteSpace(part.name) || !names.Add(part.name) || !new[] { "terrain", "water", "routes", "facilities", "markers", "deployment", "settlement", "environment", "grid", "riverbanks", "farmland", "ditches", "village", "logistics", "ponds", "scrub", "central_village", "landscape", "damage" }.Contains(part.layer)
                    || !definition.materials.Any(material => material.id == part.material) || part.vertices == null || part.vertices.Length == 0 || part.vertices.Length % 3 != 0
                    || part.vertices.Length / 3 > 65535 || part.triangles == null || part.triangles.Length == 0 || part.triangles.Length % 3 != 0
                    || part.vertices.Any(value => float.IsNaN(value) || float.IsInfinity(value)) || part.triangles.Any(index => index < 0 || index >= part.vertices.Length / 3)) return "地图分块坐标、图层或三角形索引无效";
            }
            return string.Empty;
        }
    }
}
