# Implementation Plan: Large-Scale Camera Analytics Platform

## 1. Purpose and delivery outcome

This plan turns the supplied production architecture into an executable, greenfield delivery program for a regional, multi-tenant camera analytics platform built around Rust, DeepStream/GStreamer, YOLO26, deterministic event processing, Redpanda, ClickHouse, object storage, and selectively invoked Gemma models.

The target outcome is a production release that:

- Processes at least 5,000 concurrent RTSP cameras and uploaded video segments.
- Maintains continuous connections and decoding while applying policy-driven detector rates.
- Produces deterministic tracks, counts, events, evidence, and hourly facts.
- Publishes reports within 10 minutes after the hour, even when Gemma output is invalid.
- Uses at-least-once messaging with idempotent effects and complete model lineage.
- Survives worker, broker, object-store, and regional failure tests within agreed SLOs.
- Can scale each of vision inference, event processing, aggregation, and generative inference independently.

This is not a commitment to run inference on every camera frame. The stream is continuous; detector cadence is selected per camera policy and tracking bridges detector intervals.

## 2. Scope

### In scope for the first production release

- Tenant, site, camera, policy, zone, line, model-release, assignment, and report management.
- RTSP H.264/H.265 ingest and direct-to-object-storage uploaded segments.
- Hardware decoding, batched YOLO26 TensorRT inference, NvDCF tracking, and deterministic spatial rules.
- Encoded local ring buffers and event-triggered evidence bundles.
- Regional vision cells with capacity-aware camera ownership leases.
- Versioned Protobuf events over Redpanda.
- PostgreSQL control data, ClickHouse analytics data, and S3-compatible evidence storage.
- Event-time minute/hour aggregation with late-event corrections.
- Gemma visual enrichment and grounded hourly report generation through vLLM.
- Template report fallback, auditability, observability, security controls, deployment automation, and failure runbooks.

### Explicitly out of scope for the first release

- Face recognition, cross-camera identity, and biometric embeddings.
- Recording all raw streams centrally by default.
- Sending per-frame detections or image bytes through Redpanda.
- Using Gemma for counts, geometry, track existence, or rule decisions.
- Heuristic pre-detection keyframes or image grids.
- A general video-management-system replacement.
- Training models in the online Rust services.

## 3. Planning assumptions

The schedule below assumes:

- A greenfield repository and a team of roughly 10–14 engineers: 5–6 Rust/platform, 2 video/GPU, 2 ML/MLOps, 2 SRE/data, plus shared product, security, and QA support.
- NVIDIA-capable Linux development and test hosts are available by the end of Phase 0.
- Representative, legally usable footage and camera configurations are available for development and evaluation.
- One initial deployment environment and GPU family are selected before TensorRT engine work begins.
- Managed PostgreSQL/object storage may be used; Redpanda and ClickHouse may be managed or operated in Kubernetes based on the Phase 0 decision.
- Live-stream processing is the critical path. Uploaded segments reuse downstream inference/event contracts but are scheduled separately.
- Delivery uses two-week iterations, feature flags, and progressive scale gates: 1, 16, 64, 250, 1,000, then 5,000 cameras.

The estimated path to a production-scale acceptance test is 32–40 weeks. Calendar time depends heavily on GPU procurement, representative data, model accuracy, RTSP diversity, and security/compliance review.

## 4. Decisions required before implementation

Record each decision as an ADR. Phase 0 cannot exit until decisions D01–D12 have an owner and approval date.

| ID | Decision | Required output |
|---|---|---|
| D01 | Deployment footprint | Cloud/on-premises regions, Kubernetes distribution, network paths, and data-residency boundary |
| D02 | GPU baseline | Vision and Gemma GPU SKUs, driver/CUDA/TensorRT/DeepStream compatibility matrix, and procurement plan |
| D03 | Model and licensing | Exact YOLO26 artifact/version, Ultralytics commercial or AGPL posture, Gemma artifact/version/terms, and approved registry |
| D04 | Capacity profiles | Initial low/standard/high/critical detector rates and which tenants/cameras may use each |
| D05 | Retention/privacy | Tenant defaults, jurisdiction overrides, evidence access rules, and deletion SLO |
| D06 | Availability topology | Regional cell count, failure domains, PostgreSQL/Redpanda/ClickHouse/object-store HA, and recovery objectives |
| D07 | Identity | OIDC provider, workload identity, RBAC model, mTLS mechanism, and audit requirements |
| D08 | Source time | Accepted camera clock error, NTP/PTP requirements, timestamp-quality policy, and fallback semantics |
| D09 | Product semantics | Required event catalogue, severity model, report consumers, revision notifications, and offline-camera reporting behavior |
| D10 | Data ownership | Dataset approval, labelling workflow, hard-negative review, and evaluation sign-off owners |
| D11 | ClickHouse idempotency | Dedupe and rollup algorithm proven not to double-count replayed messages |
| D12 | Control-plane consistency | Lease store, leader election, generation fencing, and behavior during control-plane partitions |

Two compatibility spikes are mandatory. First, prove that the chosen YOLO26 output can be exported and parsed by the chosen DeepStream/TensorRT versions, including the NMS-free output contract. Second, prove that the exact Gemma artifacts can be served multimodally by the selected vLLM version and return constrained structured output. Keep both integrations behind internal adapters so model/runtime replacements do not alter event or report contracts.

