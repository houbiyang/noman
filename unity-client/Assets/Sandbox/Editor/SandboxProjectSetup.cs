using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.Rendering.Universal;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>通过 Unity 官方编辑器 API 保存入口场景并构建桌面和网页客户端。</summary>
    public static class SandboxProjectSetup
    {
        private const string ScenePath = "Assets/Scenes/Sandbox.unity";

        /// <summary>创建独立沙盘场景并登记为唯一启用的构建入口。</summary>
        [MenuItem("穹图/阶段一/生成作战沙盘场景")]
        public static void PrepareScene()
        {
            if (!Application.isBatchMode && !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
            string directory = Path.GetDirectoryName(ScenePath);
            if (!Directory.Exists(directory)) Directory.CreateDirectory(directory);
            ConfigureBattlefieldShadows();
            const string cloudPath = "Assets/Sandbox/Resources/Sandbox/Cloud.mat";
            Material cloud = SandboxCloudLayer.CreateCloudMaterial();
            Material existingCloud = AssetDatabase.LoadAssetAtPath<Material>(cloudPath);
            if (existingCloud == null) AssetDatabase.CreateAsset(cloud, cloudPath);
            else
            {
                EditorUtility.CopySerialized(cloud, existingCloud);
                EditorUtility.SetDirty(existingCloud);
                UnityEngine.Object.DestroyImmediate(cloud);
            }
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            SandboxMapAssets.CreateSceneMap();
            new GameObject("QiongtuSandbox").AddComponent<SandboxBootstrap>();
            if (!EditorSceneManager.SaveScene(scene, ScenePath)) throw new InvalidOperationException("无法保存作战沙盘场景");
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
            PlayerSettings.companyName = "Qiongtu";
            PlayerSettings.productName = "穹图三维作战沙盘";
            PlayerSettings.defaultScreenWidth = 1600;
            PlayerSettings.defaultScreenHeight = 900;
            PlayerSettings.fullScreenMode = FullScreenMode.FullScreenWindow;
            PlayerSettings.resizableWindow = true;
            EditorSettings.serializationMode = SerializationMode.ForceText;
            AssetDatabase.SaveAssets();
            Debug.Log("[穹图] 作战沙盘场景已保存：" + ScenePath);
        }

        /// <summary>使地图总览也在主光阴影范围内，通过编辑器 API 持久化已有渲染配置。</summary>
        private static void ConfigureBattlefieldShadows()
        {
            foreach (string assetName in new[] { "Mobile_RPAsset", "PC_RPAsset" })
            {
                UniversalRenderPipelineAsset pipeline = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>("Assets/Settings/" + assetName + ".asset");
                if (pipeline == null) throw new InvalidOperationException("缺少既有 URP 配置：" + assetName);
                var settings = new SerializedObject(pipeline);
                settings.FindProperty("m_MainLightShadowsSupported").boolValue = true;
                settings.FindProperty("m_SoftShadowsSupported").boolValue = true;
                settings.ApplyModifiedPropertiesWithoutUndo();
                pipeline.shadowDistance = 650;
                pipeline.shadowCascadeCount = 4;
                pipeline.mainLightShadowmapResolution = 2048;
                pipeline.msaaSampleCount = 2;
                EditorUtility.SetDirty(pipeline);
            }
        }

        /// <summary>在图形编辑器中生成入口场景并进入运行模式。</summary>
        [MenuItem("穹图/阶段一/运行作战沙盘")]
        public static void RunPreview()
        {
            PrepareScene();
            if (SceneManager.GetActiveScene().path != ScenePath) return;
            EditorApplication.delayCall += EditorApplication.EnterPlaymode;
        }

        /// <summary>构建本机 Windows 客户端，失败时向自动化调用返回非零退出码。</summary>
        [MenuItem("穹图/阶段一/构建 Windows 客户端")]
        public static void BuildWindows()
        {
            PrepareScene();
            if (!Application.isBatchMode && SceneManager.GetActiveScene().path != ScenePath) return;
            Directory.CreateDirectory("Builds/Windows");
            var options = new BuildPlayerOptions
            {
                scenes = new[] { ScenePath }, locationPathName = "Builds/Windows/QiongtuSandbox.exe",
                target = BuildTarget.StandaloneWindows64, options = BuildOptions.None
            };
            BuildReport report = BuildPipeline.BuildPlayer(options);
            if (report.summary.result != BuildResult.Succeeded) throw new InvalidOperationException("Windows 构建失败：" + report.summary.result);
            Debug.Log("[穹图] Windows 客户端构建成功：Builds/Windows/QiongtuSandbox.exe");
        }

        /// <summary>构建可通过静态网页服务访问的三维沙盘，保留压缩解码回退以简化本地预览。</summary>
        [MenuItem("穹图/阶段一/构建网页版")]
        public static void BuildWeb()
        {
            if (!BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.WebGL, BuildTarget.WebGL))
                throw new InvalidOperationException("当前编辑器缺少 Web 构建支持模块");
            if (AssetDatabase.LoadAssetAtPath<Font>("Assets/Sandbox/Resources/Fonts/NotoSansCJKsc-Regular.otf") == null)
                throw new InvalidOperationException("缺少网页中文字体，请恢复工程中的字体资源");
            PrepareScene();
            if (!Application.isBatchMode && SceneManager.GetActiveScene().path != ScenePath) return;
            PlayerSettings.WebGL.template = "PROJECT:Qiongtu";
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Gzip;
            PlayerSettings.WebGL.decompressionFallback = true;
            PlayerSettings.WebGL.dataCaching = true;
            AssetDatabase.SaveAssets();
            var options = new BuildPlayerOptions
            {
                scenes = new[] { ScenePath }, locationPathName = "Builds/Web",
                target = BuildTarget.WebGL, options = BuildOptions.None
            };
            BuildReport report = BuildPipeline.BuildPlayer(options);
            if (report.summary.result != BuildResult.Succeeded)
                throw new InvalidOperationException("网页构建失败：" + report.summary.result);
            string notices = Path.Combine("Builds/Web", "ThirdPartyNotices");
            Directory.CreateDirectory(notices);
            File.Copy("Assets/Sandbox/Resources/Fonts/LICENSE.txt", Path.Combine(notices, "NotoSansCJK-LICENSE.txt"), true);
            Debug.Log("[穹图] 网页版构建成功：Builds/Web/index.html；请通过 HTTP 服务访问");
        }
    }
}
