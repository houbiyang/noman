-- 当前数据库由初始化器明确指定；只在本项目独立 schema 内建表。
CREATE SCHEMA sandbox;
REVOKE ALL ON SCHEMA sandbox FROM PUBLIC;
SET LOCAL search_path = sandbox, pg_catalog;

CREATE DOMAIN sha256_text AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE DOMAIN object_json AS jsonb CHECK (jsonb_typeof(VALUE) = 'object');
CREATE DOMAIN nonempty_text AS text CHECK (length(btrim(VALUE)) > 0);

CREATE TABLE rule_versions (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    rule_code nonempty_text NOT NULL,
    version_label nonempty_text NOT NULL,
    schema_version nonempty_text NOT NULL,
    hash_format_version nonempty_text NOT NULL,
    definition object_json NOT NULL,
    payload_sha256 sha256_text NOT NULL,
    availability text NOT NULL CHECK (availability IN ('draft', 'active', 'retired')),
    is_demo boolean NOT NULL,
    UNIQUE (rule_code, version_label)
);

CREATE TABLE scenario_versions (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    scenario_code nonempty_text NOT NULL,
    version_label nonempty_text NOT NULL,
    rule_version_id uuid NOT NULL REFERENCES rule_versions(id),
    schema_version nonempty_text NOT NULL,
    hash_format_version nonempty_text NOT NULL,
    definition object_json NOT NULL,
    asset_manifest object_json NOT NULL,
    payload_sha256 sha256_text NOT NULL,
    availability text NOT NULL CHECK (availability IN ('draft', 'active', 'retired')),
    is_demo boolean NOT NULL,
    UNIQUE (scenario_code, version_label),
    UNIQUE (id, rule_version_id)
);

CREATE TABLE games (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_no nonempty_text NOT NULL UNIQUE,
    title nonempty_text NOT NULL,
    creation_request_key uuid NOT NULL UNIQUE,
    creation_payload_sha256 sha256_text NOT NULL,
    creation_hash_format_version nonempty_text NOT NULL,
    scenario_version_id uuid NOT NULL,
    rule_version_id uuid NOT NULL,
    active_branch_id uuid NOT NULL,
    head_snapshot_id uuid NOT NULL,
    control_revision bigint NOT NULL CHECK (control_revision >= 0),
    execution_epoch uuid NOT NULL,
    is_demo boolean NOT NULL,
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (scenario_version_id, rule_version_id)
        REFERENCES scenario_versions(id, rule_version_id)
);

CREATE TABLE game_members (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL REFERENCES games(id),
    role text NOT NULL CHECK (role IN ('host', 'red', 'blue')),
    display_name text,
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (game_id, role),
    UNIQUE (game_id, id)
);

