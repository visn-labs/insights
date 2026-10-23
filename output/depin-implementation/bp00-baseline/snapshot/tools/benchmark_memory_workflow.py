#!/usr/bin/env python3
"""Actual model workflow replay: disable/enable exact work caches and compare full outputs."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from prepare_state_review import digest
from semantic_memory_server import Retrieval,QwenModels
from temporal_interpretation import SequenceInterpreter


def stable(value):
    if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in ('elapsed_secs','cached','work_cache')}
    if isinstance(value,list):return [stable(v) for v in value]
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',default='data/live-evaluation-2026-10-16');p.add_argument('--output',required=True);args=p.parse_args()
    root=Path(args.output)
    if root.exists() and any(root.iterdir()):raise ValueError('Use a new output directory')
    root.mkdir(parents=True,exist_ok=True);data=Path(args.data_dir).resolve()
    config=SimpleNamespace(data_dir=str(data),qwen_source=str(Path('.retrieval/qwen').resolve()),embedding_model='Qwen/Qwen3-VL-Embedding-2B',embedding_revision='9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda',reranker_model='Qwen/Qwen3-VL-Reranker-2B',reranker_revision='4bd860ac4f15ad1897a214615cccc700f8f71818',frames=8,frame_size=256,device='mps',keep_models_loaded=True,disable_work_caches=True,
        interpretation_api_url='http://127.0.0.1:1244/v1',interpretation_model='/Users/anagha/.lmstudio/models/lmstudio-community/Qwen3-VL-4B-Instruct-MLX-4bit',interpretation_revision='d4f53934afba23177de2a6caef3c7cdd5b868b5f2220b609584b1fe35a63beec',interpretation_frames=8,interpretation_frame_size=384,interpretation_crop_mode='source')
    job=json.loads((data/'memory/manifests/01a1433a-26bf-7e50-b482-5007781d97c7.json').read_text());camera=job['result']['camera_results'][0]
    source=data/'memory'/job['id']/camera['camera']['camera_id']/'source.mkv'
    graph=camera['long_memory'];entity=next(e for e in graph['entities'] if e['entity_id']=='ba5d72eb-c513-5b74-b634-aa70dc0e14f9')
    if digest(source)!=graph['source_sha256']:raise ValueError('Retained source changed')
    events=[dict(event_id=e['event_id'],source_path=str(source),start_ms=e['start_ms'],end_ms=e['end_ms']) for e in camera['events']]
    body=dict(query='Is the selected car moving toward or away from the camera?',events=events,lexical_ids=[e['event_id'] for e in events],limit=3,rerank=True)
    (root/'search-input.json').write_text(json.dumps(body,indent=2))
    models=QwenModels(config);service=Retrieval(config,models);interpreter=SequenceInterpreter(service);service.interpreter=interpreter
    counts=dict(embedder_loads=0,reranker_loads=0,visual_complete_calls=0)
    original_load=models.load;original_complete=interpreter.complete
    def load(kind):
        if getattr(models,kind) is None:counts[kind+'_loads']+=1
        return original_load(kind)
    def complete(*a,**kw):counts['visual_complete_calls']+=1;return original_complete(*a,**kw)
    models.load=load;interpreter.complete=complete
    manifest=dict(started_utc=datetime.now(timezone.utc).isoformat(),source_sha256=digest(source),settings=vars(config),input_sha256=digest(root/'search-input.json'),code_hashes={n:digest(Path(__file__).with_name(n)) for n in ('benchmark_memory_workflow.py','semantic_memory_server.py','temporal_interpretation.py','state_verification.py')},scope='same-process development replay; not a throughput or general accuracy benchmark')
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2));rows=[];reference=None
    by_id={e['event_id']:e for e in camera['events']}
    for label,disabled in [('baseline-1',True),('baseline-2',True),('optimized-cold',False),('optimized-warm',False)]:
        config.disable_work_caches=disabled;initial=dict(service.work_stats);before=dict(counts);started=time.monotonic();print(datetime.now(timezone.utc).isoformat(),label,flush=True)
        search=service.search(body);selected=[]
        for match in search['items']:
            e=by_id[match['event_id']];start=max(0,e['start_ms']-2000);end=min(camera['duration_ms'],e['end_ms']+2000,start+20000)
            selected.append(dict(event_id=e['event_id'],source_path=str(source),start_ms=start,end_ms=end,metadata=dict(camera_id=camera['camera']['camera_id'],original_start_ms=e['start_ms'],original_end_ms=e['end_ms'],summary=e['description']['summary'],activity_mean=e['activity_mean'],activity_peak=e['activity_peak']),grounding=dict(source_sha256=graph['source_sha256'],entity_id=entity['entity_id'],class_name=entity['class_name'],observations=[o for o in entity['observations'] if o.get('bbox') and start<=o['timestamp_ms']<end])))
        interpretation=interpreter.interpret(dict(query=body['query'],events=selected))
        result=dict(search=search,interpretation=interpretation);(root/(label+'.json')).write_text(json.dumps(result,indent=2))
        semantic=dict(search_items=search['items'],search_mode=search['mode'],search_notes=search['notes'],interpretation=stable(interpretation))
        raw=json.dumps(semantic,sort_keys=True).encode();signature=hashlib.sha256(raw).hexdigest()
        row=dict(phase=label,elapsed_secs=round(time.monotonic()-started,3),counters={k:service.work_stats[k]-initial[k] for k in initial},model_calls={k:counts[k]-before[k] for k in before},decode_payload_bytes=service.decode_cache_bytes,decode_entries=len(service.decode_cache),query_entries=len(service.query_cache),rerank_entries=len(service.rerank_cache),semantic_sha256=signature)
        rows.append(row);(root/'metrics.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
        if reference is None:reference=raw
        elif raw!=reference:raise ValueError('Exact workflow output parity failed; inspect saved results before claiming optimization equivalence')
    if digest(source)!=manifest['source_sha256']:raise ValueError('Source changed during benchmark')
    manifest.update(finished_utc=datetime.now(timezone.utc).isoformat(),exact_output_parity=True)
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2));service.db.close()

if __name__=='__main__':main()
