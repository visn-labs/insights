#!/usr/bin/env python3
"""Compare actual old/current media admission on isolated copies of owned evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import urllib.error
import urllib.request

from check_recording_recovery import ROOT,Service,wait_until


class BinaryService(Service):
    def __init__(self,binary,data,output):
        super().__init__(data,output);self.binary=binary
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        self.overrides['VISN_BIND']=f'127.0.0.1:{port}'
    def start(self):
        self.attempt+=1;self.log=self.output/f'server-{self.attempt}.log'
        with self.log.open('wb') as stream:
            self.process=subprocess.Popen([str(self.binary)],cwd=ROOT,env=self.environment(),stdout=stream,stderr=subprocess.STDOUT)
        def ready():
            if self.process.poll() is not None:raise RuntimeError(self.log.read_text())
            for line in self.log.read_text().splitlines():
                try:address=json.loads(line).get('fields',{}).get('address')
                except ValueError:continue
                if address:self.base='http://'+address;return True
        wait_until(ready)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-binary',type=Path,required=True);parser.add_argument('--evidence-run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    origin=args.evidence_run.resolve()/'data'
    original=next(path for path in (origin/'memory/migrations/projection-binding').glob('*.json')
        if not path.name.endswith('.proof.json')).read_bytes()
    job=json.loads(original);job_id=job['id'];event=job['result']['camera_results'][0]['events'][0]
    results=[]
    for name,binary in [('current',ROOT/'target/debug/visn-phase0'),('old',args.old_binary.resolve())]:
        folder=output/name;data=folder/'data';manifests=data/'memory/manifests';manifests.mkdir(parents=True)
        shutil.copytree(origin/'memory'/job_id,data/'memory'/job_id)
        (manifests/(job_id+'.json')).write_bytes(original)
        source=data/'memory'/job_id/'pending/source.mkv'
        expected=hashlib.sha256(source.read_bytes()).hexdigest()
        assert expected==event['privacy']['source']['media_sha256']
        service=BinaryService(binary,data,folder)
        def get():
            try:response=urllib.request.urlopen(service.base+'/api/v1/memory-events/'+event['event_id']+'/source',timeout=30)
            except urllib.error.HTTPError as error:response=error
            with response:return response.status,response.read()
        try:
            service.start();status,body=get()
            assert status==200 and hashlib.sha256(body).hexdigest()==expected,(name,status,body[:500])
            with source.open('ab') as stream:stream.write(b'owned compatibility fault: committed hash no longer matches')
            status,body=get()
            if name=='current':assert status in (400,404,409),(status,body[:500])
            else:assert status==200,(status,body[:500])
            results.append(dict(binary=name,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                original_source_sha256=expected,tampered_status=status,tampered_bytes_served=status==200,
                outcome='rejects_tampered_evidence' if status!=200 else 'unsupported_for_sanitized_v1_rollback'))
        finally:service.stop()
    assert hashlib.sha256((origin/'memory'/job_id/'pending/source.mkv').read_bytes()).hexdigest()==expected
    (output/'results.json').write_text(json.dumps(dict(scope=__doc__,results=results,
        decision='Do not deploy this old binary against sanitized-v1 state; compatible-generation rollback remains the qualified route.'),indent=2)+'\n')
    print(json.dumps(results,indent=2),flush=True)


if __name__=='__main__':main()
