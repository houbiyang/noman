"""本机同源HTTP入口测试：临时静态根、随机端口、无生产API/数据库。"""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest import TestCase
import functools

from backend.web_launch import WebLaunchError

spec = importlib.util.spec_from_file_location('isolated_serve_web', Path(__file__).resolve().parents[2] / 'unity-client/Tools/serve_web.py')
web = importlib.util.module_from_spec(spec); spec.loader.exec_module(web)


class ManualWebHttpTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(); cls.calls = []; cls.fail = False
        class Handler(web.SandboxWebHandler):
            def log_message(self, *args): pass
        def setup(token, body):
            cls.calls.append(('setup', token, body))
            if cls.fail: raise WebLaunchError('已脱敏错误')
            return dict(mode='manual', can_create=False)
        def create(token, body, *, recover=False):
            cls.calls.append(('recover' if recover else 'create', token, body)); return dict(game_id='fake', role='host')
        def session(*args): cls.calls.append(('session', *args)); return dict(role=args[0])
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=cls.temp.name))
        cls.server.project_id = 'isolated'; cls.server.launch_provider = SimpleNamespace(manual_setup=setup, manual_create=create, manual_validate=lambda token, body: dict(valid=True), session=session)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def setUp(self): self.calls.clear(); type(self).fail = False

    def request(self, path='/api/local-manual/setup', body=None, headers=None):
        origin = 'http://127.0.0.1:' + str(self.server.server_port)
        base = {'Origin': origin, 'Content-Type': 'application/json', 'Authorization': 'Bearer fake-host'}
        if headers: base.update(headers)
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('POST', path, json.dumps(body if body is not None else dict(game_id='fake', map_hash='hash', capture_layout_sha256='layout')), base)
            response = connection.getresponse(); payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally: connection.close()

    def test_setup_passes_only_token_and_exact_binding(self):
        status, headers, data = self.request()
        self.assertEqual(status, 200); self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.calls[0][0:2], ('setup', 'fake-host'))
        self.assertNotIn(b'fake-host', data)

    def test_no_origin_or_wrong_host_or_cross_origin_never_calls_provider(self):
        for headers in ({'Origin': ''}, {'Origin': 'https://evil.invalid'}, {'Host': 'evil.invalid'}, {'Origin': 'http://localhost:' + str(self.server.server_port)}):
            self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertFalse(self.calls)

    def test_no_bearer_rejected_before_provider(self):
        self.assertEqual(self.request(headers={'Authorization': ''})[0], 401); self.assertFalse(self.calls)

    def test_oversized_request_and_extra_fields_rejected(self):
        self.assertEqual(self.request(body=dict(padding='x' * 65536))[0], 400)
        self.assertEqual(self.request(body=dict(game_id='fake', map_hash='hash', capture_layout_sha256='layout', role='host'))[0], 400)
        self.assertFalse(self.calls)

    def test_create_and_recovery_distinct_strict_shapes(self):
        body = dict(game_id='fake', map_hash='hash', capture_layout_sha256='layout', request_key='key', payload={'title': 'test'})
        self.assertEqual(self.request('/api/local-manual/create', body)[0], 200)
        self.assertEqual(self.calls[-1][0], 'create')
        self.assertEqual(self.request('/api/local-manual/recover', body)[0], 400)
        del body['payload']
        self.assertEqual(self.request('/api/local-manual/recover', body)[0], 200)
        self.assertEqual(self.calls[-1][0], 'recover')

    def test_validate_requires_exact_shape_and_authentication(self):
        body = dict(game_id='fake', map_hash='hash', capture_layout_sha256='layout', payload={})
        self.assertEqual(self.request('/api/local-manual/validate', body)[0], 200)
        self.assertEqual(self.request('/api/local-manual/validate', dict(body, request_key='unexpected'))[0], 400)
        self.assertEqual(self.request('/api/local-manual/validate', body, {'Authorization': ''})[0], 401)

    def test_non_object_and_bad_content_type_rejected(self):
        self.assertEqual(self.request(body=['invalid'])[0], 400)
        self.assertEqual(self.request(headers={'Content-Type': 'text/plain'})[0], 400)
        self.assertFalse(self.calls)

    def test_unsupported_route_is_not_forwarded(self):
        self.assertEqual(self.request('/api/local-manual/admin')[0], 404); self.assertFalse(self.calls)

    def test_safe_errors_are_no_store(self):
        type(self).fail = True
        status, headers, payload = self.request()
        self.assertEqual(status, 503); self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(json.loads(payload)['error']['message'], '已脱敏错误')

    def test_original_local_session_contract_remains_available(self):
        status, headers, _ = self.request('/api/local-session', dict(role='blue', map_hash='hash', capture_layout_sha256='layout'))
        self.assertEqual(status, 200); self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertEqual(self.calls[-1][0], 'session')
