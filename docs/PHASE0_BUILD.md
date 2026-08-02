# Phase 0 Build Details

## Delivered boundary

This phase concentrates on the video-processing application boundary. MongoDB remains owned by the backend service and is not referenced by this code. Kafka is an optional outbound sink with configurable brokers/topics; the default is a no-op sink, making the whole flow locally testable.

```text
Browser UI
   -> Rust Axum API
      -> local upload / sample / HTTP(S) or RTSP reference
      -> DetectorBackend
           -> deterministic simulator
           -> YOLO26 development command
           -> future DeepStream/TensorRT worker (same output contract)
      -> deterministic track and event engine
      -> immutable fact report
      -> LM Studio Gemma 4 enrichment
           -> strict JSON parse and factual validation
           -> deterministic fallback on any failure
      -> no-op sink (default) or Kafka sink (optional)
```

## What was built

### Rust service

- `src/api.rs`: HTTP routes, bounded streaming multipart upload, SHA-256 metadata, static UI, media serving, and consistent JSON errors.
- `src/store.rs`: in-memory job/upload catalogue, async execution, source resolution, request validation, and network-source credential redaction.
- `src/pipeline.rs`: backend selection, bounded external process execution, observation validation, rules, reporting, Gemma fallback, and sink publication.
- `src/event_engine.rs`: confirmation threshold, deterministic grouping, point-in-polygon zones, directional line crossings, dwell events, stable UUIDv5 event IDs, track summaries, and fact reports.
- `src/gemma.rs`: LM Studio `/v1/models` discovery and `/v1/chat/completions`, strict report JSON, event-reference validation, numeric-claim validation, and bounded timeout.
- `src/sink.rs`: default no-op implementation and optional idempotent `rust-rdkafka` producer.
- `src/domain.rs`: API/pipeline contracts with normalized geometry.

### Testing UI

The UI is compiled into the Rust binary and requires no Node/npm build. It provides:

- Service, Gemma, Kafka, and detector capability status.
- Built-in sample, video upload/drop, HTTP/HTTPS, and RTSP source modes.
- Per-job stream monitoring duration followed by automatic insight generation.
- Simulator/YOLO26 backend selection and detector cadence.
- Editable detector observations and analytics policy JSON.
- Job status polling, video preview, metrics, grounded report, fallback reason, event timeline, confirmed-track table, raw JSON, and run history.
- Responsive desktop/mobile layout and offline-safe local assets.

### Model adapters

- `tools/yolo26_runner.py` runs YOLO26 tracking over a file or bounded HTTP(S)/RTSP interval and emits normalized observations. HTTP(S) uses an isolated bundled FFmpeg process; the detector result is emitted as a framed record so third-party console output cannot corrupt the Rust/JSON boundary.
- `tools/export_yolo26.py` exports ONNX/TensorRT artifacts and writes an artifact manifest/hash.
- `runtime/deepstream/` documents the production graph and contains initial `nvinfer` and NvDCF templates.
- Gemma calls the user's LM Studio server directly. The preferred model ID is discovered rather than hard-coded because LM Studio model identifiers depend on the loaded artifact.

## Core data contract

Detector backends return owned, normalized observations:

```json
{
  "model": "yolo26s.pt",
  "observations": [
    {
      "frame_time_ms": 2400,
      "track_id": "42",
      "class_name": "person",
      "confidence": 0.94,
      "bbox": [0.12, 0.21, 0.18, 0.52]
    }
  ]
}
```

`bbox` is `[x, y, width, height]`, normalized to the source frame. All values must remain within the frame. This contract prevents model-specific tensors or native DeepStream pointers from leaking into rules, APIs, UI, or Kafka.

## Deterministic processing

1. Validate confidence, identity, class, and normalized geometry.
2. Group observations by local track ID and sort by source-frame time.
3. Discard tracks below the policy confirmation threshold.
4. Derive zone entry/exit and restricted-zone events from bbox centers.
5. Derive directional line crossings from signed-side changes.
6. Derive dwell events from confirmed track duration.
7. Create event IDs from job, event type, track, time, and geometry reference using UUIDv5. Replaying a job input creates the same event IDs.
8. Produce the authoritative report facts before any Gemma call.

No bounding-box count is treated as a unique person/vehicle count.

## Gemma grounding and fallback

The report prompt supplies only the deterministic report and allowed event IDs. The service then verifies:

- Valid JSON and required fields.
- Confidence in `[0,1]`.
- Every returned event ID exists in the deterministic fact document.
- Every numeric token in Gemma's headline/summary exists in the authoritative headline/summary.

Any connection, timeout, model, JSON, reference, or claim-validation failure returns the deterministic report and records `gemma.fallback_reason`. A model outage therefore cannot fail the video job.

## Kafka integration boundary

The default binary has no Kafka dependency in its runtime path. A Kafka build uses:

- `enable.idempotence=true`
- `acks=all`
- zstd compression
- job ID as message key
- versioned JSON envelope with a message ID and logical payload kind

This is intentionally an integration-ready placeholder. Once the backend service supplies Protobuf schemas, authoritative topic names, authentication/TLS, partition keys, and delivery semantics, replace the provisional JSON envelope behind the `EventSink` trait without altering the pipeline.

## Phase 0 limitations and next production work

- Jobs and their results are in memory. Restarting the service clears the catalogue; uploaded files remain in `VISN_DATA_DIR` and can be cleaned manually. Mongo persistence waits for the backend contract.
- The development YOLO runner is Python/Ultralytics. The production online path still needs the target-host DeepStream metadata adapter and validated YOLO26 custom parser.
- Evidence frame/crop extraction, an encoded NVMe ring buffer, clip remuxing, multi-stream batching, and pipeline-group isolation belong to the NVIDIA-host follow-on slice.
- The current job API is a bounded batch/network-stream validation flow, not a long-lived camera supervisor.
- The simulator proves rules and UI plumbing; it is not a detector accuracy test.
- Kafka payloads remain provisional until the backend team freezes the contract.

These limitations are surfaced rather than hidden behind untested GPU code. The immediate next gate is to run the same detector-output contract on the selected NVIDIA Linux host and retain this local path for deterministic regression tests.
