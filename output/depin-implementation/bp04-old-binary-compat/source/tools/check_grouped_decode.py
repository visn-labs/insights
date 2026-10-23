#!/usr/bin/env python3
"""Actual RGB/PTS parity and bounded fallback; no model weights required."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import imageio_ffmpeg
import numpy as np

import grouped_decode
from semantic_memory_server import Retrieval
from check_retrieval import Models,write_video
from check_interpretation import Interpreter

FFMPEG=imageio_ffmpeg.get_ffmpeg_exe()


def video(path,codec='ffv1',fps=10,frames=30,filters=None,width=96,height=64,pixel_format=None):
    pixels=np.random.default_rng(42).integers(0,256,(frames,height,width,3),dtype=np.uint8)
    command=[FFMPEG,'-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r',str(fps),'-i','pipe:0']
    if filters:command+=['-vf',filters,'-vsync','0']
    command+=['-an','-c:v',codec]
    command+=['-pix_fmt',pixel_format or 'bgr0'] if codec=='ffv1' else ['-pix_fmt','yuvj420p'] if codec=='mjpeg' else ['-pix_fmt',pixel_format or 'yuv420p','-g','10','-bf','2']
    subprocess.run(command+[str(path)],input=pixels.tobytes(),capture_output=True,check=True)


class Checks(unittest.TestCase):
    def service(self,tmp,policy,**extra):
        directory=Path(tmp)/policy;directory.mkdir(exist_ok=True)
        service=Retrieval(SimpleNamespace(data_dir=tmp,frames=8,frame_size=64,decode_policy=policy,**extra),Models())
        self.addCleanup(service.db.close);return service

    def compare(self,tmp,event,**options):
        control=self.service(tmp,'single-seek-v1',disable_work_caches=True)
        candidate=self.service(tmp,'grouped-select-v1',disable_work_caches=True)
        expected,pts=control.frames(event,with_timestamps=True,**options)
        actual,times=candidate.frames(event,with_timestamps=True,**options)
        self.assertEqual(times,pts)
        self.assertEqual([(f.size,f.tobytes()) for f in actual],[(f.size,f.tobytes()) for f in expected])
        return control,candidate

    def test_cfr_h264_bframes_mjpeg_vfr_sparse_and_offset(self):
        with TemporaryDirectory() as tmp:
            cases=[('lossless','ffv1',None),('h264','libx264',None),('mjpeg','mjpeg',None),
                   ('vfr','libx264',"select='not(eq(mod(n,3),1))'"),
                   ('sparse','ffv1',"select='not(mod(n,10))'"),
                   ('offset','libx264','setpts=PTS+5/TB')]
            for name,codec,filters in cases:
                with self.subTest(name=name):
                    path=Path(tmp)/(name+'.mkv');video(path,codec=codec,filters=filters)
                    event=dict(source_path=str(path),start_ms=0,end_ms=2900)
                    control,candidate=self.compare(tmp,event)
                    self.assertEqual(candidate.work_stats['grouped_calls'],1)
                    self.assertEqual(candidate.work_stats['grouped_fallbacks'],0)
                    self.assertLess(candidate.work_stats['ffmpeg_calls'],control.work_stats['ffmpeg_calls'])

    def test_repeated_unsorted_tracker_targets_resize_and_no_upscale(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.mkv';video(path)
            event=dict(source_path=str(path),start_ms=200,end_ms=2500)
            for options in [dict(sample_timestamps=[1200,200,200,2100,500,2300]),dict(size=128),dict(size=128,no_upscale=True),dict(sample_timestamps=[200,200])]:
                with self.subTest(options=options):self.compare(tmp,event,**options)

    def test_fractional_clock_and_ten_bit_input(self):
        with TemporaryDirectory() as tmp:
            for name,codec,pixel_format in [('ntsc','libx264','yuv420p'),('ten-bit','ffv1','yuv420p10le')]:
                with self.subTest(name=name):
                    path=Path(tmp)/(name+'.mkv');video(path,codec=codec,pixel_format=pixel_format,fps='30000/1001',frames=90)
                    self.compare(tmp,dict(source_path=str(path),start_ms=1,end_ms=2999),sample_timestamps=[1,333.667,1001,1801.8,2300,2950])

    def test_sixty_fps_target_boundary_uses_ffmpeg_double_arithmetic(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'sixty.mp4';video(path,codec='libx264',fps=60,frames=180)
            # At 1.85s the mathematical rational and FFmpeg's double timestamp
            # straddle the seek target. The second request needs the next frame.
            self.compare(tmp,dict(source_path=str(path),start_ms=0,end_ms=2900),sample_timestamps=[1840,1850.5])

    def test_interpretation_citations_crops_and_cached_validation_match(self):
        import copy
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.mp4';write_video(path);answers=[]
            for policy in ('single-seek-v1','grouped-select-v1'):
                service=self.service(tmp,policy,interpretation_frames=4,interpretation_frame_size=64,interpretation_model='fixture',interpretation_crop_mode='source')
                worker=Interpreter(service);service.db.execute('DELETE FROM interpretations');service.db.commit()
                event=dict(event_id='target',source_path=str(path),start_ms=0,end_ms=2000)
                _,sha=service.source(event)
                event['grounding']=dict(source_sha256=sha,entity_id='fixture',observations=[dict(timestamp_ms=t,bbox=[0,0,1,1]) for t in (300,800,1300,1800)])
                first=worker.interpret(dict(query='What changes?',events=[event]))
                cached=worker.interpret(dict(query='What changes?',events=[event]))
                self.assertFalse(first['interpretations'][0]['cached']);self.assertTrue(cached['interpretations'][0]['cached'])
                self.assertTrue(all('target_crop' in frame for frame in first['interpretations'][0]['frames']))
                def normalize(value):
                    value=copy.deepcopy(value)
                    for row in value['interpretations']:
                        row.pop('cached',None);row.pop('elapsed_secs',None)
                    return value
                self.assertEqual(normalize(first),normalize(cached));answers.append(normalize(first))
            self.assertEqual(*answers)

    def test_sparse_empty_first_hold_last_eof_and_empty_interval(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'sparse.mkv';video(path,fps=2,frames=2)
            for bounds in [(0,50),(0,1000),(0,1900),(500,1000)]:
                with self.subTest(bounds=bounds):self.compare(tmp,dict(source_path=str(path),start_ms=bounds[0],end_ms=bounds[1]))
            for policy in ('single-seek-v1','grouped-select-v1'):
                service=self.service(tmp,policy)
                for bounds in [(50,90),(1000,2000)]:
                    with self.subTest(policy=policy,bounds=bounds),self.assertRaises(ValueError):
                        service.frames(dict(source_path=str(path),start_ms=bounds[0],end_ms=bounds[1]))

    def test_cache_is_shared_by_equivalent_transforms_and_images_are_detached(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.mp4';write_video(path);event=dict(source_path=str(path),start_ms=0,end_ms=2000)
            service=self.service(tmp,'grouped-select-v1');first,pts=service.frames(event,with_timestamps=True)
            self.assertEqual(service.work_stats['ffmpeg_calls'],1);original=first[0].tobytes();first[0].putpixel((0,0),(1,2,3))
            again,times=service.frames(event,with_timestamps=True)
            self.assertEqual(again[0].tobytes(),original);self.assertEqual(times,pts);self.assertEqual(service.work_stats['ffmpeg_calls'],1)
            service.frames(event,size=32);self.assertEqual(service.work_stats['ffmpeg_calls'],2)
            service.frames(event,size=32,no_upscale=True);self.assertEqual(service.work_stats['ffmpeg_calls'],3)

    def test_failed_group_falls_back_without_partial_cache_publication(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.mp4';write_video(path);event=dict(source_path=str(path),start_ms=0,end_ms=2000)
            control=self.service(tmp,'single-seek-v1');expected,pts=control.frames(event,with_timestamps=True)
            service=self.service(tmp,'grouped-select-v1')
            with patch('semantic_memory_server.decode_group',side_effect=ValueError('Damaged clock fixture')):
                frames,times=service.frames(event,with_timestamps=True)
            self.assertEqual([f.tobytes() for f in frames],[f.tobytes() for f in expected]);self.assertEqual(times,pts)
            self.assertEqual(service.work_stats['grouped_fallbacks'],1);self.assertEqual(service.work_stats['ffmpeg_calls'],9)

    def test_truncated_recording_keeps_baseline_repeats_and_pts(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'truncated.mkv';video(path)
            raw=path.read_bytes();path.write_bytes(raw[:len(raw)*2//3])
            _,candidate=self.compare(tmp,dict(source_path=str(path),start_ms=0,end_ms=2900))
            self.assertEqual(candidate.work_stats['grouped_fallbacks'],1)

    def test_bounds_format_and_payload_guards(self):
        self.assertTrue(grouped_decode.eligible([0,1000],2000,64))
        for values in [([0],2000,64),([0,1000],21000,64),([0,1000],2000,1920),(list(range(33)),2000,64)]:
            self.assertFalse(grouped_decode.eligible(*values))
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.mp4';write_video(path);event=dict(source_path=str(path),start_ms=0,end_ms=2000)
            with patch('grouped_decode.MAX_GROUP_BYTES',1),self.assertRaisesRegex(ValueError,'byte limit'):
                grouped_decode.decode_group(FFMPEG,path,['0.100000','1.100000'],2000,'scale=64:64')
            original=grouped_decode.re.findall
            def no_clock(pattern,*args,**kwargs):
                return [] if b'config in time_base' in pattern else original(pattern,*args,**kwargs)
            service=self.service(tmp,'grouped-select-v1',disable_work_caches=True)
            with patch('grouped_decode.re.findall',side_effect=no_clock):frames=service.frames(event)
            self.assertEqual(len(frames),8);self.assertEqual(service.work_stats['grouped_fallbacks'],1)

    def test_mutation_during_decode_fails_before_cache_publication(self):
        with TemporaryDirectory() as tmp:
            for policy in ('single-seek-v1','grouped-select-v1'):
                path=Path(tmp)/(policy+'.mp4');write_video(path)
                event=dict(source_path=str(path),start_ms=0,end_ms=2000);service=self.service(tmp,policy)
                original=subprocess.run
                def mutate(*args,**kwargs):
                    result=original(*args,**kwargs)
                    with path.open('ab') as handle:handle.write(b'changed')
                    return result
                with patch('subprocess.run',side_effect=mutate),self.assertRaisesRegex(ValueError,'changed during'):
                    service.frames(event)
                self.assertEqual(len(service.decode_cache),0)


if __name__=='__main__':unittest.main()
