# General video understanding: implementation and evaluation plan

Date: 17 October 2026. Status: proposed execution plan; the stages below are not implemented merely by being listed here.

Progress, 18 October 2026: the [first Milestone A work package](GENERAL_VIDEO_MILESTONE_A.md) delivers experiment manifests/replay/comparison tooling, a coverage inventory, and an opt-in canonical-vector candidate with real-model restart validation. P0 full acceptance and the remaining stages are still pending; the baseline remains the default.

The [second Milestone A package](GENERAL_VIDEO_PACKAGE_2.md) implements filtered query snapshots and indexed entity construction, with graph/result equivalence, real API/evidence checks and isolated resource measurements. Model-profile promotion remains unchanged.

The [third Milestone A package](GENERAL_VIDEO_PACKAGE_3.md) adds opt-in stage/resource diagnostics and cross-process correlation, validated with pinned-model retrieval, browser interpretation and retained-source ingestion. Detailed tracing remains disabled by default; independent corpus acceptance, complete load measurements and durable background indexing/admission are still pending.

Update: [Milestone B, package 4](GENERAL_VIDEO_PACKAGE_4.md) implements durable visual-preparation tasks, bounded single-worker admission and workspace progress/cancel/resume, with exact pinned-model parity and worker/Rust/browser recovery checks. Cross-process resource budgets, sustained load, automatic submission and general acceptance remain pending before the later decode/research stages.

Update, 19 October 2026: [package 5](GENERAL_VIDEO_PACKAGE_5.md) adds opt-in cross-process residency ownership, unload-before-handoff, dedicated native-v1 external-model recovery and retained-file mixed-load validation. This bounds participating resident owners, not total bytes or uncoordinated applications. Keep it off by default: generic acceptance, live gap/reconnect coverage, actual external-server runtime qualification and production capacity remain open. Next engineering package is P2 grouped decoding, with exact RGB/PTS comparison before promotion; automatic post-recording submission remains deferred.

## 1. Objective and scope

Build a general video evidence and understanding system that improves usefulness, latency, throughput and resource use without sacrificing accuracy or numerical precision to save compute. Highway footage is a high-activity stress case, not the product's domain or the source of universal assumptions.

Support uploaded videos and retained intervals from live sources; fixed and moving cameras; people, vehicles, animals, objects, text and interactions; quiet scenes and rare events as well as crowded activity. Questions include what/where/when, counts, before/after, state changes, interactions and histories. Optional audio should be treated as a separately evaluated modality, with explicit availability and source timestamps.

Integrate each candidate behind a reversible experiment profile, evaluate it through the actual application, diagnose failures, and promote only demonstrated improvements. If a candidate remains inferior or inconclusive after a bounded diagnosis, retain the validated baseline or evaluate a named alternative. A research paper's benchmark result is not a deployment acceptance result.

## 2. Current baseline and boundaries

Verified from the current repository:

- Retained encoded evidence, sparse event segmentation, source-aligned retrieval, persisted manifests, Qwen3-VL embeddings/reranking and bounded work caches already exist.
- The detector already uses a persistent batched worker with per-camera tracker state. File processing applies backpressure; direct live detection can replace a pending frame under load.
- Interpretation already uses ordered frames, source-detail target crops, visibility/static checks and a separate visual-state gate. Cached generations still pass the safeguards.
- Experiment 3 stores source-scoped tracker histories and evidence links. Tracker continuity is not verified physical identity; this is not a full FOLIO implementation.
- The latest retained development replay showed identical outputs and 10.892s versus 2.134s with work caches disabled/enabled on repeat. Persistent derived results were already present. This does not measure first-ever indexing, general action accuracy or sustained service capacity.
- Clearer crops did not establish reliable action interpretation. Existing human-reviewed controls and development footage must remain preserved and labeled by their original purpose.

Keep Kafka/database integration optional and preserve existing external ownership boundaries. Preserve original recordings, completed annotations, old model outputs and index versions. Do not silently reinterpret old labels when crop dimensions, target scope or evidence selection changes.

This plan governs the next work after Experiments 1–3. The older [retrieval-first roadmap](RETRIEVAL_FIRST_VIDEO_MEMORY_PLAN.md) remains architectural background; its historical baseline describes capabilities that have since been implemented.

## 3. Target workflow and extension contracts

