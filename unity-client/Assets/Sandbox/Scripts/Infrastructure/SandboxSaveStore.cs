using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>阶段一前端存档，不包含数据库和真实 AI 工作流检查点。</summary>
    [Serializable]
    public sealed class SandboxLocalSave
    {
        public int formatVersion = 1;
        public string saveId;
        public string name;
        public string savedAt;
        public SandboxSessionData snapshot;
    }

    /// <summary>桌面使用独立文件目录，网页使用当前浏览器的站点存储。</summary>
    public sealed class SandboxSaveStore
    {
        /// <summary>当前用户的前端存档目录。</summary>
        public string DirectoryPath { get; }

        /// <summary>构建当前应用独立的存档目录配置。</summary>
        public SandboxSaveStore()
        {
            DirectoryPath = Path.Combine(Application.persistentDataPath, "FrontendSandboxSaves");
        }

        /// <summary>创建独立完整快照并写入一个新的存档，不覆盖已有记录。</summary>
        /// <param name="name">用户输入的存档名称。</param>
        /// <param name="snapshot">当前前端演示状态。</param>
        /// <param name="message">成功说明或可读错误。</param>
        /// <returns>是否写入成功。</returns>
        public bool TrySave(string name, SandboxSessionData snapshot, out string message)
        {
            message = DemoSandboxViewSource.ValidateSession(snapshot);
            if (message.Length != 0) return false;
            if (string.IsNullOrWhiteSpace(name) || name.Trim().Length > 60)
            {
                message = "存档名称须填写 1 至 60 字";
                return false;
            }
            var save = new SandboxLocalSave
            {
                saveId = Guid.NewGuid().ToString("N"), name = name.Trim(), savedAt = DateTimeOffset.Now.ToString("O"),
                snapshot = JsonUtility.FromJson<SandboxSessionData>(JsonUtility.ToJson(snapshot))
            };
#if UNITY_WEBGL && !UNITY_EDITOR
            return SandboxBrowserStorage.TrySave(save, out message);
#else
            try
            {
                Directory.CreateDirectory(DirectoryPath);
                string finalPath = Path.Combine(DirectoryPath, save.saveId + ".json");
                string temporaryPath = finalPath + ".tmp";
                File.WriteAllText(temporaryPath, JsonUtility.ToJson(save, true));
                File.Move(temporaryPath, finalPath);
                message = "已保存本机前端快照；重启客户端后仍可读取";
                return true;
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
            {
                message = "存档写入失败：" + error.Message;
                return false;
            }
#endif
        }

        /// <summary>读取当前格式有效的存档，并报告损坏或无法访问的记录。</summary>
        /// <param name="warning">读取失败或损坏记录的摘要。</param>
        /// <returns>按保存时间倒序排列的有效存档。</returns>
        public SandboxLocalSave[] ReadAll(out string warning)
        {
            warning = string.Empty;
            var saves = new List<SandboxLocalSave>();
#if UNITY_WEBGL && !UNITY_EDITOR
            int invalidCount = 0;
            foreach (SandboxBrowserStorage.Record record in SandboxBrowserStorage.ReadAll(out warning))
            {
                if (!TryDeserialize(record.content, record.id, out SandboxLocalSave save)) { invalidCount++; continue; }
                saves.Add(save);
            }
            if (invalidCount > 0) warning += " " + invalidCount + " 条存档损坏或版本不支持，已保留原记录";
            return saves.OrderByDescending(save => DateTimeOffset.Parse(save.savedAt)).ToArray();
#else
            if (!Directory.Exists(DirectoryPath)) return Array.Empty<SandboxLocalSave>();
            try
            {
                int invalidCount = 0;
                foreach (string path in Directory.GetFiles(DirectoryPath, "*.json"))
                {
                    if (!TryRead(path, out SandboxLocalSave save)) { invalidCount++; continue; }
                    saves.Add(save);
                }
                if (invalidCount > 0) warning = invalidCount + " 条存档损坏或版本不支持，已保留原文件";
                return saves.OrderByDescending(save => DateTimeOffset.Parse(save.savedAt)).ToArray();
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
            {
                warning = "无法读取本机存档：" + error.Message;
                return Array.Empty<SandboxLocalSave>();
            }
#endif
        }

        /// <summary>安全读取单个独立记录，拒绝损坏格式和身份不匹配。</summary>
        /// <param name="path">枚举所得的本机路径。</param>
        /// <param name="save">通过校验的记录。</param>
        /// <returns>是否可读取。</returns>
        private static bool TryRead(string path, out SandboxLocalSave save)
        {
            save = null;
            try
            {
                return TryDeserialize(File.ReadAllText(path), Path.GetFileNameWithoutExtension(path), out save);
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
            {
                return false;
            }
        }

        /// <summary>统一校验桌面和浏览器的存档结构、身份及前端业务状态。</summary>
        /// <param name="content">存档原始内容。</param>
        /// <param name="expectedId">存储键对应的快照编号。</param>
        /// <param name="save">通过校验的独立记录。</param>
        /// <returns>是否能够安全恢复。</returns>
        private static bool TryDeserialize(string content, string expectedId, out SandboxLocalSave save)
        {
            save = null;
            if (string.IsNullOrEmpty(content)) return false;
            try
            {
                var candidate = JsonUtility.FromJson<SandboxLocalSave>(content);
                if (candidate == null || candidate.formatVersion != 1 || !Guid.TryParseExact(candidate.saveId, "N", out _)
                    || expectedId != candidate.saveId || string.IsNullOrWhiteSpace(candidate.name) || candidate.name.Length > 60
                    || !DateTimeOffset.TryParse(candidate.savedAt, out _) || DemoSandboxViewSource.ValidateSession(candidate.snapshot).Length != 0) return false;
                save = candidate;
                return true;
            }
            catch (ArgumentException)
            {
                return false;
            }
        }
    }
}
