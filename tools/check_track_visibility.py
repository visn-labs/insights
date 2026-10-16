#!/usr/bin/env python3
"""A missing/unclear selected target is not proof of scene-wide absence."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from PIL import Image
from check_retrieval import write_video
from semantic_memory_server import Retrieval
from temporal_interpretation import SequenceInterpreter,validate_result,guard_track_absence

class Checks(unittest.TestCase):
    def worker(self):
        worker=SequenceInterpreter.__new__(SequenceInterpreter)
        worker.identify_target=lambda q:dict(object='car',action='approach',reference='camera',comparison='camera_distance')
        return worker

    def test_tiny_target_never_reaches_visibility_model(self):
        worker=self.worker()
        def unexpected(*args):raise AssertionError('Tiny crop should not be interpreted')
        worker.complete=unexpected
        crops=[dict(image=Image.new('RGB',(8,12))) for _ in range(2)]
        result=worker.generate('Does the car approach?',dict(_track_crops=crops),[Image.new('RGB',(64,64),c) for c in ('red','blue')],[100,200])
        self.assertEqual(result['status'],'uncertain');self.assertTrue(all(o['target_visibility']=='unclear' for o in result['observations']))

    def test_model_absent_on_selected_crop_is_unclear(self):
        worker=self.worker();worker.complete=lambda *args:dict(target_visibility='absent',description='No car')
        crops=[dict(image=Image.new('RGB',(32,32))) for _ in range(2)]
        result=worker.generate('Does the car approach?',dict(_track_crops=crops),[Image.new('RGB',(64,64),c) for c in ('red','blue')],[100,200])
        self.assertEqual(result['status'],'uncertain');self.assertTrue(all(o['target_visibility']=='unclear' for o in result['observations']))

    def test_unconstrained_absence_is_preserved(self):
        worker=self.worker();worker.complete=lambda *args:dict(target_visibility='absent',description='No car')
        result=worker.generate('Does the car approach?',{},[Image.new('RGB',(64,64),c) for c in ('red','blue')],[100,200])
        self.assertEqual(result['status'],'not_observed')

    def test_cached_absence_cannot_bypass_selected_track_guard(self):
        class Worker(SequenceInterpreter):
            generated=0
            def load(self):self.snapshot='fixture'
            def generate(self,q,e,frames,times):
                self.generated+=1
                return validate_result(dict(status='not_observed',answer='No car',observations=[dict(frame_index=i,target_visibility='absent',description='No car') for i in range(len(times))],limitations=[]),times)
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.mp4';write_video(path)
            args=SimpleNamespace(data_dir=tmp,frames=4,frame_size=64,interpretation_frames=4,interpretation_frame_size=64,interpretation_model='fixture')
            service=Retrieval(args);worker=Worker(service)
            event=dict(event_id='one',source_path=str(path),start_ms=0,end_ms=2000)
            _,sha=service.source(event)
            event['grounding']=dict(source_sha256=sha,entity_id='fixture',observations=[dict(timestamp_ms=t,bbox=[.1,.1,.5,.5]) for t in (300,800,1300,1800)])
            body=dict(query='Does the car approach?',events=[event])
            first=worker.interpret(body)['interpretations'][0];second=worker.interpret(body)['interpretations'][0]
            self.assertEqual(first['status'],'uncertain');self.assertEqual(second['status'],'uncertain');self.assertTrue(second['cached']);self.assertEqual(worker.generated,1)
            self.assertTrue(all(o['target_visibility']=='unclear' for o in second['observations']))
            service.db.close()

if __name__=='__main__':unittest.main()
