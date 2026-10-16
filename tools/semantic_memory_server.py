#!/usr/bin/env python3
"""Local Qwen video retrieval sidecar. Run with the isolated retrieval environment."""
import argparse
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
import threading
import time
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("visn.retrieval")
ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REVISION = "393e2978d27852b0d0230d6994f37f9c15bed73c"
DECODE_CACHE_MAX_BYTES = 32 * 1024 * 1024
DECODE_CACHE_MAX_ENTRIES = 128
QUERY_CACHE_MAX_ENTRIES = 32
RERANK_CACHE_MAX_ENTRIES = 256
SOURCE_CACHE_MAX_ENTRIES = 256

INSTRUCTION = "Retrieve camera video segments that visually match the search query."


def normalize(vector):
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v*v for v in values))
    if not values or not math.isfinite(norm) or norm < 1e-8:
        raise ValueError("Invalid embedding returned by model/cache")
    return [v/norm for v in values]


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
        self.version = json.dumps({"model": args.embedding_model, "snapshot": self.embedding_path,
                                   "upstream": UPSTREAM_REVISION, "frames": args.frames,
                                   "pixels": args.frame_size, "instruction": INSTRUCTION,
                                   "sampler": "ffmpeg-pts-bounded-midpoints-v4-hold-last"}, sort_keys=True)

    def load(self, kind):
        import torch
        from src.models.qwen3_vl_embedding import Qwen3VLEmbedder
        from src.models.qwen3_vl_reranker import Qwen3VLReranker
        if getattr(self, kind) is not None:
            return getattr(self, kind)
        device = self.args.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        if not self.args.keep_models_loaded:
            other = "reranker" if kind == "embedder" else "embedder"
            setattr(self, other, None)
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        model_path = self.embedding_path if kind == "embedder" else (self.reranker_snapshot or self.snapshot_download(
            self.args.reranker_model, revision=self.args.reranker_revision))
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

    def embed(self, value):
        import torch
        with torch.inference_mode():
            return normalize(self.load("embedder").process([value])[0].float().cpu().tolist())

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
        self.hashes = OrderedDict()
        self.interpreter = None
        self.decode_cache = OrderedDict()
        self.decode_cache_bytes = 0
        self.query_cache = OrderedDict()
        self.rerank_cache = OrderedDict()
        self.work_stats = dict(decode_hits=0, ffmpeg_calls=0, decode_evictions=0, query_hits=0, query_calls=0, rerank_hits=0, rerank_calls=0)
        self.db = sqlite3.connect(self.root / "semantic-v1.sqlite3", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, version TEXT NOT NULL, vector TEXT NOT NULL)")
        self.lock = threading.Lock()

    def source(self, event):
        path = Path(event["source_path"]).resolve(strict=True)
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
        while len(self.hashes) > SOURCE_CACHE_MAX_ENTRIES:
            self.hashes.popitem(last=False)
        return path, self.hashes[key][1]

    def frames(self, event, count=None, size=None, with_timestamps=False, sample_timestamps=None, no_upscale=False):
        import imageio_ffmpeg
        from PIL import Image
        path, source_sha = self.source(event)
        count = count or self.args.frames
        size = size or self.args.frame_size
        frames = []
        decoded_times = []
        scale = f"scale='min(iw,{size})':'min(ih,{size})':force_original_aspect_ratio=decrease" if no_upscale else f"scale={size}:{size}:force_original_aspect_ratio=decrease"
        def decode(timestamp):
            cache_key = (source_sha,event['start_ms'],event['end_ms'],f"{timestamp/1000:.6f}",size,no_upscale)
            enabled = not getattr(self.args,'disable_work_caches',False)
            if enabled and cache_key in self.decode_cache:
                self.work_stats['decode_hits'] += 1
                self.decode_cache.move_to_end(cache_key)
                return self.decode_cache[cache_key]
            self.work_stats['ffmpeg_calls'] += 1
            # Keep every decoded image inside [start,end). An empty late seek
            # means sparse PTS, not necessarily missing evidence for the interval.
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
            value = (result.stdout, actual)
            if enabled and len(result.stdout) <= DECODE_CACHE_MAX_BYTES:
                self.decode_cache[cache_key] = value
                self.decode_cache_bytes += len(result.stdout)
                while self.decode_cache_bytes > DECODE_CACHE_MAX_BYTES or len(self.decode_cache) > DECODE_CACHE_MAX_ENTRIES:
                    _, evicted = self.decode_cache.popitem(last=False)
                    self.decode_cache_bytes -= len(evicted[0])
                    self.work_stats['decode_evictions'] += 1
            return value
        targets = sample_timestamps if sample_timestamps is not None else [event["start_ms"] + (i+.5)/count*(event["end_ms"]-event["start_ms"]) for i in range(count)]
        for target in targets:
            timestamp = max(event['start_ms'],target-.5) if sample_timestamps is not None else target
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
        return (frames, decoded_times) if with_timestamps else frames

    def embedding(self, event):
        _, digest = self.source(event)
        raw = json.dumps([digest, event["start_ms"], event["end_ms"], self.models.version])
        key = hashlib.sha256(raw.encode()).hexdigest()
        row = self.db.execute("SELECT vector FROM vectors WHERE key=?", (key,)).fetchone()
        if row:
            try:
                return normalize(json.loads(row[0])), True
            except (ValueError, TypeError):
                self.db.execute("DELETE FROM vectors WHERE key=?", (key,))
        vector = self.models.embed({"video": self.frames(event), "instruction": INSTRUCTION})
        self.db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)", (key, self.models.version, json.dumps(vector)))
        self.db.commit()
        return vector, False

    def query_embedding(self, query):
        key = (self.models.version, query)
        enabled = not getattr(self.args,'disable_work_caches',False)
        if enabled and key in self.query_cache:
            self.query_cache.move_to_end(key)
            self.work_stats['query_hits'] += 1
            return self.query_cache[key]
        self.work_stats['query_calls'] += 1
        vector = tuple(self.models.embed({"text":query,"instruction":INSTRUCTION}))
        if not vector or any(type(v) not in (float,int) or not math.isfinite(v) for v in vector):
            raise ValueError('Invalid query embedding')
        if enabled:
            self.query_cache[key] = vector
            while len(self.query_cache)>QUERY_CACHE_MAX_ENTRIES:self.query_cache.popitem(last=False)
        return vector

    def rerank_event(self, query, event):
        # Validate scope/source even on hits. Revision is resolved once per model session.
        _, sha = self.source(event)
        snapshot = getattr(self.models,'reranker_snapshot',None)
        key = (self.models.version,snapshot,query,sha,event['start_ms'],event['end_ms'])
        enabled = not getattr(self.args,'disable_work_caches',False)
        if enabled and snapshot and key in self.rerank_cache:
            self.rerank_cache.move_to_end(key)
            self.work_stats['rerank_hits'] += 1
            return self.rerank_cache[key]
        self.work_stats['rerank_calls'] += 1
        score = self.models.rerank(query,self.frames(event))
        if not math.isfinite(score) or not 0<=score<=1:raise ValueError('Invalid rerank score')
        snapshot = getattr(self.models,'reranker_snapshot',None)
        if enabled and snapshot:
            key = (self.models.version,snapshot,query,sha,event['start_ms'],event['end_ms'])
            self.rerank_cache[key] = score
            while len(self.rerank_cache)>RERANK_CACHE_MAX_ENTRIES:self.rerank_cache.popitem(last=False)
        return score

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
        by_id = {e["event_id"]: e for e in events}
        if len(by_id) != len(events):
            raise ValueError("Duplicate event IDs")
        lexical = body.get("lexical_ids", [])
        if not isinstance(lexical, list) or any(not isinstance(event_id, str) or event_id not in by_id for event_id in lexical):
            raise ValueError("Lexical result outside candidate set")
        if not events:
            return {"items": [], "mode": "hybrid_rrf", "notes": []}
        if self.models is None:
            self.models = QwenModels(self.args)
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
                    "work_cache":dict(enabled=not getattr(self.args,'disable_work_caches',False),
                        counters={k:self.work_stats[k]-initial_stats[k] for k in self.work_stats},decode_bytes=self.decode_cache_bytes,
                        decode_entries=len(self.decode_cache),query_entries=len(self.query_cache),rerank_entries=len(self.rerank_cache))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body):
            encoded = json.dumps(body, allow_nan=False).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded))); self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                LOG.warning("Client disconnected; completed cache writes remain reusable")
        def do_GET(self):
            self.reply(200 if self.path == "/health" else 404,
                       {"status": "ready", "models_loaded": service.models is not None})
        def do_POST(self):
            if self.path not in ("/search", "/interpret") :
                return self.reply(404, {"error": "Unknown endpoint"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply(400, {"error": "Invalid Content-Length"})
            if not 0 < length <= 8*1024*1024:
                return self.reply(413, {"error": "Invalid request size"})
            if not service.lock.acquire(blocking=False):
                return self.reply(503, {"error": "Retrieval worker is busy; retry after indexing completes"})
            try:
                body = json.loads(self.rfile.read(length))
                if self.path == '/interpret':
                    from temporal_interpretation import SequenceInterpreter
                    if service.interpreter is None:
                        service.interpreter = SequenceInterpreter(service)
                    result = service.interpreter.interpret(body)
                else:
                    result = service.search(body)
                self.reply(200, result)
            except Exception as error:
                LOG.exception("Retrieval request failed")
                self.reply(500, {"error": str(error)})
            finally:
                service.lock.release()
                gc.collect()
    LOG.info("Qwen retrieval listening on 127.0.0.1:%s; models load on first search", args.port)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
