using UnityEngine;
using UnityEngine.Rendering;

namespace Qiongtu.Sandbox
{
    /// <summary>在虚构地图上方生成缓慢漂移的半透明云团，只参与环境显示。</summary>
    public sealed class SandboxCloudLayer : MonoBehaviour
    {
        private readonly Transform[] clouds = new Transform[9];
        private Material cloudMaterial;
        private Vector2 mapSize;

        /// <summary>使用工程已有 URP 着色器建立云层，不加载外部天气包。</summary>
        /// <param name="size">显示地图宽度与长度。</param>
        public void Initialize(Vector2 size)
        {
            mapSize = size;
            Material template = Resources.Load<Material>("Sandbox/Cloud");
            cloudMaterial = template != null ? new Material(template) : CreateCloudMaterial();
            BuildClouds(size);
        }

        /// <summary>生成供编辑器持久化的透明材质，保证网页构建保留所需着色器变体。</summary>
        /// <returns>由调用方持久化或释放的云材质。</returns>
        public static Material CreateCloudMaterial()
        {
            var cloudMaterial = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            cloudMaterial.SetColor("_BaseColor", new Color(0.78f, 0.81f, 0.81f, 0.17f));
            cloudMaterial.SetFloat("_Surface", 1);
            cloudMaterial.SetFloat("_SrcBlend", (float)BlendMode.SrcAlpha);
            cloudMaterial.SetFloat("_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
            cloudMaterial.SetFloat("_ZWrite", 0);
            cloudMaterial.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            cloudMaterial.renderQueue = (int)RenderQueue.Transparent;
            return cloudMaterial;
        }

        /// <summary>建立九组不带碰撞和阴影的云团。</summary>
        /// <param name="size">显示地图范围。</param>
        private void BuildClouds(Vector2 size)
        {
            for (int index = 0; index < clouds.Length; index++)
            {
                var group = new GameObject("Cloud_" + index);
                group.transform.SetParent(transform, false);
                group.transform.position = new Vector3(250 + index * 791 % (int)(size.x - 500), 600 + index % 3 * 130, 180 + index * 533 % (int)(size.y - 360));
                clouds[index] = group.transform;
                for (int puff = 0; puff < 5; puff++)
                {
                    GameObject sphere = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                    sphere.name = "CloudPuff_" + puff;
                    sphere.transform.SetParent(group.transform, false);
                    sphere.transform.localPosition = new Vector3((puff - 2) * 55, puff % 2 * 18f, (puff % 3 - 1) * 32);
                    sphere.transform.localScale = new Vector3(190 + puff % 2 * 50, 24 + puff % 3 * 12, 130 + puff % 2 * 40);
                    Destroy(sphere.GetComponent<Collider>());
                    Renderer renderer = sphere.GetComponent<Renderer>();
                    renderer.sharedMaterial = cloudMaterial;
                    renderer.shadowCastingMode = ShadowCastingMode.Off;
                    renderer.receiveShadows = false;
                }
            }
        }

        /// <summary>以显示时间漂移，不影响任务时间、视野或业务状态。</summary>
        private void Update()
        {
            foreach (Transform cloud in clouds)
            {
                if (cloud == null) continue;
                cloud.position += new Vector3(0.35f, 0, 0.08f) * Time.deltaTime;
                if (cloud.position.x > mapSize.x + 250)
                    cloud.position = new Vector3(-250, cloud.position.y, cloud.position.z);
            }
        }

        /// <summary>释放本云层专属材质。</summary>
        private void OnDestroy()
        {
            if (cloudMaterial != null) Destroy(cloudMaterial);
        }
    }
}