## 5. Architecture invariants

These rules are enforced in design review and automated tests:

1. Raw frames and evidence bytes never enter the central event bus; messages contain IDs, metadata, and object URIs.
2. Per-frame detections remain local to a vision worker; only completed/materially changed tracks and events are published.
3. Every published record includes tenant, camera, event time, schema version, immutable message ID, producer build, policy version, and applicable model/tracker versions.
4. A worker may process a camera only while holding the current assignment generation. Stale generations cannot publish accepted results.
5. Counts and rule events are deterministic and independently reproducible from versioned inputs.
6. All external effects are safe under message replay. Consumer offsets are committed only after durable effects succeed.
7. Report numbers are copied from an immutable fact document; Gemma cannot introduce or recalculate numeric facts.
8. Invalid Gemma output retries once, then produces a deterministic template report.
9. No process has an unbounded decoded-frame, message, upload, or inference queue.
10. Removing or degrading optional evidence and enrichment must not stop core detection and critical event delivery.

## 6. Delivery strategy and dependency path

The work proceeds as a sequence of usable vertical slices. Platform infrastructure, data preparation, and model work run alongside the main path, but each scale step is gated by measured evidence.

```text
Phase 0 decisions and risk spikes
  -> Phase 1 engineering foundation and contracts
  -> Phase 2 control plane and camera ownership
  -> Phase 3 single-node vision pipeline
  -> Phase 4 deterministic events and evidence
  -> Phase 5 durable event/data platform
  -> Phase 6 event-time aggregation and deterministic reports
  -> Phase 7 Gemma enrichment and grounded reports
  -> Phase 8 regional cells and horizontal scaling
  -> Phase 9 5,000-camera resilience and production readiness
```

Model data collection/fine-tuning starts in Phase 0 and continues through Phase 9. Security, observability, testing, and runbooks are phase deliverables, not a final hardening phase.

## 7. Phase-by-phase implementation

### Phase 0 — Discovery, risk retirement, and measurable baselines (2–3 weeks)

#### Objectives

- Resolve the product, licensing, hardware, privacy, and deployment decisions that materially affect design.
- Prove the highest-risk native/model integrations before creating many dependent services.
- Establish benchmark footage, load shapes, SLO definitions, and cost assumptions.

#### Work packages

**P0.1 Workload inventory**

- Inventory camera vendors, RTSP authentication modes, codecs, profiles, resolutions, nominal/actual FPS, GOP length, bitrates, network locations, and source timestamp behavior.
- Select a representative corpus covering day/night, rain/fog, glare, occlusion, compression, dense scenes, empty scenes, and failure streams.
- Define camera categories and their initial inference/evidence policies.
- Quantify expected events, track completions, evidence rate/size, and report token volumes per camera-hour.

**P0.2 Runtime compatibility spike**

- Freeze a tested matrix for OS, NVIDIA driver, CUDA, TensorRT, DeepStream, GStreamer, Rust bindings, and GPU architecture.
- Export one YOLO26s model to ONNX and a TensorRT FP16 engine; document input normalization, tensor names/shapes, output parser, dynamic/static batch behavior, and engine hash.
- Build a minimal Rust-supervised pipeline for one recorded stream: demux/decode -> batch -> inference -> tracker -> safe Rust metadata.
- Serve the chosen routine Gemma model through vLLM and verify text plus ordered-image/short-video requests and JSON-schema-compatible responses.
- Measure cold start, steady GPU memory, throughput, and p95 latency for both paths.

**P0.3 Data correctness spikes**

- Demonstrate keyframe-aligned encoded segmentation and remuxing of a decodable pre/post event clip without continuously re-encoding the stream.
- Replay duplicate Redpanda messages into the proposed ClickHouse layout and prove an hourly aggregate does not double-count them.
- Simulate two workers claiming a camera and prove assignment generation fencing rejects the stale owner.

**P0.4 Operating model**

- Approve SLIs and SLO calculation rules, including denominators and maintenance exclusions.
- Build an initial cost model for network, GPUs, local NVMe, object storage, broker retention, ClickHouse, and Gemma.
- Write the threat model and privacy impact assessment.

#### Deliverables

- Approved ADR set D01–D12.
- Version compatibility matrix and reproducible spike manifests.
- Initial benchmark report, capacity worksheet, and cost envelope.
- Versioned evaluation corpus manifest and labelling plan.
- SLO specification, threat model, and data-flow diagram.

#### Exit gate

- One stream runs continuously for 24 hours through decode, YOLO, tracking, and Rust metadata extraction.
- An event clip is decodable and contains the requested pre/post interval within an agreed GOP-boundary tolerance.
- Duplicate replay produces exactly one logical event and one contribution to the tested hourly count.
- Licensing and data use are approved; selected hardware is available or firmly scheduled.

### Phase 1 — Repository, contracts, and engineering foundation (2–3 weeks)

#### Objectives

- Create a buildable Rust workspace, versioned contracts, local developer environment, CI, and deployment skeleton.
- Make compatibility and tenant isolation rules executable.

#### Work packages

**P1.1 Repository scaffold**

