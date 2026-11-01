using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>标识场景中已保存的公开地图实例及其来源版本。</summary>
    public sealed class SandboxMapAnchor : MonoBehaviour
    {
        /// <summary>当前地图定义标识。</summary>
        public string mapId;

        /// <summary>生成此场景对象时绑定的布局哈希。</summary>
        public string contentHash;
    }
}
