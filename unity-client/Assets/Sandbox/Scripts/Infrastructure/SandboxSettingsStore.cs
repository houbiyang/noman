using System;
using System.IO;
using System.Runtime.InteropServices;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    public sealed class SandboxSettingsStore
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] private static extern string QiongtuSettingsRead();
        [DllImport("__Internal")] private static extern string QiongtuSettingsWrite(string content);
#endif
        [Serializable] private sealed class ReadResult { public string content = ""; public string warning = ""; }
        private readonly string path;
        public SandboxSettingsStore(string storageDirectory = null)
        { path = Path.Combine(storageDirectory ?? Application.persistentDataPath, "FrontendSandboxSettings.json"); }

        public SandboxClientSettings Load(out string warning)
        {
            warning = "";
            try
            {
                string content;
#if UNITY_WEBGL && !UNITY_EDITOR
                var result = JsonUtility.FromJson<ReadResult>(QiongtuSettingsRead());
                warning = result.warning ?? ""; content = result.content;
#else
                content = File.Exists(path) ? File.ReadAllText(path) : "";
#endif
                if (string.IsNullOrEmpty(content)) return new SandboxClientSettings();
                var candidate = JsonUtility.FromJson<SandboxClientSettings>(content);
                if (SandboxSettingsValidation.Validate(candidate).Count == 0) return candidate;
                warning = "设置记录损坏或版本不支持；原记录保留，本次使用默认值。保存前请检查。";
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException || error is ArgumentException)
            { warning = "无法读取本机设置；原记录保留，本次使用默认值。"; }
            return new SandboxClientSettings();
        }

        public bool TrySave(SandboxClientSettings settings, out string message)
        {
            if (SandboxSettingsValidation.Validate(settings).Count > 0) { message = "请先修正标记的字段"; return false; }
            try
            {
                string content = JsonUtility.ToJson(settings, true);
#if UNITY_WEBGL && !UNITY_EDITOR
                string error = QiongtuSettingsWrite(content);
                if (!string.IsNullOrEmpty(error)) { message = error; return false; }
#else
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.WriteAllText(path + ".tmp", content);
                if (File.Exists(path)) File.Replace(path + ".tmp", path, null);
                else File.Move(path + ".tmp", path);
#endif
                message = "已保存到本机 · 显示设置已生效 · AI 配置等待后端接入";
                return true;
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
            { message = "设置写入失败；当前草稿保留，请检查存储空间与访问权限"; return false; }
        }
    }
}
