import base64
import hmac
import json
import os
import pathlib
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from engine import Guard


def serve():
    allowed = {host.strip() for host in os.environ.get('ALLOWED_HOSTS', '').split(',') if host.strip()}
    if not allowed or '*' in allowed:
        raise SystemExit('Set ALLOWED_HOSTS to the exact host:port used to access the UI')
    password = pathlib.Path(os.environ.get('PASSWORD_FILE', '/run/secrets/ui_password')).read_text().strip()
    if len(password) < 20:
        raise SystemExit('Password requires at least 20 characters')
    expected = ('admin:' + password).encode()
    csrf = secrets.token_urlsafe(32)
    guard = Guard(os.environ.get('CACHE_DIR', '/synology-cache'), os.environ.get('INFO_FILE', '/synology-info'), os.environ.get('DATA_DIR', '/data'))
    html = pathlib.Path(os.environ.get('PAGE_FILE', '/app/index.html')).read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def authenticated(self):
            if self.headers.get('Host') not in allowed:
                self.send_error(403)
                return False
            try:
                scheme, value = self.headers.get('Authorization', '').split(' ', 1)
                supplied = base64.b64decode(value, validate=True) if scheme == 'Basic' else b''
            except Exception:
                supplied = b''
            if not hmac.compare_digest(supplied, expected):
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Basic realm="Cache Guard"')
                self.end_headers()
                return False
            return True

        def respond(self, data, status=200, mime='application/json; charset=utf-8'):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            for key, value in {'Content-Type': mime, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store',
                               'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.authenticated():
                return
            if self.path == '/':
                self.respond(html, mime='text/html; charset=utf-8')
            elif self.path == '/api/status':
                self.respond(dict(guard.view(), csrf=csrf))
            else:
                self.send_error(404)

        def do_POST(self):
            if not self.authenticated():
                return
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), csrf):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2048 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('请求格式无效')
                body = json.loads(self.rfile.read(size))
                if self.path == '/api/scan':
                    guard.start_scan()
                    result = {'ok': True}
                elif self.path == '/api/repair':
                    result = guard.repair(body.get('plan', ''))
                elif self.path == '/api/recover':
                    result = guard.recover(body.get('transaction', ''))
                else:
                    self.send_error(404)
                    return
                self.respond(result)
            except ValueError as exc:
                self.respond({'error': str(exc)}, 409)
            except Exception:
                self.respond({'error': '操作未完成，未自动重试。请检查待恢复记录和容器日志。'}, 500)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer((os.environ.get('BIND', '0.0.0.0'), int(os.environ.get('PORT', '8080'))), Handler).serve_forever()


if __name__ == "__main__":
    serve()
