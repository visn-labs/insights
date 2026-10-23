#!/usr/bin/env python3
"""Real API regression for evidence-linked memory; requires a built highway recording.
Use --build to (re)run the real detector, and --restart-check after restarting Rust.
"""
import argparse,json,time,urllib.request,urllib.error
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8195');parser.add_argument('--job',default='01a138f2-0df1-72b0-b49d-88451f598886');parser.add_argument('--build',action='store_true');parser.add_argument('--restart-check',action='store_true');args=parser.parse_args()
    root=Path('output/experiment3');root.mkdir(parents=True,exist_ok=True)
    def request(path,body=None,method=None):
        return json.load(urllib.request.urlopen(urllib.request.Request(args.url+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Content-Type':'application/json'}),timeout=900))
    started=time.monotonic()
    job=request('/api/v1/memory-jobs/'+args.job+('/long-memory' if args.build else ''),method='POST' if args.build else 'GET')
    cameras=job['result']['camera_results'];entities=[e for c in cameras for e in c.get('long_memory',{}).get('entities',[])];assert entities
    graphs=[c['long_memory'] for c in cameras];assert all(g['status']=='ready' and len(g['source_sha256'])==64 for g in graphs)
    entity=max(entities,key=lambda e:len(set(o['event_id'] for o in e['observations'])))
    assert len(set(o['event_id'] for o in entity['observations']))>=2,'Need a track crossing indexed interval boundaries'
    body=dict(query=f"What observations were recorded for this {entity['class_name']} before and after?",job_id=args.job,entity_id=entity['entity_id'],long_memory_enabled=True,semantic_enabled=False,vlm_enabled=False,limit=1)
    result=request('/api/v1/memory-query',body);answer=result['long_memory'];assert answer['status']=='tracker_linked';assert len(answer['entities'])==1
    assert len(answer['supporting_events'])>=2;assert len(result['matches'])<=1
    evidence={e['event_id']:e for e in answer['supporting_events']}
    for observation in answer['entities'][0]['observations']:
        e=evidence[observation['event_id']];assert e['job_id']==args.job and e['camera_id']==entity['camera_id'];assert e['start_ms']<=observation['timestamp_ms']<e['end_ms']
    owners={o['observation_id']:e['entity_id'] for e in answer['entities'] for o in e['observations']}
    assert all(owners[l['from']]==owners[l['to']]==l['entity_id'] for l in answer['links'])
    for e in evidence.values():
        with urllib.request.urlopen(args.url+e['evidence_url']) as response:assert response.status==200 and response.headers['Content-Type'].startswith('video/')
    ambiguous=request('/api/v1/memory-query',dict(body,entity_id=None,query='Show vehicles across the recording'))['long_memory'];assert ambiguous['status']=='ambiguous_entities';assert len({e['entity_id'] for e in ambiguous['entities']})==len(ambiguous['entities'])
    for filters in [dict(camera_ids=['unrelated']),dict(job_id='00000000-0000-0000-0000-000000000001'),dict(cluster_id='unrelated'),dict(start_ms=999999)]:
        missing=request('/api/v1/memory-query',dict(body,**filters))['long_memory'];assert missing['status']=='insufficient_evidence';assert not missing['supporting_events'];assert not missing['links']
    time_start=entity['observations'][-1]['timestamp_ms'];filtered=request('/api/v1/memory-query',dict(body,start_ms=time_start))['long_memory'];assert all(o['timestamp_ms']>=time_start for e in filtered['entities'] for o in e['observations'])
    baseline=request('/api/v1/memory-query',dict(body,long_memory_enabled=False));assert baseline['long_memory'] is None;assert baseline['matches']==result['matches'],'entity expansion must not mutate retrieval ranking'
    snapshot={e['entity_id']:[o['observation_id'] for o in e['observations']] for e in entities}
    if args.restart_check:assert snapshot==json.loads((root/'identity-snapshot.json').read_text()),'Memory changed across restart'
    else:(root/'identity-snapshot.json').write_text(json.dumps(snapshot,indent=2))
    (root/'linked-query.json').write_text(json.dumps(result,indent=2));(root/'ambiguous-query.json').write_text(json.dumps(ambiguous,indent=2))
    print(json.dumps(dict(entities=len(entities),links=sum(len(g['links']) for g in graphs),selected_entity=entity['entity_id'],selected_track=entity['track_id'],supporting_clips=len(evidence),observations=len(entity['observations']),restart_verified=args.restart_check,elapsed_secs=round(time.monotonic()-started,3))))
if __name__=='__main__': main()
