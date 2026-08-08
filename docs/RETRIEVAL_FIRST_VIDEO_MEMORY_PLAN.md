# Retrieval-First Video Memory: Implementation Plan

## 1. Decision and intended outcome

The next architecture should add a retrieval-first event-memory lane beside the existing deterministic analytics lane.

```text
                                  ┌─ policy YOLO/tracking ─ deterministic events ─┐
encoded camera/upload ─ evidence ─┤                                                  ├─ reconciled answer
                                  └─ adaptive observer ─ event memory ─ retrieval ─┘
                                                                         │
                                                                         └─ specialists + VLM verification
```

The existing YOLO26, zone, line, dwell, cluster-association, and grounded-report implementation remains authoritative for numeric and geometric facts. The new lane retains compressed evidence, creates inexpensive temporal representations, retrieves a small candidate set, and applies expensive models only to those candidates.

This design is selected because it balances the three product priorities:

1. **Highest practical accuracy:** no activity gate is allowed to destroy evidence; shortlisted results are verified against the original interval.
2. **Richest information:** event memory can include motion, appearance, OCR, speech, sound, scene, entity, and relationship information instead of only class/box/track tuples.
3. **Lightweight common path:** codec parsing, sparse decoding, small embeddings, and indexes run continuously; YOLO, OCR, segmentation, open-vocabulary detection, and a large video VLM are scheduled selectively unless a policy requires them continuously.

The keyframe/grid workflow should not become the main reasoning representation. Contact sheets remain useful for people, but temporal clips and token-level representations should drive machine retrieval.

## 2. Scope and constraints

### In scope

- Uploaded videos and bounded HTTP(S)/RTSP streams.
- Single-camera and same-cluster multi-camera inputs.
- Retention of source-aligned evidence intervals.
- Adaptive event segmentation and temporal embeddings.
- Semantic, temporal, lexical, entity, and composed retrieval.
- Query-triggered YOLO26, OCR, re-identification, grounding, segmentation, audio, and VLM verification.
- Evidence-backed camera and cluster answers.
- A local UI and local storage implementation that do not depend on Kafka or MongoDB.
- Stable integration interfaces for the backend-owned Kafka and database adapters.

### Out of scope until backend contracts arrive

- MongoDB schema ownership or production persistence semantics.
- Final Kafka topic names, schemas, authentication, partitions, or retention.
- User/tenant identity, authorization, and production evidence-access policy.
- Production-wide camera assignment and worker orchestration.

### Non-negotiable invariants

- The activity gate schedules compute; it does not declare that an interval is irrelevant.
- Every answerable claim points to a camera, source interval, and evidence artifact.
- Counts, zones, line crossings, geometry, and track durations come from deterministic code.
- A VLM may describe or reconcile evidence but may not silently override deterministic facts.
- Cross-camera identity is probabilistic. Ambiguous candidates remain ambiguous.
- Every derived artifact records its model, preprocessing, configuration, and schema version.
- Model/index upgrades do not mutate old results in place; they create a new version.
- The service operates with a no-op Kafka sink and a disposable local persistence adapter.

## 3. Current repository baseline

The current application already supplies useful foundations:

| Existing capability | Keep | Change or extend |
| --- | --- | --- |
| Axum API and embedded test UI | Yes | Add indexing, search, evidence, and benchmark views |
| Uploaded, HTTP(S), and RTSP inputs | Yes | Add source-clock/GOP indexing and resumable evidence capture |
| Python YOLO26 runner | Yes for development | Convert from the only analysis path into a selectable policy/query specialist; later use a persistent worker |
| Normalized observations | Yes | Add evidence, provenance, quality, and feature references |
| Zone/line/dwell event engine | Yes | Link every event to evidence and specialist versions |
| Representative-frame VLM description | Yes as a fallback/UI summary | Replace one-frame-only reasoning with retrieved microclips where supported |
| LM Studio model selection | Yes | Add capability-based routing and avoid per-request model churn |
| Multi-camera topology and association | Yes | Replace color/texture prototypes with calibrated re-identification features and event/entity memory |
| In-memory jobs | Yes for deterministic tests | Introduce a local disposable memory store behind traits |
| Optional Kafka sink | Yes | Keep provisional until backend schemas are delivered |

Important current limitations that the plan directly addresses:

