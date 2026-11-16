"""随机PG库专用新局fixture；不发布创建入口、不修改/迁移用户旧局。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import unittest
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from backend import battle_scenario, database
from backend.battle_replay_store import append_round
from backend.contracts import CreateGame
from backend.main import create_app
from backend.runtime import Settings, sha256
from backend.service import HASH_VERSION, SandboxService, insert
from backend.tests.test_manual_battle import FixtureExecutor, intent, state_fixture


class ManualBattleDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_name = 'qiongtu_manual_test_' + uuid4().hex
        database.execute('postgres', f'CREATE DATABASE {database.identifier(cls.database_name)} TEMPLATE template0 ENCODING \'UTF8\';')
        cls.addClassCleanup(cls.cleanup_database)
        database.apply_migrations(cls.database_name)
        database.configure_privileges(cls.database_name)
        config = json.loads(database.APPLICATION_CONFIG_PATH.read_text())
        config['database'] = cls.database_name
        cls.settings = Settings(config, 'manual-isolated-signing', 'manual-isolated-bootstrap', False)
        cls.service = SandboxService(cls.settings)
        cls.service.seed_versions()
        cls.client = TestClient(create_app(cls.settings))
        cls.addClassCleanup(cls.client.close)

    @classmethod
    def cleanup_database(cls):
        if not cls.database_name.startswith('qiongtu_manual_test_') or len(cls.database_name.rsplit('_', 1)[-1]) != 32:
            raise RuntimeError('测试库名称保护失败')
        database.execute('postgres', f'DROP DATABASE {database.identifier(cls.database_name)};')

    def setUp(self):
        self.game_id = self.make_game()
        self.path = f'/api/v1/games/{self.game_id}'
        self.headers = {}
        for role in ('blue', 'red', 'host'):
            response = self.client.post(
                f'/api/v1/bootstrap/games/{self.game_id}/sessions',
                headers={'X-Bootstrap-Key': self.settings.bootstrap_key}, json=dict(role=role))
            self.assertEqual(response.status_code, 200, response.text)
            self.headers[role] = {'Authorization': 'Bearer ' + response.json()['access_token']}

    def make_game(self, state=None):
        """只在随机库构造协议夹具；draft场景仍未通过生产开战验收。"""
        game_id, branch_id, snapshot_id, epoch = uuid4(), uuid4(), uuid4(), uuid4()
        state = state_fixture() if state is None else deepcopy(state)
        with self.settings.connect() as connection:
            registered = battle_scenario.register(connection)
            definition = battle_scenario.definition()
            state['manifest'].update(scenario_version_id=registered['scenario_version_id'],
                rule_version_id=definition['rule_version_id'])
            insert(connection, 'games', dict(id=game_id, game_no='QT-' + game_id.hex[:16].upper(), title='仅协议测试夹具',
                creation_request_key=uuid4(), creation_payload_sha256=sha256({}), creation_hash_format_version=HASH_VERSION,
                scenario_version_id=registered['scenario_version_id'], rule_version_id=definition['rule_version_id'],
                active_branch_id=branch_id, head_snapshot_id=snapshot_id, control_revision=0, execution_epoch=epoch, is_demo=True))
            insert(connection, 'game_branches', dict(id=branch_id, game_id=game_id))
            for role in ('host', 'red', 'blue'):
                insert(connection, 'game_members', dict(id=uuid4(), game_id=game_id, role=role))
            self.service._snapshot(connection, game_id, branch_id, snapshot_id, state, 0, 0, kind='initial')
        return str(game_id)

    def view(self, role='host'):
        response = self.client.get(self.path + '/view', headers=self.headers[role])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def command(self, kind, role='host', payload=None, key=None, body=None):
        body = body or dict(context=self.view(role)['context'], command_type=kind, payload={'round_number': 1} if payload is None else payload)
        return self.client.post(self.path + '/commands', json=body,
            headers=dict(self.headers[role], **{'Idempotency-Key': key or str(uuid4())}))

    def state(self):
        with self.settings.connect() as connection:
            return self.service._game(connection, self.game_id)['state_payload']

    def test_private_drafts_do_not_advance_resources_or_publish_fake_ai(self):
        before = self.state()
        response = self.command('battle_draft', 'blue', dict(round_number=1, intents=[intent()]))
        self.assertEqual(response.status_code, 200, response.text)
        after = self.state()
        self.assertEqual(before['units'], after['units'])
        self.assertEqual(before['lifecycle'], after['lifecycle'])
        self.assertEqual(len(self.view('blue')['manual_battle']['own_drafts']), 1)
        for role in ('host', 'red'):
            view = self.view(role)
            self.assertEqual(view['manual_battle']['own_drafts'], [])
            self.assertNotIn('own_plan', view['decisions'])
            self.assertEqual(view['decisions']['source'], 'local_manual')
        with self.settings.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM proposals WHERE game_id=%s', (self.game_id,)).fetchone()['n'], 0)
        self.assertEqual(self.view()['clock']['simulation_step'], 0)

    def test_locked_side_rejects_edit_host_cannot_draft_or_resolve_without_engine(self):
        self.assertEqual(self.command('battle_submit', 'blue').status_code, 200)
        response = self.command('battle_draft', 'blue', dict(round_number=1, intents=[intent()]))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'SIDE_ALREADY_SUBMITTED')
        response = self.command('battle_draft', 'host', dict(round_number=1, intents=[intent()]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.command('battle_submit', 'red').status_code, 200)
        before = self.state()
        response = self.command('battle_resolve')
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.state(), before)
        self.assertFalse(self.view()['manual_battle']['engine_ready'])
        self.assertFalse(self.view()['manual_battle']['can_resolve'])

    def test_merge_conflict_is_422_receipt_not_500_and_keeps_draft(self):
        takeoff = dict(unit_id='blue-recon', life_id='blue-recon-life', task=dict(action='takeoff'))
        move = dict(unit_id='blue-recon', life_id='blue-recon-life', movement=dict(action='move', position=dict(x='20', y='20')))
        self.assertEqual(self.command('battle_draft', 'blue', dict(round_number=1, intents=[takeoff])).status_code, 200)
        before = self.state()
        response = self.command('battle_draft', 'blue', dict(round_number=1, intents=[move]))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()['error']['code'], 'INTENT_CONFLICT')
        self.assertEqual(before, self.state())

    def test_same_key_concurrent_submission_creates_exactly_one_snapshot(self):
        before = self.view('blue')['context']; key = str(uuid4())
        body = dict(context=before, command_type='battle_submit', payload={'round_number': 1})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.command('battle_submit', 'blue', key=key, body=body), range(2)))
        self.assertEqual([r.status_code for r in results], [200, 200])
        self.assertEqual(results[0].json()['command_id'], results[1].json()['command_id'])
        with self.settings.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM game_snapshots WHERE game_id=%s', (self.game_id,)).fetchone()['n'], 2)
        # 不同键/过期上下文不能覆盖已锁定状态。
        stale = self.command('battle_submit', 'red', body=body)
        self.assertNotEqual(stale.status_code, 200)

    def test_legacy_commands_cannot_bypass_barrier_and_scheduler_is_idle(self):
        before = self.state()
        for kind, payload in (('advance_step', {}), ('continue', {}), ('start_game', {})):
            response = self.command(kind, payload=payload)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['error']['code'], 'MANUAL_BARRIER_REQUIRED')
        self.service.tick()
        self.assertEqual(before, self.state())

    def test_replay_authentication_role_and_game_scope(self):
        url = self.path + '/battle/rounds'
        self.assertEqual(self.client.get(url).status_code, 401)
        for role in ('red', 'blue'):
            self.assertEqual(self.client.get(url, headers=self.headers[role]).status_code, 403)
        result = self.client.get(url, headers=self.headers['host'])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['items'], [])
        self.assertEqual(self.client.get(url + '/1', headers=self.headers['host']).status_code, 404)
        for number in (0, 61):
            self.assertEqual(self.client.get(url + '/' + str(number), headers=self.headers['host']).status_code, 422)
        other = self.make_game()
        self.assertEqual(self.client.get(f'/api/v1/games/{other}/battle/rounds', headers=self.headers['host']).status_code, 404)

    def insert_round_fixture(self, *, persist=True, mutate_record=None):
        """假执行器只验证存储/只读；不注入SandboxService，不能裁决真实战斗。"""
        from backend import manual_battle
        with self.settings.connect() as connection:
            game = self.service._game(connection, self.game_id, lock=True)
            state = game['state_payload']
            number = state['manual_battle']['round_number']
            revision = game['snapshot_revision'] + 1
            for side in ('blue', 'red'): state = manual_battle.submit(state, side, {'round_number': number})
            after, record = manual_battle.settle(state, 'host', {'round_number': number}, FixtureExecutor())
            if mutate_record is not None:
                from backend.tests.test_battle_replay import reseal
                mutate_record(record); reseal(record)
                after['manual_battle']['previous_round_sha256'] = record['payload_sha256']
            command_id, snapshot_id = uuid4(), uuid4()
            member = connection.execute("SELECT id FROM game_members WHERE game_id=%s AND role='host'", (self.game_id,)).fetchone()['id']
            insert(connection, 'game_commands', dict(id=command_id, game_id=self.game_id, origin_branch_id=game['active_branch_id'],
                actor_member_id=member, request_key=uuid4(), request_hash=sha256({}), hash_format_version=HASH_VERSION,
                command_type='battle_resolve', expected_control_revision=game['control_revision'], base_snapshot_id=game['head_snapshot_id'],
                request_payload={'round_number': number}, source_kind='human', execution_epoch=game['execution_epoch'], status='accepted'))
            self.service._snapshot(connection, self.game_id, game['active_branch_id'], snapshot_id, after, revision, number, command_id=command_id)
            args = dict(game_id=self.game_id, branch_id=game['active_branch_id'], snapshot_id=snapshot_id,
                snapshot_revision=revision, command_id=command_id, record=record)
            if persist: append_round(connection, **args)
            connection.execute('UPDATE games SET head_snapshot_id=%s,control_revision=control_revision+1 WHERE id=%s', (snapshot_id, self.game_id))
        return args

    def test_replay_read_is_immutable_no_commands_no_duplicate_effects(self):
        args = self.insert_round_fixture()
        def counts():
            with self.settings.connect() as connection:
                return [connection.execute(f'SELECT count(*) AS n FROM {table} WHERE game_id=%s', (self.game_id,)).fetchone()['n']
                        for table in ('game_snapshots', 'game_commands', 'game_events')]
        before, state = counts(), self.state()
        for _ in range(3):
            response = self.client.get(self.path + '/battle/rounds/1', headers=self.headers['host'])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['record'], args['record'])
            self.assertTrue(response.json()['read_only'])
        index = self.client.get(self.path + '/battle/rounds', headers=self.headers['host']).json()
        self.assertEqual(len(index['items']), 1)
        self.assertNotIn('frames', str(index))
        self.assertEqual((before, state), (counts(), self.state()))
        self.assertNotIn('battle_round', str(self.view()['timeline']))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.settings.connect() as connection: append_round(connection, **args)
        with self.assertRaises(psycopg.Error):
            with self.settings.connect() as connection:
                connection.execute("UPDATE game_events SET payload='{}' WHERE game_id=%s", (self.game_id,))

    def test_old_game_cannot_enable_new_mode(self):
        game_id = self.service.create_game(CreateGame(title='旧版不迁移', is_demo=True), uuid4())[0]['game_id']
        response = self.client.post(f'/api/v1/bootstrap/games/{game_id}/sessions',
            headers={'X-Bootstrap-Key': self.settings.bootstrap_key}, json=dict(role='host'))
        headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
        path = f'/api/v1/games/{game_id}'
        view = self.client.get(path + '/view', headers=headers).json()
        response = self.client.post(path + '/commands', headers=dict(headers, **{'Idempotency-Key': str(uuid4())}),
            json=dict(context=view['context'], command_type='battle_submit', payload={'round_number': 1}))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'MANUAL_MODE_REQUIRED')
        self.assertEqual(self.client.get(path + '/battle/rounds', headers=headers).status_code, 409)

    def test_replay_second_round_must_join_persisted_predecessor(self):
        first = self.insert_round_fixture()
        second = self.insert_round_fixture()
        self.assertEqual(second['record']['previous_round_sha256'], first['record']['payload_sha256'])
        response = self.client.get(self.path + '/battle/rounds', headers=self.headers['host'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r['round_number'] for r in response.json()['items']], [1, 2])

    def test_replay_missing_predecessor_is_rejected_atomically(self):
        from backend.runtime import ServiceError
        self.insert_round_fixture(persist=False)
        before = self.state()
        with self.assertRaises(ServiceError) as caught: self.insert_round_fixture()
        self.assertEqual(caught.exception.code, 'REPLAY_PREFIX_INVALID')
        self.assertEqual(self.state(), before)

    def test_replay_different_predecessor_world_rejected_atomically(self):
        from backend.runtime import ServiceError
        self.insert_round_fixture()
        before = self.state()
        with self.assertRaises(ServiceError) as caught:
            self.insert_round_fixture(mutate_record=lambda record: record['initial_world'].update(blue_score='1'))
        self.assertEqual(caught.exception.code, 'REPLAY_CHAIN_INVALID')
        self.assertEqual(self.state(), before)

    def test_rollback_refuses_manual_history_without_altering_constraint(self):
        rollback = (database.BACKEND_ROOT / 'rollback' / '012_manual_battle_commands.sql').read_text()
        with self.settings.connect() as connection:
            before = connection.execute("SELECT pg_get_constraintdef(oid) AS c FROM pg_constraint WHERE conrelid='sandbox.game_commands'::regclass AND conname='game_commands_command_type_check'").fetchone()['c']
        with self.assertRaises(database.DatabaseError): database.execute(self.database_name, rollback)
        with self.settings.connect() as connection:
            after = connection.execute("SELECT pg_get_constraintdef(oid) AS c FROM pg_constraint WHERE conrelid='sandbox.game_commands'::regclass AND conname='game_commands_command_type_check'").fetchone()['c']
        self.assertEqual(before, after)

    def test_empty_database_migration_rollback_and_reapply(self):
        name = 'qiongtu_manual_test_' + uuid4().hex
        database.execute('postgres', f"CREATE DATABASE {database.identifier(name)} TEMPLATE template0 ENCODING 'UTF8';")
        try:
            database.apply_migrations(name)
            rollback = (database.BACKEND_ROOT / 'rollback' / '012_manual_battle_commands.sql').read_text()
            database.execute(name, rollback)
            self.assertEqual(database.execute(name, "SELECT count(*) FROM infrastructure.schema_migrations WHERE version='012_manual_battle_commands';").strip(), '0')
            constraint = database.execute(name, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='sandbox.game_commands'::regclass AND conname='game_commands_command_type_check';")
            self.assertNotIn('battle_resolve', constraint)
            database.apply_migrations(name)
            self.assertEqual(database.execute(name, "SELECT count(*) FROM infrastructure.schema_migrations WHERE version='012_manual_battle_commands';").strip(), '1')
            constraint = database.execute(name, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='sandbox.game_commands'::regclass AND conname='game_commands_command_type_check';")
            self.assertIn('battle_resolve', constraint)
        finally:
            database.execute('postgres', f'DROP DATABASE {database.identifier(name)};')

    def execution_game(self, *, lock=True, configure=None):
        """只在随机库创建精确账本夹具，显式注入合成地图，生产装配保持None。"""
        from backend.battle_execution import Executor
        from backend.tests.test_battle_execution import FixtureGeometry, state_fixture as exact_fixture
        exact = exact_fixture(); base = state_fixture()
        base['manifest'].update(exact['manifest'])
        for key in ('units', 'observations', 'manual_battle', 'battle_points', 'battle_ledger'):
            base[key] = exact[key]
        if configure is not None:
            configure(base)
        self.geometry = FixtureGeometry()
        for unit in base['units']:
            unit['position'] = self.geometry.display_position(unit['position_mm'])
        self.game_id = self.make_game(base)
        self.path = f'/api/v1/games/{self.game_id}'
        for role in ('blue', 'red', 'host'):
            response = self.client.post(f'/api/v1/bootstrap/games/{self.game_id}/sessions',
                headers={'X-Bootstrap-Key': self.settings.bootstrap_key}, json=dict(role=role))
            self.assertEqual(response.status_code, 200, response.text)
            self.headers[role] = {'Authorization': 'Bearer ' + response.json()['access_token']}
        self.client.app.state.service.battle_executor = Executor(self.geometry)
        self.addCleanup(setattr, self.client.app.state.service, 'battle_executor', None)
        self.assertTrue(self.view()['manual_battle']['engine_ready'])
        if lock:
            for role in ('blue', 'red'):
                self.assertEqual(self.command('battle_submit', role).status_code, 200)
            self.assertTrue(self.view()['manual_battle']['can_resolve'])

    def execution_counts(self):
        with self.settings.connect() as connection:
            return tuple(connection.execute(f'SELECT count(*) AS n FROM {table} WHERE game_id=%s',
                                            (self.game_id,)).fetchone()['n']
                         for table in ('game_snapshots', 'game_events', 'game_saves'))

    def test_execution_snapshot_replay_world_revision_same_transaction_and_idempotent(self):
        self.execution_game()
        state = self.state(); before = self.execution_counts()
        context = self.view()['context']; key = str(uuid4())
        body = dict(context=context, command_type='battle_resolve', payload={'round_number': 1})
        response = self.command('battle_resolve', key=key, body=body)
        self.assertEqual(response.status_code, 200, response.text)
        after = self.state()
        self.assertEqual(after['battle_ledger']['elapsed_seconds'], 60)
        self.assertEqual(after['manual_battle']['completed_rounds'], 1)
        record = self.client.get(self.path + '/battle/rounds/1', headers=self.headers['host'])
        self.assertEqual(record.status_code, 200, record.text)
        self.assertEqual(len(record.json()['record']['frames']), 12)
        self.assertEqual(record.json()['record']['payload_sha256'], after['manual_battle']['previous_round_sha256'])
        with self.settings.connect() as connection:
            game = self.service._game(connection, self.game_id)
            self.assertEqual(game['world_revision'], 1)
        self.assertEqual(self.execution_counts(), (before[0] + 1, before[1] + 2, before[2]))
        calls = self.geometry.step_calls
        repeated = self.command('battle_resolve', key=key, body=body)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(response.json()['command_id'], repeated.json()['command_id'])
        self.assertEqual(after, self.state())
        self.assertEqual(calls, self.geometry.step_calls)
        self.assertEqual(state['battle_ledger']['elapsed_seconds'], 0)

    def test_execution_late_failure_rolls_back_all_steps_and_keeps_drafts_locked(self):
        self.execution_game(); self.geometry.fail_at = 12
        before = self.state(); counts = self.execution_counts()
        response = self.command('battle_resolve')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'FIXTURE_FAILURE')
        self.assertEqual(before, self.state())
        self.assertEqual(counts, self.execution_counts())
        self.geometry.fail_at = None
        response = self.command('battle_resolve')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state()['battle_ledger']['elapsed_seconds'], 60)

    def test_execution_replay_insert_failure_rolls_back_snapshot_and_result(self):
        from unittest.mock import patch
        from backend.runtime import ServiceError
        self.execution_game(); before = self.state(); counts = self.execution_counts()
        with patch('backend.battle_replay_store.append_round', side_effect=ServiceError(409, 'FIXTURE_STORE_FAILURE', '模拟回放保存失败')):
            response = self.command('battle_resolve')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'FIXTURE_STORE_FAILURE')
        self.assertEqual(before, self.state())
        self.assertEqual(counts, self.execution_counts())
        with self.settings.connect() as connection:
            self.assertEqual(self.service._game(connection, self.game_id)['world_revision'], 0)

    def test_execution_two_rounds_saved_prefix_and_playback_gets_never_write(self):
        self.execution_game()
        self.assertEqual(self.command('battle_resolve').status_code, 200)
        for role in ('blue', 'red'):
            self.assertEqual(self.command('battle_submit', role, dict(round_number=2)).status_code, 200)
        response = self.command('battle_resolve', payload=dict(round_number=2))
        self.assertEqual(response.status_code, 200, response.text)
        before = self.state(); counts = self.execution_counts(); calls = self.geometry.step_calls
        for _ in range(3):
            index = self.client.get(self.path + '/battle/rounds', headers=self.headers['host'])
            self.assertEqual([i['round_number'] for i in index.json()['items']], [1, 2])
            rounds = [self.client.get(self.path + '/battle/rounds/' + str(n), headers=self.headers['host']).json()['record'] for n in (1, 2)]
            self.assertEqual(rounds[0]['frames'][-1]['world'], rounds[1]['initial_world'])
            self.assertEqual(rounds[0]['payload_sha256'], rounds[1]['previous_round_sha256'])
        self.assertEqual(before, self.state())
        self.assertEqual(counts, self.execution_counts())
        self.assertEqual(calls, self.geometry.step_calls)
        self.assertEqual(self.client.get(self.path + '/battle/rounds', headers=self.headers['blue']).status_code, 403)

    def test_execution_concurrent_host_resolve_advances_exactly_once(self):
        self.execution_game()
        body = dict(context=self.view()['context'], command_type='battle_resolve', payload={'round_number': 1})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.command('battle_resolve', body=body), range(2)))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual(self.state()['manual_battle']['completed_rounds'], 1)
        self.assertEqual(self.geometry.step_calls, 12)
        with self.settings.connect() as connection:
            count = connection.execute("SELECT count(*) AS n FROM game_events WHERE game_id=%s AND event_type='battle_round'", (self.game_id,)).fetchone()['n']
            self.assertEqual(count, 1)

    def test_execution_changed_map_closes_capability_and_no_new_snapshot(self):
        self.execution_game(); self.geometry.failed = True
        before = self.state(); counts = self.execution_counts()
        view = self.view()['manual_battle']
        self.assertFalse(view['engine_ready']); self.assertFalse(view['can_resolve'])
        response = self.command('battle_resolve')
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(before, self.state()); self.assertEqual(counts, self.execution_counts())
        self.assertEqual(self.geometry.step_calls, 0)


    def test_execution_projection_exact_resources_speed_and_private_task_redaction(self):
        from fractions import Fraction
        from backend.tests.test_battle_kernel import unit, spend
        def configure(state):
            ground = unit(state); enemy = unit(state, side='red')
            spend(state, ground, 'energy', Fraction(1, 3))
            ground['speed_m_per_minute'] = '4/3'
            ground['task'] = dict(action='attack', target_id=enemy['unit_id'], target_life_id=enemy['life_id'])
            ground['movement'] = dict(action='move', position=dict(x='20', y='20'))
            state['observations']['red']['contacts'] = [deepcopy(ground)]
        self.execution_game(lock=False, configure=configure)
        blue = self.view('blue'); red = self.view('red')
        own = next(u for u in blue['units'] if u['unit_type'] == 'ground')
        self.assertEqual(own['battle_values']['energy'], '299/3')
        self.assertAlmostEqual(own['energy'], 299 / 3)
        self.assertAlmostEqual(own['current_speed_m_per_minute'], 4 / 3)
        self.assertEqual(own['speed_m_per_minute'], 400)
        self.assertEqual(own['interaction_action'], 'attack')
        self.assertEqual(own['interaction_target_id'], 'red-ground-0')
        for field in ('task', 'movement', 'movement_progress', 'barracks_registration'):
            self.assertNotIn(field, own)
            self.assertNotIn(field, red['contacts'][0])
        self.assertNotIn('interaction_action', red['contacts'][0])
        self.assertEqual({p['point_id'] for p in blue['battle_capture_points']},
                         {'P-01', 'P-02', 'P-03', 'P-04', 'P-05'})
        self.assertIn('blue_score', blue['manual_battle'])
        self.assertIn('red_score', blue['manual_battle'])

    def test_execution_invalid_service_plan_cannot_lock_and_can_be_corrected(self):
        from backend.tests.test_battle_execution import resupply
        from backend.tests.test_battle_kernel import unit
        def configure(state):
            unit(state)['movement'] = dict(action='move', position=dict(x='20', y='20'))
        self.execution_game(lock=False, configure=configure)
        state = self.state()
        response = self.command('battle_draft', 'blue', dict(round_number=1, intents=[resupply(state)]))
        self.assertEqual(response.status_code, 200, response.text)
        before = self.state(); counts = self.execution_counts()
        response = self.command('battle_submit', 'blue')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.execution_counts(), counts)
        self.assertFalse(self.view('blue')['manual_battle']['submitted']['blue'])
        target = unit(state)
        response = self.command('battle_draft', 'blue', dict(round_number=1, intents=[dict(
            unit_id=target['unit_id'], life_id=target['life_id'], movement=dict(action='stop'))]))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.command('battle_submit', 'blue').status_code, 200)

    def test_execution_invalid_camp_fails_before_side_lock(self):
        from backend.tests.test_battle_execution import order
        self.execution_game(lock=False)
        response = self.command('battle_draft', 'blue', dict(round_number=1, intents=[order(self.state(),
            task=dict(action='barracks', camp_id='not-a-frozen-camp'))]))
        self.assertEqual(response.status_code, 200, response.text)
        counts = self.execution_counts()
        response = self.command('battle_submit', 'blue')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.execution_counts(), counts)
        self.assertFalse(self.view('blue')['manual_battle']['submitted']['blue'])

    def test_malformed_service_exact_value_returns_422_without_snapshot(self):
        from backend.tests.test_battle_execution import resupply
        self.execution_game(lock=False)
        counts = self.execution_counts()
        response = self.command('battle_draft', 'blue', dict(round_number=1,
            intents=[resupply(self.state(), quantity='1/0')]))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.execution_counts(), counts)

    def test_manual_scores_use_exact_rulebook_breakdown_and_never_legacy_result(self):
        from backend import battle_kernel as kernel
        self.execution_game(lock=False)
        for role in ('blue', 'red', 'host'):
            view = self.view(role)
            self.assertIsNone(view['victory'])
            self.assertIsNone(view['battle_result'])
            for side in ('blue', 'red'):
                actual = kernel.score(self.state(), side)
                self.assertEqual(view['manual_battle'][side + '_breakdown'], actual)
                self.assertEqual(view['manual_battle'][side + '_score'], actual['total'])

    def test_manual_archive_freezes_sanitized_summary_without_private_plan_or_legacy_score(self):
        from backend.tests.test_battle_execution import attack
        self.execution_game(lock=False)
        payload = dict(round_number=1, intents=[attack(self.state())])
        self.assertEqual(self.command('battle_draft', 'blue', payload).status_code, 200)
        before = self.state()
        response = self.command('archive_match', 'blue', dict(display_name='总册只读档案'))
        self.assertEqual(response.status_code, 200, response.text)
        listed = self.client.get(self.path + '/archives', headers=self.headers['blue']).json()
        archive_id = listed['items'][0]['id']
        detail_path = self.path + '/archives/' + archive_id
        response = self.client.get(detail_path, headers=self.headers['blue'])
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()['item']['projection']
        self.assertTrue(record['read_only'])
        self.assertIsNone(record['victory']); self.assertIsNone(record['battle_result'])
        self.assertEqual(record['manual_battle']['blue_breakdown']['total'], '30')
        self.assertEqual(len(record['battle_capture_points']), 5)
        for forbidden in ('own_drafts', 'drafts', 'context', 'context_token', 'battle_ledger', 'movement', 'task'):
            # tasks为空的公开任务列表不是单位的私有task通道。
            self.assertNotIn('"' + forbidden + '":', json.dumps(record))
        self.assertTrue(all(u['side'] == 'blue' for u in record['units']))
        self.assertTrue(all(u['side'] == 'red' for u in record['contacts']))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.client.get(detail_path, headers=self.headers['red']).status_code, 404)
        self.assertEqual(self.command('battle_submit', 'blue').status_code, 200)
        self.assertEqual(self.client.get(detail_path, headers=self.headers['blue']).json()['item']['projection'], record)

    def test_manual_early_terminal_archives_all_three_seats_with_exact_frozen_result(self):
        from backend import battle_kernel as kernel
        from backend.tests.test_battle_execution import attack
        from backend.tests.test_battle_kernel import unit
        def configure(state):
            for enemy in state['units']:
                if enemy['side'] == 'red' and enemy['unit_type'] != 'ground':
                    kernel._retire(state, enemy, 'fixture_retired')
            unit(state, side='red')['durability'] = '1'
        self.execution_game(lock=False, configure=configure)
        response = self.command('battle_draft', 'blue', dict(round_number=1, intents=[attack(self.state())]))
        self.assertEqual(response.status_code, 200, response.text)
        for role in ('blue', 'red'):
            self.assertEqual(self.command('battle_submit', role).status_code, 200)
        response = self.command('battle_resolve')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state()['lifecycle']['phase'], 'finished')
        for role in ('blue', 'red', 'host'):
            listed = self.client.get(self.path + '/archives', headers=self.headers[role]).json()
            self.assertEqual(len(listed['items']), 1)
            self.assertTrue(listed['items'][0]['automatic'])
            archive_id = listed['items'][0]['id']
            projection = self.client.get(self.path + '/archives/' + archive_id, headers=self.headers[role]).json()['item']['projection']
            self.assertEqual(projection['manual_battle']['stage'], 'finished')
            self.assertEqual(projection['manual_battle']['winner'], 'blue')
            self.assertEqual(projection['manual_battle']['finish_reason'], 'elimination')
            self.assertEqual(projection['manual_battle']['blue_breakdown'], kernel.score(self.state(), 'blue'))
            self.assertNotIn('own_drafts', projection['manual_battle'])
