#!/usr/bin/env python3
"""Owned HTTP/real-codec reconnect contracts; fixture detections, no accuracy claim."""
from contextlib import contextmanager
import errno
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
import json

import av
import numpy as np

import privacy_media as privacy
from check_privacy_media import FixtureDetector


def encoded_source(path, frames):
    with av.open(str(path), 'w', format='mpegts') as container:
        stream = container.add_stream('mpeg2video', rate=25)
        stream.width = stream.height = 128
        stream.pix_fmt = 'yuv420p'
        stream.codec_context.max_b_frames = 0
        stream.codec_context.gop_size = 1
        for index in range(frames):
            frame = av.VideoFrame.from_ndarray(np.full((128, 128, 3), 80+index % 80, dtype=np.uint8), format='bgr24')
            frame.pts, frame.time_base = index, Fraction(1, 25)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path.read_bytes()


@contextmanager
def source_server(responses):
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            status, body, *declared = responses[min(len(hits), len(responses)-1)]
            hits.append(status)
            self.send_response(status)
            self.send_header('Content-Type', 'video/mp2t')
            self.send_header('Content-Length', str(declared[0] if declared else len(body)))
            self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/live.ts', hits
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


class Checks(unittest.TestCase):
    def test_expired_backoff_does_not_admit_or_count_another_connection(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            short = encoded_source(root/'short.ts', 10)
            clock = [0.]
            def elapsed_backoff(delay): clock[0] += 3.
            with source_server([(200, short)]) as (url, hits):
                with patch.object(privacy.time, 'monotonic', side_effect=lambda: clock[0]), \
                     patch.object(privacy.time, 'sleep', side_effect=elapsed_backoff):
                    report = privacy.capture_sessions(url, root/'capture', 2, FixtureDetector(), reconnect_attempts=8)
                self.assertEqual(hits, [200])
            self.assertEqual(report['transport']['reconnect_attempts'], 0)
            self.assertEqual(len(report['sessions']), 1)
            privacy.verify_capture_sessions(root/'capture')

    def test_reopen_gets_new_clock_and_preserves_committed_frames(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            short = encoded_source(root/'short.ts', 10)
            long = encoded_source(root/'long.ts', 100)
            with source_server([(200, short), (200, long)]) as (url, hits):
                report = privacy.capture_sessions(url, root/'capture', 2, FixtureDetector(), reconnect_attempts=1)
            self.assertEqual(hits, [200, 200])
            self.assertEqual(report['state'], 'completed')
            self.assertEqual(report['transport']['reconnect_attempts'], 1)
            first, second = report['sessions']
            self.assertEqual(first['frames'], 10)
            self.assertEqual(first['termination'], 'transport_disconnected')
            self.assertEqual(second['start_reason'], 'transport_reconnected')
            self.assertNotEqual(first['session_id'], second['session_id'])
            for entry in report['sessions']:
                ledger = root/'capture'/f"{entry['file']}.frames.jsonl"
                self.assertEqual(json.loads(ledger.read_text().splitlines()[0])['relative_time'], '0')
            self.assertEqual(privacy.verify_capture_sessions(root/'capture'), report)
            report['transport']['reconnect_attempts'] = 0
            (root/'capture/capture.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'transport_contract'):
                privacy.verify_capture_sessions(root/'capture')

    def test_abrupt_http_body_disconnect_rolls_over(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            short = encoded_source(root/'short.ts', 10)
            long = encoded_source(root/'long.ts', 100)
            with source_server([(200, short, len(short)*10), (200, long)]) as (url, hits):
                # The actual decoder can withhold its last buffered frame at EIO.
                # Compare with emitted frames, not the encoded packet count.
                expected = []
                with self.assertRaises(av.error.OSError) as failed:
                    with av.open(url, timeout=(2., 2.)) as container:
                        for frame in container.decode(video=0):
                            expected.append((frame.pts, str(frame.time_base)))
                self.assertEqual(failed.exception.errno, errno.EIO)
                self.assertTrue(expected)
                hits.clear()
                report = privacy.capture_sessions(url, root/'capture', 2, FixtureDetector(), reconnect_attempts=1)
                self.assertEqual(hits, [200, 200])
            self.assertEqual([row['start_reason'] for row in report['sessions']], ['initial_capture', 'transport_reconnected'])
            ledger = root/'capture/session-0000.mkv.frames.jsonl'
            observed = [(row['source_pts'], row['source_time_base']) for row in map(json.loads, ledger.read_text().splitlines())]
            self.assertEqual(observed, expected)
            privacy.verify_capture_sessions(root/'capture')

    def test_budget_exhaustion_is_partial_not_success_and_default_never_reopens(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            short = encoded_source(root/'short.ts', 10)
            with source_server([(200, short)]) as (url, hits):
                control = privacy.capture_sessions(url, root/'control', 2, FixtureDetector())
                self.assertEqual(len(hits), 1)
                self.assertNotIn('transport', control)
                with self.assertRaisesRegex(ValueError, 'reconnect_budget_exhausted'):
                    privacy.capture_sessions(url, root/'partial', 2, FixtureDetector(), reconnect_attempts=1)
                self.assertEqual(len(hits), 3)
            prefix = privacy.verify_capture_sessions(root/'partial', allow_partial=True)
            self.assertEqual(prefix['state'], 'failed')
            self.assertEqual([row['frames'] for row in prefix['sessions']], [10, 10])
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                privacy.verify_capture_sessions(root/'partial')

    def test_server_failure_retries_but_authorization_and_detector_errors_do_not(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            long = encoded_source(root/'long.ts', 100)
            with source_server([(503, b''), (200, long)]) as (url, hits):
                report = privacy.capture_sessions(url, root/'retry', 1, FixtureDetector(), reconnect_attempts=1)
                self.assertEqual(hits, [503, 200])
                self.assertEqual(report['sessions'][0]['start_reason'], 'initial_capture')
            with source_server([(401, b'')]) as (url, hits):
                with self.assertRaises(av.error.HTTPUnauthorizedError):
                    privacy.capture_sessions(url, root/'denied', 1, FixtureDetector(), reconnect_attempts=2)
                self.assertEqual(hits, [401])
            with source_server([(200, b'not a video stream')]) as (url, hits):
                with self.assertRaises(av.error.InvalidDataError):
                    privacy.capture_sessions(url, root/'invalid-video', 1, FixtureDetector(), reconnect_attempts=2)
                self.assertEqual(hits, [200])
            class FailedDetector(FixtureDetector):
                def detect(self, image): raise RuntimeError('model failure')
            with source_server([(200, long)]) as (url, hits):
                with self.assertRaisesRegex(RuntimeError, 'model failure'):
                    privacy.capture_sessions(url, root/'model', 1, FailedDetector(), reconnect_attempts=2)
                self.assertEqual(hits, [200])
            class TimedOutDetector(FixtureDetector):
                def detect(self, image): raise av.error.TimeoutError(110, 'model timeout')
            with source_server([(200, long)]) as (url, hits):
                with self.assertRaises(av.error.TimeoutError):
                    privacy.capture_sessions(url, root/'model-timeout', 1, TimedOutDetector(), reconnect_attempts=2)
                self.assertEqual(hits, [200])
            for budget in (-1, 9, True):
                with self.assertRaisesRegex(ValueError, 'reconnect_policy'):
                    privacy.capture_sessions(str(root/'long.ts'), root/'invalid', 1, FixtureDetector(), reconnect_attempts=budget)
            self.assertFalse((root/'invalid').exists())


if __name__ == '__main__':
    unittest.main()
