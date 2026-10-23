"""Stop this owned process group when its Rust parent closes/dies.

The inherited owner descriptor separately prevents concurrent writers until the
worker exits. This module is used only by isolated worker entry points.
"""
import os
import signal
import threading

_started=False


def start():
    global _started
    raw=os.environ.get('VISN_WORKER_LIFELINE_FD')
    if not raw or _started:
        return
    descriptor=int(raw)
    if descriptor < 3:
        raise ValueError('invalid_worker_lifeline_descriptor')
    # Never signal a caller's shell/session group if the worker was misconfigured.
    if os.getpgrp()!=os.getpid():
        raise RuntimeError('worker_process_group_not_isolated')
    _started=True

    def watch():
        try:
            os.read(descriptor,1)
        finally:
            os.killpg(os.getpgrp(),signal.SIGKILL)

    threading.Thread(target=watch,name='rust-parent-lifeline',daemon=True).start()
