"""Durable clip preparation and bounded, single-inference admission.

SQLite task connections are short-lived; the existing vector connection and all
model/cache operations remain owned by the inference gate, one operation at a time.
"""
from collections import deque
from contextlib import closing
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from workflow_telemetry import WorkflowTrace, stage
from workflow_resources import ResourceBusy

LOG = logging.getLogger('visn.indexing')
TERMINAL = ('completed', 'failed', 'cancelled')


class AdmissionGate:
    def __init__(self, limit=8, burst=4):
        self.condition = threading.Condition()
        self.waiters = deque()
        self.active = None
        self.streak = 0
        self.limit, self.burst = limit, burst

    def acquire(self, blocking=True, timeout=None, *, background=False):
        deadline = time.monotonic() + timeout if timeout is not None else None
        with self.condition:
            if (not blocking and (self.active or self.waiters)) or (
                not background and sum(not b for _, b in self.waiters) >= self.limit
            ):
                return False
            ticket = (object(), background)
            self.waiters.append(ticket)
            try:
                while True:
                    interactive = next((t for t in self.waiters if not t[1]), None)
                    batch = next((t for t in self.waiters if t[1]), None)
                    selected = batch if batch and (not interactive or self.streak >= self.burst) else interactive
                    if not self.active and selected == ticket:
                        self.active = 'background' if background else 'interactive'
                        self.streak = 0 if background else self.streak + 1
                        return True
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if not blocking or (remaining is not None and remaining <= 0):
                        return False
                    self.condition.wait(remaining)
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()

    def release(self):
        with self.condition:
            self.active = None
            self.condition.notify_all()

    def locked(self):
        with self.condition:
            return self.active is not None

    def snapshot(self):
        with self.condition:
            return dict(active=self.active, interactive_waiting=sum(not b for _, b in self.waiters),
                        background_waiting=sum(b for _, b in self.waiters), interactive_limit=self.limit,
                        interactive_burst=self.burst, policy='one_inference_interactive_priority_bounded_burst')


class QueueFull(Exception):
    pass


