using System;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UIElements;
using System.Reflection;

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
            var crossingBridge = data.roads.First(road => road.isBridge);
            Vector3 red = Nearest(navigation, data.nodes.First(node => node.id == crossingBridge.startId));
            Vector3 blue = Nearest(navigation, data.nodes.First(node => node.id == crossingBridge.endId));
            var crossing = navigation.FindPath(red, blue);
            Check(crossing != null && crossing.Count > 10, "cross-river A* route");
            Check(crossing.All(navigation.IsWalkable), "path avoids blocked cells");
            SandboxMapBuilding bakedBuilding = data.buildings.First(building => building.damage == 0 && building.model == "Strategic_House_0");
            Check(!navigation.IsWalkable(new Vector3(bakedBuilding.x, 0, bakedBuilding.z)), "合批建筑占地被阻挡");
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
            CheckUnitPresentation(world, source);
            CheckDroneLocate(world, source);
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
            Check(world.TryBusinessPosition(new Vector3(data.width, 0, data.length), out Vector2 corner)
                && Vector2.Distance(corner, new Vector2(120, 80)) < .001f, "十二公里边界反算");
            Check(!world.TryBusinessPosition(new Vector3(float.NaN, 0, 0), out _), "非有限选点被拒绝");
            // 本检查不将临时验证对象写回源场景。
            Debug.Log("[ArmyChecks] PASS " + checks + " checks; route " + crossing.Count + " nodes; blocked samples=" + water);
        }
        private static void CheckBarracks(SandboxWorld world, GameObject root)
        {
            world.ClearProjection();
            var source = new DemoSandboxViewSource();
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            var originals = source.Current.units.ToDictionary(unit => unit.id, unit => world.FindUnit(unit.id));
            Check(!world.CanPlaceBarracks(SandboxUnitType.Recon, SandboxRole.Red, .01f, .01f, out _), "barracks boundary rejection even for aircraft");
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
            Check(camp.localScale == Vector3.one * 1.5f, "barracks model uses 1.5x scale");
            var pick = camp.GetComponent<BoxCollider>();
            Check(pick != null && Mathf.Abs(pick.bounds.size.x - 25.5f) < .01f, "enlarged camp has matching click collider");
            var cameraObject = new GameObject("BarracksPickingCheck");
            var rig = cameraObject.AddComponent<SandboxCameraRig>(); rig.Initialize(world);
            cameraObject.transform.position = camp.position + new Vector3(0, 60, -35);
            cameraObject.transform.LookAt(camp.position + Vector3.up * 3);
            Physics.SyncTransforms();
            Check(rig.PickBarracks(rig.WorldToScreen(camp.position + Vector3.up * 3)) == campId, "camera ray click selects actual camp");
            world.SelectBarracks(campId);
            Check(root.GetComponentsInChildren<LineRenderer>(true).Single(line => line.name == "BarracksSelectionRing").gameObject.activeSelf, "selected camp has visible selection ring");
            world.SelectBarracks(null);
            Check(!root.GetComponentsInChildren<LineRenderer>(true).Single(line => line.name == "BarracksSelectionRing").gameObject.activeSelf, "closing management clears camp selection");
            UnityEngine.Object.DestroyImmediate(cameraObject);

            var unit = world.FindUnit("R-G02");
            Check(Vector3.Distance(unit.position, SandboxWorld.ArmyPosition(place.x, SandboxArmyLayout.ExitZ(place.y), SandboxUnitType.Ground)) < .01f, "new ground unit starts at barracks exit");
            Check(Mathf.Abs((unit.position.z - camp.position.z) - SandboxArmyLayout.ExitOffset) < .01f, "十二公里地图的出兵口与放大模型尺寸一致");
            Check(Vector3.Distance(unit.position, SandboxWorld.ArmyPosition(source.Current.units.Single(item => item.id == "R-G02").x,
                source.Current.units.Single(item => item.id == "R-G02").z, SandboxUnitType.Ground)) < 20, "出兵集结目标保持在兵营附近");
            Check(unit.GetComponent<SandboxUnitView>().IsMoving, "spawned unit has a path from exit to rally point");
            Check(!world.CanPlace(place.x, place.y, SandboxUnitType.Ground), "ground navigation blocks barracks building");
            Check(world.CanPlaceBarracks(SandboxUnitType.Ground, SandboxRole.Red, place.x, place.y, out _), "own camp can be selected again for relocation");
            foreach (SandboxUnitType type in Enum.GetValues(typeof(SandboxUnitType)))
                Check(Resources.Load<Texture2D>("Army/" + DemoSandboxViewSource.ArmyKey(type) + "-barracks-card")?.width == 780, type + " barracks card rendered from Unity model");
            var healthState = source.Current; healthState.units[0].health = 65; healthState.units[0].maxHealth = 100;
            var serialized = JsonUtility.FromJson<SandboxSessionData>(JsonUtility.ToJson(healthState));
            Check(serialized.units[0].health == 65 && serialized.units[0].maxHealth == 100, "Unity JSON preserves optional health values");
            Check(DemoSandboxViewSource.ValidateSession(serialized) == "" && serialized.barracks.Length == 1 && serialized.units.Single(item => item.id == "R-G02").barracksId == campId, "Unity JSON round-trip preserves barracks and unit association");
            world.ApplyProjection(source.GetProjection(SandboxRole.Blue));
            Check(world.FindBarracks(campId) == null && world.FindUnit("R-G02") == null, "role switch removes opposite camp and troops");
            world.ApplyProjection(source.GetProjection(SandboxRole.Host));
            Check(world.FindBarracks(campId) != null && world.FindUnit("R-G02").position == SandboxWorld.ArmyPosition(source.Current.units.Single(item => item.id == "R-G02").x, source.Current.units.Single(item => item.id == "R-G02").z, SandboxUnitType.Ground), "role switch does not replay spawning");
            CheckManagementUi(world, source, campId);
            world.ClearProjection();
            Check(world.FindBarracks(campId) == null && root.GetComponentsInChildren<SandboxUnitView>(true).Length == 0, "clear removes buildings and troops");
            Debug.Log("[BarracksChecks] validated placement " + place);
        }

        private static void CheckManagementUi(SandboxWorld world, DemoSandboxViewSource source, string campId)
        {
            var host = new GameObject("BarracksManagementUiCheck");
            var rig = host.AddComponent<SandboxCameraRig>(); rig.Initialize(world);
            var hud = host.AddComponent<SandboxHudController>(); hud.Initialize(source, world, rig);
            var root = host.GetComponent<UIDocument>().rootVisualElement;
            Action<string, object[]> invoke = (name, args) => typeof(SandboxHudController).GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(hud, args);
            var campLabel = root.Q<Label>("barracks-label-" + campId);
            Check(campLabel != null && campLabel.text == "红方履带单元兵营" && campLabel.ClassListContains("red-text"), "placed camp has side and building map label");
            Check(campLabel.pickingMode == PickingMode.Ignore, "camp map label does not intercept placement or selection");
            invoke("SelectBarracks", new object[] { campId });
            var manager = root.Q("barracksManagement");
            Check(manager != null && root.Q("drawer").ClassListContains("hidden"), "camp click opens integrated manager without old units drawer");
            var list = manager.Q("barracksUnits");
            Check(list.childCount == 3 && list.Children().All(item => item.name.StartsWith("barracks-unit-R-G")), "management lists only three red ground units including original");
            Check(manager.Query<Label>().ToList().Any(label => label.text == "血量 · 未提供"), "unknown health is not replaced with full health");
            var search = manager.Q<TextField>(); search.value = "R-G02";
            Check(list.childCount == 1 && list[0].name == "barracks-unit-R-G02", "integrated manager searches within scoped troops");
            var save = source.Current; save.units.Single(unit => unit.id == "R-G02").health = 65; save.units.Single(unit => unit.id == "R-G02").maxHealth = 100;
            Check(source.TryRestore(save, out _), "UI fixture can report real health");
            invoke("RefreshProjection", new object[0]);
            manager = root.Q("barracksManagement");
            Check(manager.Q<ProgressBar>()?.value == 65 && manager.Query<Label>().ToList().Any(label => label.text == "血量 65 / 100"), "reported health renders a numeric value and health bar");
            invoke("OpenDeleteArmyUnitModal", new object[] { source.Current.units.Single(unit => unit.id == "R-G02") });
            Check(!root.Q("modalBackdrop").ClassListContains("hidden") && root.Q("modalBackdrop").parent.Children().Last() == root.Q("modalBackdrop"), "delete confirmation covers camp panel");
            invoke("ConfirmModal", new object[0]);
            Check(source.Current.units.All(unit => unit.id != "R-G02") && root.Q("barracks-unit-R-G02") == null, "confirmed deletion refreshes integrated list and count");
            invoke("OpenArmyManagement", new object[] { SandboxRole.Red, SandboxUnitType.Support });
            Check(root.Q("barracksUnits").childCount == 1, "type switching does not mix troops");
            invoke("BeginMove", new object[] { "R-S01" });
            Check(root.Q("barracksManagement") == null && rig.IsMapCommand(), "map movement closes manager and enters map command");
            invoke("CancelArmyCommand", new object[0]);
            invoke("ChangeRole", new object[] { SandboxRole.Blue });
            Check(root.Q("barracksManagement") == null && world.FindBarracks(campId) == null, "role switch clears previous camp panel and models");
            Check(root.Q("barracks-label-" + campId) == null, "blue projection removes red camp labels");
            invoke("OpenArmyManagement", new object[] { SandboxRole.Red, SandboxUnitType.Ground });
            Check(root.Q("barracksManagement") == null, "blue role cannot reopen red camp private state");
            invoke("ChangeRole", new object[] { SandboxRole.Host });
            invoke("OpenArmyManagement", new object[] { SandboxRole.Blue, SandboxUnitType.Support });
            Check(root.Q("barracksUnits").childCount == 1 && root.Q("barracks-unit-B-S01") != null, "host can manage blue camp scope");
            invoke("OpenDeleteArmyUnitModal", new object[] { source.Current.units.Single(unit => unit.id == "B-S01") });
            invoke("ConfirmModal", new object[0]);
            Check(root.Q("barracksUnits").childCount == 1 && root.Q("barracksUnits")[0].ClassListContains("army-empty"), "deleting final unit shows empty state");
            Check(root.Q<Label>("barracks-label-" + campId)?.text == "红方履带单元兵营", "host projection restores red camp label");
            foreach (SandboxRole side in new[] { SandboxRole.Red, SandboxRole.Blue })
                foreach (SandboxUnitType type in Enum.GetValues(typeof(SandboxUnitType)))
                    Check(source.TryPlaceBarracks(SandboxRole.Host, side, type, side == SandboxRole.Red ? 25 : 95, 20 + (int)type * 12, out _), "label fixture places or relocates " + side + " " + type);
            invoke("RefreshProjection", new object[0]);
            var campLabels = root.Query<Label>(className: "barracks-map-label").ToList();
            Check(campLabels.Count == 6 && campLabels.Select(label => label.name).Distinct().Count() == 6, "refresh and relocation retain exactly one label per camp");
            Check(root.Q<Label>("barracks-label-" + DemoSandboxViewSource.BarracksId(SandboxRole.Red, SandboxUnitType.Support))?.text == "红方补给站"
                && root.Q<Label>("barracks-label-" + DemoSandboxViewSource.BarracksId(SandboxRole.Blue, SandboxUnitType.Support))?.text == "蓝方补给站", "support camps use side-specific supply station labels");
            invoke("ChangeRole", new object[] { SandboxRole.Blue });
            campLabels = root.Query<Label>(className: "barracks-map-label").ToList();
            Check(campLabels.Count == 3 && campLabels.All(label => label.ClassListContains("blue-text")), "blue role sees only three blue camp labels");
            UnityEngine.Object.DestroyImmediate(host);
        }

        private static void CheckUnitPresentation(SandboxWorld world, DemoSandboxViewSource source)
        {
            var data = SandboxMapFactory.LoadDefinition();
            int steepest = Enumerable.Range(data.heightColumns, data.heights.Length - data.heightColumns)
                .OrderByDescending(i => Mathf.Abs(data.heights[i] - data.heights[i - data.heightColumns])).First();
            float steepX = steepest % data.heightColumns * data.width / (data.heightColumns - 1f);
            float steepZ = steepest / data.heightColumns * data.length / (data.heightRows - 1f);
            var bridge = data.roads.First(road => road.isBridge);
            var a = data.nodes.First(node => node.id == bridge.startId); var b = data.nodes.First(node => node.id == bridge.endId);
            var locations = new[] { new Vector2(800, 1200), new Vector2(steepX, steepZ), new Vector2((a.x + b.x) * .5f, (a.z + b.z) * .5f) };
            var pose = typeof(SandboxUnitView).GetMethod("UpdateVisualPose", BindingFlags.NonPublic | BindingFlags.Instance);
            var update = typeof(SandboxUnitView).GetMethod("Update", BindingFlags.NonPublic | BindingFlags.Instance);
            var spawnAt = typeof(SandboxUnitView).GetField("spawnAt", BindingFlags.NonPublic | BindingFlags.Instance);
            foreach (SandboxUnitType type in Enum.GetValues(typeof(SandboxUnitType)))
            {
                var unit = source.Current.units.First(item => item.side == SandboxRole.Red && item.type == type);
                var view = world.FindUnit(unit.id).GetComponent<SandboxUnitView>();
                var visual = view.transform.Find("AnimatedModel");
                foreach (Vector2 point in locations)
                {
                    view.Relocate(SandboxWorld.ArmyPosition(point.x / 100, point.y / 150, type));
                    Check(Mathf.Abs(view.transform.position.y - SandboxWorld.Surface(point.x, point.y).y - (type == SandboxUnitType.Recon ? 48 : .08f)) < .001f, type + " root uses correct surface-relative altitude");
                    foreach (float yaw in new[] { 0f, 75f, 180f, 285f })
                    {
                        view.transform.rotation = Quaternion.Euler(0, yaw, 0);
                        pose.Invoke(view, new object[] { 12.5f, true, 1f });
                        Check(visual.localScale == Vector3.one * 2, type + " animation retains double model scale");
                        var parts = visual.GetComponentsInChildren<Renderer>(); var bounds = parts[0].bounds;
                        foreach (var part in parts) bounds.Encapsulate(part.bounds);
                        float highest = float.NegativeInfinity;
                        for (int z = 0; z <= 16; z++) for (int x = 0; x <= 16; x++)
                            highest = Mathf.Max(highest, SandboxWorld.Surface(Mathf.Lerp(bounds.min.x, bounds.max.x, x / 16f), Mathf.Lerp(bounds.min.z, bounds.max.z, z / 16f)).y);
                        float gap = type == SandboxUnitType.Recon ? 48 : .08f;
                        Check(bounds.min.y + .002f >= highest + gap, type + " rotated model bottom clears all sampled terrain/bridge footprint");
                        Bounds pick = view.GetComponent<BoxCollider>().bounds; pick.Expand(.01f);
                        Check(pick.Contains(bounds.min) && pick.Contains(bounds.max), type + " click bounds contain doubled lifted animation");
                        Check(view.MapLabelPosition.y > bounds.max.y, type + " map label remains above model");
                        Vector3 before = visual.localPosition;
                        pose.Invoke(view, new object[] { 12.5f, true, 1f });
                        Check(Vector3.Distance(before, visual.localPosition) < .001f, type + " terrain correction never accumulates on refresh");
                    }
                }
                view.SpawnAt(SandboxWorld.ArmyPosition(unit.x, unit.z, type), 0);
                Check(visual.localScale == Vector3.zero && !view.GetComponent<BoxCollider>().enabled, type + " delayed spawn is invisible and unpickable");
                pose.Invoke(view, new object[] { 12.5f, false, .5f });
                Check(visual.localScale.x > 1 && visual.localScale.x < 2, type + " spawn animates towards doubled scale");
                pose.Invoke(view, new object[] { 12.5f, false, 1f });
                view.SetTarget(SandboxWorld.ArmyPosition(unit.x + .5f, unit.z + .5f, type));
                spawnAt.SetValue(view, -1f); update.Invoke(view, null);
                Check(view.IsMoving && Mathf.Abs(view.transform.position.y - SandboxWorld.Surface(view.transform.position.x, view.transform.position.z).y - (type == SandboxUnitType.Recon ? 48 : .08f)) < .001f, type + " movement retains terrain-relative altitude");
                view.Relocate(SandboxWorld.ArmyPosition(unit.x, unit.z, type));
            }
        }

        private static void CheckDroneLocate(SandboxWorld world, DemoSandboxViewSource source)
        {
            var host = new GameObject("DroneLocateCameraCheck");
            var rig = host.AddComponent<SandboxCameraRig>(); rig.Initialize(world);
            var camera = host.GetComponent<Camera>();
            var apply = typeof(SandboxCameraRig).GetMethod("ApplyCamera", BindingFlags.NonPublic | BindingFlags.Instance);
            var pan = typeof(SandboxCameraRig).GetMethod("Pan", BindingFlags.NonPublic | BindingFlags.Instance);
            var yaw = typeof(SandboxCameraRig).GetField("yaw", BindingFlags.NonPublic | BindingFlags.Instance);
            var drone = source.Current.units.First(unit => unit.type == SandboxUnitType.Recon);
            var view = world.FindUnit(drone.id).GetComponent<SandboxUnitView>();
            Vector3 original = view.transform.position;
            var viewport = new Rect(.04f, .2f, .64f, .57f);
            foreach (var point in new[] { new Vector2(800, 1200), new Vector2(6100, 6000), new Vector2(10800, 10200) })
            foreach (float aspect in new[] { 16f / 9, 4f / 3, 9f / 16 })
            foreach (float angle in new[] { 0f, 90f, 225f })
            {
                view.Relocate(SandboxWorld.ArmyPosition(point.x / 100, point.y / 150, SandboxUnitType.Recon));
                camera.aspect = aspect; yaw.SetValue(rig, angle);
                rig.Locate(drone.id, viewport);
                // Update's zero keyboard input must not collapse a framed focus back to terrain.
                pan.Invoke(rig, new object[] { Vector2.zero }); apply.Invoke(rig, new object[] { false });
                Check(!rig.IsFollowing, "drone locate remains a non-follow camera");
                Bounds bounds = view.VisualBounds;
                bool inside = true;
                for (int i = 0; i < 8; i++)
                {
                    Vector3 corner = bounds.center + Vector3.Scale(bounds.extents,
                        new Vector3((i & 1) == 0 ? -1 : 1, (i & 2) == 0 ? -1 : 1, (i & 4) == 0 ? -1 : 1));
                    Vector3 screen = camera.WorldToViewportPoint(corner);
                    inside &= screen.z > camera.nearClipPlane && viewport.Contains(screen);
                }
                Check(inside, "entire doubled drone is in unobstructed locate viewport");
                Vector3 ground = SandboxWorld.Surface(point.x, point.y) + Vector3.up * .3f;
                Vector3 projected = camera.WorldToViewportPoint(ground);
                Check(projected.z > camera.nearClipPlane && viewport.Contains(projected), "ground projection remains visible with airborne model");
                Check(viewport.Contains(camera.WorldToViewportPoint(view.MapLabelPosition)), "drone map label stays in locate viewport");
            }
            world.Select(drone.id);
            var line = world.GetComponentsInChildren<LineRenderer>(true).Single(item => item.name == "SelectionProjection");
            Check(line.gameObject.activeSelf && line.GetPosition(1).y - line.GetPosition(0).y > 47, "selected drone has visible ground-to-model projection line");
            Vector3 locatedPosition = host.transform.position;
            rig.Follow(drone.id); rig.ReturnToOverview(); apply.Invoke(rig, new object[] { false });
            Check(Vector3.Distance(locatedPosition, host.transform.position) < .01f, "return from follow preserves elevated locate framing");
            var groundUnit = source.Current.units.First(unit => unit.type == SandboxUnitType.Ground);
            world.Select(groundUnit.id);
            Check(!line.gameObject.activeSelf, "ground unit selection hides airborne projection line");
            rig.Locate(groundUnit.id); apply.Invoke(rig, new object[] { false });
            Check(camera.WorldToViewportPoint(world.FindUnit(groundUnit.id).position).z > 0, "ground locate remains visible");
            rig.ResetOverview();
            Check(!(bool)typeof(SandboxCameraRig).GetField("elevatedFocus", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(rig), "reset clears elevated framing");
            view.Relocate(original); world.Select(null);
            Check(!line.gameObject.activeSelf, "deselect clears projection line");
            UnityEngine.Object.DestroyImmediate(host);
        }

        private static Vector3 Nearest(SandboxArmyNavigation navigation, SandboxMapPoint point)
        {
            for (int radius = 0; radius <= 12; radius += 3) for (int z = -radius; z <= radius; z += 3) for (int x = -radius; x <= radius; x += 3)
            { var p = SandboxWorld.Surface(point.x + x, point.z + z); if (navigation.IsWalkable(p)) return p; }
            throw new InvalidOperationException("No walkable access for " + point.id);
        }
    }
}
