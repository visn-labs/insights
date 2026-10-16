#!/usr/bin/env python3
"""Offline contracts with real decoding; no model accuracy claims."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from check_retrieval import write_video
from semantic_memory_server import Retrieval
from temporal_interpretation import SequenceInterpreter, validate_result, static_sequence

class Interpreter(SequenceInterpreter):
    calls = 0
    def load(self): self.snapshot = 'fixture-snapshot'
    def generate(self, query, event, frames, times):
        self.calls += 1
        assert frames[0].getpixel((32,32))[0] > 100
        assert frames[-1].getpixel((32,32))[2] > 100
        return validate_result(dict(status='supported', answer='Color changes.', observations=[dict(frame_index=0,description='Red'),dict(frame_index=len(times)-1,description='Blue')],limitations=[]), times)

class Checks(unittest.TestCase):
    def test_static_abstention(self):
        from PIL import Image
        red=Image.new('RGB',(64,64),'red');blue=Image.new('RGB',(64,64),'blue')
        self.assertTrue(static_sequence([red,red.copy()]))
        self.assertFalse(static_sequence([red,blue]))

    def test_citations(self):
        value=dict(status='supported',answer='Changes',observations=[dict(frame_index=0,description='A'),dict(frame_index=1,description='B')],limitations=[])
        self.assertEqual(validate_result(copy.deepcopy(value),[100,200])['observations'][1]['timestamp_ms'],200)
        for index in (-1,2,True,0):
            bad=copy.deepcopy(value);bad['observations'][1]['frame_index']=index
            with self.assertRaises(ValueError): validate_result(bad,[100,200])
        value['observations'].pop()
        with self.assertRaises(ValueError): validate_result(value,[100,200])
        value['status']='uncertain';validate_result(value,[100,200])

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
