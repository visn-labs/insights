#!/usr/bin/env python3
"""Telemetry attribution, isolation, no model initialization and HTTP parity."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from check_retrieval import Models, write_video
from general_video_experiment import signature
from semantic_memory_server import Retrieval, handler_for
from workflow_telemetry import CURRENT, WorkflowTrace, measured, resources, stage, trace_id


class Checks(unittest.TestCase):
    def test_nested_inclusive_and_exclusive_attribution(self):
        with patch('workflow_telemetry.resources',return_value={}), patch('workflow_telemetry.time.perf_counter',side_effect=[0,1,3,5,8,10]):
            with WorkflowTrace('request') as trace:
                with stage('parent'):
                    with stage('child'): pass
        self.assertEqual(trace.stages['parent']['inclusive_host_secs'],7)
        self.assertEqual(trace.stages['parent']['exclusive_host_secs'],5)
        self.assertEqual(trace.stages['child']['inclusive_host_secs'],2)
        self.assertIsNone(CURRENT.get())

    def test_failure_cleanup_and_disabled_instrumentation(self):
        @measured('model_call')
        def failure():raise ValueError('fixture')
        with self.assertRaises(ValueError),WorkflowTrace('failure') as trace:failure()
        self.assertEqual(trace.status,'error');self.assertEqual(trace.stages['model_call']['failures'],1)
        self.assertIsNone(CURRENT.get())
        with patch('workflow_telemetry.resources',side_effect=AssertionError('Disabled telemetry probed resources')):
            with WorkflowTrace('disabled',enabled=False) as trace:
                with stage('noop'):pass
        self.assertEqual(trace.stages,{})

    def test_thread_contexts_do_not_mix(self):
        barrier=Barrier(2)
        def run(name):
            with WorkflowTrace(name) as trace:
                barrier.wait()
                with stage(name):self.assertEqual(CURRENT.get().request_id,name)
            return set(trace.stages)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(run,'one');b=pool.submit(run,'two')
            self.assertEqual(a.result(),{'one'});self.assertEqual(b.result(),{'two'})

    def test_resource_probe_never_imports_models_or_synchronizes(self):
        fake=SimpleNamespace(cuda=SimpleNamespace(is_initialized=lambda:False),mps=SimpleNamespace(current_allocated_memory=Mock(return_value=123),driver_allocated_memory=Mock(return_value=456),synchronize=Mock()))
        with patch.dict('sys.modules',torch=fake):
            self.assertIsNone(resources()['accelerator']);fake.mps.current_allocated_memory.assert_not_called()
            value=resources(mps_ready=True)
            self.assertEqual(value['accelerator']['allocated_bytes'],123);fake.mps.synchronize.assert_not_called()
        with patch.dict('sys.modules',torch=None):self.assertIsNone(resources()['accelerator'])
        self.assertNotEqual(trace_id('invalid\nheader'),'invalid\nheader')

    def invoke(self,service,body,query_id='request-id'):
        class Handler(handler_for(service)):
            def send_response(self,status):self.status=status
            def send_header(self,*args):pass
            def end_headers(self):pass
        h=object.__new__(Handler);raw=json.dumps(body).encode()
        h.path='/search';h.headers={'Content-Length':str(len(raw)),'X-Visn-Query-ID':query_id,'X-Visn-Run-ID':'run-id'}
        h.rfile=io.BytesIO(raw);h.wfile=io.BytesIO()
        with self.assertLogs('visn.telemetry',level='INFO') as logs:h.do_POST()
        final=json.loads(logs.output[-1].split('WORKFLOW_TRACE ',1)[1])
        return h.status,json.loads(h.wfile.getvalue()),final

    def test_http_parity_reports_correlation_and_final_serialization(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4';write_video(source)
            args=SimpleNamespace(data_dir=tmp,frames=2,frame_size=64,disable_telemetry=False)
            service=Retrieval(args,Models());body=dict(query='red',events=[dict(event_id='red',source_path=str(source),start_ms=0,end_ms=1000)],lexical_ids=[],limit=1,rerank=True)
            status,enabled,final=self.invoke(service,body)
            self.assertEqual(status,200);proof=enabled['provenance']['telemetry']
            self.assertEqual(proof['request_id'],'request-id');self.assertEqual(proof['run_id'],'run-id')
            self.assertFalse(proof['final']);self.assertNotIn('response_serialization',proof['stages'])
            self.assertTrue(final['final']);self.assertIn('response_serialization',final['stages']);self.assertIn('response_write',final['stages']);self.assertIn('request_cleanup',final['stages'])
            self.assertIn('ffmpeg_process',final['stages']);self.assertIn('retrieval',final['stages'])
            args.disable_telemetry=True
            # Disabled requests intentionally have no final telemetry log.
            Handler=handler_for(service);h=object.__new__(Handler);raw=json.dumps(body).encode()
            h.headers={'Content-Length':str(len(raw))};h.path='/search';h.rfile=io.BytesIO(raw);h.wfile=io.BytesIO()
            h.send_response=lambda status:None;h.send_header=lambda *a:None;h.end_headers=lambda:None
            h.do_POST();disabled=json.loads(h.wfile.getvalue())
            self.assertEqual(signature(enabled),signature(disabled));self.assertNotIn('telemetry',disabled['provenance']);service.db.close()

    def test_busy_and_error_requests_have_final_traces(self):
        with TemporaryDirectory() as tmp:
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=2,frame_size=64,disable_telemetry=False),Models())
            service.lock.acquire()
            try:
                status,_,trace=self.invoke(service,{'query':'red'})
                self.assertEqual(status,503);self.assertEqual(trace['status'],'busy');self.assertNotIn('retrieval',trace['stages'])
            finally:service.lock.release()
            status,_,trace=self.invoke(service,{'query':''})
            self.assertEqual(status,500);self.assertEqual(trace['status'],'error')
            self.assertEqual(trace['stages']['retrieval']['failures'],1);self.assertFalse(service.lock.locked());service.db.close()


if __name__=='__main__':unittest.main()
