using System;

namespace Qiongtu.Sandbox
{
    [Serializable] public sealed class SandboxManualQuantities { public int recon, ground, support; }
    [Serializable] public sealed class SandboxManualDeployment
    {
        public string unit_type, surface_id;
        public int ordinal;
        public SandboxReplayPosition position;
    }
    [Serializable] public sealed class SandboxManualCampDeployment
    {
        public string unit_type, surface_id, service_surface_id;
        public SandboxReplayPosition position, service_anchor;
    }
    [Serializable] public sealed class SandboxManualSideSetup
    {
        public SandboxManualQuantities quantities;
        public SandboxManualDeployment[] units;
        public SandboxManualCampDeployment[] camps;
    }
    [Serializable] public sealed class SandboxManualCreatePayload
    {
        public string title, mode = "manual";
        public string rule_version_id, scenario_version_id, capacity_version_id;
        public string rule_sha256, scenario_sha256, capacity_sha256;
        public SandboxManualSideSetup blue, red;
    }
    [Serializable] public sealed class SandboxManualPendingCreation
    {
        public string request_key;
        public SandboxManualCreatePayload payload;
    }
    [Serializable] public sealed class SandboxManualSetupMap
    {
        public string map_id, content_hash, source_sha256;
        public int width_m, length_m;
    }
    [Serializable] public sealed class SandboxManualSetupCapacity
    {
        public int minimum_per_type, maximum_per_type, minimum_per_side, maximum_per_side, maximum_per_match;
        public string scope, overflow_policy;
        public bool asymmetric_allowed, formal_release_certified;
    }
    [Serializable] public sealed class SandboxManualSetupCatalogue
    {
        public string mode, message, release_scope;
        public bool competitive_ready;
        public string[] limitations;
        public string rule_version_id, scenario_version_id, capacity_version_id;
        public string rule_sha256, scenario_sha256, capacity_sha256;
        public int round_seconds, maximum_rounds;
        public bool can_create;
        public SandboxBattleDependencyCheck[] checks;
        public SandboxManualSetupMap map;
        public SandboxManualSetupCapacity capacity;
        public SandboxManualPendingCreation pending_creation;
        public SandboxManualCreatePayload recommended_setup;
    }
    [Serializable] public sealed class SandboxManualLocalInput
    {
        public string game_id, map_hash, capture_layout_sha256;
    }
    [Serializable] public sealed class SandboxManualCreateInput
    {
        public string game_id, map_hash, capture_layout_sha256, request_key;
        public SandboxManualCreatePayload payload;
    }
    [Serializable] public sealed class SandboxManualValidateInput
    {
        public string game_id, map_hash, capture_layout_sha256;
        public SandboxManualCreatePayload payload;
    }
    [Serializable] public sealed class SandboxManualValidation { public bool valid; }
    [Serializable] public sealed class SandboxManualRecoverInput
    {
        public string game_id, map_hash, capture_layout_sha256, request_key;
    }
}

namespace Qiongtu.Sandbox
{
    [Serializable] public sealed class SandboxCampPlacementRequest
    {
        public string side, unit_type;
        public int x_mm, z_mm, count;
        public SandboxManualCampDeployment[] other_camps = Array.Empty<SandboxManualCampDeployment>();
        public SandboxManualDeployment[] other_units = Array.Empty<SandboxManualDeployment>();
    }
    [Serializable] public sealed class SandboxCampPlacementInput
    {
        public string game_id, map_hash, capture_layout_sha256;
        public SandboxCampPlacementRequest placement;
    }
    [Serializable] public sealed class SandboxCampPlacementResult
    {
        public SandboxManualCampDeployment camp;
        public SandboxManualDeployment[] units;
        public int count;
        public string message;
    }
    [Serializable] public sealed class SandboxStagedArmy
    {
        public int[] counts = { 1, 1, 1 };
        public bool[] generated = new bool[3];
        public SandboxCampPlacementResult[] slots = new SandboxCampPlacementResult[3];
        public bool ready;
    }
    [Serializable] public sealed class SandboxStagedDeployment
    {
        public string source_game, scenario_sha256;
        public SandboxStagedArmy blue = new SandboxStagedArmy(), red = new SandboxStagedArmy();
    }
}
