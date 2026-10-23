#!/usr/bin/env python3
"""Evaluate frozen source windows directly through the real interpretation worker.

Expected answers/visibility labels stay local. Completion is not an accuracy score;
review saved images and citations separately. Use the normal API evaluator as well
when validating retrieval integration.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

from temporal_interpretation import PROMPT_VERSION, validate_result


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:8193')
    p.add_argument('--labels', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--prompt-version', help='Explicit prompt override; frozen labels remain unchanged')
    p.add_argument('--model', required=True)
    p.add_argument('--revision', required=True)
    args = p.parse_args()
    labels = Path(args.labels)
    frozen_hash = digest(labels)
    corpus = json.loads(labels.read_text())
    cases = corpus['cases']
    sampling = dict(corpus.get('sampling', dict(frames=8,frame_size=384,prompt_version=PROMPT_VERSION)))
    if args.prompt_version:
        sampling['prompt_version'] = args.prompt_version
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Corpus needs unique case IDs')
    for case in cases:
        if Path(case['id']).name != case['id'] or case['id'] in ('.', '..'):
            raise ValueError('Unsafe case ID')
        if digest(Path(case['source_path'])) != case['source_sha256']:
            raise ValueError('Source differs from frozen labels: '+case['id'])
        if not 0 <= case['start_ms'] < case['end_ms'] or case['end_ms']-case['start_ms'] > 20000:
            raise ValueError('Invalid evaluation window')
    root = Path(args.output)
    if root.exists() and any(root.iterdir()):
        raise ValueError('Use a new output directory; previous runs are immutable')
    root.mkdir(parents=True, exist_ok=True)
    manifest = dict(started_utc=datetime.now(timezone.utc).isoformat(),labels=str(labels),
        labels_sha256=frozen_hash,expected_model=args.model,expected_revision=args.revision,
        interpreter_source_sha256=digest(Path(__file__).with_name('temporal_interpretation.py')),
        state_gate_source_sha256=digest(Path(__file__).with_name('state_verification.py')),
        expected_labels_sent_to_model=False,expected_sampling=sampling,requests=[])
    rows = []
    for case in cases:
        started = time.monotonic()
        print(datetime.now(timezone.utc).isoformat(timespec='seconds'),case['id'],flush=True)
        body = dict(query=case['query'],events=[dict(event_id=case['id'],source_path=str(Path(case['source_path']).resolve()),
            start_ms=case['start_ms'],end_ms=case['end_ms'],metadata=case.get('metadata',{}))])
        payload = json.dumps(body).encode()
        manifest['requests'].append(dict(id=case['id'],payload_sha256=hashlib.sha256(payload).hexdigest()))
        (root/'run-manifest.json').write_text(json.dumps(manifest,indent=2))
        try:
            request = urllib.request.Request(args.url+'/interpret',data=payload,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=900) as response:
                result = json.load(response)
            (root/(case['id']+'.json')).write_text(json.dumps(result,indent=2))
            if len(result['interpretations']) != 1:
                raise ValueError('Expected one interpretation')
            value = result['interpretations'][0]
            if value['event_id'] != case['id'] or (value['start_ms'],value['end_ms']) != (case['start_ms'],case['end_ms']):
                raise ValueError('Wrong evidence identity/window')
            provenance = value['provenance']
            if provenance['model'] != args.model or Path(provenance['snapshot']).name != args.revision:
                raise ValueError('Wrong model or revision')
            if any(provenance.get(k) != sampling[k] for k in ('frames','frame_size','prompt_version')):
                raise ValueError('Wrong prompt or sampling configuration')
            times = value['sampled_timestamps_ms']
            if len(times) != 8 or times != sorted(set(times)) or any(not case['start_ms'] <= t < case['end_ms'] for t in times):
                raise ValueError('Unexpected sampling')
            if any(type(o.get('frame_index')) is not int or not 0 <= o['frame_index'] < len(times) or o.get('timestamp_ms') != times[o['frame_index']] for o in value['observations']):
                raise ValueError('Forged or invalid frame citation')
            validate_result(value,times)
            if len(value['frames']) != len(times) or any(f['timestamp_ms'] != t for f,t in zip(value['frames'],times)):
                raise ValueError('Frame/citation timeline mismatch')
            row = dict(id=case['id'],completed=True,status=value['status'],answer=value['answer'],cached=value['cached'],
                elapsed_secs=round(time.monotonic()-started,3),interpretation={k:v for k,v in value.items() if k!='frames'})
        except Exception as error:
            row = dict(id=case['id'],completed=False,error=str(error),elapsed_secs=round(time.monotonic()-started,3))
        rows.append(row)
        (root/'results.json').write_text(json.dumps(rows,indent=2))
        print(json.dumps({k:v for k,v in row.items() if k!='interpretation'}),flush=True)
    if digest(labels) != frozen_hash:
        raise ValueError('Labels changed during evaluation')
    manifest.update(finished_utc=datetime.now(timezone.utc).isoformat(),completed=sum(r['completed'] for r in rows),total=len(rows))
    (root/'run-manifest.json').write_text(json.dumps(manifest,indent=2))
    if manifest['completed'] != len(rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
