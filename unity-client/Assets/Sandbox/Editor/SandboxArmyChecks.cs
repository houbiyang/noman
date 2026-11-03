using System;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Qiongtu.Sandbox.Editor
{
    public static class SandboxArmyChecks
    {
        private static int checks;
        private static void Check(bool value, string description)
        {
            if (!value) throw new InvalidOperationException("Army check failed: " + description);
            checks++;
        }
        [MenuItem("穹图/军队/验证模型与地图寻路")]
        public static void Run()
        {
            checks = 0;
            EditorSceneManager.OpenScene("Assets/Scenes/Sandbox.unity");
            var anchor = UnityEngine.Object.FindAnyObjectByType<SandboxMapAnchor>();
            Check(anchor != null, "existing terrain");
            var data = SandboxMapFactory.LoadDefinition();
            var navigation = new SandboxArmyNavigation(anchor.gameObject, data);
            int water = 0, ground = 0;
            for (int z = 3; z < data.length; z += 6) for (int x = 3; x < data.width; x += 6)
            { if (navigation.IsWalkable(new Vector3(x, 0, z))) ground++; else water++; }
            Check(water > 100 && ground > 1000, "obstacle raster is nonempty and terrain remains usable");
            Vector3 red = Nearest(navigation, data.nodes.First(n => n.id == "R_GATE"));
            Vector3 blue = Nearest(navigation, data.nodes.First(n => n.id == "B_GATE"));
            var crossing = navigation.FindPath(red, blue);
            Check(crossing != null && crossing.Count > 10, "cross-river A* route");
            Check(crossing.All(navigation.IsWalkable), "path avoids blocked cells");
            foreach (var road in data.roads.Where(r => r.isBridge))
            {
                var a = data.nodes.First(n => n.id == road.startId); var b = data.nodes.First(n => n.id == road.endId);
                Check(navigation.IsWalkable(new Vector3((a.x + b.x)/2, 0, (a.z + b.z)/2)), "bridge " + road.id);
            }
            foreach (var type in (SandboxUnitType[])Enum.GetValues(typeof(SandboxUnitType)))
            {
                string key = DemoSandboxViewSource.ArmyKey(type);
                var prefab = Resources.Load<GameObject>("Army/" + key);
                Check(prefab != null && prefab.GetComponentsInChildren<MeshRenderer>().Length >= 5, key + " authored mesh");
                Check(Resources.Load<Texture2D>("Army/" + key + "-card")?.width == 780, key + " rendered card");
                var children = prefab.GetComponentsInChildren<Transform>();
                Check(children.Count(c => c.name.StartsWith(type == SandboxUnitType.Recon ? "RotorPivot_" : "WheelPivot_")) == (type == SandboxUnitType.Recon ? 4 : type == SandboxUnitType.Ground ? 8 : 6), key + " mechanical pivots");
            }
            var root = new GameObject("ArmyCheckWorld"); var world = root.AddComponent<SandboxWorld>(); world.Initialize(anchor.gameObject);
            foreach (float x in new[] { 1f, 23f, 60f, 97f, 119f }) foreach (float z in new[] { 1f, 20f, 40f, 79f })
            {
                Vector3 p = SandboxWorld.ArmyPosition(x, z, SandboxUnitType.Ground);
                Check(world.TryBusinessPosition(p, out Vector2 business) && Vector2.Distance(business, new Vector2(x, z)) < .02f, "inverse coordinates " + x + "," + z);
            }
            Check(!world.TryBusinessPosition(new Vector3(-10, 0, -10), out _), "off-map inverse rejected");
            var source = new DemoSandboxViewSource();
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            var originals = source.Current.units.ToDictionary(unit => unit.id, unit => world.FindUnit(unit.id));
            Check(source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 2, 40, 40, out _), "first append batch");
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            Transform firstNew = world.FindUnit("R-G02"); Vector3 firstNewPosition = firstNew.position;
            Check(originals.All(pair => world.FindUnit(pair.Key) == pair.Value), "first batch preserves existing model instances");
            Check(source.TryMoveArmyUnit(SandboxRole.Red, "R-U01", 45, 35, out _), "set original drone motion");
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            var drone = world.FindUnit("R-U01").GetComponent<SandboxUnitView>();
            Check(drone.IsMoving, "original drone has active motion");
            Check(source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 1, 70, 50, out _), "second append batch");
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            Check(world.FindUnit("R-G02") == firstNew && firstNew.position == firstNewPosition, "second batch does not relocate previous batch");
            Check(originals.All(pair => world.FindUnit(pair.Key) == pair.Value) && drone.IsMoving, "second batch preserves old models and movement");
            Check(world.FindUnit("R-G04") != null && world.FindUnit("R-G04") != firstNew, "new batch creates separate model");
            // Nothing in this read-only validation is saved back to the source scene.
            Debug.Log("[ArmyChecks] PASS " + checks + " checks; route " + crossing.Count + " nodes; blocked samples=" + water);
        }
        private static Vector3 Nearest(SandboxArmyNavigation navigation, SandboxMapPoint point)
        {
            for (int radius = 0; radius <= 12; radius += 3) for (int z = -radius; z <= radius; z += 3) for (int x = -radius; x <= radius; x += 3)
            { var p = SandboxWorld.Surface(point.x + x, point.z + z); if (navigation.IsWalkable(p)) return p; }
            throw new InvalidOperationException("No walkable access for " + point.id);
        }
    }
}
