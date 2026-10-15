"""Regression: MJPEG copied to MP4 is not browser evidence; transcode it."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import subprocess
import unittest
import cv2
import imageio_ffmpeg
import numpy as np
from event_memory_runner import analyze

class MemoryRunnerCheck(unittest.TestCase):
    def test_mjpeg_browser_clip_and_exact_duration(self):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.mkv'
            subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i',
                'testsrc2=size=128x96:rate=4:duration=2','-c:v','mjpeg','-threads','1',str(source)], check=True)
            args = SimpleNamespace(observer_fps=1, max_seconds=2, max_events=10,
                minimum_event_seconds=2, maximum_event_seconds=15, clip_mode='copy', threads=1)
            result = analyze(cv2,np,ffmpeg,source,root,args)
            self.assertEqual(result['duration_ms'],2000)
            self.assertEqual(result['frames_decoded'],8)
            self.assertEqual(result['events'][0]['end_ms'],2000)
            clip = cv2.VideoCapture(str(root/result['events'][0]['clip_file']))
            codec = int(clip.get(cv2.CAP_PROP_FOURCC))
            self.assertIn(''.join(chr(codec >> (8*i) & 255) for i in range(4)).lower(),('h264','avc1'))
            self.assertTrue(clip.read()[0]);clip.release()

if __name__ == '__main__':
    unittest.main()
