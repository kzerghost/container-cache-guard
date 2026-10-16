import base64
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.temp.name)
        password = 'local-test-password-only-12345'
        (root / 'password').write_text(password)
        (root / 'snapshot.json').write_text(json.dumps({'mode': 'read_only', 'repair_enabled': False}))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, BIND='127.0.0.1', PORT=str(port), PASSWORD_FILE=str(root / 'password'),
                   PAGE_FILE=str(pathlib.Path(__file__).with_name('index.html')), DATA_DIR=str(root / 'data'), ALLOWED_HOSTS='127.0.0.1:' + str(port))
        cls.process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).with_name('service.py'))],
                                       env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        cls.url = 'http://127.0.0.1:' + str(port)
        cls.header = {'Authorization': 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode()}
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            try:
                cls.opener.open(cls.url, timeout=1)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        cls.process.terminate()
        cls.process.wait()
        raise RuntimeError('web did not start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.temp.cleanup()

    def test_auth_required(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.opener.open(self.url + '/api/status')
        self.assertEqual(cm.exception.code, 401)

    def test_unlisted_host_rejected_even_with_password(self):
        headers = dict(self.header, Host='untrusted.example:8080')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.opener.open(urllib.request.Request(self.url + '/api/status', headers=headers))
        self.assertEqual(cm.exception.code, 403)

    def test_status_is_idle_without_scan(self):
        with self.opener.open(urllib.request.Request(self.url + '/api/status', headers=self.header)) as r:
            self.assertEqual(json.load(r)['phase'], 'idle')

    def test_write_requires_csrf(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.opener.open(urllib.request.Request(self.url + '/api/repair', headers=self.header, data=b'{}'))
        self.assertEqual(cm.exception.code, 403)

    def test_page_served(self):
        with self.opener.open(urllib.request.Request(self.url + '/', headers=self.header)) as r:
            self.assertIn('按需检查', r.read().decode())
            self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])


class ConfigurationTests(unittest.TestCase):
    def test_missing_or_wildcard_host_allowlist_refused(self):
        for value in ('', ' , ', '*', 'localhost:8080,*'):
            with self.subTest(value=value):
                env = dict(os.environ, ALLOWED_HOSTS=value, PASSWORD_FILE='missing-password-fixture')
                result = subprocess.run(
                    [sys.executable, str(pathlib.Path(__file__).with_name('service.py'))],
                    env=env, capture_output=True, text=True, timeout=5,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Set ALLOWED_HOSTS', result.stderr)


if __name__ == '__main__':
    unittest.main()
