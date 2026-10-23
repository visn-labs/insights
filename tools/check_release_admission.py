#!/usr/bin/env python3
"""Actual executable/launcher/storage rejection tests on owned temporary state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from check_recording_recovery import ROOT,Service,wait_until
from run_visn_release import verify


def snapshot(root):
    return {str(path.relative_to(root)):('link',os.readlink(path)) if path.is_symlink()
        else ('file',hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob('*') if (path.is_file() or path.is_symlink()) and path.name!='.writer.lock'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--old-binary',type=Path,required=True)
    parser.add_argument('--evidence-run',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    binary=ROOT/'target/debug/visn-phase0';digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    archive=next(path for path in (args.evidence_run/'data/memory/migrations/projection-binding').glob('*.json')
        if not path.name.endswith('.proof.json'))
    original=json.loads(archive.read_text());observations=[]
    empty=output/'empty';empty.mkdir();assert verify(binary,digest,empty)['compatible'];assert not list(empty.iterdir())
    for candidate,pin,reason in [(binary,'0'*64,'hash_mismatch'),
        (args.old_binary,hashlib.sha256(args.old_binary.read_bytes()).hexdigest(),'reader_unsupported')]:
        try:verify(candidate,pin,empty);raise AssertionError('invalid release admitted')
        except ValueError as error:assert reason in str(error),error
        assert not list(empty.iterdir());observations.append(dict(case=reason,rejected=True))
    contract=dict(schema='visn-storage-contract',version=1,required_features=[])
    for case in ['future-contract','unknown-feature','broken-contract','contract-link',
        'future-manifest','typed-manifest','broken-manifest','identity-mismatch']:
        data=output/case;manifests=data/'memory/manifests';manifests.mkdir(parents=True)
        # A compatible running job must not be rewritten before a later failure
        # is discovered, irrespective of directory enumeration order.
        running=json.loads(json.dumps(original));running['status']='running'
        (manifests/(running['id']+'.json')).write_text(json.dumps(running))
        marker=data/'storage-contract.json';value=dict(contract)
        if case=='future-contract':value['version']=2
        if case=='unknown-feature':value['required_features']=['unsupported-future-contract']
        if case=='broken-contract':marker.write_text('{broken')
        elif case=='contract-link':
            (data/'linked-contract').write_text(json.dumps(contract));marker.symlink_to(data/'linked-contract')
        else:marker.write_text(json.dumps(value))
        if 'manifest' in case or case=='identity-mismatch':
            bad=json.loads(json.dumps(original));bad['id']=str(uuid.uuid4())
            if case=='future-manifest':bad['storage_version']=2
            if case=='typed-manifest':bad['storage_version']='1'
            path=manifests/((str(uuid.uuid4()) if case=='identity-mismatch' else bad['id'])+'.json')
            path.write_text('{broken' if case=='broken-manifest' else json.dumps(bad))
        before=snapshot(data);service=Service(data,output)
        result=subprocess.run([str(binary)],env=service.environment(),cwd=ROOT,capture_output=True,text=True,timeout=15)
        assert result.returncode!=0 and 'service ready' not in result.stdout,(case,result.stdout,result.stderr)
        assert snapshot(data)==before,case
        try:verify(binary,digest,data);raise AssertionError('launcher admitted incompatible state')
        except ValueError as error:assert 'preflight_failed' in str(error),error
        assert snapshot(data)==before
        observations.append(dict(case=case,rejected=True,no_manifest_changes=True))
    # Exercise the actual exec launcher, not only its read-only helper.
    data=output/'launch-data';service=Service(data,output)
    command=[sys.executable,str(ROOT/'tools/run_visn_release.py'),'--binary',str(binary),
        '--sha256',digest,'--data-dir',str(data)]
    log=output/'launcher.log'
    try:
        with log.open('wb') as stream:service.process=subprocess.Popen(command,env=service.environment(),cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        def ready():
            if service.process.poll() is not None:raise RuntimeError(log.read_text())
            return 'service ready' in log.read_text()
        wait_until(ready)
        persisted=json.loads((data/'storage-contract.json').read_text())
        assert persisted['version']==1 and 'durable-revocation' in persisted['required_features']
    finally:service.stop()
    observations.append(dict(case='qualified-launch',started=True,contract_persisted=True))
    (output/'results.json').write_text(json.dumps(dict(binary_sha256=digest,observations=observations),indent=2)+'\n')
    print(f'Passed {len(observations)} release/contract cases',flush=True)


if __name__=='__main__':main()
