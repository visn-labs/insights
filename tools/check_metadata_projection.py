#!/usr/bin/env python3
"""Owned graph/interval projection replay. Optional real YOLO on retained development footage.

Checks metadata parity/recovery, not independent model accuracy or physical footfall.
Never edits the input master; all injected failures are inside a fresh output directory.
"""
import argparse
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

from check_recording_recovery import ROOT,Service


def file_hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def reference(camera,start=0,end=None):
    end=min(camera['duration_ms'],camera['duration_ms'] if end is None else end)
    selected=[e for e in camera['events'] if start<end and e['start_ms']<end and e['end_ms']>start]
    covered=set()
    for event in selected:covered.update(range(max(start,event['start_ms']),min(end,event['end_ms'])))
    graph=camera.get('long_memory')
    result=dict(camera_id=camera['camera']['camera_id'],indexed_events=len(selected),indexed_interval_ms=len(covered),
        observed_tracker_entities=None,detector_observations=None,tracker_observation_links=None)
    if camera.get('source_session_id'):result['source_session_id']=camera['source_session_id']
    if camera.get('capture_extent'):result['capture_extent']=camera['capture_extent']
    if graph and graph['version']=='entity-memory-v2-pts' and graph['status']=='ready':
        entities=set();points=set();detections=set();event_ids={e['event_id'] for e in selected}
        for entity in graph['entities']:
            for point in entity['observations']:
                if start<=point['timestamp_ms']<end and point['event_id'] in event_ids:
                    entities.add(entity['entity_id']);points.add(point['observation_id'])
                    if point['kind']=='detector_observation':detections.add(point['observation_id'])
        result.update(observed_tracker_entities=len(entities),detector_observations=len(detections),
            tracker_observation_links=sum(edge['entity_id'] in entities and edge['from'] in points and edge['to'] in points for edge in graph['links']))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=ROOT/'output/depin-implementation/bp03-fixtures/face-sequence.mkv')
    parser.add_argument('--seconds',type=int,default=2)
    parser.add_argument('--real-tracker',action='store_true')
    parser.add_argument('--ui-seconds',type=int,default=0)
    args=parser.parse_args()
    if not 1<=args.seconds<=30 or not 0<=args.ui_seconds<=600:parser.error('seconds 1..30; ui-seconds 0..600')
    source=args.source.resolve(strict=True);source_sha=file_hash(source)
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    observations=[];started=time.monotonic()
    def phase(message):print(f'[{time.monotonic()-started:.1f}s] {message}',flush=True)
    class Source(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
        def copyfile(self,source,outputfile):
            try:super().copyfile(source,outputfile)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Source,directory=str(source.parent)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    service=Service(output/'data',output)
    service.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/event_memory_runner.py --maximum-event-seconds 4',
        VISN_GEMMA_BASE_URL='http://127.0.0.1:9/v1')
    if args.real_tracker:
        service.overrides.update(VISN_DETECTOR_EXECUTABLE=str(ROOT/'.venv/bin/python'),VISN_YOLO_MODEL=str(ROOT/'yolo26s.pt'))

    def request(route,body=None,expected=200):
        req=urllib.request.Request(service.base+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        try:response=urllib.request.urlopen(req,timeout=40)
        except urllib.error.HTTPError as error:response=error
        with response:
            raw=response.read()
            value=json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else {'error':raw.decode(errors='replace')}
            assert response.status==expected,(route,response.status,value)
        observations.append(dict(route=route,status=response.status,response=value))
        return value

    def wait_for(predicate,seconds=180):
        deadline=time.monotonic()+seconds;next_log=time.monotonic()+15
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            if time.monotonic()>=next_log:phase('Still waiting for the current owned job/state');next_log=time.monotonic()+15
            time.sleep(.25)
        raise TimeoutError('Expected projection workflow state was not reached')

    def hold_ui(name,job):
        if not args.ui_seconds:return
        (output/'ui-info.json').write_text(json.dumps(dict(base=service.base,job_id=job['id'],phase=name)))
        phase(f'UI {name}; touch OUTPUT/ui-{name}-done to continue')
        deadline=time.monotonic()+args.ui_seconds
        while time.monotonic()<deadline and not (output/f'ui-{name}-done').exists():time.sleep(.2)

    try:
        service.start();phase('Capture and materialize real sanitized evidence metadata')
        job=request('/api/v1/memory-jobs',dict(name='Metadata projection replay',
            cameras=[dict(camera_id='evidence-camera',live_url=f'http://127.0.0.1:{server.server_port}/{source.name}')],
            monitor_duration_secs=args.seconds,observer_fps=2,long_memory_enabled=args.real_tracker,vlm_enabled=False),202)
        route='/api/v1/memory-jobs/'+job['id']
        def completed():
            current=request(route)
            assert current['status']!='failed',current
            metadata=current.get('metadata_projection') or {}
            assert metadata.get('state')!='blocked',metadata
            return current if current['status']=='completed' and metadata.get('state')=='completed' else None
        job=wait_for(completed,600)
        camera=job['result']['camera_results'][0]
        graph=camera.get('long_memory')
        if args.real_tracker:assert graph and graph['status']=='ready' and graph['entities'] and graph['links'],camera['data_quality_notes']
        analytics=request(route+'/analytics',{})
        assert analytics['cameras']==[reference(camera)]
        assert analytics['indexed_camera_ms']==analytics['cameras'][0]['indexed_interval_ms']
        edge=camera['events'][0]['end_ms']
        if edge<camera['duration_ms']:
            window=request(route+'/analytics',dict(start_ms=edge,end_ms=camera['duration_ms']))
            assert window['cameras']==[reference(camera,edge,camera['duration_ms'])]
        request(route+'/analytics',dict(camera_ids=['missing']),400)
        request(route+'/analytics',dict(end_ms=0),400)
        request(route+'/analytics',dict(metric='physical_footfall'),422)
        query=dict(query=graph['entities'][0]['class_name'] if graph and graph['entities'] else 'recorded activity',
            job_id=job['id'],long_memory_enabled=True,semantic_enabled=False,rerank_enabled=False)
        if graph and graph['entities']:query['entity_id']=graph['entities'][0]['entity_id']
        baseline=request('/api/v1/memory-query',query)
        assert baseline['metadata_provenance'][0]['mode']=='verified_projection',baseline
        if args.real_tracker:assert baseline['long_memory']['entities']
        if camera.get('source_session_id'):
            session=camera['source_session_id']
            assert all(event['source_session_id']==session for event in camera['events'])
            assert request(route+'/analytics',dict(source_session_ids=[session]))['cameras']==analytics['cameras']
            request(route+'/analytics',dict(source_session_ids=['missing']),400)
            scoped=request('/api/v1/memory-query',dict(query,source_session_ids=[session]))
            assert scoped['events_considered']>0 and all(match['event']['source_session_id']==session for match in scoped['matches'])
            if args.real_tracker:assert scoped['long_memory']['entities'] and all(entity['source_session_id']==session for entity in scoped['long_memory']['entities'])
            excluded=request('/api/v1/memory-query',dict(query,source_session_ids=['missing']))
            assert excluded['events_considered']==0 and not excluded['matches']
        hold_ui('ready',job)
        job=wait_for(completed,90)  # A browser may explicitly rebuild the same retained metadata.
        phase('Reject changed projection; retain exact canonical graph answer with explicit fallback')
        directory=service.data/'memory/projections'/job['id']
        artifact=directory/(job['metadata_projection']['revision']+'.json')
        capture=camera['events'][0].get('privacy',{}).get('source',{}).get('capture',{})
        if capture.get('association_sessions'):
            projected=list(json.loads(artifact.read_bytes())['cameras'].values())
            assert len(projected)==1 and projected[0]['capture_timeline']==capture
        saved=artifact.read_bytes();artifact.write_bytes(saved+b'changed')
        request(route+'/analytics',{},400)
        fallback=request('/api/v1/memory-query',query)
        assert fallback['metadata_provenance'][0]['mode']=='canonical_graph_fallback'
        assert fallback['long_memory']==baseline['long_memory']
        artifact.write_bytes(saved)
        phase('Interrupt projection publication, restart with durable pending intent, then repair storage')
        root=service.data/'memory/projections';held=output/'held-projections'
        root.rename(held);root.write_text('fixture blocking output directory')
        rebuilt=request(route+'/metadata/rebuild',{})
        revision=rebuilt['metadata_projection']['revision']
        def publication_failed():
            current=request(route);metadata=current['metadata_projection']
            return current if metadata['state']=='pending' and metadata['attempts']>0 and metadata['last_error'] else None
        wait_for(publication_failed,30)
        service.stop(hard=True);service.start()
        assert request(route)['metadata_projection']['revision']==revision
        request(route+'/analytics',{},409)
        root.unlink();held.rename(root)
        repaired=wait_for(completed,90)
        assert repaired['metadata_projection']['revision']==revision
        assert repaired['result']['camera_results'][0]['events'][0].get('privacy')==camera['events'][0].get('privacy')
        assert request(route+'/analytics',{})['cameras']==analytics['cameras']
        assert request('/api/v1/memory-query',query)['long_memory']==baseline['long_memory']
        phase('Reject changed sanitized source before aggregate or graph admission')
        media=service.data/'memory'/job['id']/'evidence-camera/source.mkv'
        size=media.stat().st_size;media_sha=file_hash(media)
        phase('Reject changed capture metadata even when encoded evidence is unchanged')
        marker=media.with_suffix(media.suffix+'.privacy.json')
        original_marker=marker.read_bytes()
        changed=json.loads(original_marker)
        changed['capture']['transport_finished_wall_ms']+=1
        marker.write_text(json.dumps(changed))
        try:
            request(route+'/analytics',{},400)
            request('/api/v1/memory-query',query,400)
        finally:
            marker.write_bytes(original_marker)
        assert file_hash(media)==media_sha
        with media.open('ab') as stream:stream.write(b'mutated')
        try:
            request(route+'/analytics',{},400)
            request('/api/v1/memory-query',query,400)
        finally:
            with media.open('r+b') as stream:stream.truncate(size)
        assert file_hash(media)==media_sha
        phase('Deny revoked projections; fail safely on cleanup symlink and recover after restart')
        directory.rename(output/'held-revoked-metadata')
        sentinel=output/'outside-projection';sentinel.mkdir();(sentinel/'keep.txt').write_text('must survive')
        directory.symlink_to(sentinel,target_is_directory=True)
        revoked=request(route+'/revoke',{});tombstone=revoked['revocation']['revision']
        request(route+'/analytics',{},410);request(route+'/metadata/rebuild',{},410)
        request('/api/v1/memory-query',query,410)
        def cleanup_blocked():
            current=request(route)
            return current if current['revocation']['local_cleanup_state']=='blocked' else None
        blocked=wait_for(cleanup_blocked,20)
        assert 'symlink' in blocked['revocation']['local_cleanup_error']
        service.stop(hard=True);service.start()
        assert request(route)['revocation']['local_cleanup_state']=='blocked'
        hold_ui('revoked',request(route))
        directory.unlink();(output/'held-revoked-metadata').rename(directory)
        assert request(route+'/revoke',{})['revocation']['revision']==tombstone
        def cleaned():
            current=request(route)
            return current if current['revocation']['local_cleanup_state']=='completed' else None
        final=wait_for(cleaned,20)
        assert not directory.exists() and (sentinel/'keep.txt').read_text()=='must survive'
        assert file_hash(source)==source_sha and file_hash(media)==media_sha
        (output/'results.json').write_text(json.dumps(dict(scope=__doc__,real_tracker=args.real_tracker,
            source_sha256=source_sha,detector_model_sha256=file_hash(ROOT/'yolo26s.pt') if args.real_tracker else None,
            analytics=analytics,baseline_answer=baseline,final=final,observations=observations),indent=2)+'\n')
        phase(f'Passed exact metadata, graph parity, restart, corruption and local revocation replay: {output}')
    finally:
        service.stop();server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':main()
