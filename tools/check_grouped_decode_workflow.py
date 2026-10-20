#!/usr/bin/env python3
"""Frozen decoder parity, balanced decode timings, optional pinned-model API replay."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from types import SimpleNamespace
import urllib.request

from general_video_experiment import corpus_inventory,digest,read,signature
from semantic_memory_server import Retrieval
from check_retrieval import Models

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL=ROOT/'data/live-evaluation-2026-10-16'
BASELINE=ROOT/'output/general-video-2026-10-20/package6-baseline'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--repeats',type=int,default=5);parser.add_argument('--models',action='store_true');parser.add_argument('--browser',action='store_true');args=parser.parse_args()
    if args.repeats<1:parser.error('repeats must be positive')
    if args.browser and not args.models:parser.error('browser validation requires --models')
    output=Path(args.output).absolute();output.mkdir(parents=True,exist_ok=False)
    code_names=['tools/semantic_memory_server.py','tools/grouped_decode.py','tools/check_grouped_decode.py','tools/check_grouped_decode_workflow.py','tools/temporal_interpretation.py','tools/workflow_indexing.py','tools/workflow_resources.py','tools/workflow_telemetry.py','tools/check_grouped_decode_browser.cjs','target/debug/visn-phase0']
    hashes={name:digest(ROOT/name) for name in code_names}
    (output/'runtime-code.json').write_text(json.dumps(hashes,indent=2)+'\n')
    frozen=read(BASELINE/'manifest.json')['files']
    assert all(digest(BASELINE/name)==value for name,value in frozen.items())
    inventory=corpus_inventory(read(ROOT/'evals/general-video/corpus.json'))
    def phase(message):print(datetime.now(timezone.utc).isoformat(),message,flush=True)
    def save(name,value):(output/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def data(name):
        directory=output/name;shutil.copytree(ORIGINAL/'memory',directory/'memory',copy_function=os.link);return directory
    def events(directory):
        return [dict(event_id=e['event_id'],source_path=str(directory/'memory'/job['id']/camera['camera']['camera_id']/'source.mkv'),start_ms=e['start_ms'],end_ms=e['end_ms'])
            for path in sorted((directory/'memory/manifests').glob('*.json')) for job in [read(path)]
            for camera in job['result']['camera_results'] for e in camera['events']]
    spec=importlib.util.spec_from_file_location('package6_frozen',BASELINE/'tools/semantic_memory_server.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    phase('Compare all retained intervals against frozen independent-seek decoding')
    directory=data('decode-data');services={}
    for name,cls in [('control',old.Retrieval),('candidate',Retrieval)]:
        services[name]=cls(SimpleNamespace(data_dir=str(directory),frames=8,frame_size=256,disable_work_caches=True,decode_policy='grouped-select-v1'),Models())
    records=[]
    def decode(service,event,size):
        before=dict(service.work_stats);started=time.monotonic()
        frames,times=service.frames(event,size=size,with_timestamps=True)
        elapsed=time.monotonic()-started
        proof=[dict(size=list(frame.size),rgb_sha256=hashlib.sha256(frame.tobytes()).hexdigest(),timestamp_ms=t) for frame,t in zip(frames,times)]
        return dict(elapsed_secs=elapsed,proof=proof,counters={k:v-before[k] for k,v in service.work_stats.items()})
    try:
        for event in events(directory):
            for size in (256,384):
                for repeat in range(args.repeats):
                    pair={name:decode(services[name],event,size) for name in (('control','candidate') if repeat%2==0 else ('candidate','control'))}
                    if pair['control']['proof']!=pair['candidate']['proof']:
                        save('failed-decode-pair.json',dict(event=event,size=size,repeat=repeat,**pair))
                        raise AssertionError((event,size,repeat))
                    records.append(dict(event_id=event['event_id'],size=size,repeat=repeat,**pair))
                save('decode-pairs.json',records)
    finally:
        for service in services.values():service.db.close()
    processes=[];env=dict(os.environ,HF_HUB_OFFLINE='1',HF_HOME=str(ROOT/'.retrieval/huggingface'),VISN_RESOURCE_DIR='',VISN_MANAGED_LMSTUDIO='false')
    def stop(p):
        if p.poll() is None:
            p.terminate()
            try:p.wait(timeout=15)
            except subprocess.TimeoutExpired:p.kill();p.wait()
    def launch(command,name,extra=None):
        with (output/(name+'.log')).open('w') as log:p=subprocess.Popen([str(c) for c in command],cwd=ROOT,env=dict(env,**(extra or {})),stdout=log,stderr=subprocess.STDOUT)
        processes.append(p);return p
    def call(port,path,body=None):
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        started=time.monotonic()
        with urllib.request.urlopen(req,timeout=300) as response:return json.load(response),time.monotonic()-started
    def ready(p,port,path='/health'):
        for _ in range(300):
            assert p.poll() is None,'Validation process exited; inspect its log'
            try:return call(port,path)
            except OSError:time.sleep(.1)
        raise AssertionError('Local service startup timed out')
    model_rows={};cold={}
    try:
        if args.models:
            for variant in ('control','candidate'):
                phase('Pinned real-model '+variant+': empty vector DB, then warm models with work caches disabled')
                directory=data(variant);script=BASELINE/'tools/semantic_memory_server.py' if variant=='control' else ROOT/'tools/semantic_memory_server.py'
                command=[ROOT/'.venv-retrieval/bin/python',script,'--data-dir',directory,'--port','8230','--qwen-source',ROOT/'.retrieval/qwen','--device','mps','--keep-models-loaded','--disable-work-caches','--telemetry',
                    '--embedding-revision','9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda','--reranker-revision','4bd860ac4f15ad1897a214615cccc700f8f71818']
                if variant=='candidate':command+=['--decode-policy','grouped-select-v1']
                worker=launch(command,variant+'-worker');ready(worker,8230)
                body=dict(query='road activity',events=events(directory),lexical_ids=[],rerank=False,limit=13)
                response,elapsed=call(8230,'/search',body);cold[variant]=dict(result=response,elapsed_secs=elapsed)
                save(variant+'-cold.json',cold[variant]);model_rows[variant]=[]
                for name in ('toll-queue','negative-door'):
                    body=read(ROOT/f'evals/general-video/requests/{name}.json')
                    for event in body['events']:event['source_path']=str(directory/Path(event['source_path']).relative_to(ORIGINAL))
                    call(8230,'/search',body) # Warm reranker separately from repeated timings.
                    for repeat in range(args.repeats):
                        response,elapsed=call(8230,'/search',body)
                        model_rows[variant].append(dict(case=name,repeat=repeat,elapsed_secs=elapsed,result=response))
                save(variant+'-queries.json',model_rows[variant])
                if variant=='candidate':
                    assert signature(cold['control']['result'])==signature(cold['candidate']['result'])
                    for a,b in zip(model_rows['control'],model_rows['candidate']):assert signature(a['result'])==signature(b['result'])
                    with sqlite3.connect(output/'control/semantic-v1.sqlite3') as a,sqlite3.connect(directory/'semantic-v1.sqlite3') as b:
                        va=a.execute('SELECT key,version,vector FROM vectors ORDER BY key').fetchall();vb=b.execute('SELECT key,version,vector FROM vectors ORDER BY key').fetchall()
                    assert va==vb and len(va)==13
                    phase('Rust API: background preparation, visual query and evidence endpoint')
                    app=launch([ROOT/'target/debug/visn-phase0'],'application',dict(VISN_BIND='127.0.0.1:8220',VISN_DATA_DIR=str(directory),VISN_RETRIEVAL_URL='http://127.0.0.1:8230'))
                    ready(app,8220,'/healthz');job='01a1433a-26bf-7e50-b482-5007781d97c7'
                    task,_=call(8220,f'/api/v1/memory-jobs/{job}/visual-index',{})
                    deadline=time.monotonic()+60
                    while task['status'] not in ('completed','failed'):
                        assert time.monotonic()<deadline
                        tasks,_=call(8220,f'/api/v1/memory-jobs/{job}/visual-index');task=tasks['jobs'][0];time.sleep(.1)
                    assert task['status']=='completed' and task['completed']==4
                    result,_=call(8220,'/api/v1/memory-query',dict(query='vehicles on the road',job_id=job,semantic_enabled=True))
                    assert result['retrieval_mode']=='hybrid_qwen_reranker' and result['matches']
                    url=result['matches'][0]['event']['evidence_url']
                    with urllib.request.urlopen('http://127.0.0.1:8220'+url,timeout=30) as evidence:assert evidence.status==200 and evidence.read(1024)
                    save('application-query.json',result);save('application-index.json',task)
                    if args.browser:
                        phase('Browser: grouped visual query, responsive rendering and evidence playback')
                        browser=launch(['node',ROOT/'tools/check_grouped_decode_browser.cjs'],'browser',dict(NODE_PATH='/private/tmp/visn-ui-qa/node_modules',VISN_DECODE_BROWSER_OUTPUT=str(ROOT/'output/playwright'/output.name)))
                        assert browser.wait(timeout=300)==0,'Browser check failed; inspect browser.log'
                    stop(app)
                stop(worker)
        assert hashes=={name:digest(ROOT/name) for name in code_names},'Runtime source/binary changed; discard this run'
        corpus_inventory(read(ROOT/'evals/general-video/corpus.json'))
        save('summary.json',dict(passed=True,code=hashes,baseline=frozen,decode_pairs=len(records),pixels_and_legacy_pts_exact=True,work_caches_disabled=True,repeats=args.repeats,
            model_api_checked=args.models,exact_vector_rows=13 if args.models else None,model_request_pairs=len(model_rows.get('control',[])),
            application_api_checked=args.models,browser_checked=args.browser,coverage=inventory,limits=['Retained development traffic, not generic acceptance.','No decoder kernel/dtype/resize change.','High-resolution detail outside the group payload bound uses independent seeks.','Model runs sequential control then candidate; decode pairs alternate order.']))
        phase('All grouped decode workflow checks passed')
    finally:
        for p in reversed(processes):stop(p)


if __name__=='__main__':main()
