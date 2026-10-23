#!/usr/bin/env python3
"""Compare frozen/current Rust APIs on retained evidence without modifying recordings."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
from urllib.parse import urlparse


def request(base, path, body=None):
    req=urllib.request.Request(base+path, data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)


def comparable(value):
    result={k:v for k,v in value.items() if k!='query_id'}
    if result.get('long_memory'):
        result['long_memory']=dict(result['long_memory'],links=sorted(result['long_memory']['links'],key=lambda e:(e['entity_id'],e['from'],e['to'],e['relation'],e['gap_ms'])))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',required=True);p.add_argument('--candidate',required=True);p.add_argument('--output',required=True)
    args=p.parse_args()
    for base in (args.baseline,args.candidate):
        parsed=urlparse(base)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password:
            raise ValueError('Use local test applications')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    jobs=request(args.baseline,'/api/v1/memory-jobs')
    if isinstance(jobs,dict):jobs=jobs['jobs']
    completed=[j for j in jobs if j.get('result',{}).get('camera_results')]
    assert len(completed)>=2,'Need at least two retained recordings'
    bodies=[dict(query='truck',semantic_enabled=False),dict(query='vehicles',semantic_enabled=False,long_memory_enabled=True)]
    for job in completed:
        cameras=job['result']['camera_results'];camera=cameras[0]
        graph=camera.get('long_memory')
        base=dict(query='Show vehicle observations',job_id=job['id'],semantic_enabled=False,vlm_enabled=False,limit=1,long_memory_enabled=True)
        bodies.append(base)
        for filters in (dict(camera_ids=['excluded']),dict(cluster_id='excluded'),dict(job_id='00000000-0000-0000-0000-000000000000'),dict(start_ms=4000,end_ms=10000),dict(start_ms=999999)):
            bodies.append(dict(base,**filters))
        if graph and graph['entities']:
            entity=max(graph['entities'],key=lambda e:len(e['observations']))
            bodies.extend([dict(base,entity_id=entity['entity_id']),dict(base,entity_id=entity['entity_id'],temporal_enabled=True),dict(base,entity_id='00000000-0000-0000-0000-000000000000',temporal_enabled=True)])
    results=[];evidence=set()
    for i,body in enumerate(bodies):
        a=request(args.baseline,'/api/v1/memory-query',body);b=request(args.candidate,'/api/v1/memory-query',body)
        (out/f'{i:02d}.json').write_text(json.dumps(dict(request=body,baseline=a,candidate=b),indent=2)+'\n')
        # Separate processes enumerate HashMap-backed camera graphs differently.
        # Compare every edge field; never normalize retrieval or observation order.
        assert comparable(a)==comparable(b),f'Case {i} differs'
        for e in b['matches']:evidence.add(e['event']['evidence_url'])
        if b.get('long_memory'):
            for e in b['long_memory']['supporting_events']:evidence.add(e['evidence_url'])
        results.append(dict(case=i,exact=True,mode=b['retrieval_mode'],events_considered=b['events_considered']))
    for path in sorted(evidence):
        hashes=[]
        for base in (args.baseline,args.candidate):
            with urllib.request.urlopen(base+path,timeout=60) as response:
                assert response.status==200 and response.headers['Content-Type'].startswith('video/')
                hashes.append(hashlib.file_digest(response,'sha256').hexdigest())
        assert hashes[0]==hashes[1],path
    summary=dict(cases=results,exact=True,evidence_files=len(evidence),normalized_fields=['query_id','long_memory.links enumeration order'],scope='Retained development API/evidence parity; no new inference or accuracy acceptance')
    (out/'results.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
