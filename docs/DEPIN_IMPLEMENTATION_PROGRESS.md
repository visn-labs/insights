# DePIN implementation progress

## Active objective

Deliver and validate the complete [research implementation plan](DEPIN_RESEARCH_IMPLEMENTATION_PLAN.md). No end-to-end completion or algorithm promotion is established by the first contract package.

## 21 October 2026 first implementation increment

### Baseline and evaluation inventory

- Saved pre-edit snapshots and SHA-256 hashes of 94 Rust/Python/source/configuration/plan/corpus files, including the uncommitted telemetry additions, under `output/depin-implementation/bp00-baseline/`.
- Used the existing general-video experiment runner to inventory the current corpus. It has 3 traffic development sources and 13 high-activity clips; independent general-video acceptance coverage remains missing. Preserve existing reviewed labels and source/session splits.
- This snapshot is a correctness-control starting point. It does not freeze every model weight/runtime or establish BP00's complete model-backed baseline replay.

### Production code changes implemented

- Vector insert/search now return validated errors for dimension, nonfinite/zero vectors, duplicate IDs, invalid top-k and oversampling, instead of panicking under the shared mutex. Valid requests remain usable after malformed requests. Full-precision cosine accumulation uses wider intermediates to avoid overflow; this affects the new experimental helper, not the existing production semantic retrieval worker.
- Privacy/anomaly helper state is scoped by workspace/camera/session with bounded session counts, monotonic timestamp checks and explicit session reset. Scope identifiers are not authenticated identities.
- Newly observed low-confidence faces receive masks. Current detections get conservative additional coverage if smoothed predictions lag. Mask edges clip to frame bounds. These remain box-level guarantees, not pixel-level redaction or complete privacy.
- Calibration is unverified. Caller-supplied signal arrays cannot authenticate a stream, even when their thresholds match. Invalid correlation geometry, insufficient PCE background and impossible frequency traces no longer produce an authentic result. Matching values are explicitly evaluation-only; independent enrollment/extraction/reference infrastructure is still absent.
- Contribution calculations reject duplicate camera entries, invalid measurements and negative/nonfinite pools. Quality below the configured minimum receives zero weight/allocation. Responses explicitly prohibit settlement; the trusted transactional reward ledger remains future work.
- Capability status identifies the telemetry contract as an unintegrated prototype. Unsupported production capability flags are false. Fabricated FPS/token-pruning values become null; actual measured helper latency is no longer floored to a plausible benchmark.
- Empty RAG cannot claim temporal confirmation; selected frame count respects a maximum rather than a minimum of14.
- Corrected Python helper authenticity/mask behavior and labeled the helper suite synthetic. Historical blueprint documentation now identifies its quantitative descriptions as unvalidated proposals.

### Evidence

| Check | Current result | Artifact |
| --- | --- | --- |
| Rust tests |64 passed,0 failed,2 ignored | `output/depin-implementation/bp01-regressions.log` |
| Build | Passed | `output/depin-implementation/bp01-build.log` |
| Synthetic Python helpers | Passed; not video/quality acceptance | `output/depin-implementation/bp01-python-helpers.log` |
| Real HTTP regression |30 requests plus correctness assertions passed against a separate instance | `output/depin-implementation/bp01-http-contracts.json` |
| General corpus inventory | Development traffic coverage only; acceptance incomplete | `output/depin-implementation/bp00-baseline/corpus-inventory.json` |

Re-run the HTTP checks against an isolated freshly built service with:

```sh
python3 tools/check_depin_contracts.py http://127.0.0.1:8231
```

The older audit reproduction script preserves the original observations and request shape. The new scoped redaction/anomaly contract requires `workspace_id`, `camera_id`, `session_id` and `timestamp_ms`; the old unscoped request is deliberately not accepted as stateful shared input. No existing landing animation or ordinary video-memory request was changed.

## 21 October 2026 sanitized media integration increment

Implemented the actual BP03 candidate described in [sanitized media integration](DEPIN_BP03_SANITIZED_MEDIA.md): pinned real YuNet/ONNX plate inference, per-frame pixel replacement before capture persistence/analytics, lossless encoded-frame verification, source/ledger/derivative hash checks, and strict-profile upload/recording/detector/query/playback wiring.

The candidate is opt-in with `VISN_MEDIA_POLICY=sanitized-v1`; the control profile remains explicitly raw research, and no general privacy qualification or algorithm promotion is claimed. Strict uploads buffer at most 64MiB of encoded content in memory; they never create a raw staging file. Audio is removed, high-bit-depth input is rejected rather than silently reduced, and source timestamps or explicitly labeled observed-arrival clocks are recorded. Historical recordings are not relabeled sanitized.