- The YOLO runner decodes frames and executes the detector at a configured rate for every monitored interval.
- Only one representative JPEG is retained for general view description.
- Detector observations are the only reusable visual representation.
- Current appearance descriptors are development-grade color/texture features.
- Jobs and results disappear on restart.
- Retrieval, raw clip evidence, OCR/ASR, late reranking, and query-triggered specialists are absent.

## 4. Target runtime architecture

### 4.1 Ingestion and evidence spine

For every source, create a common source-time mapping and an encoded evidence record before model inference.

```text
source
  → protocol reader/demux
  → packet clock normalization
  → keyframe/GOP index
  → bounded encoded evidence store
  → activity features
  → adaptive decode scheduler
```

The local implementation stores source fragments and indexes under `VISN_DATA_DIR`. The production interface exposes the same logical operations to the future backend/object-store implementation.

Required behaviors:

- Preserve PTS, DTS, time base, wall-clock receipt time, keyframe flag, codec, resolution, and reconnect generation.
- Split evidence only on decodable boundaries where possible.
- Record requested interval separately from actual GOP-expanded interval.
- Generate content hashes after fragment finalization.
- Apply duration/size quotas and atomic finalization.
- Never log source credentials.
- Mark gaps and clock discontinuities explicitly.

### 4.2 Adaptive continuous observer

The continuous observer has four compute tiers:

| Tier | Trigger | Continuous work | Optional work |
| --- | --- | --- | --- |
| 0: idle | stable codec/activity baseline | packet/GOP metadata and periodic heartbeat sample | none |
| 1: low novelty | weak/local change | one representative decode and global embedding | low-rate audio VAD |
| 2: event candidate | embedding/motion/audio/text transition | 1–4 second microclip, temporal/region embeddings | OCR/ASR on changed spans |
| 3: critical | configured policy or strong novelty | policy detector/tracker and immediate evidence pin | configured real-time specialists |

The scheduler must include safety valves:

- A maximum time between decoded heartbeat samples.
- Random audit sampling of intervals classified as idle.
- Per-camera adaptive baselines rather than global motion thresholds.
- Higher sampling around reconnects, scene changes, and poor-quality periods.
- Hysteresis, minimum event length, maximum event length, and pre/post roll.
- A fail-open policy for critical cameras: uncertainty increases compute.

Initial codec/activity features should be deliberately simple and measurable:

- Keyframe/GOP cadence and encoded packet sizes.
- I-frame luminance histogram difference.
- Low-resolution luma difference for sparse decoded samples.
- Residual/motion-vector summaries when the codec/runtime exposes reliable side data.
- Audio RMS and voice activity when an audio stream exists.

Motion-vector extraction is a benchmarked optimization, not a prerequisite for the first vertical slice. Some sources, codecs, and hardware paths will not expose comparable vectors.

### 4.3 Adaptive event segmentation

Create candidate boundaries from normalized per-camera signals:

```text
boundary_score =
    w_embedding × embedding_change
  + w_motion    × codec_or_luma_change
  + w_region    × local_region_novelty
  + w_audio     × audio_transition
  + w_text      × ocr_or_asr_change
```

Implementation requirements:

- Maintain robust rolling statistics per camera, time-of-day profile, and reconnect generation.
- Convert each signal to a calibrated percentile or z-score before fusion.
- Use hysteresis to avoid fragmented events.
- Merge short adjacent events when semantics remain stable.
- Force a boundary at a configured maximum duration.
- Retain pre-roll and post-roll evidence around every event.
- Record each contributing boundary feature for debugging and evaluation.

The first version should use a weighted rules engine. A learned boundary model is considered only after labeled errors show that a rules-based fusion has reached its ceiling.

### 4.4 Hierarchical memory

The memory hierarchy is logical; it does not require one database product.

#### L0: raw evidence

- Encoded fragments/GOPs and optional audio.
- Exact source and normalized-clock intervals.
- Integrity hash, codec metadata, reconnect generation, and quality/gap flags.

#### L1: feature artifacts

- Global frame/clip embeddings.
- Temporal tokens and selected region tokens.
- Motion/activity summaries.
- OCR tokens and boxes.
- ASR spans and timestamps.
- Audio embeddings/classifications.
- Thumbnails and frequently used crops.

#### L2: event records

- Event interval and source evidence references.
- Boundary rationale and novelty/quality confidence.
- Global embedding plus feature artifact references.
- Optional short factual caption, always marked as model-generated.
- Deterministic observations/events connected to the interval.

#### L3: entity records

