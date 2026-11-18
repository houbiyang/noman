#if UNITY_EDITOR
using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>本地预演隔离检查：不连接后端、不创建/推进用户对局。</summary>
    public static class SandboxRehearsalReplayChecks
    {
        [Serializable] private sealed class Metadata
        {
            public string scope, map_id, map_hash;
            public bool database_writes, surface_accepted;
        }
        [Serializable] private sealed class Fixture { public Metadata metadata; public SandboxReplayRecord[] records; }
        private static void Check(bool value, string message)
        { if (!value) throw new InvalidOperationException("Rehearsal check: " + message); }
        private static object Call(object target, string method, params object[] args) => target.GetType()
            .GetMethod(method, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(target, args);
        private static void Set(object target, string field, object value) => target.GetType()
            .GetField(field, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(target, value);
        [MenuItem("Qiongtu/Checks/In-map rehearsal")]
        public static void Run()
        {
            var file = JsonUtility.FromJson<Fixture>(Resources.Load<TextAsset>("Replays/tank-supply-preview").text);
            var map = SandboxMapFactory.LoadDefinition();
            Check(file.metadata.scope == "synthetic_geometry_with_current_map_visuals" && !file.metadata.database_writes
                && !file.metadata.surface_accepted && file.metadata.map_id == map.mapId && file.metadata.map_hash == map.contentHash,
                "explicit fixture scope and same current map");
            var cursor = new SandboxRoundReplayCursor(file.records);
            Check(file.records.Length == 1 && cursor.LastSeconds == 60 && file.records[0].frames.Length == 12, "one 60 second round");
            Check(cursor.Events.Count(e => e.kind == "shot" && e.source_id == "blue-ground-0") == 6
                && cursor.Events.Count(e => e.kind == "shot" && e.source_id == "red-ground-0") == 6, "both tanks fire six times");
            Check(cursor.Events.Where(e => e.kind == "service" && e.resource == "energy")
                .Sum(e => SandboxRoundReplayCursor.DisplayNumber(e.amount)) == 24, "actual energy delivery total");
            cursor.Seek(60);
            Check(cursor.World.units.Where(u => u.unit_type == "ground").All(u => u.durability == "4" && u.ammo == "54"), "tank result");
            Check(cursor.World.units.Single(u => u.unit_type == "recon").energy == "82", "hover consumption retained");
            cursor.Seek(0);
            Check(cursor.World.units.Where(u => u.unit_type == "ground").All(u => u.durability == "100")
                && cursor.World.units.Single(u => u.unit_type == "recon").energy == "60", "rewind restores initial read-only state");
            var go = new GameObject("In-map rehearsal UI checks");
            try
            {
                var hud = go.AddComponent<SandboxHudController>();
                var root = new VisualElement(); var bar = new VisualElement(); root.Add(bar);
                root.Add(new VisualElement { name = "drawer" });
                Set(hud, "drawerContent", new ScrollView());
                var world = go.AddComponent<SandboxWorld>();
                Set(world, "barracksSelectionRing", go.AddComponent<LineRenderer>());
                Set(hud, "world", world);
                bar.Add(new Button { name = "connectionAction" }); Set(hud, "root", root);
                Call(hud, "InitializeRecordedReplay");
                Check(root.Q<Button>("tankSupplyRehearsal") != null && !root.Q<Button>("recordedReplayEntry").enabledSelf,
                    "preview available without fabricating formal history");
                Call(hud, "OpenTankSupplyRehearsal");
                Check(root.Q("tank-supply-rehearsal-overlay") != null && root.Q<Image>("recorded-viewport").image != null,
                    "same Unity map overlay actually loaded");
                Call(hud, "RefreshRecordedReplayEntry");
                Check(root.Q("tank-supply-rehearsal-overlay") != null, "read-only sync does not close explicit preview");
                Call(hud, "CloseRecordedReplay");
                Check(root.Q("tank-supply-rehearsal-overlay") == null, "close releases preview");
            }
            finally { UnityEngine.Object.DestroyImmediate(go); }
            Debug.Log("[RehearsalReplayChecks] PASS: same-map resource, events, rewind, isolated UI, no backend client");
        }
        public static void CheckAndBuild() { Run(); SandboxProjectSetup.BuildCurrentWeb(); }
    }
}
#endif
