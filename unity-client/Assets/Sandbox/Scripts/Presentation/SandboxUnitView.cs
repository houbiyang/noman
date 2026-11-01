using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>可点选单位的稳定身份，不持有对方隐藏状态。</summary>
    public sealed class SandboxUnitView : MonoBehaviour
    {
        /// <summary>当前显示对象的稳定单位标识。</summary>
        public string UnitId { get; private set; }

        /// <summary>为新显示对象绑定身份。</summary>
        /// <param name="unitId">当前角色投影中的单位标识。</param>
        public void Initialize(string unitId) => UnitId = unitId;
    }
}
