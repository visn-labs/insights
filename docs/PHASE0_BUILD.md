# Phase 0 Build Details

## Delivered boundary

This phase concentrates on the video-processing application boundary. MongoDB remains owned by the backend service and is not referenced by this code. Kafka is an optional outbound sink with configurable brokers/topics; the default is a no-op sink, making the whole flow locally testable.

```text
Browser UI
   -> Rust Axum API
      -> local upload / sample / HTTP(S) or RTSP reference
      -> DetectorBackend
           -> deterministic simulator
           -> persistent YOLO26 multi-camera worker
                -> one shared model, bounded latest-frame ingress
                -> batched inference, camera-isolated ByteTrack state
           -> isolated YOLO26 command fallback
           -> future DeepStream/TensorRT worker (same output contract)
      -> deterministic track and event engine
      -> bounded representative-frame selection
      -> LM Studio visual scene description
      -> immutable fact report
      -> LM Studio Gemma 4 enrichment
           -> strict JSON parse and factual validation
           -> deterministic fallback on any failure
      -> no-op sink (default) or Kafka sink (optional)
```

## Local V1 retrieval-memory extension

The retrieval-memory extension runs beside the Phase 0 YOLO path. `tools/event_memory_runner.py` first records a bounded encoded source artifact, retrieves analysis frames at `observer_fps`, uses adaptive luma/histogram novelty plus maximum-duration boundaries, and creates event thumbnails and optional MP4 evidence derivatives. Inter-frame codecs may still require work to advance across skipped frames. `src/memory.rs` enriches a bounded, visually diverse set of high-priority events through LM Studio and provides local recall plus optional VLM synthesis.

Completed manifests live under `VISN_DATA_DIR/memory/manifests`; evidence lives under `VISN_DATA_DIR/memory/<job>/<camera>`. The local manifest is disposable development persistence. It does not define the future MongoDB contract.

## What was built

### Rust service

- `src/api.rs`: HTTP routes, bounded streaming multipart upload, SHA-256 metadata, static UI, media serving, and consistent JSON errors.
- `src/store.rs`: in-memory job/upload catalogue, async execution, source resolution, request validation, and network-source credential redaction.
- `src/pipeline.rs`: backend selection, bounded external process execution, observation validation, rules, reporting, Gemma fallback, and sink publication.
- `src/detector_worker.rs`: lifecycle supervision and restart of the shared YOLO worker, bounded per-session event channels, request cancellation, protocol validation, credential-safe diagnostics, idle reaping, and pre-VLM model draining.
- `src/event_engine.rs`: confirmation threshold, deterministic grouping, point-in-polygon zones, directional line crossings, dwell events, stable UUIDv5 event IDs, track summaries, and fact reports.
- `src/gemma.rs`: LM Studio native model discovery/loading, OpenAI-compatible `/v1/chat/completions`, multimodal representative-frame descriptions, strict report JSON, event-reference validation, numeric-claim validation, and bounded timeout.
- `src/sink.rs`: default no-op implementation and optional idempotent `rust-rdkafka` producer.
- `src/domain.rs`: API/pipeline contracts with normalized geometry.

### Testing UI

The UI is compiled into the Rust binary and requires no Node/npm build. It provides:

- Service, VLM, Kafka, and detector capability status.
- Built-in sample, video upload/drop, HTTP/HTTPS, and RTSP source modes.
- Per-job stream monitoring duration followed by automatic insight generation.
- Multi-camera HTTP cluster jobs with bounded concurrency and partial-failure isolation.
- Camera-wise physical-scene descriptions and an aggregated cluster view summary.
- Person tracklet appearance prototypes, explicit overlap/topology gates, Hungarian one-to-one assignment, ambiguity preservation and deterministic global IDs.
- Simulator/YOLO26 backend selection and detector cadence.
- Editable detector observations and analytics policy JSON.
- Job status polling, video preview, metrics, grounded report, fallback reason, event timeline, confirmed-track table, raw JSON, and run history.
- Responsive desktop/mobile layout and offline-safe local assets.

### Model adapters

- `tools/yolo26_worker.py` is the default local path. It loads YOLO once, decodes cameras concurrently, retains one live pending frame per camera, batches ready frames, and uses a dedicated ByteTrack instance per camera. `tools/yolo26_runner.py` is the isolated fallback. HTTP(S) uses bundled FFmpeg subprocesses; framed records keep third-party output outside the Rust/JSON protocol.
- `src/cluster.rs` implements the local V1 multi-camera association engine. It does not infer topology and never permits Gemma to change identity state.
- `tools/export_yolo26.py` exports static-by-default Core ML, LiteRT/TFLite, ONNX, OpenVINO, and TensorRT artifacts with precision compatibility checks, streaming hashes, and reproducibility manifests. `tools/compare_yolo26_backends.py` measures isolated peak RSS/latency and same-frame agreement, with optional labeled mAP validation. See [the backend optimization guide](BACKEND_INFERENCE_OPTIMIZATION.md).
- `runtime/deepstream/` documents the production graph and contains initial `nvinfer` and NvDCF templates.
- VLM calls go to the user's LM Studio server directly. The UI supports `prism-ml/bonsai-27b`, `moondream2`, `qwen/qwen3.6-35b-a3b`, `google/gemma-4-26b-a4b-qat`, and `zai-org/glm-4.6v-flash`. For explicit selections, the service calls LM Studio's native `/api/v1/models/load` endpoint and then uses the same ID with the OpenAI-compatible chat endpoint.

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

View description uses one representative JPEG selected from sampled frames using sharpness, contrast and exposure quality. The frame is resized to at most 960 pixels on its longest side, remains in process memory, and is not returned by the API. The multimodal prompt is limited to the visible setting and persistent layout and explicitly forbids identity, intent, event and object-count claims. Unsupported vision input or invalid JSON produces an explicit detector-context fallback without failing the camera pipeline.

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
- An encoded NVMe ring buffer and target-specific DeepStream pipeline-group isolation belong to the NVIDIA-host follow-on slice. The local worker now provides bounded multi-stream batching, while the production graph must reproduce and validate that behavior with GPU-native decode and metadata.
- The current job API is a bounded batch/network-stream validation flow, not a long-lived camera supervisor.
- The simulator proves rules and UI plumbing; it is not a detector accuracy test.
- Kafka payloads remain provisional until the backend team freezes the contract.

These limitations are surfaced rather than hidden behind untested GPU code. The immediate next gate is to run the same detector-output contract on the selected NVIDIA Linux host and retain this local path for deterministic regression tests.
