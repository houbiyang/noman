using System;

namespace Qiongtu.Sandbox
{
    [Serializable]
    public sealed class SandboxBattlePreflightInput
    {
        public string mode;
        public bool probe_ai;
        public bool acknowledge_cost;
    }

    [Serializable]
    public sealed class SandboxBattleDependencyCheck
    {
        public string key, category, name, status, message;
    }

    [Serializable]
    public sealed class SandboxBattlePreflightResult
    {
        public string mode;
        public SandboxBattleDependencyCheck[] checks;
        public bool ready, manual_fallback_available, can_start, read_only;
    }
}
