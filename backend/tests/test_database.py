"""在本项目 PostgreSQL 的唯一临时库执行真实约束、并发及备份恢复测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database


def object_id(name: str) -> str:
    """生成仅供测试使用的稳定标识，不能当作正式业务默认值。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "qiongtu-database-test/" + name))


def json_text(value: object) -> str:
    """输出测试数据使用的确定 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    """计算测试载荷摘要，不声明已经锁定正式快照哈希协议。"""
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def insert(table: str, values: dict) -> str:
    """构造受控测试对象的插入语句，不接受用户提供的 SQL。"""
    converted = []
    for value in values.values():
        if value is None:
            converted.append("NULL")
        elif isinstance(value, bool):
            converted.append("TRUE" if value else "FALSE")
        elif isinstance(value, int):
            converted.append(str(value))
        else:
            converted.append(database.sql_literal(json_text(value) if isinstance(value, (dict, list)) else value))
    return f"INSERT INTO sandbox.{table} ({', '.join(values)}) VALUES ({', '.join(converted)});"


def snapshot_values(game: str, *, name: str = "initial", branch: str | None = None,
                    revision: int = 0, command: str | None = None) -> dict:
    """创建明确标记为测试的最小封装，不模拟完整作战世界。"""
    state = dict(manifest=dict(fixture_only=True), lifecycle=dict(phase="paused", simulation_step=0),
                 camps=[], units=[], formations=[], tasks=[], routes=[], observations={}, logistics={},
                 conditions={}, decisions={}, scores={})
    return dict(id=object_id(game + "/" + name), game_id=object_id(game),
                branch_id=object_id(branch or game + "/root"), revision=revision, world_revision=0,
                simulation_step=0, phase="paused", snapshot_kind="initial" if command is None else "command",
                schema_version="database-test-only-v1", hash_format_version="database-test-json-v1",
                state_payload=state, payload_sha256=digest(state), source_command_id=object_id(command) if command else None)


def command_values(name: str, game: str = "game1", *, request: str | None = None) -> dict:
    """构造不推进正式规则的暂停命令记录。"""
    return dict(id=object_id(name), game_id=object_id(game), origin_branch_id=object_id(game + "/root"),
                actor_member_id=object_id(game + "/host"), request_key=object_id(request or name + "/request"),
                request_hash=digest(dict(command_type="pause", payload={})), hash_format_version="database-test-json-v1",
                command_type="pause", expected_control_revision=0, base_snapshot_id=object_id(game + "/initial"),
                request_payload={}, source_kind="human", execution_epoch=object_id(game + "/epoch"),
                status="succeeded", result_branch_id=object_id(game + "/root"),
                result_snapshot_id=object_id(game + "/initial"), result_payload={},
                completed_at="2026-11-03T00:00:00Z")


def job_values(name: str) -> dict:
    """构造已声明作用域的排队分析任务。"""
    return dict(id=object_id(name), job_type="analysis", subject_key="run/" + object_id("run1"),
                game_id=object_id("game1"), branch_id=object_id("game1/root"), ai_run_id=object_id("run1"),
                execution_epoch=object_id("game1/epoch"), status="queued", payload={}, attempt_count=0,
                claim_generation=0, available_at="2026-11-03T00:00:00Z")


def embedding_values(name: str) -> dict:
    """生成三维测试向量，不代表正式模型的维度或输出。"""
    return dict(id=object_id(name), chunk_id=object_id("chunk1"), embedding_space_id=object_id("space3"),
                dimension=3, embedding="[1,0,0]", source_content_sha256=digest("测试片段"))


def baseline() -> str:
    """生成两个演示局及资料来源，只写入随后销毁的隔离测试库。"""
    statements = ["BEGIN; SET LOCAL search_path = sandbox, pg_catalog;"]
    statements.append(insert("rule_versions", dict(id=object_id("rule"), rule_code="database-test",
        version_label="v1", schema_version="fixture-v1", hash_format_version="fixture-v1", definition={},
        payload_sha256=digest({}), availability="active", is_demo=True)))
    statements.append(insert("scenario_versions", dict(id=object_id("scenario"), scenario_code="database-test",
        version_label="v1", rule_version_id=object_id("rule"), schema_version="fixture-v1", hash_format_version="fixture-v1",
        definition={}, asset_manifest={}, payload_sha256=digest({}), availability="active", is_demo=True)))
    for game in ("game1", "game2"):
        statements.append(insert("games", dict(id=object_id(game), game_no=game, title="数据库约束测试",
            creation_request_key=object_id(game + "/creation"), creation_payload_sha256=digest({}),
            creation_hash_format_version="fixture-v1", scenario_version_id=object_id("scenario"),
            rule_version_id=object_id("rule"), active_branch_id=object_id(game + "/root"),
            head_snapshot_id=object_id(game + "/initial"), control_revision=0,
            execution_epoch=object_id(game + "/epoch"), is_demo=True)))
        statements.append(insert("game_branches", dict(id=object_id(game + "/root"), game_id=object_id(game))))
        statements.append(insert("game_snapshots", snapshot_values(game)))
        for role in ("host", "red", "blue"):
            statements.append(insert("game_members", dict(id=object_id(game + "/" + role), game_id=object_id(game), role=role)))
        statements.append(insert("game_saves", dict(id=object_id(game + "/auto"), game_id=object_id(game),
            branch_id=object_id(game + "/root"), snapshot_id=object_id(game + "/initial"), save_kind="auto")))
    statements.append(insert("game_commands", command_values("command1")))
    statements.append(insert("ai_runs", dict(id=object_id("run1"), game_id=object_id("game1"), branch_id=object_id("game1/root"),
        base_snapshot_id=object_id("game1/initial"), side="red", execution_epoch=object_id("game1/epoch"), agent_role="commander",
        input_view={}, model_manifest={}, knowledge_manifest={}, input_view_sha256=digest({}), hash_format_version="fixture-v1",
        checkpoint_thread_id="database-test-run1", status="queued")))
    statements.append(insert("proposals", dict(id=object_id("proposal1"), game_id=object_id("game1"), branch_id=object_id("game1/root"),
        base_snapshot_id=object_id("game1/initial"), side="red", execution_epoch=object_id("game1/epoch"), ai_run_id=object_id("run1"),
        proposal_kind="initial", status="pending_review", requires_confirmation=True, schema_version="fixture-v1", content_payload={},
        validation_payload={}, based_on_world_revision=0, input_view_sha256=digest({}), hash_format_version="fixture-v1")))
    statements.append(insert("knowledge_documents", dict(id=object_id("document"), document_code="database-test",
        title="测试原文", is_demo=True)))
    statements.append(insert("document_versions", dict(id=object_id("document/v1"), document_id=object_id("document"),
        version_label="v1", storage_key="fixture/not-a-real-file", original_filename="测试.txt", media_type="text/plain",
        size_bytes=0, sha256=digest(""), scope_game_id=object_id("game1"), visibility="red", file_status="ready",
        parse_status="pending", review_status="pending", retrieval_status="pending", next_parse_revision=1,
        processing_generation=0, parser_manifest={}, publication_revision=0, is_demo=True)))
    statements.append(insert("document_chunks", dict(id=object_id("chunk1"), document_version_id=object_id("document/v1"),
        parse_revision=1, ordinal=0, section_path=[], locator={}, content="测试片段", content_sha256=digest("测试片段"),
        parser_manifest={}, parser_manifest_sha256=digest({}))))
    statements.append(insert("document_chunks", dict(id=object_id("chunk-backup"), document_version_id=object_id("document/v1"),
        parse_revision=1, ordinal=1, section_path=[], locator={}, content="测试片段", content_sha256=digest("测试片段"),
        parser_manifest={}, parser_manifest_sha256=digest({}))))
    statements.append(insert("embedding_spaces", dict(id=object_id("space3"), space_code="database-test-space-3",
        provider_code="fixture", model_name="not-a-real-model", model_revision="fixture-v1", dimension=3,
        distance_metric="cosine", preprocessing_manifest=dict(fixture_only=True), availability="active")))
    statements.append(insert("embedding_spaces", dict(id=object_id("space4"), space_code="database-test-space-4",
        provider_code="fixture", model_name="not-a-real-model", model_revision="fixture-v1", dimension=4,
        distance_metric="cosine", preprocessing_manifest=dict(fixture_only=True), availability="active")))
    statements.append("COMMIT;")
    return "\n".join(statements)


class DatabaseChecks(unittest.TestCase):
    """验证真实 PostgreSQL 能保护的边界，不声称验证完整业务规则。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建唯一隔离数据库并建立受控测试夹具。"""
        cls.test_database = "qiongtu_verify_" + uuid.uuid4().hex
        cls.restore_database = "qiongtu_restore_" + uuid.uuid4().hex
        cls.created_databases = []
        for name in (cls.test_database, cls.restore_database):
            database.execute("postgres", f"CREATE DATABASE {database.identifier(name)} TEMPLATE template0 ENCODING 'UTF8';")
            cls.created_databases.append(name)
        database.apply_migrations(cls.test_database)
        database.configure_extension_directory(cls.restore_database)
        database.configure_privileges(cls.test_database)
        database.execute(cls.test_database, baseline())
        cls.configuration = json.loads(database.APPLICATION_CONFIG_PATH.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        """只删除本次以随机标识创建的测试数据库，不操作任何已有业务库。"""
        for name in reversed(cls.created_databases):
            if not (name.startswith("qiongtu_verify_") or name.startswith("qiongtu_restore_")) or len(name.rsplit("_", 1)[-1]) != 32:
                raise database.DatabaseError("测试数据库名称保护失败，未清理")
            database.execute("postgres", f"DROP DATABASE {database.identifier(name)};")

    def transaction(self, statement: str, *, application: bool = False) -> str:
        """在自动回滚的事务内运行断言，避免测试之间相互污染。"""
        credentials = dict(role=database.APPLICATION_ROLE, password=self.configuration["password"]) if application else {}
        return database.execute(self.test_database, "BEGIN; SET LOCAL search_path = sandbox, pg_catalog;\n"
                                + statement + "\nSET CONSTRAINTS ALL IMMEDIATE; ROLLBACK;", **credentials)

    def rejects(self, statement: str, expected: str, *, application: bool = False) -> None:
        """断言数据库因预期约束拒绝输入，而不是只因任意语法错误失败。"""
        with self.assertRaises(database.DatabaseError) as raised:
            self.transaction(statement, application=application)
        self.assertIn(expected, str(raised.exception))

    def test_01_tables_and_atomic_initialization(self):
        """逐个核对二十业务表与完整根快照已经提交。"""
        actual_tables = set(database.execute(self.test_database, "SELECT tablename FROM pg_tables WHERE schemaname='sandbox';").splitlines())
        self.assertEqual(actual_tables, database.EXPECTED_TABLES)
        self.assertEqual(database.execute(self.test_database, "SELECT count(*) FROM sandbox.games g JOIN sandbox.game_snapshots s ON (g.id,g.active_branch_id,g.head_snapshot_id)=(s.game_id,s.branch_id,s.id);"), "2")

    def test_02_repeated_migration(self):
        """相同摘要的迁移重复执行不重复建表。"""
        database.apply_migrations(self.test_database)
        self.assertEqual(database.execute(self.test_database, "SELECT version FROM infrastructure.schema_migrations ORDER BY version;").splitlines(),
            ["001_application", "002_vectors", "003_control_guards", "004_current_ui_commands", "005_map_catalog", "006_barracks_commands", "007_battle_reports"])

    def test_03_dangling_head(self):
        """提交时拒绝悬空有效头。"""
        self.rejects(f"UPDATE games SET control_revision=1,head_snapshot_id='{uuid.uuid4()}' WHERE id='{object_id('game1')}';", "games_head_snapshot_fk")

    def test_04_cross_game_head(self):
        """拒绝另一个对局的分支和快照。"""
        self.rejects(f"UPDATE games SET control_revision=1,active_branch_id='{object_id('game2/root')}',head_snapshot_id='{object_id('game2/initial')}' WHERE id='{object_id('game1')}';", "games_active_branch_fk")

    def test_05_duplicate_member(self):
        """每局同角色只能存在一个席位。"""
        self.rejects(insert("game_members", dict(id=str(uuid.uuid4()), game_id=object_id("game1"), role="red")), "game_members_game_id_role_key")

    def test_06_session_scope(self):
        """会话不能引用其他对局的席位。"""
        self.rejects(insert("access_sessions", dict(id=str(uuid.uuid4()), game_id=object_id("game1"), member_id=object_id("game2/red"),
            token_digest=digest("session"), expires_at="2100-01-01T00:00:00Z")), "access_sessions_game_id_member_id_fkey")

    def test_07_session_expiry(self):
        """会话有效期必须晚于创建时间。"""
        self.rejects(insert("access_sessions", dict(id=str(uuid.uuid4()), game_id=object_id("game1"), member_id=object_id("game1/red"),
            token_digest=digest("session"), expires_at="2000-01-01T00:00:00Z")), "check constraint")

    def test_08_duplicate_command(self):
        """同席位同分支幂等键不能产生第二条命令。"""
        values = command_values("duplicate", request="command1/request")
        self.rejects(insert("game_commands", values), "unique constraint")

    def test_09_command_terminal_state(self):
        """成功命令不能缺少完成时间和结果。"""
        values = command_values("invalid-terminal")
        values["completed_at"] = None
        self.rejects(insert("game_commands", values), "check constraint")

    def test_10_snapshot_same_step_revisions(self):
        """同一步可存在不同修订快照。"""
        values = snapshot_values("game1", name="revision1", revision=1, command="command1")
        self.transaction(insert("game_snapshots", values))

    def test_11_snapshot_revision_unique(self):
        """同分支同修订不能重复。"""
        values = snapshot_values("game1", name="duplicate-revision")
        self.rejects(insert("game_snapshots", values), "unique constraint")

    def test_12_snapshot_envelope(self):
        """列与生命周期中的阶段必须一致。"""
        values = snapshot_values("game1", name="invalid-envelope", revision=1, command="command1")
        values["state_payload"]["lifecycle"]["phase"] = "running"
        self.rejects(insert("game_snapshots", values), "check constraint")

    def test_13_snapshot_immutable(self):
        """即使用管理账号也不通过普通语句覆盖历史快照。"""
        self.rejects("UPDATE game_snapshots SET payload_sha256=repeat('a',64);", "禁止修改不可变内容")

    def test_14_source_scope(self):
        """读档分支来源快照不能属于另一局。"""
        self.rejects(insert("game_branches", dict(id=str(uuid.uuid4()), game_id=object_id("game1"),
            parent_branch_id=object_id("game1/root"), fork_snapshot_id=object_id("game2/initial"),
            created_by_command_id=object_id("command1"))), "branches_fork_snapshot_fk")

    def test_15_restore_new_branch(self):
        """恢复命令结果可以指向新分支且来源仍指向原分支。"""
        command = command_values("restore")
        command.update(command_type="restore_save", result_branch_id=object_id("restored/root"), result_snapshot_id=object_id("game1/restored"))
        snapshot = snapshot_values("game1", name="restored", branch="restored/root", command="restore")
        snapshot["snapshot_kind"] = "fork"
        self.transaction(insert("game_commands", command) + insert("game_branches", dict(id=object_id("restored/root"), game_id=object_id("game1"),
            parent_branch_id=object_id("game1/root"), fork_snapshot_id=object_id("game1/initial"), created_by_command_id=object_id("restore")))
            + insert("game_snapshots", snapshot)
            + f"UPDATE games SET active_branch_id='{object_id('restored/root')}',head_snapshot_id='{object_id('game1/restored')}',control_revision=1,execution_epoch='{uuid.uuid4()}' WHERE id='{object_id('game1')}';")

    def test_16_event_revision(self):
        """事件不能声称属于另一快照修订。"""
        self.rejects(insert("game_events", dict(id=str(uuid.uuid4()), game_id=object_id("game1"), branch_id=object_id("game1/root"),
            snapshot_id=object_id("game1/initial"), snapshot_revision=1, command_id=object_id("command1"), event_index=0,
            fact_key="test", event_type="fixture", simulation_step=0, visibility="public", payload={})), "foreign key constraint")

    def test_17_manual_save_idempotency(self):
        """同一保存命令不能生成两个手动入口。"""
        values = dict(id=str(uuid.uuid4()), game_id=object_id("game1"), branch_id=object_id("game1/root"), snapshot_id=object_id("game1/initial"),
                      save_kind="manual", created_by_member_id=object_id("game1/host"), source_command_id=object_id("command1"))
        first = insert("game_saves", values)
        values["id"] = str(uuid.uuid4())
        self.rejects(first + insert("game_saves", values), "saves_manual_command_uq")

    def test_18_manual_save_immutable(self):
        """手动存档不允许通过可变自动入口权限覆盖。"""
        values = dict(id=str(uuid.uuid4()), game_id=object_id("game1"), branch_id=object_id("game1/root"), snapshot_id=object_id("game1/initial"),
                      save_kind="manual", created_by_member_id=object_id("game1/host"), source_command_id=object_id("command1"))
        self.rejects(insert("game_saves", values) + f"UPDATE game_saves SET updated_at=clock_timestamp() WHERE id='{values['id']}';", "手动和结束存档不可覆盖")

    def test_19_proposal_side(self):
        """建议不能跨方绑定分析运行。"""
        self.rejects("INSERT INTO proposals SELECT '" + str(uuid.uuid4()) + "'::uuid,created_at,game_id,branch_id,base_snapshot_id,'blue',execution_epoch,ai_run_id,source_proposal_id,proposal_kind,status,requires_confirmation,schema_version,content_payload,validation_payload,based_on_world_revision,input_view_sha256,hash_format_version,updated_at FROM proposals;", "foreign key constraint")

    def test_20_evidence_source(self):
        """文档证据必须具有文档版本。"""
        self.rejects(insert("proposal_evidence", dict(id=str(uuid.uuid4()), game_id=object_id("game1"), proposal_id=object_id("proposal1"),
            position=0, source_type="document", locator={}, excerpt="", excerpt_sha256=digest(""), source_manifest={})), "check constraint")

    def test_21_private_document_scope(self):
        """红方私有资料不允许缺少对局作用域。"""
        # 原身份不可改；使用同一测试记录复制新身份构造非法输入。
        self.rejects("INSERT INTO document_versions SELECT '" + str(uuid.uuid4()) + "'::uuid,created_at,document_id,'invalid','fixture/invalid',original_filename,media_type,size_bytes,sha256,NULL,'red',file_status,parse_status,review_status,retrieval_status,active_parse_revision,next_parse_revision,processing_generation,parser_manifest,retrieval_manifest,publication_revision,is_demo,updated_at FROM document_versions;", "check constraint")

    def test_22_publication_requirements(self):
        """没有活动解析及审核时不能标为检索就绪。"""
        self.rejects("UPDATE document_versions SET retrieval_status='ready';", "check constraint")

    def test_23_chunk_parent_revision(self):
        """父片段不能属于另一解析代次。"""
        self.rejects(insert("document_chunks", dict(id=str(uuid.uuid4()), document_version_id=object_id("document/v1"), parse_revision=2,
            ordinal=0, parent_chunk_id=object_id("chunk1"), section_path=[], locator={}, content="", content_sha256=digest(""),
            parser_manifest={}, parser_manifest_sha256=digest({}))), "foreign key constraint")

    def test_24_digest_format(self):
        """拒绝格式不合法的内容摘要。"""
        values = snapshot_values("game1", name="digest", revision=1, command="command1")
        values["payload_sha256"] = "invalid"
        self.rejects(insert("game_snapshots", values), "sha256_text")

    def test_25_active_job_unique(self):
        """同一运行的活动任务不能重复入队。"""
        first = job_values("job1")
        second = job_values("job2")
        self.rejects(insert("background_jobs", first) + insert("background_jobs", second), "jobs_active_subject_uq")

    def test_26_running_job_lease(self):
        """执行中必须明确租约归属和截止时间。"""
        values = job_values("bad-lease")
        values["status"] = "running"
        self.rejects(insert("background_jobs", values), "check constraint")

    def test_27_stale_worker_condition(self):
        """条件更新拒绝错误领取代次，不声称已有完整任务执行器。"""
        values = job_values("old-worker")
        values.update(status="running", claim_generation=2, lease_owner="new-worker", lease_until="2100-01-01T00:00:00Z")
        output = self.transaction(insert("background_jobs", values)
            + f"WITH completed AS (UPDATE background_jobs SET status='succeeded',lease_owner=NULL,lease_until=NULL WHERE id='{values['id']}' AND status='running' AND claim_generation=1 AND lease_owner='old-worker' AND lease_until>clock_timestamp() RETURNING id) SELECT count(*) FROM completed;")
        self.assertIn("\n0\n", "\n" + output + "\n")

    def test_28_application_permissions(self):
        """应用账号没有结构修改和历史删除权限。"""
        self.rejects("CREATE TABLE sandbox.not_allowed(id integer);", "permission denied", application=True)
        self.rejects("DELETE FROM game_snapshots;", "permission denied", application=True)
        self.rejects("UPDATE rule_versions SET definition='{}';", "permission denied", application=True)
        self.rejects("SELECT * FROM infrastructure.schema_migrations;", "permission denied", application=True)

    def test_29_application_read_and_allowed_update(self):
        """应用账号可以读取业务记录并更新允许的控制字段。"""
        self.transaction("SELECT count(*) FROM games; UPDATE games SET title='权限测试';", application=True)

    def test_30_concurrent_revision_compare(self):
        """两个真实连接竞争同一控制版本，只允许一次条件更新成功。"""
        def compete() -> str:
            sql = f"BEGIN; WITH changed AS (UPDATE sandbox.games SET control_revision=control_revision+1 WHERE id='{object_id('game2')}' AND control_revision=0 RETURNING id) SELECT count(*) FROM changed; COMMIT;"
            return database.execute(self.test_database, sql)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: compete(), range(2)))
        counts = sorted(int(next(line for line in result.splitlines() if line in ("0", "1"))) for result in results)
        self.assertEqual(counts, [0, 1])

    def test_31_backup_restore(self):
        """真实生成自定义备份并恢复到独立库，验证引用和数据可读。"""
        backup_vector = embedding_values("backup-vector")
        backup_vector["chunk_id"] = object_id("chunk-backup")
        database.execute(self.test_database, insert("chunk_embeddings", backup_vector))
        backup_path = database.LOCAL_ROOT / (self.test_database + ".dump")
        with backup_path.open("xb"):
            pass
        database.protect_file(backup_path)
        result = subprocess.run([str(database.POSTGRES_BIN / "pg_dump.exe"), "-w", "-h", "127.0.0.1", "-p", "55433",
            "-U", "battle_admin", "-d", self.test_database, "-Fc", "-f", str(backup_path)], capture_output=True,
            env=database.environment(database.admin_password()))
        self.assertEqual(result.returncode, 0, "隔离测试库备份失败")
        restored = subprocess.run([str(database.POSTGRES_BIN / "pg_restore.exe"), "-w", "-h", "127.0.0.1", "-p", "55433",
            "-U", "battle_admin", "-d", self.restore_database, "--exit-on-error", str(backup_path)], capture_output=True,
            env=database.environment(database.admin_password()))
        self.assertEqual(restored.returncode, 0, restored.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(database.execute(self.restore_database, "SELECT count(*) FROM sandbox.games;"), "2")
        self.assertEqual(database.execute(self.restore_database, "SELECT count(*) FROM sandbox.proposals p JOIN sandbox.ai_runs r ON (p.game_id,p.branch_id,p.side,p.ai_run_id)=(r.game_id,r.branch_id,r.side,r.id);"), "1")
        self.assertEqual(database.execute(self.restore_database, "SELECT embedding::text FROM sandbox.chunk_embeddings;"), "[1,0,0]")
        backup_path.unlink()

    def test_32_vector_native_type(self):
        """向量列实际使用 pgvector 原生类型而非数组或 JSON 替代。"""
        self.assertEqual(database.execute(self.test_database, "SELECT udt_schema||'.'||udt_name FROM information_schema.columns WHERE table_schema='sandbox' AND table_name='chunk_embeddings' AND column_name='embedding';"), "public.vector")

    def test_33_terminal_command_frozen(self):
        """晚到失败结果不能覆盖已经成功的命令。"""
        self.rejects("UPDATE game_commands SET status='failed',result_branch_id=NULL,result_snapshot_id=NULL,result_payload=NULL,error_payload='{}';", "终态命令不得被覆盖")

    def test_34_control_progress(self):
        """更换有效头或执行世代必须同时递增控制版本。"""
        self.rejects(f"UPDATE games SET execution_epoch='{uuid.uuid4()}';", "必须递增控制版本")

    def test_35_job_generation_progress(self):
        """任务领取代次不允许回退。"""
        values = job_values("generation-backwards")
        values["claim_generation"] = 2
        self.rejects(insert("background_jobs", values) + "UPDATE background_jobs SET claim_generation=1;", "不得倒退")

    def test_36_publication_progress(self):
        """资料发布修订不能倒退。"""
        self.rejects("UPDATE document_versions SET publication_revision=2; UPDATE document_versions SET publication_revision=1;", "不得倒退")

    def test_37_vector_dimension(self):
        """实际向量长度必须匹配记录声明的维度。"""
        values = embedding_values("bad-vector-dimension")
        values["embedding"] = "[1,0]"
        self.rejects(insert("chunk_embeddings", values), "check constraint")

    def test_38_vector_space_dimension(self):
        """不能使用其他维度空间声明同一向量。"""
        values = embedding_values("bad-space-dimension")
        values["embedding_space_id"] = object_id("space4")
        self.rejects(insert("chunk_embeddings", values), "foreign key constraint")

    def test_39_vector_source_digest(self):
        """向量的来源摘要必须与原文片段一致。"""
        values = embedding_values("bad-source-digest")
        values["source_content_sha256"] = digest("其他文本")
        self.rejects(insert("chunk_embeddings", values), "foreign key constraint")

    def test_40_vector_unique(self):
        """同一片段在同一空间不得重复写入。"""
        self.rejects(insert("chunk_embeddings", embedding_values("vector-first"))
            + insert("chunk_embeddings", embedding_values("vector-second")), "unique constraint")

    def test_41_vector_application_write_and_read(self):
        """应用账号可写入并读取原生向量，余弦距离按实际 SQL 计算。"""
        output = self.transaction(insert("chunk_embeddings", embedding_values("app-vector"))
            + "SELECT embedding OPERATOR(public.<=>) '[1,0,0]'::public.vector FROM chunk_embeddings WHERE id='"
            + object_id("app-vector") + "';", application=True)
        self.assertIn("\n0\n", "\n" + output + "\n")

    def test_42_vector_immutable(self):
        """管理路径也不能就地覆盖已提交片段向量。"""
        self.rejects(insert("chunk_embeddings", embedding_values("immutable-vector"))
            + "UPDATE chunk_embeddings SET embedding='[0,1,0]';", "禁止修改不可变内容")

    def test_43_constraints_validated(self):
        """全部表约束已验证，不留下未验证外键。"""
        self.assertEqual(database.execute(self.test_database, "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON c.connamespace=n.oid WHERE n.nspname='sandbox' AND NOT c.convalidated;"), "0")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DatabaseChecks))
    report = dict(executed_at=datetime.now(timezone.utc).isoformat(), tests_run=result.testsRun,
                  failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                  success=result.wasSuccessful(), postgres_version=database.execute(database.DATABASE_NAME, "SHOW server_version;"),
                  database=database.DATABASE_NAME, scope="全部二十张业务表、约束、权限、双连接条件更新、原生向量读写与数据库备份恢复；不含业务规则或真实模型")
    report_path = database.LOCAL_ROOT / "database-verification-v1.json"
    if not report_path.exists():
        database.write_private_json(report_path, report)
    else:
        database.protect_file(report_path)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