```text
source -> retained encoded evidence + timestamps/gaps
       -> bounded observer / detector -> event and tracker evidence
       -> resumable background indexing -> versioned searchable representations

question -> scope filters -> retrieval -> temporal evidence selection
         -> required specialists -> interpretation + claim verification
         -> cited answer / explicit uncertainty / incomplete-coverage status
```

Retain source evidence independently of activity or query focus. A quiet interval remains searchable; an observer or compressed memory is not an exhaustive record of reality. Derived caches are evictable, while source retention follows an explicit separate policy.

Use a small experiment registry and existing request/response structures, extending them only where necessary. Each profile records:

- Profile ID, parent baseline, hypothesis and supported task families.
- Code revision, model and weight fingerprints, prompt/processor versions, precision, sampling/resize policy, index version and hardware/runtime.
- Retrieval, selection and specialist settings; limits on memory, queue length, decode sessions and concurrency.
- Supported capabilities and explicit behavior for missing audio, absent reference geometry, unsupported classes or missing model weights.

An evidence item must retain source hash, camera/recording identity, requested interval, actual decoded timestamps, frame/crop dimensions, target association and transformations. A model hypothesis additionally records its producing profile and citations. Keep algorithm observations, model hypotheses and human-reviewed labels distinct.

No generic question should require road calibration or a vehicle tracker. Geometry, segmentation, OCR, audio and motion features are optional capabilities selected by task and available evidence. A routing failure must produce an explicit limitation, not an invented answer.

## 4. P0 — Freeze baselines and build the general evaluation gate

**Dependencies:** none. Required before promoting subsequent stages.

### Implementation

1. Capture the current revision/configuration and immutable manifests for source data, labels, embeddings and generated outputs. Pin resolved weights rather than mutable model names.
2. Extend existing evaluators rather than replacing them: `evaluate_retrieval_corpus.py`, `evaluate_temporal_corpus.py`, `evaluate_reviewed_states.py`, `benchmark_memory_workflow.py` and the browser workflow checks.
3. Add an experiment runner that accepts a profile, corpus split and isolated output directory. Save raw inputs/responses, timings, resource samples, failures, comparison results and a decision record. Never overwrite an earlier run.
4. Instrument queue wait, capture/ingestion, decode, preprocessing, embedding, search, reranking, generation, verification and serialization separately. Record actual cache state and counts. Correlate Rust, Python and model requests by run/query ID.
5. Separate configurations for Apple Silicon and any Linux/NVIDIA host. An unavailable backend is recorded as untested; emulation or compilation cannot satisfy its runtime gate.

### General corpus

| Test family | Representative evidence and questions |
| --- | --- |
| High activity | Junctions, busy corridors, crowds, many moving objects; counts and tracking under contention |
| Quiet/rare events | Empty rooms, a small object moved once, a brief appearance during long inactivity |
| Object interactions | Placing/removing, picking up/putting down, handoffs, contact and separation |
| State changes | Opening/closing doors, drawers and containers; lights or screens changing state |
| People and animals | Entering/leaving a named region, sitting/standing, similar-looking targets, occlusion and re-entry |
| Text and fine detail | Signs, labels, visible screen text, changing numbers; insufficient-resolution negatives |
| Long history | Before/after questions, repeated similar events, target histories and explicit identity ambiguity |
| Difficult capture | Night, blur, compression, camera motion, zoom, variable FPS, dropped intervals and reconnects |

Use a bootstrap target of at least 30 clips per family from at least three independent sources per family, with multiple question types and both positive and negative/ambiguous examples. Families may overlap but metrics must disclose overlap. This is an initial coverage target, not sufficient statistical evidence for every claim; increase sample sizes based on observed uncertainty and failure frequency.

Split by original source/session/site, not by neighboring clips or questions. Keep related recordings together. Allocate development, validation and a locked acceptance set; where feasible reserve different sites for acceptance. Public benchmarks supplement project footage but do not substitute for independently reviewed examples relevant to the product. The existing highway recordings and reviewed cases are development/regression material.

Annotate the actual target scope, identity certainty, observable states, event interval, required references, relevant retrieval intervals and unanswerable conditions. Reviewers should not see model answers when producing labels. Resolve disagreements or retain ambiguity explicitly. Exclude unresolved labels from positive accuracy scoring while reporting their count and testing abstention behavior. Never send expected labels to inference.

### Metrics and acceptance policy

