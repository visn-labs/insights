#!/usr/bin/env python3
"""Owned Rust/real SQLite-index-queue replay with deterministic test embeddings.

Actual privacy models/capture and actual video decoding run. Embeddings are a
test double: this verifies delivery/recovery, not semantic quality or GPU speed.
"""
import argparse
from contextlib import closing
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
import uuid

from check_recording_recovery import ROOT, Service, wait_until
from check_retrieval import Models
from semantic_memory_server import Retrieval, handler_for
from workflow_indexing import IndexJobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--ui-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.ui_seconds <= 600:
        parser.error('ui-seconds must be 0..600')
    output = (ROOT / args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    observations = []
    source = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler,
        directory=str(ROOT / 'output/depin-implementation/bp03-fixtures')))
    source_thread = threading.Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1', 0))
        worker_port = port_probe.getsockname()[1]
    rust = Service(output / 'data', output)
    rust.overrides.update(VISN_MEMORY_RUNNER_ARGS='tools/event_memory_runner.py',
        VISN_RETRIEVAL_URL=f'http://127.0.0.1:{worker_port}', VISN_RETRIEVAL_TIMEOUT_SECS='5')
    worker = server = worker_thread = None
    deliveries = []
    fault = {'scope': False, 'retry_ack': False, 'retry_dropped': False}

    def request(route, body=None, expected=200):
        req = urllib.request.Request(rust.base + route, data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            value = json.load(response)
            assert response.status in (expected if isinstance(expected, tuple) else (expected,)), (response.status, value)
        observations.append(dict(route=route, status=response.status, response=value))
        return value

    try:
        rust.start()
        print('Capture while indexing worker is unavailable', flush=True)
        job = request('/api/v1/memory-jobs', dict(name='projection recovery',
            cameras=[dict(camera_id='projection-camera', live_url=f'http://127.0.0.1:{source.server_port}/face-sequence.mkv')],
            monitor_duration_secs=2, observer_fps=2, vlm_enabled=False), 202)
        route = '/api/v1/memory-jobs/' + job['id']

        def pending():
            current = request(route)
            if current['status'] == 'failed':
                raise AssertionError(current)
            projection = current.get('visual_projection')
            return current if current['status'] == 'completed' and projection and projection['attempts'] > 0 else None

        job = wait_until(pending, 40)
        revision = job['visual_projection']['revision']
        assert job['visual_projection']['state'] == 'pending'
        assert job['visual_projection']['last_error'] == 'projection_worker_unavailable'
        manifest = rust.data / 'memory/manifests' / (job['id'] + '.json')
        saved = json.loads(manifest.read_text())
        assert saved['status'] == 'completed' and saved['visual_projection']['revision'] == revision
        if args.ui_seconds:
            (output / 'ui-info.json').write_text(json.dumps(dict(base=rust.base, job_id=job['id'])))
            print('Waiting for owned browser probe; touch OUTPUT/ui-done to continue', flush=True)
            deadline = time.monotonic() + args.ui_seconds
            while time.monotonic() < deadline and not (output / 'ui-done').exists():
                time.sleep(.2)
        rust.stop(hard=True)
        rust.start()
        assert request(route)['visual_projection']['revision'] == revision
        print('Start real indexing queue; deliberately lose the first committed-task acknowledgement', flush=True)
        worker = Retrieval(SimpleNamespace(data_dir=str(rust.data), frames=2, frame_size=64), Models())
        worker.indexer = IndexJobs(worker)
        base = handler_for(worker)

        class DropAcknowledgement(base):
            def reply(self, status, body):
                if self.path.endswith('/retry') and status == 200 and fault['retry_ack']:
                    fault['retry_ack'] = False
                    fault['retry_dropped'] = True
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', '1000')
                    self.end_headers()
                    self.wfile.write(b'{"id":')
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.close()
                    return
                if self.path == '/index-jobs' and status == 202:
                    deliveries.append(body['id'])
                    if len(deliveries) == 1:
                        self.send_response(status)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', '1000')
                        self.end_headers()
                        self.wfile.write(b'{"id":')
                        self.wfile.flush()
                        self.close_connection = True
                        self.connection.close()
                        return
                    if fault['scope']:
                        body = dict(body, scope_id=str(uuid.uuid4()))
                return super().reply(status, body)

        server = ThreadingHTTPServer(('127.0.0.1', worker_port), DropAcknowledgement)
        worker_thread = threading.Thread(target=server.serve_forever, daemon=True)
        worker_thread.start()
        worker.indexer.start()

        def complete():
            current = request(route)
            projection = current['visual_projection']
            assert projection['state'] not in ('blocked', 'failed', 'cancelled'), projection
            return current if projection['state'] == 'completed' else None

        # A held UI probe may already have reached the documented 64-second failure backoff.
        final = wait_until(complete, 180)
        projection = final['visual_projection']
        assert projection['revision'] == revision
        assert len(deliveries) >= 2 and len(set(deliveries)) == 1, deliveries
        with closing(worker.indexer.connect()) as db:
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
        assert projection['receipt']['embedding']['model'] == 'test-only-v1'
        assert projection['receipt']['completed'] == final['result']['events_indexed']
        rust.stop(hard=True)
        rust.start()
        assert request(route)['visual_projection'] == projection
        print('Reject a receipt from the wrong scope; explicit prepare recovers without duplicate jobs', flush=True)
        rust.stop()
        saved = json.loads(manifest.read_text())
        saved['visual_projection'].update(state='pending', task_id=None, attempts=0,
            next_attempt_at_ms=0, receipt=None)
        manifest.write_text(json.dumps(saved))
        fault['scope'] = True
        rust.start()

        def blocked():
            current = request(route)
            return current if current['visual_projection']['state'] == 'blocked' else None

        invalid = wait_until(blocked, 20)
        assert invalid['visual_projection']['last_error'] == 'projection_receipt_scope_mismatch'
        request(route + '/visual-index', {}, 502)
        fault['scope'] = False
        request(route + '/visual-index', {}, 202)
        repaired = wait_until(complete, 20)
        assert repaired['visual_projection']['revision'] != revision
        with closing(worker.indexer.connect()) as db:
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
        print('Retry route republishes durable intent after a terminal worker failure', flush=True)
        previous_revision = repaired['visual_projection']['revision']
        task_id = repaired['visual_projection']['task_id']
        # Inject only the terminal failure boundary; retain already completed interval artifacts.
        with closing(worker.indexer.connect()) as db, db:
            db.execute("UPDATE jobs SET state='failed',error='fixture terminal failure' WHERE id=?", (task_id,))
        fault['retry_ack'] = True
        request(route + f'/visual-index/{task_id}/retry', {}, (200, 502))
        assert fault['retry_dropped']
        rust.stop(hard=True)
        rust.start()
        repaired = wait_until(complete, 20)
        assert repaired['visual_projection']['revision'] != previous_revision
        print('Changed sanitized source is blocked before explicit or replay indexing', flush=True)
        path = rust.data / 'memory' / job['id'] / 'projection-camera/source.mkv'
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'changed')
            request(route + '/visual-index', {}, 400)
            with closing(worker.indexer.connect()) as db:
                assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
            event = json.loads(db_event(worker))
            try:
                worker.source(event)
            except ValueError as error:
                assert 'Sanitized source hash' in str(error)
            else:
                raise AssertionError('Worker ignored expected committed-source hash')
        finally:
            path.write_bytes(original)
        (output / 'results.json').write_text(json.dumps(dict(scope=__doc__,
            deliveries=deliveries, final=repaired, rejected_receipt=invalid,
            observations=observations), indent=2) + '\n')
        print(f'Passed durable replay, lost acknowledgement, restart and tamper checks: {output}', flush=True)
    finally:
        rust.stop()
        if server:
            server.shutdown()
            server.server_close()
            worker_thread.join(timeout=5)
        if worker:
            worker.indexer.close()
            worker.db.close()
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=5)


def db_event(worker):
    with closing(worker.indexer.connect()) as db:
        return db.execute('SELECT event FROM items LIMIT 1').fetchone()[0]


if __name__ == '__main__':
    main()
