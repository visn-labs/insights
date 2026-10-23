#!/usr/bin/env python3
"""Atomic cutover/rollback through Rust; fixtures by default, --actual uses cached Qwen."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import shutil
import subprocess
import sys
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('HF_HOME',str(ROOT/'.retrieval/huggingface'))
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
from check_retrieval import Models,write_video
from check_recording_recovery import Service
from retrieval_deployments import Registry
from reconcile_projection_binding import reconcile,sha
from evidence_backup import backup,restore,current_state,file_hash
from run_visn_release import verify
from semantic_memory_server import Retrieval,handler_for
from workflow_indexing import IndexJobs


class Worker:
    def __init__(self,root,policy,model,actual=False,port=0):
        options=SimpleNamespace(data_dir=str(root),frames=2,frame_size=64,vector_policy=policy,
            interactive_wait_secs=120,disable_telemetry=True,qwen_source=str(ROOT/'.retrieval/qwen'),
            embedding_model='Qwen/Qwen3-VL-Embedding-2B',embedding_revision='main',
            reranker_model='Qwen/Qwen3-VL-Reranker-2B',reranker_revision='main',device='cpu',keep_models_loaded=False)
        self.actual=actual;self.arm=False;self.entered=threading.Event();self.release=threading.Event()
        self.drop_scope=None;self.dropped_receipt=None
        self.service=Retrieval(options,None if actual else Models())
        if not actual:self.service.models.version=json.dumps(dict(model=model))
        self.service.indexer=IndexJobs(self.service);self.service.indexer.start()
        owner=self;base=handler_for(self.service)
        class Handler(base):
            def log_message(self,*args):pass
            def reply(self,status,body):
                if self.path=='/index-jobs' and status==202 and body.get('scope_id')==owner.drop_scope:
                    owner.dropped_receipt=body
                    self.close_connection=True
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close()
                    return
                if owner.arm and self.path=='/search' and status==200:
                    owner.arm=False
                    if owner.actual:owner.service.release_models()
                    owner.entered.set()
                    if not owner.release.wait(90):raise TimeoutError('owned cutover response barrier expired')
                return super().reply(status,body)
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def unload(self):
        if self.actual:self.service.release_models()
    def close(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        self.service.indexer.close();self.service.resources.release(self.service.release_models)
        self.service.db.close();self.service.revocations.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--actual',action='store_true')
    parser.add_argument('--lost-ack',action='store_true',help='Drop every initial preparation acknowledgement until cutover')
    parser.add_argument('--replace-pending',action='store_true',help='Explicitly replace the delayed old intent under B')
    parser.add_argument('--legacy-binding',action='store_true',help='Quarantine and reconcile a saved legacy task before cutover')
    parser.add_argument('--backup-restore',action='store_true',help='Restore an older canonical snapshot with current revocations')
    parser.add_argument('--witness',action='store_true',help='Require an independently initialized revocation witness')
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    if args.legacy_binding and (args.lost_ack or args.replace_pending):parser.error('legacy-binding requires the known-task case')
    if args.witness and not args.backup_restore:parser.error('witness validation requires backup-restore')
    if args.actual:
        import torch;torch.set_num_threads(2)
    data=output/'data';data.mkdir();source=output/'source.mp4';write_video(source)
    hits=[]
    class Source(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):hits.append(self.path);super().do_GET()
    camera=ThreadingHTTPServer(('127.0.0.1',0),partial(Source,directory=str(output)))
    camera_thread=threading.Thread(target=camera.serve_forever,daemon=True);camera_thread.start()
    workers=[];a=Worker(data,'legacy-renormalize-v1','fixture-a',args.actual);workers.append(a)
    b=Worker(output,'canonical-unit-v2','fixture-b',args.actual);workers.append(b)
    rust=Service(data,output);rust.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/event_memory_runner.py',
        VISN_RETRIEVAL_URL=a.url,VISN_RETRIEVAL_TIMEOUT_SECS='120')
    witness_evidence=None
    if args.witness:
        witness=output/'independent-witness'
        initialized=json.loads(subprocess.check_output([str(ROOT/'target/debug/visn-phase0'),'--init-revocation-witness',str(witness)],cwd=ROOT,text=True))
        rust.overrides.update(VISN_REVOCATION_WITNESS_DIR=str(witness),VISN_REVOCATION_WITNESS_INSTANCE=initialized['instance'])
        witness_evidence=dict(instance=initialized['instance'],directory=str(witness))
    registry=Registry(data);observations=[];started=time.monotonic();backup_result=None;restore_result=None
    def phase(message):print(f'[{time.monotonic()-started:.1f}s] {message}',flush=True)
    def api(path,body=None,status=200):
        req=urllib.request.Request(rust.base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        try:response=urllib.request.urlopen(req,timeout=180)
        except urllib.error.HTTPError as error:response=error
        with response:
            raw=response.read();value=json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else dict(bytes=len(raw))
            assert response.status==status,(path,response.status,value)
        observations.append(dict(path=path,status=response.status));return value
    def wait(predicate,seconds=180):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.1)
        raise TimeoutError('owned deployment state not reached')
    try:
        phase('Record real sanitized evidence through Rust');rust.start()
        job=api('/api/v1/memory-jobs',dict(name='Atomic cutover',cameras=[dict(camera_id='junction',
            live_url=f'http://127.0.0.1:{camera.server_port}/source.mp4')],monitor_duration_secs=1,
            observer_fps=2,long_memory_enabled=False,vlm_enabled=False),202)
        route='/api/v1/memory-jobs/'+job['id']
        def complete():
            value=api(route);assert value['status']!='failed',value.get('error')
            return value if value['status']=='completed' else None
        saved=wait(complete)
        capture_requests=len(hits);assert capture_requests>0
        event=saved['result']['camera_results'][0]['events'][0]
        source_path=data/'memory'/job['id']/'junction/source.mkv'
        requests=[dict(scope_id=job['id'],events=[dict(event_id=event['event_id'],source_path=str(source_path),
            expected_source_sha256=event['privacy']['source']['media_sha256'],start_ms=event['start_ms'],end_ms=event['end_ms'])])]
        phase('Stage both complete index/profile targets; activate A')
        catalog,ga=registry.stage(a.url,requests,allow_fixture=not args.actual)
        a.unload()
        catalog,gb=registry.stage(b.url,requests,catalog['revision'],allow_fixture=not args.actual)
        b.unload()
        catalog=registry.activate(ga,catalog['revision'])
        # The registry selection must override the startup pin as well as its URL.
        rust.stop()
        rust.overrides['VISN_RETRIEVAL_EXPECTED_PROFILE']=catalog['targets'][ga]['worker_profile']['fingerprint']
        rust.start()
        query=dict(query='red background',job_id=job['id'],semantic_enabled=True,rerank_enabled=False,vlm_enabled=False)
        phase('Queue another real recording under A before cutover')
        a.service.indexer.stop.set();a.service.indexer.thread.join(timeout=5)
        assert not a.service.indexer.thread.is_alive()
        second_job=api('/api/v1/memory-jobs',dict(name='Pending at cutover',cameras=[dict(camera_id='pending',
            live_url=f'http://127.0.0.1:{camera.server_port}/source.mp4')],monitor_duration_secs=1,
            observer_fps=2,long_memory_enabled=False,vlm_enabled=False),202)
        second_route='/api/v1/memory-jobs/'+second_job['id']
        if args.lost_ack:a.drop_scope=second_job['id']
        def submitted():
            value=api(second_route)
            if args.lost_ack:
                intent=value.get('visual_projection') or {}
                return value if a.dropped_receipt and intent.get('attempts',0)>0 and intent.get('task_id') is None else None
            return value if (value.get('visual_projection') or {}).get('state')=='submitted' else None
        pending_record=wait(submitted)
        pending_task=a.dropped_receipt['id'] if args.lost_ack else pending_record['visual_projection']['task_id']
        assert pending_record['visual_projection']['deployment_generation']==ga
        migration=None
        if args.legacy_binding:
            phase('Quarantine an attempted legacy intent; reconcile only its exact saved task offline')
            rust.stop()
            manifest=data/'memory/manifests'/(second_job['id']+'.json')
            legacy=json.loads(manifest.read_text());legacy['visual_projection']['deployment_generation']=None
            legacy['visual_projection']['next_attempt_at_ms']=0
            manifest.write_text(json.dumps(legacy));rust.start()
            quarantined=wait(lambda:(value if (value:=api(second_route))['visual_projection']['state']=='blocked' else None))
            assert quarantined['visual_projection']['deployment_generation'] is None
            assert quarantined['visual_projection']['last_error']=='projection_legacy_binding_required'
            api(second_route+'/visual-index',{},409)
            event=quarantined['result']['camera_results'][0]['events'][0]
            migration_request=dict(scope_id=second_job['id'],events=[dict(event_id=event['event_id'],
                source_path=str(data/'memory'/second_job['id']/'pending/source.mkv'),
                expected_source_sha256=event['privacy']['source']['media_sha256'],start_ms=event['start_ms'],end_ms=event['end_ms'])])
            original=manifest.read_bytes()
            try:reconcile(data,second_job['id'],ga,catalog['revision'],sha(original),migration_request);raise AssertionError('live data ownership ignored')
            except ValueError as error:assert 'already_owned' in str(error)
            rust.stop();original_job=json.loads(manifest.read_text())
            original_job['operator_extension']={'preserve':'unknown fields survive offline migration'}
            manifest.write_text(json.dumps(original_job));original=manifest.read_bytes()
            marker=data/'storage-contract.json';contract=marker.read_bytes()
            future=json.loads(contract);future['version']=2;marker.write_text(json.dumps(future))
            try:reconcile(data,second_job['id'],ga,catalog['revision'],sha(original),migration_request,True);raise AssertionError('future storage migrated')
            except ValueError as error:assert 'storage contract unsupported' in str(error)
            assert manifest.read_bytes()==original
            marker.write_bytes(contract)
            with a.service.indexer.connect() as db:before_count=db.execute('SELECT count(*) FROM jobs').fetchone()[0]
            for gen,revision,digest in [(ga,catalog['revision'],'0'*64),(ga,str(__import__('uuid').uuid4()),sha(original)),(gb,catalog['revision'],sha(original))]:
                try:reconcile(data,second_job['id'],gen,revision,digest,migration_request,True);raise AssertionError('invalid migration accepted')
                except ValueError:pass
                assert manifest.read_bytes()==original
            missing=json.loads(original);missing['visual_projection']['task_id']=None
            manifest.write_text(json.dumps(missing));unknown=manifest.read_bytes()
            try:reconcile(data,second_job['id'],ga,catalog['revision'],sha(unknown),migration_request,True);raise AssertionError('unknown historical task inferred')
            except ValueError as error:assert 'legacy_task_unknown' in str(error)
            manifest.write_bytes(original)
            dry=reconcile(data,second_job['id'],ga,catalog['revision'],sha(original),migration_request)
            assert not dry['applied'] and manifest.read_bytes()==original
            migration=reconcile(data,second_job['id'],ga,catalog['revision'],sha(original),migration_request,True)
            updated=json.loads(manifest.read_text())
            assert updated['result']==original_job['result'] and updated['request']==original_job['request']
            assert updated['operator_extension']==original_job['operator_extension']
            assert (data/'memory/migrations/projection-binding'/(sha(original)+'.json')).read_bytes()==original
            assert json.loads((data/'memory/migrations/projection-binding'/(sha(original)+'.proof.json')).read_text())==updated['visual_projection']['binding_migration']
            with a.service.indexer.connect() as db:assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==before_count
            rust.start();roundtrip=api(second_route)['visual_projection']
            assert roundtrip['deployment_generation']==ga and roundtrip['binding_migration']['original_manifest_sha256']==sha(original)
        if args.replace_pending:
            # Keep automatic retirement out of this case: exercise the explicit
            # replacement path after restart, with the old acknowledgement lost.
            rust.stop()
            manifest=data/'memory/manifests'/(second_job['id']+'.json')
            saved_pending=json.loads(manifest.read_text())
            saved_pending['visual_projection']['next_attempt_at_ms']=int(time.time()*1000)+300000
            manifest.write_text(json.dumps(saved_pending))
            rust.start()
        capture_requests=len(hits)
        phase('Retain a failed inactive stage; never activate incomplete coverage')
        bad=json.loads(json.dumps(requests[0]));bad['scope_id']='failed-stage';bad['events'][0]['expected_source_sha256']='0'*64
        before_targets=set(catalog['targets'])
        try:registry.stage(a.url,[requests[0],bad],catalog['revision'],allow_fixture=not args.actual);raise AssertionError('bad stage accepted')
        except urllib.error.HTTPError as error:assert error.code==400,error.code
        catalog=registry.read();failed_generation=(set(catalog['targets'])-before_targets).pop()
        assert catalog['targets'][failed_generation]['state']=='failed' and catalog['active']==ga
        try:registry.activate(failed_generation,catalog['revision']);raise AssertionError('failed stage activated')
        except ValueError as error:assert 'not ready' in str(error)
        phase('Hold an actual A response, activate B, then finish both on their declared generations')
        a.arm=True;pending={}
        def search_a():
            try:pending['response']=api('/api/v1/memory-query',dict(query,query='hold'))
            except BaseException as error:pending['error']=repr(error)
        invocation=threading.Thread(target=search_a);invocation.start()
        assert a.entered.wait(90)
        catalog=registry.activate(gb,catalog['revision'])
        a.drop_scope=None
        selected_b=api('/api/v1/memory-query',query)
        assert selected_b['retrieval_mode']=='hybrid_rrf',selected_b
        assert selected_b['retrieval_provenance']['deployment_generation']==gb
        a.release.set();invocation.join(timeout=120)
        assert not invocation.is_alive() and not pending.get('error'),pending
        assert pending['response']['retrieval_provenance']['deployment_generation']==ga
        if args.replace_pending:
            phase('Explicit replacement must retire the old task before changing its durable intent')
            a.drop_scope=second_job['id']
            if args.lost_ack:
                api(second_route+'/visual-index',{},503)
                unchanged=api(second_route)['visual_projection']
                assert unchanged['revision']==pending_record['visual_projection']['revision']
                assert unchanged['deployment_generation']==ga and unchanged['task_id'] is None
            a.drop_scope=None
            api(second_route+'/visual-index',{},202)
            retired=wait(lambda:(value if (value:=api(second_route))['visual_projection']['deployment_generation']==gb
                and value['visual_projection']['state'] in ('submitted','completed') else None))
        else:
            retired=wait(lambda:(value if (value:=api(second_route))['visual_projection']['state']=='cancelled' else None))
        assert a.service.indexer.get(pending_task)['status']=='cancelled'
        api(second_route+'/visual-index/'+pending_task+'/retry?deployment_generation='+ga,{},409)
        phase('Rollback, reject stale operator revision, preserve selection across Rust restart')
        b.unload();old_revision=catalog['revision'];catalog=registry.activate(ga,old_revision)
        try:registry.activate(gb,old_revision);raise AssertionError('stale revision was accepted')
        except ValueError as error:assert 'registry_revision_conflict' in str(error)
        a.service.indexer.stop.clear();a.service.indexer.start()
        api(second_route+'/visual-index/'+pending_task+'/retry?deployment_generation='+ga,{})
        wait(lambda:(value if (value:=api(second_route))['visual_projection']['state']=='completed' else None))
        restored=api('/api/v1/memory-query',query)
        assert restored['retrieval_provenance']['deployment_generation']==ga
        rust.stop();rust.start()
        assert api('/api/v1/memory-query',query)['retrieval_provenance']['deployment_generation']==ga
        if args.backup_restore:
            phase('Quiesce and snapshot canonical evidence before revocation')
            binary=ROOT/'target/debug/visn-phase0';binary_sha=file_hash(binary)
            try:backup(data,output/'live-backup-denied',binary,binary_sha);raise AssertionError('live backup admitted')
            except ValueError as error:assert 'already_owned' in str(error)
            assert not (output/'live-backup-denied').exists()
            rust.stop()
            backup_result=backup(data,output/'snapshot',binary,binary_sha)
            try:verify(binary,binary_sha,output/'snapshot/payload');raise AssertionError('snapshot payload was launchable')
            except ValueError as error:assert 'activation_pending' in str(error)
            rust.start()
        phase('Tampered registry fails closed; exact restore recovers without recapture')
        original=registry.path.read_bytes();registry.path.write_text('{}')
        api('/api/v1/memory-query',query,400);registry.path.write_bytes(original)
        assert api('/api/v1/memory-query',query)['retrieval_provenance']['deployment_generation']==ga
        phase('Reject URL reuse and refuse cleanup acknowledgement from a different storage root')
        old_port=a.server.server_port;a.close();workers.remove(a)
        substitute=Worker(output,'legacy-renormalize-v1','fixture-a',args.actual,port=old_port);workers.append(substitute)
        try:registry.stage(substitute.url,requests,catalog['revision'],allow_fixture=not args.actual);raise AssertionError('storage URL reuse accepted')
        except ValueError as error:assert 'another storage root' in str(error)
        assert registry.read()['revision']==catalog['revision']
        if not args.backup_restore:api(second_route+'/revoke',{})
        if args.witness:
            unavailable=output/'witness-unavailable';witness.rename(unavailable)
            api(route+'/revoke',{},503)
            api('/api/v1/memory-query',query,410)
            assert not (unavailable/'records'/(job['id']+'.json')).exists()
            unavailable.rename(witness)
        api(route+'/revoke',{})
        def wrong_storage():
            value=api(route)
            return value if value['revocation']['cleanup_state']=='blocked' else None
        blocked=wait(wrong_storage)
        assert blocked['revocation']['last_error']=='revocation_storage_changed',blocked['revocation']
        assert len(blocked['revocation']['target_receipts'])==1,blocked['revocation']
        substitute.close();workers.remove(substitute)
        a=Worker(data,'legacy-renormalize-v1','fixture-a',args.actual,port=old_port);workers.append(a)
        phase('Revoke across both active and historical worker storage roots')
        if not args.backup_restore:api(second_route+'/revoke',{})
        api(route+'/revoke',{})
        revoked=wait(lambda:(value if (value:=api(route))['revocation']['cleanup_state']=='completed' else None))
        if args.witness:
            immutable_record=witness/'records'/(job['id']+'.json');immutable_hash=file_hash(immutable_record)
        assert len(revoked['revocation']['target_receipts'])==2,revoked['revocation']
        assert revoked['revocation']['registry_revision']==catalog['revision']
        api('/api/v1/memory-query',query,410)
        for worker in [a,b]:
            req=urllib.request.Request(worker.url+'/search',data=json.dumps(dict(query='red',events=requests[0]['events'],lexical_ids=[],limit=1)).encode(),headers={'Content-Type':'application/json'})
            try:urllib.request.urlopen(req,timeout=30);raise AssertionError('revoked worker evidence accepted')
            except urllib.error.HTTPError as error:assert error.code==410,error.code
        assert len(hits)==capture_requests,hits
        if args.backup_restore:
            phase('Restore the older snapshot while preserving the newer revocation')
            rust.stop();cut=current_state(data)
            destination=output/'restored-data'
            for snapshot_sha,current_sha in [('0'*64,cut),(backup_result['snapshot_sha256'],'0'*64)]:
                try:restore(output/'snapshot',snapshot_sha,data,current_sha,destination,binary,binary_sha);raise AssertionError('bad restore cut admitted')
                except ValueError:pass
                assert not destination.exists()
            lineage_path=data/'recovery-lineage.json';original_lineage=lineage_path.read_bytes()
            changed_lineage=json.loads(original_lineage);changed_lineage['id']=str(__import__('uuid').uuid4())
            lineage_path.write_text(json.dumps(changed_lineage))
            try:restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,destination,binary,binary_sha);raise AssertionError('unrelated current lineage admitted')
            except ValueError as error:assert 'lineage_mismatch' in str(error)
            lineage_path.write_bytes(original_lineage)
            payload=output/'snapshot/payload/memory'/second_job['id']/'pending/source.mkv'
            original_media=payload.read_bytes();payload.write_bytes(original_media+b'owned snapshot tamper')
            try:restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,destination,binary,binary_sha);raise AssertionError('tampered snapshot admitted')
            except ValueError as error:assert 'payload_mismatch' in str(error)
            payload.write_bytes(original_media)
            occupied=output/'occupied-restore';occupied.mkdir();(occupied/'keep').write_text('untouched')
            try:restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,occupied,binary,binary_sha);raise AssertionError('occupied destination overwritten')
            except ValueError as error:assert 'already exists' in str(error)
            assert (occupied/'keep').read_text()=='untouched'
            import evidence_backup as backup_module
            original_limit=backup_module.CUTOVER_LIMIT;backup_module.CUTOVER_LIMIT=128
            try:
                try:restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,destination,binary,binary_sha);raise AssertionError('oversized journal fenced source')
                except ValueError as error:assert 'journal_capacity' in str(error)
            finally:backup_module.CUTOVER_LIMIT=original_limit
            assert not destination.exists() and not (data/'recovery-cutover.json').exists() and current_state(data)==cut
            # Kill an owned restore process after its staged body is complete,
            # before its publication rename. No production fault switch exists.
            crash_code='''
import os,signal,sys
from contextlib import contextmanager
sys.path.insert(0,'tools')
import evidence_backup as backup
original=backup.staging
@contextmanager
def crash(destination):
    with original(destination) as temporary:
        yield temporary
        os.kill(os.getpid(),signal.SIGKILL)
backup.staging=crash
backup.restore(*sys.argv[1:])
'''
            crash=subprocess.run([sys.executable,'-c',crash_code,str(output/'snapshot'),backup_result['snapshot_sha256'],
                str(data),cut,str(destination),str(binary),binary_sha],cwd=ROOT,capture_output=True,text=True,timeout=60)
            assert crash.returncode==-9,(crash.returncode,crash.stdout,crash.stderr)
            assert not destination.exists() and current_state(data)==cut
            assert list(output.glob('.evidence-pending-*'))
            def blocked_root(root,reason):
                try:verify(binary,binary_sha,root);raise AssertionError('fenced/pending root passed preflight')
                except ValueError as error:assert reason in str(error),error
                environment=rust.environment();environment['VISN_DATA_DIR']=str(root)
                result=subprocess.run([str(binary)],cwd=ROOT,env=environment,capture_output=True,text=True,timeout=15)
                assert result.returncode!=0 and 'service ready' not in result.stdout,(result.returncode,result.stdout)
                assert reason in result.stderr,(reason,result.stderr)
            blocked_root(data,'storage_root_superseded')
            first_transaction=json.loads((data/'recovery-cutover.json').read_bytes())['transaction']
            try:restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,output/'wrong-cutover',binary,binary_sha);raise AssertionError('pending cutover retargeted')
            except ValueError as error:assert 'cutover_request_conflict' in str(error)
            try:
                with Registry(data).locked():raise AssertionError('old registry remained writable')
            except ValueError as error:assert 'superseded' in str(error)
            for phase_name,hook in [('published_pending',"backup.finish_cutover=lambda *args: os.kill(os.getpid(),signal.SIGKILL)"),
                ('committed_pending',"""
