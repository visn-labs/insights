#!/usr/bin/env python3
"""Fault fixture: real sanitizer pauses after the first committed source session.

No raw frame or synthetic detection is written. The API test cancels this owned
worker while its second session is uncommitted, then resumes with the normal runner.
"""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import privacy_media
import event_memory_runner


OriginalDetector = privacy_media.PrivacyDetector


class PauseAfterCommit(OriginalDetector):
    def detect(self, image):
        directory = Path(sys.argv[sys.argv.index('--output-dir') + 1])
        marker = directory/'capture/capture.json'
        if marker.exists():
            manifest = json.loads(marker.read_text())
            if manifest['sessions']:
                (directory/'prefix-pause.json').write_text(json.dumps(dict(
                    pid=os.getpid(), sessions=len(manifest['sessions']), state=manifest['state'])))
                while True:
                    time.sleep(.2)
        return super().detect(image)


if __name__ == '__main__':
    privacy_media.PrivacyDetector = PauseAfterCommit
    event_memory_runner.main()
