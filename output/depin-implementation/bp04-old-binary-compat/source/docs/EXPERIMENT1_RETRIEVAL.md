# Experiment 1: visual Memory retrieval

Memory search now combines keyword matches with Qwen3-VL video embeddings, then optionally reranks the shortlist with Qwen3-VL-Reranker. YOLO tracking, event segmentation, and caption generation remain independent.

## Run locally

From the repository root:

```bash
sh tools/setup_retrieval.sh
sh scripts/run_memory.sh
```

The launcher starts both processes with a shared data directory and connects Rust to the Qwen worker. Open `http://127.0.0.1:8080/#/workspace`; Video memory is the default workspace. For separate terminals, run `.venv-retrieval/bin/python tools/semantic_memory_server.py` and `VISN_RETRIEVAL_URL=http://127.0.0.1:8186 cargo run`.

Paste a URL into **Camera stream URL**, record a bounded interval, click **Search these indexed cameras**, and search with **Visual retrieval** and **Rerank** enabled. Disable the summary option to evaluate retrieval independently of LM Studio. The UI shows worker availability, the actual retrieval mode, coverage/fallback notes, and expandable raw retrieval JSON with model provenance. Optional LM Studio enrichment and synthesis default off. Detector analytics remains a separate, explicitly labeled workflow.

The worker binds to loopback and must see the same evidence files as Rust. For a custom `VISN_DATA_DIR`, pass the same absolute directory through the worker's `--data-dir`. This is a local experimental service, not a public inference endpoint.

The isolated environment downloads the two 2B checkpoints on first use. Provision space for both model snapshots and inference memory. By default only one model stays loaded at a time; `--keep-models-loaded` avoids model switching when enough memory is available. Device selection is automatic, or use `--device mps`, `cuda`, or `cpu`. CUDA has not been validated on this development machine.

## Retrieval behavior

1. Rust applies cluster, camera, and source-relative time filters before sending candidates to the worker.
2. The worker samples eight evenly spaced frames within each event's original source interval (256px bounding box by default). This avoids evidence-clip keyframe padding. Use `--frames` and `--frame-size` to adjust the cost/detail tradeoff.
3. Uncached intervals are embedded with `Qwen/Qwen3-VL-Embedding-2B`. Normalized vectors live in `semantic-v1.sqlite3` beneath the data directory. Cache identity includes source content, interval, resolved model snapshot, wrapper revision, instruction, and sampling settings.
4. Cosine-ranked visual candidates and keyword candidates are combined using reciprocal rank fusion (k=60; up to 50 from each list).
5. `Qwen/Qwen3-VL-Reranker-2B` scores up to `max(20, requested limit)` shortlisted clips. Results include exact model/sampler provenance in `retrieval_provenance`.

Indexing is lazy: the first query over new clips can be slow. Later queries reuse persisted clip embeddings, but still embed the query and rerank. The worker logs progress and accepts one search at a time. Busy, unavailable, or timed-out workers cause a visible keyword fallback; completed cache writes remain reusable. `VISN_RETRIEVAL_TIMEOUT_SECS` defaults to 600. Reranker failure preserves the fused ranking and reports a note. Per-clip failures report incomplete visual coverage.

The default wrapper revision is pinned in `tools/setup_retrieval.sh` and verified at runtime. Model revisions default to `main`; use `--embedding-revision` and `--reranker-revision` with snapshot commits for reproducible runs. Resolved weight snapshots are recorded with results.

## Verify

```bash
cargo test --offline
.venv-retrieval/bin/python tools/check_retrieval.py
```

The Python contract tests use fake model outputs but real video decoding and SQLite persistence. They cover interval isolation, cache reuse/invalidation, hybrid fusion, partial coverage, reranker fallback, and evidence-root restrictions. Rust tests cover filter boundaries, invalid worker results, and keyword fallback.

## Evaluation limits

### Local integration result (2026-10-13)

- Apple Silicon MPS, four 128px frames per clip, three synthetic colored-square videos: the real embedder and reranker ranked the red clip first for a red-square query. A direct worker query used no lexical candidates.
- Final FFmpeg decoder: 23.2 seconds for three uncached clips with weights already downloaded. Restarted-worker browser query: 14.75 seconds with all three clip embeddings cached. These tiny-sample timings are not throughput benchmarks.
- Playwright exercised the actual Rust API and models using isolated synthetic Memory manifests: correct first result, camera filter retained one event, no browser errors, no horizontal overflow at 390px. Screenshots and the API response are under `output/playwright/retrieval-*`.
- Build, 15 Rust tests, two Python contract tests, formatting, and diff checks passed. Strict Clippy remains blocked by six existing warnings in `cluster.rs`, `memory.rs`, and `pipeline.rs`.
- The integration fixture bypassed recording/segmentation; this check does not establish ingestion accuracy or CCTV retrieval quality.

### Remaining evaluation

Ranking scores are relevance signals, not probabilities that an event occurred. A nearest-neighbor search can return irrelevant clips. Sparse frames may miss brief actions, small objects, or ordering; this experiment does not implement temporal verification. Existing event segmentation can also omit relevant moments before retrieval begins.

Before replacing the baseline, label representative CCTV queries and compare lexical, embedding-only, fused, and reranked results using Recall@10, nDCG@10, latency, and missed-event review. Include night scenes, occlusion, brief actions, and negative queries. Synthetic checks establish integration, not CCTV accuracy. Retrieval may compete with detector/LM Studio processes for GPU memory; its worker is separate from the existing media inference gate.

