-- 将规则移动与部署试走纳入既有事务、幂等回执和快照流程。
ALTER TABLE sandbox.game_commands DROP CONSTRAINT game_commands_command_type_check;
ALTER TABLE sandbox.game_commands ADD CONSTRAINT game_commands_command_type_check CHECK (
    command_type IN ('set_preparation', 'set_ready', 'set_tasks', 'start_game', 'pause', 'continue',
        'advance_step', 'review_proposal', 'trigger_condition', 'create_save', 'restore_save',
        'begin_exit', 'complete_exit', 'configure_army', 'deploy_army', 'move_unit', 'reload_demo_plan',
        'place_barracks', 'delete_unit', 'movement_order', 'advance_deployment_movement')
);
