#!/usr/bin/env python3
"""Exact-PTS crop contracts; tracker IDs are not identity labels."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from PIL import Image
from check_retrieval import write_video
from semantic_memory_server import Retrieval
from temporal_interpretation import track_crops, guard_track_citations

class Checks(unittest.TestCase):
    def evidence(self):
        return dict(source_sha256='source',entity_id='entity',observations=[dict(timestamp_ms=t,bbox=[.25,.25,.5,.5]) for t in (100,300)])

    def test_exact_pts_not_seek_time_and_no_gap_interpolation(self):
        frames=[Image.new('RGB',(64,64),'red') for _ in range(4)]
        crops=track_crops(self.evidence(),'source',frames,[100,200,300,300],(0,400))
        self.assertIsNone(crops[1]);self.assertEqual(crops[0]['image'].size,(32,32))
        self.assertEqual(crops[3]['timestamp_ms'],300)
        value=dict(status='supported',answer='Moves',observations=[dict(frame_index=i) for i in (2,3)],limitations=[])
        self.assertEqual(guard_track_citations(value,crops)['status'],'uncertain')
        value=dict(status='supported',answer='Moves',observations=[dict(frame_index=i) for i in (0,1)],limitations=[])
        self.assertEqual(guard_track_citations(value,crops)['status'],'uncertain')

    def test_background_change_does_not_prove_target_change(self):
        crops=[dict(timestamp_ms=t,bbox=[.1,.1,.2,.2],image=Image.new('RGB',(32,32),'red')) for t in (100,300)]
        value=dict(status='supported',answer='Moves',observations=[dict(frame_index=i) for i in (0,1)],limitations=[])
        self.assertEqual(guard_track_citations(value,crops)['status'],'uncertain')
        crops[1]['bbox']=[.2,.1,.2,.2]
        value=dict(status='supported',answer='Moves',observations=[dict(frame_index=i) for i in (0,1)],limitations=[])
        self.assertEqual(guard_track_citations(value,crops)['status'],'supported')

    def test_source_bounds_and_conflicting_boxes_rejected(self):
        frames=[Image.new('RGB',(64,64),'red')]
        evidence=self.evidence()
        for mutate in (lambda g:g.update(source_sha256='other'), lambda g:g['observations'][0].update(timestamp_ms=400),lambda g:g['observations'][0].update(bbox=[.8,0,.5,.5]),lambda g:g['observations'][0].update(bbox=[float('nan'),0,.5,.5]),lambda g:g['observations'].append(dict(timestamp_ms=100,bbox=[0,0,.2,.2]))):
            bad=copy.deepcopy(evidence);mutate(bad)
            with self.assertRaises(ValueError):track_crops(bad,'source',frames,[100],(0,400))

    def test_real_decoder_exposes_pts_and_repeated_frames_keep_pts(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.mp4';write_video(path)
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=4,frame_size=64))
            event=dict(source_path=str(path),start_ms=0,end_ms=2000)
            frames,pts=service.frames(event,with_timestamps=True)
            self.assertEqual(len(frames),4)
            self.assertEqual(pts,[300,800,1300,1800])
            self.assertEqual(len(service.frames(event)),4)
            _, held_pts=service.frames(dict(event,start_ms=1900,end_ms=2000),with_timestamps=True)
            self.assertEqual(held_pts,[1900]*4)
            service.db.close()

if __name__=='__main__':unittest.main()