| Change class | Required quality gate | Required efficiency gate |
| --- | --- | --- |
| Exact-preserving engineering | Exact evidence pixels/PTS, vectors/scores, ordering, graph IDs and claim/safeguard outcomes for affected paths; normalize only explicitly excluded timing/run metadata | Demonstrated reduction in the targeted cost; no unexplained resource or tail-latency regression |
| Numerical/backend changes | Treat batching, kernels and dtype changes as experiments if exact parity fails; do not call them lossless | Record actual precision, peak memory and cold/warm latency; reject unapproved precision reductions |
| Retrieval/selection alternatives | Recall@K, nDCG@K, temporal evidence coverage, negative-query behavior and domain-specific failures | Compare equal evidence/token budgets and actual end-to-end cost, including ranking/selection overhead |
| Action/identity alternatives | Action precision/recall, temporal localization, false claims, identity switches and answer coverage; compare precision at matched coverage and coverage at matched precision | Latency and resource budget measured with all required verification enabled |
| Live workflows | Requested/processed/replaced samples, recording gaps, event recall, time-to-detection and time-to-complete catch-up | Sustained throughput, queue growth, p50/p95/p99 latency and peak CPU/GPU memory |

Pre-register primary metrics, budgets and minimum meaningful improvement before inspecting candidate results. As initial efficiency targets, use a 10% reduction in the targeted median/p95 latency or a 20% reduction in measured peak memory, subject to the quality gates; these are project targets, not research claims. A correctness fix can be accepted without a speedup, with its resource impact disclosed.

Use paired comparisons and confidence intervals resampled by independent source/session, not by correlated frames. For quality-changing candidates, require no measured regression on required task families and sufficient evidence that the primary quality difference is non-negative; use a zero degradation margin by default. If uncertainty is too broad, mark the result inconclusive and expand independent evaluation rather than accepting a tradeoff. A finite benchmark cannot prove universal no-loss behavior.

Evaluate cold runtime/model loading, empty derived indexes, warm model/cold work caches, new queries, exact repeats and restart recovery separately. Keep verification enabled in timed runs. Measure end-to-end request time as well as model time; report CPU RSS and accelerator allocation distinctly to avoid double-counting unified memory. Use enough repetitions to estimate variation; begin with ten warm repeats and five isolated cold runs, increasing when noisy.

**Exit:** reproducible baseline report, reviewed corpus manifest, split enforcement, stage timings and machine-readable pass/fail/inconclusive outcomes. Missing acceptance data blocks promotion, not independent engineering work.

## 5. P1 — Improve orchestration and metadata access

**Dependencies:** P0 baseline contracts. Implement as separate, reviewable increments.

| Work item | Main implementation locations | Test and fallback |
| --- | --- | --- |
| Canonical vector normalization | `tools/semantic_memory_server.py`, `check_work_caches.py`, `check_retrieval.py` | Resolve fresh-versus-cache renormalization differences; validate dimensions/finiteness/norm without silently changing valid stored values. Version a changed numerical contract and preserve the previous profile. Test fresh/repeat/restart and near-tied rankings. |
| Filtered query snapshots | `src/store.rs`, `src/retrieval.rs`, `src/long_memory.rs` | Filter before cloning heavy results; use small listing summaries. Preserve indexed-count diagnostics, all scope filters and history expansion beyond the retrieval shortlist. Compare serialized semantic output and peak memory at increasing corpus sizes. |
| Entity lookup indexes | `src/long_memory.rs`, `src/event_engine.rs` | Reuse maps for track/time lookups and indexed intervals. Preserve duplicate handling, overlapping interval selection and half-open boundaries. Exact graph equivalence is mandatory. |
| Resumable background embeddings | `tools/semantic_memory_server.py`, Rust memory job/API integration | Durable task states: queued/running/completed/failed/cancelled, with stable artifact keys and bounded retries. Recheck source/version before committing. Do not label a partially built index complete. Restart, cancel, duplicate submissions and changed-source tests. |
| Admission and resource scheduling | Python request handler, `src/config.rs`, `src/api.rs`, existing media gates | Bound active/queued tasks and give interactive work priority with starvation protection. Coordinate detector/retrieval/VLM memory budgets without deadlocks. Queue or return explicit retry status instead of silently lowering semantic coverage. Test simultaneous query/index/interpretation requests. |
| Low-cost resource cleanup | Python handler and model lifecycle | Measure unconditional collection, model reload and HTTP connection overhead. Reuse clients or condition cleanup only where profiling demonstrates benefit; retain necessary weight-release behavior. |

