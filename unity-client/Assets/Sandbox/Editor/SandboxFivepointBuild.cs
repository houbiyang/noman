using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>使用现有场景验证并构建规则客户端，不重新生成地图、材质或场景。</summary>
    public static class SandboxFivepointBuild
    {
        // 摘要锁定原备份与修复结果生成的594点补丁，不能扩展到同片段其他索引。
        private const string BridgePatchSha256 = "41620810ec0a9cbbafffa7afeea06d76f7a3d9503880a3157d90589afe987a52";
        private static readonly HashSet<string> BridgeApproachParts = new HashSet<string>
        {
            "routes_Asphalt_03_01_0", "routes_Marking_03_01_0",
            "routes_Asphalt_02_02_0", "routes_Marking_02_02_0",
            "routes_Asphalt_03_02_0", "routes_Marking_03_02_0",
            "routes_Asphalt_02_04_0", "routes_Marking_02_04_0",
            "routes_Asphalt_03_04_0", "routes_Marking_03_04_0"
        };

        [Serializable]
        private sealed class BridgePatch
        {
            public string schemaVersion = "";
            public string contentHash = "";
            public BridgePartPatch[] parts = Array.Empty<BridgePartPatch>();
        }

        [Serializable]
        private sealed class BridgePartPatch
        {
            public string name;
            public BridgeVertexPatch[] vertices;
        }

        [Serializable]
        private sealed class BridgeVertexPatch
        {
            public int index;
            public float originalY, repairedY, x, z;
        }

        private sealed class PendingMesh
        {
            public Mesh Mesh;
            public Vector3[] Vertices;
            public int Count;
        }

        /// <summary>逐点预检并准备内存副本；任何既有编辑冲突均在写入原生资产前拒绝。</summary>
        private static PendingMesh PrepareMesh(SandboxMapMeshData part, Mesh mesh, BridgePartPatch patch)
        {
            if (mesh == null || mesh.vertexCount * 3 != part.vertices.Length || !mesh.triangles.SequenceEqual(part.triangles))
                throw new InvalidOperationException("接坡源网格身份或拓扑不符：" + part.name);
            Dictionary<int, BridgeVertexPatch> authorized = new Dictionary<int, BridgeVertexPatch>();
            foreach (BridgeVertexPatch vertex in patch?.vertices ?? Array.Empty<BridgeVertexPatch>())
            {
                if (vertex == null || vertex.index < 0 || vertex.index >= mesh.vertexCount || authorized.ContainsKey(vertex.index)
                    || float.IsNaN(vertex.originalY) || float.IsInfinity(vertex.originalY)
                    || float.IsNaN(vertex.repairedY) || float.IsInfinity(vertex.repairedY) || vertex.originalY == vertex.repairedY
                    || part.vertices[vertex.index * 3] != vertex.x || part.vertices[vertex.index * 3 + 2] != vertex.z
                    || part.vertices[vertex.index * 3 + 1] != vertex.repairedY)
                    throw new InvalidOperationException("接坡顶点补丁与发布源不符：" + part.name);
                authorized.Add(vertex.index, vertex);
            }
            Vector3[] vertices = mesh.vertices;
            int count = 0;
            for (int index = 0; index < vertices.Length; index++)
            {
                if (vertices[index].x != part.vertices[index * 3] || vertices[index].z != part.vertices[index * 3 + 2])
                    throw new InvalidOperationException("接坡不能移动道路平面坐标：" + part.name);
                float expected = part.vertices[index * 3 + 1];
                if (vertices[index].y == expected) continue;
                if (!authorized.TryGetValue(index, out BridgeVertexPatch vertex)
                    || vertices[index].y != vertex.originalY)
                    throw new InvalidOperationException("已有高程编辑超出授权接坡顶点：" + part.name + " / " + index);
                vertices[index].y = vertex.repairedY;
                count++;
            }
            return new PendingMesh { Mesh = mesh, Vertices = vertices, Count = count };
        }

        /// <summary>先预检全部原生网格，再仅同步594个授权索引；重入保留已修复高程。</summary>
        public static void ApplyBridgeApproaches()
        {
            AssetDatabase.Refresh();
            SandboxMapData definition = SandboxMapFactory.LoadDefinition();
            SandboxMapGeometryData geometry = SandboxMapFactory.LoadGeometry(definition);
            string patchPath = Path.Combine(Application.dataPath, "Sandbox/Maps/QinglanValley/bridge-approaches.patch.json");
            byte[] patchBytes = File.ReadAllBytes(patchPath);
            using (SHA256 algorithm = SHA256.Create())
            {
                string actual = BitConverter.ToString(algorithm.ComputeHash(patchBytes)).Replace("-", "").ToLowerInvariant();
                if (actual != BridgePatchSha256) throw new InvalidOperationException("接坡授权顶点补丁摘要不符");
            }
            BridgePatch patch = JsonUtility.FromJson<BridgePatch>(Encoding.UTF8.GetString(patchBytes));
            if (patch == null || patch.schemaVersion != "bridge-approaches-patch-v1" || patch.contentHash != definition.contentHash
                || patch.parts == null || patch.parts.Any(item => item == null || item.vertices == null)
                || patch.parts.Select(item => item.name).Distinct().Count() != BridgeApproachParts.Count
                || patch.parts.Length != BridgeApproachParts.Count
                || !BridgeApproachParts.SetEquals(patch.parts.Select(item => item.name))
                || patch.parts.Sum(item => item.vertices.Length) != 594)
                throw new InvalidOperationException("接坡补丁身份或594个授权顶点不符");
            Dictionary<string, BridgePartPatch> patches = patch.parts.ToDictionary(item => item.name);
            HashSet<string> seen = new HashSet<string>();
            List<PendingMesh> updates = new List<PendingMesh>();
            int changedParts = 0, changedVertices = 0;
            foreach (SandboxMapMeshData part in geometry.parts.Where(item => item.layer == "routes"
                && (item.material == "Asphalt" || item.material == "Marking")))
            {
                string path = "Assets/Sandbox/Maps/QinglanValley/Meshes/" + part.name + ".asset";
                Mesh mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
                patches.TryGetValue(part.name, out BridgePartPatch partPatch);
                if (partPatch != null) seen.Add(part.name);
                PendingMesh update = PrepareMesh(part, mesh, partPatch);
                if (update.Count > 0) updates.Add(update);
            }
            if (!seen.SetEquals(BridgeApproachParts))
                throw new InvalidOperationException("发布几何缺少授权接坡片段，未写入网格");
            foreach (PendingMesh update in updates)
            {
                update.Mesh.vertices = update.Vertices;
                update.Mesh.RecalculateNormals(); update.Mesh.RecalculateTangents(); update.Mesh.RecalculateBounds();
                EditorUtility.SetDirty(update.Mesh);
                AssetDatabase.SaveAssetIfDirty(update.Mesh);
                changedParts++; changedVertices += update.Count;
            }
            Debug.Log("[桥头接坡] 同步原生路面网格 " + changedParts + " 块、" + changedVertices + " 个高程；平面与拓扑保持。");
        }

        /// <summary>以临时内存网格验证逐顶点授权，检查过程不创建或保存场景资产。</summary>
        private static void VerifyBridgePatchProtection()
        {
            SandboxMapMeshData source = new SandboxMapMeshData { name = "保护测试",
                vertices = new float[] { 0, 1, 0, 1, 2, 0, 0, 1, 1 }, triangles = new[] { 0, 1, 2 } };
            BridgePartPatch patch = new BridgePartPatch { name = source.name,
                vertices = new[] { new BridgeVertexPatch { index = 1, originalY = 1, repairedY = 2, x = 1, z = 0 } } };
            Mesh mesh = new Mesh { vertices = new[] { new Vector3(0, 1, 0), new Vector3(1, 1, 0), new Vector3(0, 1, 1) },
                triangles = source.triangles };
            try
            {
                PendingMesh prepared = PrepareMesh(source, mesh, patch);
                if (prepared.Count != 1 || prepared.Vertices[1].y != 2 || mesh.vertices[1].y != 1)
                    throw new InvalidOperationException("接坡预检提前修改了原生网格");
                Vector3[] edited = mesh.vertices;
                edited[0].y = 99;
                mesh.vertices = edited;
                ExpectBridgeRejection(() => PrepareMesh(source, mesh, patch));
                edited[0].y = 1;
                edited[1].y = 99;
                mesh.vertices = edited;
                ExpectBridgeRejection(() => PrepareMesh(source, mesh, patch));
            }
            finally { UnityEngine.Object.DestroyImmediate(mesh); }
            Debug.Log("[桥头接坡] 授权顶点、已有编辑保护和只读预检通过");
        }

        /// <summary>确认既有非授权高程编辑被拒绝，不通过吞掉其他异常获得通过结果。</summary>
        private static void ExpectBridgeRejection(Action action)
        {
            bool rejected = false;
            try { action(); }
            catch (InvalidOperationException) { rejected = true; }
            if (!rejected) throw new InvalidOperationException("接坡错误接受了既有高程编辑");
        }

        /// <summary>通过原生序列化验证五点字段和旧模式缺失字段，不读取实际席位凭据。</summary>
        public static void VerifyContracts()
        {
            const string sample = "{\"role\":\"red\",\"decisions\":{},\"fivepoint\":{\"rule_version\":\"qinglan-fivepoint-custom-v1.1\",\"step\":61,\"second\":305,\"own_score\":{\"total\":\"833/60\"},\"own_resources\":{\"energy\":\"99/2\"},\"points\":[{\"point_id\":\"A\",\"xy\":[100,200],\"owner\":null}]}}";
            SandboxServerView view = JsonUtility.FromJson<SandboxServerView>(sample);
            SandboxServerProjection.NormalizeMissingPlan(view, sample);
            if (view.fivepoint.step != 61 || view.fivepoint.points.Length != 1 || view.fivepoint.points[0].xy[1] != 200
                || SandboxFivepointView.Display(view.fivepoint.own_score.total) != "13.88" || view.decisions.own_plan != null)
                throw new InvalidOperationException("五点原生JSON字段或分数解析失败");
            const string legacy = "{\"role\":\"host\",\"decisions\":{}}";
            view = JsonUtility.FromJson<SandboxServerView>(legacy);
            SandboxServerProjection.NormalizeMissingPlan(view, legacy);
            if (view.fivepoint != null) throw new InvalidOperationException("旧模式被错误识别为五点模式");
            VerifyBridgePatchProtection();
            Debug.Log("[五点规则] 原生序列化与旧模式隔离验证通过");
        }

        /// <summary>把当前已保存场景构建到隔离目录，保留原有发布包和全部地图资产。</summary>
        public static void BuildWeb()
        {
            ApplyBridgeApproaches();
            VerifyContracts();
            const string scene = "Assets/Scenes/Sandbox.unity";
            if (!File.Exists(scene)) throw new InvalidOperationException("缺少当前场景，未生成替代地图");
            string output = Path.GetFullPath(Path.Combine(Application.dataPath, "../../.local/rules-client-20261114/Web"));
            Directory.CreateDirectory(output);
            BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { scene }, locationPathName = output, target = BuildTarget.WebGL, options = BuildOptions.None
            });
            if (report.summary.result != BuildResult.Succeeded)
                throw new InvalidOperationException("五点客户端构建失败：" + report.summary.result);
            Debug.Log("[五点规则] 已用现有场景构建客户端：" + output);
        }
    }
}
