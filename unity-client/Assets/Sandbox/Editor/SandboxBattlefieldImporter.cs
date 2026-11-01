using System;
using UnityEditor;

namespace Qiongtu.Sandbox.Editor
{
    /// <summary>仅配置原创战损资产的导入方式，保持米制模型、平铺纹理和正确法线。</summary>
    public sealed class SandboxBattlefieldImporter : AssetPostprocessor
    {
        private const string AssetPrefix = "Assets/Sandbox/Resources/Battlefield/";

        /// <summary>导入纹理时区分底色与切线法线，并生成远景需要的多级纹理。</summary>
        private void OnPreprocessTexture()
        {
            if (!assetPath.StartsWith(AssetPrefix, StringComparison.Ordinal)) return;
            var importer = (TextureImporter)assetImporter;
            bool normal = assetPath.EndsWith("_Normal.png", StringComparison.Ordinal);
            importer.textureType = normal ? TextureImporterType.NormalMap : TextureImporterType.Default;
            importer.sRGBTexture = !normal;
            importer.wrapMode = UnityEngine.TextureWrapMode.Repeat;
            importer.mipmapEnabled = true;
            importer.anisoLevel = 4;
            importer.maxTextureSize = 512;
            importer.textureCompression = TextureImporterCompression.Compressed;
            importer.isReadable = false;
        }

        /// <summary>只导入模型几何及材质身份，避免把 Blender 相机、灯光或动画带进地图。</summary>
        private void OnPreprocessModel()
        {
            if (!assetPath.StartsWith(AssetPrefix, StringComparison.Ordinal)) return;
            var importer = (ModelImporter)assetImporter;
            importer.globalScale = 1;
            importer.importCameras = false;
            importer.importLights = false;
            importer.importAnimation = false;
            importer.isReadable = false;
            importer.importNormals = ModelImporterNormals.Import;
            importer.importTangents = ModelImporterTangents.CalculateMikk;
        }
    }
}