Validation:

- 7 privacy tests passed, including real face/plate positive sequences, lossless pixels, VFR timing, audio removal, missing/changed models, failure cleanup, high-bit-depth rejection and live multipart-MJPEG clock behavior: `output/depin-implementation/bp03-pixel-tests.log`.
- 64 Rust tests passed, 2 ignored; existing experiment-runner 5 tests and memory-runner 1 test passed. Build, format, JS syntax and diff checks passed.
- Latest real HTTP replay passed 24 requests/assertions, including uploads, H.264 derivatives, recording, keyword retrieval and changed-artifact rejection: `output/depin-implementation/bp03-workflow-latest/results.json`.
- A missing-model service rejected uploads and failed recordings without creating media files: `output/depin-implementation/bp03-fail-closed-http.json`.
- Chrome played recovered sanitized evidence after service restart, with advancing playback and 4 decoded frames, no media error and no phone viewport overflow. Browser inspection found and fixed repeated optional-service polling; backoff verification observed 2 failed requests over 8.5 seconds. Evidence is under `output/playwright/bp03-privacy/`.

These are real integration and regression results, not independent detector recall, general action accuracy, semantic-model acceptance, source authenticity or settled rewards. The optional visual-index/VLM service was deliberately absent during the media workflow checks; keyword retrieval and media behavior were verified separately. Owned test servers and browsers were stopped after validation.

## Remaining work in the active plan

### BP04 recording/recovery increment

The local durable recording slice is implemented and validated as described in [recording recovery](DEPIN_BP04_RECORDING_RECOVERY.md): exclusive data ownership, synced atomic job publication, bounded idempotent submission, cancellation/owned worker cleanup, retained-only resume, anchored camera checkpoints and explicit capture-timing uncertainty.

Retained evidence: 67 Rust tests passed (2 ignored), 8 privacy tests passed, and the recovery fault replay passed 63 HTTP checks plus process/hash/source-request assertions. A subsequent regular-worker media replay passed 25 API checks including actual sanitizer/upload/playback, recording, keyword retrieval and modified-artifact rejection. Browser checks verified stop, keyboard resume failure without retained evidence, and no horizontal overflow at 320/768/1024/1440 pixels. These are scoped integration checks, not general quality acceptance. Details and artifact locations are in the increment document.

### BP04 visual projection delivery increment

Implemented [durable visual-index delivery](DEPIN_BP04_PROJECTION_REPLAY.md). New recording completion and visual-index intent share one atomic manifest commit. Replay reuses the existing durable Python indexing queue, with acknowledgement validation, retry/backoff, versioned intent, persisted retry-before-side-effect ordering, and strict source hashes checked by both Rust and the worker. The workspace now reports saved preparation intent during outages.

The owned fault replay covers worker outage, hard restart, truncated submission/retry acknowledgements, wrong-scope receipts, explicit recovery, idempotent queue rows and changed-source rejection. Test embeddings are deliberately synthetic; actual privacy inference, video decoding and SQLite queue execution are real. Browser outage messaging fit at 320 and 1440 pixels. See the increment document and final run artifacts for exact scope and the retained earlier test-deadline failure.

### BP04 revocation increment

Implemented [recording revocation and derived-cache cleanup](DEPIN_BP04_REVOCATION.md): a durable tombstone denies search/playback/recovery, retires canonical event/entity descriptors, and queues idempotent worker cleanup. Source denial persists across both supported vector policies. Cleanup handles shared artifact lineage, legacy untracked caches, active/inactive policy owners, worker outages and lost acknowledgements. The contributor UI confirms revocation, clears old evidence/results, and shows pending/blocked/completed cleanup without claiming media erasure.

The real application replay passed scoped/in-flight query denial, artifact/preparation/recovery denial, outages, hard restarts, truncated cleanup acknowledgement recovery and retained-media hash checks. The 14-test indexing suite covers shared artifacts and interrupted/multiple-policy cleanup; separate Rust, retrieval and interpretation regressions passed. Browser verification covered the actual revoke action and narrow/desktop layouts. See the increment document for exact evidence, test embeddings and limits.

### 22 October 2026: BP04 graph/analytics projection increment

Implemented [durable evidence metadata and statistics](DEPIN_BP04_METADATA_PROJECTIONS.md): atomic versioned projection delivery, verified graph consumption with canonical fallback, typed interval statistics, explicit unavailable tracker counts, rebuild controls and retryable local revocation cleanup. The local projection loop works independently of the semantic worker.

