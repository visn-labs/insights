#!/usr/bin/env python3
"""Durable preparation, artifact parity, recovery, cancellation and admission."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from check_retrieval import Models, write_video
from general_video_experiment import signature
from semantic_memory_server import Retrieval, handler_for
from workflow_indexing import AdmissionGate, IndexJobs, QueueFull


def wait_for(predicate):
    deadline = time.monotonic()+3
    while not predicate():
        if time.monotonic()>deadline: raise AssertionError('Timed out waiting for fixture state')
        threading.Event().wait(.005)


class Checks(unittest.TestCase):
    def setup_service(self, tmp, **options):
        source = Path(tmp)/'source.mp4'
        if not source.exists(): write_video(source)
        service = Retrieval(SimpleNamespace(data_dir=tmp,frames=2,frame_size=64,**options), Models())
        service.indexer = IndexJobs(service)
        self.addCleanup(service.db.close)
        self.addCleanup(service.indexer.close)
        events = [dict(event_id=name,source_path=str(source),start_ms=start,end_ms=start+1000) for name,start in [('red',0),('blue',1000)]]
        return service,dict(scope_id='recording',events=events)

    def drain(self, service, job):
        for _ in range(10):
            if service.indexer.get(job['id'])['status'] in ('completed','failed','cancelled'): break
            service.indexer.run_one()
        return service.indexer.get(job['id'])

    def test_duplicate_submission_restart_and_exact_cached_query_parity(self):
        with TemporaryDirectory() as tmp:
            service,body = self.setup_service(tmp)
            job = service.indexer.submit(body)
            self.assertEqual(job['id'],service.indexer.submit(dict(body,events=list(reversed(body['events']))))['id'])
            service.indexer.run_one()
            self.assertEqual(service.indexer.get(job['id'])['completed'],1)
            service.indexer.close()
            service.indexer = IndexJobs(service)
            self.addCleanup(service.indexer.close)
            self.assertEqual(self.drain(service,job)['completed'],2)
            query = dict(query='red',events=body['events'],lexical_ids=['blue'],rerank=True,limit=2)
            prepared = service.search(query)
            self.assertIn('2 cached',prepared['notes'][0])
            service.db.execute('DELETE FROM vectors');service.db.commit()
            service.search(query)  # Baseline creates identical vectors through the old query path.
            control = service.search(query)
            self.assertEqual(signature(prepared),signature(control))
            self.assertEqual(self.drain(service,service.indexer.submit(body))['status'],'completed')

    def test_running_marker_recovery_reuses_committed_vector(self):
        with TemporaryDirectory() as tmp:
            service,body = self.setup_service(tmp)
            job=service.indexer.submit(body)
            service.embedding(sorted(body['events'],key=lambda e:e['event_id'])[0]);calls=service.models.calls
            with closing(service.indexer.connect()) as db,db:
                db.execute("UPDATE jobs SET state='running' WHERE id=?",(job['id'],))
                db.execute("UPDATE items SET state='running',attempts=2 WHERE job=? AND ordinal=0",(job['id'],))
            service.indexer.close();service.indexer=IndexJobs(service);self.addCleanup(service.indexer.close)
            self.assertEqual(service.indexer.get(job['id'])['status'],'queued')
            service.indexer.run_one();self.assertEqual(service.models.calls,calls)
            self.assertEqual(self.drain(service,job)['status'],'completed')

    def test_cancel_in_progress_and_explicit_resume(self):
        with TemporaryDirectory() as tmp:
            service,body=self.setup_service(tmp,disable_telemetry=False);job=service.indexer.submit(body)
            entered,release=threading.Event(),threading.Event()
            original=service.models.embed
            def slow(value):
                entered.set();self.assertTrue(release.wait(3));return original(value)
            with patch.object(service.models,'embed',slow),ThreadPoolExecutor() as pool:
                future=pool.submit(service.indexer.run_one);self.assertTrue(entered.wait(3))
                cancelled=service.indexer.action(job['id'],'cancel');self.assertEqual(cancelled['status'],'cancelled')
                release.set();future.result()
            self.assertEqual(service.indexer.get(job['id'])['status'],'cancelled')
            self.assertFalse(service.indexer.run_one())
            service.indexer.action(job['id'],'retry')
            self.assertEqual(self.drain(service,job)['status'],'completed')

    def test_failure_budget_partial_coverage_and_retry(self):
        with TemporaryDirectory() as tmp:
            service,body=self.setup_service(tmp,disable_telemetry=False);job=service.indexer.submit(body)
            original=service.embedding
            def broken(event,**kwargs):
                if event['event_id']=='red': raise RuntimeError('fixture model unavailable')
                return original(event,**kwargs)
            with patch.object(service,'embedding',broken),self.assertLogs('visn.indexing',level='ERROR'),self.assertLogs('visn.telemetry',level='INFO') as logs:
                result=self.drain(service,job)
            self.assertEqual((result['status'],result['completed'],result['failed']),('failed',1,1))
            self.assertEqual(result['item_errors'][0]['attempts'],2)
            self.assertTrue(any(json.loads(line.split('WORKFLOW_TRACE ',1)[1])['status']=='error' for line in logs.output))
            calls=service.models.calls
            service.indexer.action(job['id'],'retry');self.assertEqual(self.drain(service,job)['status'],'completed')
            self.assertEqual(service.models.calls,calls+1,'Completed intervals must not be recomputed')

    def test_source_mutation_before_and_during_inference_never_commits_stale_key(self):
        with TemporaryDirectory() as tmp:
            service,body=self.setup_service(tmp);job=service.indexer.submit(body)
            path=Path(body['events'][0]['source_path']);path.write_bytes(path.read_bytes()+b'changed')
            with self.assertLogs('visn.indexing',level='ERROR'): result=self.drain(service,job)
            self.assertEqual(result['status'],'failed');self.assertEqual(result['completed'],0)
            self.assertEqual(service.db.execute('SELECT count(*) FROM vectors').fetchone()[0],0)
            new=service.indexer.submit(body);self.assertNotEqual(job['id'],new['id'])
            embed=service.models.embed
            def mutate(value):
                result=embed(value);path.write_bytes(path.read_bytes()+b'again');return result
            with patch.object(service.models,'embed',mutate),self.assertLogs('visn.indexing',level='ERROR'):
                result=self.drain(service,new)
            self.assertEqual(result['status'],'failed');self.assertEqual(service.db.execute('SELECT count(*) FROM vectors').fetchone()[0],0)

    def test_profile_change_and_missing_artifact_reprepare(self):
        with TemporaryDirectory() as tmp:
            service,body=self.setup_service(tmp);job=service.indexer.submit(body)
            service.models.version='{"model":"new-revision"}'
            self.assertFalse(service.indexer.get(job['id'])['profile_current'])
            with self.assertLogs('visn.indexing',level='ERROR'): self.assertEqual(self.drain(service,job)['status'],'failed')
            current=service.indexer.submit(body);self.assertNotEqual(job['id'],current['id'])
            self.assertTrue(current['profile_current'])
            self.assertEqual(self.drain(service,current)['status'],'completed')
            service.db.execute('DELETE FROM vectors');service.db.commit()
            self.assertEqual(service.indexer.submit(body)['status'],'queued')
            self.assertEqual(self.drain(service,current)['status'],'completed')

    def test_missing_source_cannot_retry_forever_on_a_cached_artifact(self):
        with TemporaryDirectory() as tmp:
            service,body=self.setup_service(tmp);job=service.indexer.submit(body)
            for event in body['events']:service.embedding(event)
            Path(body['events'][0]['source_path']).unlink()
            with self.assertLogs('visn.indexing',level='ERROR'):result=self.drain(service,job)
            self.assertEqual(result['status'],'failed')
            self.assertTrue(all(error['attempts']==2 for error in result['item_errors']))

    def test_queue_bounds_owner_exclusion_and_input_scope(self):
        with TemporaryDirectory() as tmp:
            service,body=self.setup_service(tmp,index_queue_limit=1);job=service.indexer.submit(body)
            with self.assertRaises(QueueFull):service.indexer.submit(dict(body,scope_id='other'))
            self.assertEqual(service.indexer.submit(body)['id'],job['id'])
            with self.assertRaisesRegex(RuntimeError,'already owns'):IndexJobs(service)
            service.indexer.action(job['id'],'cancel')
            other=service.indexer.submit(dict(body,scope_id='other'))
            with self.assertRaises(QueueFull):service.indexer.action(job['id'],'retry')
            self.assertEqual(len(service.indexer.list('other')),1)
            self.assertEqual(service.indexer.list('missing'),[])
            with self.assertRaises(ValueError):service.indexer.submit(dict(body,events=[dict(body['events'][0],source_path=__file__)]))
            self.assertEqual(service.indexer.get(other['id'])['status'],'queued')

    def test_admission_priority_fifo_fairness_limit_and_timeout(self):
        gate=AdmissionGate(limit=2,burst=2)
        self.assertTrue(gate.acquire(blocking=False));order=[]
        def enter(name,background=False):
            self.assertTrue(gate.acquire(timeout=3,background=background))
            try:order.append(name)
            finally:gate.release()
        with ThreadPoolExecutor(max_workers=3) as pool:
            bg=pool.submit(enter,'background',True)
            wait_for(lambda:gate.snapshot()['background_waiting']==1)
            one=pool.submit(enter,'first');wait_for(lambda:gate.snapshot()['interactive_waiting']==1)
            two=pool.submit(enter,'second');wait_for(lambda:gate.snapshot()['interactive_waiting']==2)
            self.assertFalse(gate.acquire(timeout=0));gate.release()
            one.result();two.result();bg.result()
        self.assertEqual(order,['first','background','second'])
        self.assertFalse(gate.locked());self.assertEqual(gate.snapshot()['interactive_waiting'],0)
        gate.acquire();self.assertFalse(gate.acquire(timeout=.01));gate.release()

    def test_http_control_progress_and_cancel_without_model_slot(self):
        with TemporaryDirectory() as tmp:
            service,body=self.setup_service(tmp,index_queue_limit=1)
            class Handler(handler_for(service)):
                def send_response(self,status):self.status=status
                def send_header(self,*args):pass
                def end_headers(self):pass
            def request(path,body=None):
                h=object.__new__(Handler);h.path=path;raw=json.dumps(body).encode()
                h.headers={'Content-Length':str(len(raw))};h.rfile=io.BytesIO(raw);h.wfile=io.BytesIO()
                h.do_POST() if body is not None else h.do_GET()
                return h.status,json.loads(h.wfile.getvalue())
            code,job=request('/index-jobs',body);self.assertEqual(code,202)
            self.assertEqual(request('/index-jobs',dict(body,scope_id='overflow'))[0],429)
            with self.assertLogs('visn.retrieval',level='ERROR'):
                self.assertEqual(request('/index-jobs',{})[0],400)
            service.lock.acquire()
            try:
                code,value=request('/index-jobs?scope_id=recording');self.assertEqual(value['jobs'][0]['id'],job['id'])
                self.assertEqual(request('/index-jobs/'+job['id']+'/cancel',{})[1]['status'],'cancelled')
                self.assertEqual(request('/search',{'query':'red'})[0],503)
                self.assertTrue(request('/health')[1]['scheduler']['active'])
            finally:service.lock.release()


if __name__=='__main__':unittest.main()