CREATE TABLE access_sessions (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    member_id uuid NOT NULL,
    token_digest sha256_text NOT NULL UNIQUE,
    expires_at timestamptz(3) NOT NULL,
    revoked_at timestamptz(3),
    FOREIGN KEY (game_id, member_id) REFERENCES game_members(game_id, id),
    CHECK (expires_at > created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE TABLE game_branches (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL REFERENCES games(id),
    parent_branch_id uuid,
    fork_snapshot_id uuid,
    created_by_command_id uuid,
    UNIQUE (game_id, id),
    CHECK ((parent_branch_id IS NULL) = (fork_snapshot_id IS NULL)),
    CHECK ((parent_branch_id IS NULL) = (created_by_command_id IS NULL)),
    CHECK (parent_branch_id IS DISTINCT FROM id)
);

CREATE TABLE game_snapshots (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    world_revision bigint NOT NULL CHECK (world_revision >= 0),
    simulation_step integer NOT NULL CHECK (simulation_step BETWEEN 0 AND 60),
    phase text NOT NULL CHECK (phase IN ('preparing', 'running', 'paused', 'awaiting_confirmation', 'finished')),
    snapshot_kind text NOT NULL CHECK (snapshot_kind IN ('initial', 'command', 'fork', 'final')),
    schema_version nonempty_text NOT NULL,
    hash_format_version nonempty_text NOT NULL,
    state_payload object_json NOT NULL,
    payload_sha256 sha256_text NOT NULL,
    source_command_id uuid,
    FOREIGN KEY (game_id, branch_id) REFERENCES game_branches(game_id, id),
    UNIQUE (game_id, id),
    UNIQUE (game_id, branch_id, id),
    UNIQUE (game_id, branch_id, revision),
    UNIQUE (game_id, branch_id, id, revision),
    CHECK (state_payload ?& ARRAY['manifest', 'lifecycle', 'camps', 'units', 'formations',
        'tasks', 'routes', 'observations', 'logistics', 'conditions', 'decisions', 'scores']),
    CHECK (jsonb_typeof(state_payload->'manifest') = 'object'),
    CHECK (jsonb_typeof(state_payload->'lifecycle') = 'object'),
    CHECK ((state_payload->'lifecycle'->>'phase') IS NOT DISTINCT FROM phase),
    CHECK ((state_payload->'lifecycle'->>'simulation_step') IS NOT DISTINCT FROM simulation_step::text),
    CHECK ((snapshot_kind = 'initial') = (source_command_id IS NULL))
);

CREATE TABLE game_commands (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    origin_branch_id uuid NOT NULL,
    actor_member_id uuid NOT NULL,
    request_key uuid NOT NULL,
    request_hash sha256_text NOT NULL,
    hash_format_version nonempty_text NOT NULL,
    command_type text NOT NULL CHECK (command_type IN ('set_preparation', 'set_ready', 'set_tasks',
        'start_game', 'pause', 'continue', 'advance_step', 'review_proposal', 'trigger_condition',
        'create_save', 'restore_save', 'begin_exit', 'complete_exit')),
    expected_control_revision bigint NOT NULL CHECK (expected_control_revision >= 0),
    base_snapshot_id uuid NOT NULL,
    request_payload object_json NOT NULL,
    source_kind text NOT NULL CHECK (source_kind IN ('human', 'workflow', 'scheduler')),
    source_ai_run_id uuid,
    source_proposal_id uuid,
    execution_epoch uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('accepted', 'running', 'succeeded', 'rejected', 'failed')),
    result_branch_id uuid,
    result_snapshot_id uuid,
    result_payload object_json,
    error_payload object_json,
    completed_at timestamptz(3),
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (game_id, id),
    UNIQUE (game_id, origin_branch_id, actor_member_id, request_key),
    FOREIGN KEY (game_id, actor_member_id) REFERENCES game_members(game_id, id),
    FOREIGN KEY (game_id, origin_branch_id, base_snapshot_id)
        REFERENCES game_snapshots(game_id, branch_id, id),
    FOREIGN KEY (game_id, result_branch_id, result_snapshot_id)
        REFERENCES game_snapshots(game_id, branch_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((result_branch_id IS NULL) = (result_snapshot_id IS NULL)),
    CHECK ((status IN ('succeeded', 'rejected', 'failed')) = (completed_at IS NOT NULL)),
    CHECK ((status = 'succeeded' AND result_snapshot_id IS NOT NULL AND result_payload IS NOT NULL AND error_payload IS NULL)
        OR (status IN ('rejected', 'failed') AND result_snapshot_id IS NULL AND result_payload IS NULL AND error_payload IS NOT NULL)
        OR (status IN ('accepted', 'running') AND result_snapshot_id IS NULL AND result_payload IS NULL AND error_payload IS NULL)),
    CHECK (source_kind <> 'workflow' OR source_ai_run_id IS NOT NULL)
);

ALTER TABLE games ADD CONSTRAINT games_active_branch_fk
    FOREIGN KEY (id, active_branch_id) REFERENCES game_branches(game_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE games ADD CONSTRAINT games_head_snapshot_fk
    FOREIGN KEY (id, active_branch_id, head_snapshot_id)
    REFERENCES game_snapshots(game_id, branch_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE game_branches ADD CONSTRAINT branches_fork_snapshot_fk
    FOREIGN KEY (game_id, parent_branch_id, fork_snapshot_id)
    REFERENCES game_snapshots(game_id, branch_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE game_branches ADD CONSTRAINT branches_source_command_fk
    FOREIGN KEY (game_id, created_by_command_id) REFERENCES game_commands(game_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE game_snapshots ADD CONSTRAINT snapshots_source_command_fk
    FOREIGN KEY (game_id, source_command_id) REFERENCES game_commands(game_id, id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE game_events (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    snapshot_revision bigint NOT NULL CHECK (snapshot_revision >= 0),
    command_id uuid NOT NULL,
    event_index integer NOT NULL CHECK (event_index >= 0),
    fact_key nonempty_text NOT NULL,
    event_type nonempty_text NOT NULL,
    simulation_step integer NOT NULL CHECK (simulation_step BETWEEN 0 AND 60),
    visibility text NOT NULL CHECK (visibility IN ('public', 'red', 'blue', 'host')),
    payload object_json NOT NULL,
    FOREIGN KEY (game_id, branch_id, snapshot_id, snapshot_revision)
        REFERENCES game_snapshots(game_id, branch_id, id, revision),
    FOREIGN KEY (game_id, command_id) REFERENCES game_commands(game_id, id),
    UNIQUE (snapshot_id, event_index),
    UNIQUE (game_id, branch_id, fact_key)
);

CREATE TABLE game_saves (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    save_kind text NOT NULL CHECK (save_kind IN ('auto', 'manual', 'final')),
    display_name text,
    created_by_member_id uuid,
    source_command_id uuid,
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (game_id, branch_id, snapshot_id) REFERENCES game_snapshots(game_id, branch_id, id),
    FOREIGN KEY (game_id, created_by_member_id) REFERENCES game_members(game_id, id),
    FOREIGN KEY (game_id, source_command_id) REFERENCES game_commands(game_id, id),
    CHECK (save_kind = 'auto' OR source_command_id IS NOT NULL),
    CHECK (save_kind <> 'manual' OR created_by_member_id IS NOT NULL)
);
CREATE UNIQUE INDEX saves_auto_uq ON game_saves(game_id, branch_id) WHERE save_kind = 'auto';
CREATE UNIQUE INDEX saves_final_uq ON game_saves(game_id, branch_id) WHERE save_kind = 'final';
CREATE UNIQUE INDEX saves_manual_command_uq ON game_saves(source_command_id) WHERE save_kind = 'manual';

CREATE TABLE ai_runs (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    base_snapshot_id uuid NOT NULL,
    side text NOT NULL CHECK (side IN ('red', 'blue')),
    execution_epoch uuid NOT NULL,
    agent_role text NOT NULL CHECK (agent_role IN ('commander', 'recon', 'ground', 'support')),
    input_view object_json NOT NULL,
    model_manifest object_json NOT NULL,
    knowledge_manifest object_json NOT NULL,
    input_view_sha256 sha256_text NOT NULL,
    hash_format_version nonempty_text NOT NULL,
    checkpoint_thread_id nonempty_text NOT NULL UNIQUE,
    checkpoint_ref object_json,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'waiting_human', 'succeeded', 'failed', 'stale', 'cancelled')),
    output_payload object_json,
    error_payload object_json,
    usage object_json,
    started_at timestamptz(3),
    finished_at timestamptz(3),
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (game_id, branch_id, base_snapshot_id) REFERENCES game_snapshots(game_id, branch_id, id),
    UNIQUE (game_id, id),
    UNIQUE (game_id, branch_id, id),
    UNIQUE (game_id, branch_id, side, id)
);

CREATE TABLE proposals (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    base_snapshot_id uuid NOT NULL,
    side text NOT NULL CHECK (side IN ('red', 'blue')),
    execution_epoch uuid NOT NULL,
    ai_run_id uuid,
    source_proposal_id uuid,
    proposal_kind text NOT NULL CHECK (proposal_kind IN ('initial', 'major_adjustment', 'routine_adjustment', 'restored')),
    status text NOT NULL CHECK (status IN ('draft', 'ready', 'pending_review', 'adopted', 'rejected', 'stale', 'cancelled')),
    requires_confirmation boolean NOT NULL,
    schema_version nonempty_text NOT NULL,
    content_payload object_json NOT NULL,
    validation_payload object_json NOT NULL,
    based_on_world_revision bigint NOT NULL CHECK (based_on_world_revision >= 0),
    input_view_sha256 sha256_text NOT NULL,
    hash_format_version nonempty_text NOT NULL,
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (game_id, id),
    UNIQUE (game_id, branch_id, id),
    UNIQUE (game_id, side, id),
    FOREIGN KEY (game_id, branch_id, base_snapshot_id) REFERENCES game_snapshots(game_id, branch_id, id),
    FOREIGN KEY (game_id, branch_id, side, ai_run_id) REFERENCES ai_runs(game_id, branch_id, side, id),
    FOREIGN KEY (game_id, side, source_proposal_id) REFERENCES proposals(game_id, side, id),
    CHECK (ai_run_id IS NOT NULL OR source_proposal_id IS NOT NULL),
    CHECK (source_proposal_id IS DISTINCT FROM id),
    CHECK (proposal_kind NOT IN ('initial', 'major_adjustment') OR requires_confirmation)
);
ALTER TABLE game_commands ADD CONSTRAINT commands_source_run_fk
    FOREIGN KEY (game_id, source_ai_run_id) REFERENCES ai_runs(game_id, id);
ALTER TABLE game_commands ADD CONSTRAINT commands_source_proposal_fk
    FOREIGN KEY (game_id, source_proposal_id) REFERENCES proposals(game_id, id);

CREATE TABLE proposal_reviews (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    proposal_id uuid NOT NULL UNIQUE,
    reviewer_member_id uuid NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approve', 'modify_approve', 'reject')),
    submitted_changes object_json NOT NULL,
    adopted_plan object_json,
    command_id uuid NOT NULL,
    result_snapshot_id uuid NOT NULL,
    FOREIGN KEY (game_id, branch_id, proposal_id) REFERENCES proposals(game_id, branch_id, id),
    FOREIGN KEY (game_id, reviewer_member_id) REFERENCES game_members(game_id, id),
    FOREIGN KEY (game_id, command_id) REFERENCES game_commands(game_id, id),
    FOREIGN KEY (game_id, branch_id, result_snapshot_id) REFERENCES game_snapshots(game_id, branch_id, id),
    CHECK ((decision = 'reject') = (adopted_plan IS NULL)),
    CHECK (decision = 'modify_approve' OR submitted_changes = '{}'::jsonb)
);

CREATE TABLE knowledge_documents (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    document_code nonempty_text NOT NULL UNIQUE,
    title nonempty_text NOT NULL,
    source_description text,
    is_demo boolean NOT NULL,
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE document_versions (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    document_id uuid NOT NULL REFERENCES knowledge_documents(id),
    version_label nonempty_text NOT NULL,
    storage_key nonempty_text NOT NULL UNIQUE,
    original_filename nonempty_text NOT NULL,
    media_type nonempty_text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    sha256 sha256_text NOT NULL,
    scope_game_id uuid REFERENCES games(id),
    visibility text NOT NULL CHECK (visibility IN ('shared', 'red', 'blue', 'host')),
    file_status text NOT NULL CHECK (file_status IN ('writing', 'ready', 'failed', 'missing')),
    parse_status text NOT NULL CHECK (parse_status IN ('pending', 'parsing', 'ready', 'failed')),
    review_status text NOT NULL CHECK (review_status IN ('pending', 'approved', 'rejected')),
    retrieval_status text NOT NULL CHECK (retrieval_status IN ('pending', 'building', 'ready', 'failed', 'disabled')),
    active_parse_revision integer CHECK (active_parse_revision > 0),
    next_parse_revision integer NOT NULL CHECK (next_parse_revision > 0),
    processing_generation bigint NOT NULL CHECK (processing_generation >= 0),
    parser_manifest object_json NOT NULL,
    retrieval_manifest object_json,
    publication_revision bigint NOT NULL CHECK (publication_revision >= 0),
    is_demo boolean NOT NULL,
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (document_id, version_label),
    CHECK (scope_game_id IS NOT NULL OR visibility = 'shared'),
    CHECK (active_parse_revision IS NULL OR next_parse_revision > active_parse_revision),
    CHECK (retrieval_status <> 'ready' OR (active_parse_revision IS NOT NULL AND retrieval_manifest IS NOT NULL
        AND file_status = 'ready' AND review_status = 'approved'))
);

CREATE TABLE document_chunks (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    document_version_id uuid NOT NULL REFERENCES document_versions(id),
    parse_revision integer NOT NULL CHECK (parse_revision > 0),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    parent_chunk_id uuid,
    section_path jsonb NOT NULL CHECK (jsonb_typeof(section_path) = 'array'),
    locator object_json NOT NULL,
    content text NOT NULL,
    content_sha256 sha256_text NOT NULL,
    parser_manifest object_json NOT NULL,
    parser_manifest_sha256 sha256_text NOT NULL,
    lexical_terms tsvector,
    UNIQUE (document_version_id, parse_revision, ordinal),
    UNIQUE (document_version_id, parse_revision, id),
    UNIQUE (document_version_id, id),
    UNIQUE (id, content_sha256),
    FOREIGN KEY (document_version_id, parse_revision, parent_chunk_id)
        REFERENCES document_chunks(document_version_id, parse_revision, id),
    CHECK (parent_chunk_id IS DISTINCT FROM id)
);

CREATE TABLE embedding_spaces (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    space_code nonempty_text NOT NULL UNIQUE,
    provider_code nonempty_text NOT NULL,
    model_name nonempty_text NOT NULL,
    model_revision nonempty_text NOT NULL,
    dimension integer NOT NULL CHECK (dimension > 0),
    distance_metric text NOT NULL CHECK (distance_metric IN ('cosine', 'l2', 'inner_product')),
    preprocessing_manifest object_json NOT NULL,
    availability text NOT NULL CHECK (availability IN ('active', 'retired')),
    UNIQUE (id, dimension)
);

CREATE TABLE proposal_evidence (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    game_id uuid NOT NULL,
    proposal_id uuid NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    source_type text NOT NULL CHECK (source_type IN ('document', 'report', 'rule')),
    document_version_id uuid REFERENCES document_versions(id),
    chunk_id uuid,
    observation_snapshot_id uuid,
    report_object_id nonempty_text,
    rule_version_id uuid REFERENCES rule_versions(id),
    locator object_json NOT NULL,
    excerpt text NOT NULL,
    excerpt_sha256 sha256_text NOT NULL,
    source_manifest object_json NOT NULL,
    UNIQUE (proposal_id, position),
    FOREIGN KEY (game_id, proposal_id) REFERENCES proposals(game_id, id),
    FOREIGN KEY (document_version_id, chunk_id) REFERENCES document_chunks(document_version_id, id),
    FOREIGN KEY (game_id, observation_snapshot_id) REFERENCES game_snapshots(game_id, id),
    CHECK ((source_type = 'document' AND document_version_id IS NOT NULL AND observation_snapshot_id IS NULL
        AND report_object_id IS NULL AND rule_version_id IS NULL)
        OR (source_type = 'report' AND observation_snapshot_id IS NOT NULL AND report_object_id IS NOT NULL
        AND document_version_id IS NULL AND chunk_id IS NULL AND rule_version_id IS NULL)
        OR (source_type = 'rule' AND rule_version_id IS NOT NULL AND document_version_id IS NULL
        AND chunk_id IS NULL AND observation_snapshot_id IS NULL AND report_object_id IS NULL))
);

CREATE TABLE background_jobs (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    job_type text NOT NULL CHECK (job_type IN ('analysis', 'execute_command', 'parse_document', 'index_document')),
    subject_key nonempty_text NOT NULL,
    game_id uuid,
    branch_id uuid,
    ai_run_id uuid,
    command_id uuid,
    execution_epoch uuid,
    document_version_id uuid REFERENCES document_versions(id),
    target_parse_revision integer CHECK (target_parse_revision > 0),
    target_processing_generation bigint CHECK (target_processing_generation >= 0),
    status text NOT NULL CHECK (status IN ('queued', 'running', 'waiting_retry', 'waiting_human', 'succeeded', 'failed', 'cancelled')),
    payload object_json NOT NULL,
    result_payload object_json,
    error_payload object_json,
    attempt_count integer NOT NULL CHECK (attempt_count >= 0),
    claim_generation bigint NOT NULL CHECK (claim_generation >= 0),
    lease_owner nonempty_text,
    lease_until timestamptz(3),
    available_at timestamptz(3) NOT NULL,
    updated_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (game_id, branch_id) REFERENCES game_branches(game_id, id),
    FOREIGN KEY (game_id, branch_id, ai_run_id) REFERENCES ai_runs(game_id, branch_id, id),
    FOREIGN KEY (game_id, command_id) REFERENCES game_commands(game_id, id),
    CHECK ((status = 'running' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL)
        OR (status <> 'running' AND lease_owner IS NULL AND lease_until IS NULL)),
    CHECK ((job_type IN ('analysis', 'execute_command') AND game_id IS NOT NULL AND branch_id IS NOT NULL
        AND execution_epoch IS NOT NULL AND document_version_id IS NULL AND target_parse_revision IS NULL
        AND target_processing_generation IS NULL)
        OR (job_type IN ('parse_document', 'index_document') AND game_id IS NULL AND branch_id IS NULL
        AND execution_epoch IS NULL AND ai_run_id IS NULL AND command_id IS NULL AND document_version_id IS NOT NULL
        AND target_parse_revision IS NOT NULL AND target_processing_generation IS NOT NULL)),
    CHECK (job_type <> 'analysis' OR ai_run_id IS NOT NULL),
    CHECK (job_type <> 'execute_command' OR command_id IS NOT NULL)
);
CREATE UNIQUE INDEX jobs_active_subject_uq ON background_jobs(job_type, subject_key)
    WHERE status IN ('queued', 'running', 'waiting_retry', 'waiting_human');
CREATE UNIQUE INDEX jobs_command_uq ON background_jobs(command_id) WHERE command_id IS NOT NULL;
CREATE INDEX jobs_claim_idx ON background_jobs(available_at, created_at, id) WHERE status IN ('queued', 'waiting_retry');
CREATE INDEX jobs_expired_idx ON background_jobs(lease_until, id) WHERE status = 'running';
CREATE INDEX jobs_run_idx ON background_jobs(ai_run_id) WHERE ai_run_id IS NOT NULL;
CREATE INDEX jobs_document_idx ON background_jobs(document_version_id) WHERE document_version_id IS NOT NULL;
CREATE INDEX games_list_idx ON games(created_at, id);
CREATE INDEX sessions_member_idx ON access_sessions(game_id, member_id);
CREATE INDEX snapshots_step_idx ON game_snapshots(game_id, branch_id, simulation_step, revision);
CREATE INDEX commands_status_idx ON game_commands(game_id, status, created_at);
CREATE INDEX events_timeline_idx ON game_events(game_id, branch_id, snapshot_revision, event_index);
CREATE INDEX events_command_idx ON game_events(game_id, command_id);
CREATE INDEX saves_list_idx ON game_saves(game_id, created_at, id);
CREATE INDEX runs_status_idx ON ai_runs(game_id, branch_id, side, status, created_at);
CREATE INDEX proposals_status_idx ON proposals(game_id, branch_id, side, status, created_at);
CREATE INDEX versions_scope_idx ON document_versions(scope_game_id, visibility, retrieval_status);

-- 内容型历史只能追加；可变表只允许明确列发生变化。
CREATE FUNCTION guard_record_mutation() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, sandbox AS $function$
DECLARE
    allowed_columns text[] := TG_ARGV;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '历史记录禁止删除：%', TG_TABLE_NAME USING ERRCODE = '42501';
    END IF;
    IF TG_NARGS = 0 OR (to_jsonb(NEW) - allowed_columns) IS DISTINCT FROM (to_jsonb(OLD) - allowed_columns) THEN
        RAISE EXCEPTION '禁止修改不可变内容：%', TG_TABLE_NAME USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER rule_versions_guard BEFORE UPDATE OR DELETE ON rule_versions
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('availability');
CREATE TRIGGER scenario_versions_guard BEFORE UPDATE OR DELETE ON scenario_versions
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('availability');
CREATE TRIGGER games_guard BEFORE UPDATE OR DELETE ON games
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('title', 'active_branch_id', 'head_snapshot_id', 'control_revision', 'execution_epoch', 'updated_at');
CREATE TRIGGER game_members_guard BEFORE UPDATE OR DELETE ON game_members
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('display_name', 'updated_at');
CREATE TRIGGER access_sessions_guard BEFORE UPDATE OR DELETE ON access_sessions
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('revoked_at');
CREATE TRIGGER game_branches_guard BEFORE UPDATE OR DELETE ON game_branches
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation();
CREATE TRIGGER game_snapshots_guard BEFORE UPDATE OR DELETE ON game_snapshots
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation();
CREATE TRIGGER game_events_guard BEFORE UPDATE OR DELETE ON game_events
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation();
CREATE FUNCTION guard_save_mutation() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, sandbox AS $function$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.save_kind <> 'auto' THEN
        RAISE EXCEPTION '手动和结束存档不可覆盖，所有存档禁止删除' USING ERRCODE = '42501';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['snapshot_id', 'source_command_id', 'updated_at'])
        IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['snapshot_id', 'source_command_id', 'updated_at']) THEN
        RAISE EXCEPTION '自动存档身份不可修改' USING ERRCODE = '42501';
    END IF;
    IF NEW.source_command_id IS NULL THEN
        RAISE EXCEPTION '自动存档更新必须关联实际命令' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;
CREATE TRIGGER game_saves_guard BEFORE UPDATE OR DELETE ON game_saves
    FOR EACH ROW EXECUTE FUNCTION guard_save_mutation();
CREATE TRIGGER proposal_reviews_guard BEFORE UPDATE OR DELETE ON proposal_reviews
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation();
CREATE TRIGGER proposal_evidence_guard BEFORE UPDATE OR DELETE ON proposal_evidence
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation();
CREATE TRIGGER document_chunks_guard BEFORE UPDATE OR DELETE ON document_chunks
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation();
CREATE TRIGGER game_commands_guard BEFORE UPDATE OR DELETE ON game_commands
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('status', 'result_branch_id', 'result_snapshot_id', 'result_payload', 'error_payload', 'completed_at', 'updated_at');
CREATE TRIGGER ai_runs_guard BEFORE UPDATE OR DELETE ON ai_runs
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('checkpoint_ref', 'status', 'output_payload', 'error_payload', 'usage', 'started_at', 'finished_at', 'updated_at');
CREATE TRIGGER proposals_guard BEFORE UPDATE OR DELETE ON proposals
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('status', 'updated_at');
CREATE TRIGGER knowledge_documents_guard BEFORE UPDATE OR DELETE ON knowledge_documents
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('title', 'source_description', 'updated_at');
CREATE TRIGGER document_versions_guard BEFORE UPDATE OR DELETE ON document_versions
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('file_status', 'parse_status', 'review_status', 'retrieval_status',
        'active_parse_revision', 'next_parse_revision', 'processing_generation', 'parser_manifest', 'retrieval_manifest', 'publication_revision', 'updated_at');
CREATE TRIGGER embedding_spaces_guard BEFORE UPDATE OR DELETE ON embedding_spaces
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('availability');
CREATE TRIGGER background_jobs_guard BEFORE UPDATE OR DELETE ON background_jobs
    FOR EACH ROW EXECUTE FUNCTION guard_record_mutation('status', 'result_payload', 'error_payload', 'attempt_count',
        'claim_generation', 'lease_owner', 'lease_until', 'available_at', 'updated_at');

COMMENT ON SCHEMA sandbox IS '穹图沙盘业务结构；角色投影、领域规则与完整 JSON 校验由后端实现';
COMMENT ON COLUMN scenario_versions.definition IS '版本化场景；map 保存地形、道路、阻挡和设施的规则定义';
COMMENT ON COLUMN rule_versions.definition IS '规则定义；map_policies 和 unit_profiles 为地形交互及兵种能力，未确认参数不填正式默认值';
COMMENT ON COLUMN game_snapshots.state_payload IS '完整权威快照；SQL 只验证顶层与生命周期，领域对象及守恒规则仍需应用校验';
COMMENT ON COLUMN game_events.event_type IS '事实代码；当前非空检查不代表任意事件已得到执行授权';
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sandbox FROM PUBLIC;
