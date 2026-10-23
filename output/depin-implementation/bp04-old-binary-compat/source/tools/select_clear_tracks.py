#!/usr/bin/env python3
"""Choose readable development sequences using detector geometry, never action answers."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from types import SimpleNamespace
from evaluate_temporal_corpus import digest
from semantic_memory_server import Retrieval
from temporal_interpretation import evidence_crops, SOURCE_CROP_MAX_EDGE

MIN_EDGE=48
MIN_SPAN_MS=1000
MIN_AREA_RATIO=1.25


def candidate_windows(graph, size, duration):
    candidates=[]
    for entity in graph['entities']:
        if entity['class_name'] not in ('car','truck','bus','motorcycle'):continue
        points={}
        for obs in entity['observations']:
            box=obs.get('bbox');t=obs['timestamp_ms']
            if box is None or not 0<=t<duration:continue
            x,y,w,h=box
            if any(not math.isfinite(v) for v in box) or x<0 or y<0 or w<=0 or h<=0 or x+w>1.001 or y+h>1.001:continue
            edge=min(w*size[0],h*size[1])
            if edge>=MIN_EDGE:points[t]=obs
        points=sorted(points.values(),key=lambda o:o['timestamp_ms'])
        best=None
        for first in points:
            selected=[o for o in points if first['timestamp_ms']<=o['timestamp_ms']<first['timestamp_ms']+20000]
            if len(selected)<2 or selected[-1]['timestamp_ms']-selected[0]['timestamp_ms']<MIN_SPAN_MS:continue
            areas=[o['bbox'][2]*o['bbox'][3] for o in selected];ratio=max(areas)/min(areas)
            if ratio<MIN_AREA_RATIO:continue
            edges=[min(o['bbox'][2]*size[0],o['bbox'][3]*size[1]) for o in selected]
            rank=(min(edges),ratio,len(selected))
            if best is None or rank>best['rank']:
                best=dict(entity=entity,observations=selected,rank=rank,min_source_edge=min(edges),area_ratio=ratio)
        if best:candidates.append(best)
    return sorted(candidates,key=lambda c:(-c['rank'][0],-c['rank'][1],c['entity']['entity_id']))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',default='data/live-evaluation-2026-10-16');p.add_argument('--output',required=True);args=p.parse_args()
    root=Path(args.output)
    if root.exists() and any(root.iterdir()):raise ValueError('Use an empty output directory')
    root.mkdir(parents=True,exist_ok=True)
    service=Retrieval(SimpleNamespace(data_dir=str(Path(args.data_dir).resolve()),frames=8,frame_size=384))
    inputs=[];report=[];cases=[]
    for path in sorted((Path(args.data_dir)/'memory/manifests').glob('*.json')):
        job=json.loads(path.read_text())
        if job['status']!='completed':continue
        for camera in job['result']['camera_results']:
            camera_id=camera['camera']['camera_id'];graph=camera.get('long_memory')
            if not graph:continue
            source=path.parent.parent/job['id']/camera_id/'source.mkv'
            probe=dict(source_path=str(source.resolve()),start_ms=0,end_ms=min(20000,camera['duration_ms']))
            frame=service.frames(probe,count=1,size=SOURCE_CROP_MAX_EDGE,no_upscale=True)[0]
            candidates=candidate_windows(graph,frame.size,camera['duration_ms'])
            camera_report=dict(camera_id=camera_id,detail_size=list(frame.size),eligible_tracks=len(candidates),selected=[])
            for candidate in candidates[:2]:
                entity=candidate['entity'];obs=candidate['observations'];start=obs[0]['timestamp_ms'];end=min(obs[-1]['timestamp_ms']+1,camera['duration_ms'])
                id=camera_id+'-'+entity['entity_id'][-8:];folder=root/id;folder.mkdir()
                event=dict(event_id=id,source_path=str(source.resolve()),start_ms=start,end_ms=end,grounding=dict(entity_id=entity['entity_id'],source_sha256=graph['source_sha256'],class_name=entity['class_name'],observations=obs))
                count=min(8,len(obs));times=[obs[round(i*(len(obs)-1)/(count-1))]['timestamp_ms'] for i in range(count)]
                previews,pts=service.frames(event,count=count,with_timestamps=True,sample_timestamps=times)
                sha=digest(source)
                preview_crops,_=evidence_crops(service,event,previews,pts,count,times,sha,(start,end),'preview')
                crops,provenance=evidence_crops(service,event,previews,pts,count,times,sha,(start,end),'source')
                if any(c is None or min(c['image'].size)<MIN_EDGE for c in crops):raise ValueError('Selected candidate failed decoded crop alignment/quality')
                saved=[]
                for i,(preview,crop,old) in enumerate(zip(previews,crops,preview_crops)):
                    preview.save(folder/f'{i}-context.png');crop['image'].save(folder/f'{i}-source.png')
                    if old:old['image'].save(folder/f'{i}-preview.png')
                    saved.append(dict(timestamp_ms=pts[i],context=str((folder/f'{i}-context.png').relative_to(root)),source_crop=str((folder/f'{i}-source.png').relative_to(root)),preview_crop=str((folder/f'{i}-preview.png').relative_to(root)) if old else None,source_size=list(crop['image'].size),preview_size=list(old['image'].size) if old else None))
                inputs.append(dict(query=f'Is the selected {entity["class_name"]} moving toward or away from the camera?',events=[event]))
                cases.append(dict(id=id,camera_id=camera_id,entity_id=entity['entity_id'],source_sha256=sha,frames=saved,crop_evidence=provenance))
                camera_report['selected'].append(dict(id=id,min_source_edge=round(candidate['min_source_edge'],2),box_area_ratio=round(candidate['area_ratio'],3)))
            report.append(camera_report)
    service.db.close()
    (root/'inputs.json').write_text(json.dumps(inputs,indent=2));(root/'cases.json').write_text(json.dumps(cases,indent=2))
    (root/'selection.json').write_text(json.dumps(dict(created_utc=datetime.now(timezone.utc).isoformat(),use='development clarity comparison, not independent acceptance',criteria=dict(min_source_crop_edge=MIN_EDGE,min_span_ms=MIN_SPAN_MS,min_area_ratio=MIN_AREA_RATIO,max_window_ms=20000),cameras=report),indent=2))
    if not inputs:raise ValueError('No sufficiently sized multi-frame targets; no low-quality substitutes selected')
    print(json.dumps(dict(cases=len(inputs),cameras=report)),flush=True)

if __name__=='__main__':main()
