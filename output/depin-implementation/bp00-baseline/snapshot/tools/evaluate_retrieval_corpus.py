#!/usr/bin/env python3
"""Compare real retrieval modes against a frozen, visually reviewed event relevance file.
Labels must be created before running models. Scores are not calibrated probabilities.
"""
import argparse,json,math,time,urllib.request,re
from datetime import datetime,timezone
from pathlib import Path

def metrics(ranked,relevant,ks=(1,3,5)):
    if len(ranked)!=len(set(ranked)):raise ValueError('Duplicate ranked IDs')
    positive=set(relevant)
    if not positive:return {'negative_candidates_returned':len(ranked),'negative_empty_result':int(not ranked)}
    result={}
    for k in ks:
        hits=sum(event in positive for event in ranked[:k]);result[f'hit@{k}']=int(hits>0);result[f'recall@{k}']=hits/len(positive)
        dcg=sum(1/math.log2(i+2) for i,event in enumerate(ranked[:k]) if event in positive)
        ideal=sum(1/math.log2(i+2) for i in range(min(k,len(positive))))
        result[f'ndcg@{k}']=dcg/ideal
    return result

def summarize(results, queries):
    positive_ids={q['id'] for q in queries if q['relevant_event_ids']}
    negative_ids={q['id'] for q in queries if not q['relevant_event_ids']}
    summary={}
    for mode in ['lexical','embedding','hybrid','reranked']:
        rows=[r for r in results if r['mode']==mode];valid=[r for r in rows if 'metrics' in r]
        positive=[r for r in valid if r['query_id'] in positive_ids];negative=[r for r in valid if r['query_id'] in negative_ids]
        keys=metrics([],['placeholder']).keys()
        summary[mode]=dict(attempted=len(rows),completed=len(valid),failed=len(rows)-len(valid),
            positive_queries=len(positive_ids),negative_queries=len(negative_ids),
            mean_metrics={k:sum(r['metrics'][k] for r in positive)/len(positive_ids) for k in keys} if positive_ids else {},
            negative_empty_result_rate=sum(r['metrics']['negative_empty_result'] for r in negative)/len(negative_ids) if negative_ids else None,
            latency_mean_secs=sum(r['latency_secs'] for r in rows)/len(rows) if rows else None)
    return summary

def main():
    parser=argparse.ArgumentParser();parser.add_argument('labels');parser.add_argument('--url',default='http://127.0.0.1:8192');parser.add_argument('--worker',default='http://127.0.0.1:8193');parser.add_argument('--data-dir',default='data/live-evaluation-2026-10-16');parser.add_argument('--output',default='output/live-evaluation-2026-10-16/retrieval');args=parser.parse_args()
    labels=json.loads(Path(args.labels).read_text());root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    def request(url,body=None):
        return json.load(urllib.request.urlopen(urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=900))
    jobs=request(args.url+'/api/v1/memory-jobs');wanted=set(labels['job_ids']);candidates=[]
    for job in jobs:
        if job['id'] not in wanted:continue
        for camera in job['result']['camera_results']:
            for event in camera['events']:
                source=Path(args.data_dir)/'memory'/job['id']/camera['camera']['camera_id']/'source.mkv'
                candidates.append(dict(event_id=event['event_id'],source_path=str(source.resolve()),start_ms=event['start_ms'],end_ms=event['end_ms']))
    ids={event['event_id'] for event in candidates};assert ids
    if any(not set(q['relevant_event_ids'])<=ids for q in labels['queries']):raise ValueError('Labels refer to missing evidence')
    results=[]
    for mode in ['lexical','embedding','hybrid','reranked']:
        for q in labels['queries']:
            print(datetime.now(timezone.utc).isoformat(timespec='seconds'),mode,q['id'],flush=True);started=time.monotonic()
            try:
                if mode=='embedding':
                    body=request(args.worker+'/search',dict(query=q['query'],events=candidates,lexical_ids=[],limit=min(50,len(candidates)),rerank=False));ranked=[r['event_id'] for r in body['items']]
                    assert body['mode']=='hybrid_rrf','Embedding-only probe must not rerank';actual='embedding_only_rrf_monotonic'
                else:
                    body=request(args.url+'/api/v1/memory-query',dict(query=q['query'],cluster_id=labels['cluster_id'],semantic_enabled=mode!='lexical',rerank_enabled=mode=='reranked',limit=50,vlm_enabled=False,temporal_enabled=False,long_memory_enabled=False))
                    actual=body['retrieval_mode'];expected={'lexical':'local_lexical_metadata','hybrid':'hybrid_rrf','reranked':'hybrid_qwen_reranker'}[mode]
                    if actual!=expected:raise RuntimeError(f'Unexpected fallback {actual}: {body["retrieval_notes"]}')
                    ranked=[r['event']['event_id'] for r in body['matches']]
                    if not set(ranked)<=ids:raise ValueError('Results escaped frozen corpus')
                notes=body.get('retrieval_notes',body.get('notes',[]));coverage=next((list(map(int,re.search(r'Visual coverage (\d+)/(\d+)',n).groups())) for n in notes if re.search(r'Visual coverage (\d+)/(\d+)',n)),None)
                row=dict(visual_coverage=coverage,notes=notes,query_id=q['id'],query=q['query'],mode=mode,actual_mode=actual,latency_secs=round(time.monotonic()-started,3),ranked_ids=ranked,metrics=metrics(ranked,q['relevant_event_ids']))
                (root/f'{mode}-{q["id"]}.json').write_text(json.dumps(body,indent=2))
            except Exception as error:row=dict(query_id=q['id'],mode=mode,error=str(error),latency_secs=round(time.monotonic()-started,3))
            results.append(row);(root/'results.json').write_text(json.dumps(results,indent=2))
    summary=summarize(results,labels['queries'])
    (root/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
