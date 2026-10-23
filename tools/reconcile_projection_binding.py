#!/usr/bin/env python3
"""Offline, evidence-checked binding of a known legacy preparation task.

Dry-run by default. Never invents a missing task ID or rewrites producer history.
"""
from contextlib import contextmanager
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from retrieval_deployments import Registry

FEATURES={'committed-media-hash-admission','saved-privacy-policy','durable-revocation','revocation-source-contract',
    'retrieval-generation-binding','legacy-projection-quarantine','worker-recording-scope-revocation','restore-root-fencing','external-revocation-witness'}


def sha(data):return hashlib.sha256(data).hexdigest()


@contextmanager
def owned(data):
    descriptor=os.open(data/'.writer.lock',os.O_RDWR|os.O_NOFOLLOW)
    try:
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('data_directory_already_owned: stop Rust and its workers first')
        yield
    finally:os.close(descriptor)


def publish(path,body):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.parent/('.migration-'+uuid.uuid4().hex)
    try:
        descriptor=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'wb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:temporary.unlink(missing_ok=True)


def reconcile(data,job_id,generation,revision,expected_sha,request,apply=False):
    data=Path(data).resolve();job_id=str(uuid.UUID(job_id));generation=str(uuid.UUID(generation))
    registry=Registry(data)
    with owned(data),registry.locked():
        marker=data/'storage-contract.json'
        if marker.exists() or marker.is_symlink():
            if marker.is_symlink():raise ValueError('storage contract path invalid')
            contract=json.loads(marker.read_bytes())
            if (not {'schema','version','required_features'}.issubset(contract) or not set(contract).issubset({'schema','version','required_features','witness_required'}) or contract.get('schema')!='visn-storage-contract'
                or type(contract.get('version')) is not int or contract['version']!=1
                or not isinstance(contract.get('required_features'),list)
                or not all(isinstance(feature,str) and feature in FEATURES for feature in contract['required_features'])):
                raise ValueError('storage contract unsupported by migration tool')
            if contract.get('witness_required'):raise ValueError('witnessed binding migration requires native witness reconciliation before offline changes')
        catalog=registry.read();registry.compare(catalog,revision)
        target=catalog['targets'][generation]
        if target.get('state')!='ready':raise ValueError('target is not ready')
        path=data/'memory/manifests'/(job_id+'.json')
        if path.is_symlink() or not path.resolve().is_relative_to(data):raise ValueError('manifest path invalid')
        original=path.read_bytes()
        if sha(original)!=expected_sha:raise ValueError('manifest_revision_conflict')
        job=json.loads(original);intent=job.get('visual_projection') or {}
        if type(job.get('storage_version',1)) is not int or job.get('storage_version',1)!=1:
            raise ValueError('manifest version unsupported by migration tool')
        if job.get('id')!=job_id or job.get('status')!='completed' or job.get('revocation') is not None:
            raise ValueError('recording is not available for reconciliation')
        if intent.get('deployment_generation') is not None:raise ValueError('intent already has a generation')
        task_id=intent.get('task_id')
        if not task_id:raise ValueError('legacy_task_unknown: cannot infer a historical task identity')
        url=target['url'];profile=target['worker_profile']
        if intent.get('worker_fingerprint') not in (None,sha(url.encode())):
            raise ValueError('historical worker URL does not match target')
        registry.validate_inputs([request],profile)
        if request['scope_id']!=job_id:raise ValueError('preparation scope mismatch')
        events={e['event_id']:e for camera in job['result']['camera_results'] for e in camera['events']}
        supplied=request['events']
        if len(events)!=len(supplied) or set(events)!={e['event_id'] for e in supplied}:
            raise ValueError('canonical interval coverage mismatch')
        for event in supplied:
            canonical=events[event['event_id']]
            if (event['start_ms'],event['end_ms'],event['expected_source_sha256']) != (
                canonical['start_ms'],canonical['end_ms'],canonical['privacy']['source']['media_sha256']):
                raise ValueError('canonical interval or source hash mismatch')
            if not Path(event['source_path']).resolve().is_relative_to(data/'memory'/job_id):
                raise ValueError('source is outside recording storage')
        if registry.inspect(url)!=profile:raise ValueError('target profile changed')
        receipt=registry.call(url,'/index-jobs',dict(request,resolve_only=True),profile['fingerprint'])
        if (receipt.get('id')!=task_id or receipt.get('scope_id')!=job_id
            or receipt.get('worker_profile')!=profile or receipt.get('profile_current') is not True
            or receipt.get('embedding')!=profile['manifest']['embedding']
            or receipt.get('total')!=len(supplied)
            or receipt.get('status') not in ('queued','running','completed','failed','cancelled')):
            raise ValueError('saved task is not the exact declared preparation')
        if receipt['status']=='completed' and (receipt.get('completed')!=len(supplied) or receipt.get('failed')!=0):
            raise ValueError('completed receipt has incomplete coverage')
        if registry.inspect(url)!=profile:raise ValueError('target profile changed during reconciliation')
        proof=dict(schema='visn-projection-binding-migration-v1',original_manifest_sha256=expected_sha,
            previous_revision=intent['revision'],deployment_generation=generation,registry_revision=revision,
            at_ms=time.time_ns()//1000000,verification='exact_current_task_contract_not_historical_attestation',receipt=receipt)
        archive=data/'memory/migrations/projection-binding'/(expected_sha+'.json')
        proof_path=archive.with_suffix('.proof.json')
        if archive.is_symlink() or proof_path.is_symlink() or not archive.resolve().is_relative_to(data):
            raise ValueError('migration archive path invalid')
        if proof_path.exists():
            previous=json.loads(proof_path.read_bytes())
            if (previous.get('original_manifest_sha256')!=expected_sha or previous.get('deployment_generation')!=generation
                or previous.get('receipt',{}).get('id')!=task_id or previous.get('receipt',{}).get('worker_profile')!=profile):
                raise ValueError('migration proof conflict')
            proof=previous
        intent.update(binding_migration=proof,deployment_generation=generation,worker_fingerprint=sha(url.encode()),
            revision=str(uuid.uuid4()),state='submitted' if receipt['status'] in ('queued','running') else receipt['status'],
            retry_requested=False,next_attempt_at_ms=0,last_error=None,receipt=receipt)
        body=(json.dumps(job,indent=2,allow_nan=False)+'\n').encode()
        if apply:
            if archive.exists():
                if archive.read_bytes()!=original:raise ValueError('migration archive conflict')
            else:publish(archive,original)
            if not proof_path.exists():publish(proof_path,(json.dumps(proof,indent=2)+'\n').encode())
            if path.read_bytes()!=original:raise ValueError('manifest changed during reconciliation')
            publish(path,body)
        return dict(applied=apply,job_id=job_id,generation=generation,task_id=task_id,
            original_manifest_sha256=expected_sha,new_manifest_sha256=sha(body),verification=proof['verification'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True);parser.add_argument('--job-id',required=True)
    parser.add_argument('--generation',required=True);parser.add_argument('--expect-revision',required=True)
    parser.add_argument('--expect-manifest-sha256',required=True);parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    result=reconcile(args.data_dir,args.job_id,args.generation,args.expect_revision,args.expect_manifest_sha256,
        json.loads(args.request.read_text()),args.apply)
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
