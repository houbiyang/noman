"""只在随机临时PostgreSQL库验证跨分支升级、原子失败与保护性回退。"""
import hashlib
import unittest
from uuid import uuid4

from backend import database
from backend.tests.test_database import command_values, digest, insert, object_id, snapshot_values
from backend.tests.test_migration_compatibility import MIGRATIONS, UPSTREAM, VERSION, REMOTE_COMMANDS


class MigrationCompatibilityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.name = 'qiongtu_manual_test_' + uuid4().hex
        database.execute('postgres', f'CREATE DATABASE {database.identifier(self.name)} TEMPLATE template0 ENCODING \'UTF8\';')
        self.addCleanup(self.cleanup_database)
        database.apply_migrations(self.name, enable_vectors=False)
        statements = ['BEGIN; SET LOCAL search_path=sandbox,pg_catalog;']
        statements += [insert('rule_versions', dict(id=object_id('rule'), rule_code='compatibility-fixture',
            version_label='v1', schema_version='fixture-v1', hash_format_version='fixture-v1', definition={},
            payload_sha256=digest({}), availability='active', is_demo=True)),
            insert('scenario_versions', dict(id=object_id('scenario'), scenario_code='compatibility-fixture',
            version_label='v1', rule_version_id=object_id('rule'), schema_version='fixture-v1', hash_format_version='fixture-v1',
            definition={}, asset_manifest={}, payload_sha256=digest({}), availability='active', is_demo=True)),
            insert('games', dict(id=object_id('game1'), game_no='compatibility-fixture', title='仅迁移隔离测试',
            creation_request_key=object_id('game1/creation'), creation_payload_sha256=digest({}),
            creation_hash_format_version='fixture-v1', scenario_version_id=object_id('scenario'), rule_version_id=object_id('rule'),
            active_branch_id=object_id('game1/root'), head_snapshot_id=object_id('game1/initial'),
            control_revision=0, execution_epoch=object_id('game1/epoch'), is_demo=True)),
            insert('game_branches', dict(id=object_id('game1/root'), game_id=object_id('game1'))),
            insert('game_snapshots', snapshot_values('game1')),
            insert('game_members', dict(id=object_id('game1/host'), game_id=object_id('game1'), role='host')),
            insert('game_commands', command_values('command1')), 'COMMIT;']
        self.sql('\n'.join(statements))

    def cleanup_database(self):
        if not self.name.startswith('qiongtu_manual_test_') or len(self.name.rsplit('_', 1)[-1]) != 32:
            raise RuntimeError('临时数据库名称保护失败')
        database.execute('postgres', f'DROP DATABASE {database.identifier(self.name)};')

    def sql(self, text):
        return database.execute(self.name, text)

    def constraint(self):
        return self.sql("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='sandbox.game_commands'::regclass AND conname='game_commands_command_type_check';")

    def audit(self):
        return self.sql("SELECT id,command_type,request_payload,result_payload,completed_at FROM sandbox.game_commands ORDER BY id;"
                        "SELECT id,state_payload,payload_sha256,source_command_id FROM sandbox.game_snapshots ORDER BY id;")

    def command(self, kind):
        value = command_values('compat-' + kind)
        value['command_type'] = kind
        self.sql(insert('game_commands', value))

    def rollback(self):
        self.sql((database.BACKEND_ROOT / 'rollback' / (VERSION + '.sql')).read_text())

    def restore_local_baseline(self):
        self.sql((MIGRATIONS / '012_manual_battle_commands.sql').read_text()
                 + "DELETE FROM infrastructure.schema_migrations WHERE version='013_cross_branch_commands';")

    def restore_upstream_baseline(self):
        for path in sorted(UPSTREAM.glob('*.sql')):
            payload = path.read_bytes()
            self.sql(payload.decode() + 'INSERT INTO infrastructure.schema_migrations(version,sha256) VALUES ('
                     + database.sql_literal(path.stem) + ',' + database.sql_literal(hashlib.sha256(payload).hexdigest()) + ');')
        self.sql("DELETE FROM infrastructure.schema_migrations WHERE version IN "
                 "('011_unit_interactions','012_manual_battle_commands','013_cross_branch_commands');")

    def test_local_upgrade_keeps_manual_history(self):
        self.restore_local_baseline()
        for kind in ('unit_order', 'battle_draft', 'battle_submit', 'battle_resolve'):
            self.command(kind)
        before = self.audit()
        database.apply_migrations(self.name, enable_vectors=False)
        self.assertEqual(before, self.audit())
        for kind in REMOTE_COMMANDS:
            self.assertIn(kind, self.constraint())

    def test_upstream_upgrade_keeps_remote_history_and_constraints(self):
        self.restore_upstream_baseline()
        for kind in REMOTE_COMMANDS:
            self.command(kind)
        values = snapshot_values('game1', name='remote-step-720', revision=1, command='command1')
        values['simulation_step'] = values['state_payload']['lifecycle']['simulation_step'] = 720
        values['state_payload']['manifest']['fivepoint_version'] = 'qinglan-fivepoint-custom-v1.1'
        values['payload_sha256'] = digest(values['state_payload'])
        self.sql(insert('game_snapshots', values))
        before = self.audit()
        steps = self.sql("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conname LIKE '%simulation_step_check' ORDER BY conname;")
        database.apply_migrations(self.name, enable_vectors=False)
        self.assertEqual(before, self.audit())
        self.assertEqual(steps, self.sql("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conname LIKE '%simulation_step_check' ORDER BY conname;"))
        for kind in ('unit_order', 'battle_draft', 'battle_submit', 'battle_resolve'):
            self.command(kind)

    def test_repeated_upgrade_preserves_both_histories(self):
        for kind in REMOTE_COMMANDS | {'battle_draft', 'battle_submit', 'battle_resolve', 'unit_order'}:
            self.command(kind)
        before = self.audit()
        registry = self.sql('SELECT version,sha256,applied_at FROM infrastructure.schema_migrations ORDER BY version;')
        database.apply_migrations(self.name, enable_vectors=False)
        self.assertEqual(before, self.audit())
        self.assertEqual(registry, self.sql('SELECT version,sha256,applied_at FROM infrastructure.schema_migrations ORDER BY version;'))

    def test_unknown_commands_remain_rejected(self):
        with self.assertRaises(database.DatabaseError) as error:
            self.command('not_an_implemented_command')
        self.assertIn('game_commands_command_type_check', str(error.exception))

    def test_local_snapshots_still_reject_step_61(self):
        values = snapshot_values('game1', name='invalid-step', revision=1, command='command1')
        values['simulation_step'] = values['state_payload']['lifecycle']['simulation_step'] = 61
        with self.assertRaises(database.DatabaseError) as error:
            self.sql(insert('game_snapshots', values))
        self.assertIn('game_snapshots_simulation_step_check', str(error.exception))

    def test_crlf_registry_checksums_are_accepted_without_rewriting(self):
        payload = (MIGRATIONS / '012_manual_battle_commands.sql').read_bytes()
        crlf_digest = hashlib.sha256(payload.replace(b'\n', b'\r\n')).hexdigest()
        self.sql("UPDATE infrastructure.schema_migrations SET sha256='" + crlf_digest + "' WHERE version='012_manual_battle_commands';")
        database.apply_migrations(self.name, enable_vectors=False)
        self.assertEqual(crlf_digest, self.sql("SELECT sha256 FROM infrastructure.schema_migrations WHERE version='012_manual_battle_commands';").strip())

    def test_semantic_digest_mismatch_rolls_back_entire_upgrade(self):
        self.restore_local_baseline()
        self.sql("UPDATE infrastructure.schema_migrations SET sha256=repeat('0',64) WHERE version='012_manual_battle_commands';")
        before, constraint = self.audit(), self.constraint()
        with self.assertRaises(database.DatabaseError) as error:
            database.apply_migrations(self.name, enable_vectors=False)
        self.assertIn('已应用迁移文件发生变化', str(error.exception))
        self.assertEqual(before, self.audit())
        self.assertEqual(constraint, self.constraint())
        self.assertEqual('0', self.sql("SELECT count(*) FROM infrastructure.schema_migrations WHERE version='013_cross_branch_commands';").strip())

    def test_failure_after_new_ddl_restores_remote_schema_and_registry(self):
        self.restore_upstream_baseline()
        self.command('fivepoint_planning')
        self.sql("INSERT INTO infrastructure.schema_migrations(version,sha256) "
                 "VALUES ('012_manual_battle_commands',repeat('0',64));")
        before, constraint = self.audit(), self.constraint()
        registry = self.sql('SELECT version,sha256,applied_at FROM infrastructure.schema_migrations ORDER BY version;')
        # 011本地迁移先扩展CHECK并登记；随后012坏摘要必须连同前序DDL一并回滚。
        with self.assertRaises(database.DatabaseError) as error:
            database.apply_migrations(self.name, enable_vectors=False)
        self.assertIn('已应用迁移文件发生变化', str(error.exception))
        self.assertEqual(before, self.audit())
        self.assertEqual(constraint, self.constraint())
        self.assertEqual(registry, self.sql('SELECT version,sha256,applied_at FROM infrastructure.schema_migrations ORDER BY version;'))
        self.assertNotIn('battle_resolve', self.constraint())

    def test_local_rollback_and_reapply_preserve_manual_history(self):
        self.command('battle_draft')
        before = self.audit()
        self.rollback()
        self.assertEqual(before, self.audit())
        self.assertNotIn('enable_fivepoint', self.constraint())
        self.assertIn('battle_resolve', self.constraint())
        database.apply_migrations(self.name, enable_vectors=False)
        self.assertIn('enable_fivepoint', self.constraint())
        self.assertEqual(before, self.audit())

    def test_rollback_refuses_remote_history_atomically(self):
        self.command('fivepoint_planning')
        before, constraint = self.audit(), self.constraint()
        with self.assertRaises(database.DatabaseError) as error:
            self.rollback()
        self.assertIn('已有五点协议历史', str(error.exception))
        self.assertEqual(before, self.audit())
        self.assertEqual(constraint, self.constraint())

    def test_rollback_refuses_remote_or_later_migration_registration(self):
        for version in ('012_fivepoint_planning', '013_another_branch', '014_fixture'):
            with self.subTest(version=version):
                self.sql("INSERT INTO infrastructure.schema_migrations(version,sha256) VALUES ('" + version + "',repeat('a',64));")
                before = self.constraint()
                with self.assertRaises(database.DatabaseError) as error:
                    self.rollback()
                self.assertIn('存在远端或后续迁移', str(error.exception))
                self.assertEqual(before, self.constraint())
                self.sql("DELETE FROM infrastructure.schema_migrations WHERE version='" + version + "';")

    def test_older_rollback_requires_reverse_order(self):
        before = self.constraint()
        with self.assertRaises(database.DatabaseError) as error:
            self.sql((database.BACKEND_ROOT / 'rollback/012_manual_battle_commands.sql').read_text())
        self.assertIn('须先按逆序回退', str(error.exception))
        self.assertEqual(before, self.constraint())

    def test_rollback_accepts_crlf_registration(self):
        for version in ('012_manual_battle_commands', VERSION):
            payload = (MIGRATIONS / (version + '.sql')).read_bytes().replace(b'\r\n', b'\n')
            checksum = hashlib.sha256(payload.replace(b'\n', b'\r\n')).hexdigest()
            self.sql("UPDATE infrastructure.schema_migrations SET sha256='" + checksum + "' WHERE version='" + version + "';")
        self.rollback()
        self.assertNotIn('enable_fivepoint', self.constraint())
        database.apply_migrations(self.name, enable_vectors=False)
        self.assertIn('enable_fivepoint', self.constraint())

    def test_rollback_refuses_fivepoint_snapshot(self):
        values = snapshot_values('game1', name='fivepoint-history', revision=1, command='command1')
        values['state_payload']['manifest']['fivepoint_version'] = 'fixture-only'
        self.sql(insert('game_snapshots', values))
        before, constraint = self.audit(), self.constraint()
        with self.assertRaises(database.DatabaseError) as error:
            self.rollback()
        self.assertIn('已有五点协议历史', str(error.exception))
        self.assertEqual(before, self.audit())
        self.assertEqual(constraint, self.constraint())

    def test_rollback_refuses_corrupt_registration(self):
        self.sql("UPDATE infrastructure.schema_migrations SET sha256=repeat('0',64) WHERE version='013_cross_branch_commands';")
        before = self.constraint()
        with self.assertRaises(database.DatabaseError) as error:
            self.rollback()
        self.assertIn('013未应用或摘要不匹配', str(error.exception))
        self.assertEqual(before, self.constraint())