- Local tracklets and global identity candidates.
- Detector class, appearance/re-ID features, attributes, trajectory, and linked events.
- Identity confidence, topology evidence, and contradiction flags.

#### L4: relationship graph

- Temporal: `BEFORE`, `AFTER`, `OVERLAPS`.
- Spatial/entity: `SAME_ENTITY_CANDIDATE`, `ENTERS`, `LEAVES`, `CARRIES`, `INTERACTS_WITH`.
- State: `STATE_CHANGED_FROM`.
- Reasoning-only: `CAUSES_CANDIDATE`; never present it as an observed fact.

### 4.5 Query and verification path

```text
natural-language or composed query
  → planner: intent + constraints + required specialists
  → metadata/time/camera filter
  → parallel coarse recall
       ├─ vector event/region search
       ├─ lexical OCR/ASR/caption search
       ├─ entity/appearance search
       └─ temporal/relationship traversal
  → rank fusion
  → token-level or cross-encoder reranking
  → temporal expansion around top candidates
  → query-specific specialists
  → source-video/VLM verification
  → deterministic/VLM reconciliation
  → answer with evidence and uncertainty
```

The query planner produces typed operations; it must not directly execute arbitrary tools. Initial query types are:

- `semantic_event`
- `deterministic_count`
- `temporal_context`
- `entity_continuity`
- `text_or_speech`
- `composed_reference_edit`
- `state_change`
- `open_ended_summary`

Unrecognized questions fall back to semantic retrieval plus conservative visual verification.

## 5. Model and specialist strategy

### 5.1 Separate model roles

A generative VLM is not automatically a suitable embedding model or reranker. Use separate versioned interfaces:

```rust
trait ClipEncoder
trait TextEncoder
trait RegionEncoder
trait Reranker
trait Detector
trait ReIdentifier
trait OcrEngine
trait SpeechRecognizer
trait Segmenter
trait VisualVerifier
```

Each result includes `model_id`, `artifact_hash`, `preprocess_version`, `device`, `precision`, and `created_at_ms`.

### 5.2 Candidate models and selection rule

The report names research candidates, but no model becomes a production dependency until it passes the project corpus and license review.

| Role | First benchmark candidates | Deployment behavior |
| --- | --- | --- |
| Global/temporal clip embedding | Qwen3-VL-Embedding 2B; compact InternVideo-family model; a smaller image baseline | Persistent batched worker; fixed ingest model per index version |
| Fine reranking | Qwen3-VL-Reranker; Video-ColBERT-style late interaction | Only top 50–100 coarse candidates |
| Object detection/tracking | Existing YOLO26 path | Always-on only for policy cameras/rules; otherwise candidate intervals |
| Re-identification | calibrated OSNet/FastReID-class embedding | Person/vehicle track crops only |
| OCR | lightweight OCR first, stronger OCR on demand | Changed regions and query candidates |
| ASR | VAD-gated small/distilled Whisper-class model | Speech spans only |
| Open-vocabulary grounding | Grounding-class detector | Query candidates only |
| Segmentation | SAM-family model | Top candidates only |
| Visual verification | capability-tested local VLM/video VLM | Top few intervals; abstain on unsupported media |

The currently available LM Studio models remain selectable for descriptions and verification. Add a capability registry so the router knows whether each loaded model supports image input, multiple ordered images, native video, JSON schema, and context length. A model that supports only text must not receive a visual request.

### 5.3 Resource-aware routing

- Keep the ingest encoder loaded and stable; do not unload it for every user-selected VLM.
- Queue model load/unload operations through one resource manager.
- Prefer one resident lightweight encoder and one resident verifier when VRAM permits.
- Batch event embeddings across cameras with a bounded maximum wait.
- Apply the larger reranker only after coarse recall.
- Limit verification to the smallest interval and spatial crop that can answer the query.
- Cache all specialist outputs by evidence hash + model version + parameters.
- Fall back from native video to ordered frames with timestamps when a VLM lacks video input.
- Fall back from VLM verification to deterministic/specialist results with an explicit reason.

## 6. Core contracts

These types should be added as versioned Rust domain objects and mirrored in API JSON. They must remain storage-independent.

### 6.1 Source and evidence

