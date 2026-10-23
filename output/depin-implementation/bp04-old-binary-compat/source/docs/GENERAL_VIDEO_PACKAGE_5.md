# Package 5 — Cross-process model residency and mixed-load development checks

Date: 19 October 2026. Scope: the next P1 increment in the [general video plan](GENERAL_VIDEO_IMPLEMENTATION_PLAN.md), following [durable background indexing](GENERAL_VIDEO_PACKAGE_4.md).

## Delivered behavior

`VISN_RESOURCE_DIR` enables a shared local coordination group. Leave it unset to retain the existing default. No new dependencies, model weights, prompts, numerical settings, frame budgets or verification rules are introduced.

- One participating process owns model residency at a time. A retrieval owner can retain its embedding/reranker weights; this is an owner-group bound, not one individual model or a hard byte budget.
- Warm weights remain while uncontended. A persistent detector yields between batches; retrieval/owned interpretation yields after an operation or during idle maintenance. Weight cleanup completes before the next process receives ownership. Camera trackers remain separate from detector weights.
- FIFO tickets and an OS advisory lock coordinate processes. Locked tickets prove waiter liveness; crashed waiters are removed without heartbeat/PID assumptions. No timer steals a running owner's lease. The waiting queue defaults to 32 and admission to 180 seconds.
- A failed owned-model cleanup retains ownership. Exceptions retain the lease until request-local tracebacks/tensors have left scope; idle maintenance can then unload. An isolated detector child inherits the lock FD so killing only its wrapper cannot admit another owner while the child lives.
- Background preparation waits before claiming an interval or spending an attempt. Resource contention does not turn into an embedding failure or reduce evidence coverage. The existing per-worker interactive-priority scheduler remains in place.
- Health/index metadata disclose ownership, waiters and external recovery. The workspace displays busy/recovery states and retains explicit keyword fallback on unavailable visual retrieval.
- Cache-only handoff skips model garbage collection/allocator trimming when the worker holds no model weights. Required model cleanup remains in place.

Implementation: `tools/workflow_resources.py`, retrieval/indexing/interpreter/detector lifecycle integration, `src/resources.rs`, Rust VLM and isolated-detector integration, configuration and workspace status. Coordination uses POSIX local file locking; a distributed/network-filesystem broker and Windows support are outside this package.

## Dedicated external model-server contract

Rust's external VLM adapter and Python's external sequence interpreter can participate only with `VISN_MANAGED_LMSTUDIO=true` and a dedicated native-v1 LM Studio server. It must initially have no loaded instances. Pre-existing unmanaged instances are refused and never unloaded automatically. Do not share this endpoint with unrelated clients.

The guardian checks native instance state, holds ownership through loading/inference, receives completion acknowledgement only after the client has received its full successful response, unloads instances, then verifies the native list is empty. Rust acknowledges before CPU-only content validation, so an invalid answer body inside a valid completed chat response can still be cleaned up safely. HTTP/decode failure or client death before acknowledgement is treated conservatively as uncertain work.

