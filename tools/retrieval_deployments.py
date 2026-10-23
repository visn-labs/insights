#!/usr/bin/env python3
"""Stage verified retrieval targets; atomically activate/rollback a local registry."""
from contextlib import contextmanager
import argparse
import fcntl
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from retrieval_profile import digest

SCHEMA='visn-retrieval-deployments-v1'

class Registry:
    def __init__(self,data_dir):
        self.directory=Path(data_dir)/'retrieval'
        self.path=self.directory/'deployments.json'

    def read(self):
        if not self.path.exists():return None
        if self.path.is_symlink() or self.path.stat().st_size>4*1024*1024:raise ValueError('registry capacity or symlink')
        value=json.loads(self.path.read_text())
        if value.get('schema')!=SCHEMA or not 1<=len(value['targets'])<=256:raise ValueError('invalid registry')
        return value

    @contextmanager
    def locked(self,allow_fenced=False):
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.directory.is_symlink():raise ValueError('registry directory symlink')
        with (self.directory/'operator.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if not allow_fenced:
                fence=self.directory.parent/'recovery-cutover.json'
                if fence.exists() or fence.is_symlink():raise ValueError('storage_root_superseded')
                proof=self.directory.parent/'recovery-activation.json'
                if proof.exists() or proof.is_symlink():
                    if proof.is_symlink():raise ValueError('storage_activation_pending_or_mismatched')
                    value=json.loads(proof.read_bytes())
                    if value.get('state')!='active' or Path(value.get('destination','')).resolve()!=self.directory.parent.resolve():
                        raise ValueError('storage_activation_pending_or_mismatched')
            yield

    def publish(self,value):
        data=(json.dumps(value,sort_keys=True,allow_nan=False)+'\n').encode()
        if len(data)>4*1024*1024:raise ValueError('registry capacity')
        temporary=self.directory/('.pending-'+uuid.uuid4().hex)
        try:
            descriptor=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(descriptor,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            temporary.replace(self.path)
            descriptor=os.open(self.directory,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        finally:temporary.unlink(missing_ok=True)

    @staticmethod
    def compare(value,expected):
        actual=value['revision'] if value else None
        if actual!=expected:raise ValueError('registry_revision_conflict')

    @staticmethod
    def call(url,path,body=None,fingerprint=None):
        headers={'Content-Type':'application/json'}
        if fingerprint:headers['X-Visn-Expected-Profile']=fingerprint
        request=urllib.request.Request(url.rstrip('/')+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        with urllib.request.urlopen(request,timeout=300) as response:
            data=response.read(8*1024*1024+1)
            if len(data)>8*1024*1024:raise ValueError('worker response capacity')
            return json.loads(data)

    def inspect(self,url):
        deadline=time.monotonic()+300
        while True:
            try:return self.call(url,'/profile')
            except urllib.error.HTTPError as error:
                if error.code not in (429,503) or time.monotonic()>=deadline:raise
                time.sleep(.2)

    @staticmethod
    def validate_url(url):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid worker URL')

    @staticmethod
    def validate_inputs(requests,profile):
        if not isinstance(requests,list) or not 1<=len(requests)<=128:raise ValueError('provide 1..128 declared scope preparations')
        for body in requests:
            if not isinstance(body,dict) or not isinstance(body.get('scope_id'),str) or not 1<=len(body['scope_id'])<=128 or not isinstance(body.get('events'),list) or not 1<=len(body['events'])<=10000:
                raise ValueError('invalid preparation scope')
            for event in body['events']:
                sha=event.get('expected_source_sha256','') if isinstance(event,dict) else ''
                if not isinstance(sha,str) or len(sha)!=64 or any(character not in '0123456789abcdef' for character in sha):
                    raise ValueError('staging requires a committed SHA-256 for every source')
        if profile.get('fingerprint')!=digest(profile.get('manifest')) or profile['manifest'].get('schema')!='visn-retrieval-profile-v1':
            raise ValueError('invalid worker profile fingerprint')
        storage=profile['manifest'].get('index_storage','')
        if len(storage)!=64 or any(character not in '0123456789abcdef' for character in storage):raise ValueError('invalid storage identity')

    def verify(self,url,requests,profile=None,allow_fixture=False):
        self.validate_url(url)
        current=self.inspect(url)
        self.validate_inputs(requests,current)
        if profile is not None and current!=profile:raise ValueError('worker profile changed since staging')
        if not allow_fixture and current['manifest']['qualification']!='integration_candidate_not_quality_acceptance':
            raise ValueError('unsealed fixture cannot be activated as an actual model')
        if not 1<=len(requests)<=128:raise ValueError('provide 1..128 declared scope preparations')
        receipts=[]
        for body in requests:
            deadline=time.monotonic()+300
            while True:
                try:receipt=self.call(url,'/index-jobs',body,current['fingerprint']);break
                except urllib.error.HTTPError as error:
                    if error.code not in (429,503) or time.monotonic()>=deadline:raise
                    time.sleep(.2)
            task_id=receipt['id']
            if receipt['status']=='cancelled':
                receipt=self.call(url,'/index-jobs/'+task_id+'/retry',{},current['fingerprint'])
            while receipt['status'] in ('queued','running'):
                if time.monotonic()>=deadline:raise TimeoutError('candidate preparation did not finish')
                time.sleep(.2)
                try:receipt=self.call(url,'/index-jobs/'+receipt['id'],fingerprint=current['fingerprint'])
                except urllib.error.HTTPError as error:
                    if error.code not in (429,503):raise
            if receipt['id']!=task_id or receipt['scope_id']!=body['scope_id'] or receipt['total']!=len(body['events']):
                raise ValueError('preparation receipt scope, task or coverage mismatch')
            if receipt['status']!='completed' or receipt['profile_current'] is not True or receipt['total']<=0 or receipt['completed']!=receipt['total'] or receipt['failed']:
                raise ValueError('candidate index is incomplete')
            if receipt.get('worker_profile')!=current:raise ValueError('preparation receipt profile mismatch')
            receipts.append(receipt)
        if self.inspect(url)!=current:raise ValueError('worker profile changed during preparation')
        return current,receipts

    def stage(self,url,requests,expected_revision=None,allow_fixture=False):
        # Register before any footage is indexed, including failed/interrupted stages.
        self.validate_url(url)
        profile=self.inspect(url)
        self.validate_inputs(requests,profile)
        if not allow_fixture and profile['manifest']['qualification']!='integration_candidate_not_quality_acceptance':
            raise ValueError('unsealed fixture cannot be staged as an actual model')
        with self.locked():
            value=self.read();self.compare(value,expected_revision)
            value=value or dict(schema=SCHEMA,revision=str(uuid.uuid4()),active=None,targets={},history=[])
            if any(target['url']==url.rstrip('/') and target['worker_profile']['manifest']['index_storage']!=profile['manifest']['index_storage'] for target in value['targets'].values()):
                raise ValueError('worker URL cannot be reused for another storage root while historical cleanup remains required')
            if any(target['url']==url.rstrip('/') and target['worker_profile']['fingerprint']!=profile['fingerprint'] for target in value['targets'].values()):
                raise ValueError('worker URL already names another immutable profile; stage on a separate endpoint')
            if len(value['targets'])>=256:raise ValueError('registry target capacity')
            generation=str(uuid.uuid4())
            value['targets'][generation]=dict(url=url.rstrip('/'),worker_profile=profile,prepared=[],state='staging',
                verification_requests=requests,decision='experimental',allow_fixture=allow_fixture)
            value['revision']=str(uuid.uuid4());self.publish(value)
        try:
            profile,receipts=self.verify(url,requests,profile,allow_fixture)
        except BaseException as error:
            with self.locked():
                latest=self.read();latest['targets'][generation]['state']='failed'
                latest['targets'][generation]['error']=type(error).__name__
                latest['revision']=str(uuid.uuid4());self.publish(latest)
            raise
        with self.locked():
            latest=self.read();latest['targets'][generation].update(prepared=receipts,state='ready')
            latest['revision']=str(uuid.uuid4());self.publish(latest)
        return latest,generation

    def activate(self,generation,expected_revision):
        with self.locked():
            value=self.read();self.compare(value,expected_revision)
            target=value['targets'][generation]
            if target.get('state','ready')!='ready':raise ValueError('candidate is not ready')
            profile,receipts=self.verify(target['url'],target['verification_requests'],target['worker_profile'],target.get('allow_fixture',False))
            if len(value['history'])>=1024:raise ValueError('registry activation history capacity')
            value['history'].append(dict(previous=value['active'],selected=generation,at_ms=time.time_ns()//1_000_000,
                worker_profile=profile['fingerprint'],prepared=receipts))
            value['active']=generation;value['revision']=str(uuid.uuid4());self.publish(value)
            return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('status')
    stage=sub.add_parser('stage');stage.add_argument('--url',required=True);stage.add_argument('--requests',type=Path,required=True)
    stage.add_argument('--expect-revision');stage.add_argument('--allow-fixture',action='store_true')
    activate=sub.add_parser('activate');activate.add_argument('generation');activate.add_argument('--expect-revision',required=True)
    args=parser.parse_args();registry=Registry(args.data_dir)
    if args.command=='status':result=registry.read()
    elif args.command=='stage':
        value,generation=registry.stage(args.url,json.loads(args.requests.read_text()),args.expect_revision,args.allow_fixture)
        result=dict(revision=value['revision'],staged_generation=generation,active=value['active'])
    else:result=registry.activate(args.generation,args.expect_revision)
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