```json
{
  "schema_version": 1,
  "evidence_id": "uuid",
  "camera_id": "camera-a",
  "cluster_id": "cluster-1",
  "source_generation": 3,
  "source_start_ms": 12500,
  "source_end_ms": 17100,
  "wall_start_ms": 1785900000000,
  "requested_start_ms": 13000,
  "requested_end_ms": 16500,
  "uri": "local-artifact-reference",
  "sha256": "...",
  "codec": "h264",
  "keyframe_aligned": true,
  "has_audio": false,
  "quality_flags": []
}
```

### 6.2 Event record

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "camera_id": "camera-a",
  "cluster_id": "cluster-1",
  "start_ms": 13000,
  "end_ms": 16500,
  "evidence_ids": ["uuid"],
  "global_embedding_ref": "artifact-id",
  "temporal_token_ref": "artifact-id",
  "region_feature_refs": ["artifact-id"],
  "ocr_spans": [],
  "asr_spans": [],
  "deterministic_event_ids": [],
  "boundary_features": {},
  "novelty": 0.82,
  "quality": 0.91,
  "provenance": {}
}
```

### 6.3 Retrieval result

```json
{
  "event_id": "uuid",
  "camera_id": "camera-a",
  "start_ms": 13000,
  "end_ms": 16500,
  "coarse_scores": {
    "semantic": 0.81,
    "lexical": null,
    "entity": 0.74
  },
  "rerank_score": 0.88,
  "verification": {
    "status": "supported",
    "confidence": 0.84,
    "observed_claims": [],
    "inferred_claims": [],
    "contradictions": []
  },
  "evidence_ids": ["uuid"]
}
```

### 6.4 Final answer contract

The final response separates facts by epistemic status:

- `deterministic_facts`: computed counts, geometry, and timestamps.
- `visual_observations`: directly visible attributes/actions from verified evidence.
- `inferences`: plausible interpretations with explicit uncertainty.
- `conflicts`: disagreements between models or deterministic tools.
- `evidence`: camera, time interval, artifact, and optional bounding region.
- `abstentions`: unsupported portions of the question.

## 7. Storage and integration boundaries

Introduce ports before choosing production infrastructure:

```rust
trait EvidenceStore
trait EventRepository
trait FeatureStore
trait VectorIndex
trait LexicalIndex
trait RelationshipStore
trait AnalyticsPublisher
```

### Default local profile

- Encoded evidence, thumbnails, crops, and feature blobs: filesystem under `VISN_DATA_DIR`.
- Metadata/event/entity records: disposable embedded local catalog or append-only versioned files.
- Vector index: in-process HNSW/USearch-class index persisted under the data directory.
- Lexical search: embedded BM25/Tantivy-class index.
- Relationships: adjacency records through the repository trait; no graph database is required initially.
- Kafka: existing no-op sink by default.

The exact embedded catalog library should be selected during Phase A by measuring crash recovery, filtering, and migration effort. It is a local developer/runtime convenience, not the production source of truth and not a substitute for the backend-owned database.

### Future backend profile

- Receive authoritative camera/cluster/policy metadata through the backend contract.
- Publish versioned event, evidence-ready, feature-ready, and answer envelopes through the agreed Kafka adapter.
- Store only opaque backend IDs in the analytics core.
- Keep vector/feature infrastructure swappable; MongoDB should not be forced to act as a high-volume vector or raw-evidence store unless the backend team explicitly selects it after benchmarking.

## 8. Phased delivery plan

Durations below are engineering estimates for sequencing, not calendar commitments. Phases can overlap only after their input contracts are frozen.

### Phase A — Baseline, corpus, and architecture decisions (3–5 engineering days)

#### Work

- Freeze 20–50 representative clips spanning static scenes, camera motion, short actions, small objects, occlusion, lighting changes, dense activity, HTTP sources, and overlapping cameras.
- Label event intervals, key entities, counts, OCR/ASR snippets where present, and query relevance.
- Create 75–150 evaluation questions covering all planned query types.
- Capture current YOLO executions, decode count, runtime, observations, HOTA/IDF1 where labels exist, and deterministic-event metrics.
- Capture current one-frame VLM description quality and failure rate across all UI-selectable models.
- Add architecture decision records for source time, local store, embedding worker protocol, model versioning, and evidence retention.
- Decide whether the first embedding worker uses Python HTTP over loopback or a Unix socket. Prefer a persistent worker over per-event subprocesses.

#### Deliverables

- `benchmarks/corpus.jsonl` manifest with hashes and licenses.
- `benchmarks/queries.jsonl` with relevance/evidence labels.
- Reproducible baseline report.
- ADRs and fixed v1 contracts.

#### Exit gate

- Every benchmark query has a correct evidence interval or is explicitly unanswerable.
- Current compute and accuracy baselines can be reproduced from one command without a database or Kafka.

### Phase B — Evidence spine and source-time correctness (1.5–2 weeks)

#### Work

- Add a `SourceSession` abstraction shared by upload, HTTP(S), and RTSP inputs.
- Probe streams for codec, dimensions, time base, nominal FPS, audio, and keyframes.
- Produce timestamped, keyframe-aware evidence fragments with pre/post expansion.
- Build a local evidence manifest and atomic artifact writer.
- Add clip-remux and thumbnail endpoints.
- Link existing deterministic events and representative frames to evidence intervals.
- Record reconnect generations and clock discontinuities.
- Add disk quota and recoverable eviction, pinning benchmark/evidence artifacts referenced by active results.

#### Deliverables

- `ingest`, `clock`, and `evidence` modules.
- Local evidence browser in the UI.
- An evidence reference on every YOLO event.

#### Exit gate

- A requested interval from upload, HTTP, and RTSP can be remuxed and played.
- Actual evidence covers requested pre/post time within documented GOP tolerance.
- Credentials never appear in API responses, manifests, or errors.
- Crash/restart does not expose partially finalized evidence as valid.

### Phase C — Adaptive observer and event segmentation (2–3 weeks)

#### Work

- Implement GOP/packet activity extraction and sparse low-resolution decoding.
- Add per-camera rolling normalization and four-tier scheduling.
- Add a persistent embedding-worker interface and first global/temporal embedding backend.
- Compute event boundaries with hysteresis, minimum/maximum duration, and pre/post roll.
- Add audit sampling for idle intervals and a fail-open critical policy.
- Persist activity signals, embeddings, boundary explanations, and provenance.
- Add UI charts for activity, sampling tier, boundaries, and compute decisions.

#### Deliverables

- Versioned `EventRecord` generation.
- Camera-specific adaptive policy configuration.
- Replayable event-segmentation benchmark.

#### Exit gate

- Critical labeled-event recall at the gate is at least 99% on the initial corpus; the target rises to 99.5% as the corpus matures.
- Short-event miss rate does not exceed the current fixed-FPS baseline.
- Heavy detector executions fall by at least 50% on non-policy footage without reducing deterministic-event accuracy outside the agreed tolerance.
- Idle audit samples quantify false negatives rather than assuming zero.

### Phase D — Hierarchical memory and coarse retrieval (2–3 weeks)

#### Work

- Implement feature, event, entity, vector, lexical, and relationship repository traits.
- Persist versioned global embeddings and a vector index.
- Generate strictly factual short captions only for high-novelty events or query candidates; captions are supplemental search text.
- Add BM25 indexing over captions, detector classes, OCR, ASR, camera labels, and deterministic events.
- Add metadata/time/camera/cluster filtering.
- Implement reciprocal-rank fusion across vector and lexical branches.
- Add `POST /api/v1/queries` and asynchronous result polling.
- Return event cards and playable evidence in the UI.

#### Deliverables

- Semantic and lexical search over indexed video.
- Retrieval score/explanation panel.
- Index-version migration/rebuild command.

#### Exit gate

- Recall@10 is at least 90% on the initial heterogeneous query set and never below the best existing baseline per query category.
- Every result resolves to valid source evidence.
- Changing an embedding model creates a new index version and leaves old results reproducible.
- The service remains fully usable with Kafka disabled and no external database.

### Phase E — Reranking, specialists, and evidence reconciliation (3–4 weeks)

#### Work

- Add late-interaction or cross-encoder reranking for only the top coarse candidates.
- Implement a typed specialist planner.
- Convert YOLO26 into a persistent/batched specialist worker while preserving the current command adapter as fallback.
- Add query-triggered OCR and re-identification first; add ASR only for sources with audio.
- Add open-vocabulary grounding and segmentation behind optional features after benchmark evidence shows value.
- Expand top event intervals backward/forward when the question is temporal.
- Verify the top few candidates with a capability-compatible VLM/video VLM.
- Reconcile deterministic facts and visual observations into the final answer contract.
- Cache specialist runs by evidence hash and complete parameter set.

#### Deliverables

- Query planner, specialist registry, reranker, verifier, and reconciler.
- Evidence overlays for detections/OCR/segments in the UI.
- Model capability and resource status panel.

#### Exit gate

- Recall@5 and temporal IoU improve over Phase D on the held-out set.
- Numeric answers match deterministic computations or explicitly report a conflict.
- Unsupported model/media combinations fall back without failing the query.
- No unreferenced visual claim is returned as observed.
- A repeated query reuses cached artifacts and records cache provenance.

### Phase F — Entity memory and multi-camera retrieval (3–4 weeks)

#### Work

- Replace color/texture appearance prototypes with a versioned, calibrated re-ID backend.
- Store local tracklets as entity observations linked to events/evidence.
- Use topology, travel time, temporal overlap, appearance, class, and quality in association.
- Add negative evidence and contradiction checks before merging components.
- Preserve alternative candidates near the decision boundary.
- Add event/entity relationships and multi-hop temporal traversal.
- Implement cluster queries such as “where did this person appear next?” and “what happened before the object reached camera B?”
- Add a cluster timeline with synchronized evidence playback.

#### Deliverables

- Entity repository and relationship traversal.
- Calibrated single/cross-camera identity decisions.
- Camera-wise and cluster-wise evidence-backed answers.

#### Exit gate

- HOTA, IDF1, ID switches, and cross-camera match precision are measured on labeled overlap footage.
- Final-match threshold favors precision; ambiguous matches do not collapse into one identity.
- Clock offset and topology errors appear as quality warnings.
- Removing the re-ID backend leaves camera-wise retrieval functional.

### Phase G — Generative zero-shot composed retrieval (3–4 weeks)

#### Work

- Accept a reference frame, crop, track, event, or clip plus modification text.
- Parse preserved, changed, forbidden, entity, and temporal constraints.
- Generate parallel representations: reference, modification text, joint composition, target caption, optional pseudo-target images, temporal/action, and negative condition.
- Generate only 2–4 pseudo-targets at query time and cache them.
- Search event, region, entity, text, and temporal indexes in parallel.
- Fuse candidates, then run preserve/edit-aware pairwise reranking over reference, candidate, and modification.
- Verify the actual clip and neighboring intervals; reject visual lookalikes that violate the edit.
- Display preserved/changed/violated evidence separately.

#### Deliverables

- Composed query API and UI workflow.
- Pseudo-target cache and provenance.
- Preserve/edit/temporal scoring breakdown.

#### Exit gate

- Evaluate reference preservation, requested-edit satisfaction, temporal consistency, and R@1/5/10 separately.
- The workflow beats text-only and image-similarity-only branches on the same held-out queries.
- Pseudo-target generation is optional and never runs during ingestion.

### Phase H — Agentic seeking, optimization, and production handoff (3–5 weeks)

#### Work

- Add bounded tool-guided seeking: adjacent interval, wider temporal window, higher-resolution crop, another camera, source audio, entity history.
- Set hard limits for search steps, decoded duration, candidates, model calls, and wall time.
- Add early token/patch pruning only after correctness comparisons.
- Batch embeddings and specialists across camera events.
- Add backpressure, queue limits, cancellation, retry classes, and circuit breakers.
- Add OpenTelemetry metrics/traces for ingest, gating, indexing, retrieval, reranking, specialists, verification, cache, and evidence.
- Run extended stream/reconnect/disk-pressure/model-outage tests.
- Freeze backend-facing persistence and Kafka contracts with the backend team.

#### Deliverables

- Bounded seeking agent with a visible execution trace.
- Capacity and cost report per camera-hour and query class.
- Backend adapter contract and migration plan.

#### Exit gate

- Desired target: at least a 10× reduction in heavy model invocations on non-policy workloads versus continuous YOLO/VLM interpretation, subject to accuracy gates.
- Query p95 meets the agreed class-specific SLO.
- Memory, queues, evidence disk, model calls, and agent steps are bounded.
- Model and backend outages degrade to searchable evidence/deterministic facts rather than losing jobs.

## 9. Repository change map

Keep the repository as one deployable service during the first vertical slice. Split processes only where model/runtime isolation is necessary.

```text
src/
  api.rs                         existing routes + query/evidence endpoints
  config.rs                      observer, retention, index, model-router configuration
  domain.rs                      versioned evidence/event/query/entity contracts
  pipeline.rs                    orchestrates both deterministic and memory lanes
  event_engine.rs                retained deterministic authority
  cluster.rs                     retained, then upgraded to entity-memory inputs
  sink.rs                        retained backend publishing port
  ingest/
    mod.rs
    source_session.rs
    probe.rs
    clock.rs
    activity.rs
  evidence/
    mod.rs
    fragmenter.rs
    manifest.rs
    local_store.rs
  memory/
    mod.rs
    repository.rs
    event.rs
    entity.rs
    relationship.rs
  indexing/
    mod.rs
    vector.rs
    lexical.rs
    versions.rs
  retrieval/
    mod.rs
    planner.rs
    recall.rs
    fusion.rs
    rerank.rs
    composed.rs
  specialists/
    mod.rs
    registry.rs
    yolo.rs
    reid.rs
    ocr.rs
    asr.rs
    grounding.rs
    segmentation.rs
  verification/
    mod.rs
    model_router.rs
    reconcile.rs
