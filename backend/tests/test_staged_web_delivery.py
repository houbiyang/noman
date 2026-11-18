"""隔离Web预览服务测试：禁用全部对局接入，不触碰私有记录。"""
import functools
import http.server
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import TestCase

path = Path(__file__).resolve().parents[2] / 'unity-client/Tools/serve_web.py'
spec = importlib.util.spec_from_file_location('staged_web_delivery', path)
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class StagedWebDeliveryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name); (root/'index.html').write_text('isolated build')
        (root/'Build').mkdir(); (root/'Build/test.wasm').write_bytes(b'\0asm')
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(web.SandboxWebHandler, directory=str(root)))
        cls.server.project_id = 'isolated-static-test'; cls.server.launch_provider = None
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.addClassCleanup(cls.server.server_close); cls.addClassCleanup(cls.server.shutdown)
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    def test_static_build_and_browser_isolation_headers(self):
        with urlopen(self.base) as r:
            self.assertEqual(r.read(), b'isolated build')
            self.assertEqual(r.headers['Cross-Origin-Embedder-Policy'], 'require-corp')
            self.assertEqual(r.headers['X-Content-Type-Options'], 'nosniff')
        with urlopen(self.base+'/Build/test.wasm') as r:
            self.assertEqual(r.headers.get_content_type(), 'application/wasm')

    def test_all_post_session_create_recover_routes_remain_disabled(self):
        for route in ('/api/local-session', '/api/local-manual/setup', '/api/local-manual/create', '/api/local-manual/recover'):
            request = Request(self.base+route, data=b'{}', headers={'Content-Type':'application/json','Origin':self.base}, method='POST')
            with self.assertRaises(HTTPError) as result: urlopen(request)
            error = result.exception
            self.assertEqual(error.code, 503)
            self.assertEqual(error.headers['Cache-Control'], 'no-store')
            self.assertIn('隔离构建预览', json.loads(error.read())['error']['message'])

    def test_agents_and_directory_listing_are_not_exposed(self):
        for route in ('/agents', '/agents/', '/Build/'):
            with self.assertRaises(HTTPError) as result: urlopen(self.base+route)
            self.assertEqual(result.exception.code, 404)
