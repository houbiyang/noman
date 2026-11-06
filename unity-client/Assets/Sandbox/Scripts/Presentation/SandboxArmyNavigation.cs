using System;
using System.Collections.Generic;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>Local visual navigation: rasterized water/building obstacles, bridges and a bounded A* grid.</summary>
    public sealed class SandboxArmyNavigation
    {
        private const float Cell = 3;
        private readonly int columns, rows;
        private readonly bool[] blocked;
        private List<Bounds> barracks = new List<Bounds>();
        public void SetBarracks(List<Bounds> bounds) => barracks = bounds;
        private bool IsBlocked(int index)
        {
            if (blocked[index]) return true;
            Vector3 p = new Vector3((index % columns + .5f) * Cell, 0, (index / columns + .5f) * Cell);
            foreach (var bounds in barracks)
                if (Mathf.Abs(p.x - bounds.center.x) <= bounds.extents.x && Mathf.Abs(p.z - bounds.center.z) <= bounds.extents.z) return true;
            return false;
        }
        public bool IsTerrainWalkable(Vector3 point) { int index = Index(point); return index >= 0 && !blocked[index]; }
        private readonly SandboxMapData definition;
        public SandboxArmyNavigation(GameObject map, SandboxMapData data)
        {
            definition = data; columns = Mathf.CeilToInt(data.width / Cell); rows = Mathf.CeilToInt(data.length / Cell);
            blocked = new bool[columns * rows];
            foreach (MeshFilter filter in map.GetComponentsInChildren<MeshFilter>(true))
            {
                bool water = false;
                for (Transform parent = filter.transform; parent != null && parent != map.transform; parent = parent.parent)
                    if (parent.name == "water" || parent.name == "ponds") water = true;
                if (!water) continue;
                Vector3[] vertices = filter.sharedMesh.vertices; int[] triangles = filter.sharedMesh.triangles;
                for (int i = 0; i < vertices.Length; i++) vertices[i] = filter.transform.TransformPoint(vertices[i]);
                for (int i = 0; i < triangles.Length; i += 3)
                {
                    Vector3 a = vertices[triangles[i]], b = vertices[triangles[i + 1]], c = vertices[triangles[i + 2]];
                    int minX = Mathf.Clamp((int)(Mathf.Min(a.x,b.x,c.x) / Cell) - 1,0,columns-1), maxX = Mathf.Clamp((int)(Mathf.Max(a.x,b.x,c.x) / Cell) + 1,0,columns-1);
                    int minZ = Mathf.Clamp((int)(Mathf.Min(a.z,b.z,c.z) / Cell) - 1,0,rows-1), maxZ = Mathf.Clamp((int)(Mathf.Max(a.z,b.z,c.z) / Cell) + 1,0,rows-1);
                    for (int z = minZ; z <= maxZ; z++) for (int x = minX; x <= maxX; x++)
                    {
                        Vector3 p = new Vector3((x+.5f)*Cell,0,(z+.5f)*Cell);
                        float s1 = Cross(a,b,p),s2=Cross(b,c,p),s3=Cross(c,a,p);
                        if (Mathf.Abs(Cross(a,b,c)) > 0.001f && !((s1<0||s2<0||s3<0)&&(s1>0||s2>0||s3>0))) blocked[z*columns+x]=true;
                    }
                }
            }
            // 按完整建筑占地阻挡，保持独立模型与合批地图采用相同的占地来源。
            foreach (Transform building in map.GetComponentsInChildren<Transform>(true))
            {
                if (!building.name.StartsWith("BUILDING_")) continue;
                Renderer[] parts = building.GetComponentsInChildren<Renderer>(true);
                if (parts.Length == 0) continue;
                Bounds bounds = parts[0].bounds;
                foreach (Renderer part in parts) bounds.Encapsulate(part.bounds);
                BlockBuilding(bounds);
            }
            if (data.buildingsBaked)
            {
                var modelBounds = new Dictionary<string, Bounds>();
                foreach (SandboxMapBuilding building in data.buildings)
                {
                    if (!modelBounds.TryGetValue(building.model, out Bounds bounds))
                    {
                        GameObject model = Resources.Load<GameObject>("Battlefield/Models/" + building.model);
                        if (model == null) throw new InvalidOperationException("缺少建筑导航占地模型：" + building.model);
                        bool first = true;
                        foreach (MeshFilter filter in model.GetComponentsInChildren<MeshFilter>(true))
                        {
                            Bounds partBounds = TransformBounds(filter.sharedMesh.bounds, model.transform.worldToLocalMatrix * filter.transform.localToWorldMatrix);
                            if (first) { bounds = partBounds; first = false; }
                            else bounds.Encapsulate(partBounds);
                        }
                        if (first) throw new InvalidOperationException("建筑导航模型没有网格：" + building.model);
                        modelBounds.Add(building.model, bounds);
                    }
                    Matrix4x4 placement = Matrix4x4.TRS(new Vector3(building.x, building.y, building.z),
                        Quaternion.Euler(0, building.yaw, 0), new Vector3(building.scaleX, building.scaleY, building.scaleZ));
                    BlockBuilding(TransformBounds(bounds, placement));
                }
            }
            foreach (SandboxMapRoad road in data.roads)
            {
                if (!road.isBridge) continue;
                SandboxMapPoint a = Array.Find(data.nodes, n => n.id == road.startId), b = Array.Find(data.nodes, n => n.id == road.endId);
                Vector2 start = new Vector2(a.x, a.z), end = new Vector2(b.x, b.z), direction = end - start;
                int minX = Mathf.Max(0, (int)((Mathf.Min(a.x, b.x) - road.width) / Cell));
                int maxX = Mathf.Min(columns - 1, (int)((Mathf.Max(a.x, b.x) + road.width) / Cell));
                int minZ = Mathf.Max(0, (int)((Mathf.Min(a.z, b.z) - road.width) / Cell));
                int maxZ = Mathf.Min(rows - 1, (int)((Mathf.Max(a.z, b.z) + road.width) / Cell));
                for (int z = minZ; z <= maxZ; z++) for (int x = minX; x <= maxX; x++)
                {
                    Vector2 point = new Vector2((x + .5f) * Cell, (z + .5f) * Cell);
                    float t = Vector2.Dot(point - start, direction) / direction.sqrMagnitude;
                    if (t < 0 || t > 1) continue;
                    if (Vector2.Distance(point, start + direction * t) < road.width / 2 - .6f) blocked[z * columns + x] = false;
                }
            }
        }

        /// <summary>将建筑包围盒及原有安全余量写入导航阻挡网格。</summary>
        /// <param name="bounds">地图世界坐标中的建筑包围盒。</param>
        private void BlockBuilding(Bounds bounds)
        {
            bounds.Expand(new Vector3(2.4f, 0, 2.4f) * SandboxUnitView.ModelScale);
            for (int z = Mathf.Max(0, (int)(bounds.min.z / Cell)); z <= Mathf.Min(rows - 1, (int)(bounds.max.z / Cell)); z++)
                for (int x = Mathf.Max(0, (int)(bounds.min.x / Cell)); x <= Mathf.Min(columns - 1, (int)(bounds.max.x / Cell)); x++) blocked[z * columns + x] = true;
        }

        /// <summary>按模型或实例变换计算轴对齐包围盒，不创建重复建筑对象。</summary>
        /// <param name="bounds">变换前的局部包围盒。</param>
        /// <param name="matrix">模型局部坐标到目标坐标的变换。</param>
        /// <returns>包含旋转和缩放后全部顶点的目标包围盒。</returns>
        private static Bounds TransformBounds(Bounds bounds, Matrix4x4 matrix)
        {
            Vector3 extentX = matrix.MultiplyVector(Vector3.right * bounds.extents.x);
            Vector3 extentY = matrix.MultiplyVector(Vector3.up * bounds.extents.y);
            Vector3 extentZ = matrix.MultiplyVector(Vector3.forward * bounds.extents.z);
            var extents = new Vector3(Mathf.Abs(extentX.x) + Mathf.Abs(extentY.x) + Mathf.Abs(extentZ.x),
                Mathf.Abs(extentX.y) + Mathf.Abs(extentY.y) + Mathf.Abs(extentZ.y),
                Mathf.Abs(extentX.z) + Mathf.Abs(extentY.z) + Mathf.Abs(extentZ.z));
            return new Bounds(matrix.MultiplyPoint3x4(bounds.center), extents * 2);
        }
        private static float Cross(Vector3 a,Vector3 b,Vector3 p)=>(b.x-a.x)*(p.z-a.z)-(b.z-a.z)*(p.x-a.x);
        private int Index(Vector3 p)=>p.x<0||p.z<0||p.x>=columns*Cell||p.z>=rows*Cell?-1:(int)(p.z/Cell)*columns+(int)(p.x/Cell);
        private Vector3 Point(int index)=>SandboxWorld.Surface((index%columns+.5f)*Cell,(index/columns+.5f)*Cell)+Vector3.up*.08f;
        public bool IsWalkable(Vector3 point) { int index=Index(point);return index>=0&&!IsBlocked(index); }
        public List<Vector3> FindPath(Vector3 start, Vector3 end)
        {
            int from=Index(start),to=Index(end); if(from<0||to<0||IsBlocked(to))return null;
            // 十二公里地图有 1600 万格；短程兵营出兵只记录实际搜索过的格子。
            var cost=new Dictionary<int,float>();var parent=new Dictionary<int,int>();var closed=new HashSet<int>();
            cost[from]=0;var open=new List<int>{from};
            int[] dx={-1,0,1,-1,1,-1,0,1},dz={-1,-1,-1,0,0,1,1,1};
            while(open.Count>0)
            {
                int best=0;float score=float.PositiveInfinity;
                for(int i=0;i<open.Count;i++){int n=open[i];float h=cost[n]+Vector2.Distance(new Vector2(n%columns,n/columns),new Vector2(to%columns,to/columns));if(h<score){score=h;best=i;}}
                int current=open[best];open.RemoveAt(best);if(current==to)break;if(!closed.Add(current))continue;
                int x=current%columns,z=current/columns;
                for(int i=0;i<8;i++)
                {
                    int nx=x+dx[i],nz=z+dz[i];if(nx<0||nx>=columns||nz<0||nz>=rows)continue;int next=nz*columns+nx;
                    if(closed.Contains(next)||IsBlocked(next)||(dx[i]!=0&&dz[i]!=0&&(IsBlocked(z*columns+nx)||IsBlocked(nz*columns+x))))continue;
                    float value=cost[current]+(dx[i]!=0&&dz[i]!=0?1.414214f:1);
                    if(cost.TryGetValue(next,out float knownCost)&&value>=knownCost)continue;cost[next]=value;parent[next]=current;open.Add(next);
                }
            }
            if(to!=from&&!parent.ContainsKey(to))return null;
            var path=new List<Vector3>();int cursor=to;
            while(cursor!=from){path.Add(Point(cursor));if(!parent.TryGetValue(cursor,out cursor))return null;}
            path.Reverse();path.Add(SandboxWorld.Surface(end.x,end.z)+Vector3.up*.08f);return path;
        }
    }
}
