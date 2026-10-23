#!/usr/bin/env python3
"""Run real temporal questions and evidence-link/filter contracts on frozen clips.
Temporal outputs require manual visual scoring; HTTP success is not an accuracy pass.
"""
import argparse,json,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8191');p.add_argument('--labels',default='evals/live-2026-10-16/temporal-labels.json');p.add_argument('--output',default='output/live-evaluation-2026-10-16/behaviors');args=p.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    def request(path,body=None):return json.load(urllib.request.urlopen(urllib.request.Request(args.url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=900))
    cases=json.loads(Path(args.labels).read_text())['cases'];scope={c['job_id'] for c in cases};jobs=[j for j in request('/api/v1/memory-jobs') if j['id'] in scope];contracts=[]
    for job in jobs:
        camera=job['result']['camera_results'][0];graph=camera['long_memory'];assert graph['status']=='ready' and graph['version']=='entity-memory-v2-pts'
        entity=max(graph['entities'],key=lambda e:len(set(o['event_id'] for o in e['observations']))) if graph['entities'] else None
        if entity is None:contracts.append(dict(job_id=job['id'],status='no_confirmed_entities'));continue
        body=dict(query='What observations occurred for this tracked object?',job_id=job['id'],entity_id=entity['entity_id'],long_memory_enabled=True,semantic_enabled=False,limit=1)
        result=request('/api/v1/memory-query',body);answer=result['long_memory'];assert len(answer['entities'])==1
        evidence={e['event_id']:e for e in answer['supporting_events']};observations=answer['entities'][0]['observations']
        for o in observations:
            e=evidence[o['event_id']];assert e['camera_id']==entity['camera_id'] and e['job_id']==job['id'];assert e['start_ms']<=o['timestamp_ms']<e['end_ms']
        assert all(a['timestamp_ms']<=b['timestamp_ms'] for a,b in zip(observations,observations[1:]))
        for filters in [dict(camera_ids=['nonexistent']),dict(cluster_id='unrelated'),dict(start_ms=999999),dict(job_id='00000000-0000-0000-0000-000000000001')]:
            excluded=request('/api/v1/memory-query',dict(body,**filters))['long_memory'];assert not excluded['entities'] and not excluded['links'] and not excluded['supporting_events']
        (root/f'links-{entity["camera_id"]}.json').write_text(json.dumps(result,indent=2));contracts.append(dict(camera=entity['camera_id'],entities=len(graph['entities']),status=answer['status'],supporting_clips=len(evidence),observations=len(observations),filter_checks=4))
    (root/'contracts.json').write_text(json.dumps(contracts,indent=2));results=[]
    for case in cases:
        print(datetime.now(timezone.utc).isoformat(timespec='seconds'),case['id'],flush=True);started=time.monotonic()
        try:
            body=dict(query=case['query'],job_id=case['job_id'],camera_ids=[case['camera']],start_ms=case['start_ms'],end_ms=case['end_ms'],semantic_enabled=True,rerank_enabled=True,temporal_enabled=True,vlm_enabled=False,limit=1)
            result=request('/api/v1/memory-query',body);(root/f'{case["id"]}.json').write_text(json.dumps(result,indent=2));assert result['retrieval_mode']=='hybrid_qwen_reranker',result['retrieval_notes'];assert len(result['interpretations'])==1,result['interpretation_notes']
            value=result['interpretations'][0];assert len(value['frames']) in (8,16);assert all(o['timestamp_ms']==value['sampled_timestamps_ms'][o['frame_index']] for o in value['observations'])
            (root/f'{case["id"]}.json').write_text(json.dumps(result,indent=2));value={k:v for k,v in value.items() if k!='frames'}
            row=dict(id=case['id'],expected=case['expected'],elapsed_secs=round(time.monotonic()-started,3),interpretation=value)
        except Exception as error:row=dict(id=case['id'],error=str(error),elapsed_secs=round(time.monotonic()-started,3))
        results.append(row);(root/'temporal-results.json').write_text(json.dumps(results,indent=2));print(json.dumps(row),flush=True)
if __name__=='__main__':main()
