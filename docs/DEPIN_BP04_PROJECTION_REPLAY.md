# BP04 durable visual-index delivery

Date: 21 October 2026. Scope: canonical recording-to-visual-index handoff. The complete research plan and BP04 remain open.

## Commit and replay contract

New completed recordings commit `visual_projection` in the same synced, atomically replaced job manifest as their result. Media artifacts are already committed and referenced by that result. There is no second independent outbox-file write that could be lost between recording completion and intent creation. Existing manifests deserialize with a missing intent; explicitly preparing an older recording creates one.

The service replays pending/submitted intents when `VISN_RETRIEVAL_URL` is configured. Without configuration, intent remains saved and no model is invoked. Restart preserves revision, task ID, attempts and next-attempt time. Recording completion means retained evidence is ready; it does not mean visual preparation has completed.

Reuse the existing Python `IndexJobs` SQLite queue. Its job ID depends on scope, actual model/vector profile, content hashes and requested intervals. Delivery is **at least once**, with idempotent worker submission under the same contract. It is not an exactly-once cross-service transaction. A model/profile change can legitimately produce another task; no claim of immutable model selection before the first accepted receipt is made.

Each intent exposes:

- Stable revision UUID and state: `pending`, `submitted`, `completed`, `failed`, `cancelled` or `blocked`.
- Attempts, next-attempt time, task UUID and a hash of the configured worker address.
- An optional durable retry request and the actual worker receipt/model identity.
- Explicit last error. Transport/capacity failures back off to at most 64 seconds. Scope, contract, evidence or worker-target changes block automatic delivery pending review.

Two due intents are dispatched sequentially per scan. This bounds admission but is not a throughput qualification. Source verification/hashing cost is included in future end-to-end performance measurements; it must not be hidden as free queue overhead.

## Correctness and privacy checks

The shared visual-index payload path verifies strict-profile source manifests and hashes before remote dispatch. Events carry `expected_source_sha256`; the worker checks it when opening the source and retains it in persisted index items. Existing worker source-change checks before artifact completion remain active. This closes the previous explicit-index preparation path that did not verify the committed strict source before sending its path to the worker.

Replay validates response scope, task UUID, permitted state and, for completion, total/prepared/failed interval counts plus embedding profile presence. A success status alone cannot publish completion. The immediate prepare API also rejects a successful response with the wrong scope or invalid task ID.

HTTP receipt buffering is capped at 1 MiB. Truncated body/EOF is treated as an uncertain transport acknowledgement and retried; structurally invalid receipts are blocked. The same intent revision must still be current before an acknowledgement can update the canonical job. A stale response cannot replace a newer prepare revision.

Retry intent is persisted before the remote retry action. A crash after the worker side effect or an interrupted response can therefore replay the idempotent retry. Completed interval artifacts are retained by the worker queue. Explicit indexing cancellation remains cooperative through that queue and is reflected when replay observes its terminal state.

Neither connector authorization nor physical authenticity is established by a scope UUID. This is the existing single-node data-owner boundary. It does not make public prototype endpoints safe for a multi-tenant deployment.

## User-visible behavior

Recording JSON exposes durable projection state separately from capture state. The workspace explains that saved preparation will retry while the worker is unavailable, that an unconfigured worker needs configuration, and that accepted work is awaiting verified completion. It does not claim a prepared receipt proves scene/action accuracy. Current worker profile reporting and keyword fallback remain separate.

## Validation and reproduction

```sh
cargo build --offline
.venv-retrieval/bin/python tools/check_projection_replay.py --output output/depin-implementation/projection-new-run
```

The replay owns a fresh Rust service, loopback fixture source, actual privacy models, actual video decoding and the existing SQLite indexing queue. It deliberately uses deterministic **test embeddings**. Results prove handoff/recovery contracts, not semantic quality or GPU performance.

The replay checks:

1. A recording commits while the visual worker is unavailable; pending intent is on disk.
2. Hard restart preserves its revision and pending work.
3. The worker creates a task but returns a truncated acknowledgement; repeated deliveries resolve to one logical queue row.
4. Complete receipts retain actual test model identity/counts and survive another hard restart.
5. Wrong-scope receipts block replay and return a 502 from explicit prepare. A corrected prepare recovers without duplicate queue jobs.
6. After an injected terminal worker-failure boundary, the actual retry endpoint loses its acknowledgement, then Rust restarts. Durable retry replay completes without another logical job.
7. Changed strict media is rejected before explicit indexing, and the worker independently rejects its mismatch with the committed hash.

Final evidence is `output/depin-implementation/bp04-projection-final-v3/results.json` and its adjacent log/server logs. The full Rust regression log is `bp04-projection-tests.log`; background-index and retrieval regression logs are `bp04-index-regressions.log` and `bp04-retrieval-regressions.log`.

The rebuilt frontend's held-outage run also completed successfully after the deadline correction: `output/depin-implementation/bp04-projection-ui-final/results.json`. Regression results were 67 Rust tests passed (2 ignored), 10 background-index tests passed and 3 retrieval tests passed. Build, Rust formatting, JavaScript/Python syntax and diff checks passed.

Browser evidence: `output/playwright/bp04-recovery/projection-pending-320.png` and `projection-pending-1440.png`. Both showed saved-intent/automatic-retry messaging during a real worker outage, with document width equal to viewport width. Expected console failures were unavailable-worker HTTP responses. The browser-only held run later exceeded its original 60-second test completion deadline after reaching the intended backoff; that failed run is retained under `bp04-projection-validated`. The harness deadline now covers the maximum backoff plus acknowledgement replay. Do not present that old run as a passed end-to-end replay.

A final rebuilt-browser regression verified that projection-only refresh updates its status without clearing an existing visible answer. The check used a temporary DOM marker restored immediately afterward; it is a rendering regression check, not semantic answer acceptance. Observations are saved in `output/playwright/bp04-recovery/projection-verification.json`.

Use `--ui-seconds 180` to hold the initial outage state for browser inspection. Read `OUTPUT/ui-info.json`, then touch `OUTPUT/ui-done` to continue. Test output directories must be new. Do not aim fault injection at a shared service.

## Remaining work

- Extend canonical projection delivery to actual entity-graph and analytics consumers; do not populate disconnected prototype stores merely to claim integration.
- Implement durable revocation tombstones and access denial, cache/vector/graph/aggregate cleanup, and interrupted cleanup replay.
- Qualify model/profile changes, model service restart, scale/capacity and compatible version activation/rollback across the complete workflow.
- Complete authenticated source/workspace boundaries, generic independent evaluation, privacy utility acceptance and target-platform qualification.

The visual-index receipt is a preparation watermark for its recorded task/profile, not perpetual certification that files, models or authorization remain unchanged. Query/playback admission still verifies retained strict artifacts. No research algorithm was promoted by this change.
