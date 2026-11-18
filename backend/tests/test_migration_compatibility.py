"""迁移兼容静态测试：不连接数据库、不加载地图、不启用远端战斗模式。"""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend import database
from backend.contracts import CommandRequest
from backend.runtime import ServiceError
from backend.service import SandboxService


MIGRATIONS = database.BACKEND_ROOT / 'migrations'
UPSTREAM = Path(__file__).parent / 'fixtures/migrations/upstream_main_7be85a5'
VERSION = '013_cross_branch_commands'
REMOTE_COMMANDS = {'enable_fivepoint', 'submit_fivepoint_plan', 'retry_fivepoint_planning',
                   'abort_fivepoint', 'fivepoint_planning'}


def command_names(source):
    clause = re.search(r'command_type IN \(([^)]*)\)', source, re.S)
    if clause is None:
        raise AssertionError('缺少完整命令白名单')
    return set(re.findall(r"'([^']+)'", clause[1]))


class MigrationCompatibilityTests(unittest.TestCase):
    def test_union_preserves_both_branch_command_lists(self):
        local = command_names((MIGRATIONS / '012_manual_battle_commands.sql').read_text())
        remote = command_names((UPSTREAM / '012_fivepoint_planning.sql').read_text())
        combined = command_names((MIGRATIONS / (VERSION + '.sql')).read_text())
        self.assertEqual(combined, local | remote)
        for migration in MIGRATIONS.glob('*.sql'):
            text = migration.read_text()
            if 'command_type IN (' in text:
                self.assertLessEqual(command_names(text), combined, migration.name)

    def test_immutable_local_migrations_are_unchanged(self):
        expected = {'011_unit_interactions.sql': '8b119080f2e4ee348b18130f857bddfa57b733a020cbbd15ad54d16197d124d3',
                    '012_manual_battle_commands.sql': '1edf1b717825c1173d4d3ac2e0853750db6b392e1729a120ae7faa7336c9af2f'}
        # 011 的摘要由本次改动前的Git版本冻结；不以迁移器重算覆盖旧登记。
        for name, digest in expected.items():
            self.assertIn(digest, database.migration_checksums((MIGRATIONS / name).read_bytes()), name)

    def test_crlf_lf_are_the_only_compatible_checksum_changes(self):
        raw = b'-- migration\nSELECT 1;\n'
        compatible = database.migration_checksums(raw)
        self.assertEqual(compatible, database.migration_checksums(raw.replace(b'\n', b'\r\n')))
        for altered in (b'-- migration\nSELECT 2;\n', raw + b' ', b'\xef\xbb\xbf' + raw, raw.replace(b'\n', b'\r')):
            self.assertNotIn(hashlib.sha256(altered).hexdigest(), compatible)

    def test_generated_runner_uses_union_and_keeps_remote_migrations_out(self):
        with patch.object(database, 'execute', side_effect=AssertionError('不允许连接数据库')):
            script = database.migration_sql('qiongtu_manual_test_' + uuid4().hex, enable_vectors=False)
        self.assertIn("version = '013_cross_branch_commands'", script)
        self.assertIn('sha256 IN (', script)
        self.assertNotIn("version = '011_fivepoint_commands'", script)
        self.assertNotIn("version = '012_fivepoint_planning'", script)
        expected = command_names((MIGRATIONS / (VERSION + '.sql')).read_text())
        for constraint in re.findall(r'ALTER TABLE sandbox\.game_commands ADD CONSTRAINT.*?\n\);', script, re.S):
            self.assertEqual(command_names(constraint), expected)

    def test_new_migration_does_not_change_time_map_or_data(self):
        text = (MIGRATIONS / (VERSION + '.sql')).read_text()
        statements = '\n'.join(line for line in text.splitlines() if not line.startswith('--'))
        self.assertNotRegex(statements, r'(?i)\b(UPDATE|INSERT|DELETE|CREATE)\b|game_snapshots|simulation_step|720')

    def test_database_whitelist_does_not_enable_remote_http_commands(self):
        service = SandboxService(None)
        for name in REMOTE_COMMANDS:
            request = CommandRequest(command_type=name, payload={}, context=dict(
                game_id=uuid4(), branch_id=uuid4(), snapshot_id=uuid4(), context_token='not-used'))
            with self.assertRaises(ServiceError) as error:
                service.command(str(uuid4()), 'not-a-token', uuid4(), request)
            self.assertEqual(error.exception.code, 'COMMAND_UNSUPPORTED')

    def test_management_errors_keep_diagnostics_and_redact_nonempty_password(self):
        for password in ('', 'fixture-password'):
            with self.subTest(empty_password=not password):
                stderr = 'ERROR: constraint_fixture ' + password
                with patch.object(database, 'psql_arguments', return_value=['isolated-psql']), \
                        patch.object(database.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr=stderr)):
                    with self.assertRaises(database.DatabaseError) as error:
                        database.execute('qiongtu_manual_test_' + uuid4().hex, 'SELECT 1;', password=password)
                self.assertIn('constraint_fixture', str(error.exception))
                if password:
                    self.assertNotIn(password, str(error.exception))
                    self.assertIn('[已隐藏]', str(error.exception))
