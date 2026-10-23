#!/usr/bin/env python3
"""Capture bounded evaluation recordings through the actual Rust memory workflow."""
import argparse,concurrent.futures,json,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8192');p.add_argument('--seconds',type=int,default=45);p.add_argument('--output',default='output/live-evaluation-2026-10-16');p.add_argument('streams',nargs='+');args=p.parse_args()
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    def log(text):print(datetime.now(timezone.utc).isoformat(timespec='seconds'),text,flush=True)
    def request(path,body=None):
        return json.load(urllib.request.urlopen(urllib.request.Request(args.url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=20))
    def capture(item):
        index,url=item;camera=f'live-{index+1}';started=time.monotonic()
        body=dict(name=f'Live evaluation {camera}',cluster_id='live-evaluation',cameras=[dict(camera_id=camera,liveurl=url)],monitor_duration_secs=args.seconds,observer_fps=2,long_memory_enabled=True,vlm_enabled=False,temporal_enabled=False)
        log(f'{camera}: submitting {args.seconds}s recording')
        try:
            job=request('/api/v1/memory-jobs',body);log(f'{camera}: job {job["id"]}')
            last=None;deadline=time.monotonic()+args.seconds+300
            while time.monotonic()<deadline:
                job=request('/api/v1/memory-jobs/'+job['id'])
                if job['status']!=last:log(f'{camera}: {job["status"]}');last=job['status']
                (root/f'{camera}-job.json').write_text(json.dumps(job,indent=2))
                if job['status'] in ('completed','failed'):break
                time.sleep(2)
            if job['status'] not in ('completed','failed'):raise TimeoutError('Evaluation recording deadline exceeded; inspect the saved job ID')
            return dict(camera=camera,url=url,job_id=job['id'],status=job['status'],elapsed_secs=round(time.monotonic()-started,3),error=job.get('error'))
        except Exception as error:return dict(camera=camera,url=url,status='evaluation_error',error=str(error),elapsed_secs=round(time.monotonic()-started,3))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(capture,enumerate(args.streams)))
    (root/'capture-results.json').write_text(json.dumps(results,indent=2));log(json.dumps(results))
if __name__=='__main__':main()