The Rust suite passed 71 tests (2 ignored). Real privacy/YOLO replay agreed with an independent reference: 2 intervals, 7,960 ms coverage, 10 tracker histories, 29 detector observations and 39 ordered observation links. Corruption, pending-publication restart, source mutation, symlink-safe cleanup and existing visual/revocation regressions passed. Real statistics and blocked cleanup were checked in Chrome at narrow/desktop sizes. See the increment document for precise evidence and costs; these counts do not establish tracking accuracy or general quality acceptance.

Next BP04 engineering should address session reconnect/clock-boundary semantics and profile/rollback integration. Independent BP02/BP03 acceptance remains open. Revocation is not physical erasure or multi-tenant authorization, and the projection outboxes are not a completed cross-store transaction system.

### 22 October 2026: BP04 association-boundary increment

Implemented [reconnect and observed-gap association boundaries](DEPIN_BP04_ASSOCIATION_BOUNDARIES.md) in both actual detector paths and the privacy sanitizer. Tracker and appearance state reset; frame-carried epochs and session-prefixed track IDs prevent reset ID reuse from merging saved histories. Existing uninterrupted control identifiers remain unchanged. Long spacing is disclosed as uncertain observation continuity, not verified camera frame loss.

Three contract checks passed, including the real capture-loop reconnect branch. Real YOLO replay through both adapters passed on an injected PTS gap. The full sanitized recording, graph and metadata fault workflow passed; the canonical saved graph had two pre-gap and six post-gap histories with no history spanning both sides. Rust regressions passed 71 tests, 2 ignored, after correcting legacy-note compatibility. Formal durable source sessions, automatic clock-reset rollover, gap UI and profile rollback remain open; this increment does not establish tracking accuracy.

### 22 October 2026: durable capture association sessions

Implemented [persisted capture-session ranges and provenance admission](DEPIN_BP04_DURABLE_CAPTURE_SESSIONS.md). Session IDs/ranges survive canonical recording commits and metadata rebuilds; typed timing metadata and ledger hashes are anchored on consumer admission. Saved graphs cannot place one entity across recorded sessions. Legacy session knowledge remains unavailable and old optional-field serialization is preserved.

Rust tests passed 73 tests, 2 ignored; the real-model privacy suite passed all 8. The real API replay verified unchanged-video/changed-capture-metadata rejection as well as restart and revocation behavior. Automatic transport/source-clock rollover, explicit unavailable coverage, session UI and profile rollback remain open. These additions do not complete BP04 or establish tracking quality.

The subsequent session UI increment exposes canonical observed ranges, frame totals, session IDs and uncertain timing boundaries. Chrome keyboard expansion and 320/768/1024/1440 px layouts passed on real recorded gap evidence. Revocation removes timeline content. Legacy-unavailable and arrival-clock explanations passed renderer checks. Evidence and the corrected stale-embedded-binary browser attempt are recorded in the durable sessions document. Transport/source-clock rollover, unavailable coverage and profile rollback remain open.

### 22 October 2026: source-clock rollover capture candidate

Implemented [independent source-clock capture files](DEPIN_BP04_CLOCK_ROLLOVER.md) as an explicit capture-layer interface. Genuine decoded clock regression and geometry changes start new sanitized files without retiming or dropping the boundary frame. A durable session journal and verifier distinguish complete captures from recoverable committed prefixes. Five codec/failure/tamper checks pass; actual cached face inference and verified playback passed on both clock-reset sessions. The existing real privacy suite passed all 8 tests.

This interface is not yet enabled in recording/indexing APIs: canonical IDs, intervals, graph, interpretation, analytics and recovery must first represent multiple independent session clocks. Automatic transport reconnect is also open. The single-file application path still rejects backwards source clocks. See the next integration requirements in the rollover document; this staged capture primitive does not satisfy the full end-to-end rollover gate.

### 22 October 2026: canonical source-session identity

Implemented [session-aware canonical identities and query scopes](DEPIN_BP04_SOURCE_SESSION_IDENTITY.md). New sanitized events and tracker entities carry the committed source-session ID; equal camera timestamps/local tracker IDs in distinct declared sessions receive distinct UUIDs and projection scopes. Session filters apply before retrieval and graph expansion, with independently computed per-session analytics. Legacy optional fields and identifiers remain unchanged.