Persist embeddings before exposing index completion. Background indexing changes when compute happens, not necessarily total compute. Batch database writes in bounded transactions while retaining durable progress and recovery. Do not share one SQLite connection across concurrent work without an explicit ownership strategy.

Expose indexing progress, query queue state and actual fallback mode through the existing workspace. Keep old clients compatible. Browser-test successful indexing/search, partial failure, cancellation, restart, evidence playback and responsive rendering after rebuilding embedded Rust assets.

**Exit:** exact-output regression pass, request/restart/failure tests, measured target improvement and working application flow. Roll back each work item independently if its gate fails.

## 6. P2 — Decode once where possible; preserve evidence exactly

**Dependencies:** P0; P1 scheduling before concurrent decode work.

1. Build a per-request decode plan grouping timestamps by source and bounded interval. Share duplicate requests across retrieval, reranking and interpretation when transformation contracts match.
2. Start with existing FFmpeg behavior in grouped requests. Compare a persistent decoder implementation and TorchCodec only if needed. Bound open sessions, retained frames and idle lifetimes.
3. Derive context/detail branches from the same native frame where equivalent. Never upscale source evidence or assume a different resize implementation yields the same pixels.
4. Preserve seek-target versus actual PTS, `[start,end)` rules, exact tracker alignment, empty-seek behavior, repeated-frame disclosure and no interpolation across missing evidence.
5. Probe each source codec once per ingest operation; schedule browser derivatives outside the observation loop where possible. Retained original footage remains the inference source. Generate playback derivatives lazily only if the product still offers a reliable loading/error path.

Test CFR/VFR, MJPEG/H.264, sparse PTS, short files, end-of-file seeks, repeated targets, different resolutions, corrupt frames, reconnect boundaries and source mutation. Compare raw RGB/PTS before interpreting model results. Benchmark decode/process startup/PNG encoding separately. If a decoder differs, either fix the cause or retain FFmpeg for affected inputs; a different pixel path requires a new experimental profile and quality evaluation.

**Exit:** source-level parity and end-to-end regressions with lower cold decode cost. A cache-hit benchmark alone is insufficient.

## 7. P3 — Improve retrieval evidence and query-time batching

**Dependencies:** P0 and P1; P2 improves efficiency but is not required to compare selection quality.

### E4: Evidence selection inspired by QSVideo

- Keep the current Qwen retrieval/reranker as control. Add query-relevant selection that balances object/action/reference relevance, diversity and temporal coverage.
- Preserve ordered before/transition/after evidence, nearby context and explicit target scope. Do not drop low-motion frames solely because they appear redundant; they may establish the initial or final state.
- Compare the current uniform/anchor sampler, a simple temporal coverage baseline, and a QSVideo-derived selector. Using QSVideo's trained ranker is a separate candidate from adopting its selection policy.
- Evaluate equal-frame/token budgets first, then higher-budget accuracy ceilings. Record extra selector calls and memory. Never feed the requested action into verification as an established fact.
- Include brief actions, repeated similar events, motionless negatives and ambiguous identity. If retrieval already contains the correct interval, measure whether selection actually recovers its decisive frames.

### E5: Representation and runtime alternatives

- Compare Qwen 2B versus 8B only after identifying retrieval failures; do not change the default on model size alone.
- Evaluate MARS or multi-vector/late-interaction retrieval when single-vector representations confuse similar clips. Build a separate index and report storage/build/query costs.
- Introduce real bounded embedding/reranker microbatches where supported. The pinned reranker loops internally over documents; a multi-document API call alone is not batched inference. Test padding, ordering, precision, OOM recovery and batch-one fallback.
- For larger corpora, measure batched vector reads and exact similarity search before adding ANN. FAISS flat search is exhaustive but float32 arithmetic need not reproduce current Python scores bit-for-bit. ANN, dimensionality reduction and quantization require separate opt-in quality experiments; they are not exact-preserving replacements.

**Exit:** improved evidence recall or ranking under P0 quality gates, or equivalent outputs with demonstrated efficiency gain. If selection cannot outperform uniform temporal coverage after diagnosis, retain the simpler sampler. If MARS/8B gains do not justify their costs, retain Qwen 2B.

## 8. P4 — General state, action and target interpretation

**Dependencies:** P0; compare against the current selector and the accepted P3 selector separately.