tools/
  yolo26_runner.py               retained compatibility adapter
  embedding_worker.py            persistent embedding/batching worker
  specialist_worker.py           optional shared model worker after Phase E
benchmarks/
  corpus.jsonl
  queries.jsonl
  labels/
static/
  index.html                     add Memory/Search workspace
  app.js
  styles.css
```

Do not create separate network services for each small Rust module. The persistent Python model worker and LM Studio are enough process boundaries initially.

## 10. API and UI plan

### API additions

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/index-jobs` | Ingest/index upload or bounded stream with an observer policy |
| `GET /api/v1/index-jobs/{id}` | Status, compute statistics, event count, and index version |
| `GET /api/v1/events` | Filter events by camera, cluster, time, quality, or class |
| `GET /api/v1/events/{id}` | Event, features, deterministic links, and provenance |
| `GET /api/v1/evidence/{id}` | Metadata and authorized playback/remux reference |
| `POST /api/v1/queries` | Semantic, temporal, entity, or composed query |
| `GET /api/v1/queries/{id}` | Retrieval stages, final answer, conflicts, and evidence |
| `GET /api/v1/model-capabilities` | Encoder/reranker/specialist/verifier capabilities and state |
| `GET /api/v1/indexes` | Active versions and rebuild status |
| `GET /api/v1/metrics/summary` | Test-friendly compute and latency summary |

