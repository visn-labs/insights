"""Opt-in process-wide residency ownership, bounded FIFO and crash-safe locks.

All participating model processes must use the same local directory. No lease
expires while kernels are running. Warm weights may stay until another owner
requests a turn; unload must succeed before releasing residency ownership.
"""
from contextlib import contextmanager
import fcntl
import gc
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
import urllib.request
from urllib.parse import urlparse
import uuid

LOG = logging.getLogger('visn.resources')


class ResourceBusy(TimeoutError):
    pass


def trim_allocator():
    gc.collect()
    torch = sys.modules.get('torch')
    if torch is not None:
        if torch.cuda.is_initialized(): torch.cuda.empty_cache()
        if torch.backends.mps.is_available(): torch.mps.empty_cache()


class Residency:
    def __init__(self, role, directory=None, timeout=None, limit=None):
        value = os.environ.get('VISN_RESOURCE_DIR') if directory is None else directory
        self.root = Path(value).absolute() if value else None
        self.role = role
        self.timeout = float(os.environ.get('VISN_RESOURCE_WAIT_SECS','180') if timeout is None else timeout)
        self.limit = int(os.environ.get('VISN_RESOURCE_QUEUE_LIMIT','32') if limit is None else limit)
        if not math.isfinite(self.timeout) or not 0 <= self.timeout <= 3600 or not 1 <= self.limit <= 256:
            raise ValueError('Resource wait must be 0..3600 seconds and queue limit 1..256')
        self.fd = None
        self.waiting = False
        if self.root: self.root.mkdir(parents=True,exist_ok=True)

    @contextmanager
    def metadata(self):
        with (self.root/'queue.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            yield

    def live_waiters(self):
        # Call only under metadata lock. An independent flock proves liveness;
        # PID reuse, clock skew and stale heartbeat files cannot steal ownership.
        live=[]
        for path in sorted(self.root.glob('*.ticket')):
            with path.open('r') as handle:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:live.append(path);continue
            path.unlink(missing_ok=True)
        return live

    def contended(self):
        if not self.root or self.fd is None:return False
        with self.metadata():return bool(self.live_waiters())

    def snapshot(self):
        if not self.root:return {'enabled':False}
        with self.metadata():
            waiting=len(self.live_waiters())
            with (self.root/'resident.lock').open('a') as probe:
                try:fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    try:owner=json.loads((self.root/'owner.json').read_text())
                    except (OSError,ValueError):owner=None
                else:owner=None
        return dict(enabled=True,group=hashlib.sha256(str(self.root).encode()).hexdigest()[:12],
                    policy='one_resident_owner_fifo_no_expiry',owner=owner,waiting=waiting,queue_limit=self.limit,
                    this_process_resident=self.fd is not None,this_process_waiting=self.waiting,
                    external_recovery_required=(self.root/'external.json').exists())

    def acquire(self, timeout=None, recover_external=False):
        if not self.root or self.fd is not None:return
        started=time.monotonic();budget=self.timeout if timeout is None else timeout
        handle=None;path=None
        try:
            with self.metadata():
                live=self.live_waiters()
                if len(live)>=self.limit:raise ResourceBusy('Shared resource queue is full')
                sequence=max(time.time_ns(),int(live[-1].name.split('-')[0])+1 if live else 0)
                path=self.root/f'{sequence:020d}-{uuid.uuid4().hex}.ticket'
                handle=path.open('x+');fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                handle.write(json.dumps(dict(pid=os.getpid(),role=self.role)));handle.flush()
            self.waiting=True
            while True:
                with self.metadata():
                    live=self.live_waiters()
                    if live and live[0]==path:
                        candidate=(self.root/'resident.lock').open('a')
                        try:fcntl.flock(candidate,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        except BlockingIOError:candidate.close()
                        else:
                            if not recover_external and (self.root/'external.json').exists():
                                candidate.close()
                                raise ResourceBusy('External model server requires recovery before another model can be admitted')
                            self.fd=candidate
                            (self.root/'owner.json').write_text(json.dumps(dict(pid=os.getpid(),role=self.role,acquired_at=time.time())))
                            LOG.info('RESOURCE_LEASE %s',json.dumps(dict(event='acquired',pid=os.getpid(),role=self.role,wait_secs=time.monotonic()-started)))
                            return
                if time.monotonic()-started>=budget:raise ResourceBusy('Shared model residency admission timed out; coverage was not reduced')
                time.sleep(min(.05,max(0,budget-(time.monotonic()-started))))
        finally:
            self.waiting=False
            if handle:
                with self.metadata():
                    path.unlink(missing_ok=True);handle.close()

    def release(self, unload):
        if self.fd is None:return
        unload()  # Failure retains the OS lease; never advertise freed weights.
        with self.metadata():
            (self.root/'owner.json').unlink(missing_ok=True)
            self.fd.close();self.fd=None
        LOG.info('RESOURCE_LEASE %s',json.dumps(dict(event='released',pid=os.getpid(),role=self.role)))

    def yield_if_contended(self, unload):
        if self.contended():self.release(unload)

    @contextmanager
    def operation(self, unload, timeout=None):
        self.yield_if_contended(unload)
        self.acquire(timeout)
        try:yield
        except BaseException:raise
        else:self.yield_if_contended(unload)


class ManagedLMStudio:
    """Dedicated native-v1 server; refuse pre-existing unmanaged residency.

    external.json survives guardian/client death. Ordinary owners cannot bypass
    it; a replacement guardian must reconcile the same endpoint before grant.
    """
    def __init__(self, lease, endpoint, token=''):
        parsed=urlparse(endpoint)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Managed model API must be loopback HTTP without URL credentials')
        self.lease,self.endpoint,self.token=lease,endpoint.rstrip('/'),token
        self.marker=lease.root/'external.json'

    def call(self,path,body=None):
        req=urllib.request.Request(self.endpoint+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)

    def instances(self):
        value=self.call('/models')
        if not isinstance(value,dict) or not isinstance(value.get('models'),list):raise ValueError('Native model list is invalid')
        if any(not isinstance(model,dict) or not isinstance(model.get('loaded_instances'),list) or
               any(not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] for item in model['loaded_instances']) for model in value['models']):
            raise ValueError('Native instance state is incomplete')
        return [item['id'] for model in value['models'] for item in model.get('loaded_instances',[])]

    def mark(self, **extra):
        with self.marker.open('w') as handle:
            json.dump(dict(endpoint=self.endpoint,pid=os.getpid(),**extra),handle);handle.flush();os.fsync(handle.fileno())

    def completed(self):
        self.mark(uncertain=False)

    def cleanup(self):
        if self.marker.exists() and json.loads(self.marker.read_text()).get('uncertain',False):
            raise RuntimeError('Client ended without completion acknowledgement; verify server quiescence and explicitly recover the group')
        for instance in self.instances():self.call('/models/unload',{'instance_id':instance})
        if self.instances():raise RuntimeError('External model instances remain loaded; resource group is quarantined')
        self.marker.unlink(missing_ok=True)

    def enter(self, recover_uncertain=False):
        self.lease.acquire(recover_external=True)
        if self.marker.exists():
            marker=json.loads(self.marker.read_text())
            if marker['endpoint']!=self.endpoint:raise RuntimeError('External recovery belongs to another endpoint')
            if marker.get('unmanaged') and self.instances():
                raise RuntimeError('Pre-existing unmanaged instances remain; unload them before enabling the dedicated profile')
            if marker.get('uncertain') and not recover_uncertain:
                raise RuntimeError('Uncertain external work requires explicit recovery after verifying server quiescence')
            if recover_uncertain:self.completed()
            self.cleanup()
        else:
            self.mark(unmanaged=True)
            if self.instances():raise RuntimeError('Dedicated managed server must start with no pre-existing loaded model instances')
        self.mark(uncertain=True)


def main():
    # Rust sends endpoint/auth over stdin, never process arguments or public logs.
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recover-external',action='store_true',help='Operator asserts the dedicated server is quiescent; reconcile and clear its recovery marker')
    parser.add_argument('--exec',dest='command',nargs=argparse.REMAINDER,help='Run an isolated model process under inherited residency ownership')
    args=parser.parse_args()
    lease=Residency('isolated_detector' if args.command else 'lmstudio')
    if not lease.root:raise ValueError('Guardian requires VISN_RESOURCE_DIR')
    if args.command:
        import subprocess
        lease.acquire()
        environment=dict(os.environ)
        environment.pop('VISN_RESOURCE_DIR',None)
        try:
            result=subprocess.run(args.command,env=environment,pass_fds=(lease.fd.fileno(),))
        finally:lease.release(lambda:None)
        raise SystemExit(result.returncode)
    configuration=json.loads(sys.stdin.readline())
    managed=ManagedLMStudio(lease,configuration['endpoint'],configuration.get('token',''))
    entered=False
    try:
        managed.enter(recover_uncertain=args.recover_external);entered=True
        if args.recover_external:
            managed.completed()
            managed.cleanup();entered=False
            lease.release(lambda:None)
            print('VISN_RESOURCE_RECOVERED',flush=True)
            return
        print('VISN_RESOURCE_READY',flush=True)
        if sys.stdin.readline().strip()=='completed':managed.completed()
    finally:
        # Failed unload leaves the durable marker, even if this process exits.
        # It blocks every ordinary owner until a matching guardian recovers it.
        if entered:managed.cleanup()
        lease.release(lambda:None)


if __name__=='__main__':main()
