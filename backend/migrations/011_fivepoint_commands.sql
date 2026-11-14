-- 五点模式使用720个五秒步；旧模式快照仍受原60步限制，不重算历史成绩。
ALTER TABLE sandbox.game_snapshots DROP CONSTRAINT game_snapshots_simulation_step_check;
ALTER TABLE sandbox.game_snapshots ADD CONSTRAINT game_snapshots_simulation_step_check CHECK (
    simulation_step BETWEEN 0 AND CASE
        WHEN state_payload->'manifest'->>'fivepoint_version' = 'qinglan-fivepoint-custom-v1.1' THEN 720
        ELSE 60 END
);
ALTER TABLE sandbox.game_events DROP CONSTRAINT game_events_simulation_step_check;
ALTER TABLE sandbox.game_events ADD CONSTRAINT game_events_simulation_step_check CHECK (simulation_step BETWEEN 0 AND 720);
ALTER TABLE sandbox.match_archives DROP CONSTRAINT match_archives_simulation_step_check;
ALTER TABLE sandbox.match_archives ADD CONSTRAINT match_archives_simulation_step_check CHECK (simulation_step BETWEEN 0 AND 720);

ALTER TABLE sandbox.game_commands DROP CONSTRAINT game_commands_command_type_check;
ALTER TABLE sandbox.game_commands ADD CONSTRAINT game_commands_command_type_check CHECK (
    command_type IN ('set_preparation', 'set_ready', 'set_tasks', 'start_game', 'pause', 'continue',
        'advance_step', 'review_proposal', 'trigger_condition', 'create_save', 'restore_save',
        'begin_exit', 'complete_exit', 'configure_army', 'deploy_army', 'move_unit', 'reload_demo_plan',
        'place_barracks', 'delete_unit', 'movement_order', 'advance_deployment_movement', 'spawn_from_barracks',
        'enable_coordination', 'review_agent_plan', 'settle_goal_stage', 'surrender', 'archive_match',
        'enable_fivepoint', 'submit_fivepoint_plan', 'retry_fivepoint_planning', 'abort_fivepoint')
);
