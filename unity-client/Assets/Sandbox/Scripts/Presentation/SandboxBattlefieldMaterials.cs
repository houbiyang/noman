using System;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>将原创底色和法线贴图绑定至现有 URP Lit 材质，不引入外部着色器。</summary>
    public static class SandboxBattlefieldMaterials
    {
        /// <summary>按地图材质身份建立可持久化、可复用的写实表面。</summary>
        /// <param name="identifier">地图颜色身份或 Blender 材质名。</param>
        /// <param name="shader">工程已经保留的 URP Lit 着色器。</param>
        /// <param name="fallback">标记类材质使用的既有颜色。</param>
        /// <returns>由调用方持久化或销毁的材质。</returns>
        public static Material Create(string identifier, Shader shader, Color fallback)
        {
            var material = new Material(shader) { name = identifier, enableInstancing = true };
            string surface = ResolveSurface(identifier);
            material.SetColor("_BaseColor", surface == null ? fallback : SurfaceTint(identifier));
            material.SetFloat("_Smoothness", surface == "Water" ? 0.72f : surface == "Metal" ? 0.3f : 0.1f);
            material.SetFloat("_Metallic", surface == "Metal" ? 0.45f : 0);
            if (surface == null) return material;
            Texture2D baseMap = Resources.Load<Texture2D>("Battlefield/Textures/" + surface + "_Base");
            Texture2D normalMap = Resources.Load<Texture2D>("Battlefield/Textures/" + surface + "_Normal");
            if (baseMap == null || normalMap == null) throw new InvalidOperationException("缺少战损表面贴图：" + surface);
            material.SetTexture("_BaseMap", baseMap);
            material.SetTexture("_BumpMap", normalMap);
            material.SetFloat("_BumpScale", surface == "Water" ? 0.25f : 0.65f);
            material.EnableKeyword("_NORMALMAP");
            return material;
        }

        /// <summary>把地图和 Blender 的材质身份映射到共用纹理组。</summary>
        /// <param name="identifier">材质稳定名称。</param>
        /// <returns>纹理组名；颜色标记返回空。</returns>
        private static string ResolveSurface(string identifier)
        {
            if (identifier.StartsWith("Battle_", StringComparison.Ordinal)) return identifier.Substring(7);
            switch (identifier)
            {
                case "GroundLow": case "GroundHigh": case "Meadow": case "MeadowDry": case "FieldGreen": case "Crop": case "Grass": return "Ground";
                case "Earth": case "Bank": case "Mud": case "Soil": case "FieldGold": case "Gravel": return "Soil";
                case "Road": case "Asphalt": return "Road";
                case "Rock": case "Bridge": case "Paving": return "Concrete";
                case "Plaster": case "Wall": return "Plaster";
                case "Roof": return "Metal";
                case "Forest": case "ForestLight": return "Foliage";
                case "Wood": return "Timber";
                case "Tile": return "Tile";
                case "Water": return "Water";
                case "Ash": case "Glass": return "Ash";
                default: return null;
            }
        }

        /// <summary>只对田地和植被做轻度色差，避免整片地表出现硬边色块。</summary>
        /// <param name="identifier">地图或模型材质名。</param>
        /// <returns>用于调制纹理的颜色。</returns>
        private static Color SurfaceTint(string identifier)
        {
            if (identifier == "FieldGreen" || identifier == "Crop") return new Color(0.81f, 0.87f, 0.67f);
            if (identifier == "FieldGold") return new Color(1, 0.96f, 0.79f);
            if (identifier == "Forest") return new Color(0.78f, 0.85f, 0.72f);
            if (identifier == "Mud") return new Color(0.69f, 0.68f, 0.59f);
            if (identifier == "Asphalt") return new Color(0.52f, 0.56f, 0.58f);
            if (identifier == "Paving") return new Color(0.84f, 0.87f, 0.84f);
            if (identifier == "Gravel") return new Color(0.89f, 0.9f, 0.85f);
            if (identifier == "Grass") return new Color(0.69f, 0.79f, 0.58f);
            return Color.white;
        }
    }
}
