#!/usr/bin/env python3
"""Pixel/PTS/commit/failure regression tests, with optional real-model smoke test.

Fixture detectors test pipeline contracts only; they do not measure detector recall.
"""
from fractions import Fraction
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import av
import numpy as np

import privacy_media as privacy


class FixtureDetector:
    threshold = .1
    models = dict(yunet=dict(sha256='fixture'), plate=dict(sha256='fixture'))

    def detect(self, image):
        return dict(face=[dict(bbox=[.125,.25,.25,.25], confidence=.5)],
                    plate=[dict(bbox=[.5,.5,.25,.25], confidence=.95)])


def fixture(path, *, times=(0, 40, 120, 160), audio=True):
    with av.open(str(path), mode='w', format='matroska') as container:
        stream = container.add_stream('ffv1', rate=25)
        stream.width, stream.height, stream.pix_fmt = 128, 96, 'bgr0'
        stream.time_base = stream.codec_context.time_base = Fraction(1,1000)
        sound = container.add_stream('pcm_s16le', rate=8000) if audio else None
        if sound is not None:
            sound.layout = 'mono'
        for index, pts in enumerate(times):
            image = np.zeros((96,128,3),dtype=np.uint8)
            image[:,:,:] = [20+index,90,160]
            frame = av.VideoFrame.from_ndarray(image, format='bgr24')
            frame.pts, frame.time_base = pts, Fraction(1,1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        if sound is not None:
            samples = np.full((1,8000),1000,dtype=np.int16)
            frame = av.AudioFrame.from_ndarray(samples,format='s16',layout='mono')
            frame.sample_rate, frame.pts, frame.time_base = 8000, 0, Fraction(1,8000)
            for packet in sound.encode(frame):
                container.mux(packet)
            for packet in sound.encode():
                container.mux(packet)


class Checks(unittest.TestCase):
    def test_missing_model_fails_closed(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'missing_or_changed'):
                privacy.resolve_models(directory)
            lock=json.loads((privacy.ROOT/'tools/privacy_models.lock.json').read_text())
            (Path(directory)/lock['models']['yunet']['file']).write_bytes(b'changed model')
            with self.assertRaisesRegex(ValueError,'missing_or_changed'):
                privacy.resolve_models(directory)

    def test_every_encoded_frame_is_scrubbed_and_vfr_is_preserved(self):
        with TemporaryDirectory() as directory:
            source, target = Path(directory)/'input.mkv', Path(directory)/'output.mkv'
            fixture(source)
            with av.open(str(source)) as original:
                self.assertTrue(original.streams.audio)
            before = privacy.digest(source)
            report = privacy.sanitize(str(source), target, 1, FixtureDetector())
            self.assertEqual(report['frames'],4)
            self.assertEqual(privacy.digest(source),before)
            self.assertEqual(privacy.verify_commit(target),report)
            self.assertEqual(set(p.name for p in Path(directory).iterdir()),
                             {'input.mkv','output.mkv','output.mkv.privacy.json','output.mkv.frames.jsonl'})
            playback = Path(directory)/'playback.mp4'
            playback_report = privacy.render_playback(target,playback)
            self.assertEqual(playback_report['frames'],4)
            self.assertEqual(playback_report['source_sha256'], report['media_sha256'])
            with av.open(str(playback)) as played:
                self.assertFalse(played.streams.audio)
                self.assertEqual(played.streams.video[0].codec_context.name,'h264')
            with av.open(str(target)) as container:
                self.assertFalse(container.streams.audio)
                for index, frame in enumerate(container.decode(video=0)):
                    self.assertEqual(frame.pts*frame.time_base, Fraction([0,40,120,160][index],1000))
                    image = frame.to_ndarray(format='bgr24')
                    self.assertFalse(image[24:48,16:48].any())
                    self.assertFalse(image[48:72,64:96].any())
                    self.assertTrue(np.all(image[0,0] == [20+index,90,160]))
            with self.assertRaises(FileExistsError):
                privacy.sanitize(str(source),target,1,FixtureDetector())
            target.write_bytes(target.read_bytes()+b'mutation')
            with self.assertRaisesRegex(ValueError,'changed'):
                privacy.verify_commit(target)

    def test_failure_never_commits_partial_media_or_raw_fallback(self):
        class BrokenDetector(FixtureDetector):
            def detect(self, image):
                raise RuntimeError('detector_down')
        with TemporaryDirectory() as directory:
            source, target = Path(directory)/'input.mkv', Path(directory)/'output.mkv'
            fixture(source)
            with self.assertRaisesRegex(RuntimeError,'detector_down'):
                privacy.sanitize(str(source),target,1,BrokenDetector())
            self.assertEqual([p.name for p in Path(directory).iterdir()],['input.mkv'])
            with self.assertRaises(ValueError):
                privacy.sanitize(str(source),target,1,FixtureDetector(),max_pixels=100)
            self.assertFalse(target.exists())

    def test_encoded_stdin_has_no_raw_file_staging(self):
        with TemporaryDirectory() as directory:
            source, target = Path(directory)/'input.mkv', Path(directory)/'output.mkv'
            fixture(source)
            report = privacy.sanitize(io.BytesIO(source.read_bytes()),target,1,FixtureDetector())
            self.assertEqual(report['frames'],4)

    def test_high_bit_depth_is_not_silently_reduced(self):
        with TemporaryDirectory() as directory:
            source,target=Path(directory)/'high-depth.mkv',Path(directory)/'output.mkv'
            with av.open(str(source),'w',format='matroska') as output:
                stream=output.add_stream('ffv1',rate=1)
                stream.width=stream.height=32
                stream.pix_fmt='gray16le'
                frame=av.VideoFrame.from_ndarray(np.full((32,32),10000,dtype=np.uint16),format='gray16le')
                frame.pts,frame.time_base=0,Fraction(1,1000)
                for packet in stream.encode(frame):output.mux(packet)
                for packet in stream.encode():output.mux(packet)
            with self.assertRaisesRegex(ValueError,'precision_unsupported'):
                privacy.sanitize(str(source),target,1,FixtureDetector())
            self.assertFalse(target.exists())

    def test_large_timestamp_spacing_is_disclosed_without_invented_frames(self):
        with TemporaryDirectory() as directory:
            source,target=Path(directory)/'input.mkv',Path(directory)/'output.mkv'
            fixture(source,times=(0,100,2500),audio=False)
            report=privacy.sanitize(str(source),target,3,FixtureDetector())
            capture=report['capture']
            self.assertEqual(report['frames'],3)
            self.assertEqual(capture['timestamp_jump_count'],1)
            self.assertEqual(capture['timestamp_jump_samples'],[[100,2500]])
            self.assertEqual(capture['termination'],'end_of_stream')
            self.assertFalse(capture['continuity_certified'])
            self.assertTrue(capture['transport_time_is_not_capture_time'])
            ledger = [json.loads(line) for line in target.with_suffix('.mkv.frames.jsonl').read_text().splitlines()]
            self.assertEqual(ledger[0]['association_session_id'], ledger[1]['association_session_id'])
            self.assertNotEqual(ledger[1]['association_session_id'], ledger[2]['association_session_id'])
            sessions=capture['association_sessions']
            self.assertEqual([(s['first_frame_index'],s['last_frame_index']) for s in sessions],[(0,1),(2,2)])
            self.assertEqual([(s['first_observed_ms'],s['last_observed_ms']) for s in sessions],[(0,100),(2500,2500)])
            self.assertEqual(sessions[1]['start_reason'],'observed_timestamp_spacing')
            with av.open(str(target)) as video:
                self.assertEqual([int(f.pts*f.time_base*1000) for f in video.decode(video=0)],[0,100,2500])
@unittest.skipUnless(os.environ.get('VISN_PRIVACY_REAL_MODELS'), 'Explicit cached real-model smoke run required')
class ModelChecks(unittest.TestCase):
    def test_live_mjpeg_uses_observed_arrival_not_invented_capture_timestamps(self):
        import cv2
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import time
        assets=Path(os.environ['VISN_PRIVACY_REAL_MODELS'])
        image=cv2.imread(str(assets/'lena.jpg'))
        jpeg=cv2.imencode('.jpg',image)[1].tobytes()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame')
                self.end_headers()
                try:
                    for _ in range(30):
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+
                                         str(len(jpeg)).encode()+b'\r\n\r\n'+jpeg+b'\r\n')
                        self.wfile.flush()
                        time.sleep(.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            detector=privacy.PrivacyDetector(privacy.ROOT/'.retrieval/privacy-models')
            with TemporaryDirectory() as directory:
                target=Path(directory)/'sanitized.mkv'
                result=privacy.sanitize(f'http://127.0.0.1:{server.server_port}/video.mjpg',target,.3,detector)
                self.assertEqual(result['clock_kind'],'decoder_observed_monotonic_arrival')
                self.assertTrue(result['clock_limitations'])
                self.assertGreater(result['frames'],0)
                privacy.verify_commit(target)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_public_face_and_plate_sequences(self):
        import cv2
        assets=Path(os.environ['VISN_PRIVACY_REAL_MODELS'])
        detector=privacy.PrivacyDetector(privacy.ROOT/'.retrieval/privacy-models')
        for filename, kind in [('lena.jpg','face'),('plate.png','plate')]:
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                image=cv2.imread(str(assets/filename))
                self.assertIsNotNone(image)
                found=detector.detect(image)
                self.assertTrue(found[kind], f'Positive {kind} smoke fixture was not detected')
                source,target=Path(directory)/'input.mkv',Path(directory)/'sanitized.mkv'
                with av.open(str(source),'w',format='matroska') as container:
                    stream=container.add_stream('ffv1',rate=4)
                    stream.width,stream.height=image.shape[1],image.shape[0]
                    stream.pix_fmt='bgr0'
                    stream.time_base=stream.codec_context.time_base=Fraction(1,1000)
                    for pts in [0,250,500,750]:
                        frame=av.VideoFrame.from_ndarray(image,format='bgr24')
                        frame.pts,frame.time_base=pts,Fraction(1,1000)
                        for packet in stream.encode(frame):container.mux(packet)
                    for packet in stream.encode():container.mux(packet)
                result=privacy.sanitize(str(source),target,1,detector)
                self.assertGreaterEqual(result['detections'][kind],4)
                self.assertEqual(result['frames'],4)
                privacy.verify_commit(target)
                playback=Path(directory)/'playback.mp4'
                privacy.render_playback(target,playback)
                with av.open(str(playback)) as playable:
                    self.assertEqual(playable.streams.video[0].height,image.shape[0]+image.shape[0]%2)


if __name__ == '__main__':
    unittest.main()
