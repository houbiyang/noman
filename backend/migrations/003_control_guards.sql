-- 控制版本与任务代次只能递增；终态命令不可被晚到结果改写。
SET LOCAL search_path = sandbox, pg_catalog;
CREATE FUNCTION guard_control_progress() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, sandbox AS $function$
BEGIN
    IF TG_TABLE_NAME = 'games' THEN
        IF NEW.control_revision < OLD.control_revision THEN
            RAISE EXCEPTION '对局控制版本不得倒退' USING ERRCODE = '23514';
        END IF;
        IF (NEW.active_branch_id IS DISTINCT FROM OLD.active_branch_id
            OR NEW.head_snapshot_id IS DISTINCT FROM OLD.head_snapshot_id
            OR NEW.execution_epoch IS DISTINCT FROM OLD.execution_epoch)
            AND NEW.control_revision <= OLD.control_revision THEN
            RAISE EXCEPTION '更换有效头或执行世代必须递增控制版本' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'background_jobs' THEN
        IF NEW.claim_generation < OLD.claim_generation OR NEW.attempt_count < OLD.attempt_count THEN
            RAISE EXCEPTION '任务领取代次及执行次数不得倒退' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'document_versions' THEN
        IF NEW.next_parse_revision < OLD.next_parse_revision
            OR NEW.processing_generation < OLD.processing_generation
            OR NEW.publication_revision < OLD.publication_revision THEN
            RAISE EXCEPTION '资料解析分配、处理代次与发布修订不得倒退' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'game_commands' THEN
        IF OLD.status IN ('succeeded', 'rejected', 'failed') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION '终态命令不得被覆盖' USING ERRCODE = '42501';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;
CREATE TRIGGER games_control_guard BEFORE UPDATE ON games
    FOR EACH ROW EXECUTE FUNCTION guard_control_progress();
CREATE TRIGGER jobs_control_guard BEFORE UPDATE ON background_jobs
    FOR EACH ROW EXECUTE FUNCTION guard_control_progress();
CREATE TRIGGER documents_control_guard BEFORE UPDATE ON document_versions
    FOR EACH ROW EXECUTE FUNCTION guard_control_progress();
CREATE TRIGGER commands_terminal_guard BEFORE UPDATE ON game_commands
    FOR EACH ROW EXECUTE FUNCTION guard_control_progress();
REVOKE ALL ON FUNCTION guard_control_progress() FROM PUBLIC;
