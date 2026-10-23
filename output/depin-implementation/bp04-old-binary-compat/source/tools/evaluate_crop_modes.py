#!/usr/bin/env python3
"""Controlled preview/source crop comparison on development evidence; no accuracy score."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import urllib.request
from evaluate_reviewed_states import validate_response
from prepare_state_review import digest
from temporal_interpretation import CROP_VERSION,PROMPT_VERSION


def compare(preview,source):
    if preview['sampled_timestamps_ms']!=source['sampled_timestamps_ms'] or preview['frames'] and [f['image'] for f in preview['frames']]!=[f['image'] for f in source['frames']]:raise ValueError('Context evidence changed between crop modes')
    for value,mode in ((preview,'preview'),(source,'source')):
        proof=value['provenance']['crop_evidence']
        if proof['version']!=CROP_VERSION or proof['mode']!=mode:raise ValueError('Wrong crop mode/version')
        if mode=='source' and proof['no_upscale'] is not True:raise ValueError('Source crops allow upscaling')
    a=preview['provenance']['crop_evidence'];b=source['provenance']['crop_evidence']
    if a['decoded_timestamps_ms']!=b['decoded_timestamps_ms']:raise ValueError('Crop PTS differ')
    ratios=[]
    for old,new in zip(a['crop_sizes'],b['crop_sizes']):
        if old and new:
            ratios.append(round(new[0]*new[1]/(old[0]*old[1]),3))
    return dict(context_pixels_equal=True,crop_pts_equal=True,pixel_area_ratios=ratios)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candidates',required=True);p.add_argument('--output',required=True)
    p.add_argument('--preview-url',default='http://127.0.0.1:8194');p.add_argument('--source-url',default='http://127.0.0.1:8193')
    p.add_argument('--model',required=True);p.add_argument('--revision',required=True);args=p.parse_args()
    candidates=Path(args.candidates);bodies=json.loads((candidates/'inputs.json').read_text());cases={c['id']:c for c in json.loads((candidates/'cases.json').read_text())}
    for body in bodies:
        event=body['events'][0]
        if digest(event['source_path'])!=cases[event['event_id']]['source_sha256']:raise ValueError('Candidate source changed')
    root=Path(args.output)
    if root.exists() and any(root.iterdir()):raise ValueError('Use a new output directory')
    root.mkdir(parents=True,exist_ok=True)
    now=lambda:datetime.now(timezone.utc).isoformat()
    manifest=dict(started_utc=now(),use='development comparison; no independent accuracy labels',model=args.model,revision=args.revision,prompt_version=PROMPT_VERSION,crop_version=CROP_VERSION,inputs_sha256=digest(candidates/'inputs.json'),cases_sha256=digest(candidates/'cases.json'),code_hashes={n:digest(Path(__file__).with_name(n)) for n in ('evaluate_crop_modes.py','evaluate_reviewed_states.py','temporal_interpretation.py','semantic_memory_server.py','state_verification.py','select_clear_tracks.py')},requests=[])
    rows=[]
    for body in bodies:
        event=body['events'][0];id=event['event_id'];payload=json.dumps(body).encode();values={}
        manifest['requests'].append(dict(id=id,identical_payload_sha256=hashlib.sha256(payload).hexdigest()))
        (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
        for mode,url in (('preview',args.preview_url),('source',args.source_url)):
            print(now(),id,mode,flush=True)
            request=urllib.request.Request(url+'/interpret',data=payload,headers={'Content-Type':'application/json'})
            result=json.load(urllib.request.urlopen(request,timeout=900));(root/(id+'-'+mode+'.json')).write_text(json.dumps(result,indent=2))
            values[mode]=validate_response(result,cases[id],event,args.model,args.revision)
        row=dict(id=id,**compare(values['preview'],values['source']),modes={mode:dict(status=v['status'],answer=v['answer'],crop_sizes=v['provenance']['crop_evidence']['crop_sizes'],state_verification=v['provenance']['state_verification']) for mode,v in values.items()})
        rows.append(row);(root/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
    if digest(candidates/'inputs.json')!=manifest['inputs_sha256'] or digest(candidates/'cases.json')!=manifest['cases_sha256']:raise ValueError('Candidate inputs changed during comparison')
    for body in bodies:
        event=body['events'][0]
        if digest(event['source_path'])!=cases[event['event_id']]['source_sha256']:raise ValueError('Source changed during comparison')
    manifest.update(finished_utc=now(),completed=len(rows));(root/'manifest.json').write_text(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
