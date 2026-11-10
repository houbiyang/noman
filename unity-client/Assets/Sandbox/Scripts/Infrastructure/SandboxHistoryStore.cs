using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    [Serializable] public sealed class SandboxHistoryObservation
    {
        public SandboxRole role;
        public string title, taskType;
        public int step;
        public SandboxUnitData[] units;
        public SandboxBarracksData[] barracks;
        public SandboxRecord[] records;
        public static SandboxHistoryObservation FromProjection(SandboxProjection projection) => new SandboxHistoryObservation {
            role = projection.role, title = projection.title, taskType = projection.taskType, step = projection.step,
            units = projection.units.Select(unit => unit.Copy()).ToArray(),
            barracks = projection.barracks,
            records = projection.records.Where(record => !record.isPrivatePlan).Select(record => record.Copy()).ToArray()
        };
    }

    /// <summary>只读席位观察档案，不是后端存档，不含令牌、写入上下文或双方私有建议。</summary>
    [Serializable] public sealed class SandboxMatchArchive
    {
        public int formatVersion = 1;
        public string id, title, savedAt, source, matchId, branchId, snapshotId, phase, automaticKey;
        public SandboxRole role;
        public SandboxHistoryObservation observation;
        public bool hasVictory;
        public SandboxVictoryEvidence victory;
    }

    public sealed class SandboxHistoryStore
    {
        public string DirectoryPath { get; }
        public SandboxHistoryStore(string directory = null) { DirectoryPath = directory ?? Path.Combine(Application.persistentDataPath, "MatchHistory-v1"); }
#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] private static extern string QiongtuHistoryWrite(string id, string content);
        [DllImport("__Internal")] private static extern string QiongtuHistoryRead();
        [Serializable] private sealed class Envelope { public SandboxBrowserStorage.Record[] records = Array.Empty<SandboxBrowserStorage.Record>(); public string warning = ""; }
#endif
        public bool TrySave(SandboxMatchArchive entry, out string message)
        {
            if (!Valid(entry, entry?.id)) { message = "历史记录格式不完整，未写入"; return false; }
            entry.hasVictory = entry.victory != null;
            string content = JsonUtility.ToJson(entry, true);
#if UNITY_WEBGL && !UNITY_EDITOR
            string error = QiongtuHistoryWrite(entry.id, content);
            message = string.IsNullOrEmpty(error) ? "已归档到当前浏览器；清理站点数据会移除记录" : "归档失败：" + error;
            return string.IsNullOrEmpty(error);
#else
            try
            {
                Directory.CreateDirectory(DirectoryPath);
                string path = Path.Combine(DirectoryPath, entry.id + ".json");
                File.WriteAllText(path + ".tmp", content); File.Move(path + ".tmp", path);
                message = "已归档到本机；重启后仍可查看"; return true;
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
            { message = "归档失败：" + error.Message; return false; }
#endif
        }

        public SandboxMatchArchive[] ReadAll(out string warning)
        {
            warning = ""; var result = new List<SandboxMatchArchive>(); int invalid = 0;
            try
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                var envelope = JsonUtility.FromJson<Envelope>(QiongtuHistoryRead());
                warning = envelope?.warning ?? "";
                foreach (var record in envelope?.records ?? Array.Empty<SandboxBrowserStorage.Record>())
                { if (Decode(record.content, record.id, out var entry)) result.Add(entry); else invalid++; }
#else
                if (!Directory.Exists(DirectoryPath)) return result.ToArray();
                foreach (string path in Directory.GetFiles(DirectoryPath, "*.json"))
                {
                    try { if (Decode(File.ReadAllText(path), Path.GetFileNameWithoutExtension(path), out var entry)) result.Add(entry); else invalid++; }
                    catch (Exception error) when (error is IOException || error is UnauthorizedAccessException) { invalid++; }
                }
#endif
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException || error is ArgumentException)
            { warning = "历史读取失败：" + error.Message; }
            if (invalid > 0) warning += " " + invalid + " 条损坏或不支持的记录已跳过，原件保留";
            return result.OrderByDescending(entry => DateTimeOffset.Parse(entry.savedAt)).ToArray();
        }

        private static bool Decode(string content, string id, out SandboxMatchArchive entry)
        {
            entry = null;
            try { var value = JsonUtility.FromJson<SandboxMatchArchive>(content); if (value != null) { if (!value.hasVictory) value.victory = null; if (value.source == "preview") value.observation = null; } if (!Valid(value, id)) return false; entry = value; return true; }
            catch (ArgumentException) { return false; }
        }
        private static bool Valid(SandboxMatchArchive value, string id)
        {
            return value != null && value.formatVersion == 1 && value.id == id && Guid.TryParseExact(id, "N", out _)
                && !string.IsNullOrWhiteSpace(value.title) && value.title.Length <= 100 && DateTimeOffset.TryParse(value.savedAt, out _)
                && Enum.IsDefined(typeof(SandboxRole), value.role)
                && (value.source == "preview" ? value.victory != null && value.victory.preview
                    : (value.source == "server" || value.source == "local") && value.observation != null
                        && value.observation.role == value.role && value.observation.units != null && value.observation.records != null
                        && value.observation.units.All(unit => unit != null && !string.IsNullOrEmpty(unit.id))
                        && value.observation.records.All(record => record != null && !record.isPrivatePlan && !string.IsNullOrEmpty(record.id))
                        && (value.victory == null || !value.victory.preview));
        }
    }
}
