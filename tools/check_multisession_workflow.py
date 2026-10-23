#!/usr/bin/env python3
"""Owned real clock-reset API replay; integration evidence, not tracking accuracy."""
import argparse
from fractions import Fraction
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.request
import urllib.error

import av
from check_recording_recovery import ROOT, Service, alive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ui-seconds', type=int, default=0)
    parser.add_argument('--prefix-recovery', action='store_true')
    parser.add_argument('--transport-reconnect', action='store_true')
    parser.add_argument('--derived-profiles', action='store_true')
    parser.add_argument('--ui-phase', choices=('all','prefix'), default='all')
    args = parser.parse_args()
    if args.derived_profiles and args.prefix_recovery:
        parser.error('run derived-profile and prefix fault scenarios separately')
    if args.transport_reconnect and args.prefix_recovery:
        parser.error('run transport and committed-prefix fault scenarios separately')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    def sha(path):
        with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()
    source = args.source.resolve(strict=True); original_hash = sha(source)
    print('Create genuine reset fixture from retained development footage', flush=True)
    images = []
    first_time = None
    with av.open(str(source)) as container:
        for frame in container.decode(video=0):
            exact_time = frame.pts*frame.time_base
            if first_time is None:first_time=exact_time
            relative=exact_time-first_time
            if relative>=4:break
            assert (relative*25).denominator==1,'fixture MPEG2 clock must preserve input PTS exactly'
            images.append((frame.to_ndarray(format='bgr24'),int(relative*25)))
    assert len(images)>=10
    parts = []
    for part in range(2):
        path = output/f'part-{part}.ts'
        with av.open(str(path), 'w', format='mpegts') as container:
            stream = container.add_stream('mpeg2video', rate=25)
            stream.width, stream.height = images[0][0].shape[1], images[0][0].shape[0]
            stream.pix_fmt = 'yuv420p'; stream.codec_context.max_b_frames = 0
            stream.codec_context.gop_size = 1
            for repeat in range(5 if args.transport_reconnect and part else 1):
                for image, timestamp in images:
                    frame = av.VideoFrame.from_ndarray(image, format='bgr24')
                    frame.pts, frame.time_base = timestamp + repeat*100, Fraction(1, 25)
                    for packet in stream.encode(frame): container.mux(packet)
            for packet in stream.encode(): container.mux(packet)
        parts.append(path.read_bytes())
    fixture = output/'reset.ts'; fixture.write_bytes(b''.join(parts)); del images, parts
    hits = []
    class Source(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            hits.append(self.path)
            if args.transport_reconnect:
                self.path = '/part-0.ts' if len(hits) == 1 else '/part-1.ts'
            super().do_GET()
        def send_header(self, keyword, value):
            if args.transport_reconnect and len(hits) == 1 and keyword.lower() == 'content-length':
                value = str(int(value)*10)  # Simulate an actual interrupted HTTP body.
            super().send_header(keyword, value)
        def copyfile(self, source, destination):
            try: super().copyfile(source, destination)
            except (BrokenPipeError, ConnectionResetError): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Source, directory=str(output)))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    service = Service(output/'data', output)
    service.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/event_memory_runner.py --maximum-event-seconds 4',
        VISN_DETECTOR_EXECUTABLE=str(ROOT/'.venv/bin/python'), VISN_YOLO_MODEL=str(ROOT/'yolo26s.pt'))
    if args.transport_reconnect:
        service.overrides['VISN_MEMORY_RUNNER_ARGS'] = 'tools/event_memory_runner.py --maximum-event-seconds 30 --reconnect-attempts 1'
    worker_wrapper = output/'worker.py'
    runner_wrapper = output/'detector.py'
    worker_program = f"import runpy, sys\nsys.path.insert(0, {str(ROOT/'tools')!r})\nrunpy.run_path({str(ROOT/'tools/yolo26_worker.py')!r}, run_name='__main__')\n"
    runner_program = f"import runpy, time, sys\nfrom pathlib import Path\nsys.path.insert(0, {str(ROOT/'tools')!r})\nrunpy.run_path({str(ROOT/'tools/yolo26_runner.py')!r}, run_name='__main__')\nPath({str(output/'detector-paused')!r}).touch()\nwhile not Path({str(output/'detector-release')!r}).exists(): time.sleep(.05)\n"
    if args.derived_profiles:
        worker_wrapper.write_text(worker_program); runner_wrapper.write_text(runner_program)
        service.overrides.update(VISN_DETECTOR_WORKER_ARGS=str(worker_wrapper),VISN_DETECTOR_WORKER_IDLE_SECS='600')
    observations = []
    def hold_ui(phase, job):
        if not args.ui_seconds or args.ui_phase=='prefix' and phase!='prefix':return
        (output/'ui-info.json').write_text(json.dumps(dict(base=service.base,job_id=job['id'],phase=phase,
            source_url=f'http://127.0.0.1:{server.server_port}/reset.ts')))
        print(f'UI {phase}; touch OUTPUT/ui-{phase}-done to continue',flush=True)
        deadline=time.monotonic()+args.ui_seconds
        while time.monotonic()<deadline and not (output/f'ui-{phase}-done').exists():time.sleep(.2)
    def request(route, body=None, expected=200):
        req = urllib.request.Request(service.base+route, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type':'application/json'})
        try: response = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as error: response = error
        with response:
            raw = response.read()
            value = json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            assert response.status == expected, (route,response.status,value)
        observations.append(dict(route=route,status=response.status,response=value)); return value
    def wait(predicate, seconds=600):
        deadline = time.monotonic()+seconds; next_log=time.monotonic()+15
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            if time.monotonic()>=next_log:print('Waiting for current owned recording/projection',flush=True);next_log=time.monotonic()+15
            time.sleep(.25)
        raise TimeoutError('current owned job did not reach expected state')
    try:
        service.start()
        print('Capture and index both source-clock sessions through the API',flush=True)
        job=request('/api/v1/memory-jobs',dict(name='Real clock reset',capture_sessions=True,
            cameras=[dict(camera_id='junction',live_url=f'http://127.0.0.1:{server.server_port}/reset.ts')],
            monitor_duration_secs=12 if args.transport_reconnect else 15,observer_fps=2,long_memory_enabled=True,vlm_enabled=False),202)
        route='/api/v1/memory-jobs/'+job['id']
        def complete():
            value=request(route)
            assert value['status']!='failed',value.get('error')
            return value if value['status']=='completed' and value.get('metadata_projection',{}).get('state')=='completed' else None
        job=wait(complete)
        cameras=job['result']['camera_results'];assert len(cameras)==2
        assert job['result']['cameras_processed']==1
        sessions={camera['source_session_id'] for camera in cameras};assert len(sessions)==2
        assert all(camera['camera']['camera_id']=='junction' for camera in cameras)
        events=[event for camera in cameras for event in camera['events']]
        assert len(events)==len({event['event_id'] for event in events})==2
        assert events[0]['start_ms']==events[1]['start_ms']==0
        assert all(event['end_ms'] > 0 for event in events)
        if not args.transport_reconnect:
            assert events[0]['end_ms']==events[1]['end_ms']
        assert all(camera['long_memory']['entities'] for camera in cameras),[c['data_quality_notes'] for c in cameras]
        entity_sets=[{e['entity_id'] for e in camera['long_memory']['entities']} for camera in cameras]
        assert not entity_sets[0]&entity_sets[1]
        stats=request(route+'/analytics',{});assert len(stats['cameras'])==2 and stats['indexed_camera_ms']==sum(c['duration_ms'] for c in cameras)
        all_hits=request('/api/v1/memory-query',dict(query='junction',job_id=job['id'],semantic_enabled=False,rerank_enabled=False))
        assert len(all_hits['matches'])==2
        media_hashes={}
        for camera in cameras:
            session=camera['source_session_id']
            scoped=request(route+'/analytics',dict(camera_ids=['junction'],source_session_ids=[session]))
            assert len(scoped['cameras'])==1 and scoped['cameras'][0]['source_session_id']==session
            query=dict(query=camera['long_memory']['entities'][0]['class_name'],job_id=job['id'],
                source_session_ids=[session],semantic_enabled=False,rerank_enabled=False,long_memory_enabled=True)
            answer=request('/api/v1/memory-query',query)
            assert answer['long_memory']['entities'] and all(e['source_session_id']==session for e in answer['long_memory']['entities'])
            event=camera['events'][0]
            for kind in ('thumbnail','clip','source'):
                path=f"/api/v1/memory-events/{event['event_id']}/{kind}"
                media_hashes[path]=request(path)['sha256']
        root=service.data/'memory'/job['id']/'junction'
        transport_evidence = None
        if args.transport_reconnect:
            journal = json.loads((root/'capture/capture.json').read_text())
            assert len(hits) == 2
            assert journal['transport']['reconnect_attempts'] == 1
            assert journal['sessions'][0]['termination'] == 'transport_disconnected'
            assert journal['sessions'][1]['start_reason'] == 'transport_reconnected'
            transport_evidence = journal
        checkpoint=root/'camera-result.json';assert len(json.loads(checkpoint.read_text()))==2
        assert sha(checkpoint)==job['camera_checkpoints']['junction']
        sources={camera['storage_session_id']:sha(root/'sessions'/camera['storage_session_id']/'source.mkv') for camera in cameras}
        hold_ui('ready',job)
        print('Restart, verify session media, and reuse checkpoint without observer',flush=True)
        service.stop(hard=True);service.start()
        assert [c['source_session_id'] for c in request(route)['result']['camera_results']]==[c['source_session_id'] for c in cameras]
        for path, digest in media_hashes.items():assert request(path)['sha256']==digest
        service.stop()
        stored_path=service.data/'memory/manifests'/f"{job['id']}.json"
        stored=json.loads(stored_path.read_text());stored.update(status='running',result=None,completed_at_ms=None)
        stored_path.write_text(json.dumps(stored))
        producer_overrides = dict(service.overrides)
        service.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/fixtures/does-not-exist.py',
            VISN_DETECTOR_EXECUTABLE=str(output/'unavailable-detector'), VISN_YOLO_MODEL=str(output/'unavailable-model.pt'),
            VISN_PRIVACY_MODEL_DIR=str(output/'unavailable-privacy-models'), VISN_PERSISTENT_DETECTOR='false')
        before=len(hits);service.start();request(route+'/resume',{})
        recovered=wait(complete)
        assert [e['event_id'] for c in recovered['result']['camera_results'] for e in c['events']]==[e['event_id'] for e in events]
        assert len(hits)==before
        assert [c['long_memory'] for c in recovered['result']['camera_results']] == [c['long_memory'] for c in cameras]
        derived_evidence=None
        if args.derived_profiles:
            print('Build derived recipes independently; prove warm reuse and generation replacement',flush=True)
            original_recipe=recovered['execution_profile']
            service.stop();service.overrides=dict(producer_overrides)
            service.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/fixtures/does-not-exist.py',
                VISN_PRIVACY_MODEL_DIR=str(output/'unavailable-privacy-models'))
            service.start(); source_count=len(hits)
            def generations():
                return sum('persistent detector worker launched' in line for line in service.log.read_text().splitlines())
            def graphs(value):return [camera['long_memory'] for camera in value['result']['camera_results']]
            first=request(route+'/long-memory',{})
            assert first['execution_profile']==original_recipe
            assert all(graph['execution_profile']['version']=='tracker-enrichment-v1' for graph in graphs(first))
            first_generations=generations();assert first_generations==1
            same=request(route+'/long-memory',{});assert generations()==first_generations
            assert [g['execution_profile'] for g in graphs(first)]==[g['execution_profile'] for g in graphs(same)]
            worker_wrapper.write_text(worker_program+'# new declared detector recipe\n')
            replacement=request(route+'/long-memory',{});assert generations()==first_generations+1
            assert replacement['execution_profile']==original_recipe
            assert all(left['execution_profile']['fingerprint']!=right['execution_profile']['fingerprint'] for left,right in zip(graphs(first),graphs(replacement)))
            warm=request(route+'/long-memory',{});assert generations()==first_generations+1
            assert 'using isolated runner fallback' not in service.log.read_text()
            assert sum('persistent detector worker ready' in line for line in service.log.read_text().splitlines())==first_generations+1
            stable=wait(complete)
            saved_graphs=graphs(stable)
            print('Reject an actual in-flight derived recipe change without replacing saved graphs',flush=True)
            service.stop();service.overrides.update(VISN_PERSISTENT_DETECTOR='false',VISN_DETECTOR_ARGS=str(runner_wrapper));service.start()
            pending={}
            def rebuild():
                try:pending['response']=request(route+'/long-memory',{},409)
                except BaseException as error:pending['error']=repr(error)
            invocation=threading.Thread(target=rebuild);invocation.start()
            wait(lambda:(output/'detector-paused').exists())
            runner_wrapper.write_text(runner_program+'# changed while inference was pending\n')
            (output/'detector-release').touch();invocation.join(timeout=60)
            assert not invocation.is_alive() and not pending.get('error'),pending
            assert 'execution_profile_changed' in pending['response']['error'],pending
            after=request(route)
            assert graphs(after)==saved_graphs and after['execution_profile']==original_recipe
            assert len(hits)==source_count
            for path,digest in media_hashes.items():assert request(path)['sha256']==digest
            derived_evidence=dict(original_recording_recipe=original_recipe,
                initial_stage_profiles=[g['execution_profile'] for g in graphs(first)],
                replacement_stage_profiles=[g['execution_profile'] for g in saved_graphs],
                resident_generations_before=first_generations,resident_generations_after=first_generations+1,
                rejected_rebuild=pending['response'],source_requests_during_enrichment=len(hits)-source_count)
        prefix_evidence=None
        if args.prefix_recovery:
            print('Cancel after a committed session, before any camera checkpoint',flush=True)
            service.stop()
            service.overrides = dict(producer_overrides)
            service.overrides['VISN_MEMORY_RUNNER_ARGS']='tools/fixtures/paused_multisession_runner.py --maximum-event-seconds 4'
            service.start()
            prefix=request('/api/v1/memory-jobs',dict(name='Committed-prefix recovery',capture_sessions=True,
                cameras=[dict(camera_id='prefix-junction',live_url=f'http://127.0.0.1:{server.server_port}/reset.ts')],
                monitor_duration_secs=15,observer_fps=2,long_memory_enabled=True,vlm_enabled=False),202)
            prefix_route='/api/v1/memory-jobs/'+prefix['id']
            prefix_root=service.data/'memory'/prefix['id']/'prefix-junction'
            barrier=prefix_root/'prefix-pause.json'
            paused=wait(lambda:json.loads(barrier.read_text()) if barrier.exists() else None,90)
            assert paused['sessions']==1 and paused['state']=='capturing'
            from privacy_media import verify_capture_sessions
            journal=verify_capture_sessions(prefix_root/'capture',allow_partial=True)
            retained=prefix_root/'capture'/journal['sessions'][0]['file']
            retained_hash=sha(retained)
            assert not request(prefix_route)['camera_checkpoints']
            assert request(prefix_route+'/cancel',{})['status']=='cancelled'
            wait(lambda:not alive(paused['pid']),20)
            assert sha(retained)==retained_hash
            service.stop()
            service.overrides['VISN_MEMORY_RUNNER_ARGS']='tools/event_memory_runner.py --maximum-event-seconds 4'
            service.start();before_prefix=len(hits)
            request(prefix_route+'/resume',{})
            def prefix_complete():
                current=request(prefix_route)
                assert current['status']!='failed',current.get('error')
                return current if current['status']=='completed' and current.get('metadata_projection',{}).get('state')=='completed' else None
            recovered_prefix=wait(prefix_complete)
            assert len(hits)==before_prefix
            members=recovered_prefix['result']['camera_results'];assert len(members)==1
            assert members[0]['capture_extent']=='retained_prefix'
            assert members[0]['source_session_id']==journal['sessions'][0]['session_id']
            assert all(event['capture_extent']=='retained_prefix' for event in members[0]['events'])
            prefix_stats=request(prefix_route+'/analytics',{})
            assert prefix_stats['cameras'][0]['capture_extent']=='retained_prefix'
            assert sha(prefix_root/'sessions'/members[0]['storage_session_id']/'source.mkv')==retained_hash
            assert any('Capture journal state: capturing' in note for note in members[0]['data_quality_notes'])
            prefix_answer=request('/api/v1/memory-query',dict(query='prefix-junction',job_id=prefix['id'],semantic_enabled=False,rerank_enabled=False))
            assert prefix_answer['matches'] and all(m['event']['capture_extent']=='retained_prefix' for m in prefix_answer['matches'])
            hold_ui('prefix',recovered_prefix)
            prefix_evidence=dict(job_id=prefix['id'],paused=paused,statistics=prefix_stats,
                                 source_requests_after_resume=len(hits)-before_prefix,retained_media_sha256=retained_hash)
            request(prefix_route+'/revoke',{})
            for event in members[0]['events']:request(event['evidence_url'],expected=404)
            assert sha(retained)==retained_hash
        print('Revoke both sessions and preserve retained-media hashes',flush=True)
        revoked=request(route+'/revoke',{})
        expected_paths={str(root/'sessions'/session/'source.mkv') for session in sources}
        assert expected_paths.issubset(set(revoked['revocation']['source_paths'])),revoked['revocation']
        for path in media_hashes:request(path,expected=404)
        request(route+'/analytics',{},410)
        request('/api/v1/memory-query',dict(query='junction',job_id=job['id']),410)
        for session,digest in sources.items():assert sha(root/'sessions'/session/'source.mkv')==digest
        assert sha(source)==original_hash
        hold_ui('revoked',request(route))
        (output/'results.json').write_text(json.dumps(dict(scope=__doc__,source_sha256=original_hash,
            detector_model_sha256=sha(ROOT/'yolo26s.pt'),sessions=sorted(sessions),statistics=stats,
            prefix_recovery=prefix_evidence,transport_reconnect=transport_evidence,derived_profiles=derived_evidence,
            source_requests=hits,observations=observations),indent=2))
        print(f'Passed real multi-session capture/query/media/recovery/revocation: {output}',flush=True)
    finally:
        service.stop();server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':main()
