#!/usr/bin/env python3
"""Real-model/API privacy integration replay against an isolated sanitized service.

Requires cached public face-sequence.mkv fixture and its loopback HTTP server.
This is not an independent detector recall or general-video acceptance study.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

import av


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8232')
    parser.add_argument('--source-url', default='http://127.0.0.1:8233/face-sequence.mkv')
    parser.add_argument('--fixture', type=Path, default=Path('output/depin-implementation/bp03-fixtures/face-sequence.mkv'))
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    observations = []

    def request(route, payload=None, expected=200, *, raw=None, content_type=None):
        req = urllib.request.Request(args.base_url+route, data=raw if raw is not None else
            (None if payload is None else json.dumps(payload).encode()),
            headers={'Content-Type': content_type or 'application/json'})
        try:
            response = urllib.request.urlopen(req,timeout=120)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data, status = response.read(), response.status
            mime = response.headers.get('Content-Type','')
        body = json.loads(data) if 'application/json' in mime else None
        observations.append(dict(route=route,status=status,response=body,bytes=len(data),
                                 sha256=hashlib.sha256(data).hexdigest()))
        assert status == expected, (route,status,body)
        return body if body is not None else data

    def upload(data,expected=201):
        boundary = 'visn-privacy-contract'
        raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="video"; filename="fixture.mkv"\r\n'
               'Content-Type: video/x-matroska\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
        return request('/api/v1/uploads',expected=expected,raw=raw,
                       content_type=f'multipart/form-data; boundary={boundary}')

    def assert_h264(data):
        with av.open(io.BytesIO(data)) as video:
            assert len(video.streams)==1 and video.streams.video[0].codec_context.name=='h264'
            frames=list(video.decode(video=0))
            assert frames
            return len(frames)

    print('Checking strict profile and real-model upload',flush=True)
    assert request('/api/v1/capabilities')['media_policy']=='sanitized-v1'
    record = upload(args.fixture.read_bytes())
    assert record['privacy']['frames']==8
    assert_h264(request(f'/api/v1/uploads/{record["id"]}/content'))
    canonical = args.data_dir/'uploads'/f'{record["id"]}.mkv'
    manifest=json.loads(canonical.with_suffix('.mkv.privacy.json').read_text())
    assert manifest['detections']['face']>=8
    before=set((args.data_dir/'uploads').iterdir())
    upload(b'invalid encoded input',expected=400)
    assert before==set((args.data_dir/'uploads').iterdir()), 'Failure left upload artifacts'

    print('Checking HTTP recording and derived artifact serving',flush=True)
    job = request('/api/v1/memory-jobs',dict(name='privacy contract replay',
        cameras=[dict(camera_id='privacy-demo',live_url=args.source_url)],
        monitor_duration_secs=2,observer_fps=2,vlm_enabled=False),expected=202)
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        job=request('/api/v1/memory-jobs/'+job['id'])
        if job['status'] in ('completed','failed'):
            break
        time.sleep(.25)
    assert job['status']=='completed',job.get('error')
    camera=job['result']['camera_results'][0]
    event=camera['events'][0]
    assert event['privacy']['source']['frames']==8
    assert_h264(request(event['evidence_url']))
    request(event['thumbnail_url'])
    request(camera['evidence_url'])
    query=dict(query='recorded activity',job_id=job['id'],semantic_enabled=False,rerank_enabled=False)
    assert request('/api/v1/memory-query',query)['events_considered']>=1

    directory=args.data_dir/'memory'/job['id']/'privacy-demo'
    print('Checking changed evidence is rejected before serving or querying',flush=True)
    for path, route in [(directory/f'{event["event_id"]}.jpg',event['thumbnail_url']),
                        (directory/'source.mkv',camera['evidence_url'])]:
        saved=path.read_bytes()
        try:
            path.write_bytes(saved+b'mutation')
            request(route,expected=404)
            if path.name=='source.mkv':
                request('/api/v1/memory-query',query,expected=400)
        finally:
            path.write_bytes(saved)
        request(route)
    assert request('/api/v1/memory-query',query)['events_considered']>=1
    report=dict(scope='real detector and application integration only; acceptance unqualified',
                recording_id=job['id'],upload_id=record['id'],requests=observations)
    (args.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'Passed {len(observations)} API requests; {args.output}',flush=True)


if __name__=='__main__':
    main()
