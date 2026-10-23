# Milestone A, package 3: workflow diagnostics

Date: 18 October 2026. Status: implemented and development-validated. Detailed diagnostics are opt-in. This package advances P0 measurement in the [implementation plan](GENERAL_VIDEO_IMPLEMENTATION_PLAN.md); independent general-video acceptance and background indexing remain pending.

## Delivered

- Correlated query IDs across Rust retrieval/interpretation, the Python worker and outbound interpretation-model HTTP requests. The experiment runner also supplies a run ID and validates returned correlation. Ingestion uses its memory-job ID across Rust and the observer subprocess.
- Rust wall-time logs for filtered snapshots, lexical filtering, retrieval, interpretation, synthesis, linked history and observer admission.
- Python stage counts, failures, inclusive/exclusive host wall time, process CPU time and resource snapshots. Stages cover source validation, capture, observer analysis, decoding, preprocessing, embedding/cache work, reranking, generation, safeguards, visual-state verification, image encoding, serialization, response writing and existing cleanup.
- Additive retrieval/interpretation provenance snapshots and a final `WORKFLOW_TRACE` JSON log. The observer keeps its existing stdout contract and emits diagnostics on stderr; Rust forwards the successful child's final trace to its structured logs.
- Six regression tests covering nested attribution, failures, disabled probes, thread isolation, safe accelerator probing, HTTP output parity, correlation, serialization, busy admission and lock release.

The shared implementation uses Python's standard library and does not import models to measure them. Stage names are bounded by code; no per-frame trace history is retained. Source paths, images and query text are not included in the trace. Model weights, precision, frame budgets, cache keys, prompts, ranking and verification policies are unchanged.

## Enable and read diagnostics

Detailed Python diagnostics default to **off**. Add `--telemetry` to the existing pinned worker command, or set `VISN_WORKFLOW_TELEMETRY=1` in that worker's environment. `--disable-telemetry` explicitly overrides the environment. Values `1` and `true` enable the environment switch, case-insensitively. `/health` exposes `telemetry_enabled`; it does not load models.

Set the same environment variable on the Rust service before starting it to enable observer-child traces. The worker is a separate process and needs its own setting. For a direct retained-source observer check:

```bash
VISN_WORKFLOW_TELEMETRY=1 VISN_TRACE_ID=retained-replay \
.venv-retrieval/bin/python tools/event_memory_runner.py \
  --source data/live-evaluation-2026-10-16/memory/01a1433a-26bf-7e50-b482-5007781d97c7/live-1/source.mkv \
  --output-dir output/general-video-new-observer-trace \
  --max-seconds 3 --clip-mode reference
```

Use a new output directory. Reference mode is this replay's existing clip policy; the telemetry feature does not require changing clip policy. The pinned retrieval command and replay commands remain in the [first package guide](GENERAL_VIDEO_MILESTONE_A.md).

| Location | Meaning |
| --- | --- |
| `retrieval_provenance.telemetry` in Rust query responses | Python `/search` snapshot before response serialization |
| `interpretations[].provenance.telemetry` | One request-wide `/interpret` snapshot repeated on each interpretation |
| Final `WORKFLOW_TRACE` log | Completed worker request, including serialization, writing and cleanup stages when reached |
| Rust query/job logs | Rust stage timing and correlation with worker/observer IDs |

Deduplicate repeated interpretation snapshots by `(request_id, operation)`; they are not individual clip costs. The response snapshot has `final: false` and `report_scope: before_response_serialization`. Only the final log has `final: true`; its timing need not equal client response time because cleanup happens after the response. Error/busy requests can have a final log without a success-provenance snapshot. A failed response-write stage can record a client disconnect even when inference completed successfully.

Each stage reports `calls`, `failures`, `inclusive_host_secs` and `exclusive_host_secs`. Parent inclusive time overlaps child time. Do not add inclusive values together. Process CPU time is process-wide and can include other request threads. Host wall time includes scheduling/contention and is not synchronized accelerator execution time.

Resources distinguish current RSS from process-lifetime peak RSS. Linux can report both; this macOS run has no current-RSS measurement. CUDA counters are read only after initialization. MPS counters are read only when an owned model is already on MPS; MPS request/process peak allocation is unavailable. Point-in-time snapshots can miss transient peaks. Child FFmpeg processes and the external MLX model server are excluded. Do not add RSS and accelerator allocation on unified-memory machines.

Python `worker_admission` measures a nonblocking lock check, not queue wait. A busy worker still returns 503 and no queue was added. Rust separately logs time waiting for its observer gate. This package does not instrument every detector/runtime path or satisfy the full P0 resource/load gate.

## Validation and evidence

Final checks: **87 Python unit tests pass**, **30 Rust tests pass**, with the two unchanged component benchmarks intentionally excluded from the default test run. Python compilation, Rust formatting and diff whitespace checks pass. Tests include no-model-initialization diagnostics, concurrent context isolation and unchanged HTTP results when telemetry is disabled.

Real inference used the existing legacy vector profile on Apple MPS: Qwen3-VL-Embedding-2B revision `9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda`, Qwen3-VL-Reranker-2B revision `4bd860ac4f15ad1897a214615cccc700f8f71818`, eight 256-pixel frames and the existing work caches. Browser interpretation used the existing local Qwen3-VL-4B-Instruct MLX model with fingerprint `d4f53934afba23177de2a6caef3c7cdd5b868b5f2220b609584b1fe35a63beec`.

