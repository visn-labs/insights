#!/usr/bin/env python3
"""Actual lossless decoding proves source crops preserve pixels rather than enlarge previews."""
import copy
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import imageio_ffmpeg
import numpy as np
from PIL import Image
from semantic_memory_server import Retrieval
from temporal_interpretation import evidence_crops,SequenceInterpreter,validate_result
from select_clear_tracks import candidate_windows
from check_retrieval import write_video

class Checks(unittest.TestCase):
    def test_native_detail_no_upscale_and_pts_match(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'stripes.mkv'
            array=np.zeros((240,320,3),dtype=np.uint8);array[:,::2]=255
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s','320x240','-r','2','-i','pipe:0','-c:v','ffv1','-pix_fmt','bgr0',str(source)],input=array.tobytes()*2,check=True,capture_output=True)
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=2,frame_size=64))
            event=dict(source_path=str(source),start_ms=0,end_ms=1000)
            _,sha=service.source(event);event['grounding']=dict(source_sha256=sha,entity_id='fixture',observations=[dict(timestamp_ms=t,bbox=[.25,.25,.5,.5]) for t in (0,500)])
            previews,pts=service.frames(event,count=2,size=64,with_timestamps=True,sample_timestamps=[0,500])
            old,_=evidence_crops(service,event,previews,pts,2,[0,500],sha,(0,1000),'preview')
            crops,proof=evidence_crops(service,event,previews,pts,2,[0,500],sha,(0,1000),'source')
            self.assertEqual(proof['detail_sizes'],[[320,240]]*2);self.assertEqual(proof['decoded_timestamps_ms'],pts)
            self.assertEqual(crops[0]['image'].size,(160,120));self.assertEqual(old[0]['image'].size,(32,24))
            self.assertGreater(np.asarray(crops[0]['image']).std(),100);self.assertLess(np.asarray(old[0]['image']).std(),10)
            capped=service.frames(event,count=1,size=160,no_upscale=True)[0];self.assertEqual(capped.size,(160,120))
            service.db.close()

    def test_timestamp_mismatch_fails_and_untracked_does_not_decode_detail(self):
        class Service:
            def frames(self,*a,**kw):return [Image.new('RGB',(64,64))]*2,[1,501]
        previews=[Image.new('RGB',(64,64))]*2
        event=dict(grounding={})
        with self.assertRaises(ValueError):evidence_crops(Service(),event,previews,[0,500],2,[0,500],'sha',(0,1000),'source')
        crops,proof=evidence_crops(None,{},previews,[0,500],2,None,'sha',(0,1000),'source')
        self.assertIsNone(crops);self.assertEqual(proof['mode'],'none')

    def test_large_source_crop_is_downsampled_to_model_budget(self):
        class Service:
            def frames(self,*a,**kw):return [Image.new('RGB',(1920,1080))]*2,[0,500]
        event=dict(grounding=dict(entity_id='fixture',source_sha256='sha',observations=[dict(timestamp_ms=t,bbox=[0,0,1,1]) for t in (0,500)]))
        crops,proof=evidence_crops(Service(),event,[Image.new('RGB',(384,216))]*2,[0,500],2,[0,500],'sha',(0,1000),'source')
        self.assertEqual(crops[0]['image'].size,(768,432));self.assertEqual(proof['original_crop_sizes'],[[1920,1080]]*2);self.assertTrue(proof['no_upscale'])

    def test_crop_mode_partitions_generation_cache(self):
        class Worker(SequenceInterpreter):
            generated=0
            def load(self):self.snapshot='fixture'
            def generate(self,q,e,f,t):
                self.generated+=1
                return validate_result(dict(status='uncertain',answer='Fixture',observations=[],limitations=[]),t)
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4';write_video(source)
            args=SimpleNamespace(data_dir=tmp,frames=4,frame_size=64,interpretation_frames=4,interpretation_frame_size=64,interpretation_model='fixture',interpretation_crop_mode='preview')
            service=Retrieval(args);worker=Worker(service);event=dict(event_id='one',source_path=str(source),start_ms=0,end_ms=2000)
            _,sha=service.source(event);event['grounding']=dict(source_sha256=sha,entity_id='fixture',observations=[dict(timestamp_ms=t,bbox=[.1,.1,.5,.5]) for t in (300,800,1300,1800)])
            body=dict(query='Car?',events=[event]);first=worker.interpret(body)['interpretations'][0]
            args.interpretation_crop_mode='source';second=worker.interpret(body)['interpretations'][0];third=worker.interpret(body)['interpretations'][0]
            self.assertFalse(first['cached']);self.assertFalse(second['cached']);self.assertTrue(third['cached']);self.assertEqual(worker.generated,2)
            self.assertEqual(first['frames'][0]['image'],second['frames'][0]['image']);service.db.close()

    def test_selection_excludes_tiny_static_short_and_long_gap_evidence(self):
        def graph(points):return dict(entities=[dict(entity_id='fixture',class_name='car',observations=[dict(timestamp_ms=t,bbox=[0,0,w,h]) for t,w,h in points])])
        valid=graph([(0,.2,.2),(2000,.4,.4)]);before=copy.deepcopy(valid)
        self.assertEqual(len(candidate_windows(valid,(640,480),30000)),1);self.assertEqual(valid,before)
        for points in ([(0,.01,.01),(2000,.02,.02)],[(0,.2,.2),(2000,.2,.2)],[(0,.2,.2),(200,.4,.4)],[(0,.2,.2),(21000,.4,.4)]):
            self.assertEqual(candidate_windows(graph(points),(640,480),30000),[])

if __name__=='__main__':unittest.main()
