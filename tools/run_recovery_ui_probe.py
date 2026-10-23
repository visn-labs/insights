#!/usr/bin/env python3
"""Owned UI fixture service. Touch OUTPUT/done to stop; no camera capture occurs.

Real cancel/resume/submission routes run against the normal Rust service, with
the capture-stage fault fixture stopping before its source is opened.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.request
import uuid

from check_recording_recovery import ROOT,Service


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=600)
    args=parser.parse_args()
    if not 1<=args.seconds<=3600:parser.error('seconds must be 1..3600')
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,
        directory=str(ROOT/'output/depin-implementation/bp03-fixtures')))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    service=Service(output/'data',output)
    service.overrides['VISN_RECOVERY_PROBE_PHASE']='before_capture'
    try:
        service.start()
        payload=dict(name='Recording recovery controls',idempotency_key=uuid.uuid4().hex,
            cameras=[dict(camera_id='ui-camera',live_url=f'http://127.0.0.1:{server.server_port}/face-sequence.mkv')],
            monitor_duration_secs=2,observer_fps=2,vlm_enabled=False)
        request=urllib.request.Request(service.base+'/api/v1/memory-jobs',data=json.dumps(payload).encode(),
                                       headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=30) as response:job=json.load(response)
        (output/'ui-info.json').write_text(json.dumps(dict(base=service.base,job_id=job['id'])))
        print(service.base,job['id'],flush=True)
        deadline=time.monotonic()+args.seconds
        while time.monotonic()<deadline and not (output/'done').exists():time.sleep(.2)
    finally:
        service.stop();server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':main()
