#!/usr/bin/env python3
"""Pinned local integration replay. Writes only to a new isolated output tree."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from general_video_experiment import corpus_inventory, digest, read, signature

ROOT = Path(__file__).resolve().parents[1]
LIVE_JOB = '01a1433a-26bf-7e50-b482-5007781d97c7'
NIGHT_JOB = '01a1433a-26bf-7e50-b482-4ff132722114'
THIRD_JOB = '01a1433a-26bf-7e50-b482-4fe9e55d087c'
FAILURE_JOB = 'd4ce756e-b174-57f2-b85d-95acb3b0649f'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--browser',action='store_true')
    args=parser.parse_args()
    output=Path(args.output).absolute();output.mkdir(parents=True,exist_ok=False)
    original=ROOT/'data/live-evaluation-2026-10-16'
    python=ROOT/'.venv-retrieval/bin/python'
    env=dict(os.environ,HF_HUB_OFFLINE='1',HF_HOME=str(ROOT/'.retrieval/huggingface'))
    processes=[]
    def phase(text):print(datetime.now(timezone.utc).isoformat(),text,flush=True)
    def save(name,value):(output/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    code_files=['tools/semantic_memory_server.py','tools/workflow_indexing.py','tools/workflow_telemetry.py','tools/check_background_index_workflow.py','tools/check_background_index_browser.cjs','static/app.js','static/index.html','static/styles.css','src/api.rs','src/retrieval.rs','src/store.rs']
    code_hashes={name:digest(ROOT/name) for name in code_files}
    save('runtime-code.json',dict(code=code_hashes,binary=digest(ROOT/'target/debug/visn-phase0')))
    def call(base,path,body=None,timeout=900):
        request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json','X-Visn-Query-ID':str(uuid.uuid4())})
        started=time.monotonic()
        try:
            with urllib.request.urlopen(request,timeout=timeout) as response:
                return response.status,json.load(response),time.monotonic()-started
        except urllib.error.HTTPError as error:
            return error.code,json.load(error),time.monotonic()-started
    def until(predicate,seconds=900):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            result=predicate()
            if result:return result
            time.sleep(.1)
        raise AssertionError('Integration phase timed out')
    def launch(command,name,extra=None):
        log=(output/(name+'.log')).open('w')
        process=subprocess.Popen([str(c) for c in command],cwd=ROOT,env=dict(env,**(extra or {})),stdout=log,stderr=subprocess.STDOUT)
        log.close();processes.append(process);return process
    def halt(process,kill=False):
        if process.poll() is None:
            process.kill() if kill else process.terminate()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    def ready(process,base,path='/health'):
        def probe():
            if process.poll() is not None:raise AssertionError('Validation process exited; inspect its log')
            try:return call(base,path,timeout=2)[0]==200
            except (urllib.error.URLError,TimeoutError):return False
        until(probe,60)
    def worker(data,name,baseline=False):
        script=output.parent/'package4-baseline/tools/semantic_memory_server.py' if baseline else ROOT/'tools/semantic_memory_server.py'
        command=[python,script,'--port','8231' if baseline else '8230','--data-dir',data,
            '--qwen-source',ROOT/'.retrieval/qwen','--device','mps','--keep-models-loaded',
            '--embedding-revision','9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda',
            '--reranker-revision','4bd860ac4f15ad1897a214615cccc700f8f71818',
            '--interpretation-revision','89644892e4d85e24eaac8bacfd4f463576704203']
        if not baseline:command.append('--telemetry')
        process=launch(command,name);ready(process,'http://127.0.0.1:'+('8231' if baseline else '8230'));return process
    def vectors(data):
        with sqlite3.connect(data/'semantic-v1.sqlite3') as db:return db.execute('SELECT key,version,vector FROM vectors ORDER BY key').fetchall()
    def poll_task():return call(app,f'/api/v1/memory-jobs/{LIVE_JOB}/visual-index')[1]['jobs'][0]
    try:
        phase('Prepare isolated retained evidence; source files remain read-only inputs')
        before={str(p.relative_to(original)):digest(p) for p in original.rglob('source.mkv')}
        for variant in ('control','candidate'):
            shutil.copytree(original/'memory',output/variant/'memory',copy_function=os.link)
        # Deliberately invalid derived interval, in this output tree only. This
        # tests partial-failure presentation; it is not a labeled video example.
        fixture=json.loads((original/f'memory/manifests/{LIVE_JOB}.json').read_text())
        fixture['id']=FAILURE_JOB;fixture['request']['name']='Synthetic invalid-interval control'
        fixture['created_at_ms']+=1000
        camera=fixture['result']['camera_results'][0];old_event=camera['events'][0]
        events=[];directory=output/'candidate'/'memory'/FAILURE_JOB/camera['camera']['camera_id'];directory.mkdir(parents=True)
        source_directory=original/'memory'/LIVE_JOB/camera['camera']['camera_id']
        os.link(source_directory/'source.mkv',directory/'source.mkv')
        for number in range(2):
            event=dict(old_event,event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f'visn-invalid-interval-{number}')),job_id=FAILURE_JOB)
            if number:event.update(start_ms=900000,end_ms=901000,duration_ms=1000)
            event['thumbnail_url']=f'/api/v1/memory-events/{event["event_id"]}/thumbnail'
            event['evidence_url']=f'/api/v1/memory-events/{event["event_id"]}/clip'
            os.link(source_directory/(old_event['event_id']+'.jpg'),directory/(event['event_id']+'.jpg'))
            events.append(event)
        camera['events']=events;camera['long_memory']=None;camera['data_quality_notes']=['Synthetic invalid-interval control; not accuracy evidence.']
        fixture['result']['events_indexed']=2
        (output/'candidate'/f'memory/manifests/{FAILURE_JOB}.json').write_text(json.dumps(fixture))
        cases=[]
        for name in ('toll-queue','negative-door'):
            body=json.loads((ROOT/f'evals/general-video/requests/{name}.json').read_text())
            for event in body['events']:
                event['source_path']=str(output/'control'/Path(event['source_path']).relative_to(original))
            cases.append((name,body))
        phase('Run frozen pre-package worker: empty vector cache, then exact warm controls')
        control=worker(output/'control','control-worker',baseline=True)
        baseline={}
        for name,body in cases:
            rows=[]
            for repeat in range(2):
                code,result,elapsed=call('http://127.0.0.1:8231','/search',body)
                assert code==200,(code,result)
                rows.append(dict(repeat=repeat,elapsed_secs=elapsed,result=result))
            baseline[name]=rows
        control_vectors=vectors(output/'control');save('control-results.json',baseline);halt(control)
        phase('Start candidate worker and rebuilt Rust application')
        current=worker(output/'candidate','candidate-worker')
        application=launch([ROOT/'target/debug/visn-phase0'],'application',dict(VISN_BIND='127.0.0.1:8220',VISN_DATA_DIR=str(output/'candidate'),VISN_RETRIEVAL_URL='http://127.0.0.1:8230'))
        app='http://127.0.0.1:8220';ready(application,app,'/healthz')
        code,task,_=call(app,f'/api/v1/memory-jobs/{LIVE_JOB}/visual-index',{})
        assert code==202,(code,task)
        code,duplicate,_=call(app,f'/api/v1/memory-jobs/{LIVE_JOB}/visual-index',{})
        assert duplicate['id']==task['id']
        interrupted=until(lambda: (v if v['completed']>=1 else None) if (v:=poll_task()) else None)
        assert interrupted['completed']<interrupted['total'],'Need an actual partially completed restart control'
        save('before-worker-kill.json',interrupted);halt(current,kill=True)
        phase(f'Recover after worker kill: {interrupted["completed"]}/{interrupted["total"]} intervals were durable')
        current=worker(output/'candidate','candidate-worker-restarted')
        complete=until(lambda: (v if v['status'] in ('completed','failed') else None) if (v:=poll_task()) else None)
        assert complete['status']=='completed',complete
        save('after-worker-restart.json',complete)
        phase('Compare prepared retrieval with frozen worker; exact vectors, scores and rankings')
        candidate={};parity=[]
        for name,body in cases:
            body=dict(body,events=[dict(e,source_path=e['source_path'].replace('/control/','/candidate/')) for e in body['events']])
            rows=[]
            for repeat in range(2):
                code,result,elapsed=call('http://127.0.0.1:8230','/search',body);assert code==200,(code,result)
                assert signature(result)==signature(baseline[name][repeat]['result']),name
                assert '4 cached embeddings' in result['notes'][0]
                rows.append(dict(repeat=repeat,elapsed_secs=elapsed,result=result))
                parity.append(dict(case_id=name,repeat=repeat,exact=True,elapsed_secs=elapsed))
            candidate[name]=rows
        assert vectors(output/'candidate')==control_vectors,'Persisted vector bytes differ'
        save('candidate-results.json',candidate);save('retrieval-parity.json',dict(rows=parity,exact_vectors=True,vector_count=len(control_vectors)))
        if args.browser:
            phase('Browser: prepare another recording, cancel, reload, resume, search and play evidence')
            browser=launch(['node',ROOT/'tools/check_background_index_browser.cjs'],'browser',dict(
                VISN_TEST_URL=app,VISN_INDEX_BROWSER_OUTPUT=str(ROOT/'output/playwright'/output.name),NODE_PATH='/private/tmp/visn-ui-qa/node_modules'))
            assert browser.wait(timeout=900)==0,'Browser validation failed; inspect browser.log'
            save('browser-artifacts.json',dict(path=str(ROOT/'output/playwright'/output.name)))
        phase('Concurrent background preparation, retrieval and static-sequence interpretation')
        code,third,_=call(app,f'/api/v1/memory-jobs/{THIRD_JOB}/visual-index',{});assert code==202
        source=output/'candidate'/'memory'/LIVE_JOB/'live-1/source.mkv'
        static=dict(query='Did the target change state?',events=[dict(event_id='static-window-control',source_path=str(source),start_ms=0,end_ms=8)])
        search_body=dict(cases[0][1],events=[dict(e,source_path=e['source_path'].replace('/control/','/candidate/')) for e in cases[0][1]['events']])
        with ThreadPoolExecutor(max_workers=2) as pool:
            searching=pool.submit(call,'http://127.0.0.1:8230','/search',search_body)
            interpreting=pool.submit(call,'http://127.0.0.1:8230','/interpret',static)
            search=searching.result();interpret=interpreting.result()
        assert search[0]==interpret[0]==200,(search[0],interpret[0])
        assert signature(search[1])==signature(baseline['toll-queue'][0]['result'])
        assert interpret[1]['interpretations'][0]['status']=='uncertain'
        save('concurrent-results.json',dict(search=search[1],interpretation=interpret[1]))
        finished=until(lambda: (v if v['status'] in ('completed','failed') else None) if (v:=call(app,f'/api/v1/memory-jobs/{THIRD_JOB}/visual-index')[1]['jobs'][0]) else None)
        assert finished['status']=='completed',finished
        save('third-recording.json',finished)
        # Rust and worker restart independently; the completed task is not held in browser memory.
        halt(application)
        application=launch([ROOT/'target/debug/visn-phase0'],'application-restarted',dict(VISN_BIND='127.0.0.1:8220',VISN_DATA_DIR=str(output/'candidate'),VISN_RETRIEVAL_URL='http://127.0.0.1:8230'))
        ready(application,app,'/healthz');assert poll_task()['status']=='completed'
        code,value,_=call(app,f'/api/v1/memory-jobs/{NIGHT_JOB}/visual-index/{task["id"]}/cancel',{})
        assert code==404,'Cross-recording task actions must be rejected'
        halt(current)
        code,offline,_=call(app,f'/api/v1/memory-jobs/{LIVE_JOB}/visual-index')
        assert code==503,(code,offline)
        code,fallback,_=call(app,'/api/v1/memory-query',dict(query='truck',job_id=LIVE_JOB,semantic_enabled=True))
        assert code==200 and fallback['retrieval_mode']=='local_lexical_metadata'
        assert any('unavailable' in note for note in fallback['retrieval_notes'])
        save('worker-unavailable.json',dict(status=offline,fallback=fallback))
        after={str(p.relative_to(original)):digest(p) for p in original.rglob('source.mkv')};assert before==after
        corpus_inventory(read(ROOT/'evals/general-video/corpus.json'))
        assert code_hashes=={name:digest(ROOT/name) for name in code_files},'Runtime source code changed during validation'
        save('summary.json',dict(passed=True,exact_pairs=4,exact_vectors=len(control_vectors),
            restart_partial=interrupted['completed'],restart_completed=complete['completed'],
            concurrent_search_and_static_interpretation=True,rust_restart=True,cross_scope_rejected=True,
            worker_unavailable_explicit=True,source_hashes_unchanged=before,
            scope='Development integration on retained traffic; static interpretation is a safeguard/scheduling control, not action accuracy.'))
        phase('All integration checks passed')
    finally:
        for process in reversed(processes):halt(process)


if __name__=='__main__':main()
