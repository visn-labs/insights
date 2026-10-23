#!/usr/bin/env python3
"""Local API adapter contracts; no model accuracy claims."""
import base64
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image
from temporal_interpretation import SequenceInterpreter

class Checks(unittest.TestCase):
    def worker(self):
        worker=SequenceInterpreter.__new__(SequenceInterpreter)
        worker.service=SimpleNamespace(args=SimpleNamespace(interpretation_api_url='http://127.0.0.1:1244/v1',interpretation_model='fixture-model'))
        return worker

    def test_exact_image_and_prompt_reach_api(self):
        image=Image.new('RGB',(32,32),(11,22,33))
        def reply(request,timeout):
            body=json.loads(request.data)
            self.assertEqual(body['temperature'],0)
            self.assertEqual(body['max_tokens'],180)
            self.assertEqual(body['messages'][0],dict(role='system',content='Inspect only this image'))
            content=body['messages'][1]['content']
            self.assertEqual(content[0],dict(type='text',text='Target?'))
            encoded=content[1]['image_url']['url'];self.assertTrue(encoded.startswith('data:image/png;base64,'))
            actual=Image.open(io.BytesIO(base64.b64decode(encoded.split(',',1)[1])))
            self.assertEqual(actual.tobytes(),image.tobytes())
            return io.BytesIO(json.dumps(dict(model='fixture-model',choices=[dict(message=dict(content='{"target_visibility":"visible"}'))])).encode())
        with patch('urllib.request.urlopen',reply):
            self.assertEqual(self.worker().api_complete('Inspect only this image',[dict(type='text',text='Target?'),dict(type='image',image=image)],180)['target_visibility'],'visible')

    def test_wrong_model_is_a_hard_failure(self):
        value=dict(model='wrong-model',choices=[dict(message=dict(content='{}'))])
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(value).encode())):
            with self.assertRaises(RuntimeError): self.worker().api_complete('Inspect',[dict(type='image',image=Image.new('RGB',(16,16)))],180)

    def test_api_requires_loopback_and_pinned_revision(self):
        worker=Path(__file__).with_name('semantic_memory_server.py')
        for arguments in [
            ['--interpretation-api-url','http://remote.invalid/v1','--interpretation-revision','pinned'],
            ['--interpretation-api-url','http://127.0.0.1:1244/v1'],
        ]:
            result=subprocess.run([sys.executable,str(worker),*arguments],capture_output=True,timeout=5)
            self.assertEqual(result.returncode,2)

    def test_api_only_load_does_not_import_local_model_libraries(self):
        import builtins
        worker=self.worker();worker.model=None;worker.service.models=None;worker.service.args.interpretation_revision='pinned'
        original=builtins.__import__
        def guarded(name,*a,**kw):
            if name in ('torch','transformers','huggingface_hub'):raise AssertionError('Unnecessary local model import: '+name)
            return original(name,*a,**kw)
        with patch('builtins.__import__',guarded),patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"data":[{"id":"fixture-model"}]}')):
            worker.load()
        self.assertIs(worker.model,True);self.assertEqual(worker.snapshot,'pinned')

    def test_api_load_still_releases_actual_retrieval_weights(self):
        from unittest.mock import Mock
        fake_torch=SimpleNamespace(backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda:True)),mps=SimpleNamespace(empty_cache=Mock()),cuda=SimpleNamespace(is_available=lambda:False))
        worker=self.worker();worker.model=None;worker.service.models=SimpleNamespace(embedder=object(),reranker=object());worker.service.args.interpretation_revision='pinned'
        with patch.dict('sys.modules',torch=fake_torch),patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"data":[{"id":"fixture-model"}]}')):
            worker.load()
        self.assertIsNone(worker.service.models.embedder);self.assertIsNone(worker.service.models.reranker);fake_torch.mps.empty_cache.assert_called_once()

    def test_image_is_required(self):
        with self.assertRaises(ValueError): self.worker().api_complete('Inspect',[dict(type='text',text='metadata only')],180)

if __name__=='__main__':unittest.main()