Rust regressions passed 74 tests, 2 ignored. The two-session canonical contract passed distinct-ID, scope, graph, coverage and tamper checks. Real sanitized recording/YOLO/API replay passed positive/negative session filters, restart, provenance corruption and revocation checks. Capture and continuity suites passed 5 and 3 tests respectively. Multi-file application materialization/checkpointing remains next; the real API recording still has a single source clock. No full rollover or algorithm-quality promotion is claimed.

### 22 October 2026: session storage and checkpoint-set consumer

Implemented [UUID-scoped evidence paths and ordered checkpoint sets](DEPIN_BP04_SESSION_STORAGE_CHECKPOINTS.md), including legacy single-checkpoint compatibility, scope/size validation, per-member retained artifact checks and distinct physical camera totals. Recovery attempts with a checkpoint anchor verify it and never reconnect an old live interval on validation failure.

Rust tests passed 75 tests, 2 ignored. A durable two-directory/set contract verifies path isolation, ordering and scope rejection using synthetic storage fixtures. The actual single-source API recovery replay passed 64 HTTP plus crash/ownership/child checks, and real sanitized/YOLO metadata recovery passed. The observer/materialization producer still returns one source result; wiring it to capture-directory sessions and validating actual multi-session media end to end remain next. No full rollover completion is claimed.

### 22 October 2026: integrated multi-session recording candidate

Connected [clock-reset capture to application materialization, queries and recovery](DEPIN_BP04_MULTISESSION_RECORDING.md), opt-in under sanitized capture. The worker verifies the session journal, analyzes each timeline independently, returns typed session results and checkpoints the whole set. Physical camera totals remain separate from session totals; paths, events, tracker requests, graphs and analytics preserve source scope. The workspace has capture/session controls and a longest-session duration label.

Rust tests passed 76 tests, 2 ignored. Real sanitized/YOLO clock-reset replay passed distinct IDs, local metadata/graph queries, per-session analytics, media, restart, whole-set checkpoint recovery without an observer or new source request, and revocation. Legacy recovery passed 68 HTTP plus crash/ownership/child checks. Actual UI submission, keyboard type-ahead source filtering, two decoded advancing clips, four responsive widths and native-confirmation revocation passed. Browser testing found and fixed a queued null-report error; fixture testing found and corrected accelerated test timestamps without relaxing model thresholds.

The final entity-picker validation also passed on actual source-session graphs: two eligible entities per selected session, exactly one explicitly selected entity returned, and all four viewport widths without overflow. Its held replay and earlier controlled-restart interruption are retained separately in the multi-session document.

Final mobile review also corrected low-contrast legacy answer colors using the existing theme token. Rendered summary/metadata contrast is 5.35:1 on the cream card; the check reused retained evidence without another capture/model run.

This does not complete BP04. API cancellation/resume from a pre-checkpoint committed prefix, transport reconnect, profile rollback, load, actual semantic/VLM multi-session acceptance and independent generic/privacy/hardware qualification remain open. The default single-file control and the logo remain unchanged.

BP00 complete model/runtime baseline replay, BP01 remaining authorization boundaries, and BP02 independent labels/paired metric adapters remain open. BP03 now has a working sanitized-media candidate; independent privacy/utility acceptance and detector alternatives remain required. The research control still retains raw evidence, and the candidate must not be described as generally privacy-qualified.

Next implementation work should extend durable canonical evidence/projection and session/gap/recovery contracts (BP04) while collecting independent BP02 acceptance coverage and evaluating BP03 detector alternatives. Strict-profile restart/query/cache admission must continue to reject unqualified or changed historical evidence. See the BP03 document for concrete remaining bounds and replay commands.

Subsequent BP04–BP12 integration, algorithm alternatives, held-out quality comparisons, reward settlement, browser acceptance, load/recovery/rollback and Linux/NVIDIA qualification remain required. Quantization recall, real compressed-domain extraction, genuine crowd/anomaly/intent models, ClickHouse/H3 interoperability and claimed performance gains are not established by this increment.

Each following increment must update this evidence record and the component decision register without overwriting previous outputs. Keep the goal active until the full requirement-by-requirement acceptance audit passes.

### 23 October 2026: committed-prefix recovery and search consistency

Validated [pre-checkpoint cancellation and retained-prefix recovery](DEPIN_BP04_COMMITTED_PREFIX_RECOVERY.md) with actual privacy/YOLO inference. Recovery indexed one committed 3,920 ms session, preserved its hash, and made zero new source requests. Typed capture extent propagates to canonical events, statistics and interpretation context; legacy extent stays unknown. Browser playback, explicit partial-capture disclosure and four responsive widths passed. Rust regressions passed 77 tests, 2 ignored.

