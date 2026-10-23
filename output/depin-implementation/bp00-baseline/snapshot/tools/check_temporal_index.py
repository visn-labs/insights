#!/usr/bin/env python3
"""End-to-end opt-in indexing using a retained highway source served over HTTP."""
import functools,json,threading,time,urllib.request
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
root=Path('data/highway-validation/memory/01a138f2-0df1-72b0-b49d-88451f598886')
source=next(root.rglob('source.mkv')).resolve()
server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(SimpleHTTPRequestHandler,directory=str(source.parent)))
threading.Thread(target=server.serve_forever,daemon=True).start()
def request(path,body=None):
    return json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8197'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=600))
try:
    job=request('/api/v1/memory-jobs',dict(name='Experiment 2 retained highway indexing',cluster_id='experiment2-validation',cameras=[dict(liveurl=f'http://127.0.0.1:{server.server_port}/source.mkv',camera_id='temporal-highway')],monitor_duration_secs=6,observer_fps=1,temporal_enabled=True,vlm_enabled=False))
    deadline=time.monotonic()+600
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(2);job=request('/api/v1/memory-jobs/'+job['id'])
    assert job['status']=='completed',job.get('error',job['status'])
    events=[e for c in job['result']['camera_results'] for e in c['events']]
    assert events and any(e.get('interpretation') for e in events),[(e['description'].get('fallback_reason')) for e in events]
    for e in events:
        if e.get('interpretation'):assert len(e['interpretation']['frames'])==8
    Path('output/experiment2/index-job.json').write_text(json.dumps(job,indent=2))
    print(json.dumps(dict(job_id=job['id'],events=len(events),interpreted=sum(bool(e.get('interpretation')) for e in events))))
finally: server.shutdown();server.server_close()
