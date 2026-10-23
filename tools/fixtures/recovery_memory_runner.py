"""Fault-injection fixture: pause after REAL sanitized capture, before observation.

Not a production worker. Resume bypasses this hook because it never opens source.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import event_memory_runner as runner

record=runner.record_network_source


def pause_after_capture(ffmpeg,source,target,seconds):
    before=os.environ.get('VISN_RECOVERY_PROBE_PHASE')=='before_capture'
    if not before:record(ffmpeg,source,target,seconds)
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
    marker=target.parent/'recovery-probe.json'
    temporary=marker.with_suffix('.pending')
    temporary.write_text(json.dumps(dict(worker_pid=os.getpid(),descendant_pid=child.pid,
                                         phase='before_capture' if before else 'after_capture'))+'\n')
    temporary.replace(marker)
    time.sleep(120)
    if before:raise RuntimeError('fixture_before_capture_timeout')


runner.record_network_source=pause_after_capture
runner.main()
