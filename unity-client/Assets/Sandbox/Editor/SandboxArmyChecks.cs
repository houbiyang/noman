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
            int routesBeforeDelete = root.GetComponentsInChildren<LineRenderer>(true).Count(line => line.name == "MovementRoute");
            world.Select("R-U01");
            Check(source.TryDeleteArmyUnit(SandboxRole.Red, "R-U01", out _), "delete moving drone");
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            world.Select(null);
            Check(world.FindUnit("R-U01") == null && drone == null, "deleted model is removed from map and lookup");
            Check(root.GetComponentsInChildren<LineRenderer>(true).Count(line => line.name == "MovementRoute") == routesBeforeDelete - 1, "deleted movement route is destroyed");
            Check(world.FindUnit("R-G02") == firstNew && firstNew.position == firstNewPosition, "deletion preserves other model instances and positions");
            Check(source.TryDeployArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Recon, 1, 45, 45, out _), "redeploy deleted identity");
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            Check(world.FindUnit("R-U01") != null && world.FindUnit("R-U01").GetComponent<SandboxUnitView>().HasTarget(SandboxWorld.ArmyPosition(45, 45, SandboxUnitType.Recon)), "redeployed identity has new model at new target");
            foreach (var unit in source.Current.units) source.TryDeleteArmyUnit(SandboxRole.Host, unit.id, out _);
            world.ApplyProjection(source.GetProjection(SandboxRole.Host)); world.Select(null);
            Check(root.GetComponentsInChildren<SandboxUnitView>(true).Length == 0, "empty map has no remaining unit models");
            Check(root.GetComponentsInChildren<LineRenderer>(true).All(line => line.name != "MovementRoute"), "empty map has no remaining movement routes");
            Check(!root.GetComponentsInChildren<LineRenderer>(true).Single(line => line.name == "SelectionRing").gameObject.activeSelf, "empty map clears selection ring");
            Check(source.TryDeployArmy(SandboxRole.Host, SandboxRole.Blue, SandboxUnitType.Recon, 10, 90, 40, out _), "redeploy ten models after clearing map");
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            Check(root.GetComponentsInChildren<SandboxUnitView>(true).Length == 10, "all ten redeployed models appear");
            CheckBarracks(world, root);
            // Nothing in this read-only validation is saved back to the source scene.
            Debug.Log("[ArmyChecks] PASS " + checks + " checks; route " + crossing.Count + " nodes; blocked samples=" + water);
        }
        private static void CheckBarracks(SandboxWorld world, GameObject root)
        {
            world.ClearProjection();
            var source = new DemoSandboxViewSource();
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            var originals = source.Current.units.ToDictionary(unit => unit.id, unit => world.FindUnit(unit.id));
            Check(!world.CanPlaceBarracks(SandboxUnitType.Recon, SandboxRole.Red, 1, 1, out _), "barracks boundary rejection even for aircraft");
            Vector2 place = Vector2.zero; bool found = false;
            for (int z = 8; z < 65 && !found; z += 4) for (int x = 8; x < 110 && !found; x += 4)
                if (world.CanPlaceBarracks(SandboxUnitType.Ground, SandboxRole.Red, x, z, out _)) { place = new Vector2(x, z); found = true; }
            Check(found, "find dry flat building footprint and ten valid rally slots");
            Check(source.TryPlaceBarracks(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, place.x, place.y, out _), "place ground barracks");
            Check(source.Current.units.Length == 6, "construction does not manually deploy units");
            Check(source.TrySpawnArmy(SandboxRole.Red, SandboxRole.Red, SandboxUnitType.Ground, 2, out _), "reserved simulation spawn entry");
            world.AnimateBarracksSpawns(new[] { "R-G02", "R-G03" });
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            string campId = DemoSandboxViewSource.BarracksId(SandboxRole.Red, SandboxUnitType.Ground);
            Transform camp = world.FindBarracks(campId);
            Check(camp != null && camp.GetComponentsInChildren<Renderer>().Length > 12, "map barracks is a real rendered building");
            Check(originals.All(pair => world.FindUnit(pair.Key) == pair.Value), "barracks creation preserves existing model instances");
            var unit = world.FindUnit("R-G02");
            Check(Vector3.Distance(unit.position, SandboxWorld.ArmyPosition(place.x, place.y + 3.5f, SandboxUnitType.Ground)) < .01f, "new ground unit starts at barracks exit");
            Check(unit.GetComponent<SandboxUnitView>().IsMoving, "spawned unit has a path from exit to rally point");
            Check(!world.CanPlace(place.x, place.y, SandboxUnitType.Ground), "ground navigation blocks barracks building");
            Check(world.CanPlaceBarracks(SandboxUnitType.Ground, SandboxRole.Red, place.x, place.y, out _), "own camp can be selected again for relocation");
            foreach (SandboxUnitType type in Enum.GetValues(typeof(SandboxUnitType)))
                Check(Resources.Load<Texture2D>("Army/" + DemoSandboxViewSource.ArmyKey(type) + "-barracks-card")?.width == 780, type + " barracks card rendered from Unity model");
            var serialized = JsonUtility.FromJson<SandboxSessionData>(JsonUtility.ToJson(source.Current));
            Check(DemoSandboxViewSource.ValidateSession(serialized) == "" && serialized.barracks.Length == 1 && serialized.units.Single(item => item.id == "R-G02").barracksId == campId, "Unity JSON round-trip preserves barracks and unit association");
            world.ApplyProjection(source.GetProjection(SandboxRole.Blue));
            Check(world.FindBarracks(campId) == null && world.FindUnit("R-G02") == null, "role switch removes opposite camp and troops");
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            Check(world.FindBarracks(campId) != null && world.FindUnit("R-G02").position == SandboxWorld.ArmyPosition(source.Current.units.Single(item => item.id == "R-G02").x, source.Current.units.Single(item => item.id == "R-G02").z, SandboxUnitType.Ground), "role switch does not replay spawning");
            world.ClearProjection();
            Check(world.FindBarracks(campId) == null && root.GetComponentsInChildren<SandboxUnitView>(true).Length == 0, "clear removes buildings and troops");
            Debug.Log("[BarracksChecks] validated placement " + place);
        }

        private static Vector3 Nearest(SandboxArmyNavigation navigation, SandboxMapPoint point)
        {
            for (int radius = 0; radius <= 12; radius += 3) for (int z = -radius; z <= radius; z += 3) for (int x = -radius; x <= radius; x += 3)
            { var p = SandboxWorld.Surface(point.x + x, point.z + z); if (navigation.IsWalkable(p)) return p; }
            throw new InvalidOperationException("No walkable access for " + point.id);
        }
    }
}