### E6: Task-aware specialist evidence

Introduce explicit capability routing for object localization, identity continuity, motion, state change, OCR and optional audio. Start with deterministic supported query categories; use a model router only if separately evaluated. Preserve a generic interpretation route for supported open-ended questions and explicit uncertainty for unsupported ones.

| Task | Evidence to implement and compare | Required negative/control cases |
| --- | --- | --- |
| Enter/leave and region motion | Source-aligned trajectory relative to a named region/boundary; reuse existing line/zone machinery | Camera motion, occlusion, ID switch, first/last observation without actual crossing |
| Approach/recede | Multi-frame trajectory and scene reference; optional camera calibration where meaningful | Zoom, pose changes, moving camera, changing detector box shape |
| Open/close | Localized object and visible frame/gap state over time | Target occlusion, partly open starting state, similar adjacent objects |
| Place/remove or handoff | Object/support/contact relationships before and after; localized interacting objects | Brief occlusion, object swap, no visible contact or reference |
| Read/change text | Query-scoped OCR with original image crops and timestamps | Unreadable text, repeated labels, screen flicker and uncertain characters |
| Audio-supported events | Separately timestamped ASR/sound evidence when an audio track exists | Silent/noisy audio, off-screen sound, audiovisual disagreement |

Evaluate SAM 3.1 on difficult localized clips as a segmentation/tracking specialist, retaining YOLO/ByteTrack as the baseline. Score association, mask/box quality and identity continuity independently from action labels. Do not merge physical identities across recordings from resemblance alone.

### E7: Temporal representation specialists

Compare V-JEPA 2.1 and InternVideo-Next features with the existing ordered-image VLM. These encoders require a compatible head/adaptation for task predictions; they are not drop-in chat models. Train any head only on the training portion, tune on development/validation, and lock before acceptance.

Maintain separate observation, hypothesis and verified-transition records. Keep source citations, static/visibility/identity checks and contradiction handling. A second call to the same VLM is not independent ground truth. If a specialist changes verification semantics, run it in shadow mode and version the verifier rather than silently bypassing existing guards.

**Exit:** action/state quality improvement at matched answer coverage, with no regression on ambiguous/negative cases. A model that abstains on everything does not pass. Promote per supported capability/domain if needed; do not advertise general action understanding from highway success.

## 9. P5 — Entity-focused long-term memory

**Dependencies:** P0; P4 for promoting structured state-change hypotheses into a useful memory layer.

### E8: FOLIO-inspired focused memory

- Extend tracker histories with timestamped state/relation hypotheses, recent visual context, linked visual evidence and query focus. FOLIO is training-free; reproducing its behavior still requires its writer/retrieval protocol, not just tracker links.
- Allocate additional derived detail to focused entities while preserving searchable background evidence and original sources. A later question may concern an object that was never in focus.
- Test direct structured lookup, semantic expansion and evidence recovery independently. Never let a speculative writer output overwrite observations or turn into a verified identity fact.
- Version records and resolve contradictory updates explicitly. Support forgetting/eviction of derived caches without deleting source evidence or corrupting older citations.
- Evaluate long gaps, distractors, repeated events, target switches, delayed questions and multi-turn references. Measure memory growth over duration and source count, write cost, retrieval coverage and answer quality.

**Alternatives:** retain the present deterministic graph if semantic writing increases false claims or cost without benefit. Test simple hierarchical event summaries before a larger architectural change. StreamForest is a later candidate for bounded event memory; its compression must pass recall tests and cannot replace the source archive.

**Exit:** improved long-history answers within the declared memory budget, with source-grounded recovery and no contamination of persistent facts by unverified text.

## 10. P6 — Sustained streaming and specialized runtimes

**Dependencies:** P0/P1; begin gap instrumentation early, promote the complete streaming workflow after the preceding service gates.

