"""仅随机隔离PG库：新局API和真实执行/持久回放联调；合成地图不发布生产。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend import battle_setup, battle_scenario, database
from backend.battle_execution import Executor
from backend.contracts import CreateGame
from backend.runtime import ServiceError
from backend.tests import test_manual_battle_db as manual_db
from backend.tests.test_battle_setup import setup_request, SetupGeometry


class BattleSetupDatabaseTests(TestCase):
    setUpClass = classmethod(manual_db.ManualBattleDatabaseTests.setUpClass.__func__)
    cleanup_database = classmethod(manual_db.ManualBattleDatabaseTests.cleanup_database.__func__)

    def setUp(self):
        self.bootstrap = {'X-Bootstrap-Key': self.settings.bootstrap_key}
        self.api_service = self.client.app.state.service
        self.api_service.battle_executor = None
        self.addCleanup(setattr, self.api_service, 'battle_executor', None)

    def create(self, body=None, key=None, headers=None):
        return self.client.post('/api/v1/battle/games', json=body or setup_request(),
            headers={**(self.bootstrap if headers is None else headers), 'Idempotency-Key': key or str(uuid4())})

    def counts(self):
        with self.settings.connect() as c:
            return {table: c.execute('SELECT count(*) AS n FROM ' + table).fetchone()['n']
                    for table in ('games', 'game_members', 'game_snapshots', 'game_saves', 'game_commands',
                                  'game_events', 'proposals', 'ai_runs', 'access_sessions')}

    def accepted_fixture(self):
        """隔离库专用发布夹具，不调用生产definition注册/变更真实场景来源。"""
        rules, scenario, capacity = battle_setup.sources()
        scenario = deepcopy(scenario)
        scenario['version'] = 'synthetic-setup-test-' + uuid4().hex
        acceptance = scenario['acceptance']
        acceptance.update({k: True for k in acceptance if k not in ('status', 'blocks')})
        acceptance.update(status='accepted', blocks=[])
        binding = battle_setup.versions(rules, scenario, capacity)
        # 只在随机测试库以管理员发布合成版本；应用账号不获得发布权限。
        if not self.database_name.startswith('qiongtu_manual_test_'):
            raise RuntimeError('合成发布仅限随机测试库')
        database.execute(self.database_name, "UPDATE sandbox.rule_versions SET availability='active' WHERE id IN ('%s','%s');" %
                         (binding['rule_version_id'], binding['capacity_version_id']))
        with self.settings.connect() as c:
            c.execute('''INSERT INTO scenario_versions
                (id,scenario_code,version_label,rule_version_id,schema_version,hash_format_version,
                 definition,asset_manifest,payload_sha256,availability,is_demo)
                VALUES (%s,'synthetic-setup-test',%s,%s,%s,'qiongtu-json-v1',%s,%s,%s,'active',false)''',
                (binding['scenario_version_id'], scenario['version'], binding['rule_version_id'], scenario['schema_version'],
                 Jsonb(scenario), Jsonb({}), binding['scenario_sha256']))
        geometry = SetupGeometry(); geometry.scenario_sha256 = binding['scenario_sha256']
        self.api_service.battle_executor = Executor(geometry)
        return (rules, scenario, capacity), setup_request((rules, scenario, capacity)), geometry

    def sessions(self, game_id):
        return {role: {'Authorization': 'Bearer ' + self.client.post(f'/api/v1/bootstrap/games/{game_id}/sessions',
                    headers=self.bootstrap, json=dict(role=role)).json()['access_token']}
                for role in ('blue', 'red', 'host')}

    def command(self, game, headers, role, kind, round_number):
        path = f'/api/v1/games/{game}'
        view = self.client.get(path + '/view', headers=headers[role])
        self.assertEqual(view.status_code, 200, view.text)
        return self.client.post(path + '/commands',
            json=dict(context=view.json()['context'], command_type=kind, payload=dict(round_number=round_number)),
            headers={**headers[role], 'Idempotency-Key': str(uuid4())})

    def test_bootstrap_auth_and_setup_read_is_nonwriting(self):
        before = self.counts()
        self.assertEqual(self.client.get('/api/v1/battle/setup').status_code, 401)
        self.assertEqual(self.create(headers={}).status_code, 401)
        result = self.client.get('/api/v1/battle/setup', headers=self.bootstrap)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['round_seconds'], 60)
        self.assertEqual(result.json()['maximum_rounds'], 60)
        self.assertFalse(result.json()['can_create'])
        self.assertEqual(len(result.json()['checks']), 6)
        self.assertTrue(all(c['status'] != 'passed' for c in result.json()['checks']))
        self.assertNotIn('access_token', result.text)
        self.assertEqual(before, self.counts())

    def test_actual_draft_cannot_create_or_modify_legacy_games(self):
        self.service.create_game(CreateGame(title='保留的旧局', is_demo=True), uuid4())
        before = self.counts()
        # Exercise the current draft's acceptance gate, not a stale version conflict.
        response = self.create(setup_request(battle_setup.sources(hover_energy=True)))
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()['error']['code'], 'BATTLE_SCENARIO_NOT_READY')
        self.assertEqual(before, self.counts())

    def test_strict_payload_no_client_resources_or_acceptance(self):
        before = self.counts()
        for change in ('resource', 'acceptance', 'missing_position', 'empty_roster'):
            body = setup_request()
            if change == 'resource': body['blue']['units'][0]['durability'] = '500'
            elif change == 'acceptance': body['accepted'] = True
            elif change == 'missing_position': body['red']['units'].pop()
            else: body['red']['quantities'] = dict(recon=0, ground=0, support=0)
            self.assertEqual(self.create(body).status_code, 422)
        self.assertEqual(before, self.counts())

    def test_new_game_has_atomic_root_no_ai_and_idempotent_retry(self):
        values, body, _ = self.accepted_fixture()
        before = self.counts(); key = str(uuid4())
        with patch('backend.battle_setup.sources', return_value=values):
            first = self.create(body, key); again = self.create(body, key)
            self.assertEqual(first.status_code, 201, first.text)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(first.json()['game_id'], again.json()['game_id'])
            changed = deepcopy(body); changed['title'] = '异参'
            self.assertEqual(self.create(changed, key).status_code, 409)
        after = self.counts()
        for table, delta in dict(games=1, game_members=3, game_snapshots=1, game_saves=1,
                                  game_commands=0, game_events=0, proposals=0, ai_runs=0, access_sessions=0).items():
            self.assertEqual(after[table]-before[table], delta, table)
        game = first.json()['game_id']
        with self.settings.connect() as c:
            state = self.service._game(c, game)['state_payload']
        self.assertEqual(len(state['camps']), 6)
        self.assertTrue(all(c['deployment_validated'] for c in state['camps']))
        self.assertEqual(state['manual_battle']['elapsed_seconds'], 0)

    def test_legacy_creation_key_cannot_bypass_manual_validation(self):
        key = uuid4()
        self.service.create_game(CreateGame(title='旧模式创建键', is_demo=True), key)
        before = self.counts()
        response = self.create(key=str(key))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'IDEMPOTENCY_KEY_REUSED')
        self.assertEqual(before, self.counts())

    def test_seat_token_cannot_bootstrap_and_creation_can_be_recovered(self):
        values, body, _ = self.accepted_fixture(); key = str(uuid4())
        with patch('backend.battle_setup.sources', return_value=values):
            created = self.create(body, key)
        self.assertEqual(created.status_code, 201, created.text)
        recovered = self.client.get('/api/v1/game-creations/' + key, headers=self.bootstrap)
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()['game_id'], created.json()['game_id'])
        host = self.sessions(created.json()['game_id'])['host']; before = self.counts()
        self.assertEqual(self.create(body, headers=host).status_code, 401)
        self.assertEqual(self.client.get('/api/v1/battle/setup', headers=host).status_code, 401)
        self.assertEqual(before, self.counts())

    def test_concurrent_same_key_creates_exactly_one_game(self):
        values, body, _ = self.accepted_fixture(); key = str(uuid4()); before = self.counts()
        with patch('backend.battle_setup.sources', return_value=values):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: self.create(body, key), range(2)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 201], [r.text for r in responses])
        self.assertEqual(responses[0].json()['game_id'], responses[1].json()['game_id'])
        self.assertEqual(self.counts()['games']-before['games'], 1)

    def test_failed_final_snapshot_rolls_back_all_creation_rows(self):
        values, body, _ = self.accepted_fixture(); before = self.counts()
        with patch('backend.battle_setup.sources', return_value=values), \
             patch.object(self.api_service, '_snapshot', side_effect=ServiceError(503, 'TEST_ABORT', '仅测试失败')):
            result = self.create(body)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(before, self.counts())

    def test_deployment_engine_and_published_versions_fail_closed(self):
        values, body, geometry = self.accepted_fixture(); before = self.counts()
        with patch('backend.battle_setup.sources', return_value=values):
            geometry.validate_deployment = lambda _: False
            response = self.create(body)
            self.assertEqual(response.status_code, 422, response.text)
            self.api_service.battle_executor = None
            self.assertEqual(self.create(body).status_code, 503)
            changed = deepcopy(body); changed['capacity_sha256'] = '0'*64
            self.assertEqual(self.create(changed).status_code, 409)
        self.assertEqual(before, self.counts())

    def test_missing_capacity_publication_rejected_without_writes(self):
        values, body, _ = self.accepted_fixture()
        database.execute(self.database_name, "UPDATE sandbox.rule_versions SET availability='draft' WHERE id='%s';" % body['capacity_version_id'])
        before = self.counts()
        try:
            with patch('backend.battle_setup.sources', return_value=values):
                response = self.create(body)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()['error']['code'], 'BATTLE_VERSION_NOT_READY')
            self.assertEqual(before, self.counts())
        finally:
            database.execute(self.database_name, "UPDATE sandbox.rule_versions SET availability='active' WHERE id='%s';" % body['capacity_version_id'])

    def test_sixty_rounds_real_executor_persisted_replay_and_role_isolation(self):
        values, body, _ = self.accepted_fixture()
        with patch('backend.battle_setup.sources', return_value=values):
            result = self.create(body)
        self.assertEqual(result.status_code, 201, result.text)
        game = result.json()['game_id']; headers = self.sessions(game)
        path = f'/api/v1/games/{game}'
        for role in ('red', 'blue'):
            view = self.client.get(path + '/view', headers=headers[role]).json()
            self.assertEqual(len(view['units']), 3)
            self.assertEqual(len(view['camps']), 3)
            self.assertNotIn('battle_setup', view)
            self.assertEqual(view['manual_battle']['own_drafts'], [])
        initial_result = self.client.get(path+'/result', headers=headers['blue'])
        self.assertEqual(initial_result.status_code, 200, initial_result.text)
        self.assertEqual(initial_result.json()['result_status'], 'in_progress')
        self.assertIsNone(initial_result.json()['result'])
        self.assertEqual(initial_result.json()['blue_score'], '30')
        self.assertIsNone(initial_result.json()['red_score'])
        self.assertIsNone(initial_result.json()['red_breakdown'])
        self.assertEqual(initial_result.json()['red_control_score'], '0')
        for role in ('blue', 'red', 'host'):
            summary = self.client.get(path+'/result', headers=headers[role]).json()
            if role == 'host':
                self.assertEqual(summary['blue_score'], '30')
                self.assertEqual(summary['red_score'], '30')
            else:
                other = 'red' if role == 'blue' else 'blue'
                self.assertIsNone(summary[other+'_score'])
                self.assertIsNone(summary[other+'_breakdown'])
        for n in range(1, 61):
            for role in ('blue', 'red'):
                response = self.command(game, headers, role, 'battle_submit', n)
                self.assertEqual(response.status_code, 200, response.text)
            response = self.command(game, headers, 'host', 'battle_resolve', n)
            self.assertEqual(response.status_code, 200, response.text)
        view = self.client.get(path + '/view', headers=headers['host']).json()
        self.assertEqual(view['manual_battle']['elapsed_seconds'], 3600)
        self.assertEqual(view['manual_battle']['completed_rounds'], 60)
        self.assertEqual(view['clock']['phase'], 'finished')
        for role in ('host', 'blue', 'red'):
            result_response = self.client.get(path+'/result', headers=headers[role])
            self.assertEqual(result_response.status_code, 200, result_response.text)
            summary = result_response.json()
            self.assertEqual(summary['result_status'], 'settled')
            self.assertEqual(summary['result']['reason'], 'time_limit')
            self.assertEqual(summary['blue_score'], view['manual_battle']['blue_score'])
            self.assertEqual(summary['red_score'], view['manual_battle']['red_score'])
            self.assertNotIn('battle_ledger', summary)
            self.assertNotIn('units', summary)
        rounds = self.client.get(path+'/battle/rounds', headers=headers['host'])
        self.assertEqual(rounds.status_code, 200, rounds.text)
        self.assertEqual(len(rounds.json()['items']), 60)
        with self.settings.connect() as c:
            state = self.service._game(c, game)['state_payload']
            self.assertEqual(state['battle_result']['reason'], 'time_limit')
            self.assertEqual(c.execute("SELECT count(*) AS n FROM game_events WHERE game_id=%s AND event_type='battle_round'", (game,)).fetchone()['n'], 60)
        for role in ('red', 'blue'):
            self.assertEqual(self.client.get(path+'/battle/rounds', headers=headers[role]).status_code, 403)
        before = self.counts()
        for n in (1, 30, 60):
            response = self.client.get(path+f'/battle/rounds/{n}', headers=headers['host'])
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(before, self.counts())

    def test_local_bridge_newgame_round_and_replay_in_isolated_database(self):
        """实际HTTP契约+PG+执行器联调，仅合成部署，不宣称现图可通行。"""
        from io import BytesIO
        import hashlib
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from urllib.error import HTTPError
        from backend.demo_domain import MAP_PATH
        from backend.web_launch import LocalWebLaunch

        values, payload, _ = self.accepted_fixture()
        source = self.api_service.create_game(CreateGame(title='隔离本机接入源对局', is_demo=True), uuid4())[0]['game_id']
        source_headers = self.sessions(source)['host']
        with TemporaryDirectory() as temporary:
            root = Path(temporary); private = root / 'private'; private.mkdir()
            resources = root / 'unity-client/Assets/Sandbox/Resources/Maps/QinglanValley'; resources.mkdir(parents=True)
            (resources / 'map-definition.json').write_bytes(MAP_PATH.read_bytes())
            layout = MAP_PATH.with_name('capture-points.json').read_bytes()
            (resources / 'capture-points.json').write_bytes(layout)
            map_hash = json.loads(MAP_PATH.read_bytes())['contentHash']; layout_hash = hashlib.sha256(layout).hexdigest()
            provider = LocalWebLaunch(root)
            def request(method, path, body=None, *, token=None, bootstrap=False, request_key=None):
                headers = dict(self.bootstrap) if bootstrap else {}
                if token: headers['Authorization'] = 'Bearer ' + token
                if request_key: headers['Idempotency-Key'] = request_key
                response = self.client.request(method, '/api/v1' + path, json=body, headers=headers)
                if response.status_code >= 400:
                    raise HTTPError('isolated', response.status_code, 'isolated API error', {}, BytesIO(response.content))
                return response.json()
            provider.request = request
            with patch('backend.web_launch.database.LOCAL_ROOT', private), patch('backend.battle_setup.sources', return_value=values):
                provider.save(private / f'web-launch-qinglan-control-v2-{map_hash}-{layout_hash}.json',
                    dict(game_id=source, sessions={}, map_hash=map_hash, capture_layout_sha256=layout_hash))
                binding = dict(game_id=source, map_hash=map_hash, capture_layout_sha256=layout_hash)
                catalogue = provider.manual_setup(source_headers['Authorization'][7:], binding)
                self.assertEqual((catalogue['round_seconds'], catalogue['maximum_rounds']), (60, 60))
                launch = provider.manual_create(source_headers['Authorization'][7:],
                    dict(**binding, request_key=str(uuid4()), payload=payload))
                game = launch['game_id']; self.assertNotEqual(game, source)
                headers = {}
                for role in ('blue', 'red', 'host'):
                    seat = provider.session(role, map_hash, layout_hash)
                    self.assertEqual((seat['game_id'], seat['role']), (game, role))
                    self.assertEqual(set(seat), {'api_url', 'game_id', 'role', 'access_token'})
                    headers[role] = {'Authorization': 'Bearer ' + seat['access_token']}
                for role in ('blue', 'red'):
                    receipt = self.command(game, headers, role, 'battle_submit', 1)
                    self.assertEqual(receipt.status_code, 200, receipt.text)
                    self.assertEqual(receipt.json()['status'], 'succeeded', receipt.text)
                receipt = self.command(game, headers, 'host', 'battle_resolve', 1)
                self.assertEqual(receipt.json()['status'], 'succeeded', receipt.text)
                path = '/api/v1/games/' + game
                view = self.client.get(path + '/view', headers=headers['host']).json()
                self.assertEqual((view['manual_battle']['completed_rounds'], view['manual_battle']['elapsed_seconds']), (1, 60))
                replay = self.client.get(path + '/battle/rounds/1', headers=headers['host'])
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json()['record']['end_seconds'] - replay.json()['record']['start_seconds'], 60)
                self.assertEqual(self.client.get(path + '/battle/rounds/1', headers=headers['blue']).status_code, 403)
