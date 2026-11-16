"""独立 PostgreSQL 数据库验证总册登记，不触碰本机正在使用的对局。"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from backend import database, rulebook
from backend.contracts import CreateGame
from backend.runtime import ServiceError, Settings, sha256
from backend.service import SandboxService


class RulebookDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_name = 'qiongtu_rulebook_test_' + uuid4().hex
        database.execute('postgres', f'CREATE DATABASE {database.identifier(cls.database_name)} TEMPLATE template0 ENCODING \'UTF8\';')
        cls.addClassCleanup(cls.cleanup_database)
        database.apply_migrations(cls.database_name)
        database.configure_privileges(cls.database_name)
        config = json.loads(database.APPLICATION_CONFIG_PATH.read_text(encoding='utf-8'))
        config['database'] = cls.database_name
        cls.settings = Settings(config, 'rulebook-test-signing', 'rulebook-test-bootstrap', False)
        cls.service = SandboxService(cls.settings)
        cls.service.seed_versions()

    @classmethod
    def cleanup_database(cls):
        if not cls.database_name.startswith('qiongtu_rulebook_test_') or len(cls.database_name.rsplit('_', 1)[-1]) != 32:
            raise RuntimeError('测试数据库名称保护失败')
        database.execute('postgres', f'DROP DATABASE {database.identifier(cls.database_name)};')

    def versions(self):
        with self.settings.connect() as connection:
            return connection.execute('SELECT * FROM rule_versions ORDER BY id').fetchall()

    def test_seed_registers_complete_draft(self):
        value = rulebook.definition()
        with self.settings.connect() as connection:
            row = connection.execute('SELECT * FROM rule_versions WHERE id=%s', (rulebook.identity(value),)).fetchone()
        self.assertEqual(row['definition'], value)
        self.assertEqual(row['payload_sha256'], sha256(value))
        self.assertEqual(row['availability'], 'draft')
        self.assertFalse(row['is_demo'])
        self.assertEqual(row['hash_format_version'], rulebook.HASH_VERSION)
        self.assertEqual(len(row['definition']['source']['tables']), 32)

    def test_repeated_register_and_seed_are_idempotent(self):
        before = self.versions()
        for _ in range(2):
            with self.settings.connect() as connection:
                self.assertFalse(rulebook.register(connection)['inserted'])
            self.service.seed_versions()
        self.assertEqual(self.versions(), before)

    def test_tampered_input_is_rejected_without_writes(self):
        before = self.versions()
        value = rulebook.definition()
        value['parameters']['attack']['damage'] = 999
        with self.assertRaisesRegex(ValueError, '只能登记'):
            with self.settings.connect() as connection:
                rulebook.register(connection, value)
        self.assertEqual(self.versions(), before)

    def test_same_label_different_payload_is_never_overwritten(self):
        before = self.versions()
        value = deepcopy(rulebook.definition())
        value['parameters']['version'] += '-collision-test'
        forged = deepcopy(value)
        forged['parameters']['attack']['damage'] = 999
        # 只在此测试事务中制造冲突；无论成功失败均回滚，不污染其他测试。
        with self.settings.connect() as connection:
            try:
                connection.execute('''INSERT INTO rule_versions
                    (id,rule_code,version_label,schema_version,hash_format_version,definition,payload_sha256,availability,is_demo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'draft',false)''',
                    (rulebook.identity(value), rulebook.RULE_CODE, value['parameters']['version'],
                     value['schema_version'], rulebook.HASH_VERSION, Jsonb(forged), sha256(forged)))
                with patch.object(rulebook, 'definition', return_value=value):
                    with self.assertRaisesRegex(ValueError, '同名规则版本内容不同'):
                        rulebook.register(connection)
                row = connection.execute('SELECT definition FROM rule_versions WHERE id=%s',
                                         (rulebook.identity(value),)).fetchone()
                self.assertEqual(row['definition'], forged)
            finally:
                connection.rollback()
        self.assertEqual(self.versions(), before)

    def test_draft_cannot_be_used_to_create_game(self):
        with self.assertRaises(ServiceError) as raised:
            self.service.create_game(CreateGame(title='不允许草案开局', is_demo=True,
                rule_version_id=UUID(rulebook.identity(rulebook.definition()))), uuid4())
        self.assertEqual(raised.exception.code, 'VERSION_UNSUPPORTED')

    def test_registration_does_not_change_existing_game(self):
        self.service.create_game(CreateGame(title='总册隔离测试', is_demo=True), uuid4())
        def snapshot():
            with self.settings.connect() as connection:
                return {
                    'games': connection.execute('SELECT * FROM games ORDER BY id').fetchall(),
                    'snapshots': connection.execute('SELECT * FROM game_snapshots ORDER BY id').fetchall(),
                    'commands': connection.execute('SELECT * FROM game_commands ORDER BY id').fetchall(),
                    'scenarios': connection.execute('SELECT * FROM scenario_versions ORDER BY id').fetchall(),
                }
        before = snapshot()
        with self.settings.connect() as connection:
            rulebook.register(connection)
        self.service.seed_versions()
        self.assertEqual(snapshot(), before)


if __name__ == '__main__':
    unittest.main()