An actual delayed-response browser replay exposed stale source-A evidence under source-B controls. The shared normalized request snapshot now rejects changed-input responses, and fresh scoped retrieval passes. Evidence-card contrast was also verified at 6.41:1 for caption/metadata and 11.30:1 for headings. This closes the previously listed pre-checkpoint prefix gate, not the broader BP04 acceptance gates.

### 23 October 2026: explicit live reconnect candidate

Implemented [bounded transport reconnect](DEPIN_BP04_TRANSPORT_RECONNECT.md) through the existing sanitized multi-session worker and runner configuration. Defaults preserve finite-source EOF. Handled disconnects commit only emitted sanitized frames and reopen into separate source clocks/identities; exhausted retries leave a failed, recoverable prefix. Authorization, malformed-video and model failures do not retry. Deadline admission, bounded read/backoff, retry count and lineage are explicit.

Five owned HTTP/codec/deadline checks and five existing capture regressions pass. Actual privacy/YOLO API replays pass both EOF and abrupt body interruption, source-scoped retrieval/graphs/statistics, media, restart, checkpoint recovery without another source request, and revocation. The abrupt test found FFmpeg EIO handling and independently verified that the decoder can withhold its last buffered frame; exact emitted-frame preservation now passes. Failed runs remain archived. RTSP-specific qualification, profile rollback, load and broader quality/security gates remain open.

### 23 October 2026: complete sanitizer producer provenance

Fixed a [producer-recipe loss at the Rust commit boundary](DEPIN_BP04_PRODUCER_PROVENANCE.md): model/code hashes, thresholds, runtime and unknown manifest fields now survive canonical/checkpoint roundtrips and participate in source equality. Metadata fingerprints bind the complete source descriptor. Indexing and tracker enrichment share source admission before model access; the verified source digest is reused.

Rust passed 80 tests, 2 ignored. Actual pinned privacy-model API replay rejects seven producer mutations across query, statistics, projection/visual preparation, tracker enrichment and all media routes. Exact restore, restart with inference unavailable, checkpoint-only recovery with zero source requests, and revocation pass. The workspace shows the actual saved recipe; keyboard disclosure and four browser widths pass. Historical strict records missing canonical producer provenance stay restricted instead of being relabeled. Whole-job execution recipe pinning, atomic activation/migration and old-binary rollback are still open; this increment does not complete the profile gate.

### 23 October 2026: recording execution recipes and saved-policy compatibility

Implemented [versioned recording recipe admission](DEPIN_BP04_EXECUTION_PROFILES.md), with compiled code/build, Python tools/runtime, actual model bytes and recording settings pinned before acknowledgement. Strict execution checks before start, checkpoint and final publication prevent producer drift. Verified complete checkpoints can republish without active inference; enriched tracker graphs are now saved in those checkpoints and reused exactly. Metadata binds the recipe fingerprint.

Fixed a rollback-policy gap: saved sanitized evidence keeps its admission checks when the active deployment is research-raw. The fault replay covers configuration/private-model/script drift, restored retained-only execution, read-only publication with inference unavailable, research-mode tamper denial and revocation. The real privacy/YOLO replay covers two source sessions and exact saved-graph recovery with all inference executables/model files unavailable. Rust passes 83 tests, 2 ignored. An initial detector-runtime probe failed on the installed Python 3.9; the streaming hash fix and successful actual reruns are retained separately.

The initial recording recipe is now pinned; standalone derived stages, sealed runtime artifacts, external VLM/remote index identity, atomic activation/migration and a genuine old-binary rollback drill remain open. This is not completion of BP04 or broader generic/privacy/security/hardware acceptance.

### 23 October 2026: independent derived tracker recipes and model-cache identity

Implemented [separate tracker enrichment recipes](DEPIN_BP04_DERIVED_PROFILES.md), preserving the original recording recipe. Before/after inference and pre-publication checks bind actual code/runtime/model bytes to retained evidence, intervals and source/session scope. Multi-session rebuilds require one detector identity, and a rejected rebuild leaves prior graphs intact. Derived profiles participate in metadata/checkpoint validation.

Resident detector generations now reuse only a matching declared identity. Real privacy/YOLO replay verifies warm reuse, exactly one new ready generation after a worker-script recipe change, no fallback in the cache checks, no recapture, and rejection of an in-flight isolated-runner change without graph replacement. Rust passes 84 tests, 2 ignored. Atomic profile/index activation, migration, external/sealed runtime qualification, old-binary rollback and broader acceptance remain open.

