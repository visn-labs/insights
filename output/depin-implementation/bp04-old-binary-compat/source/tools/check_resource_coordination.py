#!/usr/bin/env python3
"""Process ownership, FIFO, crashes, unload ordering and external quarantine."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import select
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from check_retrieval import Models, write_video
from semantic_memory_server import Retrieval
from workflow_indexing import IndexJobs
from workflow_resources import ManagedLMStudio, Residency, ResourceBusy

TOOLS=Path(__file__).resolve().parent


def wait_for(predicate):
    deadline=time.monotonic()+5
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('Fixture timed out')
        time.sleep(.01)


class Native:
    def __init__(self):self.loaded=[];self.calls=[];self.fail=False
    def call(self,path,body=None):
        self.calls.append((path,body))
        if path=='/models':return {'models':[{'key':'fixture','loaded_instances':[{'id':i} for i in self.loaded]}]}
        if path=='/models/load':self.loaded=['fixture-instance'];return {'instance_id':'fixture-instance'}
        if path=='/models/unload':
            if self.fail:raise RuntimeError('fixture unload failed')
            assert set(body)=={'instance_id'}
            self.loaded.remove(body['instance_id']);return body
        raise AssertionError(path)


class Checks(unittest.TestCase):
    def actor(self,directory,name):
        code="from workflow_resources import Residency;import sys;r=Residency(sys.argv[1]);r.acquire();print('READY',flush=True);sys.stdin.readline();r.release(lambda:None)"
        env=dict(os.environ,VISN_RESOURCE_DIR=str(directory),PYTHONPATH=str(TOOLS),PYTHONUNBUFFERED='1')
        p=subprocess.Popen([sys.executable,'-c',code,name],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def close():
            if p.poll() is None:p.kill()
            p.wait();p.stdin.close();p.stdout.close();p.stderr.close()
        self.addCleanup(close);return p

    def ready(self,p):
        self.assertTrue(select.select([p.stdout],[],[],5)[0]);self.assertEqual(p.stdout.readline().strip(),'READY')

    def test_disabled_and_parameter_validation(self):
        r=Residency('off',directory='')
        with r.operation(lambda:self.fail('Disabled profile unloaded a model')):pass
        self.assertEqual(r.snapshot(),{'enabled':False})
        with self.assertRaises(ValueError):Residency('bad',directory='',timeout=float('nan'))
        with self.assertRaises(ValueError):Residency('bad',directory='',limit=0)

    def test_cache_only_release_does_not_collect_or_trim(self):
        with TemporaryDirectory() as tmp:
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=2,frame_size=64),Models())
            try:
                with patch('semantic_memory_server.trim_allocator') as trim:
                    service.release_models();trim.assert_not_called()
                    service.models.embedder=object()
                    service.release_models();trim.assert_called_once()
                    self.assertIsNone(service.models.embedder)
            finally:service.db.close()

    def test_fifo_across_processes_owner_and_waiter_crash_recovery(self):
        with TemporaryDirectory() as tmp:
            probe=Residency('probe',directory=tmp,timeout=.01)
            owner=self.actor(tmp,'owner');self.ready(owner)
            stale=self.actor(tmp,'stale');wait_for(lambda:probe.snapshot()['waiting']==1)
            stale.kill();stale.wait();wait_for(lambda:probe.snapshot()['waiting']==0)
            first=self.actor(tmp,'first');wait_for(lambda:probe.snapshot()['waiting']==1)
            second=self.actor(tmp,'second');wait_for(lambda:probe.snapshot()['waiting']==2)
            owner.kill();owner.wait();self.ready(first)
            self.assertFalse(select.select([second.stdout],[],[],.02)[0])
            first.stdin.write('release\n');first.stdin.flush();self.ready(second)
            second.stdin.write('release\n');second.stdin.flush();second.wait(timeout=5)
            probe.acquire();probe.release(lambda:None)

    def test_unload_failure_and_exception_cannot_release_residency(self):
        with TemporaryDirectory() as tmp:
            owner=Residency('owner',directory=tmp);owner.acquire()
            other=Residency('other',directory=tmp,timeout=.02)
            def bad():raise RuntimeError('unload failed')
            with self.assertRaises(RuntimeError):owner.release(bad)
            with self.assertRaises(ResourceBusy):other.acquire()
            with self.assertRaises(ValueError),owner.operation(lambda:None):raise ValueError('inference failed')
            self.assertIsNotNone(owner.fd)
            owner.release(lambda:self.assertIsNotNone(owner.fd))
            other.acquire();other.release(lambda:None)

    def test_queue_bound_and_unload_before_handoff(self):
        with TemporaryDirectory() as tmp,ThreadPoolExecutor() as pool:
            owner=Residency('owner',directory=tmp,limit=1);owner.acquire();events=[]
            waiter=Residency('next',directory=tmp,timeout=2,limit=1)
            def next_owner():waiter.acquire();events.append('acquired');waiter.release(lambda:None)
            future=pool.submit(next_owner);wait_for(lambda:owner.snapshot()['waiting']==1)
            with self.assertRaises(ResourceBusy):Residency('overflow',directory=tmp,limit=1,timeout=0).acquire()
            owner.yield_if_contended(lambda:events.append('unloaded'));future.result()
            self.assertEqual(events,['unloaded','acquired'])

    def test_resource_wait_does_not_consume_background_retry(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4';write_video(source)
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=2,frame_size=64),Models())
            service.resources=Residency('retrieval',directory=Path(tmp)/'resources',timeout=.02)
            service.indexer=IndexJobs(service)
            try:
                job=service.indexer.submit(dict(scope_id='scope',events=[dict(event_id='red',source_path=str(source),start_ms=0,end_ms=1000)]))
                owner=Residency('detector',directory=service.resources.root);owner.acquire()
                try:self.assertFalse(service.indexer.run_one())
                finally:owner.release(lambda:None)
                self.assertEqual(service.indexer.get(job['id'])['status'],'queued')
                with service.indexer.connect() as db:self.assertEqual(db.execute('SELECT attempts FROM items').fetchone()[0],0)
                self.assertTrue(service.indexer.run_one());self.assertEqual(service.indexer.get(job['id'])['status'],'completed')
            finally:service.resources.release(service.release_models);service.indexer.close();service.db.close()

    def managed(self,tmp,native):
        lease=Residency('lmstudio',directory=tmp,timeout=.02)
        server=ManagedLMStudio(lease,'http://127.0.0.1:1/api/v1')
        server.call=native.call
        self.addCleanup(lambda:lease.fd.close() if lease.fd is not None else None)
        return lease,server

    def test_managed_acknowledgement_unload_and_recovery(self):
        with TemporaryDirectory() as tmp:
            native=Native();lease,server=self.managed(tmp,native)
            server.enter();native.call('/models/load',{'model':'fixture'});server.completed()
            native.fail=True
            with self.assertRaises(RuntimeError):server.cleanup()
            lease.release(lambda:None)
            with self.assertRaises(ResourceBusy):Residency('blocked',directory=tmp,timeout=0).acquire()
            native.fail=False;server.enter();self.assertEqual(native.loaded,[])
            server.completed();server.cleanup();lease.release(lambda:None)
            self.assertFalse((Path(tmp)/'external.json').exists())

    def test_unacknowledged_client_requires_explicit_recovery(self):
        with TemporaryDirectory() as tmp:
            native=Native();lease,server=self.managed(tmp,native)
            server.enter();native.call('/models/load',{'model':'fixture'})
            with self.assertRaisesRegex(RuntimeError,'acknowledgement'):server.cleanup()
            self.assertEqual(native.loaded,['fixture-instance'],'Do not unload potentially active work on EOF')
            lease.release(lambda:None)
            with self.assertRaisesRegex(RuntimeError,'explicit recovery'):server.enter()
            server.enter(recover_uncertain=True);self.assertEqual(native.loaded,[])
            server.completed();server.cleanup()

    def test_recovery_is_not_blocked_behind_an_ordinary_waiter(self):
        with TemporaryDirectory() as tmp,ThreadPoolExecutor() as pool:
            native=Native();lease,server=self.managed(tmp,native);server.enter()
            blocked=Residency('ordinary',directory=tmp,timeout=3)
            waiting=pool.submit(blocked.acquire)
            wait_for(lambda:lease.snapshot()['waiting']==1)
            lease.release(lambda:None)
            with self.assertRaisesRegex(ResourceBusy,'requires recovery'):waiting.result(timeout=1)
            server.enter(recover_uncertain=True);server.completed();server.cleanup()

    def test_preexisting_and_unrecognized_native_state_fail_closed(self):
        with TemporaryDirectory() as tmp:
            native=Native();native.loaded=['pre-existing'];lease,server=self.managed(tmp,native)
            with self.assertRaisesRegex(RuntimeError,'pre-existing'):server.enter()
            self.assertEqual(native.loaded,['pre-existing'])
            with self.assertRaisesRegex(RuntimeError,'Pre-existing'):server.enter()
            native.loaded=[];server.enter();server.completed();server.cleanup()
            server.call=lambda *args:{'models':[{'key':'incomplete'}]}
            with self.assertRaisesRegex(ValueError,'incomplete'):server.enter()
            self.assertTrue(server.marker.exists())

    def test_isolated_child_keeps_lease_after_guardian_kill(self):
        with TemporaryDirectory() as tmp:
            env=dict(os.environ,VISN_RESOURCE_DIR=tmp)
            child_code="import os,time;print(os.getpid(),flush=True);time.sleep(1)"
            p=subprocess.Popen([sys.executable,str(TOOLS/'workflow_resources.py'),'--exec',sys.executable,'-c',child_code],env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
            try:
                self.assertTrue(select.select([p.stdout],[],[],5)[0]);self.assertTrue(p.stdout.readline().strip().isdigit())
                p.kill();p.wait()
                probe=Residency('probe',directory=tmp,timeout=.02)
                with self.assertRaises(ResourceBusy):probe.acquire()
                wait_for(lambda:not probe.snapshot()['owner'])
                probe.acquire();probe.release(lambda:None)
            finally:
                if p.poll() is None:p.kill();p.wait()
                p.stdout.close()


if __name__=='__main__':unittest.main()
