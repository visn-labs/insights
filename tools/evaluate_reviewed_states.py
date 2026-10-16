#!/usr/bin/env python3
"""Evaluate human-reviewed targets using pre-review input envelopes, never labels as prompts."""
import argparse
import base64
import io
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request
from prepare_state_review import check_annotations, digest
from temporal_interpretation import PROMPT_VERSION, validate_result
from state_verification import transition


def validate_response(result, case, event, model, revision):
    if len(result.get('interpretations',[]))!=1:raise ValueError('Expected one interpretation')
    value=result['interpretations'][0]
    if value['event_id']!=case['id'] or (value['start_ms'],value['end_ms'])!=(event['start_ms'],event['end_ms']):
        raise ValueError('Evidence identity or interval changed')
    p=value['provenance']
    if p['model']!=model or Path(p['snapshot']).name!=revision or p['prompt_version']!=PROMPT_VERSION:
        raise ValueError('Model, revision or prompt changed')
    frames=value['frames'];times=value['sampled_timestamps_ms']
    if len(frames)!=len(case['frames']) or len(frames)!=len(times) or times!=sorted(set(times)):
        raise ValueError('Unreviewed frame sequence returned')
    if [f.get('decoded_timestamp_ms') for f in frames]!=[f['timestamp_ms'] for f in case['frames']]:
        raise ValueError('Decoded timestamps differ from reviewed images')
    # Visibility annotations bind to the inspected crop resolution, not just PTS.
    # Source-detail experiments must not silently reuse blurry-preview labels.
    if any('crop_size' in f for f in case['frames']):
        from PIL import Image
        for returned,reviewed in zip(frames,case['frames']):
            encoded=returned.get('target_crop') or returned['image']
            if len(encoded)>1_000_000:raise ValueError('Oversized reviewed image')
            with Image.open(io.BytesIO(base64.b64decode(encoded.split(',',1)[1],validate=True))) as image:
                if list(image.size)!=reviewed.get('crop_size'):raise ValueError('Returned crop resolution differs from human-reviewed evidence; do not transfer visibility labels')
    if any(f['timestamp_ms']!=t for f,t in zip(frames,times)):
        raise ValueError('Frame/citation seek targets disagree')
    for o in value['observations']:
        i=o.get('frame_index')
        if type(i) is not int or not 0<=i<len(times) or o.get('timestamp_ms')!=times[i]:raise ValueError('Invalid original citation timestamp')
    validate_result(value,times)
    if value['status']=='supported':
        gate=p.get('state_verification',{})
        if gate.get('status')!='passed' or gate.get('identity')!='same' or gate.get('reference_visible') is not True or gate.get('transition')!=value.get('claimed_transition') or gate.get('frame_indices')!=[o['frame_index'] for o in value['observations']]:
            raise ValueError('Supported response has no consistent state proof')
    return value