original_publish=backup.publish
def crash_publish(path,body):
    if path.name=='recovery-activation.json' and json.loads(body).get('state')=='active':os.kill(os.getpid(),signal.SIGKILL)
    return original_publish(path,body)
backup.publish=crash_publish
""")]:
                code="import os,signal,sys,json;sys.path.insert(0,'tools');import evidence_backup as backup\n"+hook+"\nbackup.restore(*sys.argv[1:])"
                result=subprocess.run([sys.executable,'-c',code,str(output/'snapshot'),backup_result['snapshot_sha256'],str(data),cut,str(destination),str(binary),binary_sha],cwd=ROOT,capture_output=True,text=True,timeout=60)
                assert result.returncode==-9,(phase_name,result.stdout,result.stderr)
                journal=json.loads((data/'recovery-cutover.json').read_bytes())
                assert journal['transaction']==first_transaction and journal['state']==('pending' if phase_name=='published_pending' else 'committed')
                blocked_root(data,'storage_root_superseded');blocked_root(destination,'storage_activation_pending')
            restore_result=restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,destination,binary,binary_sha)
            restore_result['crash_before_publish']='SIGKILL; destination absent; current state unchanged; retry succeeded'
            restore_result['cutover_crashes']=['source fenced before publish','published destination pending','source committed destination pending']
            before_existing=file_hash(destination/'restore.json')
            assert restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,destination,binary,binary_sha)['cutover_already_committed']
            assert file_hash(destination/'restore.json')==before_existing
            clone=output/'wrong-activation-root';shutil.copytree(destination,clone)
            blocked_root(clone,'storage_activation_root_mismatch')
            assert job['id'] in restore_result['revoked_job_ids'] and second_job['id'] not in restore_result['revoked_job_ids']
            assert Registry(destination).read()['active'] is None
            assert not list(destination.glob('*.sqlite3*'))
            rust=Service(destination,output);rust.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/event_memory_runner.py',VISN_RETRIEVAL_TIMEOUT_SECS='120')
            rust.start()
            restored_record=api(second_route)
            assert restored_record['visual_projection']['state']=='blocked'
            second_event=restored_record['result']['camera_results'][0]['events'][0]
            source_request=urllib.request.urlopen(rust.base+f'/api/v1/memory-events/{second_event["event_id"]}/source',timeout=30)
            with source_request:assert sha(source_request.read())==second_event['privacy']['source']['media_sha256']
            api('/api/v1/memory-query',query,410)
            api(route+'/visual-index',{},410)
            api(f'/api/v1/memory-events/{requests[0]["events"][0]["event_id"]}/source',status=404)
            denied_event=dict(requests[0]['events'][0],source_path=str(destination/'memory'/job['id']/'junction/source.mkv'))
            denied=urllib.request.Request(b.url+'/search',data=json.dumps(dict(query='red',events=[denied_event],lexical_ids=[],limit=1)).encode(),headers={'Content-Type':'application/json'})
            try:urllib.request.urlopen(denied,timeout=30);raise AssertionError('restored path bypassed worker scope revocation')
            except urllib.error.HTTPError as error:assert error.code==410,error.code
            second_query=dict(query,job_id=second_job['id'],query=second_event['description']['summary'][:200],semantic_enabled=False)
            lexical=api('/api/v1/memory-query',second_query)
            assert lexical['matches'],lexical
            restored_registry=Registry(destination)
            request=dict(scope_id=second_job['id'],events=[dict(event_id=second_event['event_id'],
                source_path=str(destination/'memory'/second_job['id']/'pending/source.mkv'),
                expected_source_sha256=second_event['privacy']['source']['media_sha256'],start_ms=second_event['start_ms'],end_ms=second_event['end_ms'])])
            restored_catalog,generation=restored_registry.stage(b.url,[request],restored_registry.read()['revision'],allow_fixture=not args.actual)
            restored_registry.activate(generation,restored_catalog['revision'])
            semantic=api('/api/v1/memory-query',dict(second_query,query='red background',semantic_enabled=True))
            assert semantic['retrieval_mode']=='hybrid_rrf' and semantic['retrieval_provenance']['deployment_generation']==generation,semantic
            completed=wait(lambda:(value if (value:=api(route))['revocation']['cleanup_state']=='completed' else None))
            assert len(completed['revocation']['target_receipts'])==2
            rust.stop();rust.start()
            api('/api/v1/memory-query',query,410)
            assert api('/api/v1/memory-query',dict(second_query,query='red background',semantic_enabled=True))['retrieval_mode']=='hybrid_rrf'
            phase('Revoke the still-valid recording after restoration and semantic rebuild')
            api(second_route+'/revoke',{})
            newly_revoked=wait(lambda:(value if (value:=api(second_route))['revocation']['cleanup_state']=='completed' else None))
            assert len(newly_revoked['revocation']['target_receipts'])==2
            api('/api/v1/memory-query',second_query,410)
            post_restore_request=urllib.request.Request(b.url+'/search',data=json.dumps(dict(query='red',events=request['events'],lexical_ids=[],limit=1)).encode(),headers={'Content-Type':'application/json'})
            try:urllib.request.urlopen(post_restore_request,timeout=30);raise AssertionError('post-restore revoked evidence retrieved')
            except urllib.error.HTTPError as error:assert error.code==410,error.code
            if args.witness:
                phase('Recover missing/stale canonical tombstones from the independent witness before API startup')
                assert file_hash(immutable_record)==immutable_hash
                rust.stop()
                first_manifest=destination/'memory/manifests'/(job['id']+'.json');first_manifest.unlink()
                second_manifest=destination/'memory/manifests'/(second_job['id']+'.json')
                second_manifest.write_bytes((output/'snapshot/payload/memory/manifests'/(second_job['id']+'.json')).read_bytes())
                pin=destination/'revocation-witness.json'
                pin.write_bytes((output/'snapshot/payload/revocation-witness.json').read_bytes())
                rust.start();api('/api/v1/memory-query',query,410);api('/api/v1/memory-query',second_query,410)
                assert api(second_route)['revocation']['revision']==newly_revoked['revocation']['revision']
                assert len(json.loads(pin.read_bytes())['acknowledged'])==2
                assert json.loads((destination/'storage-contract.json').read_bytes())['witness_required'] is True
                rust.stop()
                def refused():
                    try:verify(binary,binary_sha,destination);raise AssertionError('bad witness passed release preflight')
                    except ValueError as error:assert 'preflight_failed' in str(error)
                    result=subprocess.run([str(binary)],cwd=ROOT,env=rust.environment(),capture_output=True,text=True,timeout=15)
                    assert result.returncode!=0 and 'service ready' not in result.stdout
                missing_record=witness/'records'/'held-record';immutable_record.rename(missing_record);refused();missing_record.rename(immutable_record)
                original_record=immutable_record.read_bytes();corrupt=json.loads(original_record);corrupt['job']['error']='owned checksum fault'
                immutable_record.write_text(json.dumps(corrupt));refused();immutable_record.write_bytes(original_record)
                instance_path=witness/'instance.json';original_instance=instance_path.read_bytes();changed=json.loads(original_instance);changed['id']=str(__import__('uuid').uuid4())
                instance_path.write_text(json.dumps(changed));refused();instance_path.write_bytes(original_instance)
                held_pin=destination/'held-pin';pin.rename(held_pin);refused();held_pin.rename(pin)
                unavailable=output/'witness-unavailable';witness.rename(unavailable);refused();unavailable.rename(witness)
                rust.start();api('/api/v1/memory-query',query,410);api('/api/v1/memory-query',second_query,410)
                witness_evidence.update(recovered_missing_and_stale_manifests=True,immutable_hash=immutable_hash,
                    rejected_missing_record=True,rejected_corrupt_record=True,rejected_wrong_instance=True,rejected_missing_pin=True,rejected_unavailable_store=True,
                    unavailable_revocation_ack_status=503,acknowledged_records=2)
            assert restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,destination,binary,binary_sha)['cutover_already_committed']
            api('/api/v1/memory-query',second_query,410)
            rust.stop()
            missing=output/'retained-new-root';destination.rename(missing)
            try:restore(output/'snapshot',backup_result['snapshot_sha256'],data,cut,destination,binary,binary_sha);raise AssertionError('lost committed root rebuilt from stale cut')
            except ValueError as error:assert 'committed_cutover_destination_missing' in str(error)
            missing.rename(destination)
            verify(binary,binary_sha,destination)
            assert len(hits)==capture_requests
            restore_result.update(restored_generation=generation,remote_cleanup=completed['revocation'],post_restore_revocation=newly_revoked['revocation'],source_recapture=False)
        (output/'results.json').write_text(json.dumps(dict(scope=__doc__,actual_qwen=args.actual,lost_ack=args.lost_ack,replace_pending=args.replace_pending,legacy_binding=migration,backup=backup_result,restore=restore_result,witness=witness_evidence,generations=[ga,gb],
            inflight_a=pending['response']['retrieval_provenance'],selected_b=selected_b['retrieval_provenance'],
            rolled_back=restored['retrieval_provenance'],wrong_storage_rejection=blocked['revocation'],failed_stage_generation=failed_generation,retired_projection=retired['visual_projection'],revocation=revoked['revocation'],
            source_requests=hits,observations=observations),indent=2)+'\n')
        phase('Passed atomic cutover, request binding, rollback, CAS, restart, tamper and cross-target revocation')
    finally:
        rust.stop()
        for worker in workers:worker.close()
        camera.shutdown();camera.server_close();camera_thread.join(timeout=5)


if __name__=='__main__':main()
