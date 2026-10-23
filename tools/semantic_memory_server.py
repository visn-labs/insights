#!/usr/bin/env python3
"""Local Qwen video retrieval sidecar. Run with the isolated retrieval environment."""
import argparse
from contextlib import contextmanager
from collections import OrderedDict
import gc
import hashlib
import io
import json
import logging
import math
import os
import re
from pathlib import Path
import sqlite3
import sys
import subprocess
import time
import weakref
import uuid
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from workflow_telemetry import CURRENT, WorkflowTrace, measured, stage
from workflow_indexing import AdmissionGate, IndexJobs, QueueFull
from workflow_resources import Residency, ResourceBusy, ManagedLMStudio, trim_allocator
from grouped_decode import decode_group, eligible
from retrieval_profile import FrozenFiles, model_files, runtime_identity, worker_profile, storage_identity, ProfileConflict

LOG = logging.getLogger("visn.retrieval")
ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REVISION = "393e2978d27852b0d0230d6994f37f9c15bed73c"
DECODE_CACHE_MAX_BYTES = 32 * 1024 * 1024
DECODE_CACHE_MAX_ENTRIES = 128
QUERY_CACHE_MAX_ENTRIES = 32
RERANK_CACHE_MAX_ENTRIES = 256
SOURCE_CACHE_MAX_ENTRIES = 256
VECTOR_POLICIES = ('legacy-renormalize-v1', 'canonical-unit-v2')
VECTOR_DATABASES = dict(zip(VECTOR_POLICIES, ('semantic-v1.sqlite3', 'semantic-canonical-v2.sqlite3')))

INSTRUCTION = "Retrieve camera video segments that visually match the search query."
LOADED_SERVER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
LOADED_LOCAL_SOURCES = {name:hashlib.sha256(Path(sys.modules[name].__file__).read_bytes()).hexdigest()
    for name in ['grouped_decode','workflow_resources','workflow_indexing','workflow_telemetry','retrieval_profile']}


def normalize(vector):
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v*v for v in values))
    if not values or not math.isfinite(norm) or norm < 1e-8:
        raise ValueError("Invalid embedding returned by model/cache")
    return [v/norm for v in values]


def unit_vector(vector):
    """Validate the model's normalized values without changing their precision."""
    if not isinstance(vector, (list, tuple)) or not vector or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
        raise ValueError('Invalid unit embedding')
    if abs(math.sqrt(sum(v*v for v in vector)) - 1.0) > 1e-6:
        raise ValueError('Embedding is not unit normalized')
    return vector


def fuse(lexical, semantic, limit):
    scores = {}
    for ranking in (lexical, semantic):
        for rank, event_id in enumerate(dict.fromkeys(ranking), 1):
            scores[event_id] = scores.get(event_id, 0) + 1/(60+rank)
    return sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:limit]


