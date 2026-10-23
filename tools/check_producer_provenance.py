#!/usr/bin/env python3
"""Real sanitizer producer-provenance replay; no generic accuracy qualification."""
import argparse
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

from check_recording_recovery import ROOT, Service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT/'output/depin-implementation/bp03-fixtures/face-sequence.mkv')
    parser.add_argument('--ui-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.ui_seconds <= 1800: parser.error('ui-seconds must be 0..1800')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve(strict=True)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    hits = []
    class Source(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self): hits.append(self.path); super().do_GET()
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Source, directory=str(source.parent)))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    service = Service(output/'data', output)
    service.overrides['VISN_MEMORY_RUNNER_ARGS'] = 'tools/event_memory_runner.py'
    observations = []
    started = time.monotonic()
    def phase(message): print(f'[{time.monotonic()-started:.1f}s] {message}', flush=True)
    def request(route, body=None, expected=200):
        req = urllib.request.Request(service.base+route, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type':'application/json'})
        try: response = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as error: response = error
        with response:
            raw = response.read()
            value = json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            assert response.status == expected, (route, response.status, value)
        observations.append(dict(route=route, status=response.status))
        return value
    def complete():
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            job = request(route)
            assert job['status'] != 'failed', job.get('error')
            if job['status'] == 'completed' and (job.get('metadata_projection') or {}).get('state') == 'completed':
                return job
            time.sleep(.1)
        raise TimeoutError('owned producer-provenance job did not complete')
    try:
        service.start(); phase('Capture real sanitized evidence and retain the full producer recipe')
        job = request('/api/v1/memory-jobs', dict(name='Producer profile replay',
            cameras=[dict(camera_id='profile-camera', live_url=f'http://127.0.0.1:{server.server_port}/{source.name}')],
            monitor_duration_secs=2, observer_fps=2, long_memory_enabled=False, vlm_enabled=False), 202)
        route = f"/api/v1/memory-jobs/{job['id']}"
        job = complete()
        camera = job['result']['camera_results'][0]; event = camera['events'][0]
        root = service.data/'memory'/job['id']/'profile-camera'
        marker = root/'source.mkv.privacy.json'; original = marker.read_bytes()
        producer = json.loads(original)
        assert event['privacy']['source']['models'] == producer['models']
        assert event['privacy']['source']['runtime'] == producer['runtime']
        assert event['privacy']['source']['sanitizer_code_sha256'] == producer['sanitizer_code_sha256']
        query = dict(query='profile-camera', job_id=job['id'], semantic_enabled=False, rerank_enabled=False)
        paths = [f"/api/v1/memory-events/{event['event_id']}/{kind}" for kind in ('source','clip','thumbnail')]
        def admitted():
            assert request('/api/v1/memory-query', query)['matches']
            request(route+'/analytics', {})
            return {path: request(path)['sha256'] for path in paths}
        hashes = admitted()
        if args.ui_seconds:
            (output/'ui-info.json').write_text(json.dumps(dict(base=service.base, job_id=job['id'])))
            phase('UI ready; touch OUTPUT/ui-done to continue')
            deadline = time.monotonic()+args.ui_seconds
            while time.monotonic() < deadline and not (output/'ui-done').exists(): time.sleep(.2)
        mutations = {
            'model': lambda v: v['models']['yunet'].update(sha256='e'*64),
            'code': lambda v: v.update(sanitizer_code_sha256='e'*64),
            'tracker': lambda v: v.update(tracker_code_sha256='e'*64),
            'threshold': lambda v: v.update(detector_threshold=.2),
            'runtime': lambda v: v['runtime'].update(opencv='different-version'),
            'future-field': lambda v: v.update(future_producer_field={'version':'different'}),
            'missing-profile': lambda v: v.pop('sanitizer_code_sha256'),
        }
        phase('Reject changed model/code/threshold/runtime and unknown fields at every evidence boundary')
        for name, mutate in mutations.items():
            changed = json.loads(original); mutate(changed)
            marker.write_text(json.dumps(changed))
            for path in paths: request(path, expected=404)
            for endpoint, body in [('/api/v1/memory-query', query), (route+'/analytics', {}),
                                   (route+'/metadata/rebuild', {}), (route+'/visual-index', {}), (route+'/long-memory', {})]:
                denied = request(endpoint, body, 400)
                if endpoint.endswith('/long-memory'):
                    assert 'camera_privacy_source_changed' in denied['error'] or 'privacy_producer_profile_missing_or_invalid' in denied['error'], denied
            marker.write_bytes(original)
            assert admitted() == hashes, name
        phase('Restart with inference unavailable: saved producer stays explicit and media remain valid')
        service.stop()
        service.overrides.update(VISN_PRIVACY_MODEL_DIR=str(output/'unavailable-models'),
            VISN_MEMORY_RUNNER_ARGS='tools/fixtures/does-not-exist.py')
        service.start()
        assert admitted() == hashes
        reloaded = request(route)['result']['camera_results'][0]['events'][0]['privacy']['source']
        assert reloaded == event['privacy']['source']
        phase('Reject changed producer metadata during anchored retained-only checkpoint recovery')
        service.stop()
        stored_path = service.data/'memory/manifests'/f"{job['id']}.json"
        stored = json.loads(stored_path.read_text())
        stored.update(status='running', result=None, completed_at_ms=None)
        stored_path.write_text(json.dumps(stored))
        changed = json.loads(original); changed['runtime']['opencv'] = 'changed-after-interruption'
        marker.write_text(json.dumps(changed))
        before = len(hits)
        service.start(); request(route+'/resume', {})
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            failed = request(route)
            if failed['status'] == 'failed': break
            time.sleep(.1)
        assert failed['status'] == 'failed' and 'camera_checkpoint_artifact_changed' in failed['error'], failed
        assert len(hits) == before
        marker.write_bytes(original)
        request(route+'/resume', {}); restored = complete()
        assert admitted() == hashes and len(hits) == before
        assert restored['result']['camera_results'][0]['events'][0]['privacy']['source'] == event['privacy']['source']
        request(route+'/revoke', {})
        for path in paths: request(path, expected=404)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        (output/'results.json').write_text(json.dumps(dict(scope=__doc__, job_id=job['id'],
            source_sha256=original_hash, producer=producer, mutations=list(mutations),
            source_requests=hits, source_requests_during_recovery=len(hits)-before,
            observations=observations), indent=2)+'\n')
        phase('Passed full producer provenance, restart, recovery and revocation replay')
    finally:
        service.stop(); server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == '__main__':
    main()