- Create the Cargo workspace described in Section 9, pin the Rust toolchain, define feature flags, and enforce formatting, Clippy, dependency/license/audit checks, and denied unsafe code outside `deepstream-sys`.
- Add common crates for typed IDs, UTC time, configuration, errors, telemetry, auth context, health endpoints, and test fixtures.
- Use reproducible container builds with SBOM, provenance, vulnerability scan, and immutable image digest.

**P1.2 Contract-first schemas**

- Define Protobuf envelopes and payloads for camera lifecycle/health, segment-ready, track-completed, event-observed, evidence-ready, window-closed, enrichment, insight, and dead-letter records.
- Establish compatibility rules: additive fields only within `v1`, never reuse field numbers, reserve removed fields, explicit enums with `UNSPECIFIED`, and contract fixtures for every producer/consumer.
- Define external JSON/OpenAPI schemas separately from bus Protobuf. Do not expose storage records directly as API models.
- Specify deterministic ID formulas, canonical serialization where hashes are used, partition keys, retry metadata, trace context, and source/processing timestamps.

**P1.3 Local and CI environments**

- Provide a CPU-only local stack for PostgreSQL, Redpanda/schema registry, ClickHouse, object storage, OTel collector, and fake Gemma.
- Provide a GPU integration lane on an approved self-hosted runner with pinned runtime images and model fixtures.
- Add database migration validation, Protobuf compatibility tests, container tests, unit test coverage reporting, and secret scanning.

**P1.4 Operational standards**

- Define service templates: config precedence, graceful shutdown, readiness/liveness/startup probes, structured logging redaction, metrics naming, retry policy, and request/message correlation.
- Define environment promotion, migration order, rollback, and secret delivery patterns.

#### Deliverables

- Compiling workspace and CI/CD skeleton.
- Published `v1` contracts and API specification.
- One-command local non-GPU environment and documented GPU test path.
- Service and observability templates.

#### Exit gate

- A sample producer and consumer exchange all `v1` message types in CI.
- Backward-compatibility tests reject a deliberately breaking schema change.
- Tenant ID propagation, auth context, traces, and redaction work end to end in the sample service.

### Phase 2 — Control plane, policies, and safe camera ownership (3–4 weeks)

#### Objectives

- Register cameras and policies and assign each active camera to exactly one current worker generation.
- Provide the minimum operational API before scaling stream processing.

#### Work packages

**P2.1 PostgreSQL model**

- Implement migrations for tenants, sites, cameras, credential references, inference/analytics/retention policies, zones, lines, model releases, workers, assignments, assignment history, report status, users/roles, and audit log.
- Use soft deletion and explicit lifecycle states for cameras. Retain immutable assignment/model/report history.
- Add tenant-scoped keys/indexes and database-level defenses against accidental cross-tenant queries where feasible.

**P2.2 Control APIs**

- Implement camera CRUD, policy/zones/lines APIs, segment initiation/completion, reports/evidence lookup, health, worker inspection, and drain operations.
- Validate normalized zone/line coordinates, policy/model compatibility, RTSP secret references, and idempotency keys.
- Return presigned upload URLs; never proxy video bytes.
- Implement OIDC authentication, tenant-aware authorization, rate limits, audit events, pagination, and consistent error bodies.

**P2.3 Assignment controller**

- Use a PostgreSQL-backed authoritative lease with `camera_id`, `worker_id`, `generation`, state, expiry, and policy/model snapshot version.
- Use rendezvous hashing with measured capacity weights for stable initial placement.
- Implement controller leader election, worker heartbeat/capacity advertisements, transactional compare-and-swap generation changes, lease renewals, explicit revocation, and stale-generation rejection.
- Support gradual drain, per-cell constraints, anti-affinity, camera priority, and reconciliation after controller restart.

**P2.4 Worker-side supervisor shell**

- Watch assignments, resolve credentials through workload identity, start/stop placeholder camera tasks, renew ownership, and publish camera lifecycle/health.
- Implement bounded exponential reconnect backoff with jitter and complete health state.

#### Deliverables

- Deployable control API/registry and assignment controller.
- PostgreSQL migrations and seed/test data.
- Worker registration, heartbeat, assignment, revoke, drain, and reconciliation flows.
- API and operator documentation.

#### Exit gate

- At least 10,000 simulated camera records can be assigned and rebalanced within the agreed control-plane latency.
- Concurrent claim and expired-lease tests prove only the latest generation is accepted.
- Controller restart does not cause assignment loss or mass reassignment.
- A drained worker receives no new cameras and releases existing cameras gradually.

### Phase 3 — Single-node production video pipeline (4–6 weeks)

#### Objectives

- Continuously process a bounded, representative camera group on one GPU node.
- Establish the safe FFI boundary and obtain measured per-node capacity.

#### Work packages

**P3.1 Source and isolation model**

- Implement RTSP source bins with TCP/UDP policy, latency configuration, watchdogs, codec discovery, reconnect, and source timestamp mapping.
- Group sources into independently restartable pipelines; benchmark 16, 32, and 64 streams rather than assuming a group size.
- Bound queues and define drop policy explicitly. Never accumulate decoded frames during downstream stalls.

**P3.2 Encoded ring buffer**

- Tee the encoded stream before decode into timestamped, keyframe-aware fragments on local NVMe.
- Maintain a 5–15 minute size/time-bounded index, atomic fragment finalization, continuous eviction, crash cleanup, and disk quotas per priority.
- Persist enough source/processing clock metadata to locate event intervals after timestamp drift.

