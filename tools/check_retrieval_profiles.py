#!/usr/bin/env python3
"""Profile/HTTP admission contracts; fixture vectors are not accuracy evidence."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request

from check_retrieval import Models, write_video
from retrieval_profile import FrozenFiles, model_files, storage_identity, ProfileConflict
from semantic_memory_server import Retrieval, handler_for
from workflow_indexing import IndexJobs


class Checks(unittest.TestCase):
    def test_storage_identity_survives_restart_but_not_database_reset(self):
        with TemporaryDirectory() as directory:
            args=SimpleNamespace(data_dir=directory,frames=2,frame_size=64)
            def identity():
                service=Retrieval(args,Models())
                try:return storage_identity(service)
                finally:
                    service.db.close();service.revocations.close()
            first=identity()
            self.assertEqual(first,identity())
            database=Path(directory)/'revocations-v1.sqlite3'
            self.assertTrue(database.is_file())
            database.unlink()
            self.assertNotEqual(first,identity())

    def test_sealed_artifacts_detect_membership_and_bytes_without_path_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first=root/'a';second=root/'b';first.mkdir();second.mkdir()
            for folder in [first,second]:
                (folder/'model.safetensors').write_bytes(b'fixture weights, not a model')
                (folder/'config.json').write_text('{}')
            sealed=model_files(first)
            self.assertEqual(sealed.identity,model_files(second).identity)
            sealed.verify()
            (first/'adapter_config.json').write_text('{}')
            with self.assertRaises(ProfileConflict):sealed.verify()
            (first/'adapter_config.json').unlink()
            (first/'model.safetensors').write_bytes(b'changed fixture weights')
            with self.assertRaises(ProfileConflict):sealed.verify()
            self.assertNotEqual(sealed.identity,model_files(first).identity)
            empty=root/'empty';empty.mkdir()
            with self.assertRaises(ValueError):model_files(empty)

    def test_http_profile_matching_receipts_and_inflight_drift(self):
        class MutableModels(Models):
            drift=False
            def embed(self,value):
                result=super().embed(value)
                if self.drift and 'text' in value:self.version='{"model":"changed-during-inference"}'
                return result
        with TemporaryDirectory() as directory:
            source=Path(directory)/'source.mp4';write_video(source)
            models=MutableModels();service=Retrieval(SimpleNamespace(data_dir=directory,frames=2,frame_size=64),models)
            service.indexer=IndexJobs(service)
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(service))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def request(path,body=None,profile=None,status=200):
                headers={'Content-Type':'application/json'}
                if profile is not None:headers['X-Visn-Expected-Profile']=profile
                req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
                try:response=urllib.request.urlopen(req,timeout=15)
                except urllib.error.HTTPError as error:response=error
                with response:
                    value=json.load(response);self.assertEqual(response.status,status,value);return value
            try:
                profile=request('/profile')
                self.assertEqual(profile,request('/profile'))
                self.assertEqual(profile['manifest']['qualification'],'fixture_or_unsealed_model')
                event=dict(event_id='red',source_path=str(source),start_ms=0,end_ms=1000)
                body=dict(query='red',events=[event],lexical_ids=['red'],limit=1,rerank=False)
                result=request('/search',body,profile['fingerprint'])
                self.assertEqual(result['provenance']['worker_profile'],profile)
                calls=models.calls
                request('/search',body,'0'*64,409)
                self.assertEqual(models.calls,calls)
                lookup=dict(scope_id='recording',events=[event],resolve_only=True)
                request('/index-jobs',lookup,profile['fingerprint'],404)
                self.assertEqual(service.indexer.list('recording'),[])
                self.assertEqual(models.calls,calls)
                request('/index-jobs',dict(lookup,resolve_only='yes'),profile['fingerprint'],400)
                receipt=request('/index-jobs',dict(scope_id='recording',events=[event]),profile['fingerprint'],202)
                self.assertEqual(receipt['worker_profile'],profile)
                resolved=request('/index-jobs',lookup,profile['fingerprint'])
                self.assertEqual(resolved['id'],receipt['id'])
                self.assertEqual(resolved['updated_at'],receipt['updated_at'])
                self.assertEqual(len(service.indexer.list('recording')),1)
                models.drift=True
                failed=request('/search',dict(body,query='uncached query'),profile['fingerprint'],409)
                self.assertIn('profile changed',failed['error'].lower())
                self.assertFalse(any(key[1]=='uncached query' for key in service.query_cache))
                changed=request('/profile');self.assertNotEqual(profile['fingerprint'],changed['fingerprint'])
                request('/search',body,profile['fingerprint'],409)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
                service.indexer.close();service.db.close();service.revocations.close()


if __name__=='__main__':unittest.main()
