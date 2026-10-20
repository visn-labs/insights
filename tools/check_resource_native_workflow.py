#!/usr/bin/env python3
"""Rust/guardian native-v1 contract test; HTTP fixture is not a real model."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import urllib.request

from workflow_resources import Residency, ResourceBusy
from general_video_experiment import digest

ROOT=Path(__file__).resolve().parents[1]
JOB='01a1433a-26bf-7e50-b482-5007781d97c7'
MODEL='google/gemma-4-26b-a4b-qat'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output).absolute();output.mkdir(parents=True,exist_ok=False)
    code_files=['src/resources.rs','src/config.rs','src/gemma.rs','src/main.rs','tools/workflow_resources.py','tools/check_resource_native_workflow.py','target/debug/visn-phase0']
    hashes={name:digest(ROOT/name) for name in code_files}
    (output/'runtime-code.json').write_text(json.dumps(hashes,indent=2)+'\n')
    shutil.copytree(ROOT/'data/live-evaluation-2026-10-16/memory',output/'data/memory',copy_function=os.link)
    loaded=[];calls=[];chat_started=threading.Event();finish_chat=threading.Event();finish_chat.set()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,value):
            data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            calls.append(dict(path=self.path,authenticated=self.headers.get('Authorization')=='Bearer fixture-private-token'))
            if self.path=='/api/v1/models':self.respond({'models':[dict(key=MODEL,type='vlm',loaded_instances=[{'id':i} for i in loaded])]})
            elif self.path=='/v1/models':self.respond({'data':[{'id':MODEL}]})
            else:self.send_error(404)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(dict(path=self.path,body=body,authenticated=self.headers.get('Authorization')=='Bearer fixture-private-token'))
            if self.path=='/api/v1/models/load':
                loaded[:]=['fixture-instance'];self.respond(dict(status='loaded',instance_id=loaded[0]))
            elif self.path=='/api/v1/models/unload':
                if set(body)!={'instance_id'}:self.send_error(400);return
                loaded.remove(body['instance_id']);self.respond({'status':'unloaded'})
            elif self.path=='/v1/chat/completions':
                chat_started.set();finish_chat.wait(timeout=60)
                content=json.dumps(dict(summary='Retrieved evidence is available for review.',relevant_event_ids=[],confidence=.5))
                self.respond({'choices':[{'message':{'role':'assistant','content':content}}]})
            else:self.send_error(404)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    endpoint=f'http://127.0.0.1:{server.server_port}'
    env=dict(os.environ,VISN_BIND='127.0.0.1:8220',VISN_DATA_DIR=str(output/'data'),VISN_RETRIEVAL_URL='',
        VISN_GEMMA_BASE_URL=endpoint+'/v1',VISN_LMSTUDIO_API_BASE_URL=endpoint+'/api/v1',VISN_GEMMA_MODEL=MODEL,
        VISN_GEMMA_API_KEY='fixture-private-token',VISN_RESOURCE_DIR=str(output/'resources'),VISN_MANAGED_LMSTUDIO='true',
        VISN_DETECTOR_EXECUTABLE=str(ROOT/'.venv-retrieval/bin/python'),VISN_RESOURCE_WAIT_SECS='2')
    app=None;log=(output/'application.log').open('w');probe=Residency('test-probe',directory=output/'resources',timeout=.02)
    def until(predicate):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.05)
        raise AssertionError('Native integration fixture timed out')
    def call(path,body=None):
        request=urllib.request.Request('http://127.0.0.1:8220'+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
    def ready():
        assert app.poll() is None,'Application exited; inspect application.log'
        try:return call('/healthz')
        except OSError:return None
    request=dict(query='activity',job_id=JOB,semantic_enabled=False,long_memory_enabled=False,temporal_enabled=False,
                 vlm_enabled=True,vlm_model=MODEL)
    try:
        app=subprocess.Popen([str(ROOT/'target/debug/visn-phase0')],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        until(ready)
        result=call('/api/v1/memory-query',request)
        assert result['model']=='fixture-instance' and result['fallback_reason'] is None,result
        until(lambda:not (output/'resources/external.json').exists() and probe.snapshot()['owner'] is None)
        assert loaded==[]
        first=list(calls)
        assert any(c['path']=='/api/v1/models/unload' and c['body']=={'instance_id':'fixture-instance'} for c in first)
        assert all(c['authenticated'] for c in first)
        (output/'normal-query.json').write_text(json.dumps(result,indent=2)+'\n')
        probe.acquire();probe.release(lambda:None)
        # Completion acknowledgement must not be inferred from client EOF.
        finish_chat.clear();chat_started.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(call,'/api/v1/memory-query',request)
            assert chat_started.wait(10)
            app.kill();app.wait(timeout=10)
            until(lambda:probe.snapshot()['owner'] is None)
            marker=json.loads((output/'resources/external.json').read_text())
            assert marker['uncertain'] is True and loaded==['fixture-instance']
            try:probe.acquire()
            except ResourceBusy:pass
            else:raise AssertionError('Unacknowledged native work allowed another model owner')
            finish_chat.set()
            try:pending.result(timeout=5)
            except (OSError,TimeoutError):pass
        # Fixture server is now quiescent; explicitly reconcile its instances.
        recover=subprocess.run([str(ROOT/'.venv-retrieval/bin/python'),str(ROOT/'tools/workflow_resources.py'),'--recover-external'],
            cwd=ROOT,env=env,input=json.dumps(dict(endpoint=endpoint+'/api/v1',token='fixture-private-token'))+'\n',
            text=True,capture_output=True,timeout=10)
        assert recover.returncode==0,(recover.stdout,recover.stderr)
        assert not (output/'resources/external.json').exists() and loaded==[]
        probe.acquire();probe.release(lambda:None)
        (output/'native-calls.json').write_text(json.dumps(calls,indent=2)+'\n')
        assert hashes=={name:digest(ROOT/name) for name in code_files},'Evaluated native lifecycle code changed during run'
        summary=dict(passed=True,actual_rust_and_guardian=True,native_http_fixture=True,real_model_quality=False,
            authenticated=True,official_instance_id_unload=True,completion_acknowledged=True,client_crash_quarantined=True,
            unacknowledged_no_unsafe_unload=True,explicit_quiescent_recovery=True)
        (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
    finally:
        finish_chat.set()
        if app is not None and app.poll() is None:app.kill();app.wait(timeout=10)
        log.close();server.shutdown();server.server_close();serving.join(timeout=5)


if __name__=='__main__':main()
