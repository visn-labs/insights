#!/usr/bin/env python3
"""Owned immutable-recipe and retained-only rollback compatibility replay."""
import argparse
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import threading
import time
import urllib.error
import urllib.request

from check_recording_recovery import ROOT, Service, alive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    source = ROOT/'output/depin-implementation/bp03-fixtures/face-sequence.mkv'
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    models = output/'models'; models.mkdir()
    lock = json.loads((ROOT/'tools/privacy_models.lock.json').read_text())
    for descriptor in lock['models'].values():
        shutil.copyfile(ROOT/'.retrieval/privacy-models'/descriptor['file'], models/descriptor['file'])
    wrapper = output/'observer.py'
    original_wrapper = f'import runpy\nrunpy.run_path({str(ROOT/"tools/fixtures/profile_memory_runner.py")!r}, run_name="__main__")\n'
    wrapper.write_text(original_wrapper)
    recipe_args = str(wrapper)
    hits = []
    class Source(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self): hits.append(self.path); super().do_GET()
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Source, directory=str(source.parent)))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    service = Service(output/'data', output)
    service.overrides.update(VISN_MEMORY_RUNNER_ARGS=recipe_args, VISN_PRIVACY_MODEL_DIR=str(models))
    observations = []; started = time.monotonic()
    def phase(message): print(f'[{time.monotonic()-started:.1f}s] {message}', flush=True)
    def request(route, body=None, expected=200):
        req = urllib.request.Request(service.base+route, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type':'application/json'})
        try: response = urllib.request.urlopen(req, timeout=90)
        except urllib.error.HTTPError as error: response = error
        with response:
            raw = response.read()
            value = json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else dict(sha256=hashlib.sha256(raw).hexdigest())
            assert response.status == expected, (route, response.status, value)
        observations.append(dict(route=route, status=response.status, error=value.get('error')))
        return value
    def wait(predicate, timeout=120):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.1)
        raise TimeoutError('owned execution profile state not reached')
    def submit(key):
        return request('/api/v1/memory-jobs', dict(name=key,idempotency_key=key,
            cameras=[dict(camera_id='profile-camera',live_url=f'http://127.0.0.1:{server.server_port}/{source.name}')],
            monitor_duration_secs=2,observer_fps=2,long_memory_enabled=False,vlm_enabled=False),202)
    def paths(job):
        event=job['result']['camera_results'][0]['events'][0]
        return [f"/api/v1/memory-events/{event['event_id']}/{kind}" for kind in ('source','clip','thumbnail')]
    def completed(route):
        def status():
            value=request(route)
            assert value['status']!='failed',value.get('error')
            return value if value['status']=='completed' and (value.get('metadata_projection') or {}).get('state')=='completed' else None
        return wait(status)
    try:
        service.start(); phase('Pin recipe before acknowledgement; capture a real sanitized source then cancel')
        first=submit('execution-profile-first')
        assert first['execution_profile']['version']=='recording-execution-v1'
        profile=first['execution_profile']
        route=f"/api/v1/memory-jobs/{first['id']}"
        root=service.data/'memory'/first['id']/'profile-camera'
        marker=root/'profile-pause.json'; wait(lambda:marker.exists())
        paused=json.loads(marker.read_text()); request(route+'/cancel',{})
        wait(lambda:not alive(paused['pid']))
        assert not (root/'camera-result.json').exists()
        before=len(hits)
        phase('Reject settings/model drift without reopening the source; restore recipe and recover')
        service.stop(); service.overrides['VISN_MEMORY_RUNNER_ARGS']=recipe_args+' --maximum-event-seconds 10'
        service.start()
        denied=request(route+'/resume',{},409); assert 'execution_profile_changed' in denied['error']
        assert len(hits)==before
        service.stop(); service.overrides['VISN_MEMORY_RUNNER_ARGS']=recipe_args; service.start()
        model=models/lock['models']['yunet']['file']; original=model.read_bytes(); model.write_bytes(original+b'x')
        denied=request(route+'/resume',{},409); assert 'execution_privacy_model_changed' in denied['error']
        model.write_bytes(original)
        request(route+'/resume',{}); recovered=completed(route)
        assert recovered['execution_profile']==profile and len(hits)==before
        first_recovery_requests = len(hits)-before
        hashes={path:request(path)['sha256'] for path in paths(recovered)}
        phase('Publish an anchored checkpoint with active inference unavailable and a different recipe')
        service.stop()
        stored_path=service.data/'memory/manifests'/f"{first['id']}.json"
        stored=json.loads(stored_path.read_text()); stored.update(status='running',result=None,completed_at_ms=None)
        stored_path.write_text(json.dumps(stored))
        service.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/fixtures/does-not-exist.py',
            VISN_PRIVACY_MODEL_DIR=str(output/'missing-models'))
        service.start(); request(route+'/resume',{}); republished=completed(route)
        assert republished['execution_profile']==profile and len(hits)==before
        assert {path:request(path)['sha256'] for path in paths(republished)}==hashes
        phase('Keep saved sanitized policy when the active deployment switches to research mode')
        service.stop(); service.overrides['VISN_MEDIA_POLICY']='research-raw-v1'; service.start()
        assert {path:request(path)['sha256'] for path in paths(republished)}==hashes
        query=dict(query='profile-camera',job_id=first['id'],semantic_enabled=False,rerank_enabled=False)
        assert request('/api/v1/memory-query',query)['matches']
        request(route+'/analytics',{})
        source_marker=root/'source.mkv.privacy.json'; source_bytes=source_marker.read_bytes()
        altered=json.loads(source_bytes);altered['detector_threshold']=.2;source_marker.write_text(json.dumps(altered))
        for path in paths(republished):request(path,expected=404)
        for endpoint,body in [('/api/v1/memory-query',query),(route+'/analytics',{}),
            (route+'/visual-index',{}),(route+'/long-memory',{})]:request(endpoint,body,400)
        source_marker.write_bytes(source_bytes)
        assert request('/api/v1/memory-query',query)['matches']
        request(route+'/analytics',{})
        phase('Detect in-place script drift before a checkpoint can be anchored')
        service.stop();service.overrides.update(VISN_MEDIA_POLICY='sanitized-v1',VISN_MEMORY_RUNNER_ARGS=recipe_args,VISN_PRIVACY_MODEL_DIR=str(models));service.start()
        second=submit('execution-profile-drift'); second_route=f"/api/v1/memory-jobs/{second['id']}"
        second_root=service.data/'memory'/second['id']/'profile-camera'
        wait(lambda:(second_root/'profile-pause.json').exists())
        wrapper.write_text(original_wrapper+'# changed after admission\n')
        (second_root/'profile-release').touch()
        def failed():
            value=request(second_route)
            return value if value['status']=='failed' else None
        rejected=wait(failed)
        assert 'execution_profile_changed' in rejected['error'] and not rejected['camera_checkpoints'],rejected
        wrapper.write_text(original_wrapper); count=len(hits)
        request(second_route+'/resume',{}); restored=completed(second_route)
        assert restored['execution_profile']==second['execution_profile'] and len(hits)==count
        for job in [republished,restored]:
            request(f"/api/v1/memory-jobs/{job['id']}/revoke",{})
            for path in paths(job):request(path,expected=404)
        assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
        (output/'results.json').write_text(json.dumps(dict(scope=__doc__,source_sha256=source_hash,
            profile=profile,drift_failure=rejected['error'],source_requests=hits,
            source_requests_during_first_recovery=first_recovery_requests,
            observations=observations),indent=2)+'\n')
        phase('Passed recipe admission, drift rejection, restored execution, read-only compatibility and revocation')
    finally:
        wrapper.write_text(original_wrapper)
        service.stop();server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':main()
