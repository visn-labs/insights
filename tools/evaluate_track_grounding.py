#!/usr/bin/env python3
"""Exercise explicit tracker crops on retained live recordings; no accuracy scoring."""
import argparse
import json
from pathlib import Path
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8193')
    parser.add_argument('--data-dir',default='data/live-evaluation-2026-10-16')
    parser.add_argument('--output',required=True)
    args=parser.parse_args();root=Path(args.output)
    if root.exists() and any(root.iterdir()):raise ValueError('Use a new output directory')
    root.mkdir(parents=True,exist_ok=True);rows=[]
    for path in sorted((Path(args.data_dir)/'memory/manifests').glob('*.json')):
        job=json.loads(path.read_text())
        for camera in job['result']['camera_results']:
            graph=camera['long_memory']
            entities=[e for e in graph['entities'] if e['class_name'] in ('car','truck','bus','motorcycle')]
            entity=max(entities,key=lambda e:len({o['timestamp_ms'] for o in e['observations'] if o.get('bbox')}))
            observations=[o for o in entity['observations'] if o.get('bbox')]
            start=min(o['timestamp_ms'] for o in observations);end=min(start+20000,camera['duration_ms'])
            observations=[o for o in observations if start<=o['timestamp_ms']<end]
            camera_id=camera['camera']['camera_id']
            event=dict(event_id='track-'+camera_id,source_path=str((path.parent.parent/job['id']/camera_id/'source.mkv').resolve()),start_ms=start,end_ms=end,
                metadata=dict(camera_id=camera_id,job_id=job['id']),grounding=dict(source_sha256=graph['source_sha256'],entity_id=entity['entity_id'],class_name=entity['class_name'],observations=observations))
            body=dict(query=f'Is the selected {entity["class_name"]} moving toward or away from the camera?',events=[event])
            (root/(camera_id+'-request.json')).write_text(json.dumps(body,indent=2))
            print('Running',camera_id,entity['entity_id'],flush=True)
            request=urllib.request.Request(args.url+'/interpret',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            result=json.load(urllib.request.urlopen(request,timeout=900))
            (root/(camera_id+'.json')).write_text(json.dumps(result,indent=2))
            value=result['interpretations'][0];frames=value['frames'];ground=value['provenance']['track_grounding']
            assert ground['entity_id']==entity['entity_id']
            assert all(start<=f['decoded_timestamp_ms']<end for f in frames)
            cropped=[i for i,f in enumerate(frames) if f.get('target_crop')]
            assert cropped==ground['aligned_frame_indices']
            if value['status']=='supported':
                cited=[o['frame_index'] for o in value['observations']]
                assert all(i in cropped for i in cited)
                assert len({frames[i]['decoded_timestamp_ms'] for i in cited})>=2
            rows.append(dict(camera_id=camera_id,job_id=job['id'],entity_id=entity['entity_id'],class_name=entity['class_name'],status=value['status'],answer=value['answer'],frames=len(frames),crops=len(cropped),window=[start,end],sampler=value['provenance']['sampler']))
            (root/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows[-1]),flush=True)

if __name__=='__main__':main()
