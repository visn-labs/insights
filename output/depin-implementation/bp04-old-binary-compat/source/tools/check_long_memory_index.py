#!/usr/bin/env python3
"""Exercise normal recording/indexing with experiment 3, or its detector-failure fallback."""
import argparse,functools,json,threading,time,urllib.request,urllib.error
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8195');parser.add_argument('--expect-detector-failure',action='store_true');args=parser.parse_args()
source=next(Path('data/highway-validation/memory/01a138f2-0df1-72b0-b49d-88451f598886').rglob('source.mkv')).resolve()
class Handler(SimpleHTTPRequestHandler):
    def copyfile(self,source,outputfile):
        try:super().copyfile(source,outputfile)
        except (BrokenPipeError,ConnectionResetError):pass # FFmpeg stops reading after bounded interval.
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source.parent)));threading.Thread(target=server.serve_forever,daemon=True).start()
def request(path,body=None,method=None):
    return json.load(urllib.request.urlopen(urllib.request.Request(args.url+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Content-Type':'application/json'}),timeout=600))
try:
    if args.expect_detector_failure:
        path='/api/v1/memory-jobs/01a138f2-0df1-72b0-b49d-88451f598886';before=request(path)
        try:request(path+'/long-memory',method='POST');raise AssertionError('Broken detector unexpectedly succeeded')
        except urllib.error.HTTPError as error:assert error.code>=400
        assert request(path)==before,'Failed rebuild must preserve previous evidence and entity graph'
    job=request('/api/v1/memory-jobs',dict(name='Experiment 3 recording test'+(' failure fallback' if args.expect_detector_failure else ''),cluster_id='experiment3-validation',cameras=[dict(liveurl=f'http://127.0.0.1:{server.server_port}/source.mkv',camera_id='entity-highway')],monitor_duration_secs=16,observer_fps=1,long_memory_enabled=True,vlm_enabled=False,temporal_enabled=False))
    deadline=time.monotonic()+600
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(1);job=request('/api/v1/memory-jobs/'+job['id'])
    assert job['status']=='completed',job
    camera=job['result']['camera_results'][0];assert camera['events']
    if args.expect_detector_failure:
        assert camera['long_memory'] is None;assert any('Experiment 3 entity memory unavailable' in n for n in camera['data_quality_notes'])
    else:
        graph=camera['long_memory'];assert graph['status']=='ready' and graph['entities'];assert all(o['event_id'] in {e['event_id'] for e in camera['events']} for entity in graph['entities'] for o in entity['observations'])
    output=Path('output/experiment3/index-'+('fallback' if args.expect_detector_failure else 'success')+'.json');output.write_text(json.dumps(job,indent=2))
    print(json.dumps(dict(job=job['id'],events=len(camera['events']),entity_memory=bool(camera['long_memory']),fallback_verified=args.expect_detector_failure)))
finally:server.shutdown();server.server_close()