### 23 October 2026: retrieval producer identity and Rust profile binding

Implemented the [worker-profile prerequisite for activation](DEPIN_BP04_RETRIEVAL_PROFILES.md). Actual embedding/reranker files, local implementation, decoder and numerical runtime now bind cache identity; sealed-file/runtime checks precede cache use and artifact publication. `/profile`, expected-profile headers and bound receipts make the selected producer explicit. Rust forwards and verifies an optional deployment pin across search, interpretation, visual controls and projection replay.

Rust passes 85 tests, 2 ignored; retrieval/index/profile suites pass 19 checks. Actual cached-Qwen CPU FP32 preparation and repeated retrieval pass. The Rust sanitized-recording vertical slice retrieves through the matching pin; a mismatched pin produces explicit lexical fallback rather than substituted semantic output. The final actual run is `bp04-real-retrieval-pin-final/`. Reranker inference and generic quality are not qualified by this smoke test.

Atomic activation is still open: a durable target/index registry must cover foreground requests, background receipts, task controls and every prior revocation target together. No partial URL-only switch was enabled. The broader release/quality/authentication/hardware gates remain active.

### 23 October 2026: durable retrieval activation and compatible-generation rollback

Implemented the [local retrieval deployment registry](DEPIN_BP04_RETRIEVAL_ACTIVATION.md): inactive staging before indexing, source-hash-bound preparation receipts, atomic revision-checked activation, immutable foreground selection, generation-bound task actions and historical/staged-target revocation coverage. Failed stages remain registered for cleanup. Incompatible tracked pending preparation is cancelled rather than relabeled. Worker URLs cannot be reused for different profiles/storage instances. Persisted storage UUIDs distinguish ordinary restarts from rebuilt databases.

Final offline Rust checks pass 86 tests, 0 failed, 2 ignored, and the build passes. Retrieval/indexing/profile suites pass 20 checks (3 + 14 + 3). The actual cached-Qwen CPU FP32 Rust/sanitized-media replay passes in 71.3 seconds in `bp04-registry-storage-instance-qwen/`: in-flight A remains A during B activation; B retrieves under B; stale task retry and stale operator revision fail; failed staging stays inactive; rollback and restart retain A; registry corruption fails closed; wrong storage cannot acknowledge old cleanup; restored historical storage and active storage both acknowledge revocation. Recovery/rollback add no source requests.

This closes the local activation-pointer integration gap identified above, not BP04 or the release gate. It is current-binary generation rollback, not older-binary/schema migration. Orphan preparation reconciliation (lost acknowledgements/manual intent overwrite), authenticated service boundaries, native/external runtime identity, independent generic/privacy quality, actual reranker inference, load and Linux/NVIDIA qualification remain open. HTTP workflows were exercised; no new browser acceptance is claimed. Continue with reconciliation and compatibility gates without treating experimental activation as model-quality promotion.

### 23 October 2026: registered preparation acknowledgement and replacement reconciliation

Fixed the [lost-acknowledgement and explicit-replacement races](DEPIN_BP04_PREPARATION_RECONCILIATION.md). The dispatcher commits generation/worker binding before remote submission. Missing acknowledgements are recovered through the original producer's deterministic task identity before cancellation. Explicit controls and replay now share serialization through acknowledgement; a failed cancellation preserves the prior durable revision and returns HTTP 503. Cancellation receipts bind task/scope/profile, and projection validation uses the selected deployment's pin rather than the startup pin.

Rust passes 86 tests, 0 failed, 2 ignored; the offline build passes. The existing sanitized-media/SQLite projection replay passes unavailable-worker, acknowledgement/retry loss, restart, malformed-scope and source-tamper cases. A dedicated replacement replay verifies no overwrite without cancellation acknowledgement, then successful retirement/replacement. A separate full replay passes with startup pin A while B prepares/retrieves. Actual cached-Qwen CPU FP32 lost-acknowledgement cutover passes in 67.4 seconds (`bp04-reconciliation-qwen/`), including rollback, restart, wrong-storage denial and both cleanup acknowledgements, without recapture. Expected injected failures and the initial HTTP-classification failure remain archived.

Registered-generation races are covered; legacy intents already missing producer binding before this change are not reconstructed or declared reconciled. Historical migration policy, older-binary/schema compatibility, authentication, generic/privacy quality, load and hardware qualification remain open. No inference algorithm changed or unmeasured performance gain was claimed. Continue with legacy migration and release compatibility checks under BP04; keep the full goal active.