def score(value, review):
    row=dict(model_status=value['status'],human_identity=review['identity'],accuracy_claim=False)
    if review['identity']!='same':
        row.update(category='mixed_or_unclear_identity_history',policy='Do not accept an action for an unresolved selected-target history; this is not pairwise identity ground truth.',policy_passed=value['status']=='uncertain')
        return row
    if not review['reference_visible']:
        row.update(category='reference_unresolved',policy_passed=value['status']=='uncertain');return row
    visible=[f for f in review['frames'] if f['visibility']=='visible']
    if len(visible)<2:
        row.update(category='insufficient_reviewed_target_visibility',policy='Fewer than two clearly visible target observations cannot ground a directional comparison.',policy_passed=value['status']=='uncertain')
        return row
    if value['status']!='supported':
        row.update(category='abstained_without_action_comparison',policy_passed=None);return row
    indices=[o['frame_index'] for o in value['observations']]
    labels=[review['frames'][i] for i in indices]
    if len(labels)!=2 or any(l['visibility']!='visible' for l in labels):
        row.update(category='unsupported_visibility',policy_passed=False);return row
    expected=transition('camera_distance',labels[0]['state'],labels[1]['state'])
    row.update(category='cited_target_states',expected_transition=expected,policy_passed=expected is not None and value.get('claimed_transition')==expected)
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--packet',required=True);p.add_argument('--annotations',required=True)
    p.add_argument('--inputs',required=True);p.add_argument('--scopes',required=True)
    p.add_argument('--output',required=True);p.add_argument('--url',default='http://127.0.0.1:8193')
    p.add_argument('--model',required=True);p.add_argument('--revision',required=True);args=p.parse_args()
    files={k:Path(getattr(args,k)) for k in ('packet','annotations','inputs','scopes')}
    hashes={k:digest(v) for k,v in files.items()}
    review=check_annotations(files['packet'],files['annotations'])
    packet=json.loads(files['packet'].read_text());inputs=json.loads(files['inputs'].read_text());scopes=json.loads(files['scopes'].read_text())
    cases=packet['cases'];by_id={c['id']:c for c in cases};labels={c['id']:c for c in review['cases']}
    if set(scopes['cases'])!=set(by_id) or any(v not in ('target','scene') for v in scopes['cases'].values()):raise ValueError('Every case needs an explicit target/scene scope')
    if any(label.get('annotation_scope') is not None and label['annotation_scope']!=scopes['cases'][id] for id,label in labels.items()):raise ValueError('Scope overrides conflict with explicit human review scope')
    if len(inputs)!=len(cases) or len({body['events'][0]['event_id'] for body in inputs})!=len(cases):raise ValueError('Input envelopes incomplete or duplicated')
    for body in inputs:
        if set(body)!= {'query','events'} or len(body['events'])!=1:raise ValueError('Use pre-review query/event envelopes only')
        event=body['events'][0];case=by_id[event['event_id']]
        if Path(event['source_path']).resolve()!=Path(case['source_path']).resolve() or digest(event['source_path'])!=case['source_sha256'] or (event['start_ms'],event['end_ms'])!=(case['start_ms'],case['end_ms']):raise ValueError('Input evidence differs from review packet')
    root=Path(args.output)
    if root.exists() and any(root.iterdir()):raise ValueError('Use a new output directory')
    root.mkdir(parents=True,exist_ok=True)
    for key,path in files.items():(root/(key+'.json')).write_bytes(path.read_bytes())
    now=lambda:datetime.now(timezone.utc).isoformat()
    manifest=dict(started_utc=now(),input_hashes=hashes,model=args.model,revision=args.revision,prompt_version=PROMPT_VERSION,
        code_hashes={name:digest(Path(__file__).with_name(name)) for name in ('evaluate_reviewed_states.py','temporal_interpretation.py','state_verification.py')},
        review_frozen_before_inference=True,human_labels_sent_to_model=False,requests=[],excluded_cases=[dict(id=id,reason='User clarified that annotations describe the whole scene, not the vehicle target.') for id,scope in scopes['cases'].items() if scope=='scene'])
    rows=[]
    for body in inputs:
        event=body['events'][0];id=event['event_id']
        if scopes['cases'][id]!='target':continue
        payload=json.dumps(body).encode();manifest['requests'].append(dict(id=id,payload_sha256=hashlib.sha256(payload).hexdigest()))
        (root/'run-manifest.json').write_text(json.dumps(manifest,indent=2))
        print(now(),id,flush=True)
        request=urllib.request.Request(args.url+'/interpret',data=payload,headers={'Content-Type':'application/json'})
        result=json.load(urllib.request.urlopen(request,timeout=900))
        (root/(id+'.json')).write_text(json.dumps(result,indent=2))
        value=validate_response(result,by_id[id],event,args.model,args.revision)
        row=dict(id=id,completed=True,**score(value,labels[id]),state_verification=value['provenance'].get('state_verification'))
        rows.append(row);(root/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
    if any(digest(path)!=hashes[key] for key,path in files.items()):raise ValueError('Review/input files changed during inference')
    check_annotations(files['packet'],files['annotations'])
    manifest.update(finished_utc=now(),completed=len(rows),excluded=len(manifest['excluded_cases']))
    (root/'run-manifest.json').write_text(json.dumps(manifest,indent=2))
    summary=dict(completed=len(rows),excluded_scene_cases=len(manifest['excluded_cases']),identity_policy_passes=sum(r.get('policy_passed') is True for r in rows if r['category']=='mixed_or_unclear_identity_history'),identity_policy_cases=sum(r['category']=='mixed_or_unclear_identity_history' for r in rows),low_visibility_policy_cases=sum(r['category']=='insufficient_reviewed_target_visibility' for r in rows),low_visibility_policy_passes=sum(r.get('policy_passed') is True for r in rows if r['category']=='insufficient_reviewed_target_visibility'),accepted_actions=sum(r['model_status']=='supported' for r in rows),readiness='experimental',limitations=['Global history identity annotations are not pairwise identity labels.','Scene-scoped annotations are excluded from target action scoring.','No same-identity positive action acceptance set is established.'])
    (root/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
