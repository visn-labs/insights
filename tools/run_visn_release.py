#!/usr/bin/env python3
"""Hash-pinned local release preflight/launch; not an adversarial binary attestation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

READER=b'visn-storage-reader-v1'


def binary_identity(path):
    fingerprint=hashlib.sha256();found=False;tail=b''
    with path.open('rb') as stream:
        before=os.fstat(stream.fileno())
        while block:=stream.read(1024*1024):
            fingerprint.update(block);found=found or READER in tail+block;tail=block[-len(READER):]
        after=os.fstat(stream.fileno())
    if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('release_binary_changed_during_hash')
    return fingerprint.hexdigest(),found


def verify(binary,expected_sha,data,recovery=False):
    binary=Path(binary).resolve();data=Path(data).resolve()
    if len(expected_sha)!=64 or any(value not in '0123456789abcdef' for value in expected_sha):
        raise ValueError('provide the approved executable SHA-256')
    digest,reader=binary_identity(binary)
    if digest!=expected_sha:raise ValueError('release_binary_hash_mismatch')
    # Old executables ignore unknown arguments and start a server. Do not probe
    # one unless it contains the expected reader marker and matches the pin.
    if not reader:raise ValueError('release_reader_unsupported: old binary was not executed')
    def probe(*arguments):
        result=subprocess.run([str(binary),*arguments],capture_output=True,text=True,timeout=15)
        if result.returncode:raise ValueError('release_storage_preflight_failed: '+result.stderr[:2000])
        if len(result.stdout)>65536:raise ValueError('release_probe_output_invalid')
        return json.loads(result.stdout)
    capabilities=probe('--storage-capabilities')
    if capabilities.get('reader')!=READER.decode() or 1 not in capabilities.get('storage_versions',[]):
        raise ValueError('release_reader_unsupported')
    if recovery and 'restore-root-fencing' not in capabilities.get('features',[]):raise ValueError('release_recovery_reader_unsupported')
    status=probe('--storage-check-recovery' if recovery else '--storage-check',str(data))
    if status.get('compatible') is not True:raise ValueError('release_storage_incompatible')
    return dict(binary=str(binary),binary_sha256=digest,data_dir=str(data),capabilities=capabilities,compatible=True,recovery_inspection_only=recovery)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--sha256',required=True)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--check',action='store_true',help='Read-only preflight, without starting the API')
    args=parser.parse_args();result=verify(args.binary,args.sha256,args.data_dir)
    print(json.dumps(result,indent=2),flush=True)
    if not args.check:
        if binary_identity(Path(result['binary']))[0]!=args.sha256:raise ValueError('release_binary_changed_before_launch')
        environment=os.environ.copy();environment['VISN_DATA_DIR']=result['data_dir']
        environment.setdefault('VISN_MEDIA_POLICY','sanitized-v1')
        os.execve(result['binary'],[result['binary']],environment)


if __name__=='__main__':main()
