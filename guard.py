"""Read-only Synology cache comparison. No Docker mutations."""
import base64
import hashlib
import hmac
import http.client
import json
import os
import pathlib
import re
import socket
import stat
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ID = re.compile(r'^[0-9a-f]{64}$')
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
SUPPORTED = '24.0.2-1543'


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def containers():
    conn = UnixHTTP('localhost', timeout=8)
    try:
        conn.request('GET', '/v1.43/containers/json?all=1')
        response = conn.getresponse()
        if response.status != 200:
            raise RuntimeError('Docker list failed: HTTP ' + str(response.status))
        body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError('Docker response too large')
        return json.loads(body)
    finally:
        conn.close()


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError('not a regular file')
        raw = f.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('file too large')
    return raw


def package_version(path):
    raw = read_regular(path).decode('utf-8')
    match = re.search(r'^version="?([^"\r\n]+)"?[ \t]*\r?$', raw, re.M)
    return match.group(1) if match else 'unknown'


def assess(docker_rows, records, filenames, version):
    """Pure comparison. Mismatch is not permission to repair."""
    actual = {}
    for row in docker_rows:
        cid, names = row.get('Id', ''), row.get('Names', [])
        if ID.fullmatch(cid) and len(names) == 1 and NAME.fullmatch(names[0].lstrip('/')):
            actual[cid] = (names[0].lstrip('/'), row.get('State', 'unknown'))
    counts = {}
    for _, d, _ in records:
        counts[d.get('id', '')] = counts.get(d.get('id', ''), 0) + 1
    result = []
    for filename, d, digest in records:
        cid, cached = d.get('id', ''), d.get('name', '')
        row = {'file': filename, 'id': cid if ID.fullmatch(str(cid)) else '',
               'cached': cached if isinstance(cached, str) else '', 'actual': '',
               'state': '', 'result': 'manual_review', 'hash': digest}
        if cid in actual:
            name, state = actual[cid]
            row.update(actual=name, state=state)
            if cached == name and filename == name + '.config':
                row['result'] = 'matched'
            elif (version == SUPPORTED and counts[cid] == 1
                  and state in ('running', 'exited')
                  and isinstance(cached, str)
                  and re.fullmatch(r'[0-9a-f]{12}_' + re.escape(name), cached)
                  and filename == cached + '.config'
                  and name + '.config' not in filenames):
                row['result'] = 'observing'
        result.append(row)
    return result


def scan(cache, info, docker_rows, history, now):
    records, errors = [], []
    paths = list(cache.glob('*.config'))
    for path in paths:
        try:
            raw = read_regular(path)
            obj = json.loads(raw)
            if not isinstance(obj, dict) or not isinstance(obj.get('id'), str):
                raise ValueError('unsupported schema')
            records.append((path.name, obj, hashlib.sha256(raw).hexdigest()))
        except Exception:
            errors.append(path.name)
    version = package_version(info)
    rows = assess(docker_rows, records, {p.name for p in paths}, version)
    current = {}
    for row in rows:
        if row['result'] == 'observing':
            key = (row['id'], row['file'], row['cached'], row['actual'], row['state'], row['hash'])
            first, samples = history.get(key, (now, 0))
            current[key] = (first, samples + 1)
            if now - first >= 60 and samples + 1 >= 3 and not errors:
                row['result'] = 'persistent_mismatch'
        row.pop('hash', None)
    return {'time': int(time.time()), 'mode': 'read_only', 'version': version,
            'supported': version == SUPPORTED, 'rows': rows, 'errors': errors,
            'docker_count': len(docker_rows), 'repair_enabled': False}, current
