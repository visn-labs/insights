#!/usr/bin/env python3
"""Freeze, inventory, replay and compare source-scoped video experiments."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import time
import urllib.request
import uuid
from urllib.parse import urlparse

from evaluate_retrieval_corpus import metrics

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ('high_activity', 'quiet_rare', 'object_interactions', 'state_changes',
            'people_animals', 'text_detail', 'long_history', 'difficult_capture')
SPLITS = ('development', 'validation', 'acceptance')


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def file_ref(ref):
    path = (ROOT / ref['path']).resolve(strict=True)
    if not path.is_relative_to(ROOT.resolve()) or digest(path) != ref['sha256']:
        raise ValueError(f'Invalid or changed project artifact: {ref["path"]}')
    return path


def corpus_inventory(corpus):
    if corpus.get('schema') != 'visn-general-corpus-v1':
        raise ValueError('Unknown corpus schema')
    groups, sources, cases = {}, {}, set()
    coverage = {split: {f: dict(sources=0, clips=0) for f in FAMILIES} for split in SPLITS}
    for source in corpus['sources']:
        if source['id'] in sources or source['split'] not in SPLITS:
            raise ValueError('Duplicate source or invalid split')
        file_ref(source)
        split = source['split']
        if split == 'acceptance' and source.get('development_exposed', True):
            raise ValueError('Development-exposed evidence cannot enter acceptance')
        for field in ('source_group', 'session_group', 'site_group', 'sha256'):
            if not source.get(field):
                raise ValueError(f'Missing split grouping: {field}')
            key = (field, source[field])
            if key in groups and groups[key] != split:
                raise ValueError(f'Source/session/site leakage across splits: {field}')
            groups[key] = split
        if type(source['clips']) is not int or source['clips'] < 1 or not source['families']:
            raise ValueError('Source needs clip count and task families')
        for family in set(source['families']):
            if family not in FAMILIES:
                raise ValueError('Unknown task family')
            coverage[split][family]['sources'] += 1
            coverage[split][family]['clips'] += source['clips']
        sources[source['id']] = source
    for ref in corpus.get('labels', []):
        file_ref(ref)
    for case in corpus['cases']:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', case['id']) or case['id'] in cases or case['split'] not in SPLITS:
            raise ValueError('Invalid/duplicate case ID or split')
        cases.add(case['id'])
        if not case['source_ids'] or any(s not in sources or sources[s]['split'] != case['split'] for s in case['source_ids']):
            raise ValueError('Case scope does not belong to its declared split')
        request = read(file_ref(case['request']))
        if set(request) - {'query', 'events', 'lexical_ids', 'limit', 'rerank'}:
            raise ValueError('Only inference search fields may be sent; labels are scoring-only')
        paths = {file_ref(sources[s]) for s in case['source_ids']}
        for event in request['events']:
            if set(event) != {'event_id', 'source_path', 'start_ms', 'end_ms'} or Path(event['source_path']).resolve() not in paths:
                raise ValueError('Search event outside declared evidence or contains extra fields')
    return dict(schema='visn-coverage-v1', coverage=coverage, cases=len(cases),
                source_count=len(sources), label_files=len(corpus.get('labels', [])),
                acceptance_ready=False,
                limitations=['Counts overlap between families; source counts are not independent site counts.',
                             'Coverage inventory is not evidence of reviewed label quality or accuracy.',
                             'Acceptance promotion requires reviewed labels and the plan quality gates.'])


def load_profile(path):
    value = read(path)
    if value.get('schema') != 'visn-experiment-profile-v1' or not re.fullmatch(r'[A-Za-z0-9_-]+', value['id']):
        raise ValueError('Invalid experiment profile')
    if value['vector_policy'] not in ('legacy-renormalize-v1', 'canonical-unit-v2'):
        raise ValueError('Unknown vector policy')
    return value


def validate_result(result, profile, request):
    proof = result['provenance']
    if proof.get('vector_policy') != profile['vector_policy']:
        raise ValueError('Worker vector policy differs from profile')
    for name, expected in profile['embedding'].items():
        actual = proof['embedding'].get(name)
        if name == 'snapshot':
            if not isinstance(actual, str) or Path(actual).name != expected:
                raise ValueError('Embedding snapshot differs from profile')
        elif actual != expected:
            raise ValueError(f'Embedding setting mismatch: {name}')
    if request.get('rerank', True):
        if result['mode'] != 'hybrid_qwen_reranker' or Path(proof.get('reranker_snapshot') or '').name != profile['reranker_revision']:
            raise ValueError('Reranking failed or snapshot differs from profile')
    ids = {e['event_id'] for e in request['events']}
    ranked = [r['event_id'] for r in result['items']]
    if len(ranked) != len(set(ranked)) or not set(ranked) <= ids:
        raise ValueError('Invalid/out-of-scope ranked results')


def signature(result):
    # Only these diagnostic fields are excluded. Evidence and scores stay exact.
    proof = {k: v for k, v in result['provenance'].items()
             if k not in ('elapsed_secs', 'work_cache', 'vector_policy', 'telemetry')}
    notes = [re.sub(r'; \d+ cached embeddings\.', '; <cache count> cached embeddings.', note)
             for note in result['notes']]
    return dict(items=result['items'], mode=result['mode'], notes=notes, provenance=proof)


def freeze(args):
    corpus = read(args.corpus)
    inventory = corpus_inventory(corpus)
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=False)
    refs = [dict(path=str(Path(args.corpus).resolve().relative_to(ROOT)), sha256=digest(args.corpus))]
    refs += corpus['sources'] + corpus.get('labels', []) + [c['request'] for c in corpus['cases']]
    files = {}
    for ref in refs:
        path = file_ref(ref)
        files[str(path.relative_to(ROOT))] = dict(sha256=digest(path), bytes=path.stat().st_size)
    code = {}
    for directory in ('src', 'tools'):
        for path in sorted((ROOT / directory).glob('*')):
            if path.suffix in ('.rs', '.py', '.cjs', '.sh'):
                code[str(path.relative_to(ROOT))] = digest(path)
    save(root / 'corpus.json', corpus)
    save(root / 'inventory.json', inventory)
    save(root / 'manifest.json', dict(schema='visn-experiment-freeze-v1', created_utc=datetime.now(timezone.utc).isoformat(),
        git_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        git_status=subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True),
        code=code, files=files, host=dict(system=platform.system(), machine=platform.machine(), python=platform.python_version()),
        scope='Hashes of source/labels/requests and code; pre-change code and SQLite backup are separate baseline artifacts.'))
    print(json.dumps(inventory, indent=2))


def run(args):
    profile = load_profile(args.profile)
    corpus = read(args.corpus)
    inventory = corpus_inventory(corpus)
    parsed = urlparse(args.worker)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use a local HTTP worker without credentials')
    selected = [c for c in corpus['cases'] if c['split'] == args.split]
    if not selected:
        raise ValueError('No cases in requested split')
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=False)
    save(root / 'profile.json', profile)
    save(root / 'corpus.json', corpus)
    save(root / 'inventory.json', inventory)
    save(root / 'manifest.json', dict(schema='visn-experiment-run-v1', profile_sha256=digest(args.profile),
        corpus_sha256=digest(args.corpus), split=args.split, profile=profile['id'], repeats=args.repeats,
        code_sha256={name:digest(Path(__file__).with_name(name)) for name in ('general_video_experiment.py','semantic_memory_server.py')},
        status='running', scope='HTTP retrieval replay; worker/model cache state is reported, not inferred as cold.',
        run_id=str(uuid.uuid4()),
        resource_measurement='Worker-reported resource snapshots when available; process-lifetime high-water marks are not request-local peaks. External model-server memory requires separate measurement.'))
    rows = []
    try:
        for case in selected:
            body = read(file_ref(case['request']))
            for repeat in range(args.repeats):
                started = time.monotonic()
                row = dict(case_id=case['id'], repeat=repeat, request_id=str(uuid.uuid4()))
                print(datetime.now(timezone.utc).isoformat(), case['id'], repeat, flush=True)
                try:
                    req = urllib.request.Request(args.worker.rstrip('/') + '/search', json.dumps(body).encode(),
                        {'Content-Type':'application/json','X-Visn-Query-ID':row['request_id'],'X-Visn-Run-ID':read(root/'manifest.json')['run_id']})
                    with urllib.request.urlopen(req, timeout=900) as response:
                        result = json.load(response)
                    save(root / f'{case["id"]}-{repeat}.json', result)
                    validate_result(result, profile, body)
                    telemetry=result['provenance'].get('telemetry')
                    if telemetry:
                        if telemetry['request_id'] != row['request_id'] or telemetry['run_id'] != read(root/'manifest.json')['run_id']:
                            raise ValueError('Worker telemetry correlation mismatch')
                        row['telemetry']=telemetry
                    row.update(status='passed', result=f'{case["id"]}-{repeat}.json', result_sha256=digest(root / f'{case["id"]}-{repeat}.json'))
                    if case.get('label_query_id'):
                        label_ref = next(r for r in corpus['labels'] if r['kind'] == 'assistant_retrieval_pilot')
                        labels = read(file_ref(label_ref))
                        query = next(q for q in labels['queries'] if q['id'] == case['label_query_id'])
                        if query['query'] != body['query']:
                            raise ValueError('Scoring query mismatch')
                        row['development_metrics'] = metrics([r['event_id'] for r in result['items']], query['relevant_event_ids'])
                except Exception as error:
                    row.update(status='failed', error=f'{type(error).__name__}: {error}')
                row['elapsed_secs'] = time.monotonic() - started
                rows.append(row)
                save(root / 'results.json', rows)
        corpus_inventory(corpus)  # Recheck source, label and request bytes after inference.
        inputs = read(root / 'manifest.json')
        if digest(args.corpus) != inputs['corpus_sha256'] or digest(args.profile) != inputs['profile_sha256']:
            raise ValueError('Corpus or profile changed during replay')
        status = 'passed' if all(r['status'] == 'passed' for r in rows) else 'failed'
    except Exception:
        manifest = read(root / 'manifest.json'); manifest['status'] = 'failed'; save(root / 'manifest.json', manifest)
        raise
    manifest = read(root / 'manifest.json'); manifest['status'] = status; save(root / 'manifest.json', manifest)
    save(root / 'decision.json', dict(status='keep_experimental' if status == 'passed' else 'retain_baseline',
        reason='Development integration only; acceptance corpus and full quality/resource gates are required for promotion.'))
    if status != 'passed':
        raise ValueError('Replay failures; inspect results.json')


def compare(args):
    baseline, candidate = Path(args.baseline), Path(args.candidate)
    first, second = read(baseline / 'manifest.json'), read(candidate / 'manifest.json')
    if any(first.get(k) != second.get(k) for k in ('corpus_sha256','split','repeats')) or first.get('status') != 'passed' or second.get('status') != 'passed':
        raise ValueError('Runs are incomplete or use different corpus/split/repeat counts')
    left, right = read(baseline / 'results.json'), read(candidate / 'results.json')
    if not left or not right:
        raise ValueError('Empty runs cannot pass comparison')
    rows = []
    for a, b in zip(left, right, strict=True):
        if (a['case_id'], a['repeat']) != (b['case_id'], b['repeat']):
            raise ValueError('Case pairing mismatch')
        for root, row in ((baseline, a), (candidate, b)):
            if row.get('status') != 'passed' or digest(root / row['result']) != row.get('result_sha256'):
                raise ValueError('Failed or changed raw result')
        x, y = read(baseline / a['result']), read(candidate / b['result'])
        sx, sy = signature(x), signature(y)
        rows.append(dict(case_id=a['case_id'], repeat=a['repeat'], exact=sx == sy,
            ranked_ids_equal=[r['event_id'] for r in x['items']] == [r['event_id'] for r in y['items']],
            development_metrics_equal=a.get('development_metrics') == b.get('development_metrics'),
            baseline_secs=a['elapsed_secs'], candidate_secs=b['elapsed_secs']))
    report = dict(schema='visn-experiment-comparison-v1', rows=rows, exact=all(r['exact'] for r in rows),
        decision='keep_experimental', reason='Development comparison cannot establish generic acceptance or performance significance.')
    path = Path(args.output)
    with path.open('x') as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('freeze', 'inventory', 'run'):
        cmd = sub.add_parser(name); cmd.add_argument('--corpus', required=True)
        if name != 'inventory': cmd.add_argument('--output', required=True)
        if name == 'run':
            cmd.add_argument('--profile', required=True); cmd.add_argument('--split', choices=SPLITS, required=True)
            cmd.add_argument('--worker', required=True); cmd.add_argument('--repeats', type=int, default=2)
    cmd = sub.add_parser('compare')
    for name in ('baseline', 'candidate', 'output'): cmd.add_argument('--' + name, required=True)
    args = p.parse_args()
    if args.command == 'inventory': print(json.dumps(corpus_inventory(read(args.corpus)), indent=2))
    elif args.command == 'freeze': freeze(args)
    elif args.command == 'run':
        if not 1 <= args.repeats <= 100: p.error('repeats must be 1..100')
        run(args)
    else: compare(args)


if __name__ == '__main__':
    main()
