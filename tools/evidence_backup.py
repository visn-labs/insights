#!/usr/bin/env python3
"""Offline canonical-evidence backup/restore with current-state revocation union.

External worker databases are not copied. Restore never overwrites a live root.
"""
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from reconcile_projection_binding import owned,publish
from retrieval_deployments import Registry
from run_visn_release import verify

CUTOVER_LIMIT=64*1024*1024


def encode(value):return (json.dumps(value,sort_keys=True,allow_nan=False)+'\n').encode()
def digest(body):return hashlib.sha256(body).hexdigest()
def file_hash(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):value.update(block)
    return value.hexdigest()


def files(root):
    result={}
    for path in root.rglob('*'):
        if path.is_symlink():raise ValueError('snapshot symlinks are unsupported')
        if path.is_file():result[str(path.relative_to(root))]=dict(sha256=file_hash(path),bytes=path.stat().st_size)
        elif not path.is_dir():raise ValueError('snapshot special file is unsupported')
    return result


def jobs(root):
    result={}
    for path in (root/'memory/manifests').glob('*.json'):
        if path.is_symlink():raise ValueError('manifest symlink')
        value=json.loads(path.read_bytes())
        if path.stem!=value['id']:raise ValueError('manifest identity mismatch')
        result[value['id']]=value
    return result


def lineage(root):
    path=root/'recovery-lineage.json'
    if path.is_symlink():raise ValueError('lineage symlink')
    value=json.loads(path.read_bytes())
    if set(value)!={'schema','id'} or value['schema']!='visn-recovery-lineage-v1':raise ValueError('invalid recovery lineage')
    uuid.UUID(value['id'])
    return value


def current_state(root):
    # The operator must obtain this cut from authoritative current state, not an
    # arbitrary old clone. A digest cannot discover a withheld/deleted tombstone.
    catalog=Registry(root).read()
    value=dict(lineage=lineage(root),manifests={key:digest(encode(value)) for key,value in jobs(root).items()},
        registry=catalog,contract=json.loads((root/'storage-contract.json').read_bytes()),
        activation=json.loads((root/'recovery-activation.json').read_bytes()) if (root/'recovery-activation.json').exists() else None,
        witness=json.loads((root/'revocation-witness.json').read_bytes()) if (root/'revocation-witness.json').exists() else None)
    return digest(encode(value))


