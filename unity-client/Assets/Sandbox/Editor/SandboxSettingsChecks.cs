using System;
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Qiongtu.Sandbox.Editor
{
    public static class SandboxSettingsChecks
    {
        private static int count;
        private static void Check(bool value, string message)
        { if (!value) throw new InvalidOperationException("Settings check failed: " + message); count++; }

        [MenuItem("穹图/设置/验证本机设置与资源")]
        public static void Run()
        {
            count = 0;
            string directory = Path.Combine(Path.GetTempPath(), "qiongtu-settings-check-" + Guid.NewGuid().ToString("N"));
            string path = Path.Combine(directory, "FrontendSandboxSettings.json");
            try
            {
                var store = new SandboxSettingsStore(directory);
                var loaded = store.Load(out string warning);
                Check(warning.Length == 0 && loaded.showRoutes, "missing settings use valid defaults without warning");
                var settings = new SandboxClientSettings();
                settings.red.generation.baseUrl = "https://red.example/v1";
                settings.red.generation.credentialRef = "RED_AI_API_KEY";
                settings.red.generation.enabled = true;
                settings.blue.generation.baseUrl = "http://localhost:11434/v1";
                settings.blue.generation.model = "local-test";
                settings.showGrid = true; settings.showLabels = false;
                Check(store.TrySave(settings, out string message), "initial native write");
                loaded = store.Load(out warning);
                Check(warning.Length == 0 && loaded.red.generation.enabled && loaded.red.generation.baseUrl == "https://red.example/v1", "Unity JSON restores nested models");
                Check(loaded.blue.generation.model == "local-test" && loaded.red.generation.model != loaded.blue.generation.model, "side configuration independence");
                Check(loaded.showGrid && !loaded.showLabels && loaded.red.temperature == .2f, "display booleans and decimal round trip");
                settings.showGrid = false;
                Check(store.TrySave(settings, out message) && !store.Load(out warning).showGrid, "atomic replacement updates existing record");
                string before = File.ReadAllText(path);
                settings.red.generation.credentialRef = "sk-do-not-store-this";
                Check(!store.TrySave(settings, out message) && File.ReadAllText(path) == before, "invalid secret input never overwrites saved settings");
                File.WriteAllText(path, "{broken");
                loaded = store.Load(out warning);
                Check(warning.Length > 0 && loaded.showRoutes && File.ReadAllText(path) == "{broken", "corrupt record preserved and surfaced");
                var future = new SandboxClientSettings { formatVersion = 99 };
                File.WriteAllText(path, JsonUtility.ToJson(future)); before = File.ReadAllText(path);
                loaded = store.Load(out warning);
                Check(warning.Length > 0 && loaded.formatVersion == 1 && File.ReadAllText(path) == before, "unknown version not silently accepted or destroyed");
                foreach (string resource in new[] { "Sandbox/SandboxSettings", "Sandbox/SandboxArmy", "Sandbox/SandboxHud" })
                    Check(Resources.Load<StyleSheet>(resource) != null, "style resource " + resource);
                var layout = Resources.Load<VisualTreeAsset>("Sandbox/SandboxHud").CloneTree();
                Check(layout.Q<Button>("settingsAction") != null && layout.Q<Button>("armyAction") != null, "settings and army entries in actual layout");
                Check(layout.Q("modalBackdrop") != null && layout.Q<Button>("confirmModalButton") != null, "shared confirmation available");
                Debug.Log("[SettingsChecks] PASS " + count + " checks; temporary storage isolated from user preferences");
            }
            finally { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
        }
    }
}
