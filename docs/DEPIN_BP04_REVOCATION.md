# BP04 recording revocation and derived-cache cleanup

Date: 21 October 2026. Status: integrated local candidate. This does not complete BP04 or the research implementation plan.

## Scope and guarantees

`POST /api/v1/memory-jobs/{id}/revoke` writes a durable tombstone before acknowledging revocation. It cancels active recording work, removes searchable event/entity descriptors and checkpoint admission from the canonical job, and invalidates visual-preparation readiness. Canonical media files remain under the existing retention policy. This endpoint is **access revocation, not physical erasure**.

The tombstone contains a stable revision, time, cleanup state, attempts/backoff, expected worker-address fingerprint and eventual receipt. Repeated requests preserve the revision. A blocked cleanup may be retried using the same endpoint after repairing configuration; this does not restore access. Pending cleanup survives service restart. No un-revoke operation exists.

After acknowledgement:

- Scoped questions return HTTP 410. All-recording search excludes the revoked recording.
- Thumbnail, clip and source routes return 404. New evidence responses use `Cache-Control: private, no-store`.
- Visual preparation and entity-memory building are denied; old worker task controls cannot be used through the recording routes.
- Recording resume/cancel cannot reopen the source. Duplicate original submission keys still refer to the same revoked record rather than creating a new live capture.
- The recording API exposes operational tombstone status with no retained result payload. Searchable graph/event descriptors are removed from that job.

Queries recheck every recording used by their retrieval/graph snapshot before returning. This rejects a semantic answer whose input was revoked while the model was running. Entity-memory enrichment merges its result only after reacquiring the job mutation gate and checking current revocation/status; it cannot overwrite a tombstone or a newer projection update with an old job clone.

Previously delivered bytes or a stream already admitted before revocation cannot be recalled. No claim is made that old downloads, browser copies, filesystem backups or external model-service logs are erased. Authentication/authorization for a multi-tenant deployment remains a separate open package; the current scope is the existing local data-owner boundary.

## Durable worker cleanup

Rust replays cleanup to `/revocations`, carrying the scope/revision and canonical source paths. The worker persists source/scope denial first, then cancels scoped index tasks, clears affected persisted vector/interpretation artifacts and invalidates in-memory decode/hash/query/rerank caches. Only then can it acknowledge completed cleanup. Interrupted or lost acknowledgements can be retried idempotently.

`revocations-v1.sqlite3` is shared by the two supported vector-policy workers. Every source admission checks its persistent tombstones. This keeps denial active across policy owners and restarts. Revoked index scopes cannot be submitted/retried, and revoked visual candidates are rejected before lexical/semantic processing. Interpretation requests use the same source checks and now receive the committed strict-source hash from Rust.

Artifact lineage records canonical source paths for both vector and interpretation caches, including cache hits. An artifact independently referenced by another non-revoked source may remain reusable. Legacy cache rows without attributable lineage are conservatively invalidated rather than assumed unrelated. This can cause recomputation; it does not change model precision, evidence sampling or answer thresholds.

Cleanup is tracked for each existing supported vector-policy database:

- The current policy clears its own cache while holding its inference gate.
- An inactive policy is cleaned only after acquiring its owner lease.
- Another active policy sees the shared pending cleanup and performs it under its own gate. Until all pending policies finish, the cleanup endpoint returns a retryable 503 rather than claiming completion.

The worker's persistent denial is committed before cache mutation. Its cache and cleanup-status databases are not a distributed atomic transaction; replay repairs either ordering boundary. This is not a declaration of complete erasure across arbitrary stores. Future graph/OLAP projections need their own registered cleanup consumers before their integration can be accepted.

## Contributor workflow

The workspace offers **Revoke recording access** with a native confirmation describing search/playback withdrawal, cache cleanup and retained-media policy. Confirming saves the tombstone; dismissing leaves the control flow unchanged. Revoked recordings display pending/completed/blocked cleanup status, hide recovery and disable searching that recording. Blocked cleanup exposes a retry control.

