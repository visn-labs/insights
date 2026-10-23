#!/usr/bin/env python3
"""Exercise the fallback's tracking call and real ByteTrack ID continuity."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import torch
from ultralytics.engine.results import Boxes
from ultralytics.trackers.byte_tracker import BYTETracker
from ultralytics.utils import IterableSimpleNamespace, YAML

import yolo26_runner as runner
import yolo26_worker as worker


class Checks(unittest.TestCase):
    def test_both_paths_resolve_same_repository_policy_from_any_directory(self):
        with patch('sys.argv', ['worker']):
            args = worker.parse_args()
        self.assertEqual(args.tracker, runner.DEFAULT_TRACKER)
        self.assertTrue(Path(args.tracker).is_absolute())
        self.assertTrue(Path(args.tracker).is_file())
        model = Mock()
        frame = object()
        options = SimpleNamespace(confidence=.25, device='cpu', imgsz=640)
        self.assertIs(runner.track_frame(model, frame, options), model.track.return_value)
        model.track.assert_called_once_with(frame, persist=True, verbose=False,
            conf=.25, device='cpu', imgsz=640, tracker=args.tracker)

    def test_real_tracker_retains_id_after_short_occlusion(self):
        config = IterableSimpleNamespace(**YAML.load(runner.DEFAULT_TRACKER))
        self.assertEqual(config.tracker_type, 'bytetrack')
        tracker = BYTETracker(args=config)
        def detections(rows):
            return Boxes(torch.tensor(rows, dtype=torch.float32).reshape(-1, 6),
                         orig_shape=(100, 100)).numpy()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        initial = tracker.update(detections([[10, 10, 30, 50, .9, 0]]), frame)
        self.assertEqual(len(initial), 1)
        tracker.update(detections([]), frame)
        recovered = tracker.update(detections([[11, 10, 31, 50, .9, 0]]), frame)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(initial[0, 4], recovered[0, 4])


if __name__ == '__main__':
    unittest.main()
