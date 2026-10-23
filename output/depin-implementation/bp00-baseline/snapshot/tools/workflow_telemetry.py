"""Bounded request diagnostics; no model imports, GPU synchronization or cache changes."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import logging
import re
import sys
import time
import uuid

LOG = logging.getLogger('visn.telemetry')
CURRENT = ContextVar('visn_workflow_trace', default=None)


def trace_id(value=None):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) else str(uuid.uuid4())


def resources(mps_ready=False):
    result = {'process_lifetime_peak_rss_bytes': None, 'current_rss_bytes': None,
              'accelerator': None, 'scope': 'this Python worker only; excludes child decoders and external model servers'}
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result['process_lifetime_peak_rss_bytes'] = int(value if sys.platform == 'darwin' else value * 1024)
        if sys.platform.startswith('linux'):
            import os
            with open('/proc/self/statm') as handle:
                result['current_rss_bytes'] = int(handle.read().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (ImportError, OSError, ValueError, IndexError):
        result['rss_unavailable'] = True
    # Inspect only an already-loaded library. Do not load weights or initialize a
    # CUDA context for telemetry, and never reset global allocation statistics.
    torch = sys.modules.get('torch')
    if torch is not None:
        try:
            if torch.cuda.is_initialized():
                result['accelerator'] = dict(backend='cuda', allocated_bytes=torch.cuda.memory_allocated(),
                    reserved_bytes=torch.cuda.memory_reserved(), process_lifetime_peak_allocated_bytes=torch.cuda.max_memory_allocated())
            elif mps_ready:
                result['accelerator'] = dict(backend='mps', allocated_bytes=torch.mps.current_allocated_memory(),
                    driver_allocated_bytes=torch.mps.driver_allocated_memory(), process_lifetime_peak_allocated_bytes=None)
        except (AttributeError, RuntimeError, AssertionError):
            result['accelerator_unavailable'] = True
    return result


class WorkflowTrace:
    def __init__(self, request_id=None, operation='workflow', run_id=None, enabled=True, mps_ready=None):
        self.request_id, self.run_id = trace_id(request_id), trace_id(run_id) if run_id else None
        self.operation, self.enabled = operation, enabled
        self.stages, self.stack = {}, []
        self.status = 'ok'
        self.mps_ready = mps_ready

    def resources(self):
        try:
            return resources(mps_ready=bool(self.mps_ready and self.mps_ready()))
        except Exception as error:
            return dict(unavailable=True, error_type=type(error).__name__)

    def __enter__(self):
        self.started, self.cpu_started = time.perf_counter(), time.process_time()
        self.start_resources = self.resources() if self.enabled else None
        self.token = CURRENT.set(self if self.enabled else None)
        return self

    def __exit__(self, kind, value, traceback):
        if kind is not None: self.status = 'error'
        try:
            if self.enabled:
                LOG.info('WORKFLOW_TRACE %s', json.dumps(self.report(final=True), allow_nan=False, separators=(',', ':')))
        finally:
            CURRENT.reset(self.token)

    @contextmanager
    def span(self, name):
        # Stage names come from code, never from object/query contents.
        entry = [name, time.perf_counter(), 0.0]
        self.stack.append(entry)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = max(0.0, time.perf_counter() - entry[1])
            self.stack.pop()
            if self.stack: self.stack[-1][2] += elapsed
            value = self.stages.setdefault(name, dict(calls=0, failures=0, inclusive_host_secs=0.0, exclusive_host_secs=0.0))
            value['calls'] += 1
            value['failures'] += int(failed)
            value['inclusive_host_secs'] += elapsed
            value['exclusive_host_secs'] += max(0.0, elapsed - entry[2])

    def report(self, final=False):
        return dict(version='workflow-telemetry-v1', request_id=self.request_id, run_id=self.run_id,
            operation=self.operation, status=self.status, final=final,
            report_scope='completed_worker_request' if final else 'before_response_serialization',
            elapsed_host_secs=max(0.0, time.perf_counter()-self.started),
            process_cpu_secs=max(0.0, time.process_time()-self.cpu_started),
            stages={k:dict(v) for k,v in self.stages.items()}, resources_start=self.start_resources,
            resources_end=self.resources(), queue_policy='bounded_interactive_priority_with_background_yield' if self.operation in ('/search','/interpret','/index','/index-jobs') else 'not_measured_in_this_process',
            measurement_notes=['Host wall time; asynchronous accelerator kernels are not synchronized.',
                               'Nested inclusive times overlap; use exclusive times for additive attribution.',
                               'RSS/accelerator high-water marks are process-lifetime, not request-local peaks.',
                               'Do not sum CPU RSS and accelerator allocation on unified-memory hosts.',
                               'Unavailable measurements remain null or explicitly unavailable.'])


@contextmanager
def stage(name):
    trace = CURRENT.get()
    if trace is None:
        yield
    else:
        with trace.span(name): yield


def measured(name):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            if CURRENT.get() is None: return function(*args, **kwargs)
            with stage(name): return function(*args, **kwargs)
        return call
    return decorate
