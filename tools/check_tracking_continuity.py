#!/usr/bin/env python3
"""Association boundary contracts; synthetic clocks do not measure tracking quality."""
import unittest
from unittest.mock import patch
import threading
import itertools
import json
from pathlib import Path
import sys
from yolo26_runner import TrackingContinuity
from yolo26_worker import CameraSession, FramePacket


class Checks(unittest.TestCase):
    def test_gap_and_reconnect_do_not_reuse_identity(self):
        clock = TrackingContinuity()
        epochs = [clock.observe(t) for t in (0, 100, 1100, 2101, 2200)]
        self.assertEqual(epochs, [0, 0, 0, 1, 1])
        self.assertEqual(clock.track_id(0, 7), '7')
        self.assertNotEqual(clock.track_id(0, 7), clock.track_id(1, 7))
        clock.reconnect()
        # A reconnect opens a new association clock, even when its timestamps
        # restart. Packet epochs survive a latest-frame queue skipping frames.
        packet = FramePacket(None, 0, clock.observe(0))
        self.assertEqual(packet.continuity_epoch, 2)
        self.assertEqual(clock.track_id(packet.continuity_epoch, 7), 'session-2:7')

    def test_regression_is_not_silently_retimed(self):
        clock = TrackingContinuity()
        clock.observe(500)
        with self.assertRaisesRegex(ValueError, 'source_clock_regressed'):
            clock.observe(499)

    def test_capture_reconnect_carries_boundary_in_packet(self):
        class Capture:
            def __init__(self):
                self.reads = 0
            def is_opened(self): return True
            def fps(self): return 25
            def release(self): pass
            def read(self):
                self.reads += 1
                return (True, object()) if self.reads == 1 else (False, None)
        session = CameraSession(dict(request_id='owned-test', source='http://fixture.invalid',
            fps=1000, max_seconds=2), None, None, None, None, threading.Event())
        captures = iter([Capture(), Capture()])
        session._open_capture = lambda remaining: next(captures)
        packets = []
        def offer(packet):
            packets.append(packet)
            session.cancelled = len(packets) == 2
            return not session.cancelled
        session._offer = offer
        ticks = itertools.count()
        with patch('yolo26_worker.time.monotonic', side_effect=lambda: next(ticks) / 100), \
             patch('yolo26_worker.time.sleep'):
            session._capture_loop()
        self.assertIsNone(session.error)
        self.assertTrue(session.capture_done)
        self.assertEqual([packet.continuity_epoch for packet in packets], [0, 1])


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--workflow-report':
        path = Path(sys.argv[2])
        report = json.loads(path.read_text())
        cameras = next(item['response']['result']['camera_results'] for item in report['observations']
                       if item['response'].get('status') == 'completed' and item['response'].get('result'))
        graph = cameras[0]['long_memory']
        before = after = 0
        for entity in graph['entities']:
            times = [point['timestamp_ms'] for point in entity['observations']]
            left, right = any(t < 1000 for t in times), any(t >= 3000 for t in times)
            assert not (left and right), 'saved entity crossed injected observation boundary'
            before += left
            after += right
            if right:
                assert entity['track_id'].startswith('session-1:')
        assert before and after
        assert any('Session-prefixed' in note for note in graph['notes'])
        result = dict(entities_before=before, entities_after=after, crossed_boundary=False,
                      scope='Injected 2000ms spacing in real detector/API replay; not tracking accuracy')
        path.with_name('association-check.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        sys.exit(0)
    unittest.main()