Unload uses `POST /api/v1/models/unload` with `instance_id`; instance inventory comes from `GET /api/v1/models`. These follow the official [unload](https://lmstudio.ai/docs/developer/rest/unload), [list](https://lmstudio.ai/docs/developer/rest/list) and [load](https://lmstudio.ai/docs/developer/rest/load) contracts. The Rust legacy unload path retains its older field fallbacks outside guardian cleanup.

`external.json` persists uncertainty after guardian/client death. Ordinary owners cannot bypass it. A failed acknowledged cleanup can be reconciled by a matching guardian. An unacknowledged operation requires explicit recovery after verifying the dedicated server has stopped loading/generating; an empty list alone does not prove that pending loading cannot finish later. Recovery refuses a different endpoint and does not automatically evict pre-existing unmanaged instances.

Stop/drain participating clients first (a Python client can retain its lease after a failed cleanup), then establish dedicated-server quiescence. From the project root, use the same resource directory and dedicated endpoint:

```bash
export VISN_RESOURCE_DIR="$PWD/output/local-model-residency"
printf '%s\n' '{"endpoint":"http://127.0.0.1:1234/api/v1"}' | \
  .venv-retrieval/bin/python tools/workflow_resources.py --recover-external
```

For authenticated servers provide `token` through a protected stdin producer; do not place a real secret in shell history. Do not delete quarantine markers to bypass recovery. OpenAI-only/MLX servers and a real LM Studio runtime remain unqualified for coordinated mode; use the existing uncoordinated profile for those backends.

## Enabling and rollback

Set the same absolute `VISN_RESOURCE_DIR` and `VISN_RESOURCE_QUEUE_LIMIT` for Rust, retrieval and every detector process, using a dedicated local directory. The Rust service launches guardians with `VISN_DETECTOR_EXECUTABLE` (Python) and `VISN_RESOURCE_RUNNER_ARGS` (default `tools/workflow_resources.py`). Python resource deadlines can be fractional; Rust requires integer seconds. Native external calls additionally require the dedicated-server flag above; without it, coordinated external calls fail explicitly and existing fallback is disclosed.

This is cooperative scheduling: an unrelated model application, a worker started without the directory, per-process CPU caches, decoder processes and weights inside a single owner are outside the residency bound. The directory contains control state, not source recordings or embeddings. Do not infer total memory usage from owner count.

To roll back, drain/stop all participating clients and establish that any external server is idle/unloaded. Resolve an external quarantine first. Unset `VISN_RESOURCE_DIR` and `VISN_MANAGED_LMSTUDIO` in every process and restart. Keep vector/task databases, recordings and reviewed labels. The feature does not migrate those artifacts. The frozen pre-package source is under `output/general-video-2026-10-18/package5-baseline`; the default-off path remains tested by the full regression suite.

## Validation and limits

Pinned retained-file tests use the original three development traffic recordings; no new action labels are inferred. Source hashes and original annotations are checked without rewriting them. The frozen control runs before the candidate with no competing heavy workload. Qwen embedding/reranking use the existing pinned 2B revisions on MPS, 8×256 frames and existing FP16 settings. Detector controls use the same YOLO26s weights, CPU FP32, 640 pixels, one frame per batch and 1 FPS retained-file sampling.

| Check | Result / scope |
| --- | --- |
| Unit regression | 108 Python tests pass, including 11 process/resource tests; 30 Rust tests pass, two existing component benchmarks excluded |
| Uncontended retrieval | Two queries × ten warm repeats, identical scores, ordering and non-diagnostic proof; first candidate requests recorded separately |
| Detector control | Exact single-stream observations, global IDs, frame coverage and representative evidence |
| Mixed replay | At least 60 seconds each at 1, 2 and 4 concurrent retained-file streams, concurrent questions and background interval embedding |
| Persisted artifacts | All 25 vector rows match the frozen control exactly, including 12 freshly embedded intervals |
| Process failures | Owner/waiter crashes, FIFO, bounded overflow, unload failure, exception retention, inherited child ownership and retry preservation |
| External lifecycle | Actual Rust API and Python guardian against a native HTTP fixture: auth, official unload body, completion, killed client quarantine, explicit quiescent recovery |
| Browser | Actual rebuilt Rust/worker status, held test lease, keyword fallback and recovery marker; responsive status rendering and screenshots |

Cross-camera tracker IDs use an existing process-global counter and can be assigned in a different order under concurrent replay. The multi-stream check excludes only `track_id` when comparing raw observations; numeric detections and sampled-frame counts remain exact. Single-stream comparison includes IDs and full result evidence. This is not verified physical identity or proof of whole multi-camera event-graph equivalence.

The native HTTP fixture validates lifecycle protocol, not real model quality, real LM Studio cancellation behavior or memory reclamation. The browser lease actor holds no weights and its recovery marker is synthetic. The pinned Qwen/YOLO load replay uses real local models and retained video. These distinctions are recorded in machine-readable summaries.

Final replay timings (seconds; p95 uses the nearest rank):

| Workload | Questions | Query median / p95 / max | Detector cycles / sampled frames | Prepared intervals |
| --- | ---: | --- | --- | --- |
| Uncontended frozen control | 20 | 0.071 / 0.086 / 0.088 | — | — |
| Uncontended coordinated candidate | 20 | 0.083 / 0.098 / 0.099 | — | — |
| 1 retained-file stream, 60.16s | 80 | 0.081 / 0.200 / 3.493 | 97 / 582 | 4/4 |
| 2 retained-file streams, 61.00s | 81 | 0.082 / 0.158 / 3.461 | 49 / 588 | 4/4 |
| 4 retained-file streams, 61.48s | 81 | 0.085 / 0.201 / 3.536 | 24 / 576 | 4/4 |

All 242 mixed-load answers passed exact retrieval comparison. Each detector cycle replays six seconds at 1 FPS per stream with file backpressure; these are accelerated repeated files, not six minutes of continuous live-camera capture or a live sampling guarantee. The worst detector cycle took 16.78 seconds under model handoff. Cold/new inference, concurrent uncached questions and long steady-state loads need separate qualification.

The initial implementation performed allocator cleanup even after weights were already absent. Under the earlier replay its mixed-query medians were 0.163–0.169s; conditional model cleanup reduced the final observed medians to 0.081–0.085s. These are sequential development runs, not a randomized causal performance claim. The final uncontended candidate is still about 16% slower than its frozen control on median, so it does not meet the efficiency-promotion gate. First requests are excluded from both warm samples and saved separately for the candidate.

Worker process-lifetime RSS high-water marks were 9.54 GB for control and 10.22 GB for the candidate's larger mixed workload; sampled maximum MPS allocation was 8.51 GB in both. They do not demonstrate a memory-byte saving. MacOS telemetry reports process-lifetime RSS peaks; current RSS is unavailable. MPS point samples are not peaks and allocator state after unload is not a total-system measurement. CPU RSS and GPU memory must not be added on unified memory. No hard byte reduction, generic accuracy gain or production capacity qualification is claimed. Raw values and measurement caveats are in `package5-load-final/measurements.json`.

## Artifacts and repeatable checks

Under `output/general-video-2026-10-18`:

- `package5-baseline/`: frozen pre-change implementation.
- `package5-load-final/`: final pinned real-model controls, warm timings, detector observations, 1/2/4-stream results, resources, logs, source/model/code hashes and summary. The runner rejects source changes during execution.
- `package5-native-verified/`: actual Rust/guardian native HTTP fixture results, evaluated source/binary hashes and call log. Earlier `package5-native` documents a test-query mistake: the retained metadata had no truck captions, so no synthesis was triggered; the fixture now uses the existing `activity` term.
- `package5-load/`: earlier passing replay before conditional cleanup and recovery-queue refinements. Its candidate warm file included first requests; do not use it as a paired warm benchmark.
- `package5-python-tests.log`: full unit-suite output.
- `package5-manifest.json`: evaluated source/binary/input/artifact fingerprints and decision.
- `output/playwright/package5-resources-verified/`: browser screenshots after paint settlement, evaluated source/binary hashes, actual fallback response, control visibility and result summary.

```bash
.venv-retrieval/bin/python tools/check_resource_coordination.py
cargo test --offline
cargo fmt --check
cargo build --offline
node --check static/app.js
git diff --check
.venv-retrieval/bin/python tools/check_resource_load.py \
  --output output/general-video-2026-10-18/package5-next-load --phase-seconds 60
.venv-retrieval/bin/python tools/check_resource_native_workflow.py \
  --output output/general-video-2026-10-18/package5-next-native
NODE_PATH=/private/tmp/visn-ui-qa/node_modules node tools/check_resource_browser.cjs
```

Use a fresh output directory (and `VISN_RESOURCE_BROWSER_OUTPUT` for another browser run). The real-model runner requires retained data, the frozen baseline, package 4 vector backup and cached pinned models/MPS. Native tests use local HTTP fixtures and no real external model server. Chrome/Playwright use the existing test installation. Test listeners use 8220, 8230/8231 and 8240/8241 respectively and must be free; runners stop their processes in `finally`.

## Decision and next package

Keep residency coordination available as an opt-in conservative contention profile. Preserve the uncoordinated baseline as default; reload/serialization costs and incomplete memory/load qualification do not justify global promotion. No precision/sampling/verification trade-off is accepted to hide contention.

Next: P2 grouped FFmpeg decoding with a bounded per-request source/timestamp plan. Compare exact RGB pixels and actual PTS across CFR/VFR, EOF, repeated timestamps, corruption and changed sources before timing end-to-end cold work. Retain the existing decoder wherever equivalence fails. Generic reviewed-corpus acquisition, actual native-server lifecycle qualification, longer mixed loads, live replacement/reconnect coverage, Linux/NVIDIA and measured global-memory budgets remain separate open gates.