class QwenModels:
    def __init__(self, args):
        revision = subprocess.check_output(
            ["git", "-C", args.qwen_source, "rev-parse", "HEAD"], text=True).strip()
        if revision != UPSTREAM_REVISION:
            raise ValueError("Qwen wrapper revision mismatch; rerun tools/setup_retrieval.sh")
        sys.path.insert(0, str(Path(args.qwen_source).resolve()))
        from huggingface_hub import snapshot_download
        self.args = args
        self.embedder = self.reranker = None
        self.reranker_snapshot = None
        self.snapshot_download = snapshot_download
        # A resolved snapshot identifies the exact weights used by every cached vector.
        self.embedding_path = snapshot_download(args.embedding_model, revision=args.embedding_revision)
        self.reranker_path = snapshot_download(args.reranker_model, revision=args.reranker_revision)
        import torch
        # Resolve wrapper imports before freezing numerical runtime settings.
        from src.models.qwen3_vl_embedding import Qwen3VLEmbedder
        from src.models.qwen3_vl_reranker import Qwen3VLReranker
        self.device = args.device
        if self.device == 'auto':
            self.device = 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SERVER_SHA256:
            raise ValueError('Retrieval implementation changed since process startup')
        if any(hashlib.sha256(Path(sys.modules[name].__file__).read_bytes()).hexdigest() != expected for name,expected in LOADED_LOCAL_SOURCES.items()):
            raise ValueError('Loaded retrieval helper changed since process startup')
        self.artifacts = [model_files(self.embedding_path), model_files(self.reranker_path)]
        code = {str(path.relative_to(ROOT)):path for path in (ROOT/'tools').glob('*.py')}
        code.update({f'qwen/{path.relative_to(args.qwen_source)}':path for path in Path(args.qwen_source).rglob('*.py') if '.git' not in path.parts})
        self.artifacts.append(FrozenFiles(code, [(ROOT/'tools','*.py'),(Path(args.qwen_source),'**/*.py')]))
        implementation = self.artifacts[-1].identity['sha256']
        import imageio_ffmpeg
        self.ffmpeg_path = str(Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve())
        executable = FrozenFiles({'ffmpeg':Path(self.ffmpeg_path)})
        self.artifacts.append(executable)
        runtime = runtime_identity(torch, self.device)
        runtime['ffmpeg_sha256'] = executable.identity['files']['ffmpeg']
        self.runtime_identity = runtime
        common = dict(runtime=runtime, implementation=implementation)
        self.reranker_identity = dict(model=args.reranker_model, weights=self.artifacts[1].identity['sha256'], **common)
        self.version = json.dumps({"model": args.embedding_model, "weights":self.artifacts[0].identity['sha256'], **common,
                                   "upstream": UPSTREAM_REVISION, "frames": args.frames,
                                   "decode_policy":getattr(args,'decode_policy','single-seek-v1'),
                                   "pixels": args.frame_size, "instruction": INSTRUCTION,
                                   "sampler": "ffmpeg-pts-bounded-midpoints-v4-hold-last"}, sort_keys=True)

    def verify_profile(self):
        import imageio_ffmpeg
        import torch
        runtime = runtime_identity(torch, self.device)
        runtime['ffmpeg_sha256'] = self.runtime_identity['ffmpeg_sha256']
        if runtime != self.runtime_identity:
            raise ProfileConflict('Retrieval numerical runtime changed; stage a new profile')
        if str(Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()) != self.ffmpeg_path:
            raise ProfileConflict('Retrieval decoder selection changed; stage a new profile')
        for artifact in self.artifacts: artifact.verify()

    @measured('retrieval_model_load')
    def load(self, kind):
        import torch
        from src.models.qwen3_vl_embedding import Qwen3VLEmbedder
        from src.models.qwen3_vl_reranker import Qwen3VLReranker
        self.verify_profile()
        if getattr(self, kind) is not None:
            return getattr(self, kind)
        device = self.device
        if not self.args.keep_models_loaded:
            other = "reranker" if kind == "embedder" else "embedder"
            setattr(self, other, None)
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        model_path = self.embedding_path if kind == "embedder" else self.reranker_path
        if kind == "reranker":
            self.reranker_snapshot = model_path
        LOG.info("Loading %s on %s", kind, device)
        cls = Qwen3VLEmbedder if kind == "embedder" else Qwen3VLReranker
        model = cls(model_name_or_path=model_path, max_length=8192,
                    max_frames=self.args.frames, total_pixels=self.args.frames*self.args.frame_size**2,
                    torch_dtype=torch.float32 if device == "cpu" else torch.float16,
                    attn_implementation="sdpa")
        model.model.to(device)
        if kind == "reranker":
            model.device = torch.device(device)
            model.score_linear.to(device)
        # Upstream catches preprocessing errors and substitutes NULL. Never index that
        # as a successful video embedding or silently rerank a failed visual input.
        name = "_preprocess_inputs" if kind == "embedder" else "tokenize"
        original = getattr(model, name)
        def checked(conversations, **kwargs):
            with stage('model_preprocessing'):
                result = original(conversations, **kwargs)
            expects_video = any(part.get("type") == "video" for conversation in conversations
                                for message in conversation for part in message.get("content", [])
                                if isinstance(part, dict))
            if expects_video and "pixel_values_videos" not in result:
                raise ValueError("Video preprocessing failed; refusing text-only substitution")
            return result
        setattr(model, name, checked)
        setattr(self, kind, model)
        return model

    @measured('embedding_inference')
    def embed(self, value):
        import torch
        with torch.inference_mode():
            return normalize(self.load("embedder").process([value])[0].float().cpu().tolist())

    @measured('reranker_inference')
    def rerank(self, query, frames):
        import torch
        with torch.inference_mode():
            scores = self.load("reranker").process({"query": {"text": query},
                "documents": [{"video": frames}], "instruction": INSTRUCTION})
        score = float(scores[0])
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid reranker score")
        return score


