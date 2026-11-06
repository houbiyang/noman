"""使用已有 PostgreSQL 工具管理穹图本机数据库，不安装 Python 或数据库扩展依赖。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone


BACKEND_ROOT = Path(__file__).resolve().parent
LOCAL_ROOT = BACKEND_ROOT / ".local"
POSTGRES_BIN = Path(r"D:\PostgreSQL\18\bin")
DATABASE_NAME = "qiongtu_sandbox_v1"
APPLICATION_ROLE = "qiongtu_app_v1"
CONFIG_PATH = LOCAL_ROOT / "database-v1.json"
APPLICATION_CONFIG_PATH = LOCAL_ROOT / "application-database-v1.json"
DATABASE_MARKER = "qiongtu-managed-database-v1"
EXPECTED_TABLES = frozenset({
    "rule_versions", "scenario_versions", "games", "game_members", "access_sessions",
    "game_branches", "game_snapshots", "game_commands", "game_events", "game_saves",
    "ai_runs", "proposals", "proposal_reviews", "proposal_evidence", "background_jobs",
    "knowledge_documents", "document_versions", "document_chunks", "embedding_spaces", "chunk_embeddings",
})


class DatabaseError(RuntimeError):
    """表示可公开显示且不包含连接凭据的数据库管理错误。"""


def sql_literal(value: str) -> str:
    """将普通文本转换为 SQL 字面量，调用方仍须限制数据库对象名称。"""
    return "'" + value.replace("'", "''") + "'"


def identifier(value: str) -> str:
    """验证本工具创建的 ASCII 标识，拒绝表达式及任意路径。"""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise DatabaseError("数据库对象名称不符合本机工具约定")
    return '"' + value + '"'


def environment(password: str) -> dict[str, str]:
    """只为子进程提供认证信息，避免把密码写入命令行或输出。"""
    process_environment = os.environ.copy()
    # 禁止继承外部 PostgreSQL 路由与配置，保持本项目本机目标确定。
    for key in list(process_environment):
        if key.startswith("PG"):
            del process_environment[key]
    process_environment.update(PGPASSWORD=password, PGCLIENTENCODING="UTF8", PGCONNECT_TIMEOUT="5")
    return process_environment


def admin_password() -> str:
    """读取项目已有本地管理凭据，不将其返回到日志。"""
    password_path = LOCAL_ROOT / "admin-password"
    if not password_path.is_file():
        raise DatabaseError("缺少本项目已有管理凭据，未创建或重置任何旧账号")
    return password_path.read_text(encoding="utf-8-sig").strip()


def psql_arguments(database: str, role: str = "battle_admin") -> list[str]:
    """构造明确限定本地实例的 psql 参数。"""
    identifier(database)
    identifier(role)
    executable = POSTGRES_BIN / "psql.exe"
    if not executable.is_file():
        raise DatabaseError("未找到现有 psql.exe；本工具不会自动安装依赖")
    return [str(executable), "-X", "-w", "-h", "127.0.0.1", "-p", "55433", "-U", role,
            "-d", database, "-At", "-v", "ON_ERROR_STOP=1"]


def execute(database: str, statement: str, *, role: str = "battle_admin", password: str | None = None,
            secret_statement: bool = False) -> str:
    """通过标准输入执行 SQL，失败时脱敏并中止，不忽略工具退出码。"""
    connection_password = password if password is not None else admin_password()
    result = subprocess.run(psql_arguments(database, role), input=statement, capture_output=True,
                            text=True, encoding="utf-8", env=environment(connection_password))
    if result.returncode:
        detail = result.stderr.replace(connection_password, "[已隐藏]").strip()
        if secret_statement:
            detail = "涉及凭据的管理语句失败，原始 SQL 未输出"
        raise DatabaseError(detail or "PostgreSQL 执行失败")
    return result.stdout.strip()


def protect_file(path: Path) -> None:
    """使用 Windows ACL 将私有文件访问限定为当前用户与 SYSTEM。"""
    owner = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", check=True).stdout
    matched_sid = re.search(r"S-1-5-[0-9-]+", owner)
    if not matched_sid:
        raise DatabaseError("无法识别当前 Windows 用户，私有配置未开放访问")
    result = subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r",
                             f"*{matched_sid.group(0)}:(F)", "*S-1-5-18:(F)"], capture_output=True)
    if result.returncode:
        raise DatabaseError("私有配置 ACL 设置失败，请先检查本地目录权限")


def write_private_json(path: Path, value: dict) -> None:
    """先保护新文件权限，再写入配置；既有文件不自动覆盖。"""
    if path.exists():
        raise DatabaseError("私有配置已存在，拒绝自动覆盖")
    with path.open("x", encoding="utf-8"):
        pass
    protect_file(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def start_cluster() -> None:
    """启动项目既有回环地址实例，校验其配置，不操作其他实例。"""
    data_directory = LOCAL_ROOT / "pgdata"
    configuration = (data_directory / "postgresql.conf").read_text(encoding="utf-8-sig")
    if not re.search(r"(?m)^port\s*=\s*55433\s*(?:#.*)?$", configuration):
        raise DatabaseError("项目实例端口与预期不符，未启动")
    if not re.search(r"(?m)^listen_addresses\s*=\s*'127\.0\.0\.1'\s*(?:#.*)?$", configuration):
        raise DatabaseError("项目实例未限制为回环地址，未启动")
    controller = POSTGRES_BIN / "pg_ctl.exe"
    status = subprocess.run([str(controller), "status", "-D", str(data_directory)], capture_output=True)
    if status.returncode:
        started = subprocess.run([str(controller), "start", "-D", str(data_directory), "-l",
                                  str(LOCAL_ROOT / "postgres.log"), "-w", "-t", "30"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if started.returncode:
            raise DatabaseError("本项目 PostgreSQL 启动失败，请查看私有 postgres.log")
    # Windows 下服务器返回的中文目录可能受历史本地编码影响，使用控制文件身份核对。
    control = subprocess.run([str(POSTGRES_BIN / "pg_controldata.exe"), "-D", str(data_directory)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    expected_identity = re.search(r"(?m)^Database system identifier:\s*(\d+)", control.stdout)
    actual_identity = execute("postgres", "SELECT system_identifier FROM pg_control_system();")
    if not expected_identity or expected_identity.group(1) != actual_identity:
        raise DatabaseError("监听端口不属于本项目数据目录，已停止后续操作")


def ensure_database() -> None:
    """只创建新的项目数据库；已存在时必须具有本工具的所有权标记。"""
    existing = execute("postgres", "SELECT COALESCE(shobj_description(oid, 'pg_database'), '') "
                       "FROM pg_database WHERE datname = " + sql_literal(DATABASE_NAME) + ";")
    exists = execute("postgres", "SELECT count(*) FROM pg_database WHERE datname = " + sql_literal(DATABASE_NAME) + ";")
    if exists == "1":
        if existing != DATABASE_MARKER:
            raise DatabaseError("同名数据库已存在且不属于本初始化器，未修改")
        return
    execute("postgres", f"CREATE DATABASE {identifier(DATABASE_NAME)} ENCODING 'UTF8' TEMPLATE template0;")
    execute("postgres", f"COMMENT ON DATABASE {identifier(DATABASE_NAME)} IS {sql_literal(DATABASE_MARKER)};")


def configure_extension_directory(database: str) -> None:
    """为明确目标库设置已校验的独立扩展目录，不修改其他数据库及实例全局配置。"""
    installation_path = LOCAL_ROOT / "pgvector-installation.json"
    if not installation_path.is_file():
        return
    installation = json.loads(installation_path.read_text(encoding="utf-8"))
    if installation.get("postgres_major") != 18:
        raise DatabaseError("扩展安装清单与 PostgreSQL 主版本不匹配")
    install_root = Path(installation["install_root"]).resolve()
    for entry in installation["files"]:
        installed_file = Path(entry["path"]).resolve()
        if not installed_file.is_relative_to(install_root) or not installed_file.is_file():
            raise DatabaseError("独立扩展文件缺失或越出安装目录")
        if hashlib.sha256(installed_file.read_bytes()).hexdigest() != entry["sha256"]:
            raise DatabaseError("独立扩展文件摘要与安装清单不符")
    share_directory = (install_root / "share").as_posix()
    if share_directory != installation["share_directory"]:
        raise DatabaseError("独立扩展目录与安装清单不符")
    execute(database, f"ALTER DATABASE {identifier(database)} SET extension_control_path = "
            + sql_literal(share_directory + ";$system") + ";")


def migration_sql(database: str, enable_vectors: bool = True) -> str:
    """生成带事务锁与文件摘要核对的迁移，不修改已应用版本。"""
    files = [BACKEND_ROOT / "migrations" / "001_application.sql"]
    if enable_vectors:
        if execute(database, "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector';") != "1":
            raise DatabaseError("当前实例未提供 pgvector，未安装扩展，向量迁移未执行")
        files.append(BACKEND_ROOT / "migrations" / "002_vectors.sql")
    files.append(BACKEND_ROOT / "migrations" / "003_control_guards.sql")
    files.append(BACKEND_ROOT / "migrations" / "004_current_ui_commands.sql")
    files.append(BACKEND_ROOT / "migrations" / "005_map_catalog.sql")
    files.append(BACKEND_ROOT / "migrations" / "006_barracks_commands.sql")
    files.append(BACKEND_ROOT / "migrations" / "007_movement_commands.sql")
    pieces = ["BEGIN; SELECT pg_advisory_xact_lock(80644, 1103);",
              "CREATE SCHEMA IF NOT EXISTS infrastructure; REVOKE ALL ON SCHEMA infrastructure FROM PUBLIC;",
              "CREATE TABLE IF NOT EXISTS infrastructure.schema_migrations ("
              "version text PRIMARY KEY, sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'), "
              "applied_at timestamptz(3) NOT NULL DEFAULT clock_timestamp());"]
    for migration_file in files:
        payload = migration_file.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        version = migration_file.stem
        pieces.append(f"SELECT EXISTS (SELECT 1 FROM infrastructure.schema_migrations WHERE version = {sql_literal(version)}) AS already_applied \\gset\n")
        pieces.append("\\if :already_applied\n")
        pieces.append("DO $check$ BEGIN IF NOT EXISTS (SELECT 1 FROM infrastructure.schema_migrations WHERE "
                      f"version = {sql_literal(version)} AND sha256 = {sql_literal(digest)}) THEN "
                      "RAISE EXCEPTION '已应用迁移文件发生变化，请新增迁移而不是修改旧文件'; END IF; END; $check$;\n")
        pieces.append("\\else\n" + payload.decode("utf-8-sig") + "\n")
        pieces.append("INSERT INTO infrastructure.schema_migrations(version, sha256) VALUES ("
                      + sql_literal(version) + ", " + sql_literal(digest) + ");\n\\endif\n")
    pieces.append("COMMIT;")
    return "\n".join(pieces)


def apply_migrations(database: str, enable_vectors: bool = True) -> None:
    """在目标数据库原子应用迁移，重复运行只检查摘要。"""
    if enable_vectors:
        configure_extension_directory(database)
    execute(database, migration_sql(database, enable_vectors))


def configure_application_role() -> None:
    """建立最小权限应用账号，密码只写私有配置且不自动重置既有账号。"""
    role_exists = execute("postgres", "SELECT count(*) FROM pg_roles WHERE rolname = " + sql_literal(APPLICATION_ROLE) + ";") == "1"
    if APPLICATION_CONFIG_PATH.exists():
        configuration = json.loads(APPLICATION_CONFIG_PATH.read_text(encoding="utf-8"))
        if configuration.get("database") != DATABASE_NAME or configuration.get("user") != APPLICATION_ROLE:
            raise DatabaseError("应用配置目标不匹配，未修改账号")
    elif role_exists:
        raise DatabaseError("同名应用角色已存在但无本项目配置，拒绝接管或重置密码")
    else:
        configuration = dict(host="127.0.0.1", port=55433, database=DATABASE_NAME,
                             user=APPLICATION_ROLE, password=secrets.token_hex(32))
        write_private_json(APPLICATION_CONFIG_PATH, configuration)
    if not role_exists:
        execute("postgres", f"CREATE ROLE {identifier(APPLICATION_ROLE)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                f"NOREPLICATION NOBYPASSRLS PASSWORD {sql_literal(configuration['password'])};", secret_statement=True)
    configure_privileges(DATABASE_NAME)
    execute(DATABASE_NAME, "SELECT 1;", role=APPLICATION_ROLE, password=configuration["password"])
    if not CONFIG_PATH.exists():
        write_private_json(CONFIG_PATH, dict(host="127.0.0.1", port=55433, database=DATABASE_NAME,
                                            schema="sandbox", application_role=APPLICATION_ROLE))


def configure_privileges(database: str) -> None:
    """为明确指定的新库或隔离测试库授予同一应用权限清单。"""
    privilege_sql = f"""
