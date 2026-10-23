#!/usr/bin/env python3
"""Cadence regression: sparse/irregular PTS must not be decimated by nominal FPS."""
import math,unittest
from yolo26_runner import TimestampSampler
class Checks(unittest.TestCase):
    def test_sparse_and_regular_timestamps(self):
        sparse=TimestampSampler(5);self.assertEqual(sum(sparse.accept(t) for t in range(0,45000,400)),113)
        regular=TimestampSampler(5);self.assertEqual(sum(regular.accept(t) for t in range(0,1000,40)),5)
    def test_jitter_duplicate_and_discontinuity(self):
        sampler=TimestampSampler(5);self.assertEqual([t for t in [0,0,199,201,399,405,1200] if sampler.accept(t)],[0,201,405,1200])
        with self.assertRaises(ValueError):sampler.accept(100)
        with self.assertRaises(ValueError):TimestampSampler(float('nan'))
    def test_persistent_capture_uses_pts_with_wrong_nominal_fps(self):
        import threading
        from types import SimpleNamespace
        from yolo26_worker import CameraSession
        class Capture:
            index=-1
            def isOpened(self):return True
            def grab(self):self.index+=1;return self.index<10
            def retrieve(self):return True,object()
            def get(self,key):return 25 if key==1 else self.index*400
            def release(self):pass
        capture=Capture();cv=SimpleNamespace(VideoCapture=lambda _:capture,CAP_PROP_FPS=1,CAP_PROP_POS_MSEC=2)
        session=CameraSession(dict(request_id='cadence-test',source='fixture.mkv',fps=5,max_seconds=5,confidence=.25),cv,None,None,None,threading.Event())
        sampled=[];session._offer=lambda packet:sampled.append(packet.source_time_ms) is None
        session._capture_loop();self.assertIsNone(session.error);self.assertEqual(sampled,list(range(0,4000,400)))
if __name__=='__main__':unittest.main()
