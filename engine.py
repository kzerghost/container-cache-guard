"""Conservative on-demand repair; only the known Synology name-cache pattern."""
import hashlib
import json
import os
import pathlib
import secrets
import stat
import threading
import time
from guard import scan, containers, read_regular, package_version, SUPPORTED


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, raw):
    with open(path, 'xb') as f:
        os.chmod(path, 0o600)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def syncdir(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class Guard:
    def __init__(self, cache, info, data, provider=containers, pause=30):
        self.cache, self.info, self.data = map(pathlib.Path, (cache, info, data))
        self.data.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data, 0o700)
        self.provider, self.pause = provider, pause
        self.lock = threading.Lock()
        self.plans = {}
        self.status = {'mode': 'on_demand', 'phase': 'idle', 'rows': [], 'repair_enabled': True}
        self.pending = self.find_pending()

    def find_pending(self):
        result = []
        for path in self.data.glob('*/intent.json'):
            if not (path.parent / 'done.json').exists():
                result.append(path.parent.name)
        return result

    def view(self):
        return dict(self.status, pending=self.find_pending())

    def start_scan(self):
        if not self.lock.acquire(False):
            raise ValueError('另一项操作正在进行')
        self.plans.clear()
        self.status = {'phase': 'scanning', 'rows': [], 'message': '正在进行三次检查，请等待约 60 秒'}
        threading.Thread(target=self.scan_job, daemon=True).start()

    def scan_job(self):
        try:
            history = {}
            for index in range(3):
                snapshot, history = scan(self.cache, self.info, self.provider(), history, time.monotonic())
                self.status = dict(snapshot, phase='scanning', sample=index + 1)
                if index < 2:
                    time.sleep(self.pause)
            for row in snapshot['rows']:
                if row['result'] == 'persistent_mismatch' and not self.find_pending():
                    token = secrets.token_urlsafe(24)
                    raw = read_regular(self.cache / row['file'])
                    # Bind to the exact file observed throughout all three samples.
                    key = (row['id'], row['file'], row['cached'], row['actual'], row['state'], digest(raw))
                    if key in history:
                        self.plans[token] = (dict(row), digest(raw), time.monotonic())
                        row['plan'] = token
            self.status = dict(snapshot, phase='ready', mode='on_demand', repair_enabled=True)
        except Exception as exc:
            self.status = {'phase': 'error', 'rows': [], 'error': '扫描未完成：' + type(exc).__name__}
            self.plans.clear()
        finally:
            self.lock.release()

    def audit(self, record):
        with open(self.data / 'audit.jsonl', 'a', encoding='utf-8') as f:
            os.chmod(self.data / 'audit.jsonl', 0o600)
            f.write(json.dumps(dict(time=int(time.time()), **record), ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def fresh(self, row, expected):
        snap, _ = scan(self.cache, self.info, self.provider(), {}, time.monotonic())
        if snap['version'] != SUPPORTED or snap['errors']:
            raise ValueError('套件版本或缓存状态变化，请重新扫描')
        candidates = [r for r in snap['rows'] if r['id'] == row['id']]
        if len(candidates) != 1 or any(candidates[0].get(k) != row.get(k) for k in ('id', 'file', 'cached', 'actual', 'state')) or candidates[0]['result'] != 'observing':
            raise ValueError('容器状态或记录已变化，拒绝修复')
        src = self.cache / row['file']
        if digest(read_regular(src)) != expected:
            raise ValueError('缓存内容已变化，拒绝修复')
        return src

    def repair(self, token):
        if not self.lock.acquire(False):
            raise ValueError('另一项操作正在进行')
        tx = None
        try:
            if self.find_pending():
                raise ValueError('有未完成操作，请先检查恢复记录')
            plan = self.plans.pop(token, None)
            if not plan or time.monotonic() - plan[2] > 300:
                raise ValueError('修复预览已失效，请重新扫描')
            row, expected, _ = plan
            src = self.fresh(row, expected)
            before = read_regular(src)
            metadata = src.stat(follow_symlinks=False)
            obj = json.loads(before)
            obj['name'] = row['actual']
            after = json.dumps(obj, ensure_ascii=False).encode()
            dst = self.cache / (row['actual'] + '.config')
            tx = self.data / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(6))
            tx.mkdir(mode=0o700)
            save(tx / 'before.json', before)
            intent = {'source': src.name, 'target': dst.name, 'before': digest(before),
                      'after': digest(after), 'id': row['id'], 'mode': stat.S_IMODE(metadata.st_mode),
                      'uid': metadata.st_uid, 'gid': metadata.st_gid}
            save(tx / 'intent.json', json.dumps(intent).encode())
            syncdir(tx)
            syncdir(self.data)
            self.fresh(row, expected)
            temp = self.cache / ('.cache-guard-' + secrets.token_hex(12))
            try:
                save(temp, after)
                os.chmod(temp, intent['mode'])
                if os.name != 'nt' and (temp.stat().st_uid, temp.stat().st_gid) != (intent['uid'], intent['gid']):
                    os.chown(temp, intent['uid'], intent['gid'])
                # Atomic publication without replacing any existing destination.
                os.link(temp, dst)
                syncdir(self.cache)
            finally:
                if temp.exists():
                    temp.unlink()
            # External watcher does not share our lock. Fail closed if its data changed.
            if digest(read_regular(src)) != expected or digest(read_regular(dst)) != digest(after):
                raise ValueError('检测到并发变更，保留备份并停止；需检查恢复记录')
            src.unlink()
            syncdir(self.cache)
            current = {r['Id']: r for r in self.provider()}
            if current.get(row['id'], {}).get('Names') != ['/' + row['actual']] or digest(read_regular(dst)) != digest(after):
                raise ValueError('修复后验证未通过，需检查恢复记录')
            save(tx / 'done.json', b'{"result":"repaired"}')
            syncdir(tx)
            self.audit({'result': 'repaired', 'id': row['id'], 'transaction': tx.name})
            self.plans.clear()
            self.status = {'phase': 'idle', 'rows': [], 'message': '已修复并核对。请刷新群晖面板；可再次扫描。'}
            return {'ok': True, 'backup': tx.name}
        except Exception:
            if tx:
                self.audit({'result': 'incomplete', 'transaction': tx.name})
            raise
        finally:
            self.lock.release()

    def recover(self, transaction):
        """Resolve an interrupted operation conservatively, never overwrite new data."""
        if transaction not in self.find_pending():
            raise ValueError('没有该待恢复记录')
        if not self.lock.acquire(False):
            raise ValueError('另一项操作正在进行')
        try:
            tx = self.data / transaction
            if package_version(self.info) != SUPPORTED:
                raise ValueError('套件版本变化，拒绝自动恢复')
            item = json.loads(read_regular(tx / 'intent.json'))
            for name in (item['source'], item['target']):
                if pathlib.Path(name).name != name or not name.endswith('.config'):
                    raise ValueError('恢复记录无效')
            src, dst = self.cache / item['source'], self.cache / item['target']
            current = {r['Id']: r for r in self.provider()}
            real = current.get(item['id'], {})
            if real.get('Names') != ['/' + item['target'][:-7]] or real.get('State') not in ('running', 'exited'):
                raise ValueError('真实容器已变化，拒绝自动恢复')
            source = digest(read_regular(src)) if src.exists() else None
            target = digest(read_regular(dst)) if dst.exists() else None
            # Before publish: no action. After publish with source intact: remove only our unchanged duplicate.
            if source == item['before'] and target in (None, item['after']):
                if target is not None:
                    dst.unlink()
                result = 'rolled_back'
            elif source is None and target == item['after']:
                current = {r['Id']: r for r in self.provider()}
                if current.get(item['id'], {}).get('Names') != ['/' + item['target'][:-7]]:
                    raise ValueError('真实容器已变化，不能自动完成恢复')
                result = 'completed'
            else:
                raise ValueError('文件发生外部变化，拒绝覆盖；请人工检查备份')
            syncdir(self.cache)
            save(tx / 'done.json', json.dumps({'result': result}).encode())
            syncdir(tx)
            self.audit({'result': result, 'transaction': transaction})
            self.plans.clear()
            return {'ok': True, 'result': result}
        finally:
            self.lock.release()
