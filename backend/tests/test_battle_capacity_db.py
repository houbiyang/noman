"""隔离PG验证容量草案登记、旧局隔离与失败回滚，不启动用户真实对局。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import unittest
from uuid import UUID, uuid4
from unittest.mock import patch

from psycopg.types.json import Jsonb

from backend import battle_capacity, database, rulebook
from backend.contracts import CreateGame
from backend.runtime import ServiceError, Settings, sha256
from backend.service import SandboxService


class BattleCapacityDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_name = 'qiongtu_capacity_test_' + uuid4().hex
        database.execute('postgres', f'CREATE DATABASE {database.identifier(cls.database_name)} TEMPLATE template0 ENCODING \'UTF8\';')
        cls.addClassCleanup(cls.cleanup_database)
        database.apply_migrations(cls.database_name)
        database.configure_privileges(cls.database_name)
        config = json.loads(database.APPLICATION_CONFIG_PATH.read_text(encoding='utf-8'))
        config['database'] = cls.database_name
        cls.settings = Settings(config, 'capacity-test-signing', 'capacity-test-bootstrap', False)
        cls.service = SandboxService(cls.settings)
        cls.service.seed_versions()
        cls.game_id = cls.service.create_game(CreateGame(title='容量版本隔离测试', is_demo=True), uuid4())[0]['game_id']

    @classmethod
    def cleanup_database(cls):
        if not cls.database_name.startswith('qiongtu_capacity_test_') or len(cls.database_name.rsplit('_', 1)[-1]) != 32:
            raise RuntimeError('测试数据库名称保护失败')
        database.execute('postgres', f'DROP DATABASE {database.identifier(cls.database_name)};')

    def test_seed_registers_separate_inactive_capacity_version(self):
        value = battle_capacity.definition()
        with self.settings.connect() as connection:
            row = connection.execute('SELECT * FROM rule_versions WHERE id=%s', (battle_capacity.identity(value),)).fetchone()
            self.assertEqual(row['definition'], value)
            self.assertEqual(row['payload_sha256'], sha256(value))
            self.assertEqual(row['availability'], 'draft')
            self.assertTrue(row['is_demo'])
            for table in ('scenario_versions', 'games'):
                self.assertEqual(connection.execute(f'SELECT count(*) AS n FROM {table} WHERE rule_version_id=%s',
                                                   (row['id'],)).fetchone()['n'], 0)

    def test_reseed_does_not_modify_base_games_or_snapshots(self):
        def snapshot():
            with self.settings.connect() as connection:
                return {table: connection.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
                        for table in ('rule_versions', 'scenario_versions', 'games', 'game_snapshots', 'game_commands')}
        before = snapshot()
        for _ in range(2):
            self.service.seed_versions()
            with self.settings.connect() as connection:
                self.assertFalse(battle_capacity.register(connection)['inserted'])
        self.assertEqual(before, snapshot())
        base = rulebook.definition()
        with self.settings.connect() as connection:
            row = connection.execute('SELECT definition,payload_sha256 FROM rule_versions WHERE id=%s', (rulebook.identity(base),)).fetchone()
        self.assertEqual(row['definition'], base)
        self.assertEqual(row['payload_sha256'], sha256(base))

    def test_missing_source_version_is_rejected_without_writes(self):
        value = battle_capacity.definition()
        value['rulebook']['rule_version_id'] = str(uuid4())
        with self.settings.connect() as connection:
            before = connection.execute('SELECT count(*) AS n FROM rule_versions').fetchone()['n']
        with patch.object(battle_capacity, 'definition', return_value=value):
            with self.assertRaisesRegex(ValueError, '引用的总册版本'):
                with self.settings.connect() as connection:
                    battle_capacity.register(connection)
        with self.settings.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM rule_versions').fetchone()['n'], before)

    def test_concurrent_first_registration_is_idempotent(self):
        value = battle_capacity.definition()
        value['capacity']['release_id'] += '-concurrency-' + uuid4().hex
        def register_once(_):
            with self.settings.connect() as connection:
                return battle_capacity.register(connection)
        with patch.object(battle_capacity, 'definition', return_value=value):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(register_once, range(2)))
        self.assertEqual(sum(r['inserted'] for r in results), 1)
        self.assertEqual(results[0]['rule_version_id'], results[1]['rule_version_id'])
        with self.settings.connect() as connection:
            row = connection.execute('SELECT definition FROM rule_versions WHERE id=%s', (results[0]['rule_version_id'],)).fetchone()
        self.assertEqual(row['definition'], value)

    def test_capacity_draft_cannot_be_used_to_start_legacy_game(self):
        with self.assertRaises(ServiceError) as error:
            self.service.create_game(CreateGame(title='拒绝用容量草案开局', is_demo=True,
                rule_version_id=UUID(battle_capacity.identity(battle_capacity.definition()))), uuid4())
        self.assertEqual(error.exception.code, 'VERSION_UNSUPPORTED')

    def test_name_collision_rejects_and_rolls_back(self):
        value = battle_capacity.definition()
        value['capacity']['release_id'] += '-collision-' + uuid4().hex
        forged = deepcopy(value)
        forged['capacity']['maximum_per_type'] = 11
        with self.settings.connect() as connection:
            before = connection.execute('SELECT count(*) AS n FROM rule_versions').fetchone()['n']
        # App role is intentionally INSERT/SELECT only for immutable versions.
        # Create a conflicting unpublished label in an isolated transaction, never UPDATE old rows.
        with self.settings.connect() as connection:
            try:
                connection.execute('''INSERT INTO rule_versions
                    (id,rule_code,version_label,schema_version,hash_format_version,definition,payload_sha256,availability,is_demo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'draft',true)''',
                    (battle_capacity.identity(value), battle_capacity.RULE_CODE, value['capacity']['release_id'],
                     value['schema_version'], rulebook.HASH_VERSION, Jsonb(forged), sha256(forged)))
                with patch.object(battle_capacity, 'definition', return_value=value):
                    with self.assertRaisesRegex(ValueError, '同名容量版本内容不同'):
                        battle_capacity.register(connection)
                row = connection.execute('SELECT definition FROM rule_versions WHERE id=%s',
                                         (battle_capacity.identity(value),)).fetchone()
                self.assertEqual(row['definition'], forged)
            finally:
                connection.rollback()
        with self.settings.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM rule_versions').fetchone()['n'], before)


if __name__ == '__main__':
    unittest.main()
