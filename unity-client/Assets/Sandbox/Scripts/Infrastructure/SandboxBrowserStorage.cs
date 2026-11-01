using System;
using System.Runtime.InteropServices;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>通过浏览器同步存储保存网页前端快照，写入失败时不退出沙盘。</summary>
    public static class SandboxBrowserStorage
    {
        /// <summary>浏览器返回的单条原始存档，由存档层继续校验业务内容。</summary>
        [Serializable]
        public sealed class Record
        {
            public string id = string.Empty;
            public string content = string.Empty;
        }

        /// <summary>浏览器存储枚举结果及访问错误。</summary>
        [Serializable]
        private sealed class RecordList
        {
            public Record[] records = Array.Empty<Record>();
            public string warning = string.Empty;
        }

#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")]
        private static extern string QiongtuStorageWrite(string id, string content);

        [DllImport("__Internal")]
        private static extern string QiongtuStorageRead();

        [DllImport("__Internal")]
        private static extern void QiongtuExit();
#endif

        /// <summary>同步写入浏览器存储，只有成功后才允许继续退出。</summary>
        /// <param name="save">已通过业务校验的独立快照。</param>
        /// <param name="message">写入结果或配额、隐私模式等错误。</param>
        /// <returns>是否已写入当前浏览器的站点存储。</returns>
        public static bool TrySave(SandboxLocalSave save, out string message)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            string error = QiongtuStorageWrite(save.saveId, JsonUtility.ToJson(save));
            message = string.IsNullOrEmpty(error)
                ? "已保存到当前浏览器；使用同一网址和浏览器可恢复，清理站点数据会移除存档"
                : "浏览器存档写入失败：" + error;
            return string.IsNullOrEmpty(error);
#else
            message = "浏览器存储只在网页构建中启用";
            return false;
#endif
        }

        /// <summary>读取当前网址目录独立的原始存档，不访问其他站点或应用的数据。</summary>
        /// <param name="warning">浏览器拒绝存储访问时的说明。</param>
        /// <returns>等待业务校验的原始记录。</returns>
        public static Record[] ReadAll(out string warning)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            RecordList result = JsonUtility.FromJson<RecordList>(QiongtuStorageRead());
            warning = result.warning ?? string.Empty;
            return result.records ?? Array.Empty<Record>();
#else
            warning = "浏览器存储只在网页构建中启用";
            return Array.Empty<Record>();
#endif
        }

        /// <summary>通知网页释放 Unity 运行实例并显示重新进入入口，不强制关闭标签页。</summary>
        public static void Exit()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            QiongtuExit();
#endif
        }
    }
}
