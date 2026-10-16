#!/usr/bin/env python3
"""Offline contracts with real decoding; no model accuracy claims."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from check_retrieval import write_video
from semantic_memory_server import Retrieval
from temporal_interpretation import SequenceInterpreter, validate_result, static_sequence, abstain_on_static_citations

class Interpreter(SequenceInterpreter):
    calls = 0
    def load(self): self.snapshot = 'fixture-snapshot'
    def generate(self, query, event, frames, times):
        self.calls += 1
        assert frames[0].getpixel((32,32))[0] > 100
        assert frames[-1].getpixel((32,32))[2] > 100
        return validate_result(dict(status='supported', answer='Color changes.', observations=[dict(target_visibility='visible',frame_index=0,description='Red'),dict(target_visibility='visible',frame_index=len(times)-1,description='Blue')],limitations=[]), times)

class Checks(unittest.TestCase):
    def test_identical_cited_pair_abstains_with_distinct_uncited_frames(self):
        from PIL import Image
        frames=[Image.new('RGB',(32,32),c) for c in ('red','blue','blue')]
        value=dict(status='supported',answer='Moves',observations=[dict(frame_index=i) for i in (1,2)],limitations=[])
        self.assertEqual(abstain_on_static_citations(value,frames)['status'],'uncertain')

    def test_cached_claim_is_revalidated_against_cited_images(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4';write_video(source)
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=4,frame_size=64,interpretation_frames=4,interpretation_frame_size=64,interpretation_model='fixture'))
            worker=Interpreter(service)
            body=dict(query='What changes?',events=[dict(event_id='one',source_path=str(source),start_ms=0,end_ms=2000,metadata={})])
            worker.interpret(body)
            import json
            raw=json.loads(service.db.execute('SELECT result FROM interpretations').fetchone()[0])
            raw['observations'][1]['frame_index']=1
            service.db.execute('UPDATE interpretations SET result=?',(json.dumps(raw),));service.db.commit()
            answer=worker.interpret(body)['interpretations'][0]
            self.assertTrue(answer['cached']);self.assertEqual(worker.calls,1)
            self.assertEqual(answer['status'],'uncertain')
            service.db.close()

    def test_static_abstention(self):
        from PIL import Image
        red=Image.new('RGB',(64,64),'red');blue=Image.new('RGB',(64,64),'blue')
        self.assertTrue(static_sequence([red,red.copy()]))
        self.assertFalse(static_sequence([red,blue]))

    def test_citations(self):
        value=dict(status='supported',answer='Changes',observations=[dict(target_visibility='visible',frame_index=0,description='A'),dict(target_visibility='visible',frame_index=1,description='B')],limitations=[])
        self.assertEqual(validate_result(copy.deepcopy(value),[100,200])['observations'][1]['timestamp_ms'],200)
        for index in (-1,2,True,0):
            bad=copy.deepcopy(value);bad['observations'][1]['frame_index']=index
            with self.assertRaises(ValueError): validate_result(bad,[100,200])
        value['observations'].pop()
        with self.assertRaises(ValueError): validate_result(value,[100,200])
        value['status']='uncertain';validate_result(value,[100,200])

    def test_target_visibility_contract(self):
        value=dict(status='supported',answer='Approaches',observations=[dict(frame_index=1,target_visibility='visible',description='Small target'),dict(frame_index=2,target_visibility='visible',description='Larger target')],limitations=[])
        validate_result(copy.deepcopy(value),[100,200,300,400])
        value['observations'][1]['target_visibility']='absent'
        with self.assertRaises(ValueError): validate_result(value,[100,200,300,400])
        value['status']='not_observed'
        with self.assertRaises(ValueError): validate_result(value,[100,200,300,400])
        value['observations']=[dict(frame_index=i,target_visibility='absent',description='Target not visible') for i in (0,3)]
        validate_result(value,[100,200,300,400])
        value['observations'][1]['target_visibility']='unclear'
        with self.assertRaises(ValueError): validate_result(value,[100,200,300,400])

    def test_independent_audit_gates_synthesis(self):
        from PIL import Image
        frames=[Image.new('RGB',(32,32),c) for c in ('red','green','blue','white')]
        worker=SequenceInterpreter.__new__(SequenceInterpreter)
        def run(audits, synthesis=None):
            responses=iter([dict(target_visibility=v,description=v) for v in audits]+([] if synthesis is None else [synthesis]))
            worker.complete=lambda *args: next(responses)
            return worker.generate('Does the target approach?',{},frames,[100,200,300,400])
        self.assertEqual(run(['absent']*4)['status'],'not_observed')
        self.assertEqual(run(['absent','visible','absent','unclear'])['status'],'uncertain')
        synthesis=dict(status='supported',answer='Approaches',frame_indices=[1,2])
        good=run(['absent','visible','visible','absent'],synthesis)
        self.assertEqual([o['frame_index'] for o in good['observations']],[1,2])
        self.assertEqual(good['observations'][0]['description'],'visible')
        synthesis['frame_indices'][1]=3
        self.assertEqual(run(['absent','visible','visible','absent'],synthesis)['status'],'uncertain')
        self.assertEqual(run(['absent','visible','visible','absent'],[])['status'],'uncertain')

    def test_malformed_audit_abstains(self):
        from PIL import Image
        worker=SequenceInterpreter.__new__(SequenceInterpreter)
        def malformed(*args): raise ValueError('truncated JSON')
        worker.complete=malformed
        result=worker.generate('Does the car approach?',{},[Image.new('RGB',(32,32),c) for c in ('red','blue')],[100,200])
        self.assertEqual(result['status'],'uncertain')
        self.assertTrue(all(o['target_visibility']=='unclear' for o in result['observations']))

    def test_decode_cache_and_invalidation(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4';write_video(source)
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=4,frame_size=64,interpretation_frames=4,interpretation_frame_size=64,interpretation_model='fixture'))
            worker=Interpreter(service)
            event=dict(event_id='one',source_path=str(source),start_ms=0,end_ms=2000,metadata={})
            body=dict(query='What changes?',events=[event])
            first=worker.interpret(body)['interpretations'][0]
            self.assertFalse(first['cached']);self.assertEqual(len(first['frames']),4)
            self.assertEqual(first['sampled_timestamps_ms'],[250,750,1250,1750])
            self.assertTrue(worker.interpret(body)['interpretations'][0]['cached']);self.assertEqual(worker.calls,1)
            body['query']='Another question';self.assertFalse(worker.interpret(body)['interpretations'][0]['cached'])
            event['metadata']['note']='new';self.assertFalse(worker.interpret(body)['interpretations'][0]['cached'])
            self.assertEqual(worker.calls,3)
            service.db.execute("UPDATE interpretations SET result='{}'");service.db.commit()
            self.assertFalse(worker.interpret(body)['interpretations'][0]['cached'])
            self.assertEqual(worker.calls,4)
            with self.assertRaises(ValueError): worker.interpret([])
            event['end_ms']=21000
            with self.assertRaises(ValueError): worker.interpret(body)
            service.db.close()

if __name__=='__main__': unittest.main()