On observed revocation, the browser pauses/detaches evidence players and clears raw result text and answers. It tracks an access revision so an older in-flight response cannot repopulate the page after a newly observed revocation. Revoked records no longer poll visual preparation as if it were available. Branding, logo and landing animations were not changed.

## Validation

### Real application replay

```sh
cargo build --offline
.venv-retrieval/bin/python tools/check_recording_revocation.py --output output/depin-implementation/revocation-new-run
```

Requires the prepared BP03 privacy models and cached public `face-sequence.mkv` fixture. Use a new output directory. The script owns its loopback source, Rust instances and Python worker queue; it does not revoke existing user recordings or stop other services.

Final non-browser evidence: `output/depin-implementation/bp04-revocation-final-v3/results.json`, adjacent log and server logs. This replay ran actual privacy inference, encoding, video decoding and SQLite index execution with deterministic **test embeddings**. It verifies integration/recovery, not semantic quality or GPU performance.

The replay checked:

1. Capture, automatic visual preparation, keyword retrieval and evidence playback before revocation; media responses carry `no-store`.
2. Revocation while a semantic response is deliberately held in flight; that response is rejected with 410 after release.
3. Search, source/clip/thumbnail, resume, entity-memory and preparation denial; all-recording search contains no revoked evidence.
4. Idempotent repeated revocation and persistence through a hard Rust restart during a real worker outage.
5. Worker cleanup committing before a deliberately truncated acknowledgement, followed by restarting both services and replaying the same revision to completion.
6. Persisted vector/interpretation cache removal, cancelled worker task, direct worker rejection of revoked search/submission/retry, and unchanged retained media hash.

### Component and browser evidence

- Rust tests include active-job revocation, duplicate submission behavior, restart persistence and scoped HTTP 410. Results: `output/depin-implementation/bp04-revocation-rust.log`.
- Four added indexing cases cover shared permitted artifacts, canonical path aliases, active second-policy coordination, inactive-policy owner leases, interrupted cleanup and worker restart. All 14 indexing tests passed: `bp04-revocation-index-final.log`.
- Retrieval, temporal interpretation and API-adapter regressions passed: `bp04-revocation-retrieval-final.log`, `bp04-revocation-interpretation-final.log`, `bp04-revocation-interpretation-api-final.log`.
- The browser ran a real keyword search, opened/dismissed the confirmation and then confirmed revocation through the actual UI. The harness independently observed that the browser had revoked before its own revoke phase. Pending-cleanup UI fit at 320/768/1440 pixels; evidence players and old summary/raw text were empty, search was disabled and resume hidden.
- Screenshots and scoped observations: `output/playwright/bp04-recovery/revocation-320.png`, `revocation-768.png`, `revocation-1440.png`, and `revocation-verification.json`. The browser replay completed cleanup/restart checks under `output/depin-implementation/bp04-revocation-ui-final/`. Its report explicitly marks the in-flight-query test as covered by the separate non-browser replay.

The first harness run failed because its queued-state probe treated a null projection as an object; corrected before the subsequent successful runs. A component test exposed non-canonical path lineage leaving an interpretation row behind; canonicalization fixed the root cause. Expected 410 responses produce worker error logs during negative probes. Tool-only browser issues (a redundant dialog dismissal, a relative API URL and an unavailable CLI network command) are recorded in the browser observations rather than claimed as passed assertions.

## Open work

- Real graph and analytics projection consumers, their watermarks and replay/cleanup registration.
- Source reconnect/clock discontinuity, complete coverage semantics, and projection/profile migration/rollback.
- Authentication/authorization, retention/deletion policy enforcement and any required external-store/backup disposal.
- Independent generic privacy/utility/model acceptance, sustained mixed load and Linux/NVIDIA qualification.

Do not promote a research algorithm, claim complete anonymization/erasure, or mark the full goal complete from these scoped tests.