1. Distinguish capture continuity, requested sampling, processed sampling and provisional analytics. Count pending-frame replacements and retain their source-time ranges.
2. Where complete analysis is required, keep capture independent of inference and process retained fragments with backpressure. Bound catch-up queues and disk use; disclose lag/incompleteness when capacity or retention is exhausted. No system can guarantee zero loss under unlimited overload.
3. Add catch-up work with stable IDs so replay does not double-count events. Test interruption, reconnects, out-of-order completion and failures during recovery.
4. Run sustained tests at 1, 2 and 4 streams first, then increase only while latency/memory/coverage budgets hold. Include quiet and active streams together. Measure resource plateaus and queue growth, not only short bursts.
5. Evaluate ReKV-style attention-state reuse only if repeated questions on growing streams make model prefill a measured bottleneck. This requires a compatible runtime; the current HTTP model interface does not itself expose reusable internal attention state. Keep it model/version/source scoped and bound retention.
6. Evaluate StreamForest/VST-style streaming reasoning or newer latent-memory methods only after the simpler profiles establish a baseline. Generated/compressed latent memory remains fallible and must retain auditable links to recoverable evidence.
7. On actual NVIDIA hardware, compare the existing worker with DeepStream/NVDEC/TensorRT using the same input policy and supported numerical settings. Preserve source clocks, camera isolation and decode/inference coverage. An export or compile check is not deployment validation.

**Exit:** stable resources and declared completeness under the supported load envelope; explicit overload behavior; no undetected quality degradation. Retain the local worker for platforms where an alternative fails compatibility or quality gates.

## 11. Required integration-test ladder for every stage

1. **Contract tests:** sources, timestamps, filters, cache invalidation, malformed output, identity namespaces, unsupported capabilities and schema migration.
2. **Component comparison:** decode pixel/PTS parity, vector/ranking parity, graph equivalence or task-specific labels as appropriate.
3. **Application integration:** real ingest/index/query/interpret/evidence endpoints; failure, cancellation, restart and mixed-version artifacts.
4. **Browser flow:** recording or upload, indexing progress, query modes, target selection, uncertainty display, evidence playback and mobile layout. Rebuild/restart embedded assets before validation.
5. **Controlled replay:** same retained bytes, isolated candidate indexes/caches, pinned models and balanced run order. No competing heavy workloads during sequential performance comparisons.
6. **Load and failure tests:** concurrent indexing/search/detection, bounded queues, model unavailability, source mutation, partial coverage and resource exhaustion.
7. **Locked acceptance:** new sources and unseen questions, evaluated only after development choices are frozen. Report domain breakdowns and confidence intervals, not only an aggregate score.

Existing runnable checks remain part of the baseline:

```bash
cargo test --offline
cargo fmt --check
.venv-retrieval/bin/python tools/check_retrieval.py
.venv-retrieval/bin/python tools/check_work_caches.py
.venv-retrieval/bin/python tools/check_source_crops.py
.venv-retrieval/bin/python tools/check_track_grounding.py
.venv-retrieval/bin/python tools/check_state_verification.py
.venv-retrieval/bin/python tools/check_track_visibility.py
git diff --check
```

Model/browser evaluation commands require the declared fixtures and running services. Future general-corpus/profile-runner commands must be documented when implemented; do not present planned tooling as executable today.

## 12. Failure diagnosis and keep/replace decisions

Every failed or inconclusive candidate enters this procedure before rejection:

1. **Reproduce:** freeze the failing request, source, profile, environment and raw outputs. Confirm repeated failures; distinguish noisy timing from a deterministic error.
2. **Locate the earliest failing stage:** source retention/clock -> decode -> detection/association -> index -> retrieval -> selected evidence -> state interpretation -> verification -> answer/UI. Assign failure categories and counts by task family.
3. **Use controlled substitutions:** give the selector the human-labeled relevant interval; give the interpreter manually verified evidence; compare correct and switched targets; bypass only the component under diagnosis in an offline test. These oracle runs diagnose ceilings and must never be reported as deployable accuracy.
4. **Check integration before model quality:** tensor shapes, color order, resize/padding, normalization, frame ordering, timestamps, prompts, processor/model compatibility, cache keys, stale artifacts, actual precision and hardware contention.
5. **Run one-factor ablations:** isolate selection from model size, graph expansion from rewriting, and compression from memory organization. Measure where additional compute is spent.
6. **Fix and regress:** implement the smallest supported root-cause correction, add a failure-derived test and rerun affected development/integration gates. Keep historical outputs.
7. **Make a bounded decision:** default to one baseline evaluation and up to two evidence-driven repair cycles per candidate. Additional cycles require a recorded new diagnosis and expected gain; do not continue indefinite prompt tuning. There is no need to ask the user again for routine debugging within authorized work.

Decision states:

- **Promote:** passes integrity/quality/resource gates and meets its predeclared benefit. Promote only the evaluated scope.
- **Keep experimental:** promising but insufficient evidence or incomplete platform coverage. Baseline stays default.
- **Retain baseline:** no meaningful improvement, unexplained regression, unacceptable cost or inconclusive acceptance evidence.
- **Try alternative:** record the diagnosed limitation and the next candidate that addresses it; do not substitute another model without a hypothesis.

Once acceptance results influence a repair, that set becomes development evidence. Obtain a new untouched acceptance set for the final promotion decision. Never tune repeatedly against a nominally held-out set.

Each decision record includes compared profiles, source/label fingerprints, primary and per-domain metrics, efficiency distribution, failures, repairs/ablations, limitations, chosen default and exact rollback instructions.

## 13. Rollout, rollback and execution order

Use experiment profiles rather than global replacement. Run baseline and candidate sequentially offline first; enable resource-bounded shadow evaluation next; expose explicit opt-in only after integration passes. Keep the last validated default available throughout.

Write new indexes and memory schemas to versioned locations. Activate a completed version atomically. Rollback changes the active profile/index pointer and cancels incompatible background work; it must not delete sources, labels or old results. Test rollback while jobs are queued and after a process restart. Display the actual profile, coverage and uncertainty in results.

Recommended implementation sequence:

1. **Milestone A:** P0 manifests/metrics/corpus scaffolding; P1 canonical vectors, filtered snapshots and entity lookups. These can progress while independent labels are collected.
2. **Milestone B:** background indexing, scheduler and queue/progress UI; early live-gap accounting. Validate concurrency and recovery.
3. **Milestone C:** grouped decoding and actual bounded batching, individually compared against the baseline.
4. **Milestone D:** E4 temporal evidence selection, then E6 general state specialists and E7 motion representations on multiple task families.
5. **Milestone E:** E5 alternative retrieval representations and E8 focused memory where observed failures justify them. Avoid combining unvalidated candidates.
6. **Milestone F:** sustained streaming and runtime alternatives once core quality and load gates are established.

The first executable work package is P0 plus the P1 canonical-vector regression: freeze the baseline, define the experiment manifest/comparison contract, inventory existing labeled coverage, and reproduce the fresh/cache numerical discrepancy before changing it. The immediate deliverable is an auditable baseline and one reversible, verified improvement—not simultaneous replacement of the video stack.

## 14. Research inputs and adoption limits

The following primary sources informed the plan. Dates/claims were reviewed in the preceding research pass; none is evidence of superiority on this project's generic corpus.

| Source | Planned use |
| --- | --- |
| [Qwen3-VL Embedding/Reranker, January 2026](https://arxiv.org/abs/2601.04720) | Existing baseline and controlled size/runtime comparisons |
| [QSVideo, July 2026](https://arxiv.org/abs/2607.04559) | Relevance, diversity and temporal evidence coverage |
| [FOLIO, July 2026](https://arxiv.org/abs/2607.13298) | Training-free focused entity memory and linked evidence |
| [MARS, September 2026](https://arxiv.org/abs/2609.02565) | Multi-slot/multi-layer retrieval representations |
| [V-JEPA 2.1, March 2026](https://arxiv.org/abs/2603.14482) | Dense temporal features requiring task adaptation |
| [InternVideo-Next, revised March 2026](https://arxiv.org/abs/2512.01342) | Alternative spatiotemporal features |
| [SAM 3.1 release, March 2026](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md) | Selective multi-object segmentation/tracking; hardware-specific evaluation |
| [ReKV, 2025](https://arxiv.org/abs/2503.00540) | Optional model attention-state reuse for streaming queries |
| [StreamForest, 2025](https://arxiv.org/abs/2509.24871) | Optional bounded hierarchical event memory; compression is not lossless |
| [Video Streaming Thinking, March 2026](https://arxiv.org/abs/2603.12262) | Later streaming-reasoning candidate with additional training/runtime requirements |
| [LatentStream, September 2026](https://arxiv.org/abs/2609.04131) | Watchlist for latent memory; evaluate auditability and evidence recovery before adoption |
| [StreamingEval, March 2026](https://arxiv.org/abs/2603.21493) | Joint evaluation of quality, latency and bounded memory |
| [TorchCodec official introduction](https://pytorch.org/blog/torchcodec/) | Candidate decoding backend, subject to existing pixel/PTS contracts |
| [FAISS index documentation](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes) | Exact versus approximate search distinction; numerical equivalence still needs testing |