**P3.3 GPU inference graph**

- Add NVDEC, `nvstreammux`, TensorRT YOLO26s FP16, NvDCF, and spatial analytics using immutable model/policy snapshots.
- Implement configurable inference intervals without disrupting continuous decode.
- Validate dynamic source add/remove and batch timeout behavior.
- Record engine/model/parser/tracker hashes and reject incompatible engine/runtime combinations at startup.

**P3.4 Safe metadata extraction**

- Keep all C pointer traversal, lifetime handling, and metadata-copy logic inside `deepstream-sys`.
- Expose owned Rust detections, track observations, frame timing, and quality fields to the rest of the application.
- Add malformed/null metadata tests and sanitizer-assisted native integration tests where supported.

**P3.5 Capacity benchmark**

- Measure decoder sessions, sustainable inferred FPS, batch distribution, p50/p95/p99 latency, tracker cost, GPU memory/utilization, CPU, network, file descriptors, and NVMe I/O.
- Calculate safe cameras/GPU as the minimum of decode, detector, memory, tracking, network, and evidence limits at 60–70% planned utilization.

#### Deliverables

- `stream-worker`, `video-pipeline`, and isolated `deepstream-sys` implementations.
- FP16 engine manifest and compatibility validation.
- Camera profile configurations and benchmark harness/report.
- RTSP reconnect and pipeline-restart runbook.

#### Exit gate

- The selected stream group runs for 72 hours with bounded memory/file descriptors and no cross-group failure propagation.
- Detection freshness is under 5 seconds at the planned utilization for all initial profiles.
- Reconnect, malformed stream, timestamp drift, source add/remove, and pipeline crash scenarios pass.
- A defensible production camera/GPU capacity number is approved; vendor benchmark numbers are not used as capacity evidence.

### Phase 4 — Tracking semantics, deterministic events, and evidence (4–5 weeks)

#### Objectives

- Turn local observations into trustworthy track summaries, rule events, counts, and evidence without Gemma.

#### Work packages

**P4.1 Track state**

- Implement candidate, confirmed, active, temporarily missing, and completed states with camera-category calibration.
- Downsample trajectory observations, compute quality/confidence, track zone membership/line crossings, and close tracks with explicit reasons including timeout, worker restart, policy change, and drain.
- Namespace track IDs by camera, worker generation, and local tracker ID to prevent collision/reuse.

**P4.2 Geometry and event state machines**

- Use normalized source-frame coordinates and versioned transformations.
- Implement hysteresis/debounce for zone boundaries and robust directional line intersection to prevent jitter-induced duplicates.
- Implement the initial high-value events first: zone enter/exit, directional line crossing, person/vehicle enter/exit, dwell, occupancy threshold, restricted zone, wrong direction, and camera offline/obstructed.
- Add stationary/removed/unattended-object candidates only after accuracy criteria and product semantics are defined.
- Generate deterministic event IDs and attach policy, detector, tracker, assignment generation, and evidence requirements.

**P4.3 Evidence selection and capture**

- Maintain bounded top candidates per active track based on confidence, visible area, sharpness, occlusion, and boundary truncation.
- Capture the original-resolution frame and crops without grids.
- On event trigger, pin required encoded fragments until post-roll completes, remux a keyframe-aligned clip, and transcode with NVENC only where the delivery format or exact boundaries require it.
- Create an immutable manifest with checksums, dimensions, timestamps, track path, source/model lineage, and upload state.

**P4.4 Accuracy harness**

- Build golden trajectory/rule fixtures and an annotated video evaluator for per-event precision/recall, count error, track fragmentation, ID-switch candidates, boundary jitter, and evidence usability.
- Version thresholds by camera category; do not collapse results into one system accuracy score.

#### Deliverables

- `tracking`, `event-engine`, and `evidence-selector` crates.
- Versioned policy schema and initial policy library.
- Evidence bundle/manifest format and clip extractor.
- Accuracy dashboard and evaluation report.

#### Exit gate

- Counts equal unique confirmed directional crossings, never bounding-box observations.
- Replaying identical track observations produces byte-equivalent logical events and IDs.
- Required event classes meet agreed per-class accuracy thresholds on held-out footage.
- Evidence clips are independently decodable, contain required context, and remain linked to their exact event/model/policy versions.

### Phase 5 — Durable event, analytics, and object-storage platform (3–4 weeks)

#### Objectives

- Make events/evidence durable, replayable, ordered per camera, and safe under consumer or infrastructure failure.

#### Work packages

**P5.1 Redpanda topics and producers**

- Provision the versioned topic set, replication, retention, ACLs, quotas, schema compatibility, and initial partition counts.
- Configure producers with `acks=all`, idempotence, zstd compression, bounded delivery time, and camera/event keys as specified.
- Enforce message size limits that make frame/video publication impossible.

**P5.2 Local disk spool**

- Implement a checksummed, ordered, bounded per-camera spool for serialized messages when brokers are unavailable.
- Recover incomplete writes after process restart, republish in original camera order with the same IDs, expose disk pressure, and apply the documented priority drop policy only at hard limits.

**P5.3 Event persister and ClickHouse schema**

