-- 兼容本地手动战斗与 upstream/main@7be85a5 的命令历史；只扩展白名单。
-- 不导入远端5秒提交流程，不改变60秒/轮与60轮上限，不改地图/快照/存量对局。
-- 数据库允许保存历史命令不代表HTTP开放该命令或对应模式已通过发布验收。
ALTER TABLE sandbox.game_commands DROP CONSTRAINT game_commands_command_type_check;
ALTER TABLE sandbox.game_commands ADD CONSTRAINT game_commands_command_type_check CHECK (
    command_type IN ('set_preparation', 'set_ready', 'set_tasks', 'start_game', 'pause', 'continue',
        'advance_step', 'review_proposal', 'trigger_condition', 'create_save', 'restore_save',
        'begin_exit', 'complete_exit', 'configure_army', 'deploy_army', 'move_unit', 'reload_demo_plan',
        'place_barracks', 'delete_unit', 'movement_order', 'advance_deployment_movement', 'spawn_from_barracks',
        'enable_coordination', 'review_agent_plan', 'settle_goal_stage', 'surrender', 'archive_match',
        'enable_unit_interactions', 'unit_order', 'battle_draft', 'battle_submit', 'battle_resolve',
        'enable_fivepoint', 'submit_fivepoint_plan', 'retry_fivepoint_planning', 'abort_fivepoint',
        'fivepoint_planning')
);
