-- 不改写既有演示规则/快照；目标裁定复用命令事务和幂等链。
ALTER TABLE sandbox.game_commands DROP CONSTRAINT game_commands_command_type_check;
ALTER TABLE sandbox.game_commands ADD CONSTRAINT game_commands_command_type_check CHECK (
    command_type IN ('set_preparation','set_ready','set_tasks','start_game','pause','continue',
    'advance_step','review_proposal','trigger_condition','create_save','restore_save','begin_exit',
    'complete_exit','configure_army','deploy_army','move_unit','reload_demo_plan','place_barracks',
    'delete_unit','settle_goal_stage','surrender','archive_match')
);
CREATE TABLE sandbox.match_archives (
    id uuid PRIMARY KEY,
    game_id uuid NOT NULL REFERENCES sandbox.games(id),
    member_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 80),
    automatic boolean NOT NULL DEFAULT false,
    role text NOT NULL CHECK (role IN ('host','red','blue')),
    phase text NOT NULL,
    simulation_step integer NOT NULL CHECK (simulation_step BETWEEN 0 AND 60),
    projection jsonb NOT NULL CHECK (jsonb_typeof(projection) = 'object'),
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (game_id, member_id) REFERENCES sandbox.game_members(game_id,id),
    FOREIGN KEY (game_id,branch_id,snapshot_id) REFERENCES sandbox.game_snapshots(game_id,branch_id,id)
);
CREATE INDEX archives_member_time_idx ON sandbox.match_archives(game_id,member_id,created_at DESC,id DESC);
CREATE UNIQUE INDEX archives_auto_uq ON sandbox.match_archives(game_id,branch_id,member_id) WHERE automatic;
-- 应用账号只获得全表 SELECT/INSERT，没有 UPDATE/DELETE，档案保持不可变。
