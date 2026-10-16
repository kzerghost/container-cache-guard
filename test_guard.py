import hashlib
import json
import pathlib
import tempfile
import unittest
from guard import assess, scan

CID = 'a' * 64
OLD = '123456789abc_demo-app'


class ComparisonTests(unittest.TestCase):
    def result(self, cached=OLD, filename=None, state='running', version='24.0.2-1543', extra=(), duplicate=False, cid=CID):
        record = (filename or cached + '.config', {'id': cid, 'name': cached}, 'hash')
        records = [record, record] if duplicate else [record]
        return assess([{'Id': CID, 'Names': ['/demo-app'], 'State': state}],
                      records, {record[0], *extra}, version)[0]['result']

    def test_correct_record(self):
        self.assertEqual(self.result(cached='demo-app'), 'matched')

    def test_exact_known_pattern(self):
        self.assertEqual(self.result(), 'observing')

    def test_wrong_id_is_not_repaired_by_name(self):
        self.assertEqual(self.result(cid='b' * 64), 'manual_review')

    def test_unrecognized_prefix(self):
        self.assertEqual(self.result(cached='backup_demo-app'), 'manual_review')

    def test_destination_collision(self):
        self.assertEqual(self.result(extra=['demo-app.config']), 'manual_review')

    def test_duplicate_id(self):
        self.assertEqual(self.result(duplicate=True), 'manual_review')

    def test_unknown_version(self):
        self.assertEqual(self.result(version='24.0.2-9999'), 'manual_review')

    def test_transient_state(self):
        for state in ('created', 'restarting', 'removing', 'dead', 'paused'):
            self.assertEqual(self.result(state=state), 'manual_review')


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.info = self.root / 'INFO'
        self.info.write_text('version="24.0.2-1543"\n')
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.file = self.cache / (OLD + '.config')
        self.file.write_text(json.dumps({'id': CID, 'name': OLD, 'private': 'SECRET_NOT_FOR_UI'}))
        self.rows = [{'Id': CID, 'Names': ['/demo-app'], 'State': 'running'}]

    def test_three_samples_and_read_only(self):
        before = self.file.read_bytes()
        a, history = scan(self.cache, self.info, self.rows, {}, 100)
        b, history = scan(self.cache, self.info, self.rows, history, 130)
        c, history = scan(self.cache, self.info, self.rows, history, 160)
        self.assertEqual(a['rows'][0]['result'], 'observing')
        self.assertEqual(b['rows'][0]['result'], 'observing')
        self.assertEqual(c['rows'][0]['result'], 'persistent_mismatch')
        self.assertEqual(self.file.read_bytes(), before)
        self.assertNotIn('SECRET_NOT_FOR_UI', json.dumps(c))
        self.assertFalse(c['repair_enabled'])

    def test_content_change_resets_observation(self):
        _, history = scan(self.cache, self.info, self.rows, {}, 100)
        _, history = scan(self.cache, self.info, self.rows, history, 130)
        self.file.write_text(json.dumps({'id': CID, 'name': OLD, 'version': 2}))
        c, _ = scan(self.cache, self.info, self.rows, history, 160)
        self.assertEqual(c['rows'][0]['result'], 'observing')

    def test_malformed_record_blocks_candidate(self):
        (self.cache / 'broken.config').write_text('{')
        history = {}
        for now in (100, 130, 160):
            c, history = scan(self.cache, self.info, self.rows, history, now)
        self.assertEqual(c['rows'][0]['result'], 'observing')
        self.assertEqual(c['errors'], ['broken.config'])

    def test_disappeared_container_resets_observation(self):
        _, history = scan(self.cache, self.info, self.rows, {}, 100)
        _, history = scan(self.cache, self.info, [], history, 130)
        c, _ = scan(self.cache, self.info, self.rows, history, 160)
        self.assertEqual(c['rows'][0]['result'], 'observing')


if __name__ == '__main__':
    unittest.main()