- Create raw track/event/telemetry tables partitioned by time and ordered for camera/window queries.
- Implement the D11 dedupe design. Do not rely on eventual `ReplacingMergeTree` merges before aggregation; either query a proven canonical latest-row projection or recompute versioned windows from logically deduplicated records.
- Store ingest/message identity, source partition/offset, producer generation, and all lineage fields.
- Validate replay, reordering across different cameras, schema upgrades, poison messages, and dead-letter inspection/re-drive.

**P5.4 Evidence uploads**

- Upload evidence directly from regional workers using multipart/retry/checksum validation, immutable keys, server-side encryption, and manifest-last completion semantics.
- Publish `evidence.ready` only after all required objects and the final manifest are durable.
- Implement local retry/eviction priorities for object-store outages and presigned read access through the API.

#### Deliverables

- Production topic/config definitions and schema registry policy.
- Redpanda client/spool, event persister, ClickHouse migrations, and object-store client.
- Replay, dead-letter, spool-pressure, and evidence-recovery runbooks.

#### Exit gate

- A worker can lose broker connectivity, restart, reconnect, replay, and produce exactly one logical downstream event/count.
- A failed object store does not stop detection/event publication; critical evidence uploads after recovery.
- Cross-tenant topic, table, and object access tests fail closed.
- Load test sustains at least twice the projected peak event rate with acceptable lag.

### Phase 6 — Event-time aggregation and deterministic hourly reports (3–4 weeks)

#### Objectives

- Produce authoritative hourly fact documents and useful template reports without a language model.
- Make lateness, revision, and data-quality semantics explicit.

#### Work packages

**P6.1 Event-time windows**

- Calculate windows from source event time and store processing time, clock offset, and timestamp-quality separately.
- Maintain per-camera watermarks, close normal windows after the three-minute grace period, and accept corrections during a configurable 30-minute interval.
- Define behavior for missing/invalid source time and surface it as degraded data quality rather than silently shifting events.

**P6.2 Rollups and fact documents**

- Create deterministic minute/hour calculations for uptime, disconnects, decoded/inferred/dropped frames, track counts, crossings, zone activity, occupancy, dwell, events, notable candidates, data quality, and version lineage.
- Select notable events using deterministic severity/confidence/policy rules.
- Serialize and hash an immutable fact document; store report status and every fact/report version.
- Make aggregation restartable by camera/window and safe to recompute.

**P6.3 Window scheduling**

- Implement `hash(camera_id) mod 300` stable report offsets after window closure, with a separate priority route for critical reports.
- Size the scheduler and downstream queue for the worst offset: a job released near minute eight still needs to finish by minute ten.
- Track expected versus completed reports, oldest pending window, and correction backlog.

**P6.4 Deterministic report path**

- Generate a schema-valid template report entirely from facts, including data-quality warnings and evidence/event references.
- Implement list/get/reprocess/revision APIs and explicit `supersedes_report_id` links. Never mutate a delivered version in place.

#### Deliverables

- Window coordinator, rollup queries/jobs, report builder core, report status API, and deterministic renderer.
- Backfill/recompute utility and operational runbook.
- Golden fact documents and late-event fixtures.

#### Exit gate

- Boundary, timezone, daylight-saving, clock-drift, duplicate, out-of-order, late, and reprocess tests pass.
- Every active camera receives a fact document/report or an explicit terminal failure/offline state.
- A 5,000-camera top-of-hour simulation completes template reports by the SLO with no synchronized inference burst.

### Phase 7 — Gemma visual enrichment and grounded report generation (3–5 weeks)

#### Objectives

- Add selective semantic interpretation and readable reports without weakening deterministic truth or report availability.

#### Work packages

**P7.1 Serving and queues**

- Deploy separate routine E4B and escalation 12B vLLM pools, each with pinned model digest, warm minimum replicas, health/model-load probes, concurrency limits, and independent topics/consumer groups.
- Autoscale on queue lag and oldest job age, not GPU utilization alone.
- Enforce request budgets for image/frame count, clip duration, pixels, input/output tokens, deadline, and tenant quota.

**P7.2 Rust model adapter**

- Implement an OpenAI-compatible client with model/prompt versioning, timeout/cancellation, bounded retry, response capture policy, and circuit breaker.
- Accept ordered separate images, short clips, and structured facts only. Never create grids or submit raw hours.
- Keep the provider protocol behind `gemma-client`; workers operate on internal request/result contracts.

**P7.3 Policy-driven visual enrichment**

- Trigger visual calls only for configured semantic questions, uncertain/high-value events, report evidence needs, or explicit user verification.
- Require structured output with event/evidence/track references, confidence, and uncertainties.
- Escalate only on policy importance, complexity, low confidence, or conflict with deterministic evidence; do not let escalation jobs block routine reports.

**P7.4 Grounded reports and validation**

- Pass the immutable fact document, selected event descriptions, and evidence references with the strict prompt contract.
- Validate JSON schema, referenced IDs, timestamps, enum values, confidence, and exact numeric agreement with facts.
- On first failure, submit only bounded correction context plus validation errors. On second failure/time budget exhaustion, persist the deterministic template report.
- Record fact hash, model digest, prompt version, validator version, latency, usage, validation failures, and fallback reason.

**P7.5 Evaluation and safety**