Official model wrappers: [Qwen3-VL-Embedding](https://github.com/QwenLM/Qwen3-VL-Embedding).

## Highway-stream validation and workflow fixes (2026-10-14)

Tested the user-supplied `http://103.151.177.124:221/mjpg/video.mjpg` through the browser, Rust API, recording adapter, and real Qwen models. No synthetic manifests were used for this run.

| Check | Observed result |
| --- | --- |
| Bounded recording | 29,880ms from a requested 30 seconds; 119 source frames consumed, 26 frames analyzed by the sparse observer |
| Indexing | Four intervals, one successful camera, no camera failures on the first recording |
| Visual retrieval | `hybrid_qwen_reranker`, visual coverage 4/4; the vehicle query had no lexical matches |
| Model configuration | Qwen3-VL-Embedding-2B + Qwen3-VL-Reranker-2B, eight 256px frames per interval, MPS |
| First search | 57.05 seconds including model loading; no cached embeddings |
| Repeat search | 14.65 seconds; all four embeddings cached |
| Restart recovery | Persisted recording selectable and searchable; cached reranked searches took 13.61 and 11.06 seconds |
| Evidence playback | Real H.264 clips played and continued advancing across multiple UI polling cycles |
| Frontend checks | Correct indexing-model selector; cluster and camera selection carried into search; excluded-camera query returned no candidates; no browser errors or horizontal overflow at 320/768/1024/1440px |
| Failure recovery | A second connection to the camera timed out and produced an explicit failed job. The previous successful recording remained usable. Stopping Qwen produced an unavailable status and visibly labeled keyword fallback with four metadata matches. |

The frames show vehicles in marked highway/toll lanes. The query was "Vehicles and trucks on a road at an intersection"; all four results were visual candidates. This establishes the real workflow, not precision/recall, signal-state detection, or a calibrated relevance threshold.

### Fixes made

- Video memory is the default workspace; detector analytics has a clearly labeled link that transfers its stream URL into Memory.
- A single stream can be pasted directly; camera JSON remains available for multiple cameras.
- Memory submits its own selected enrichment model. Optional caption generation and synthesis start disabled, and their status is separate from visual retrieval.
- Completed evidence cards are not replaced on every poll, preserving playback. Recorded sessions can be selected after restart or a failed new recording.
- Query cluster/camera filters follow the indexed cameras. Raw query output and model provenance are visible in the UI.
- MJPEG URLs use arrival timestamps; observation sampling follows presentation timestamps rather than nominal frame-rate strides. Non-H.264 source clips are transcoded with browser-compatible pixel format rather than assuming an MP4 container is sufficient.
- The backend logs recording start, indexing completion, and retrieval start/completion with query ID, actual mode, filtered candidate count, coverage/cache notes, model provenance, and elapsed time. Keyword fallback is distinguishable from successful Qwen inference.
- Deterministic report confidence is `null`, not an invented 100%. Track classes use confidence-weighted consensus, with changes disclosed; published track/event labels agree.

### Reproduce

Start the integrated app with `sh scripts/run_memory.sh`. With Playwright available to Node:

```bash
VISN_TEST_URL=http://127.0.0.1:8080 \
STREAM_URL=http://103.151.177.124:221/mjpg/video.mjpg \
node tools/check_highway_workflow.cjs
```

The test records 30 seconds. To recheck an existing recording without reconnecting to the camera, set `REUSE_JOB_ID` instead of `STREAM_URL`. Set `CHROME_PATH` for a different Chrome installation; `NODE_PATH` can point to an existing Playwright installation.

Artifacts from this validation are under `output/playwright/highway/`: `job-01a138e4-ec9d-7b91-820e-21ce7a155ff2.json`, `query.json`, `cached-query.json`, `fallback.json`, desktop/mobile screenshots, and backend/worker logs. This directory is intentionally gitignored. The query files now reflect the final restart-recovery checks.

Validation: 16 Rust tests, two retrieval contract tests, one MJPEG regression test, build, formatting, JavaScript syntax, and diff checks passed. Strict Clippy's pre-existing unrelated warnings remain as recorded above.

### Search-scope correction (2026-10-14)

The previous session-selection fix was incomplete: choosing a recorded session did not synchronize search filters, and loading a camera preset changed the query cluster without indexing anything. This could return `events_considered: 0` despite stored footage.

Search now defaults to the most recent completed recording with evidence and sends its `job_id` explicitly. Selecting a recording synchronizes cluster/camera filters; failed or unfinished recordings cannot be searched. Presets configure new recording inputs only. **Search all recordings · clear filters** removes the recording restriction and both visible filters. Polling does not overwrite manual filter edits.

The API returns `not_run_empty_index` when no clips are indexed and `not_run_filtered_empty` when clips exist but filters exclude them. Both include explanatory notes; neither is represented as successful keyword or visual retrieval. Filters are never silently broadened.

Regression check: `NODE_PATH=<existing-playwright-node_modules> node tools/check_search_workflow.cjs` against the running validation app. It exercises two completed highway recordings and a failed recording, preset isolation, deliberately stale camera filters, explicit clear-filter recovery, and actual Qwen retrieval for `truck` and a sentence query. Results and screenshots are saved in `output/playwright/search-fix/`.

Final restart verification (2026-10-15): restarted the app and Qwen worker against the persisted highway data and reran `check_search_workflow.cjs`. Both `truck` and `Show trucks waiting in marked road lanes` returned four matches from four clips using `hybrid_qwen_reranker`, with all four embeddings cached. Recording selection, failed-recording blocking, preset isolation, stale-filter diagnostics, explicit filter clearing, and mobile overflow checks passed. The completed implementation has 17 passing Rust tests; JavaScript syntax, formatting, and diff checks also passed. No new live recording was needed for this restart check.
