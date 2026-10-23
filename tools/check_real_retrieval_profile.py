#!/usr/bin/env python3
"""Offline cached-Qwen smoke: actual weights, profile-bound HTTP and embeddings."""
import argparse
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('HF_HOME',str(ROOT/'.retrieval/huggingface'))
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
from check_retrieval import write_video
from semantic_memory_server import Retrieval, handler_for
from workflow_indexing import IndexJobs


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rust',action='store_true')
    parser.add_argument('--device',choices=['cpu','mps','cuda'],default='cpu')
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    import torch
    torch.set_num_threads(2)
    options=SimpleNamespace(data_dir=str(output),qwen_source=str(ROOT/'.retrieval/qwen'),
        embedding_model='Qwen/Qwen3-VL-Embedding-2B',embedding_revision='main',
        reranker_model='Qwen/Qwen3-VL-Reranker-2B',reranker_revision='main',frames=2,frame_size=64,
        device=args.device,keep_models_loaded=False,disable_telemetry=True,interactive_wait_secs=120)
    service=Retrieval(options);service.indexer=IndexJobs(service)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(service))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}';started=time.monotonic();observations=[]
    def phase(message):print(f'[{time.monotonic()-started:.1f}s] {message}',flush=True)
    def request(path,body=None,profile=None,status=200):
        headers={'Content-Type':'application/json'}
        if profile:headers['X-Visn-Expected-Profile']=profile
        req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        try:response=urllib.request.urlopen(req,timeout=300)
        except urllib.error.HTTPError as error:response=error
        with response:
            value=json.load(response);assert response.status==status,(path,response.status,value)
        observations.append(dict(path=path,status=response.status));return value
    try:
        phase('Seal cached model weights, resolved runtime/device and implementation')
        profile=request('/profile');fingerprint=profile['fingerprint']
        assert profile['manifest']['qualification']=='integration_candidate_not_quality_acceptance'
        embedding=profile['manifest']['embedding']
        assert embedding['runtime']['device']==args.device
        assert embedding['runtime']['dtype']==('float32' if args.device=='cpu' else 'float16')
        assert len(embedding['weights'])==64 and len(profile['manifest']['reranker']['weights'])==64
        source=output/'source.mp4';write_video(source)
        event=dict(event_id='red',source_path=str(source),start_ms=0,end_ms=1000)
        phase('Prepare and validate an actual Qwen video embedding')
        receipt=request('/index-jobs',dict(scope_id='real-profile',events=[event]),fingerprint,202)
        assert receipt['worker_profile']==profile
        service.indexer.run_one()
        prepared=service.indexer.get(receipt['id'])
        assert prepared['status']=='completed',prepared
        phase('Run profile-bound actual text/video retrieval and repeat from caches')
        body=dict(query='red background',events=[event],lexical_ids=[],limit=1,rerank=False)
        first=request('/search',body,fingerprint)
        assert first['items'][0]['event_id']=='red' and first['provenance']['worker_profile']==profile
        second=request('/search',body,fingerprint)
        assert first['items']==second['items']
        request('/search',body,'0'*64,409)
        assert request('/profile')==profile
        rust_evidence=None
        if args.rust:
            from check_recording_recovery import Service
            from functools import partial
            from http.server import SimpleHTTPRequestHandler
            class Source(SimpleHTTPRequestHandler):
                def log_message(self,*args):pass
            source_server=ThreadingHTTPServer(('127.0.0.1',0),partial(Source,directory=str(output)))
            source_thread=threading.Thread(target=source_server.serve_forever,daemon=True);source_thread.start()
            service.indexer.start()
            rust=Service(output,output)
            rust.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/event_memory_runner.py',
                VISN_RETRIEVAL_URL=base,VISN_RETRIEVAL_EXPECTED_PROFILE=fingerprint,VISN_RETRIEVAL_TIMEOUT_SECS='120')
            def api(path,body=None):
                req=urllib.request.Request(rust.base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=180) as response:return json.load(response)
            try:
                phase('Verify the Rust recording/search workflow honors the selected worker profile')
                rust.start()
                job=api('/api/v1/memory-jobs',dict(name='Actual pinned retrieval',cameras=[dict(camera_id='profile-camera',
                    live_url=f'http://127.0.0.1:{source_server.server_port}/source.mp4')],monitor_duration_secs=1,observer_fps=2,
                    long_memory_enabled=False,vlm_enabled=False))
                route='/api/v1/memory-jobs/'+job['id'];deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    current=api(route)
                    assert current['status']!='failed',current.get('error')
                    if current['status']=='completed':break
                    time.sleep(.1)
                assert current['status']=='completed'
                query=dict(query='red background',job_id=job['id'],semantic_enabled=True,rerank_enabled=False,vlm_enabled=False)
                matched=api('/api/v1/memory-query',query)
                assert matched['retrieval_mode']=='hybrid_rrf',matched
                assert matched['retrieval_provenance']['worker_profile']==profile
                for attempt in range(60):
                    try:
                        visual_status=api(route+'/visual-index');break
                    except urllib.error.HTTPError as error:
                        if error.code!=503 or attempt==59:raise
                        time.sleep(.1)
                assert visual_status['worker_profile']==profile
                rust.stop();rust.overrides['VISN_RETRIEVAL_EXPECTED_PROFILE']='0'*64;rust.start()
                rejected=api('/api/v1/memory-query',query)
                assert rejected['retrieval_mode']=='local_lexical_metadata',rejected
                assert any('unavailable' in note.lower() for note in rejected['retrieval_notes']),rejected
                rust_evidence=dict(job_id=job['id'],matched=matched,rejected=rejected,prepared=visual_status)
                api(route+'/revoke',{})
            finally:
                rust.stop();source_server.shutdown();source_server.server_close();source_thread.join(timeout=5)
        (output/'results.json').write_text(json.dumps(dict(scope=__doc__,profile=profile,
            prepared=prepared,first=first,second=second,rust=rust_evidence,observations=observations),indent=2)+'\n')
        phase('Passed actual cached-Qwen profile, preparation, query, cache parity and mismatch rejection')
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
        service.indexer.close();service.resources.release(service.release_models);service.db.close();service.revocations.close()


if __name__=='__main__':main()