- Build an adversarial set containing ambiguous scenes, missing evidence, irrelevant evidence, prompt-like text in images, conflicting metadata, and unsupported motive questions.
- Score schema validity, event/evidence citation validity, numeric fidelity, unsupported claims, uncertainty calibration, and human usefulness independently.

#### Deliverables

- Gemma deployments, clients, enrichment/report workers, prompt registry, validator, and fallback path.
- Offline evaluation harness, scorecard, and cost/latency report.

#### Exit gate

- Numeric mismatch and nonexistent event/evidence reference rates are zero in accepted reports.
- Invalid, unavailable, or slow Gemma responses still yield an on-time template report.
- Routine and escalation queue isolation is proven under overload.
- Human review approves report usefulness and per-task semantic quality thresholds.

### Phase 8 — Regional vision cells and horizontal scaling (4–6 weeks)

#### Objectives

- Scale from one node to multiple independent cells and regions without restarting unaffected streams.
- Automate deployment, draining, capacity placement, and queued-work scaling.

#### Work packages

**P8.1 Cell packaging**

- Package supervisor, bounded GPU workers, NVMe ring/spool storage, evidence uploader, event publisher, and health reporter as a versioned cell release.
- Define cell identity, regional endpoints, certificate bootstrap, local disk quotas, egress allowlists, and offline operating limits.

**P8.2 Capacity-aware placement**

- Feed assigned weighted detector FPS, decoder usage, GPU/memory metrics, frame drops, stream health, disk pressure, and reserved headroom to the assignment controller.
- Preserve critical profiles, reduce low-priority detector rates, disable specialists, defer enrichment, then reassign in that order under overload.
- Keep 20–30% inference headroom, one worker failure reserve, and reconnect-storm capacity.

**P8.3 Kubernetes delivery**

- Create separate CPU control, GPU vision, GPU routine Gemma, GPU escalation Gemma, and data node pools with labels/taints/tolerations.
- Add Helm charts, Argo CD promotion, PodDisruptionBudgets, topology spread, anti-affinity, local persistent volumes, startup/readiness probes, and graceful pre-stop drain.
- Use KEDA for segment/enrichment/insight queues and a purpose-built controller for long-lived RTSP workers.

**P8.4 Regional and failure behavior**

- Route cameras to data-residency-compliant cells and prevent cross-region raw media movement by default.
- Define central-control loss behavior: existing leases continue for a bounded interval, new ownership is conservative, local detection continues, and queued results replay later.
- Exercise rolling upgrades, GPU node replacement, model canary, rollback, cell isolation, and multi-zone storage failure.

#### Deliverables

- Reproducible cell, Kubernetes, Helm, Argo CD, and KEDA configurations.
- Capacity controller, regional routing, progressive model/service rollout, and drain automation.
- Cell deployment, failure, expansion, and rollback runbooks.

#### Exit gate

- Capacity can be added without restarting streams on unaffected workers.
- Losing one worker or zone meets event-gap and availability targets and never permits two accepted owners.
- A rolling software/model update completes with bounded reassignment and immediate rollback capability.
- A 1,000-camera multi-cell soak passes before the final scale phase.

### Phase 9 — 5,000-camera validation and production readiness (4–5 weeks plus seven-day soak)

#### Objectives

- Prove SLOs, correctness, recovery, security, and operational readiness at target scale with production reserve.

#### Test matrix

**P9.1 Scale and endurance**

- Ramp 250 -> 1,000 -> 2,500 -> 5,000 cameras using real streams where possible and the Rust RTSP simulator for the remainder.
- Run 24-hour, 72-hour, and seven-day soaks; inspect memory, file descriptors, NVMe growth, decoder/inference stability, GPU throttling, event lag, and report delay.
- Generate realistic event/evidence distributions, not only empty repeated streams.

**P9.2 Failure injection**

- Kill a pipeline, worker pod, GPU node, assignment leader, Redpanda broker/quorum member, ClickHouse replica, object-store endpoint, Gemma replica/pool, and a complete cell/region.
- Simulate reconnect storms, packet loss/jitter, corrupt video, time drift, DNS/TLS/secret failures, full local disk, broker outage, object-store outage, duplicate uploads, and delayed messages.
- Verify documented degradation priority and recovery without count inflation.

**P9.3 Scheduled workload**

- Close 5,000 camera windows together, apply stable offsets, mix late events/corrections, and overload routine and escalation Gemma queues.
- Verify 99.5% report-generation success and completion-by-minute-ten target, with valid fallback reports counted separately.

**P9.4 Security and compliance**

- Complete penetration testing, dependency/container review, tenant-isolation tests, credential/log leak tests, encryption verification, presigned URL expiry tests, audit review, retention expiration, and deletion exercises.

**P9.5 Operational readiness**

- Run restore tests for PostgreSQL, ClickHouse, topic configuration/offset strategy, object manifests, and model registry metadata.
- Conduct on-call game days from alerts through runbooks and post-incident review.
- Finalize dashboards, alerts, capacity forecasts, ownership, escalation contacts, change management, and support handoff.

#### Exit gate

- Camera ingest availability is at least 99.9%, detection freshness is under 5 seconds, event freshness under 10 seconds, and report SLOs meet the agreed measurement definition under target load.
- All critical failure scenarios recover within approved RTO/RPO and preserve logical counts/event lineage.
- Seven-day soak has no unexplained resource growth, unbounded backlog, or critical alert.
- Security, privacy, licensing, model accuracy, SRE, and product owners sign the production readiness review.

