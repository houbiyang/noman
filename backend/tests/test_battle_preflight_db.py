"""随机隔离PG/HTTP权限与无写入审计；不启动用户现有对局。"""
from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import database, rulebook
from backend.contracts import CreateGame
from backend.main import create_app
from backend.runtime import Settings
from backend.service import SandboxService


class PreflightDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_name = 'qiongtu_preflight_test_' + uuid4().hex
        database.execute('postgres', f"CREATE DATABASE {database.identifier(cls.database_name)} TEMPLATE template0 ENCODING 'UTF8';")
        cls.addClassCleanup(cls.cleanup_database)
        database.apply_migrations(cls.database_name)
        database.configure_privileges(cls.database_name)
        config = json.loads(database.APPLICATION_CONFIG_PATH.read_text(encoding='utf-8'))
        config['database'] = cls.database_name
        cls.settings = Settings(config, 'preflight-test-signing', 'preflight-test-bootstrap', False)
        cls.service = SandboxService(cls.settings)
        cls.service.seed_versions()
        cls.game_id = cls.service.create_game(CreateGame(title='开战只读检查隔离验证', is_demo=True), uuid4())[0]['game_id']
        cls.path = f'/api/v1/games/{cls.game_id}/battle/preflight'
        cls.client = TestClient(create_app(cls.settings))
        cls.addClassCleanup(cls.client.close)
        cls.headers = {role: {'Authorization': 'Bearer ' + cls.service.issue_session(cls.game_id, role)['access_token']}
                       for role in ('host', 'red', 'blue')}

    @classmethod
    def cleanup_database(cls):
        if not cls.database_name.startswith('qiongtu_preflight_test_') or len(cls.database_name.rsplit('_', 1)[-1]) != 32:
            raise RuntimeError('测试数据库名称保护失败')
        database.execute('postgres', f'DROP DATABASE {database.identifier(cls.database_name)};')

    def snapshot(self):
        with self.settings.connect() as connection:
            return {t: connection.execute(f'SELECT * FROM {t} ORDER BY id').fetchall()
                    for t in ('rule_versions', 'scenario_versions', 'games', 'game_snapshots', 'game_commands',
                              'game_events', 'game_saves', 'access_sessions')}

    def test_authorization_is_required_before_reading_or_probing_models(self):
        for role in (None, 'red', 'blue'):
            with self.subTest(role=role):
                response = self.client.post(self.path, headers=self.headers.get(role, {}), json={'mode': 'agent'})
                self.assertEqual(response.status_code, 401 if role is None else 403)
        response = self.client.post(f'/api/v1/games/{uuid4()}/battle/preflight', headers=self.headers['host'], json={'mode': 'manual'})
        self.assertEqual(response.status_code, 404)

    def test_core_checks_use_real_registration_but_do_not_activate_legacy_game(self):
        before = self.snapshot()
        response = self.client.post(self.path, headers=self.headers['host'], json={'mode': 'manual'})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        facts = {c['key']: c for c in result['checks']}
        for key in ('backend_database', 'deployment', 'rulebook', 'capacity', 'map'):
            self.assertEqual(facts[key]['status'], 'passed', key)
        self.assertEqual(facts['battle_engine']['status'], 'failed')
        self.assertEqual(facts['battle_scenario']['status'], 'failed')
        self.assertFalse(result['ready'])
        self.assertFalse(result['can_start'])
        self.assertFalse(result['manual_fallback_available'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_source_failure_is_reported_without_overwriting_versions(self):
        before = self.snapshot()
        with patch.object(rulebook, 'definition', side_effect=ValueError('PRIVATE SOURCE ERROR')):
            response = self.client.post(self.path, headers=self.headers['host'], json={'mode': 'manual'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(next(c for c in response.json()['checks'] if c['key'] == 'rulebook')['status'], 'failed')
        self.assertNotIn('PRIVATE SOURCE', response.text)
        self.assertFalse(response.json()['ready'])
        self.assertEqual(before, self.snapshot())

    def test_concurrent_repeated_read_only_checks_do_not_write(self):
        before = self.snapshot()
        def once(_):
            response = self.client.post(self.path, headers=self.headers['host'], json={'mode': 'manual'})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(once, range(3)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertEqual(before, self.snapshot())

    def test_paid_probe_requires_explicit_consent(self):
        before = self.snapshot()
        response = self.client.post(self.path, headers=self.headers['host'], json={'mode': 'agent', 'probe_ai': True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(before, self.snapshot())
