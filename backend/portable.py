"""macOS/Linux 本机开发入口。独立集群、回环监听，不接管 Windows 旧实例。"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import unittest
from unittest.mock import patch

from backend import database


def prepare():
    """显式初始化项目专用集群；既有配置只核对，绝不重置账号。"""
    import pgserver
    database.POSTGRES_BIN = Path(pgserver.__file__).parent / 'pginstall' / 'bin'
    database.LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    database.LOCAL_ROOT.chmod(0o700)
    cluster = database.LOCAL_ROOT / 'portable-pgdata'
    marker = database.LOCAL_ROOT / 'portable-postgres.json'
    config = dict(cluster=str(cluster), port=database.POSTGRES_PORT, admin='battle_admin')
    if marker.exists():
        if json.loads(marker.read_text()) != config:
            raise RuntimeError('独立开发集群配置不一致，未启动其他实例')
    else:
        if cluster.exists() or database.APPLICATION_CONFIG_PATH.exists():
            raise RuntimeError('已有数据或应用配置但没有本入口标识，拒绝接管')
        database.write_private_json(marker, config)
    def run(binary, *args):
        result = subprocess.run([str(database.POSTGRES_BIN / binary), *map(str,args)], capture_output=True, text=True)
        if result.returncode: raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    if not (cluster/'PG_VERSION').exists():
        run('initdb', '-D', cluster, '-U', 'battle_admin', '--encoding=UTF8', '--locale=C', '--auth-local=trust', '--auth-host=scram-sha-256')
        # 管理操作通过本项目专用 Unix socket；TCP 所有应用账号强制密码验证。
        with (cluster/'postgresql.conf').open('a') as stream:
            stream.write(f"\nlisten_addresses = '127.0.0.1'\nport = {database.POSTGRES_PORT}\nunix_socket_directories = '{database.LOCAL_ROOT}'\n")
    status = subprocess.run([str(database.POSTGRES_BIN/'pg_ctl'),'-D',str(cluster),'status'],capture_output=True)
    if status.returncode:
        run('pg_ctl','-D',cluster,'-l',database.LOCAL_ROOT/'portable-postgres.log','-w','start')
    # 管理 SQL 只走专用 socket；应用 Settings 始终使用 TCP + 最小权限账号。
    original_psql = database.psql_arguments
    def arguments(name, role='battle_admin'):
        args = original_psql(name,role)
        if role == 'battle_admin': args[args.index('-h')+1] = str(database.LOCAL_ROOT)
        return args
    database.psql_arguments = arguments
    database.admin_password = lambda: ''  # 专用 socket trust；不更改其他实例的认证。
    exists = database.execute('postgres', "SELECT count(*) FROM pg_database WHERE datname='qiongtu_sandbox_v1';") == '1'
    if not exists: database.execute('postgres', f'CREATE DATABASE {database.identifier(database.DATABASE_NAME)} TEMPLATE template0 ENCODING \'UTF8\';')
    database.apply_migrations(database.DATABASE_NAME, enable_vectors=False)
    database.configure_application_role()
    from backend.runtime import initialize_runtime
    initialize_runtime()
    if not (database.LOCAL_ROOT/'bootstrap-key').exists():
        path=database.LOCAL_ROOT/'bootstrap-key'
        with path.open('x'): database.protect_file(path)
        path.write_text(secrets.token_hex(32))
    return config


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('setup','test','serve'))
    args=parser.parse_args()
    prepare()
    if args.action=='test':
        # pgvector 非本功能依赖。Windows 完整向量/备份测试保持原入口，不在这里伪称通过。
        original=database.apply_migrations
        with patch.object(database,'apply_migrations',side_effect=lambda name,enable_vectors=True:original(name,False)):
            suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(module) for module in (
                'backend.tests.test_battle_rules','backend.tests.test_qinglan_rules','backend.tests.test_service','backend.tests.test_battle_reports',
                'backend.tests.test_spatial','backend.tests.test_map_catalog','backend.tests.test_web_launch'))
            result=unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(not result.wasSuccessful())
    if args.action=='serve':
        from backend.runtime import Settings
        from backend.service import SandboxService
        from backend.main import create_app
        import uvicorn
        settings=Settings.from_local(); SandboxService(settings).seed_versions()
        uvicorn.run(create_app(settings),host='127.0.0.1',port=18080)
    else: print('本机独立 PostgreSQL 已就绪；未启用可选 pgvector。')


if __name__=='__main__': main()