class IndexJobs:
    def __init__(self, service):
        self.service = service
        self.path = service.root / ('index-jobs-' + service.vector_policy + '-v1.sqlite3')
        self.limit = getattr(service.args, 'index_queue_limit', 8)
        self.max_attempts = getattr(service.args, 'index_max_attempts', 2)
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        # One task owner per policy/database. A second worker must not reset an
        # active owner's running records or concurrently mutate its vector cache.
        self.owner = open(str(self.path) + '.lock', 'a')
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise RuntimeError('An indexing worker already owns this data directory and vector policy')
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, scope TEXT NOT NULL, version TEXT NOT NULL,
                  state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                  error TEXT);
                CREATE TABLE IF NOT EXISTS items (
                  job TEXT NOT NULL, ordinal INTEGER NOT NULL, event TEXT NOT NULL,
                  sha TEXT NOT NULL, artifact_key TEXT NOT NULL, state TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
                  PRIMARY KEY(job, ordinal));
                CREATE INDEX IF NOT EXISTS jobs_scope ON jobs(scope, created);
                CREATE INDEX IF NOT EXISTS items_state ON items(job, state, ordinal);
            ''')
            db.execute("UPDATE items SET state='pending' WHERE state='running'")
            db.execute("UPDATE jobs SET state='queued',updated=? WHERE state='running'", (time.time(),))
        with service.revocations:
            service.revocations.execute("INSERT OR IGNORE INTO revocation_cleanup SELECT scope,?,'pending' FROM revocations",(service.vector_policy,))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def identity(self, body):
        events, scope = body.get('events'), body.get('scope_id')
        if not isinstance(scope, str) or not 1 <= len(scope) <= 128:
            raise ValueError('A scope_id of 1..128 characters is required')
        if self.scope_revoked(scope):
            raise ValueError('Recording scope is revoked')
        if not isinstance(events, list) or not 1 <= len(events) <= 10000:
            raise ValueError('Index jobs require 1..10000 events')
        if any(not isinstance(e, dict) or not isinstance(e.get('event_id'), str) for e in events):
            raise ValueError('Every event needs a string event_id')
        if len({e['event_id'] for e in events}) != len(events):
            raise ValueError('Duplicate event IDs')
        self.service.ensure_models()
        version = self.service.models.version
        items = []
        for event in sorted(events, key=lambda e: e['event_id']):
            path, sha = self.service.source(event)
            clean = dict(event_id=event['event_id'], source_path=str(path), start_ms=event['start_ms'], end_ms=event['end_ms'])
            if event.get('expected_source_sha256') is not None:
                clean['expected_source_sha256'] = event['expected_source_sha256']
            items.append((clean, sha, self.service.embedding_key(clean, sha)))
        contract = json.dumps([scope, self.service.vector_policy, version, items], sort_keys=True)
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, hashlib.sha256(contract.encode()).hexdigest()))
        return job_id,scope,version,items

    def resolve(self, body):
        job_id,_,_,_=self.identity(body)
        return self.get(job_id)

    def submit(self, body):
        job_id,scope,version,items=self.identity(body)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not previous:
                self.capacity(db)
                now = time.time()
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,NULL)', (job_id, scope, version, 'queued', now, now))
                db.executemany('INSERT INTO items(job,ordinal,event,sha,artifact_key,state) VALUES (?,?,?,?,?,?)',
                    [(job_id, i, json.dumps(e), sha, key, 'pending') for i, (e, sha, key) in enumerate(items)])
            elif previous[0] == 'completed':
                missing = [i for i, (_, _, key) in enumerate(items) if not self.service.artifact_valid(key)]
                if missing:
                    self.capacity(db)
                    db.executemany("UPDATE items SET state='pending',attempts=0,error=NULL WHERE job=? AND ordinal=?",[(job_id,i) for i in missing])
                    db.execute("UPDATE jobs SET state='queued',updated=? WHERE id=?",(time.time(),job_id))
        self.wake.set()
        return self.get(job_id)

    def capacity(self, db):
        if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= self.limit:
            raise QueueFull('Background indexing queue is full; retry after a task completes')

    def get(self, job_id):
        with closing(self.connect()) as db:
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if job is None:
                return None
            counts = dict(db.execute('SELECT state,count(*) FROM items WHERE job=? GROUP BY state', (job_id,)).fetchall())
            errors = [dict(row) for row in db.execute('SELECT ordinal,attempts,error FROM items WHERE job=? AND error IS NOT NULL ORDER BY ordinal LIMIT 10', (job_id,))]
            return dict(id=job['id'], scope_id=job['scope'], status=job['state'], created_at=job['created'], updated_at=job['updated'],
                        total=sum(counts.values()), completed=counts.get('completed',0), failed=counts.get('failed',0),
                        pending=counts.get('pending',0)+counts.get('running',0), error=job['error'], item_errors=errors,
                        vector_policy=self.service.vector_policy, embedding=json.loads(job['version']), max_attempts=self.max_attempts,
                        profile_current=(self.service.models.version == job['version']) if self.service.models is not None else None,
                        coverage='prepared_intervals_only_not_exhaustive_scene_coverage')

    def list(self, scope):
        with closing(self.connect()) as db:
            ids = db.execute('SELECT id FROM jobs WHERE scope=? ORDER BY created DESC,id LIMIT 20', (scope,)).fetchall()
        return [self.get(row[0]) for row in ids]

    def action(self, job_id, action):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state,scope FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return None
            if self.scope_revoked(row['scope']):
                raise ValueError('Recording scope is revoked')
            if action == 'cancel' and row[0] not in TERMINAL:
                db.execute("UPDATE jobs SET state='cancelled',updated=? WHERE id=?", (time.time(),job_id))
            elif action == 'retry' and row[0] in ('failed','cancelled'):
                self.capacity(db)
                db.execute("UPDATE items SET state='pending',attempts=0,error=NULL WHERE job=? AND state!='completed'", (job_id,))
                db.execute("UPDATE jobs SET state='queued',updated=?,error=NULL WHERE id=?", (time.time(),job_id))
            elif action not in ('cancel','retry'):
                raise ValueError('Unknown index job action')
        self.wake.set()
        return self.get(job_id)

    def scope_revoked(self, scope):
        return self.service.revocations.execute('SELECT 1 FROM revocations WHERE scope=?',(scope,)).fetchone() is not None

    def scope_sources(self,scope):
        paths=set()
        for policy,filename in self.service.vector_databases.items():
            queue=self.service.root/('index-jobs-'+policy+'-v1.sqlite3')
            if queue.exists():
                with closing(sqlite3.connect(queue,timeout=10)) as db:
                    for (event,) in db.execute('SELECT items.event FROM items JOIN jobs ON items.job=jobs.id WHERE jobs.scope=?',(scope,)):
                        paths.add(json.loads(event)['source_path'])
            cache=self.service.root/filename
            if cache.exists():
                with closing(sqlite3.connect(cache,timeout=10)) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifact_sources'").fetchone():
                        paths.update(row[0] for row in db.execute('SELECT DISTINCT source_path FROM artifact_sources WHERE source_path LIKE ?',('%/memory/'+scope+'/%',)))
        paths={str(Path(path).resolve()) for path in paths}
        if len(paths)>10000 or any(not Path(path).is_relative_to(self.service.root) for path in paths):
            raise ValueError('Invalid worker scope source contract')
        return paths

    def revoke(self, body):
        if not isinstance(body,dict):
            raise ValueError('Expected a revocation object')
        scope, revision, paths = body.get('scope_id'), body.get('revision'), body.get('source_paths')
        mode=body.get('source_contract','paths-v1')
        if not isinstance(scope,str) or not 1 <= len(scope) <= 128 or not isinstance(revision,str):
            raise ValueError('Invalid revocation scope/revision')
        uuid.UUID(revision)
        if mode not in ('paths-v1','recording-scope-v1'):raise ValueError('Unknown revocation source contract')
        if mode=='recording-scope-v1':
            if str(uuid.UUID(scope))!=scope:raise ValueError('Recording scope must be a canonical UUID')
            paths=sorted(self.scope_sources(scope))
        if not isinstance(paths,list) or not (0 if mode=='recording-scope-v1' else 1) <= len(paths) <= 10000 or any(not isinstance(p,str) for p in paths):
            raise ValueError('Revocation requires 1..10000 source paths')
        paths = sorted({str(Path(p).resolve()) for p in paths})
        if any(not Path(p).is_relative_to(self.service.root) for p in paths):
            raise ValueError('Revocation source outside configured evidence directory')
        db = self.service.revocations
        with db:
            old = db.execute('SELECT revision FROM revocations WHERE scope=?',(scope,)).fetchone()
            if old and old[0] != revision:
                raise ValueError('Revocation revision conflict')
            previous={r[0] for r in db.execute('SELECT path FROM revoked_sources WHERE scope=?',(scope,))}
            if mode=='recording-scope-v1':paths=sorted(set(paths)|previous)
            if old and mode=='paths-v1' and paths != sorted(previous):
                raise ValueError('Revocation source contract conflict')
            db.execute('INSERT OR IGNORE INTO revocations VALUES (?,?,?)',(scope,revision,time.time()))
            db.executemany('INSERT OR IGNORE INTO revoked_sources VALUES (?,?)',[(p,scope) for p in paths])
            for policy, filename in self.service.vector_databases.items():
                if (self.service.root / filename).exists():
                    db.execute("INSERT OR IGNORE INTO revocation_cleanup VALUES (?,?,'pending')",(scope,policy))
                    if set(paths)-previous:db.execute("UPDATE revocation_cleanup SET state='pending' WHERE scope=? AND policy=?",(scope,policy))
        # Source denial is durable before the first cache deletion. Repeated delivery is safe.
        receipt=self.cleanup_revocation(scope, revision)
        receipt['source_contract']=mode
        return receipt

    @staticmethod
    def purge_artifacts(db, paths):
        db.execute('CREATE TABLE IF NOT EXISTS artifact_sources (kind TEXT NOT NULL,key TEXT NOT NULL,source_path TEXT NOT NULL,PRIMARY KEY(kind,key,source_path))')
        db.execute('CREATE TEMP TABLE IF NOT EXISTS revoked_paths(path TEXT PRIMARY KEY)')
        db.execute('DELETE FROM revoked_paths')
        db.executemany('INSERT INTO revoked_paths VALUES (?)',[(p,) for p in paths])
        for kind, table in [('vector','vectors'),('interpretation','interpretations')]:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                continue
            # Keep a shared artifact only if another non-revoked source independently references it.
            # Legacy untracked rows cannot be attributed safely, so invalidate them conservatively.
            db.execute(f'''DELETE FROM {table} WHERE key NOT IN (
                SELECT key FROM artifact_sources WHERE kind=? AND source_path NOT IN (SELECT path FROM revoked_paths)
            )''',(kind,))
        db.execute('DELETE FROM artifact_sources WHERE source_path IN (SELECT path FROM revoked_paths)')
        db.commit()

    @staticmethod
    def cancel_scope(path, scope):
        if not path.exists(): return
        with closing(sqlite3.connect(path,timeout=10)) as db, db:
            db.execute("UPDATE jobs SET state='cancelled',error='scope_revoked',updated=? WHERE scope=?",(time.time(),scope))
            db.execute("UPDATE items SET state='cancelled',error='scope_revoked' WHERE job IN (SELECT id FROM jobs WHERE scope=?)",(scope,))

    def cleanup_revocation(self, scope, revision):
        db = self.service.revocations
        paths = [r[0] for r in db.execute('SELECT DISTINCT path FROM revoked_sources')]
        pending = [r[0] for r in db.execute("SELECT policy FROM revocation_cleanup WHERE scope=? AND state='pending'",(scope,))]
        for policy in pending:
            queue = self.service.root / ('index-jobs-' + policy + '-v1.sqlite3')
            owner = None
            try:
                if policy != self.service.vector_policy:
                    owner = open(str(queue)+'.lock','a')
                    try: fcntl.flock(owner,fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError: continue  # Active policy owner replays its own pending cleanup.
                self.cancel_scope(queue,scope)
                if policy == self.service.vector_policy:
                    self.purge_artifacts(self.service.db,paths)
                    self.service.decode_cache.clear();self.service.decode_cache_bytes=0
                    self.service.hashes.clear();self.service.query_cache.clear();self.service.rerank_cache.clear()
                else:
                    with closing(sqlite3.connect(self.service.root/self.service.vector_databases[policy],timeout=10)) as cache:
                        self.purge_artifacts(cache,paths)
                with db:
                    db.execute("UPDATE revocation_cleanup SET state='completed' WHERE scope=? AND policy=?",(scope,policy))
            finally:
                if owner: owner.close()
        remaining = [r[0] for r in db.execute("SELECT policy FROM revocation_cleanup WHERE scope=? AND state!='completed'",(scope,))]
        return dict(scope_id=scope,revision=revision,status='revoked',cleanup_complete=not remaining,
                    pending_policies=remaining,media_erased=False)

    def run_one(self):
        # The background thread yields the model/cache connection between clips.
        # Kernels are not preempted and task cancellation is cooperative.
        with closing(self.connect()) as db:
            revocation = self.service.revocations.execute('''SELECT r.scope,r.revision FROM revocations r
                JOIN revocation_cleanup c ON r.scope=c.scope WHERE c.state='pending' ORDER BY r.created LIMIT 1''').fetchone()
            if not revocation and not db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
                return False
        if not self.service.lock.acquire(timeout=0.5, background=True):
            return False
        try:
            if revocation:
                return self.cleanup_revocation(*revocation)['cleanup_complete']
            with self.service.inference_resources(timeout=0.5):
                return self.run_admitted()
        except ResourceBusy:
            return False
        finally:
            self.service.lock.release()

    def run_admitted(self):
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT * FROM jobs WHERE state IN ('queued','running') ORDER BY updated,id LIMIT 1").fetchone()
            if row is None:
                return False
            job_id = row['id']
            db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (time.time(),job_id))
            item = db.execute("SELECT * FROM items WHERE job=? AND state='pending' ORDER BY ordinal LIMIT 1", (job_id,)).fetchone()
            if item:
                cached = self.service.artifact_valid(item['artifact_key'])
                charge = int(not cached and item['attempts'] < self.max_attempts)
                db.execute("UPDATE items SET state='running',attempts=attempts+? WHERE job=? AND ordinal=?", (charge,job_id,item['ordinal']))
        with WorkflowTrace(job_id, '/index', enabled=not getattr(self.service.args,'disable_telemetry',True), mps_ready=self.service.mps_ready) as trace:
            try:
                self.service.ensure_models()
                if self.service.models.version != row['version']:
                    raise ValueError('Embedding profile changed; submit a new job for the current profile')
                if self.service.interpreter:
                    self.service.interpreter.release()
                if item:
                    if item['attempts'] >= self.max_attempts and not cached:
                        raise RuntimeError('Retry budget exhausted after interruption')
                    event = json.loads(item['event'])
                    if self.service.source(event)[1] != item['sha']:
                        raise ValueError('Source changed; submit a new job for the retained source')
                    self.service.embedding(event, expected_digest=item['sha'])
                    with closing(self.connect()) as db, db:
                        db.execute("UPDATE items SET state='completed',error=NULL WHERE job=? AND ordinal=?", (job_id,item['ordinal']))
                with stage('index_finalize'):
                    self.finish(job_id)
            except Exception as error:
                trace.status = 'error'
                LOG.exception('Index task %s failed', job_id)
                # Keep paths/credentials out of public progress errors.
                message = type(error).__name__ + ': indexing failed; inspect worker logs'
                with closing(self.connect()) as db, db:
                    if item:
                        retry = not isinstance(error, ValueError) and item['attempts']+1 < self.max_attempts
                        db.execute('UPDATE items SET state=?,attempts=?,error=? WHERE job=? AND ordinal=?',
                            ('pending' if retry else 'failed',min(self.max_attempts,item['attempts']+1),message,job_id,item['ordinal']))
                    else:
                        db.execute("UPDATE jobs SET state='failed',error=?,updated=? WHERE id=? AND state!='cancelled'", (message,time.time(),job_id))
                if item:
                    self.finish(job_id)
        return True

    def finish(self, job_id):
        # Recheck every source after the last artifact is durable, before the
        # complete marker. Source bytes and the embedding profile define readiness.
        with closing(self.connect()) as db:
            job = db.execute('SELECT state,version FROM jobs WHERE id=?',(job_id,)).fetchone()
            if job['state'] == 'cancelled':
                return
            if db.execute("SELECT 1 FROM items WHERE job=? AND state IN ('pending','running') LIMIT 1",(job_id,)).fetchone():
                return
            items = db.execute('SELECT * FROM items WHERE job=? ORDER BY ordinal',(job_id,)).fetchall()
        failed = any(i['state']=='failed' for i in items)
        if not failed:
            if self.service.models.version != job['version']:
                raise ValueError('Embedding profile changed before completion')
            for item in items:
                if self.service.source(json.loads(item['event']))[1] != item['sha']:
                    raise ValueError('Source changed before index completion')
                if not self.service.artifact_valid(item['artifact_key']):
                    raise ValueError('A prepared artifact is missing or invalid before completion')
        with closing(self.connect()) as db, db:
            db.execute("UPDATE jobs SET state=?,error=?,updated=? WHERE id=? AND state!='cancelled'",
                       ('failed' if failed else 'completed', 'Some intervals failed; coverage is incomplete' if failed else None, time.time(),job_id))

    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    worked = self.run_one()
                except Exception:
                    LOG.exception('Index dispatcher failed; durable task state retained')
                    worked = False
                if not worked:
                    self.service.maintain_resources()
                    self.wake.wait(0.5)
                    self.wake.clear()
        self.thread = threading.Thread(target=run, name='visn-indexing', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join()
        self.owner.close()
