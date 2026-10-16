import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch
from engine import Guard, digest
from guard import scan

CID='a'*64
OLD='123456789abc_demo-app'
class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=pathlib.Path(self.temp.name)
        self.cache=self.root/'cache'; self.cache.mkdir()
        self.info=self.root/'INFO'; self.info.write_text('version="24.0.2-1543"')
        self.src=self.cache/(OLD+'.config'); self.dst=self.cache/'demo-app.config'
        self.raw=json.dumps({'id':CID,'name':OLD,'labels':{'keep':'unchanged'},'enabled':True}).encode()
        self.src.write_bytes(self.raw)
        self.rows=[{'Id':CID,'Names':['/demo-app'],'State':'running'}]
        self.g=Guard(self.cache,self.info,self.root/'data',lambda:self.rows)
        row=scan(self.cache,self.info,self.rows,{},0)[0]['rows'][0]
        self.g.plans['test']=(row,digest(self.raw),time.monotonic())
    def tearDown(self): self.temp.cleanup()
    def test_repair_preserves_fields_and_backup(self):
        result=self.g.repair('test')
        self.assertFalse(self.src.exists())
        actual=json.loads(self.dst.read_bytes()); actual['name']=OLD
        self.assertEqual(actual,json.loads(self.raw))
        self.assertEqual((self.g.data/result['backup']/'before.json').read_bytes(),self.raw)
        self.assertEqual(self.g.find_pending(),[])
    def test_modified_cache_refused(self):
        self.src.write_bytes(self.raw+b' ')
        with self.assertRaises(ValueError): self.g.repair('test')
        self.assertFalse(self.dst.exists())
    def test_destination_collision_refused(self):
        self.dst.write_bytes(self.raw)
        with self.assertRaises(ValueError): self.g.repair('test')
        self.assertEqual(self.dst.read_bytes(),self.raw)
    def test_changed_container_refused(self):
        self.rows[0]['State']='restarting'
        with self.assertRaises(ValueError): self.g.repair('test')
        self.assertTrue(self.src.exists())
    def test_unknown_version_refused(self):
        self.info.write_text('version="future"')
        with self.assertRaises(ValueError): self.g.repair('test')
    def test_expired_plan_refused(self):
        row,h,_=self.g.plans['test']; self.g.plans['test']=(row,h,time.monotonic()-301)
        with self.assertRaises(ValueError): self.g.repair('test')
    def test_lock_refused(self):
        self.g.lock.acquire()
        try:
            with self.assertRaises(ValueError): self.g.repair('test')
        finally: self.g.lock.release()
    def interrupt_after_publish(self):
        original=pathlib.Path.unlink
        def unlink(path,*a,**kw):
            if path==self.src: raise OSError('simulated interruption')
            return original(path,*a,**kw)
        with patch.object(pathlib.Path,'unlink',unlink):
            with self.assertRaises(OSError): self.g.repair('test')
        return self.g.find_pending()[0]
    def test_interrupted_publish_recovery(self):
        tx=self.interrupt_after_publish()
        self.assertTrue(self.dst.exists()); self.assertTrue(self.src.exists())
        self.assertEqual(self.g.recover(tx)['result'],'rolled_back')
        self.assertFalse(self.dst.exists()); self.assertEqual(self.src.read_bytes(),self.raw)
    def test_recovery_rejects_external_changes(self):
        tx=self.interrupt_after_publish(); self.dst.write_bytes(b'external content')
        with self.assertRaises(ValueError): self.g.recover(tx)
        self.assertEqual(self.dst.read_bytes(),b'external content')
    def test_recovery_rejects_new_version(self):
        tx=self.interrupt_after_publish(); self.info.write_text('version="future"')
        with self.assertRaises(ValueError): self.g.recover(tx)
        self.assertTrue(self.dst.exists())
    def test_interrupted_after_source_removal(self):
        real=self.g.provider
        count=0
        def provider():
            nonlocal count
            count+=1
            if count==3: raise OSError('simulated socket failure')
            return real()
        self.g.provider=provider
        with self.assertRaises(OSError): self.g.repair('test')
        self.g.provider=real
        self.assertFalse(self.src.exists())
        self.assertEqual(self.g.recover(self.g.find_pending()[0])['result'],'completed')
    def test_three_stable_samples_issue_plan(self):
        self.g.plans.clear(); self.g.lock.acquire()
        with patch('engine.time.sleep'),patch('engine.time.monotonic',side_effect=[0,30,60,61]):
            self.g.scan_job()
        self.assertEqual(self.g.status['phase'],'ready')
        self.assertEqual(len(self.g.plans),1)
    def test_publish_failure_leaves_original(self):
        with patch('engine.os.link',side_effect=OSError('simulated filesystem failure')):
            with self.assertRaises(OSError): self.g.repair('test')
        self.assertEqual(self.src.read_bytes(),self.raw)
        self.assertFalse(self.dst.exists())
        self.assertEqual(self.g.recover(self.g.find_pending()[0])['result'],'rolled_back')

if __name__=='__main__': unittest.main()