Existing `/jobs` and `/cluster-jobs` remain compatible.

### UI additions

Add a new `Memory & Search` workspace with:

- Source/index job form using upload, HTTP(S), or RTSP.
- Camera/cluster event timeline with activity tier and event boundaries.
- Evidence player with pre/post interval and overlays.
- Natural-language search with camera/time filters.
- Reference selection for composed retrieval.
- Result cards showing coarse, rerank, and verification scores.
- Answer panel separating deterministic facts, observations, inferences, conflicts, and abstentions.
- Model/specialist routing and fallback diagnostics.
- Compute panel showing decoded frames, detector executions, embedding clips, VLM calls, cache hits, and elapsed GPU/CPU time.
- Evaluation panel for running a small selected benchmark manually; no automatic comprehensive suite.

Advanced thresholds should live behind an `Expert settings` disclosure. The normal workflow exposes three policies: `accuracy`, `balanced`, and `economy`, with `balanced` as default and critical rules configured separately.

## 11. Accuracy and efficiency evaluation

### Frozen comparisons

Run the research report's ablations as explicit configurations:

```text
A. existing keyframe/representative-frame behavior
B. existing continuous YOLO path
C. event embeddings only
D. C + adaptive codec/activity gate
E. D + hierarchical memory
F. E + fine reranking
G. F + query-triggered specialists
H. G + composed retrieval
I. H + bounded agentic verification
```

