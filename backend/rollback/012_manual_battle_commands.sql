-- 手工回滚012：先停止API/调度器/前端写入并验证备份，只能由数据库管理员执行。
-- 首选兼容性回退：旧代码保留新增CHECK允许值，不执行此文件。
-- 一旦出现新协议快照或命令，本脚本拒绝收窄，禁止删除不可变历史来绕过检查。
BEGIN;
SELECT pg_advisory_xact_lock(80644, 1103);
LOCK TABLE sandbox.game_commands IN ACCESS EXCLUSIVE MODE;
DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM infrastructure.schema_migrations
        WHERE version='012_manual_battle_commands'
          AND sha256='1edf1b717825c1173d4d3ac2e0853750db6b392e1729a120ae7faa7336c9af2f') THEN
        RAISE EXCEPTION '012未应用或摘要不匹配，停止回滚';
    END IF;
    IF EXISTS (SELECT 1 FROM sandbox.game_commands WHERE command_type IN ('battle_draft','battle_submit','battle_resolve'))
        OR EXISTS (SELECT 1 FROM sandbox.game_snapshots WHERE state_payload ? 'manual_battle') THEN
        RAISE EXCEPTION '已有新协议历史，必须保留兼容代码或恢复到独立备份库，不能收窄约束';
    END IF;
END; $guard$;
ALTER TABLE sandbox.game_commands DROP CONSTRAINT game_commands_command_type_check;
ALTER TABLE sandbox.game_commands ADD CONSTRAINT game_commands_command_type_check CHECK (
    command_type IN ('set_preparation', 'set_ready', 'set_tasks', 'start_game', 'pause', 'continue',
        'advance_step', 'review_proposal', 'trigger_condition', 'create_save', 'restore_save',
        'begin_exit', 'complete_exit', 'configure_army', 'deploy_army', 'move_unit', 'reload_demo_plan',
        'place_barracks', 'delete_unit', 'movement_order', 'advance_deployment_movement', 'spawn_from_barracks',
        'enable_coordination', 'review_agent_plan', 'settle_goal_stage', 'surrender', 'archive_match', 'enable_unit_interactions', 'unit_order')
);

-- 只有未产生新协议数据时，才撤销匹配摘要的迁移登记；在外部发布日志登记本次回滚。
DELETE FROM infrastructure.schema_migrations WHERE version='012_manual_battle_commands'
    AND sha256='1edf1b717825c1173d4d3ac2e0853750db6b392e1729a120ae7faa7336c9af2f';
COMMIT;