### 23 October 2026: legacy task reconciliation and actual older-binary rejection

Implemented [legacy quarantine and offline exact-task reconciliation](DEPIN_BP04_LEGACY_MIGRATION.md). Attempted unbound intents cannot inherit a newly active generation or bypass review through explicit prepare/retry. The local migration command requires released writer ownership, exact manifest hash and registry revision, canonical interval/hash coverage and a read-only lookup matching the saved task/producer. Applying preserves the original manifest and a separate durable verification proof before atomic publication; Rust retains the typed migration proof. Unknown historical task IDs remain unsupported and blocked rather than guessed. Existing configured-worker behavior remains available without an active registry.

Offline Rust passes 86 tests, 0 failed, 2 ignored; build passes. Fourteen existing indexing checks and three profile/HTTP checks pass, including lookup without queue/inference side effects. Fixture integration checks owner exclusion, stale manifest/revision, wrong target, unknown task, dry-run immutability, original/proof archives, canonical-payload preservation, no additional jobs and migrated-proof restart. Existing projection/retry-loss regression passes. Final actual cached-Qwen CPU FP32 migration/cutover/rollback/revocation passes in 68.7 seconds in `bp04-legacy-binding-qwen-final/`, with both cleanup acknowledgements and no source recapture.

Built actual Git revision `9d7e12460ba3dc50447f842af158440d742ec41d` offline in an isolated source/target directory. An owned-copy comparison proves that both binaries serve valid committed media, but the current binary denies a hash-changing append (404) while that old executable serves changed bytes (200). The final reproduction is `bp04-old-binary-compat/admission-final/`, which also checks the originals remain unchanged. The old revision is therefore **unsupported for sanitized-v1 rollback**; retaining current integrity admission and verified compatible-generation rollback is the selected alternative. No real privacy leak was introduced; the fault is an appended test marker on copied sanitized media. Initial status/port-probe setup failures are retained separately and are not counted as compatibility evidence.

This is a qualified rejection of one actual old release, not a passed older-binary release gate. Next implement supported-release admission/launch enforcement and explicit schema negotiation, then validate crash-safe upgrade/downgrade and restore without resurrecting revoked state. Authentication, independent generic/privacy quality, mixed load, platform qualification and BP05–BP12 acceptance remain open. Keep the complete goal active.

### 23 October 2026: native storage compatibility and pinned release launcher

Implemented the [native storage/release admission boundary](DEPIN_BP04_RELEASE_ADMISSION.md). Startup validates the complete manifest set and versioned required-feature contract under the existing owner lease before recovery/API/projections. Recording manifests persist numeric version 1; absent versions remain the supported legacy shape. Future/invalid versions, malformed records, symlinked contracts/manifests and filename/ID mismatch fail closed rather than being ignored. A valid interrupted record cannot be rewritten before another incompatible record is discovered. Read-only capability/storage-check commands avoid ordinary configuration/model initialization.

The local release launcher verifies the approved executable hash, rejects binaries lacking the new reader marker without executing them, requires actual executable preflight and rechecks the hash before exec. The running binary repeats admission under ownership. README now links this launch path and the old-binary restriction. Offline preparation migration also rejects unsupported storage/manifest contracts. This is a trusted local deployment workflow, not OS enforcement against direct old-binary execution or malicious-binary attestation.

Rust passes 87 tests, 0 failed, 2 ignored; offline build passes. Eleven actual release/startup cases pass in `bp04-release-admission/`, including wrong-hash/actual-old-executable rejection, direct and launcher future/corrupt-state denial without canonical writes, read-only empty-root behavior and successful exec launch. Existing preparation/retry/restart regression passes. Actual cached-Qwen sanitized recording, offline migration/proof, generation cutover/rollback, restart, tamper rejection and both target revocation acknowledgements pass in 67.7 seconds in `bp04-release-qwen/`, with no recapture.

Native manifest/global schema admission is now implemented. External worker schema transitions, a supported-release matrix, multi-file upgrade crash recovery, host launch enforcement and backup/restore with revocation continuity remain open. Next qualify recovery/restore without reviving revoked evidence; cold startup alone is insufficient. Authentication, independent generic/privacy acceptance, resource/load/platform qualification and remaining BP05–BP12 work are still required. Keep the full goal active.

### 23 October 2026: canonical snapshots and revocation-preserving restore