### Metrics

| Area | Metrics |
| --- | --- |
| Gate/segmentation | critical-event recall, short-event miss rate, boundary F1, temporal IoU, idle-audit false-negative rate |
| Retrieval | Recall@1/5/10, mAP, nDCG, evidence timestamp precision |
| Composed retrieval | preserve accuracy, edit satisfaction, temporal validity, R@1/5/10, near-duplicate violation rate |
| Tracking | HOTA, IDF1, ID switches, count MAE, zone/line/dwell precision and recall |
| Answers | factual correctness, evidence correctness, hallucination rate, conflict detection, abstention quality |
| Systems | full decodes/hour, YOLO calls/hour, embedding clips/hour, visual tokens/query, GPU-seconds/camera-hour, p50/p95 latency, cache hit rate |

### Acceptance method

- Report results by query/camera category; an average must not conceal a critical regression.
- Maintain a held-out set not used for thresholds or prompt tuning.
- Bootstrap confidence intervals for small corpora.
- Record accuracy against compute as a Pareto curve for `accuracy`, `balanced`, and `economy` policies.
- Promote a change only when it either improves accuracy at similar cost or reduces cost within the agreed per-category accuracy tolerance.
- Never claim a compute saving by excluding failed or unanswerable jobs from the denominator.

## 12. Failure handling and safeguards

