using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>前后端共用的五据点叠加层；不改变底图网格及旧任务身份。</summary>
    [Serializable]
    public sealed class SandboxCaptureLayout
    {
        public int schemaVersion;
        public string layoutId, version, baseMapId, baseMapContentHash, coordinateUnit;
        public SandboxMapPoint[] points;

        public static string ContentHash
        {
            get
            {
                TextAsset asset = Resources.Load<TextAsset>(SandboxMapFactory.ResourceRoot + "capture-points");
                if (asset == null) throw new InvalidOperationException("缺少五据点配置");
                using (var hash = System.Security.Cryptography.SHA256.Create())
                    return BitConverter.ToString(hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes(asset.text))).Replace("-", "").ToLowerInvariant();
            }
        }

        public static SandboxCaptureLayout Load(SandboxMapData definition)
        {
            TextAsset asset = Resources.Load<TextAsset>(SandboxMapFactory.ResourceRoot + "capture-points");
            if (asset == null) throw new InvalidOperationException("缺少五据点配置");
            var layout = JsonUtility.FromJson<SandboxCaptureLayout>(asset.text);
            if (layout == null || layout.schemaVersion != 1 || layout.baseMapId != definition.mapId
                || layout.baseMapContentHash != definition.contentHash || layout.coordinateUnit != "meter"
                || layout.points == null || layout.points.Length != 5
                || !layout.points.Select(p => p.id).OrderBy(id => id).SequenceEqual(new[] { "P-01", "P-02", "P-03", "P-04", "P-05" }))
                throw new InvalidOperationException("五据点配置与底图身份不一致");
            foreach (var p in layout.points)
            {
                if (float.IsNaN(p.x) || float.IsNaN(p.z) || p.x < 0 || p.x > definition.width || p.z < 0 || p.z > definition.length
                    || p.radius != 250 || p.kind != "capture" || p.initialOwner != "neutral")
                    throw new InvalidOperationException("据点坐标、半径或初始归属无效：" + p.id);
                bool bridge = p.support == "bridge_deck";
                if (bridge ? p.areaPolicy != "circle_intersect_bridge_deck" || !definition.roads.Any(r => r.id == p.bridgeRoadId && r.isBridge)
                    : p.support != "ground" || p.areaPolicy != "circle" || !string.IsNullOrEmpty(p.bridgeRoadId))
                    throw new InvalidOperationException("据点支撑面无效：" + p.id);
            }
            return layout;
        }

        /// <summary>真实显示边界：地面圆域；桥面圆域裁切至有限桥面矩形，始终使用桥面高程。</summary>
        public static Vector3[] Boundary(SandboxMapData definition, SandboxMapPoint point)
        {
            var polygon = new List<Vector2>();
            for (int i = 0; i < 96; i++)
            {
                float angle = i * Mathf.PI * 2 / 96;
                polygon.Add(new Vector2(point.x + Mathf.Cos(angle) * point.radius, point.z + Mathf.Sin(angle) * point.radius));
            }
            SandboxMapRoad bridge = point.support == "bridge_deck" ? definition.roads.Single(r => r.id == point.bridgeRoadId && r.isBridge) : null;
            if (bridge != null)
            {
                var a = definition.nodes.Single(n => n.id == bridge.startId);
                var b = definition.nodes.Single(n => n.id == bridge.endId);
                var start = new Vector2(a.x, a.z);
                var direction = new Vector2(b.x - a.x, b.z - a.z);
                float length = direction.magnitude; direction /= length;
                var normal = new Vector2(-direction.y, direction.x);
                polygon = Clip(polygon, p => Vector2.Dot(p - start, direction));
                polygon = Clip(polygon, p => length - Vector2.Dot(p - start, direction));
                polygon = Clip(polygon, p => bridge.width / 2 - Vector2.Dot(p - start, normal));
                polygon = Clip(polygon, p => bridge.width / 2 + Vector2.Dot(p - start, normal));
            }
            if (polygon.Count < 3) throw new InvalidOperationException("据点没有有效支撑面：" + point.id);
            polygon.Add(polygon[0]);
            return polygon.Select(p => new Vector3(p.x, (bridge != null ? bridge.surfaceHeight : definition.SampleSurfaceHeight(p.x, p.y)) + 6, p.y)).ToArray();
        }

        private static List<Vector2> Clip(List<Vector2> input, Func<Vector2, float> signedDistance)
        {
            var output = new List<Vector2>();
            if (input.Count == 0) return output;
            Vector2 previous = input[input.Count - 1]; float previousDistance = signedDistance(previous);
            foreach (var current in input)
            {
                float distance = signedDistance(current);
                if ((distance >= 0) != (previousDistance >= 0))
                    output.Add(Vector2.Lerp(previous, current, previousDistance / (previousDistance - distance)));
                if (distance >= 0) output.Add(current);
                previous = current; previousDistance = distance;
            }
            return output;
        }
    }
}