Implemented [offline evidence snapshot/restore](DEPIN_BP04_BACKUP_RESTORE.md), using the existing owner/registry leases and hash-pinned native preflight. Snapshots cover canonical memory, contract, lineage and registry, with complete file hashes/sizes and atomic directory publication after fsync. Restore requires a matching lineage and exact authoritative-current state cut; it carries the union of backup/current revocations into a separate destination, clears active semantic selection, blocks stale visual preparation and resets cleanup acknowledgements. External SQLite workers and ephemeral upload/cluster jobs are explicitly outside the snapshot contract.

The first restore replay found recomputed source-path contracts could not clean historical roots. Revocation now saves actual session paths before result removal. A subsequent post-restore revocation case required worker-derived recording scope: workers derive bound sources from their own queue/artifact stores, deny canonical recording aliases by UUID before decoding, and support model-independent `recording-scope-v1` cleanup. Rust now attempts every target and persists independent acknowledgements despite another target's failure. New required reader features prevent a compatible-reader downgrade from silently discarding these contracts. Legacy explicit path-based worker revocation remains supported.

Rust passes 87 tests, 0 failed, 2 ignored; build passes. Fourteen indexing and 11 release-admission regressions pass. Fixture restore tests verify live-owner rejection, descriptor/current-cut/payload/lineage failures, existing-destination protection, SIGKILL before publication (destination absent, current state unchanged), retry, valid playback, lexical retrieval, semantic preparation/activation, restart and original/new post-restore revocation. Actual cached-Qwen CPU FP32 passes the complete workflow in 76.2 seconds in `bp04-backup-restore-qwen-final/`; both revocations receive two target acknowledgements, direct restored-path retrieval is denied, and no recapture occurs. Actual privacy/YOLO multi-session traffic replay passes and verifies all session source paths and unchanged retained-media hashes. The initial unsuitable short fixture and the path-contract/test-shape failures remain archived, not counted as successes.

This establishes the explicitly supplied authoritative-current-cut restore workflow, not arbitrary disaster recovery. Superseded retained roots are not yet fenced, and a shared lineage/hash cannot identify the newest clone or discover omitted later tombstones. Next add root fencing and a recoverable cutover journal; independent revocation witnessing/replication and authenticated evidence access remain required. External worker database restore, release/schema matrix, independent generic/privacy quality, mixed load, hardware qualification and remaining BP05–BP12 work remain open. Keep the complete goal active.

### 23 October 2026: fenced root authority transfer and recoverable cutover

Implemented [source-root fencing and destination activation](DEPIN_BP04_ROOT_FENCING.md). The coordinator durably fences the source before publishing a pending destination, commits the source journal, then writes the bound active destination proof. Native startup and ordinary release preflight reject fenced sources, pending/snapshot roots and activation proofs copied to another canonical path. Registry/migration writers also reject superseded/pending roots. Recovery-only format inspection never starts an API and is not exposed as a launcher bypass. Required reader features prevent a compatible-reader downgrade from ignoring fences.

The same pinned restore command resumes its recorded transaction. Changed cuts/destination are rejected. Pending published roots must match their complete prepared inventory before activation. Completed retries never overwrite newer manifests/revocations; a missing committed root is not recreated from stale data. New snapshot payloads are non-launchable. The 64 MiB journal bound is checked against final-state encoding before fencing, preventing an unreopenable oversized transaction.

Rust passes 88 tests, 0 failed, 2 ignored; offline build passes. All 11 release checks and 17 worker profile/indexing checks pass. Fixture integration passes in 35.9 seconds. Actual cached-Qwen CPU FP32 passes in 77.2 seconds in `bp04-root-fencing-qwen-final/`: SIGKILL before publication, after pending publication and after source commit; direct startup/launcher denial; same-transaction recovery; source registry-write denial; snapshot/copy-root rejection; retarget/capacity/destination protection; semantic rebuild/restart; original and new revocation cleanup; idempotent retry preserving later denial; and refusal to recreate a missing committed destination. Final source/destination transactions match, the journal is committed, activation is active, both target acknowledgements complete and no recapture occurs. The initial macOS canonical-path unit mismatch is retained as a failed test, not counted as successful evidence.

Supported local old-root restart and cutover recovery are now covered. Independent, unrollbackable revocation durability/witnessing, historical-clone authentication, cross-host ownership and lost-newer-root disaster recovery remain open. SIGKILL is not physical power-loss qualification; arbitrary disk/I/O failure coverage remains incomplete. Next implement independent revocation durability and authenticated contribution/evidence access. Generic/privacy acceptance, external release/schema matrix, resource/load/hardware qualification and remaining BP05–BP12 work are still required. Keep the full goal active.
