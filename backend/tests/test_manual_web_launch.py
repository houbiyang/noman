"""独立临时目录的本机新局桥接回归；不访问用户数据库或修改实际地图。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

from backend import database
from backend.runtime import sha256
from backend.tests.test_battle_setup import setup_request
from backend.web_launch import LocalWebLaunch, WebLaunchError


class ManualWebLaunchTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.local = self.root / 'private'; self.local.mkdir()
        resources = self.root / 'unity-client/Assets/Sandbox/Resources/Maps/QinglanValley'
        resources.mkdir(parents=True); self.map_hash = 'a' * 64
        (resources / 'map-definition.json').write_text(json.dumps(dict(contentHash=self.map_hash)))
        context = patch('backend.web_launch.database.LOCAL_ROOT', self.local); context.start(); self.addCleanup(context.stop)
        self.provider = LocalWebLaunch(self.root)
        self.source, self.target = str(uuid4()), str(uuid4())
        self.tokens = {'source-host': dict(game_id=self.source, role='host'), 'source-blue': dict(game_id=self.source, role='blue')}
        self.calls = []; self.games = {}; self.counter = 0; self.lose_response = False; self.fail_session = False; self.rejection = None
        self.payload = setup_request()
        self.body = dict(game_id=self.source, map_hash=self.map_hash, capture_layout_sha256='legacy')
        self.create_body = dict(**self.body, request_key=str(uuid4()), payload=self.payload)
        self.demo = self.local / f'web-launch-qinglan-control-v2-{self.map_hash}-legacy.json'
        self.provider.save(self.demo, dict(game_id=self.source, sessions={}, map_hash=self.map_hash, capture_layout_sha256='legacy'))

        def request(method, path, body=None, **options):
            self.calls.append((method, path, deepcopy(body), options))
            if path == '/session':
                seat = self.tokens.get(options.get('token'))
                if not seat: raise HTTPError('', 401, '', {}, BytesIO(b''))
                return seat
            if path == '/battle/setup': return dict(mode='manual', message='仅测试目录', can_create=False)
            if path == '/battle/games':
                if self.rejection:
                    status, code = self.rejection
                    raise HTTPError('', status, 'secret upstream', {}, BytesIO(json.dumps(dict(error=dict(code=code, message='NEVER EXPOSE TOKEN'))).encode()))
                key = options['request_key']
                if key not in self.games:
                    self.counter += 1
                    self.games[key] = dict(game_id=self.target, creation_request_key=key, is_demo=True,
                        scenario_version_id=body['scenario_version_id'], rule_version_id=body['rule_version_id'],
                        creation_payload_sha256=sha256(dict(protocol='qinglan-manual-creation-v1', payload=body)))
                if self.lose_response: self.lose_response = False; raise URLError('NEVER EXPOSE TOKEN')
                return self.games[key]
            if path.startswith('/game-creations/'):
                if path.rsplit('/', 1)[-1] not in self.games: raise HTTPError('', 404, '', {}, BytesIO(b''))
                return self.games[path.rsplit('/', 1)[-1]]
            if path.endswith('/sessions'):
                if self.fail_session: self.fail_session = False; raise URLError('secret session failure')
                game = path.split('/')[3]; token = game + '-' + body['role']
                self.tokens[token] = dict(game_id=game, role=body['role'])
                return dict(game_id=game, role=body['role'], access_token=token)
            raise AssertionError(path)
        self.provider.request = request

    def active(self):
        return self.provider.manual_active_path(self.map_hash, 'legacy')

    def test_setup_requires_host_of_current_game(self):
        for token in ('source-blue', 'invalid'):
            with self.assertRaises(WebLaunchError): self.provider.manual_setup(token, self.body)
        self.assertFalse(any(c[1] == '/battle/setup' for c in self.calls))
        result = self.provider.manual_setup('source-host', self.body)
        self.assertIsNone(result['pending_creation']); self.assertFalse(result['can_create'])

    def test_wrong_source_or_map_or_layout_never_creates(self):
        for update in (dict(game_id=str(uuid4())), dict(map_hash='b' * 64), dict(capture_layout_sha256='wrong')):
            with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', dict(self.create_body, **update))
        self.assertEqual(self.counter, 0); self.assertFalse(self.active().exists())

    def test_create_activates_only_new_game_and_keeps_demo(self):
        before = self.demo.read_bytes()
        launch = self.provider.manual_create('source-host', self.create_body)
        self.assertEqual((launch['role'], launch['game_id']), ('host', self.target))
        self.assertEqual(self.demo.read_bytes(), before)
        self.assertEqual(set(launch), {'api_url', 'game_id', 'role', 'access_token'})
        for role in ('blue', 'red', 'host'):
            seat = self.provider.session(role, self.map_hash)
            self.assertEqual((seat['game_id'], seat['role']), (self.target, role))
        self.assertEqual(self.counter, 1)
        self.assertFalse(any(c[1] == '/games' for c in self.calls))

    def test_same_key_replay_does_not_create_again(self):
        first = self.provider.manual_create('source-host', self.create_body)
        self.assertEqual(first, self.provider.manual_create('source-host', self.create_body))
        self.assertEqual(sum(c[1] == '/battle/games' for c in self.calls), 1)
        changed = deepcopy(self.create_body); changed['payload']['title'] = 'different'
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', changed)

    def test_response_loss_persists_original_key_and_recovers_without_post(self):
        self.lose_response = True
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.assertFalse(self.active().exists())
        pending = self.provider.manual_setup('source-host', self.body)['pending_creation']
        self.assertEqual(pending, dict(request_key=self.create_body['request_key'], payload=self.payload))
        # 模拟服务重启，仍从私有文件恢复，而非依赖进程内状态。
        restarted = LocalWebLaunch(self.root); restarted.request = self.provider.request
        result = restarted.manual_create('source-host', dict(**self.body, request_key=pending['request_key']), recover=True)
        self.assertEqual(result['game_id'], self.target)
        self.assertEqual(sum(c[1] == '/battle/games' for c in self.calls), 1)

    def test_response_loss_retry_keeps_key_and_body(self):
        self.lose_response = True
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', dict(self.create_body, request_key=str(uuid4())))
        self.provider.manual_create('source-host', self.create_body)
        posts = [c for c in self.calls if c[1] == '/battle/games']
        self.assertEqual(len(posts), 2); self.assertEqual(posts[0][2:], posts[1][2:]); self.assertEqual(self.counter, 1)

    def test_failed_seat_does_not_activate_but_keeps_created_id(self):
        self.fail_session = True
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.assertFalse(self.active().exists()); self.assertEqual(self.counter, 1)
        self.provider.manual_create('source-host', dict(**self.body, request_key=self.create_body['request_key']), recover=True)
        self.assertTrue(self.active().exists()); self.assertEqual(sum(c[1] == '/battle/games' for c in self.calls), 1)

    def test_pending_key_cannot_change_payload(self):
        self.lose_response = True
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        changed = deepcopy(self.create_body); changed['payload']['blue']['quantities']['recon'] = 0
        changed['payload']['blue']['units'] = changed['payload']['blue']['units'][1:]
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', changed)
        self.assertEqual(sum(c[1] == '/battle/games' for c in self.calls), 1)

    def test_no_result_recovery_keeps_pending_and_does_not_create(self):
        self.rejection = (503, 'DATABASE_UNAVAILABLE')
        with self.assertRaises(WebLaunchError) as caught: self.provider.manual_create('source-host', self.create_body)
        self.assertNotIn('NEVER EXPOSE', str(caught.exception))
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', dict(**self.body, request_key=self.create_body['request_key']), recover=True)
        self.assertIsNotNone(self.provider.manual_setup('source-host', self.body)['pending_creation'])
        self.assertEqual(sum(c[1] == '/battle/games' for c in self.calls), 1)

    def test_known_rejection_is_sanitized_and_allows_new_configuration(self):
        self.rejection = (503, 'BATTLE_SCENARIO_NOT_READY')
        with self.assertRaises(WebLaunchError) as caught: self.provider.manual_create('source-host', self.create_body)
        self.assertIn('未创建', str(caught.exception)); self.assertNotIn('NEVER EXPOSE', str(caught.exception))
        self.assertIsNone(self.provider.manual_setup('source-host', self.body)['pending_creation'])
        self.rejection = None
        self.provider.manual_create('source-host', dict(self.create_body, request_key=str(uuid4())))
        self.assertEqual(self.counter, 1)

    def test_blocked_camp_is_definite_rejection_not_pending(self):
        self.rejection = (409, 'BATTLE_CAMP_BLOCKS_ROUTE')
        with self.assertRaises(WebLaunchError) as caught:
            self.provider.manual_create('source-host', self.create_body)
        self.assertIn('补给/停靠通道', str(caught.exception))
        self.assertNotIn('NEVER EXPOSE', str(caught.exception))
        self.assertIsNone(self.provider.manual_setup('source-host', self.body)['pending_creation'])
        self.assertEqual(self.counter, 0)
        self.rejection = None
        self.provider.manual_create('source-host', dict(self.create_body, request_key=str(uuid4())))
        self.assertEqual(self.counter, 1)

    def test_full_validation_is_host_only_and_does_not_persist_intent(self):
        original = self.provider.request
        def request(method, path, body=None, **options):
            if path == '/battle/deployment-validate':
                self.assertTrue(options['bootstrap'])
                self.assertEqual(body, self.payload)
                return dict(valid=True)
            return original(method, path, body, **options)
        self.provider.request = request
        body = dict(self.body, payload=self.payload)
        before = {p.name:p.read_bytes() for p in self.local.iterdir()}
        with self.assertRaises(WebLaunchError): self.provider.manual_validate('source-blue', body)
        self.assertEqual(self.provider.manual_validate('source-host', body), dict(valid=True))
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.local.iterdir()})
        self.assertEqual(self.counter, 0)

    def test_rejected_retry_becomes_pending_before_uncertain_request(self):
        self.rejection = (503, 'BATTLE_ENGINE_NOT_READY')
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.rejection = (503, 'DATABASE_UNAVAILABLE')
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.assertIsNotNone(self.provider.manual_setup('source-host', self.body)['pending_creation'])
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', dict(self.create_body, request_key=str(uuid4())))

    def test_cannot_reactivate_old_newgame_after_another_game(self):
        self.provider.manual_create('source-host', self.create_body)
        second_source = self.target; self.target = str(uuid4())
        second = dict(self.create_body, game_id=second_source, request_key=str(uuid4()))
        self.provider.manual_create(second_source + '-host', second)
        before = self.active().read_bytes()
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.assertEqual(before, self.active().read_bytes())

    def test_concurrent_same_intent_is_single_creation(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.provider.manual_create('source-host', self.create_body), range(4)))
        self.assertEqual(len({r['game_id'] for r in results}), 1)
        self.assertEqual(sum(c[1] == '/battle/games' for c in self.calls), 1)

    def test_disk_failure_before_intent_never_sends_create(self):
        self.provider.save = lambda *args: (_ for _ in ()).throw(OSError('disk full'))
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.assertEqual(self.counter, 0)

    def test_active_write_failure_can_recover_without_second_game(self):
        save = self.provider.save
        def fail(path, record):
            if path == self.active(): raise OSError('disk full')
            save(path, record)
        self.provider.save = fail
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.assertEqual(self.counter, 1); self.assertFalse(self.active().exists())
        self.provider.save = save
        self.provider.manual_create('source-host', self.create_body)
        self.assertEqual(self.counter, 1); self.assertTrue(self.active().exists())

    def test_private_intent_is_not_world_readable(self):
        self.lose_response = True
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        path = self.local / f'web-manual-pending-{self.source}.json'
        self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_bad_roster_is_rejected_before_persisting_or_creating(self):
        value = deepcopy(self.create_body); value['payload']['blue']['units'] = []
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', value)
        self.assertEqual(self.counter, 0); self.assertFalse((self.local / f'web-manual-pending-{self.source}.json').exists())

    def test_recovery_rejects_unrelated_creation_summary(self):
        self.lose_response = True
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.games[self.create_body['request_key']]['creation_payload_sha256'] = '0' * 64
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', dict(**self.body, request_key=self.create_body['request_key']), recover=True)
        self.assertFalse(self.active().exists())
        self.assertFalse(any(call[1].endswith('/sessions') for call in self.calls))

    def test_non_string_identifiers_fail_closed_without_forwarding(self):
        for field in ('game_id', 'request_key'):
            for value in (None, 12, [], {}, True):
                with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', dict(self.create_body, **{field: value}))
        self.assertEqual(self.counter, 0)

    def test_active_game_expired_role_token_is_reissued_for_same_game(self):
        self.provider.manual_create('source-host', self.create_body)
        blue = self.provider.session('blue', self.map_hash)
        del self.tokens[blue['access_token']]
        seat = self.provider.session('blue', self.map_hash)
        self.assertEqual((seat['game_id'], seat['role']), (self.target, 'blue'))
        self.assertEqual(self.counter, 1)

    def test_corrupt_active_pointer_never_falls_back_to_old_demo(self):
        self.active().write_text('{broken')
        with self.assertRaises(WebLaunchError): self.provider.session('blue', self.map_hash)
        self.assertFalse(any(call[1].endswith('/sessions') or call[1] == '/games' for call in self.calls))

    def test_wrong_seat_reply_cannot_switch_active_game(self):
        request = self.provider.request
        def wrong(method, path, body=None, **options):
            reply = request(method, path, body, **options)
            if path.endswith('/sessions'): reply['role'] = 'blue'
            return reply
        self.provider.request = wrong
        with self.assertRaises(WebLaunchError): self.provider.manual_create('source-host', self.create_body)
        self.assertFalse(self.active().exists())