class Retrieval:
    def __init__(self, args, models=None):
        self.args = args
        self.root = Path(args.data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.models = models
        self.vector_policy = getattr(args, 'vector_policy', VECTOR_POLICIES[0])
        if self.vector_policy not in VECTOR_POLICIES:
            raise ValueError('Unknown vector policy')
        self.decode_policy = getattr(args, 'decode_policy', 'single-seek-v1')
        if self.decode_policy not in ('single-seek-v1', 'grouped-select-v1'):
            raise ValueError('Unknown decode policy')
        self.hashes = OrderedDict()
        self.interpreter = None
        self.decode_cache = OrderedDict()
        self.decode_cache_bytes = 0
        self.query_cache = OrderedDict()
        self.rerank_cache = OrderedDict()
        self.work_stats = dict(decode_hits=0, ffmpeg_calls=0, decode_evictions=0, grouped_calls=0, grouped_fallbacks=0, query_hits=0, query_calls=0, rerank_hits=0, rerank_calls=0)
        self.vector_databases = VECTOR_DATABASES
        database = VECTOR_DATABASES[self.vector_policy]
        self.db = sqlite3.connect(self.root / database, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, version TEXT NOT NULL, vector TEXT NOT NULL)")
        self.db.execute('CREATE TABLE IF NOT EXISTS artifact_sources (kind TEXT NOT NULL,key TEXT NOT NULL,source_path TEXT NOT NULL,PRIMARY KEY(kind,key,source_path))')
        self.revocations = sqlite3.connect(self.root / 'revocations-v1.sqlite3', timeout=10, check_same_thread=False)
        weakref.finalize(self, self.revocations.close)
        self.revocations.executescript('''
            CREATE TABLE IF NOT EXISTS retrieval_storage_instance(version INTEGER PRIMARY KEY CHECK(version=1),id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revocations(scope TEXT PRIMARY KEY,revision TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS revoked_sources(path TEXT NOT NULL,scope TEXT NOT NULL,PRIMARY KEY(path,scope));
            CREATE TABLE IF NOT EXISTS revocation_cleanup(scope TEXT NOT NULL,policy TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(scope,policy));
        ''')
        self.revocations.execute('INSERT OR IGNORE INTO retrieval_storage_instance VALUES (1,?)',(uuid.uuid4().hex,))
        self.revocations.commit()
        self.lock = AdmissionGate(getattr(args, 'interactive_queue_limit', 8))
        self.indexer = None
        self.resources = Residency('retrieval')

    def release_models(self):
        interpreted = getattr(self.interpreter, 'model', None) is not None
        retrieved = any(getattr(self.models, kind, None) is not None for kind in ('embedder','reranker'))
        if not interpreted and not retrieved:return
        if interpreted:self.interpreter.release()
        if self.models is not None:
            self.models.embedder = self.models.reranker = None
        trim_allocator()

    @contextmanager
    def inference_resources(self, external=False, timeout=None):
        if not self.resources.root:
            yield
            return
        if external:
            if os.environ.get('VISN_MANAGED_LMSTUDIO','').lower() not in ('1','true'):
                raise ResourceBusy('Coordinated external interpretation requires a dedicated managed LM Studio server')
            endpoint=self.args.interpretation_api_url.rstrip('/')
            if urlparse(endpoint).path != '/v1':raise ValueError('Managed interpretation requires a /v1 API')
            self.resources.yield_if_contended(self.release_models)
            self.release_models()
            managed=ManagedLMStudio(self.resources,endpoint[:-3]+'/api/v1',os.environ.get('VISN_GEMMA_API_KEY','lm-studio'))
            entered=False
            try:
                managed.enter();entered=True
                managed.call('/models/load',{'model':self.args.interpretation_model})
                yield
                managed.completed()
            finally:
                if entered:managed.cleanup()
                self.resources.release(self.release_models)
        else:
            with stage('resource_admission'):
                self.resources.yield_if_contended(self.release_models)
                self.resources.acquire(timeout)
            try:yield
            except BaseException:raise
            else:self.resources.yield_if_contended(self.release_models)

    def maintain_resources(self):
        if self.resources.contended() and self.lock.acquire(blocking=False):
            try:self.resources.yield_if_contended(self.release_models)
            finally:self.lock.release()

    def ensure_models(self):
        if self.models is None:
            self.models = QwenModels(self.args)
        if hasattr(self.models, 'verify_profile'): self.models.verify_profile()

    def mps_ready(self):
        models = self.models
        owned = [getattr(models, kind, None) for kind in ('embedder', 'reranker')]
        owned.append(getattr(self.interpreter, 'model', None))
        return any(getattr(getattr(getattr(model, 'model', model), 'device', None), 'type', None) == 'mps' for model in owned if model is not None and model is not True)

    @measured('source_validation')
    def source(self, event):
        path = Path(event["source_path"]).resolve(strict=True)
        if self.source_revoked(path):
            raise ValueError('Evidence source is revoked')
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("Evidence must be a file inside the configured data directory")
        start, end = event["start_ms"], event["end_ms"]
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
            raise ValueError("Invalid event interval")
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        key = str(path)
        cached = self.hashes.get(key)
        if cached is None or cached[0] != identity:
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
                after = os.fstat(handle.fileno())
            if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Evidence changed while fingerprinting")
            self.hashes[key] = (identity, digest)
        self.hashes.move_to_end(key)
        expected = event.get('expected_source_sha256')
        if expected is not None and expected != self.hashes[key][1]:
            raise ValueError('Sanitized source hash differs from committed evidence')
        while len(self.hashes) > SOURCE_CACHE_MAX_ENTRIES:
            self.hashes.popitem(last=False)
        return path, self.hashes[key][1]

    def source_revoked(self, path):
        path=Path(path).resolve()
        if self.revocations.execute('SELECT 1 FROM revoked_sources WHERE path=? LIMIT 1',(str(path),)).fetchone():return True
        # Canonical Visn media keeps its recording namespace when restored under
        # another root. Do not let a new absolute path bypass the scope tombstone.
        if path.is_relative_to(self.root):
            for parent in path.parents:
                if parent.parent.name=='memory' and parent.is_relative_to(self.root):
                    try:scope=str(uuid.UUID(parent.name))
                    except ValueError:continue
                    if self.revocations.execute('SELECT 1 FROM revocations WHERE scope=?',(scope,)).fetchone():return True
        return False

    @measured('frame_sampling')
    def frames(self, event, count=None, size=None, with_timestamps=False, sample_timestamps=None, no_upscale=False):
        import imageio_ffmpeg
        from PIL import Image
        path, source_sha = self.source(event)
        source_identity = self.hashes[str(path)][0]
        count = count or self.args.frames
        size = size or self.args.frame_size
        frames = []
        decoded_times = []
        scale = f"scale='min(iw,{size})':'min(ih,{size})':force_original_aspect_ratio=decrease" if no_upscale else f"scale={size}:{size}:force_original_aspect_ratio=decrease"
        targets = sample_timestamps if sample_timestamps is not None else [event["start_ms"] + (i+.5)/count*(event["end_ms"]-event["start_ms"]) for i in range(count)]
        seeks = [max(event['start_ms'], target-.5) if sample_timestamps is not None else target for target in targets]
        enabled = not getattr(self.args,'disable_work_caches',False)
        def cache_key(timestamp):
            return (source_sha,event['start_ms'],event['end_ms'],f"{timestamp/1000:.6f}",size,no_upscale)
        def unchanged():
            if self.source(event) != (path, source_sha) or self.hashes[str(path)][0] != source_identity:
                raise ValueError('Evidence changed during frame sampling')
        prepared = {}
        if self.decode_policy == 'grouped-select-v1':
            missing = sorted({f'{t/1000:.6f}' for t in seeks if not enabled or cache_key(t) not in self.decode_cache}, key=float)
            if missing and eligible([float(t)*1000 for t in missing],event['end_ms'],size):
                self.work_stats['ffmpeg_calls'] += 1
                self.work_stats['grouped_calls'] += 1
                try:
                    with stage('ffmpeg_grouped_process'):
                        prepared = decode_group(imageio_ffmpeg.get_ffmpeg_exe(),path,missing,event['end_ms'],scale)
                except (ValueError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as error:
                    self.work_stats['grouped_fallbacks'] += 1
                    LOG.warning('Grouped decode retained independent seeks: %s',error)
                unchanged()
        @measured('decode_seek')
        def decode(timestamp):
            key = cache_key(timestamp)
            if enabled and key in self.decode_cache:
                self.work_stats['decode_hits'] += 1
                self.decode_cache.move_to_end(key)
                return self.decode_cache[key]
            if key[3] in prepared:
                value = prepared[key[3]]
            else:
                value = independent(timestamp)
            unchanged()
            if value[0] and (value[1] is None or not event['start_ms'] <= value[1] < event['end_ms']):
                raise ValueError('Decoded image has no in-window PTS')
            if enabled and len(value[0]) <= DECODE_CACHE_MAX_BYTES:
                self.decode_cache[key] = value
                self.decode_cache_bytes += len(value[0])
                while self.decode_cache_bytes > DECODE_CACHE_MAX_BYTES or len(self.decode_cache) > DECODE_CACHE_MAX_ENTRIES:
                    _, evicted = self.decode_cache.popitem(last=False)
                    self.decode_cache_bytes -= len(evicted[0])
                    self.work_stats['decode_evictions'] += 1
            return value
        def independent(timestamp):
            self.work_stats['ffmpeg_calls'] += 1
            # Keep every decoded image inside [start,end). An empty late seek
            # means sparse PTS, not necessarily missing evidence for the interval.
            with stage('ffmpeg_process'):
                result = subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "info",
                "-copyts", "-start_at_zero", "-ss", f"{timestamp/1000:.6f}",
                "-t", f"{(event['end_ms']-timestamp)/1000:.6f}", "-i", str(path),
                "-frames:v", "1", "-vf",
                f"select='gte(t,{timestamp/1000:.6f})*lt(t,{event['end_ms']/1000:.6f})',{scale},showinfo",
                "-vsync", "0",
                "-threads", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"
            ], capture_output=True, timeout=30, check=True)
            matches = re.findall(rb'pts_time:([0-9.eE+-]+)', result.stderr)
            actual = round(float(matches[0])*1000) if matches else None
            if result.stdout and (actual is None or not event['start_ms'] <= actual < event['end_ms']):
                raise ValueError('Decoded image has no in-window PTS')
            return result.stdout, actual
        for timestamp in seeks:
            raw, actual = decode(timestamp)
            if not raw:
                if frames:
                    frames.append(frames[-1].copy())
                    decoded_times.append(decoded_times[-1])
                    continue
                raw, actual = decode(event['start_ms'])
            if not raw:
                raise ValueError("No decodable image inside the requested event interval")
            with Image.open(io.BytesIO(raw)) as image:
                frames.append(image.convert("RGB"))
                decoded_times.append(actual)
        unchanged()
        return (frames, decoded_times) if with_timestamps else frames

    @measured('clip_embedding')
    def embedding(self, event, expected_digest=None):
        self.ensure_models()
        path, digest = self.source(event)
        if expected_digest is not None and digest != expected_digest:
            raise ValueError('Source changed since index submission')
        version = self.models.version
        key = self.embedding_key(event, digest)
        self.record_artifact_source('vector', key, path)
        with stage('vector_cache_read'):
            row = self.db.execute("SELECT vector FROM vectors WHERE key=?", (key,)).fetchone()
        if row:
            try:
                vector = json.loads(row[0])
                return (unit_vector(vector) if self.vector_policy == 'canonical-unit-v2' else normalize(vector)), True
            except (ValueError, TypeError):
                self.db.execute("DELETE FROM vectors WHERE key=?", (key,))
        vector = self.models.embed({"video": self.frames(event), "instruction": INSTRUCTION})
        if self.vector_policy == 'canonical-unit-v2':
            unit_vector(vector)
        self.ensure_models()
        if self.source(event)[1] != digest or self.models.version != version:
            raise ValueError('Source or embedding profile changed during inference; artifact was not committed')
        with stage('vector_cache_write'):
            self.db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)", (key, version, json.dumps(vector)))
            self.db.commit()
        return vector, False

    def embedding_key(self, event, digest):
        raw = json.dumps([digest, event["start_ms"], event["end_ms"], self.models.version])
        return hashlib.sha256(raw.encode()).hexdigest()

    def record_artifact_source(self, kind, key, path):
        cursor = self.db.execute('INSERT OR IGNORE INTO artifact_sources VALUES (?,?,?)',(kind,key,str(Path(path).resolve())))
        if cursor.rowcount:
            self.db.commit()

    def artifact_valid(self, key):
        row = self.db.execute('SELECT vector FROM vectors WHERE key=?',(key,)).fetchone()
        if not row: return False
        try:
            value = json.loads(row[0])
            unit_vector(value) if self.vector_policy == 'canonical-unit-v2' else normalize(value)
            return True
        except (ValueError, TypeError, OverflowError):
            return False

    @measured('query_embedding')
    def query_embedding(self, query):
        self.ensure_models()
        version = self.models.version
        key = (self.models.version, query)
        enabled = not getattr(self.args,'disable_work_caches',False)
        if enabled and key in self.query_cache:
            self.query_cache.move_to_end(key)
            self.work_stats['query_hits'] += 1
            return self.query_cache[key]
        self.work_stats['query_calls'] += 1
        vector = tuple(self.models.embed({"text":query,"instruction":INSTRUCTION}))
        if self.vector_policy == 'canonical-unit-v2':
            unit_vector(vector)
        if not vector or any(type(v) not in (float,int) or not math.isfinite(v) for v in vector):
            raise ValueError('Invalid query embedding')
        self.ensure_models()
        if version != self.models.version: raise ProfileConflict('Query embedding profile changed during inference')
        if enabled:
            self.query_cache[key] = vector
            while len(self.query_cache)>QUERY_CACHE_MAX_ENTRIES:self.query_cache.popitem(last=False)
        return vector

    @measured('clip_reranking')
    def rerank_event(self, query, event):
        self.ensure_models()
        version = self.models.version
        # Validate scope/source even on hits. Revision is resolved once per model session.
        _, sha = self.source(event)
        snapshot = getattr(self.models,'reranker_snapshot',None)
        key = (self.models.version,json.dumps(getattr(self.models,'reranker_identity',None),sort_keys=True),snapshot,query,sha,event['start_ms'],event['end_ms'])
        enabled = not getattr(self.args,'disable_work_caches',False)
        if enabled and snapshot and key in self.rerank_cache:
            self.rerank_cache.move_to_end(key)
            self.work_stats['rerank_hits'] += 1
            return self.rerank_cache[key]
        self.work_stats['rerank_calls'] += 1
        score = self.models.rerank(query,self.frames(event))
        self.ensure_models()
        if version != self.models.version: raise ProfileConflict('Reranker profile changed during inference')
        if not math.isfinite(score) or not 0<=score<=1:raise ValueError('Invalid rerank score')
        snapshot = getattr(self.models,'reranker_snapshot',None)
        if enabled and snapshot:
            key = (self.models.version,json.dumps(getattr(self.models,'reranker_identity',None),sort_keys=True),snapshot,query,sha,event['start_ms'],event['end_ms'])
            self.rerank_cache[key] = score
            while len(self.rerank_cache)>RERANK_CACHE_MAX_ENTRIES:self.rerank_cache.popitem(last=False)
        return score

    @measured('retrieval')
    def search(self, body):
        if self.interpreter:
            self.interpreter.release()
        started = time.monotonic()
        initial_stats = dict(self.work_stats)
        if not isinstance(body, dict) or not isinstance(body.get("query"), str):
            raise ValueError("Expected an object with a string query")
        query = body["query"].strip()
        events = body.get("events", [])
        limit = body.get("limit", 10)
        if not query or len(query) > 1000 or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("Invalid query or result limit")
        if not isinstance(events, list) or len(events) > 10000:
            raise ValueError("Invalid candidate set (maximum 10000 events)")
        if any(not isinstance(e, dict) or not isinstance(e.get("event_id"), str) for e in events):
            raise ValueError("Each event needs a string event_id")
        if any(isinstance(e.get('source_path'),str) and self.source_revoked(e['source_path']) for e in events):
            raise ValueError('Evidence source is revoked')
        by_id = {e["event_id"]: e for e in events}
        if len(by_id) != len(events):
            raise ValueError("Duplicate event IDs")
        lexical = body.get("lexical_ids", [])
        if not isinstance(lexical, list) or any(not isinstance(event_id, str) or event_id not in by_id for event_id in lexical):
            raise ValueError("Lexical result outside candidate set")
        if not events:
            return {"items": [], "mode": "hybrid_rrf", "notes": []}
        self.ensure_models()
        q = self.query_embedding(query)
        semantic, failures, cached = [], [], 0
        for index, event in enumerate(events):
            try:
                vector, hit = self.embedding(event)
                if len(vector) != len(q):
                    raise ValueError("Embedding dimensions do not match")
                semantic.append((event["event_id"], sum(a*b for a, b in zip(q, vector))))
                cached += int(hit)
            except Exception as error:
                LOG.warning("Embedding failed for %s: %s", event["event_id"], error)
                failures.append(event["event_id"])
            LOG.info("Indexed %d/%d candidates (%d cached)", index+1, len(events), cached)
        if not semantic:
            raise RuntimeError("No visual embeddings were available; use lexical fallback")
        semantic.sort(key=lambda row: (-row[1], row[0]))
        shortlist = fuse(lexical[:50], [row[0] for row in semantic[:50]], max(20, limit))
        notes = [f"Visual coverage {len(semantic)}/{len(events)} events; {cached} cached embeddings. Scores are relevance rankings, not confidence."]
        mode = "hybrid_rrf"
        if failures:
            notes.append(f"{len(failures)} events could not be embedded; retrieval coverage is incomplete.")
        if body.get("rerank", True):
            try:
                reranked = []
                for event_id, score in shortlist:
                    reranked.append((event_id, self.rerank_event(query, by_id[event_id]), score))
                reranked.sort(key=lambda row: (-row[1], -row[2], row[0]))
                shortlist = [(event_id, score) for event_id, score, _ in reranked]
                mode = "hybrid_qwen_reranker"
            except Exception as error:
                LOG.warning("Reranker unavailable: %s", error)
                notes.append("Reranking failed; retained hybrid reciprocal-rank ordering.")
        LOG.info("Search finished in %.2fs: %s, %d candidates, %d cached", time.monotonic()-started, mode, len(events), cached)
        return {"items": [{"event_id": event_id, "score": score} for event_id, score in shortlist[:limit]],
                "mode": mode, "notes": notes, "provenance": {
                    "embedding": json.loads(self.models.version),
                    "reranker_snapshot": getattr(self.models, "reranker_snapshot", None) if mode == "hybrid_qwen_reranker" else None,
                    "fusion": "reciprocal-rank-k60", "shortlist_limit": max(20, limit),
                    "elapsed_secs": round(time.monotonic()-started, 3),
                    "vector_policy": self.vector_policy,
                    "work_cache":dict(enabled=not getattr(self.args,'disable_work_caches',False),
                        decode_policy=self.decode_policy,
                        counters={k:self.work_stats[k]-initial_stats[k] for k in self.work_stats},decode_bytes=self.decode_cache_bytes,
                        decode_entries=len(self.decode_cache),query_entries=len(self.query_cache),rerank_entries=len(self.rerank_cache))}}


def handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        def inspect_profile(self):
            if not service.lock.acquire(timeout=0): raise ResourceBusy('Profile inspection is busy; retry')
            try:
                profile=worker_profile(service)
                expected=self.headers.get('X-Visn-Expected-Profile')
                if expected is not None and expected != profile['fingerprint']:
                    raise ProfileConflict('retrieval_profile_changed: worker no longer matches the selected profile')
                return profile
            finally:service.lock.release()

        def reply(self, status, body):
            if status >= 400 and CURRENT.get() and CURRENT.get().status == 'ok':
                CURRENT.get().status = 'http_error'
            with stage('response_serialization'):
                encoded = json.dumps(body, allow_nan=False).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json")
            if status in (429, 503): self.send_header('Retry-After', '2')
            self.send_header("Content-Length", str(len(encoded))); self.end_headers()
            try:
                with stage('response_write'):
                    self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                LOG.warning("Client disconnected; completed cache writes remain reusable")
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == '/profile':
                try: return self.reply(200, self.inspect_profile())
                except Exception as error: return self.reply(409 if isinstance(error,ProfileConflict) else 503, {'error':str(error)})
            profile=None
            if parsed.path.startswith('/index-jobs') and self.headers.get('X-Visn-Expected-Profile') is not None:
                try:profile=self.inspect_profile()
                except Exception as error:return self.reply(409 if isinstance(error,ProfileConflict) else 503, {'error':str(error)})
            if parsed.path == '/health':
                return self.reply(200, {"status": "ready", "models_loaded": service.models is not None,
                        "telemetry_enabled": not getattr(service.args,'disable_telemetry',True),
                        'scheduler': service.lock.snapshot(), 'background_indexing': service.indexer is not None, 'resources':service.resources.snapshot(), 'decode_policy':service.decode_policy})
            if service.indexer and parsed.path == '/index-jobs':
                scope = parse_qs(parsed.query).get('scope_id', [''])[0]
                return self.reply(200, {'jobs': service.indexer.list(scope), 'scheduler':service.lock.snapshot(), 'resources':service.resources.snapshot(),'worker_profile':profile})
            if service.indexer and parsed.path.startswith('/index-jobs/'):
                result = service.indexer.get(parsed.path.split('/')[-1])
                if result is not None and profile is not None:result['worker_profile']=profile
                return self.reply(200 if result else 404, result or {'error':'Index job not found'})
            self.reply(404, {'error':'Unknown endpoint'})
        def do_POST(self):
            with WorkflowTrace(self.headers.get('X-Visn-Query-ID'), self.path,
                               self.headers.get('X-Visn-Run-ID'),
                               enabled=not getattr(service.args,'disable_telemetry',True),
                               mps_ready=service.mps_ready):
                self.process_post()

        def process_post(self):
            index_action = re.fullmatch(r'/index-jobs/([^/]+)/(cancel|retry)', self.path)
            if index_action and service.indexer:
                try:
                    profile=self.inspect_profile() if self.headers.get('X-Visn-Expected-Profile') is not None else None
                    result = service.indexer.action(*index_action.groups())
                    if result is not None and profile is not None:result['worker_profile']=profile
                    return self.reply(200 if result else 404, result or {'error':'Index job not found'})
                except ProfileConflict as error:
                    return self.reply(409, {'error':str(error)})
                except ResourceBusy as error:
                    return self.reply(503, {'error':str(error)})
                except ValueError as error:
                    return self.reply(410, {'error':str(error)})
                except QueueFull as error:
                    return self.reply(429, {'error':str(error)})
            if self.path not in ("/search", "/interpret", '/index-jobs', '/revocations') or (self.path in ('/index-jobs','/revocations') and not service.indexer):
                return self.reply(404, {"error": "Unknown endpoint"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply(400, {"error": "Invalid Content-Length"})
            if not 0 < length <= 8*1024*1024:
                return self.reply(413, {"error": "Invalid request size"})
            with stage('worker_admission'):
                admitted = service.lock.acquire(timeout=getattr(service.args,'interactive_wait_secs',0))
            if not admitted:
                if CURRENT.get(): CURRENT.get().status = 'busy'
                return self.reply(503, {"error": "Inference queue is full or admission timed out; retry without reducing evidence coverage"})
            try:
                with stage('request_deserialization'):
                    body = json.loads(self.rfile.read(length))
                if self.path == '/revocations':
                    receipt = service.indexer.revoke(body)
                    receipt['index_storage']=storage_identity(service)
                    return self.reply(200 if receipt['cleanup_complete'] else 503, receipt)
                profile = worker_profile(service)
                expected = self.headers.get('X-Visn-Expected-Profile')
                if expected is not None and expected != profile['fingerprint']:
                    raise ProfileConflict('retrieval_profile_changed: request requires a different worker profile')
                if self.path == '/index-jobs':
                    if not isinstance(body, dict): raise ValueError('Expected an index request object')
                    if 'resolve_only' in body and not isinstance(body['resolve_only'],bool):
                        raise ValueError('resolve_only must be a boolean')
                    lookup=body.get('resolve_only',False)
                    receipt = service.indexer.resolve(body) if lookup else service.indexer.submit(body)
                    if worker_profile(service) != profile: raise ProfileConflict('retrieval_profile_changed_during_admission')
                    if receipt is None:return self.reply(404, {'error':'No exact preparation task exists','worker_profile':profile})
                    receipt['worker_profile'] = profile
                    return self.reply(200 if lookup else 202, receipt)
                with service.inference_resources(external=self.path == '/interpret' and bool(getattr(service.args,'interpretation_api_url',None))):
                    if self.path == '/interpret':
                        from temporal_interpretation import SequenceInterpreter
                        if service.interpreter is None:
                            service.interpreter = SequenceInterpreter(service)
                        result = service.interpreter.interpret(body)
                    else:
                        result = service.search(body)
                if worker_profile(service) != profile:
                    raise ProfileConflict('retrieval_profile_changed_during_inference')
                if self.path == '/search': result.setdefault('provenance', {})['worker_profile'] = profile
                else:
                    for item in result['interpretations']: item.setdefault('provenance', {})['worker_profile'] = profile
                if CURRENT.get():
                    snapshot = CURRENT.get().report()
                    if self.path == '/search':
                        result.setdefault('provenance', {})['telemetry'] = snapshot
                    else:
                        for item in result['interpretations']:
                            item['provenance']['telemetry'] = snapshot
                self.reply(200, result)
            except ProfileConflict as error:
                self.reply(409, {'error':str(error)})
            except QueueFull as error:
                self.reply(429, {'error':str(error)})
            except ResourceBusy as error:
                if CURRENT.get():CURRENT.get().status='busy'
                self.reply(503, {'error':str(error)})
            except Exception as error:
                if CURRENT.get(): CURRENT.get().status = 'error'
                LOG.exception("Retrieval request failed")
                self.reply(410 if 'revoked' in str(error).lower() else 400 if self.path in ('/index-jobs','/revocations') and isinstance(error,(ValueError,KeyError)) else 500, {"error": str(error)})
            finally:
                service.lock.release()
                with stage('request_cleanup'):
                    gc.collect()
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interactive-queue-limit', type=int, default=8)
    parser.add_argument('--interactive-wait-secs', type=float, default=120)
    parser.add_argument('--index-queue-limit', type=int, default=8)
    parser.add_argument('--index-max-attempts', type=int, default=2)
    telemetry = parser.add_mutually_exclusive_group()
    telemetry.add_argument('--telemetry', dest='disable_telemetry', action='store_false', help='Opt into additive stage/resource diagnostics')
    telemetry.add_argument('--disable-telemetry', dest='disable_telemetry', action='store_true', help='Explicitly disable diagnostics for controlled comparison (default)')
    parser.set_defaults(disable_telemetry=os.environ.get('VISN_WORKFLOW_TELEMETRY','').lower() not in ('1','true'))
    parser.add_argument('--vector-policy', choices=VECTOR_POLICIES, default=VECTOR_POLICIES[0], help='Versioned embedding numerical contract; canonical uses an isolated database')
    parser.add_argument('--decode-policy', choices=('single-seek-v1','grouped-select-v1'), default='single-seek-v1', help='Independent seeks (default) or bounded experimental grouped FFmpeg selection')
    parser.add_argument("--disable-work-caches", action="store_true", help="Disable bounded lossless decode/query/rerank work caches for controlled validation")
    parser.add_argument("--keep-models-loaded", action="store_true", help="Keep both models in memory instead of switching between them")
    parser.add_argument("--interpretation-model", default="Qwen/Qwen3-VL-2B-Instruct")
    parser.add_argument("--interpretation-revision", default="main")
    parser.add_argument("--interpretation-api-url", help="Optional local OpenAI-compatible /v1 API; requires a pinned interpretation revision")
    parser.add_argument("--interpretation-frames", type=int, default=8, choices=range(2,17))
    parser.add_argument("--interpretation-crop-mode", choices=("source", "preview"), default="source", help="Source-detail crops without upscaling (default), or preview crops for controlled comparisons")
    parser.add_argument("--interpretation-frame-size", type=int, default=384, choices=range(128,513))
    parser.add_argument("--port", type=int, default=8186)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--qwen-source", default=str(ROOT / ".retrieval/qwen"))
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-VL-Embedding-2B")
    parser.add_argument("--embedding-revision", default="main")
    parser.add_argument("--reranker-model", default="Qwen/Qwen3-VL-Reranker-2B")
    parser.add_argument("--reranker-revision", default="main")
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--frame-size", type=int, default=256)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    args = parser.parse_args()
    if not 1 <= args.interactive_queue_limit <= 64 or not 0 <= args.interactive_wait_secs <= 300 or not 1 <= args.index_queue_limit <= 64 or not 1 <= args.index_max_attempts <= 5:
        parser.error('queue limits must be 1..64, interactive wait 0..300 seconds, and attempts 1..5')
    if not 2 <= args.frames <= 32 or not 64 <= args.frame_size <= 512:
        parser.error("frames must be 2..32 and frame-size 64..512")
    if args.interpretation_api_url:
        endpoint = urlparse(args.interpretation_api_url)
        if endpoint.scheme != "http" or endpoint.hostname not in ("127.0.0.1", "localhost", "::1") or endpoint.username or endpoint.password:
            parser.error("Interpretation API must be a local HTTP endpoint without URL credentials")
    if args.interpretation_api_url and args.interpretation_revision == "main":
        parser.error("API interpretation requires --interpretation-revision identifying the evaluated model files")
    os.environ.setdefault("HF_HOME", str(ROOT / ".retrieval/huggingface"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = Retrieval(args)
    service.indexer = IndexJobs(service)
    Handler = handler_for(service)
    LOG.info("Qwen retrieval listening on 127.0.0.1:%s; models load on first search", args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    service.indexer.start()
    try:
        server.serve_forever()
    finally:
        service.indexer.close()
        service.resources.release(service.release_models)
        server.server_close()
        service.db.close()
        service.revocations.close()


if __name__ == "__main__":
    main()
