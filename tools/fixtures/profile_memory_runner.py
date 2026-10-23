"""Owned fault fixture: pause after real sanitized commit, before observation."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import event_memory_runner as runner

record = runner.record_network_source

def pause(ffmpeg, source, target, seconds):
    record(ffmpeg, source, target, seconds)
    (target.parent/'profile-pause.json').write_text(json.dumps(dict(pid=os.getpid())))
    deadline = time.monotonic()+120
    while not (target.parent/'profile-release').exists():
        if time.monotonic() >= deadline: raise RuntimeError('owned_profile_barrier_expired')
        time.sleep(.05)

runner.record_network_source = pause
runner.main()
