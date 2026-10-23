#!/usr/bin/env python3
"""Owned recording revocation replay: real media/queue, deterministic test embeddings.

Verifies access denial, in-flight response checks, durable cleanup/restarts and
lost cleanup acknowledgements. Not model-quality or physical-erasure acceptance.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

from check_recording_recovery import ROOT, Service, wait_until
from check_retrieval import Models
from semantic_memory_server import Retrieval, handler_for
from workflow_indexing import IndexJobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--ui-seconds',type=int,default=0)
    args = parser.parse_args()
    if not 0 <= args.ui_seconds <= 600:parser.error('ui-seconds must be 0..600')
    output = (ROOT/args.output).resolve();output.mkdir(parents=True,exist_ok=False)
    source = ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,
        directory=str(ROOT/'output/depin-implementation/bp03-fixtures')))
    source_thread = threading.Thread(target=source.serve_forever,daemon=True);source_thread.start()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    rust=Service(output/'data',output)
    rust.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/event_memory_runner.py',
        VISN_RETRIEVAL_URL=f'http://127.0.0.1:{port}',VISN_RETRIEVAL_TIMEOUT_SECS='10')
    worker=server=thread=None
    observations=[]
    fault=dict(hold_query=False,block_cleanup=True,drop_ack=True)
    query_waiting=threading.Event();release_query=threading.Event();ack_dropped=threading.Event()

    def request(route,body=None,expected=200,base=None):
        req=urllib.request.Request((base or rust.base)+route,
            data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        try:response=urllib.request.urlopen(req,timeout=40)
        except urllib.error.HTTPError as error:response=error
        with response:
            raw=response.read()
            value=json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else None
            assert response.status==expected,(route,response.status,value)
            observations.append(dict(route=route,status=response.status,response=value,bytes=len(raw)))
            if '/memory-events/' in route and response.status==200:
                assert response.headers.get('Cache-Control')=='private, no-store'
            return value if value is not None else raw

    def start_worker():
        nonlocal worker,server,thread
        worker=Retrieval(SimpleNamespace(data_dir=str(rust.data),frames=2,frame_size=64),Models())
        worker.indexer=IndexJobs(worker)
        class Handler(handler_for(worker)):
            def process_post(self):
                if self.path=='/revocations' and fault['block_cleanup']:
                    return self.reply(503,{'error':'fixture cleanup temporarily unavailable'})
                return super().process_post()
            def reply(self,status,body):
                if self.path=='/search' and status==200 and fault['hold_query']:
                    query_waiting.set()
                    if not release_query.wait(30):raise TimeoutError('fixture query release missing')
                if self.path=='/revocations' and status==200 and fault['drop_ack']:
                    fault['drop_ack']=False
                    self.send_response(status);self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length','1000');self.end_headers()
                    self.wfile.write(b'{"scope_id":');self.wfile.flush()
                    ack_dropped.set();self.close_connection=True;self.connection.close();return
                return super().reply(status,body)
        server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();worker.indexer.start()

    def stop_worker():
        nonlocal worker,server,thread
        if server:server.shutdown();server.server_close();thread.join(timeout=5);server=None
        if worker:
            worker.indexer.close();worker.db.close();worker.revocations.close();worker=None

    def hold_ui(phase,job_id):
        if not args.ui_seconds:return
        (output/'ui-info.json').write_text(json.dumps(dict(base=rust.base,job_id=job_id,phase=phase)))
        print(f'UI phase {phase}; touch OUTPUT/ui-{phase}-done to continue',flush=True)
        deadline=time.monotonic()+args.ui_seconds
        while time.monotonic()<deadline and not (output/f'ui-{phase}-done').exists():time.sleep(.2)

    try:
        start_worker();rust.start()
        print('Capture and prepare actual sanitized evidence',flush=True)
        job=request('/api/v1/memory-jobs',dict(name='revocation workflow',
            cameras=[dict(camera_id='revoke-camera',live_url=f'http://127.0.0.1:{source.server_port}/face-sequence.mkv')],
            monitor_duration_secs=2,observer_fps=2,vlm_enabled=False),202)
        route='/api/v1/memory-jobs/'+job['id']
        def prepared():
            current=request(route)
            assert current['status']!='failed',current
            return current if (current.get('visual_projection') or {}).get('state')=='completed' else None
        job=wait_until(prepared,45)
        task=job['visual_projection']['task_id']
        camera=job['result']['camera_results'][0];event=camera['events'][0]
        paths=[event['thumbnail_url'],event['evidence_url'],camera['evidence_url']]
        for path in paths:request(path)
        media=rust.data/'memory'/job['id']/'revoke-camera/source.mkv'
        digest=hashlib.sha256(media.read_bytes()).hexdigest()
        query=dict(query='recorded activity',job_id=job['id'],semantic_enabled=False,rerank_enabled=False)
        assert request('/api/v1/memory-query',query)['events_considered']>0
        worker.db.execute('CREATE TABLE interpretations(key TEXT PRIMARY KEY,result TEXT NOT NULL)')
        worker.db.execute("INSERT INTO interpretations VALUES ('fixture-answer','{}')")
        worker.record_artifact_source('interpretation','fixture-answer',media)
        hold_ui('ready',job['id'])
        current=request(route)
        inflight_tested=current['revocation'] is None
        if inflight_tested:
            print('Revoke while a semantic response is in flight; suppress cleanup initially',flush=True)
            fault['hold_query']=True
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(request,'/api/v1/memory-query',dict(query,semantic_enabled=True),410)
                assert query_waiting.wait(10),'semantic request never reached worker'
                revoked=request(route+'/revoke',{})
                release_query.set();future.result(timeout=15)
        else:
            print('Browser already revoked the recording; in-flight query test is covered by the non-browser replay',flush=True)
            revoked=current
        assert revoked['revocation']['cleanup_state']=='pending' and revoked['result'] is None
        revision=revoked['revocation']['revision']
        fault['hold_query']=False
        for path in paths:request(path,expected=404)
        for action in ['resume','cancel','long-memory','visual-index']:request(route+'/'+action,{},410)
        request(route+'/visual-index',expected=404)
        request('/api/v1/memory-query',query,410)
        assert request('/api/v1/memory-query',dict(query,job_id=None))['events_considered']==0
        assert request(route+'/revoke',{})['revocation']['revision']==revision
        hold_ui('revoked',job['id'])
        print('Worker outage and hard Rust restart preserve access denial and pending cleanup',flush=True)
        stop_worker()
        def unavailable():
            current=request(route)
            return current if 'projection_worker_unavailable' in (current['revocation']['last_error'] or '') else None
        wait_until(unavailable,90)
        rust.stop(hard=True);rust.start()
        assert request(route)['revocation']['revision']==revision
        for path in paths:request(path,expected=404)
        fault['block_cleanup']=False
        start_worker()
        assert ack_dropped.wait(90),'cleanup did not reach worker'
        wait_until(unavailable,20)
        assert worker.db.execute('SELECT count(*) FROM vectors').fetchone()[0]==0
        assert worker.db.execute('SELECT count(*) FROM interpretations').fetchone()[0]==0
        print('Restart both services after cleanup commit but before its acknowledgement',flush=True)
        rust.stop(hard=True);stop_worker();start_worker();rust.start()
        def cleaned():
            current=request(route)
            assert current['revocation']['cleanup_state']!='blocked',current
            return current if current['revocation']['cleanup_state']=='completed' else None
        final=wait_until(cleaned,180)
        assert final['revocation']['revision']==revision and final['result'] is None
        assert final['revocation']['receipt']['media_erased'] is False
        assert worker.indexer.get(task)['status']=='cancelled'
        original=dict(event_id=event['event_id'],source_path=str(media),start_ms=event['start_ms'],end_ms=event['end_ms'])
        for endpoint,body in [('/search',dict(query='activity',events=[original],limit=1,rerank=False)),
                              ('/index-jobs',dict(scope_id=job['id'],events=[original]))]:
            request(endpoint,body,410,base=f'http://127.0.0.1:{port}')
        request('/index-jobs/'+task+'/retry',{},410,base=f'http://127.0.0.1:{port}')
        assert hashlib.sha256(media.read_bytes()).hexdigest()==digest
        assert request(route+'/revoke',{})['revocation']['cleanup_state']=='completed'
        (output/'results.json').write_text(json.dumps(dict(scope=__doc__,final=final,
            media_sha256=digest,media_retained=True,inflight_tested=inflight_tested,observations=observations),indent=2)+'\n')
        print(f'Passed revocation, in-flight query, denial, outage and lost-ack restart replay: {output}',flush=True)
    finally:
        release_query.set();rust.stop();stop_worker()
        source.shutdown();source.server_close();source_thread.join(timeout=5)


if __name__=='__main__':main()