| Failure | Required behavior |
| --- | --- |
| HTTP/RTSP disconnect | Close current generation, finalize valid fragments, reconnect with a new generation, record a gap |
| Missing/invalid timestamps | Create receipt-clock estimates and mark them degraded; do not silently mix clock domains |
| Codec side data unavailable | Continue with sparse luma/activity features |
| Embedding worker unavailable | Retain evidence and queue bounded retry; deterministic policy lane continues |
| Vector index unavailable | Fall back to lexical/time/entity filters where possible |
| VLM unavailable or unsupported media | Return deterministic/specialist observations with a fallback reason |
| Specialist timeout | Preserve partial result and mark the unanswered claim |
| Disk pressure | Evict unpinned expired evidence by policy; never remove a referenced artifact silently |
| Index/model upgrade | Dual-read or version-select during rebuild; do not mix incompatible vectors |
| Cross-camera uncertainty | Preserve alternatives and avoid a final identity merge |
| Conflicting evidence | Surface conflict and evidence; optionally rerun the authoritative specialist |

## 13. Principal risks and mitigations

1. **Research model availability or licensing:** benchmark at least one smaller fallback per role and keep contracts model-neutral.
2. **Compressed-domain signal inconsistency:** treat motion vectors as optional; validate per codec/camera profile.
3. **Gate false negatives:** preserve raw evidence, audit idle samples, use maximum heartbeat intervals, and fail open on uncertainty.
4. **Index drift after model upgrades:** immutable index versions, artifact provenance, and reproducible rebuilds.
5. **VLM hallucination:** typed answer contract, evidence validation, numeric guardrails, and abstention.
6. **Cross-camera false merges:** topology gates, calibrated re-ID, contradiction constraints, and high-precision final thresholds.
7. **VRAM churn from UI-selected VLMs:** centralized load queue, capability registry, resident ingest encoder, and request batching.
8. **Excessive architecture complexity:** deliver one end-to-end vertical slice before graph, CIR, or agentic seeking.
9. **Backend contract churn:** keep Kafka and persistence behind ports; no production schema assumptions in the core.
10. **Privacy exposure from rich memory:** evidence access, face features, audio, OCR, and retention require explicit policy before production enablement.

## 14. Recommended first vertical slice

The first useful delivery should include only Phases A through the thin part of D:

1. Index one upload or bounded HTTP stream into keyframe-aware evidence fragments.
2. Extract sparse activity features and 1–4 second event microclips.
3. Generate one versioned clip embedding per event through a persistent worker.
4. Store evidence/event/embedding metadata locally.
5. Search events by text with camera/time filters.
6. Play the exact retrieved evidence in the UI.
7. Optionally run existing YOLO26 and the selected LM Studio VLM on only the top candidate.
8. Show detector/VLM calls and retrieval scores so compute and quality are visible.

This slice proves the new architectural premise while reusing the current service. It deliberately excludes entity graphs, pseudo-target generation, open-vocabulary grounding, and autonomous seeking until coarse retrieval and evidence correctness are demonstrably strong.

### First-slice definition of done

- Works locally without Kafka or MongoDB.
- Supports upload and plain HTTP sources; RTSP follows through the same source abstraction.
- A query returns ranked event intervals with playable evidence.
- Every artifact is versioned and reproducible.
- Existing job and cluster flows remain operational.
- The fixed evaluation subset reports retrieval accuracy and compute against the current YOLO path.
- Failures return explicit partial/fallback results rather than corrupting the index.

## 15. Delivery order summary

```text
Baseline/corpus
  → source-time + evidence spine
  → adaptive temporal events
  → coarse searchable memory
  → fine reranking + selective specialists
  → calibrated entity/cross-camera memory
  → composed retrieval
  → bounded agentic seeking and production adapters
```

The most important sequence rule is: **prove evidence correctness and coarse recall before adding a powerful reasoning agent**. A better final model cannot recover intervals that ingestion failed to preserve or retrieval failed to surface.

