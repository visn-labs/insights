#!/usr/bin/env python3
"""Real codec/reset capture contracts. Fixture detections are not accuracy evidence."""
from fractions import Fraction
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import time
import unittest

import av
import numpy as np

import privacy_media as privacy
from check_privacy_media import FixtureDetector


def reset_source(directory, *, resize=False, image=None):
    """Two genuine MPEG-TS sequences: PTS reset or resolution discontinuity."""
    parts = []
    for part in range(2):
        path = directory / f'part-{part}.ts'
        with av.open(str(path), 'w', format='mpegts') as container:
            stream = container.add_stream('mpeg2video', rate=25)
            stream.width = image.shape[1] if image is not None else (256 if resize and part else 128)
            stream.height = image.shape[0] if image is not None else stream.width
            stream.pix_fmt = 'yuv420p'
            stream.codec_context.max_b_frames = 0
            stream.codec_context.gop_size = 1
            for index in range(10):
                pixels = image if image is not None else np.full((stream.height, stream.width, 3), 50 + part*80 + index, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(pixels, format='bgr24')
                frame.pts = index + (part*10 if resize else 0)
                frame.time_base = Fraction(1, 25)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        parts.append(path.read_bytes())
    source = directory / 'source.ts'
    source.write_bytes(b''.join(parts))
    return source


class Checks(unittest.TestCase):
    def test_clock_reset_keeps_every_frame_and_independent_pts(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = reset_source(root)
            original_hash = privacy.digest(source)
            with av.open(str(source)) as container:
                expected = [(frame.pts, str(frame.time_base)) for frame in container.decode(video=0)]
            self.assertEqual(len(expected), 20)
            self.assertLess(expected[10][0], expected[9][0])
            with self.assertRaisesRegex(ValueError, 'nonmonotonic'):
                privacy.sanitize(str(source), root/'legacy.mkv', 2, FixtureDetector())
            self.assertFalse((root/'legacy.mkv').exists())
            report = privacy.capture_sessions(str(source), root/'capture', 2, FixtureDetector())
            self.assertEqual(report['state'], 'completed')
            self.assertEqual([entry['frames'] for entry in report['sessions']], [10, 10])
            self.assertEqual(report['sessions'][1]['start_reason'], 'source_clock_regressed')
            observed = []
            for index, entry in enumerate(report['sessions']):
                target = root/'capture'/entry['file']
                ledger = [json.loads(line) for line in target.with_suffix('.mkv.frames.jsonl').read_text().splitlines()]
                observed.extend((row['source_pts'], row['source_time_base']) for row in ledger)
                self.assertEqual([row['sanitized_pts_ms'] for row in ledger], list(range(0, 400, 40)))
                privacy.render_playback(target, root/f'playback-{index}.mp4')
            self.assertEqual(observed, expected)  # Includes the boundary frame.
            self.assertNotEqual(report['sessions'][0]['session_id'], report['sessions'][1]['session_id'])
            self.assertEqual(privacy.verify_capture_sessions(root/'capture'), report)
            self.assertEqual(privacy.digest(source), original_hash)

    def test_resolution_change_starts_new_sanitized_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = reset_source(root, resize=True)
            with av.open(str(source)) as container:
                expected = [(frame.pts, str(frame.time_base)) for frame in container.decode(video=0)]
            report = privacy.capture_sessions(str(source), root/'capture', 2, FixtureDetector())
            self.assertEqual(report['sessions'][1]['start_reason'], 'source_resolution_changed')
            self.assertEqual(sum(entry['frames'] for entry in report['sessions']), len(expected))
            dimensions = []
            observed = []
            for entry in report['sessions']:
                target = root/'capture'/entry['file']
                dimensions.append(privacy.verify_commit(target)['width'])
                observed.extend((row['source_pts'], row['source_time_base']) for row in
                    map(json.loads, target.with_suffix('.mkv.frames.jsonl').read_text().splitlines()))
            self.assertEqual(dimensions, [128, 256])
            self.assertEqual(observed, expected)

    def test_failure_retains_only_explicit_committed_prefix(self):
        class FailingDetector(FixtureDetector):
            calls = 0
            def detect(self, image):
                self.calls += 1
                if self.calls == 11:
                    raise RuntimeError('private source credential must not enter journal')
                return super().detect(image)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RuntimeError):
                privacy.capture_sessions(str(reset_source(root)), root/'capture', 2, FailingDetector())
            journal = root/'capture/capture.json'
            self.assertNotIn('credential', journal.read_text())
            self.assertEqual(json.loads(journal.read_text())['state'], 'failed')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                privacy.verify_capture_sessions(root/'capture')
            prefix = privacy.verify_capture_sessions(root/'capture', allow_partial=True)
            self.assertEqual(len(prefix['sessions']), 1)
            self.assertFalse((root/'capture/session-0001.mkv').exists())
            self.assertFalse(list((root/'capture').glob('.privacy-*')))

    def test_limit_and_manifest_tampering_fail_closed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = reset_source(root)
            with self.assertRaisesRegex(ValueError, 'session_limit'):
                privacy.capture_sessions(str(source), root/'limited', 2, FixtureDetector(), max_sessions=1)
            self.assertEqual(len(privacy.verify_capture_sessions(root/'limited', allow_partial=True)['sessions']), 1)
            privacy.capture_sessions(str(source), root/'capture', 2, FixtureDetector())
            target = root/'capture/session-0001.mkv.privacy.json'
            saved = target.read_bytes()
            changed = json.loads(saved)
            changed['capture']['transport_finished_wall_ms'] += 1
            target.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'provenance_changed'):
                privacy.verify_capture_sessions(root/'capture')
            target.write_bytes(saved)
            marker = root/'capture/capture.json'
            original_marker = marker.read_bytes()
            changed = json.loads(original_marker)
            changed['sessions'][1]['file'] = '../source.ts'
            marker.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'session_contract'):
                privacy.verify_capture_sessions(root/'capture')
            marker.write_bytes(original_marker)
            media = root/'capture/session-0000.mkv'
            held = root/'held.mkv'
            media.rename(held)
            media.symlink_to(source)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                privacy.verify_capture_sessions(root/'capture')
            media.unlink()
            held.rename(media)
            with self.assertRaises(FileExistsError):
                privacy.capture_sessions(str(source), root/'capture', 2, FixtureDetector())
            privacy.verify_capture_sessions(root/'capture')

    def test_process_kill_preserves_only_published_sanitized_prefix(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = reset_source(root)
            barrier = root/'second-session-entered'
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--crash-child',
                                      str(source), str(root/'capture'), str(barrier)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 15
                while not barrier.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(barrier.exists(), 'child did not reach the second session')
                child.kill()
                child.wait(timeout=5)
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    privacy.verify_capture_sessions(root/'capture')
                report = privacy.verify_capture_sessions(root/'capture', allow_partial=True)
                self.assertEqual(report['state'], 'capturing')
                self.assertEqual([entry['frames'] for entry in report['sessions']], [10])
                self.assertFalse((root/'capture/session-0001.mkv.privacy.json').exists())
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--crash-child':
        class PauseDetector(FixtureDetector):
            calls = 0
            def detect(self, image):
                self.calls += 1
                if self.calls == 11:
                    Path(sys.argv[4]).write_text('second-session-entered')
                    time.sleep(20)
                return super().detect(image)
        privacy.capture_sessions(sys.argv[2], sys.argv[3], 2, PauseDetector())
        raise SystemExit(0)
    unittest.main()