@contextmanager
def staging(destination):
    if destination.exists() or destination.is_symlink():raise ValueError('destination already exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.parent/('.evidence-pending-'+uuid.uuid4().hex)
    temporary.mkdir(mode=0o700)
    try:
        yield temporary
        # Every file and directory is durable before the one publication rename.
        directories=[temporary]
        for path in temporary.rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:os.fsync(stream.fileno())
            elif path.is_dir():directories.append(path)
        for directory in sorted(directories,key=lambda value:len(value.parts),reverse=True):
            descriptor=os.open(directory,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        if destination.exists():raise ValueError('destination appeared during publication')
        temporary.rename(destination)
        descriptor=os.open(destination.parent,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:
        if temporary.exists():shutil.rmtree(temporary)


def separate(destination,*sources):
    if any(destination==source or destination.is_relative_to(source) or source.is_relative_to(destination) for source in sources):
        raise ValueError('destination must be separate from source roots')


def backup(data,destination,binary,binary_sha):
    data=Path(data).resolve();destination=Path(destination).absolute()
    separate(destination.resolve(),data)
    registry=Registry(data)
    with owned(data),registry.locked():
        verify(binary,binary_sha,data)
        if 'restore-root-fencing' not in json.loads((data/'storage-contract.json').read_bytes())['required_features']:
            raise ValueError('backup_requires_reader_upgrade')
        identity=data/'recovery-lineage.json'
        if not identity.exists():publish(identity,encode(dict(schema='visn-recovery-lineage-v1',id=str(uuid.uuid4()))))
        cut=current_state(data)
        # Copy canonical evidence and operator state only. SQLite model caches,
        # queues, worker tombstones and their storage IDs remain remote authority.
        with staging(destination) as temporary:
            payload=temporary/'payload';payload.mkdir(mode=0o700)
            if (data/'memory').exists():
                files(data/'memory')
                shutil.copytree(data/'memory',payload/'memory')
            for relative in ['storage-contract.json','recovery-lineage.json','retrieval/deployments.json','revocation-witness.json']:
                source=data/relative
                if source.exists():
                    if source.is_symlink():raise ValueError('canonical state symlink')
                    target=payload/relative;target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(source,target)
            publish(payload/'recovery-activation.json',encode(dict(schema='visn-recovery-activation-v1',state='snapshot')))
            descriptor=dict(schema='visn-evidence-backup-v1',id=str(uuid.uuid4()),created_at_ms=time.time_ns()//1000000,
                lineage=lineage(data),source_state_sha256=cut,binary_sha256=binary_sha,files=files(payload),
                scope='canonical memory, storage contract and retrieval registry; no external SQLite stores')
            body=encode(descriptor);publish(temporary/'snapshot.json',body)
            if current_state(data)!=cut:raise ValueError('current state changed during backup')
        return dict(snapshot=str(destination),snapshot_sha256=digest(body),source_state_sha256=cut,lineage=descriptor['lineage'])


def merge_registry(older,current):
    if older is None:return current
    if current is None:return older
    result=json.loads(json.dumps(current))
    for generation,target in older['targets'].items():
        if generation in result['targets']:
            other=result['targets'][generation]
            if target['url']!=other['url'] or target['worker_profile']!=other['worker_profile']:
                raise ValueError('registry generation conflict')
        else:result['targets'][generation]=target
    by_url={}
    for target in result['targets'].values():
        contract=target['worker_profile']
        if target['url'] in by_url and by_url[target['url']]!=contract:raise ValueError('historical worker URL reused')
        by_url[target['url']]=contract
    if len(result['targets'])>256:raise ValueError('restore registry capacity')
    return result


def activation(journal,state):
    return dict(schema='visn-recovery-activation-v1',transaction=journal['transaction'],state=state,
        destination=journal['destination'],source_root=journal['source_root'],snapshot_sha256=journal['snapshot_sha256'],
        current_state_sha256=journal['current_state_sha256'],
        source_commit_sha256=digest(encode(journal)) if state=='active' else None)


def load_cutover(current,snapshot_sha,current_sha,destination):
    path=current/'recovery-cutover.json'
    if not path.exists() and not path.is_symlink():return None
    if path.is_symlink() or path.stat().st_size>CUTOVER_LIMIT:raise ValueError('cutover journal path invalid')
    value=json.loads(path.read_bytes());uuid.UUID(value['transaction'])
    if (value.get('schema')!='visn-recovery-cutover-v1' or value.get('state') not in ('pending','committed')
        or value.get('source_root')!=str(current) or value.get('destination')!=str(destination)
        or value.get('snapshot_sha256')!=snapshot_sha or value.get('current_state_sha256')!=current_sha
        or not isinstance(value.get('prepared_files'),dict)):
        raise ValueError('cutover_request_conflict: resume the original destination and cuts')
    return value


def prepared_files(root):
    # A refused startup may have touched its owner lease, never canonical state.
    return {key:value for key,value in files(root).items() if key not in ('.writer.lock','retrieval/operator.lock')}


def publish_cutover(current,journal):
    # Reserve the final state size before fencing. A journal we cannot reread
    # must never strand a source at the publication boundary.
    if len(encode(dict(journal,state='committed')))>CUTOVER_LIMIT:raise ValueError('cutover_journal_capacity')
    publish(current/'recovery-cutover.json',encode(journal))


def finish_cutover(current,destination,journal):
    if not destination.is_dir() or destination.is_symlink():raise ValueError('cutover destination missing or invalid')
    proof=destination/'recovery-activation.json'
    if proof.is_symlink():raise ValueError('cutover activation symlink')
    existing=json.loads(proof.read_bytes())
    if journal['state']=='committed' and existing==activation(journal,'active'):
        # The active root may now have newer revocations. Never replay old files
        # over a completed transaction merely because the operator retries it.
        return dict(destination=str(destination),**json.loads((destination/'restore.json').read_bytes()),cutover_already_committed=True,
            destination_superseded=(destination/'recovery-cutover.json').exists())
    if existing!=activation(journal,'pending') or prepared_files(destination)!=journal['prepared_files']:
        raise ValueError('cutover destination changed before activation')
    journal=dict(journal,state='committed')
    publish_cutover(current,journal)
    publish(proof,encode(activation(journal,'active')))
    return dict(destination=str(destination),**json.loads((destination/'restore.json').read_bytes()),cutover_already_committed=False)


def restore(snapshot,snapshot_sha,current,current_sha,destination,binary,binary_sha):
    snapshot=Path(snapshot).resolve();current=Path(current).resolve();destination=Path(destination).resolve()
    separate(destination.resolve(),snapshot,current)
    descriptor_path=snapshot/'snapshot.json'
    if descriptor_path.is_symlink():raise ValueError('snapshot descriptor symlink')
    body=descriptor_path.read_bytes()
    if digest(body)!=snapshot_sha:raise ValueError('snapshot_descriptor_hash_mismatch')
    descriptor=json.loads(body)
    if descriptor.get('schema')!='visn-evidence-backup-v1':raise ValueError('unsupported snapshot schema')
    payload=snapshot/'payload'
    if payload.is_symlink() or files(payload)!=descriptor['files']:raise ValueError('snapshot_payload_mismatch')
    with owned(current),Registry(current).locked(allow_fenced=True):
        journal=load_cutover(current,snapshot_sha,current_sha,destination)
        release=verify(binary,binary_sha,current,recovery=journal is not None)
        if 'restore-root-fencing' not in json.loads((current/'storage-contract.json').read_bytes())['required_features']:
            raise ValueError('restore_requires_reader_upgrade: admit the fencing reader before taking the current cut')
        if lineage(current)!=descriptor['lineage'] or lineage(payload)!=descriptor['lineage']:
            raise ValueError('recovery_lineage_mismatch')
        if current_state(current)!=current_sha:raise ValueError('current_state_hash_mismatch')
        if journal:
            if destination.exists():return finish_cutover(current,destination,journal)
            if journal['state']=='committed':raise ValueError('committed_cutover_destination_missing: do not rebuild a possibly newer lost root')
        latest=jobs(current);restored=jobs(payload)
        revoked={key:value for key,value in restored.items() if value.get('revocation') is not None}
        revoked.update({key:value for key,value in latest.items() if value.get('revocation') is not None})
        with staging(destination) as temporary:
            shutil.copytree(payload,temporary,dirs_exist_ok=True)
            # Recheck the actual copied bytes before applying the revocation union.
            if files(temporary)!=descriptor['files']:raise ValueError('snapshot_changed_during_restore')
            for key,value in revoked.items():
                value=json.loads(json.dumps(value));value.update(status='cancelled',error='recording_revoked',result=None,camera_checkpoints={})
                tombstone=value['revocation']
                tombstone.update(cleanup_state='pending',local_cleanup_state='pending',local_cleanup_error=None,
                    next_attempt_at_ms=0,last_error=None,target_receipts={},registry_revision=None,receipt=None)
                for field in ['metadata_projection','visual_projection']:
                    if value.get(field):
                        value[field]['state']='cancelled'
                        if field=='visual_projection':value[field].update(receipt=None,retry_requested=False)
                restored[key]=value
            for key,value in restored.items():
                if key not in revoked and value.get('visual_projection'):
                    value['visual_projection'].update(state='blocked',last_error='projection_restored_requires_preparation',
                        task_id=None,receipt=None,retry_requested=False,deployment_generation=None,worker_fingerprint=None,attempts=0)
                publish(temporary/'memory/manifests'/(key+'.json'),encode(value))
            catalog=merge_registry(Registry(payload).read(),Registry(current).read())
            if catalog:
                catalog=json.loads(json.dumps(catalog));catalog.update(active=None,revision=str(uuid.uuid4()))
                if len(encode(catalog))>4*1024*1024:raise ValueError('restore registry byte capacity')
                publish(temporary/'retrieval/deployments.json',encode(catalog))
            report=dict(schema='visn-evidence-restore-v1',snapshot_sha256=snapshot_sha,current_state_sha256=current_sha,
                lineage=descriptor['lineage'],revoked_job_ids=sorted(revoked),
                excluded_new_nonrevoked_job_ids=sorted(set(latest)-set(restored)),visual_preparation='explicit_rebuild_required',
                external_cleanup='pending; prior acknowledgements are not copied as completion')
            transaction=journal['transaction'] if journal else str(uuid.uuid4())
            report.update(cutover_transaction=transaction,source_root=str(current))
            contract=json.loads((temporary/'storage-contract.json').read_bytes())
            contract['required_features']=sorted(set(contract['required_features'])|set(release['capabilities']['features']))
            publish(temporary/'storage-contract.json',encode(contract))
            publish(temporary/'restore.json',encode(report))
            journal=dict(schema='visn-recovery-cutover-v1',transaction=transaction,state='pending',
                source_root=str(current),destination=str(destination),snapshot_sha256=snapshot_sha,
                current_state_sha256=current_sha,lineage=descriptor['lineage'],prepared_files={})
            publish(temporary/'recovery-activation.json',encode(activation(journal,'pending')))
            verify(binary,binary_sha,temporary,recovery=True)
            if current_state(current)!=current_sha:raise ValueError('current state changed during restore')
            journal['prepared_files']=prepared_files(temporary)
            # This durable fence precedes publication. Interrupted cutover must
            # roll forward; neither an old root nor a pending new root may serve.
            publish_cutover(current,journal)
        return finish_cutover(current,destination,journal)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--sha256',required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    save=sub.add_parser('backup');save.add_argument('--data-dir',type=Path,required=True);save.add_argument('--destination',type=Path,required=True)
    cut=sub.add_parser('current-cut');cut.add_argument('--data-dir',type=Path,required=True)
    load=sub.add_parser('restore')
    for name in ['snapshot','current-data-dir','destination']:load.add_argument('--'+name,type=Path,required=True)
    load.add_argument('--snapshot-sha256',required=True);load.add_argument('--current-state-sha256',required=True)
    args=parser.parse_args()
    if args.command=='backup':result=backup(args.data_dir,args.destination,args.binary,args.sha256)
    elif args.command=='restore':result=restore(args.snapshot,args.snapshot_sha256,args.current_data_dir,args.current_state_sha256,args.destination,args.binary,args.sha256)
    else:
        with owned(args.data_dir),Registry(args.data_dir).locked():
            verify(args.binary,args.sha256,args.data_dir);result=dict(current_state_sha256=current_state(args.data_dir),lineage=lineage(args.data_dir))
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
