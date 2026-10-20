#!/usr/bin/env python3
"""Sequential pinned controls, then retained-file mixed load at 1/2/4 streams."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from general_video_experiment import corpus_inventory,digest,read,signature
from workflow_resources import Residency

ROOT=Path(__file__).resolve().parents[1]


class Detector:
    def __init__(self,script,output,environment):
        self.log=output.open('w');self.messages=queue.Queue();self.observations=defaultdict(list);self.frames=defaultdict(int)
        self.process=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(script),'--model',str(ROOT/'yolo26s.pt'),
            '--device','cpu','--threads','1','--max-sessions','4','--max-batch-size','1','--imgsz','640'],cwd=ROOT,
            env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,bufsize=1)
        def receive():
            for line in self.process.stdout:
                if line.startswith('VISN_WORKER_JSON:'):self.messages.put(json.loads(line.split(':',1)[1]))
            self.messages.put({'type':'process_closed'})
        self.reader=threading.Thread(target=receive,daemon=True);self.reader.start()
        while True:
            message=self.messages.get(timeout=60)
            if message['type']=='ready':break
            if message['type']=='process_closed':raise AssertionError('Detector failed; inspect log')

    def cycle(self,source,count):
        ids=[str(uuid.uuid4()) for _ in range(count)]
        for identifier in ids:
            body=dict(type='analyze',request_id=identifier,source=str(source),fps=1,max_seconds=6,
                confidence=.25,appearance_mode='off',appearance_interval_secs=1)
            self.process.stdin.write(json.dumps(body)+'\n')
        self.process.stdin.flush();done={};started=time.monotonic()
        while len(done)<count:
            message=self.messages.get(timeout=300);kind=message['type'];identifier=message.get('request_id')
            if kind in ('error','process_closed'):raise AssertionError(message)
            if kind=='observations':
                self.frames[identifier]+=1;self.observations[identifier].extend(message['observations'])
            elif kind=='complete':
                done[identifier]=dict(frames=self.frames.pop(identifier),observations=self.observations.pop(identifier),result=message['result'])
        return [done[i] for i in ids],time.monotonic()-started

    def close(self):
        if self.process.poll() is None:
            try:self.process.stdin.write('{"type":"shutdown"}\n');self.process.stdin.flush();self.process.wait(timeout=10)
            except (OSError,subprocess.TimeoutExpired):self.process.kill();self.process.wait()
        self.reader.join(timeout=5);self.process.stdin.close();self.process.stdout.close();self.log.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--phase-seconds',type=float,default=60);args=parser.parse_args()
    if args.phase_seconds<1:parser.error('phase-seconds must be positive')
    output=Path(args.output).absolute();output.mkdir(parents=True,exist_ok=False)
    baseline=output.parent/'package5-baseline/tools'
    original=ROOT/'data/live-evaluation-2026-10-16'
    sources=read(ROOT/'evals/general-video/corpus.json');corpus_inventory(sources)
    code_files=['tools/semantic_memory_server.py','tools/workflow_indexing.py','tools/workflow_resources.py','tools/yolo26_worker.py','tools/temporal_interpretation.py','tools/check_resource_load.py']
    hashes={name:digest(ROOT/name) for name in code_files}
    env=dict(os.environ,HF_HUB_OFFLINE='1',HF_HOME=str(ROOT/'.retrieval/huggingface'),VISN_RESOURCE_DIR='',VISN_MANAGED_LMSTUDIO='false')
    processes=[];detectors=[]
    def phase(message):print(datetime.now(timezone.utc).isoformat(),message,flush=True)
    def save(name,value):(output/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def call(port,path,body=None):
        request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        started=time.monotonic()
        with urllib.request.urlopen(request,timeout=300) as response:return json.load(response),time.monotonic()-started
    def stop(p):
        if p.poll() is None:
            p.terminate()
            try:p.wait(timeout=15)
            except subprocess.TimeoutExpired:p.kill();p.wait()
    def worker(data,name,coordinated=False):
        log=(output/(name+'.log')).open('w');script=ROOT/'tools/semantic_memory_server.py' if coordinated else baseline/'semantic_memory_server.py'
        command=[str(ROOT/'.venv-retrieval/bin/python'),str(script),'--data-dir',str(data),'--qwen-source',str(ROOT/'.retrieval/qwen'),
            '--port','8230' if coordinated else '8231','--device','mps','--keep-models-loaded','--telemetry',
            '--embedding-revision','9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda','--reranker-revision','4bd860ac4f15ad1897a214615cccc700f8f71818']
        process=subprocess.Popen(command,cwd=ROOT,env=dict(env,VISN_RESOURCE_DIR=str(output/'resources') if coordinated else ''),stdout=log,stderr=subprocess.STDOUT)
        log.close();processes.append(process)
        for _ in range(300):
            if process.poll() is not None:raise AssertionError('Retrieval process exited')
            try:call(8230 if coordinated else 8231,'/health');return process
            except OSError:time.sleep(.1)
        raise AssertionError('Worker startup timed out')
    try:
        phase('Create isolated data and exact vector-database backups')
        for variant in ('control','candidate'):
            data=output/variant;shutil.copytree(original/'memory',data/'memory',copy_function=os.link)
            src=ROOT/'output/general-video-2026-10-18/package4-validation-final/candidate/semantic-v1.sqlite3'
            with sqlite3.connect(f'file:{src}?mode=ro',uri=True) as source,sqlite3.connect(data/'semantic-v1.sqlite3') as target:source.backup(target)
        def bodies(data):
            result=[]
            for name in ('toll-queue','negative-door'):
                body=read(ROOT/f'evals/general-video/requests/{name}.json')
                for event in body['events']:event['source_path']=str(data/Path(event['source_path']).relative_to(original))
                result.append(body)
            return result
        controls=bodies(output/'control');source=Path(controls[0]['events'][0]['source_path'])
        intervals={str(n):[dict(event_id=f'phase-{n}-{i}',source_path=str(source),start_ms=base+i*1000,end_ms=base+(i+1)*1000) for i in range(4)] for n,base in [(1,0),(2,10000),(4,20000)]}
        phase('Frozen retrieval control: real pinned models, warm repeats and twelve fresh interval vectors')
        old=worker(output/'control','control-worker');expected=[];warm_control=[]
        for body in controls:
            value,_=call(8231,'/search',body);expected.append(value)
            for _ in range(10):
                value,elapsed=call(8231,'/search',body);warm_control.append(elapsed)
                assert signature(value)==signature(expected[-1])
        all_intervals=[e for group in intervals.values() for e in group]
        call(8231,'/search',dict(query='road activity',events=all_intervals,lexical_ids=[],limit=12,rerank=False))
        save('control-retrieval.json',expected);save('control-warm-seconds.json',warm_control);stop(old)
        phase('Frozen detector control: retained bytes, CPU FP32, 640px, one frame per batch')
        old_detector=Detector(baseline/'yolo26_worker.py',output/'control-detector.log',env);detectors.append(old_detector)
        control_detection,_=old_detector.cycle(source,1);old_detector.close();detectors.remove(old_detector)
        save('control-detector.json',control_detection[0])
        phase('Coordinated worker: uncontended warm retrieval parity')
        current=worker(output/'candidate','candidate-worker',True);requests=bodies(output/'candidate');warm=[];first_requests=[]
        for index,body in enumerate(requests):
            value,elapsed=call(8230,'/search',body)
            assert signature(value)==signature(expected[index])
            first_requests.append(dict(elapsed_secs=elapsed,exact=True))
            for _ in range(10):
                value,elapsed=call(8230,'/search',body);warm.append(elapsed)
                assert signature(value)==signature(expected[index])
        save('candidate-warm-seconds.json',warm);save('candidate-first-requests.json',first_requests)
        coordinated_env=dict(env,VISN_RESOURCE_DIR=str(output/'resources'))
        detector=Detector(ROOT/'tools/yolo26_worker.py',output/'candidate-detector.log',coordinated_env);detectors.append(detector)
        candidate_source=Path(requests[0]['events'][0]['source_path'])
        phase('Contended single-stream detector parity')
        single,_=detector.cycle(candidate_source,1)
        assert single[0]==control_detection[0],'Single-stream observations, IDs or evidence changed'
        save('candidate-detector.json',single[0])
        probe=Residency('probe',directory=output/'resources');load=[];health=[]
        for count in (1,2,4):
            phase(f'Mixed sustained development replay: {count} retained-file stream(s), minimum {args.phase_seconds}s')
            events=[dict(e,source_path=str(candidate_source)) for e in intervals[str(count)]]
            task,_=call(8230,'/index-jobs',dict(scope_id=f'load-{count}',events=events))
            started=time.monotonic();stop_queries=threading.Event();query_rows=[];errors=[]
            def queries():
                index=0
                while not stop_queries.is_set():
                    try:
                        value,elapsed=call(8230,'/search',requests[index%2])
                        assert signature(value)==signature(expected[index%2])
                        query_rows.append(dict(query=index%2,elapsed_secs=elapsed,exact=True));index+=1
                    except Exception as error:errors.append(repr(error));return
                    stop_queries.wait(.5)
            reader=threading.Thread(target=queries,daemon=True);reader.start();cycles=[]
            try:
                while time.monotonic()-started<args.phase_seconds:
                    values,elapsed=detector.cycle(candidate_source,count)
                    for value in values:
                        assert value['frames']==control_detection[0]['frames'],'Retained-file samples were lost'
                        # Cross-camera global tracker IDs depend on scheduling;
                        # frame coverage and raw numeric detections stay checked.
                        strip=lambda rows:[{k:v for k,v in row.items() if k!='track_id'} for row in rows]
                        assert strip(value['observations'])==strip(control_detection[0]['observations'])
                    cycles.append(dict(elapsed_secs=elapsed,streams=count,frames=[v['frames'] for v in values]))
                    health.append(probe.snapshot())
                deadline=time.monotonic()+240
                while True:
                    progress,_=call(8230,'/index-jobs/'+task['id'])
                    if progress['status'] in ('completed','failed'):break
                    assert time.monotonic()<deadline,'Background preparation failed to drain'
                    time.sleep(.1)
                assert progress['status']=='completed',progress
            finally:stop_queries.set();reader.join(timeout=300)
            assert not errors and query_rows,(errors,query_rows)
            load.append(dict(streams=count,elapsed_secs=time.monotonic()-started,cycles=cycles,queries=query_rows,task=progress,errors=errors))
            save('load-results.json',load);save('resource-snapshots.json',health)
        detector.close();detectors.remove(detector)
        with sqlite3.connect(output/'control/semantic-v1.sqlite3') as left,sqlite3.connect(output/'candidate/semantic-v1.sqlite3') as right:
            a=left.execute('SELECT key,version,vector FROM vectors ORDER BY key').fetchall();b=right.execute('SELECT key,version,vector FROM vectors ORDER BY key').fetchall()
        assert a==b,'Persisted vector values changed'
        corpus_inventory(sources);assert hashes=={name:digest(ROOT/name) for name in code_files}
        save('summary.json',dict(passed=True,code=hashes,source_hashes={s['id']:s['sha256'] for s in sources['sources']},
            yolo_model_sha256=digest(ROOT/'yolo26s.pt'),single_stream_exact=True,multi_stream_detections_exact_except_global_track_ids=True,
            exact_vector_rows=len(a),fresh_intervals=12,uncontended_paired_requests=20,load_streams=[1,2,4],phase_seconds=args.phase_seconds,
            limitations=['Retained-file backpressure, not direct-live replacement or reconnect coverage.','CPU detector FP32 batch=1; Qwen MPS FP16 unchanged.',
            'Cross-camera tracker counter ordering is not stable across concurrent replays.','No generic action accuracy or production capacity acceptance.']))
        phase('All resource/load development checks passed')
    finally:
        for detector in detectors:detector.close()
        for process in reversed(processes):stop(process)


if __name__=='__main__':main()
