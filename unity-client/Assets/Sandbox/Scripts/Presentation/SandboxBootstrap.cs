using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;

namespace Qiongtu.Sandbox
{
    /// <summary>独立 Unity 沙盘入口，组装场景、显示数据源和原生界面。</summary>
    [DisallowMultipleComponent]
    public sealed class SandboxBootstrap : MonoBehaviour
    {
        private VolumeProfile ownedProfile;

        /// <summary>允许首次打开模板场景时直接运行，正式场景由编辑器脚本保存。</summary>
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void EnsureEntryPoint()
        {
            string sceneName = SceneManager.GetActiveScene().name;
            if (sceneName != "SampleScene" && sceneName != "Sandbox") return;
            if (FindAnyObjectByType<SandboxBootstrap>() != null) return;
            new GameObject("QiongtuSandbox").AddComponent<SandboxBootstrap>();
        }

        /// <summary>只在本工程入口场景中替换模板相机与光源，建立完整客户端。</summary>
        private void Start()
        {
            foreach (Camera templateCamera in FindObjectsByType<Camera>()) templateCamera.enabled = false;
            foreach (AudioListener listener in FindObjectsByType<AudioListener>()) listener.enabled = false;
            foreach (Light templateLight in FindObjectsByType<Light>()) templateLight.enabled = false;
            Application.targetFrameRate = Application.platform == RuntimePlatform.WebGLPlayer ? -1 : 60;
            Application.runInBackground = false;
            var worldObject = new GameObject("SandboxWorld");
            worldObject.transform.SetParent(transform, false);
            SandboxWorld world = worldObject.AddComponent<SandboxWorld>();
            SandboxMapAnchor existingMap = FindAnyObjectByType<SandboxMapAnchor>();
            world.Initialize(existingMap == null ? null : existingMap.gameObject);
            var cameraObject = new GameObject("SandboxCamera");
            cameraObject.transform.SetParent(transform, false);
            SandboxCameraRig cameraRig = cameraObject.AddComponent<SandboxCameraRig>();
            cameraRig.Initialize(world);
            cameraObject.AddComponent<AudioListener>();
            UniversalAdditionalCameraData cameraData = cameraObject.GetComponent<Camera>().GetUniversalAdditionalCameraData();
            cameraData.renderPostProcessing = true;
            cameraData.volumeLayerMask = 1;
            CreateLighting();
            var cloudObject = new GameObject("SandboxCloudLayer");
            cloudObject.transform.SetParent(transform, false);
            cloudObject.AddComponent<SandboxCloudLayer>().Initialize(world.MapSize);
            var uiObject = new GameObject("SandboxHud");
            uiObject.transform.SetParent(transform, false);
            uiObject.AddComponent<SandboxHudController>().Initialize(new DemoSandboxViewSource(), world, cameraRig);
        }

        /// <summary>建立柔和方向光、环境光和轻量后处理，不加载额外素材包。</summary>
        private void CreateLighting()
        {
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = new Color(0.46f, 0.49f, 0.52f);
            RenderSettings.ambientEquatorColor = new Color(0.3f, 0.31f, 0.29f);
            RenderSettings.ambientGroundColor = new Color(0.17f, 0.16f, 0.14f);
            RenderSettings.fog = true;
            RenderSettings.fogMode = FogMode.Linear;
            RenderSettings.fogColor = new Color(0.58f, 0.61f, 0.61f);
            RenderSettings.fogStartDistance = 400;
            RenderSettings.fogEndDistance = 1000;
            var sun = new GameObject("SandboxSun");
            sun.transform.SetParent(transform, false);
            sun.transform.rotation = Quaternion.Euler(35, -38, 0);
            Light light = sun.AddComponent<Light>();
            light.type = LightType.Directional;
            light.color = new Color(1, 0.95f, 0.86f);
            light.intensity = 1.45f;
            light.shadows = LightShadows.Soft;
            light.shadowStrength = 0.78f;
            var volumeObject = new GameObject("SandboxGlobalVolume");
            volumeObject.transform.SetParent(transform, false);
            Volume volume = volumeObject.AddComponent<Volume>();
            volume.isGlobal = true;
            volume.priority = 10;
            ownedProfile = ScriptableObject.CreateInstance<VolumeProfile>();
            Tonemapping tonemapping = ownedProfile.Add<Tonemapping>(true);
            tonemapping.mode.Override(TonemappingMode.ACES);
            Bloom bloom = ownedProfile.Add<Bloom>(true);
            bloom.intensity.Override(0.18f);
            bloom.threshold.Override(1.1f);
            Vignette vignette = ownedProfile.Add<Vignette>(true);
            vignette.intensity.Override(0.17f);
            volume.sharedProfile = ownedProfile;
        }

        /// <summary>释放仅由当前客户端创建的后处理资源。</summary>
        private void OnDestroy()
        {
            if (ownedProfile != null)
            {
                foreach (VolumeComponent component in ownedProfile.components) Destroy(component);
                Destroy(ownedProfile);
            }
        }
    }
}