| Check | Result |
| --- | --- |
| Actual worker, tracing off/on | All four paired retrieval responses have exact ranks, scores, model/settings proof and non-cache notes |
| Warm retrieval repeats | All 20 paired responses pass the same exact comparison |
| Browser, tracing off/on | Three interpretations and 11 target crops; exact answers, scores, observations, images, decoded timestamps and state-proof outcomes after excluding named diagnostics |
| Browser interaction/layout | Existing selected-track workflow passes with no browser errors at 320, 390 and 1440 pixels; desktop evidence inspected |
| Correlation | Rust query ID equals retrieval and interpretation trace IDs; all three visual-state checks and external visual calls still execute |
| Observer replay | Final opt-in run's complete result JSON equals the frozen pre-change observer on the same retained three-second source interval |
| Rust ingestion API | Isolated recording job completes, indexes one interval and forwards the child's final trace under the same job ID |
| Configuration | Default off, flag on, environment on and explicit flag off all pass real worker startup/health/request checks without loading models |
| Source preservation | Registry source and label/request fingerprints revalidated; original footage remains unchanged |

Comparisons exclude only query/run IDs and explicitly named elapsed/cache/telemetry diagnostics, as recorded in their artifacts. Scores, evidence and claim safeguards are not rounded or relaxed. Browser interpretation retains the existing fallible model judgments: two supported answers remain labeled unverified and one remains uncertain. Parity does not establish action accuracy.

The real-model comparisons preceded the final switch from default-on to opt-in. The enabled measurement branch was retained; final unit, CLI-precedence and opt-in observer/API checks validate the final configuration. No fresh model-quality experiment is claimed for this default-only change.

## What the measurements show

| Diagnostic sample | Measurement |
| --- | --- |
| Twenty warm requests, diagnostics off | Median client request 156.7 ms |
| Twenty warm requests, diagnostics on | Median client request 171.0 ms |
| Enabled warm final traces | Median existing cleanup 81.8 ms; median retrieval stage 88.5 ms |
| Three-clip interpretation request | 5.744 s total host time; 22 FFmpeg calls totaling 1.956 s; three external visual HTTP calls totaling 3.274 s |

These are sequential development diagnostics on one host, not randomized acceptance measurements. The two warm medians differ by approximately 14.3 ms, but workload variation and cleanup contention prevent assigning that difference entirely to instrumentation. Cleanup runs after releasing the worker lock and can overlap the next request; its time must not simply be added to next-request latency. This evidence supports keeping detailed telemetry opt-in and investigating cleanup before changing it. No speedup is claimed for this package.

Retrieval-end snapshots observed about 8.51 GB of owned MPS allocation and a 9.55 GB process-lifetime RSS high-water mark in one run. They overlap on unified memory and must not be summed. Interpretation releases the retrieval models; the external model server's allocations are not part of these figures. Linux/NVIDIA runtime behavior and sustained capacity remain untested.

## Artifacts and reproduction

Artifacts are retained under `output/general-video-2026-10-18`:

- `package3-manifest.json`: final code, input and evidence fingerprints; validation summary and configuration chronology.
- `package3-baseline/`: pre-instrumentation Python source snapshots.
- `package3-telemetry-off/`, `package3-telemetry-on/`, `package3-retrieval-parity.json`: raw pinned-model responses and exact comparisons.
- `package3-warm-off/`, `package3-warm-on/`, `package3-warm-parity.json`, `package3-measurements.json`: all warm samples and timing limitations.
- `package3-worker-traces.json`: finalized enabled worker traces from the model/browser run.
- `package3-browser-parity.json` and `../playwright/package3-telemetry-{off,on}/`: API output, browser checks and screenshots.
- `package3-ingest-final-validation.json`: final opt-in observer trace and frozen-output parity.
- `package3-ingest-api-final/validation.json`: completed isolated API job and correlated child trace.
- `package3-cli-validation.json`: final default/flag/environment precedence checks.

```bash
.venv-retrieval/bin/python tools/check_workflow_telemetry.py
cargo test --offline
cargo fmt --check
git diff --check
```

For model parity, run the existing profile replay against separate sequential worker starts, with and without `--telemetry`, and compare using `general_video_experiment.py compare`. Use new output directories and the same pinned profile/input bytes; retain both client snapshots and final worker logs. For the browser check, use the existing `tools/check_track_grounding_browser.cjs` with `VISN_TEST_URL`, `VISN_GROUNDING_OUTPUT` and a running application/worker. This requires installed Playwright and its browser, not a new project dependency. The test-only Playwright installation used here lives under `/private/tmp/visn-ui-qa`.

All temporary validation listeners have been stopped. The pre-existing external model server was left alone.

## Decision and next package

Keep the diagnostics available explicitly; keep detailed tracing disabled by default. Lightweight Rust stage logs remain enabled. To disable diagnostics, restart the worker with `--disable-telemetry` and unset `VISN_WORKFLOW_TELEMETRY` on the Rust service for future observer children. This does not delete indexes, recordings or labels. Code rollback must reverse this package's instrumentation only and preserve the previous packages' pending changes.

Next: durable background embedding tasks, bounded admission and progress reporting under P1/Milestone B. Use these measurements to diagnose cleanup/model switching and decoding costs, then evaluate changes individually with exact evidence and verification checks. Cancellation/restart, duplicate work, stale-source commits, concurrent query/index work and explicit partial coverage need integration tests before promotion. General corpus acquisition and independent review continue alongside engineering; the current three traffic sources and 13 development clips cannot satisfy generic-video acceptance.