BEGIN;
REVOKE ALL ON DATABASE {identifier(database)} FROM PUBLIC;
GRANT CONNECT ON DATABASE {identifier(database)} TO {identifier(APPLICATION_ROLE)};
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA sandbox TO {identifier(APPLICATION_ROLE)};
GRANT USAGE ON TYPE sandbox.sha256_text, sandbox.object_json, sandbox.nonempty_text TO {identifier(APPLICATION_ROLE)};
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA sandbox TO {identifier(APPLICATION_ROLE)};
ALTER ROLE {identifier(APPLICATION_ROLE)} IN DATABASE {identifier(database)} SET search_path = sandbox, pg_catalog;
ALTER ROLE {identifier(APPLICATION_ROLE)} IN DATABASE {identifier(database)} SET timezone = 'UTC';
"""
    mutable_columns = {
        "rule_versions": ["availability"], "scenario_versions": ["availability"],
        "games": ["title", "active_branch_id", "head_snapshot_id", "control_revision", "execution_epoch", "updated_at"],
        "game_members": ["display_name", "updated_at"], "access_sessions": ["revoked_at"],
        "game_commands": ["status", "result_branch_id", "result_snapshot_id", "result_payload", "error_payload", "completed_at", "updated_at"],
        "ai_runs": ["checkpoint_ref", "status", "output_payload", "error_payload", "usage", "started_at", "finished_at", "updated_at"],
        "proposals": ["status", "updated_at"], "knowledge_documents": ["title", "source_description", "updated_at"],
        "document_versions": ["file_status", "parse_status", "review_status", "retrieval_status", "active_parse_revision", "next_parse_revision", "processing_generation", "parser_manifest", "retrieval_manifest", "publication_revision", "updated_at"],
        "embedding_spaces": ["availability"],
        "background_jobs": ["status", "result_payload", "error_payload", "attempt_count", "claim_generation", "lease_owner", "lease_until", "available_at", "updated_at"],
        "game_saves": ["snapshot_id", "source_command_id", "updated_at"],
    }
    for table, columns in mutable_columns.items():
        privilege_sql += f"GRANT UPDATE ({', '.join(columns)}) ON sandbox.{table} TO {identifier(APPLICATION_ROLE)};\n"
    privilege_sql += "COMMIT;"
    execute(database, privilege_sql)


def database_status() -> dict:
    """返回不包含凭据的实际数据库状态。"""
    table_names = execute(DATABASE_NAME, "SELECT tablename FROM pg_tables WHERE schemaname = 'sandbox' ORDER BY tablename;").splitlines()
    migrations = execute(DATABASE_NAME, "SELECT version FROM infrastructure.schema_migrations ORDER BY version;").splitlines()
    vector_enabled = execute(DATABASE_NAME, "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector');") == "t"
    missing_tables = sorted(EXPECTED_TABLES - set(table_names))
    return dict(host="127.0.0.1", port=55433, database=DATABASE_NAME, server_version=execute(DATABASE_NAME, "SHOW server_version;"),
                business_table_count=len(table_names), tables=table_names, migrations=migrations,
                expected_business_table_count=len(EXPECTED_TABLES), missing_tables=missing_tables,
                all_business_tables_created=not missing_tables,
                unvalidated_constraints=int(execute(DATABASE_NAME, "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON c.connamespace=n.oid WHERE n.nspname='sandbox' AND NOT c.convalidated;")),
                pgvector_available=execute(DATABASE_NAME, "SELECT EXISTS(SELECT 1 FROM pg_available_extensions WHERE name = 'vector');") == "t",
                pgvector_enabled=vector_enabled,
                pgvector_version=execute(DATABASE_NAME, "SELECT extversion FROM pg_extension WHERE extname='vector';") or None,
                games=int(execute(DATABASE_NAME, "SELECT count(*) FROM sandbox.games;")))


def backup_database() -> Path:
    """生成当前新库的自定义格式备份与摘要，不替代跨资源恢复验收。"""
    backup_root = LOCAL_ROOT / "backups-v1"
    backup_root.mkdir(exist_ok=True)
    output = backup_root / (DATABASE_NAME + "_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".dump")
    with output.open("xb"):
        pass
    protect_file(output)
    result = subprocess.run([str(POSTGRES_BIN / "pg_dump.exe"), "-w", "-h", "127.0.0.1", "-p", "55433", "-U", "battle_admin",
                             "-d", DATABASE_NAME, "-Fc", "-f", str(output)], capture_output=True, env=environment(admin_password()))
    if result.returncode:
        raise DatabaseError("新库备份失败，未将其标记为成功备份")
    manifest = dict(database=DATABASE_NAME, sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                    format="pg_dump-custom", database_only=True, resources_included=False)
    write_private_json(output.with_suffix(".json"), manifest)
    return output


def main() -> int:
    """执行明确的本机管理动作，所有业务数据初始化保持显式。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="穹图数据库管理：复用本机 PostgreSQL，不安装依赖")
    parser.add_argument("action", choices=["setup", "status", "verify", "backup"])
    parser.add_argument("--enable-vectors", action="store_true", default=True,
                        help="兼容旧命令参数；当前 Setup 默认建立含向量表的全部二十张业务表")
    arguments = parser.parse_args()
    start_cluster()
    if arguments.action == "setup":
        ensure_database()
        apply_migrations(DATABASE_NAME, arguments.enable_vectors)
        configure_application_role()
        print(json.dumps(database_status(), ensure_ascii=False, indent=2))
    elif arguments.action == "status":
        print(json.dumps(database_status(), ensure_ascii=False, indent=2))
    elif arguments.action == "backup":
        print("数据库备份已生成：" + str(backup_database()))
    else:
        return subprocess.run([sys.executable, str(BACKEND_ROOT / "tests" / "test_database.py")]).returncode
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DatabaseError, OSError, ValueError) as error:
        print("数据库管理失败：" + str(error), file=sys.stderr)
        raise SystemExit(1)