## 8. Cross-cutting workstreams

### 8.1 Model and dataset lifecycle

This work starts in Phase 0 and continues throughout delivery:

1. Create immutable train/validation/test dataset versions with provenance, consent, class map, site/camera split, and leakage controls.
2. Establish a COCO-pretrained YOLO26s baseline on the held-out domain set.
3. Label domain classes and hard negatives across all camera categories and conditions.
4. Fine-tune, validate per class/scenario, export ONNX, build architecture-specific FP16 engines, and verify numerical/accuracy parity.
5. Introduce INT8 only after a representative calibration set proves accuracy remains within approved per-class tolerances.
6. Register model ID, dataset/class-map versions, input size, precision, runtime/GPU compatibility, engine hash, parser version, validation report, and release state.
7. Shadow candidate models, then canary 1%, 5%, 20%, 50%, and 100%, with automated stop/rollback thresholds.
8. Retain original lineage on historical events and reports after rollback.

### 8.2 Security and tenant isolation

- Apply tenant context at API authorization, message production/consumption, database queries, object keys, presigned URLs, metrics/log access, and support tools.
- Retrieve RTSP credentials just in time through workload identity; never persist resolved secrets in PostgreSQL, messages, logs, traces, crash dumps, or config maps.
- Use TLS externally and mTLS internally, short-lived identities, Kubernetes network policies, private object buckets, public-access blocks, encryption at rest, and audited administrative actions.
- Treat future face/re-identification work as a separate product and legal review, not an extension of ordinary event metadata.

### 8.3 Observability and SLOs

- Emit service build/config/model versions on metrics and structured events.
- Build camera, pipeline, GPU, event, broker/spool, evidence, aggregation, Gemma, report, and tenant-usage dashboards as each component lands.
- Trace sampled flows from assignment through report while avoiding image bytes and secrets.
- Alert on user-visible SLOs and leading indicators: last-frame age, freshness, accepted-owner conflicts, dropped frames, spool/disk pressure, oldest queue age, report completion delay, unsupported claims, and fallback rate.
- Maintain synthetic cameras and hourly canary reports in every production region.

### 8.4 Testing pyramid

- Unit: geometry, track states, ID generation, dedupe, leases, backoff, windows, claims, retention, and authorization.
- Property/fuzz: line/zone geometry, Protobuf decode, DeepStream metadata conversion, timestamp boundaries, canonical hashes, and spool recovery.
- Contract: every producer against every supported schema version and every external API against OpenAPI fixtures.
- Integration: RTSP -> inference -> event -> broker -> ClickHouse/object storage and facts -> Gemma/fake -> validator -> report.
- Accuracy: separate metrics for detection, tracking, each deterministic event, evidence quality, Gemma semantic tasks, and report factuality.
- Resilience/load: simulator-driven faults, replay, infrastructure outage, reconnect storms, top-of-hour scheduling, and long soaks.

## 9. Initial repository layout

Create only deployable services that have an independent scaling, failure, or security boundary. Start with the following; split further only when measurements require it.

```text
vision-platform/
├── Cargo.toml
├── rust-toolchain.toml
├── crates/
│   ├── contracts/
│   ├── types/
│   ├── config/
│   ├── auth/
│   ├── telemetry/
│   ├── redpanda-client/
│   ├── clickhouse-client/
│   ├── object-store/
│   ├── video-pipeline/
│   ├── deepstream-sys/
│   ├── tracking/
│   ├── event-engine/
│   ├── evidence-selector/
│   ├── gemma-client/
│   └── report-validator/
├── services/
│   ├── control-api/
│   ├── assignment-controller/
│   ├── stream-worker/
│   ├── segment-worker/
│   ├── event-persister/
│   ├── window-coordinator/
│   ├── enrichment-worker/
│   ├── report-worker/
│   └── query-api/
├── proto/
├── database/postgres/
├── database/clickhouse/
├── models/manifests/
├── deploy/helm/
├── deploy/argocd/
├── deploy/keda/
├── tools/
│   ├── camera-load-generator/
│   ├── rtsp-replayer/
│   ├── benchmark-runner/
│   └── report-evaluator/
└── docs/
    ├── adr/
    ├── architecture/
    ├── runbooks/
    ├── schemas/
    └── model-releases/
```

Initially combine camera registry, policy management, upload-session creation, reports/evidence access, and operations endpoints in `control-api`. Split them only if their load, release cadence, or security boundaries diverge. Likewise, `report-worker` should own fact retrieval, prompt assembly, validation, fallback, and persistence while those stages share one queue/SLO; internal crates preserve boundaries without premature network services.

## 10. Contract and data checklist

Every event/message schema must define:

- Immutable message/logical entity ID and schema version.
- Tenant, site, camera, region/cell, and current assignment generation.
- Source event time, processing time, clock offset estimate, and timestamp quality.
- Trace/correlation/causation ID.
- Producer service/build/config version.
- Detector, engine, parser, tracker, policy, prompt, validator, and model versions where applicable.
- Retry attempt/original message identity without changing the logical ID.
- Partition key and idempotent sink key.

