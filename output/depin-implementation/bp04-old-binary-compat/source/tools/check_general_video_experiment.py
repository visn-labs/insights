#!/usr/bin/env python3
"""Experiment isolation, split leakage, provenance and exact comparison checks."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import general_video_experiment as exp


class Checks(unittest.TestCase):
    def fixture(self, root):
        source=root/'source.bin';source.write_bytes(b'evidence')
        body=dict(query='objects',events=[dict(event_id='a',source_path=str(source),start_ms=0,end_ms=1000)],lexical_ids=[],rerank=True,limit=1)
        request=root/'request.json';exp.save(request,body)
        corpus=dict(schema='visn-general-corpus-v1',sources=[dict(id='s',path='source.bin',sha256=exp.digest(source),source_group='g',session_group='session',site_group='site',split='development',development_exposed=True,clips=1,families=['quiet_rare'])],labels=[],cases=[dict(id='a',split='development',source_ids=['s'],request=dict(path='request.json',sha256=exp.digest(request)))])
        profile=dict(schema='visn-experiment-profile-v1',id='fixture',vector_policy='canonical-unit-v2',embedding=dict(model='fixture',snapshot='pinned'),reranker_revision='rerank-pinned')
        result=dict(items=[dict(event_id='a',score=.12345678901234568)],mode='hybrid_qwen_reranker',notes=['Visual coverage 1/1 events; 0 cached embeddings.'],provenance=dict(embedding=dict(model='fixture',snapshot='/weights/pinned'),reranker_snapshot='/weights/rerank-pinned',vector_policy='canonical-unit-v2',elapsed_secs=1,work_cache={}))
        return corpus,profile,body,result

    def test_inventory_and_source_mutation(self):
        with TemporaryDirectory() as tmp,patch.object(exp,'ROOT',Path(tmp)):
            root=Path(tmp);corpus,*_=self.fixture(root);report=exp.corpus_inventory(corpus)
            self.assertEqual(report['coverage']['development']['quiet_rare']['clips'],1)
            self.assertFalse(report['acceptance_ready'])
            (root/'source.bin').write_bytes(b'changed')
            with self.assertRaises(ValueError):exp.corpus_inventory(corpus)

    def test_split_leakage_exposed_acceptance_and_out_of_scope_requests(self):
        with TemporaryDirectory() as tmp,patch.object(exp,'ROOT',Path(tmp)):
            root=Path(tmp);corpus,*_=self.fixture(root)
            corpus['sources'].append(dict(corpus['sources'][0],id='other',split='validation'))
            with self.assertRaisesRegex(ValueError,'leakage'):exp.corpus_inventory(corpus)
            corpus['sources'].pop();corpus['sources'][0]['split']='acceptance'
            with self.assertRaisesRegex(ValueError,'Development-exposed'):exp.corpus_inventory(corpus)
            corpus['sources'][0]['split']='development'
            body=exp.read(root/'request.json');body['labels']={'answer':'leak'};exp.save(root/'request.json',body)
            corpus['cases'][0]['request']['sha256']=exp.digest(root/'request.json')
            with self.assertRaisesRegex(ValueError,'scoring-only'):exp.corpus_inventory(corpus)

    def test_provenance_mismatch_and_tiny_score_change_are_detected(self):
        with TemporaryDirectory() as tmp,patch.object(exp,'ROOT',Path(tmp)):
            _,profile,body,result=self.fixture(Path(tmp));exp.validate_result(result,profile,body)
            changed=json.loads(json.dumps(result));changed['items'][0]['score']+=1e-16
            self.assertNotEqual(exp.signature(changed),exp.signature(result))
            changed=json.loads(json.dumps(result));changed['notes']=['Visual coverage 1/1 events; 1 cached embeddings.'];changed['provenance']['elapsed_secs']=9
            self.assertEqual(exp.signature(changed),exp.signature(result))
            changed['provenance']['vector_policy']='legacy-renormalize-v1'
            with self.assertRaisesRegex(ValueError,'policy'):exp.validate_result(changed,profile,body)

    def test_run_saves_raw_results_and_refuses_reuse(self):
        with TemporaryDirectory() as tmp,patch.object(exp,'ROOT',Path(tmp)),redirect_stdout(io.StringIO()):
            root=Path(tmp);corpus,profile,_,result=self.fixture(root)
            exp.save(root/'corpus.json',corpus);exp.save(root/'profile.json',profile)
            args=SimpleNamespace(corpus=root/'corpus.json',profile=root/'profile.json',split='development',worker='http://127.0.0.1:8193',output=root/'run',repeats=2)
            with patch.object(exp.urllib.request,'urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(result).encode())):
                exp.run(args)
                with self.assertRaises(FileExistsError):exp.run(args)
            self.assertEqual(exp.read(root/'run/manifest.json')['status'],'passed')
            self.assertEqual(exp.read(root/'run/a-0.json'),result)
            exp.compare(SimpleNamespace(baseline=root/'run',candidate=root/'run',output=root/'comparison.json'))
            self.assertTrue(exp.read(root/'comparison.json')['exact'])
            changed=exp.read(root/'run/a-0.json');changed['items'][0]['score']+=1e-16;exp.save(root/'run/a-0.json',changed)
            with self.assertRaisesRegex(ValueError,'changed raw result'):
                exp.compare(SimpleNamespace(baseline=root/'run',candidate=root/'run',output=root/'tampered.json'))

    def test_mutating_corpus_during_run_marks_it_failed(self):
        with TemporaryDirectory() as tmp,patch.object(exp,'ROOT',Path(tmp)),redirect_stdout(io.StringIO()):
            root=Path(tmp);corpus,profile,_,result=self.fixture(root)
            exp.save(root/'corpus.json',corpus);exp.save(root/'profile.json',profile)
            args=SimpleNamespace(corpus=root/'corpus.json',profile=root/'profile.json',split='development',worker='http://127.0.0.1:8193',output=root/'run',repeats=1)
            def response(*a,**k):
                (root/'corpus.json').write_text((root/'corpus.json').read_text()+'\n')
                return io.BytesIO(json.dumps(result).encode())
            with patch.object(exp.urllib.request,'urlopen',side_effect=response):
                with self.assertRaisesRegex(ValueError,'changed during replay'):exp.run(args)
            self.assertEqual(exp.read(root/'run/manifest.json')['status'],'failed')


if __name__=='__main__':unittest.main()
