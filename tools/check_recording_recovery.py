#!/usr/bin/env python3
"""Real sanitized capture, hard-crash/cancel recovery and single-writer replay.

Uses an owned loopback source/service and a fixture-only pause after real capture.
It never kills a user service or recaptures a source during resume.
"""
import argparse
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def alive(pid):
    try:
        state=subprocess.check_output(['ps','-o','stat=','-p',str(pid)],text=True).strip()
        return bool(state) and not state.startswith('Z')
    except subprocess.CalledProcessError:
        return False


def wait_until(predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=predicate()
        if result:return result
        time.sleep(.05)
    raise TimeoutError('expected state did not become true')


class Service:
    def __init__(self,data,output):
        self.data,self.output,self.process,self.base=data,output,None,None
        self.attempt=0
        self.overrides={}

    def environment(self):
        environment=os.environ.copy()
        environment.update(VISN_BIND='127.0.0.1:0',VISN_DATA_DIR=str(self.data),
            VISN_MEDIA_POLICY='sanitized-v1',VISN_PRIVACY_PYTHON=str(ROOT/'.venv-retrieval/bin/python'),
            VISN_PRIVACY_MODEL_DIR=str(ROOT/'.retrieval/privacy-models'),
            VISN_DETECTOR_EXECUTABLE=str(ROOT/'.venv-retrieval/bin/python'),
            VISN_MEMORY_RUNNER_ARGS='tools/fixtures/recovery_memory_runner.py',
            VISN_GEMMA_BASE_URL='http://127.0.0.1:9/v1',VISN_MAX_EPHEMERAL_JOBS='2')
        environment.pop('VISN_RETRIEVAL_URL',None)
        environment.update(self.overrides)
        return environment

    def start(self):
        self.attempt+=1
        self.log=self.output/f'server-{self.attempt}.log'
        with self.log.open('wb') as stream:
            self.process=subprocess.Popen([str(ROOT/'target/debug/visn-phase0')],cwd=ROOT,
                env=self.environment(),stdout=stream,stderr=subprocess.STDOUT)

        def ready():
            if self.process.poll() is not None:
                raise RuntimeError(self.log.read_text())
            for line in self.log.read_text().splitlines():
                try:record=json.loads(line)
                except ValueError:continue
                address=record.get('fields',{}).get('address')
                if address:
                    self.base='http://'+address
                    return True
        wait_until(ready)

    def stop(self,hard=False):
        if self.process and self.process.poll() is None:
            if hard:self.process.kill()
            else:self.process.terminate()
            try:self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fixture',type=Path,default=ROOT/'output/depin-implementation/bp03-fixtures/face-sequence.mkv')
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    data=output/'data';observations=[];hits=[];groups=[]
    source_hash=hashlib.sha256(args.fixture.read_bytes()).hexdigest()

    class Source(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            hits.append(dict(path=self.path,time_ns=time.time_ns()))
            super().do_GET()

    source_server=ThreadingHTTPServer(('127.0.0.1',0),partial(Source,directory=str(args.fixture.parent)))
    thread=threading.Thread(target=source_server.serve_forever,daemon=True);thread.start()
    service=Service(data,output)

    def request(route,payload=None,expected=200):
        req=urllib.request.Request(service.base+route,data=None if payload is None else json.dumps(payload).encode(),
                                   headers={'content-type':'application/json'})
        try:response=urllib.request.urlopen(req,timeout=30)
        except urllib.error.HTTPError as error:response=error
        with response:status=response.status;body=json.load(response)
        observations.append(dict(route=route,status=status,response=body))
        assert status==expected,(route,status,body)
        return body

    def submit(name,key):
        payload=dict(name=name,idempotency_key=key,cameras=[dict(camera_id='camera-a',
            live_url=f'http://127.0.0.1:{source_server.server_port}/{args.fixture.name}')],
            monitor_duration_secs=2,observer_fps=2,vlm_enabled=False)
        job=request('/api/v1/memory-jobs',payload,202)
        assert request('/api/v1/memory-jobs',payload,202)['id']==job['id']
        request('/api/v1/memory-jobs',dict(payload,name='changed intent'),409)
        return job,payload

    def seal(job):
        directory=data/'memory'/job['id']/'camera-a'
        probe=wait_until(lambda:json.loads((directory/'recovery-probe.json').read_text())
                         if (directory/'recovery-probe.json').is_file() else None)
        assert os.getpgid(probe['worker_pid'])==probe['worker_pid']
        assert os.getpgid(probe['descendant_pid'])==probe['worker_pid']
        groups.append(probe)
        return directory,probe

    def await_dead(probe):
        wait_until(lambda:not alive(probe['worker_pid']) and not alive(probe['descendant_pid']),10)

    def complete(id):
        def terminal():
            result=request('/api/v1/memory-jobs/'+id)
            return result if result['status'] in ('completed','failed','cancelled') else None
        return wait_until(terminal)

    try:
        service.start()
        print('Checking single writer, durable acknowledgements and idempotency',flush=True)
        with (output/'second-writer.log').open('wb') as stream:
            blocked=subprocess.run([str(ROOT/'target/debug/visn-phase0')],cwd=ROOT,env=service.environment(),
                stdout=stream,stderr=subprocess.STDOUT,timeout=10)
        assert blocked.returncode!=0 and 'data_directory_already_owned' in (output/'second-writer.log').read_text()
        job,payload=submit('crash after committed capture','crash-operation-0123456789')
        directory,probe=seal(job)
        media_hash=hashlib.sha256((directory/'source.mkv').read_bytes()).hexdigest()
        before=len(hits)
        print('Hard-killing only owned Rust parent; checking worker and descendant exit',flush=True)
        service.stop(hard=True)
        await_dead(probe)
        service.start()
        interrupted=request('/api/v1/memory-jobs/'+job['id'])
        assert interrupted['status']=='failed' and 'interrupted_by_restart' in interrupted['error']
        assert request('/api/v1/memory-jobs',payload,202)['id']==job['id']
        request('/api/v1/memory-jobs/'+job['id']+'/resume',{})
        restored=complete(job['id'])
        assert restored['status']=='completed',restored.get('error')
        assert len(hits)==before,'Resume reopened live source'
        assert hashlib.sha256((directory/'source.mkv').read_bytes()).hexdigest()==media_hash
        camera=restored['result']['camera_results'][0]
        assert restored['camera_checkpoints']['camera-a']
        ids=[event['event_id'] for event in camera['events']]
        assert len(ids)==len(set(ids))
        assert all(event['privacy'] for event in camera['events'])

        print('Checking cancellation and retained-only recovery',flush=True)
        cancelled,_=submit('cancel after committed capture','cancel-operation-0123456789')
        _,probe=seal(cancelled);before=len(hits)
        assert request('/api/v1/memory-jobs/'+cancelled['id']+'/cancel',{})['status']=='cancelled'
        await_dead(probe)
        request('/api/v1/memory-jobs/'+cancelled['id']+'/resume',{})
        assert complete(cancelled['id'])['status']=='completed'
        assert len(hits)==before

        print('Checking restart persistence of completed checkpoints',flush=True)
        service.stop();service.start()
        finished=request('/api/v1/memory-jobs/'+job['id'])
        assert [e['event_id'] for e in finished['result']['camera_results'][0]['events']]==ids
        assert request('/api/v1/memory-jobs/'+job['id']+'/resume',{})['status']=='completed'

        print('Checking anchored checkpoint reuse without any observation worker',flush=True)
        service.stop()
        job_path=data/'memory/manifests'/f'{job["id"]}.json'

        def interrupted_checkpoint():
            stored=json.loads(job_path.read_text())
            stored.update(status='running',result=None,completed_at_ms=None)
            temporary=job_path.with_suffix('.pending')
            temporary.write_text(json.dumps(stored))
            temporary.replace(job_path)

        # Simulates the valid durable boundary after camera checkpoint, before final job publication.
        interrupted_checkpoint()
        service.overrides['VISN_MEMORY_RUNNER_ARGS']='tools/fixtures/does-not-exist.py'
        service.start();before=len(hits)
        request('/api/v1/memory-jobs/'+job['id']+'/resume',{})
        reused=complete(job['id'])
        assert reused['status']=='completed',reused.get('error')
        assert [e['event_id'] for e in reused['result']['camera_results'][0]['events']]==ids
        assert len(hits)==before

        print('Checking modified checkpoint metadata is rejected, then recoverable after restoration',flush=True)
        service.stop();interrupted_checkpoint()
        checkpoint=directory/'camera-result.json';saved=checkpoint.read_bytes()
        try:
            checkpoint.write_bytes(saved+b' ')
            service.start()
            request('/api/v1/memory-jobs/'+job['id']+'/resume',{})
            rejected=complete(job['id'])
            assert rejected['status']=='failed' and 'camera_checkpoint_changed' in rejected['error']
        finally:
            checkpoint.write_bytes(saved)
        request('/api/v1/memory-jobs/'+job['id']+'/resume',{})
        assert complete(job['id'])['status']=='completed'
        assert len(hits)==before

        print('Checking cancellation before capture cannot be resumed as a new live interval',flush=True)
        service.stop()
        service.overrides['VISN_MEMORY_RUNNER_ARGS']='tools/fixtures/recovery_memory_runner.py'
        service.overrides['VISN_RECOVERY_PROBE_PHASE']='before_capture'
        service.start()
        before=len(hits)
        empty,_=submit('cancel before capture','before-capture-operation-0123456789')
        empty_directory,probe=seal(empty)
        assert probe['phase']=='before_capture' and not (empty_directory/'source.mkv').exists()
        request('/api/v1/memory-jobs/'+empty['id']+'/cancel',{})
        await_dead(probe)
        request('/api/v1/memory-jobs/'+empty['id']+'/resume',{},400)
        assert len(hits)==before
        assert hashlib.sha256(args.fixture.read_bytes()).hexdigest()==source_hash
        (output/'results.json').write_text(json.dumps(dict(scope='local real-capture crash/cancel recovery',
            source_hits=hits,worker_groups=groups,requests=observations),indent=2)+'\n')
        print(f'Passed {len(observations)} HTTP checks plus crash/ownership/child checks: {output}',flush=True)
    finally:
        service.stop()
        for probe in groups:
            if alive(probe['worker_pid']) and os.getpgid(probe['worker_pid'])==probe['worker_pid']:
                os.killpg(probe['worker_pid'],signal.SIGKILL)
        source_server.shutdown();source_server.server_close();thread.join(timeout=5)


if __name__=='__main__':main()