Storage design rules:

- PostgreSQL contains transactional control/report status, never per-frame detections.
- ClickHouse contains logical raw events/tracks/telemetry/facts and versioned aggregates; duplicate safety is verified before materialized rollups are enabled.
- Object manifests are immutable and written after their referenced objects succeed.
- Retention is tenant/jurisdiction policy, enforced by lifecycle automation and tested deletion workflows.
- The event bus stores references, never media content or resolved credentials.

## 11. Release environments and promotion

| Environment | Purpose | Scale/data |
|---|---|---|
| Local CPU | API, contracts, rules, aggregation, fake model | Synthetic only |
| GPU integration | Native pipeline and engine compatibility | 1–16 recorded/live test streams |
| Staging cell | Full vertical slice and fault tests | 16–250 representative cameras |
| Pre-production | Capacity, regional behavior, security, soak | 250–1,000 cameras |
| Scale environment | 5,000-camera qualification | Synthetic plus approved representative feeds |
| Production canary | Service/model progressive rollout | 1%, then staged expansion |

Artifacts progress by immutable digest. Configuration, schema, database migration, service image, TensorRT engine, and Gemma/prompt releases are promoted independently but recorded together in deployment manifests. Every rollout has automated health/accuracy thresholds and a tested rollback path.

## 12. Suggested milestone plan

| Milestone | Approx. week | Demonstrable outcome |
|---|---:|---|
| M0 Risk baseline | 3 | One-camera native vertical spike, decodable clip, dedupe and lease proofs |
| M1 Engineering foundation | 6 | Buildable workspace, contracts, CI, local stack |
| M2 Control plane | 10 | Safe registration, policy, assignment, reassign, and drain |
| M3 Vision node | 16 | 72-hour bounded multi-camera YOLO/tracking pipeline with measured capacity |
| M4 Deterministic analytics | 21 | Validated counts/events/evidence without Gemma |
| M5 Durable data plane | 25 | Broker/object failures replay without logical duplication |
| M6 Deterministic hourly product | 29 | 5,000 scheduled fact/template reports meet timing in simulation |
| M7 Grounded Gemma product | 34 | Selective enrichment and factual reports with automatic fallback |
| M8 Multi-cell platform | 38 | 1,000-camera regional soak and non-disruptive scale/rollout |
| M9 Production qualification | 40+ | 5,000-camera failure matrix and seven-day soak approved |

Teams can shorten calendar time by overlapping Phase 2 control APIs, Phase 3 video internals, data/model preparation, and environment automation after Phase 1 contracts stabilize. Phase exit gates must not be skipped to meet dates.

## 13. Production readiness checklist

### Product and correctness

- Per-class/scenario detection, tracking, and event thresholds approved.
- Report fact document is reproducible; counts remain unchanged under replay.
- Evidence is relevant, decodable, authorized, retained, and deletable.
- Late reports are versioned and consumers are notified of revisions.

### Reliability and scale

- Safe capacity uses measured 60–70% vision utilization and 20–30% headroom.
- Worker/zone/cell failure, reconnect storm, broker outage, object outage, Gemma outage, and full-disk behavior pass.
- Seven-day soak has bounded memory, descriptors, disk, queues, and lag.
- Backup/restore and model/service rollback are demonstrated, not documented only.

### Security and compliance

- Tenant isolation, OIDC/RBAC, mTLS, secret rotation/redaction, encryption, audit, retention, and deletion tests pass.
- YOLO26 and Gemma licensing/use terms are approved for the exact deployment.
- No raw media crosses residency boundaries or enters telemetry/message systems unexpectedly.

### Operations

- SLO dashboards and actionable alerts are live in every region.
- Runbooks have been exercised by the on-call team.
- Capacity/cost forecasts, quotas, escalation ownership, change process, and incident response are approved.
- All running artifacts and generated records expose complete build/config/model lineage.

## 14. Immediate next actions (first 10 working days)

1. Name owners for product, Rust platform, video/GPU, ML, data, SRE, and security workstreams.
2. Approve the target deployment region, GPU candidates, camera pilot cohort, and source-footage access.
3. Resolve YOLO26 licensing and pin exact YOLO26, Gemma, vLLM, DeepStream, TensorRT, CUDA, driver, and OS candidates.
4. Capture 20–50 representative camera samples and produce an anonymized, versioned evaluation manifest.
5. Run the one-stream YOLO/TensorRT/DeepStream/Rust metadata spike and the Gemma/vLLM structured-output spike.
6. Prototype encoded keyframe-aware ring fragments and pre/post event clip extraction.
7. Prototype the PostgreSQL generation-fenced camera lease under concurrent claims and controller restart.
8. Prototype Redpanda replay into ClickHouse and decide the exact no-double-count rollup strategy.
9. Approve `v1` logical entities, topic names, partition keys, ID formulas, and event-time semantics.
10. Review the first benchmark/cost/SLO report and either confirm the architecture baseline or revise the affected ADRs before building services.

The first implementation increment should therefore be a risk-retirement vertical slice, not a broad set of empty microservices. Its successful result is one camera whose ownership is fenced, whose stream is continuously decoded and inferred, whose deterministic event produces valid evidence, whose message survives replay without double counting, and whose fact document can generate both a validated Gemma report and a deterministic fallback.
