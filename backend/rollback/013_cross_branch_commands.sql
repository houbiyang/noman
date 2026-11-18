-- 手工回退013；须先停写并验证备份。优先保留兼容CHECK、仅回退应用代码。
-- 仅限本地来源且没有五点历史/远端迁移/后续迁移的数据库；不得删除历史绕过保护。
BEGIN;
SELECT pg_advisory_xact_lock(80644, 1103);
LOCK TABLE sandbox.game_commands, sandbox.game_snapshots IN ACCESS EXCLUSIVE MODE;
DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM infrastructure.schema_migrations
        WHERE version='013_cross_branch_commands' AND sha256 IN ('821bf2eab1f35b2cf7e3f97d778c2ea8486a1c9b85b22c8fa22435934e6dee66', '61f88d1636c6c35db3b63e762ad07b63af4ed01e077f995a4cd61d21bad5b2cc')) THEN
        RAISE EXCEPTION '013未应用或摘要不匹配，停止回滚';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM infrastructure.schema_migrations
        WHERE version='012_manual_battle_commands'
          AND sha256 IN ('1edf1b717825c1173d4d3ac2e0853750db6b392e1729a120ae7faa7336c9af2f', 'f717b2c1bbe7cdf29903a8c29aa18338ee15fe3f4cc609d9bed9842c16a42558')) THEN
        RAISE EXCEPTION '缺少可回退的本地012基线，停止回滚';
    END IF;
    IF EXISTS (SELECT 1 FROM infrastructure.schema_migrations
        WHERE version IN ('011_fivepoint_commands', '012_fivepoint_planning')
           OR (version >= '013_' AND version <> '013_cross_branch_commands')) THEN
        RAISE EXCEPTION '存在远端或后续迁移，不能按本地012收窄约束';
    END IF;
    IF EXISTS (SELECT 1 FROM sandbox.game_commands WHERE command_type IN (
        'enable_fivepoint','submit_fivepoint_plan','retry_fivepoint_planning','abort_fivepoint','fivepoint_planning'))
        OR EXISTS (SELECT 1 FROM sandbox.game_snapshots
            WHERE state_payload ? 'fivepoint' OR state_payload->'manifest' ? 'fivepoint_version') THEN
        RAISE EXCEPTION '已有五点协议历史，必须保留兼容约束或恢复独立备份库';
    END IF;
END; $guard$;
ALTER TABLE sandbox.game_commands DROP CONSTRAINT game_commands_command_type_check;
ALTER TABLE sandbox.game_commands ADD CONSTRAINT game_commands_command_type_check CHECK (
    command_type IN ('set_preparation', 'set_ready', 'set_tasks', 'start_game', 'pause', 'continue',
        'advance_step', 'review_proposal', 'trigger_condition', 'create_save', 'restore_save',
        'begin_exit', 'complete_exit', 'configure_army', 'deploy_army', 'move_unit', 'reload_demo_plan',
        'place_barracks', 'delete_unit', 'movement_order', 'advance_deployment_movement', 'spawn_from_barracks',
        'enable_coordination', 'review_agent_plan', 'settle_goal_stage', 'surrender', 'archive_match',
        'enable_unit_interactions', 'unit_order', 'battle_draft', 'battle_submit', 'battle_resolve')
);

DELETE FROM infrastructure.schema_migrations WHERE version='013_cross_branch_commands'
    AND sha256 IN ('821bf2eab1f35b2cf7e3f97d778c2ea8486a1c9b85b22c8fa22435934e6dee66', '61f88d1636c6c35db3b63e762ad07b63af4ed01e077f995a4cd61d21bad5b2cc');
COMMIT;
