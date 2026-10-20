# Phase 0 HTTP API

All endpoints are local and unauthenticated in this Phase 0 lab build. Add service authentication before binding beyond loopback.

## Runtime

### `GET /healthz`

Returns service status, version, and active sink.

### `GET /api/v1/capabilities`

Returns runtime capabilities and whether Kafka was compiled/enabled.

### `GET /api/v1/models`

Proxies LM Studio model discovery as a safe availability response. The response includes `configured_vlms`, the UI-supported VLM IDs, and `models`, what LM Studio's native API or OpenAI-compatible API reported. LM Studio failure returns `available: false` rather than failing the service.

### `GET /api/v1/sample`

Returns the built-in observations and policy used by the UI editors.

## Uploads

### `POST /api/v1/uploads`

Multipart request with one field named `video`. Returns local upload ID, original filename, MIME type, size, and SHA-256. File bytes never pass through Kafka.

```bash
curl -F 'video=@./sample.mp4' http://127.0.0.1:8080/api/v1/uploads
```

### `GET /api/v1/uploads`

Lists uploads known to this running process.

### `GET /api/v1/uploads/{id}/content`

Streams the local media object and supports browser video preview/range behavior through `ServeFile`.

## Jobs

### `POST /api/v1/jobs`

Built-in sample request:

```json
{
  "name": "API smoke test",
  "source": "sample",
  "backend": "simulator",
  "detector_fps": 5.0,
  "monitor_duration_secs": 120,
  "gemma_enabled": false,
  "vlm_model": "google/gemma-4-26b-a4b-qat",
  "observations": [],
  "policy": {}
}
```

Upload source:

```json
{"source":{"upload":{"upload_id":"0198..."}}}
```

RTSP source:

```json
{"source":{"rtsp":{"uri":"rtsp://user:password@host/live"}}}
```

HTTP/HTTPS stream source (for example HLS or MJPEG):

```json
{
  "name": "HTTP camera window",
  "source": {"http": {"uri": "https://camera.example/live.m3u8?token=secret"}},
  "backend": "yolo26_command",
  "detector_fps": 5,
  "monitor_duration_secs": 300,
  "gemma_enabled": true,
  "vlm_model": "zai-org/glm-4.6v-flash",
  "observations": [],
  "policy": {}
}
```

`monitor_duration_secs` controls the wall-clock monitoring window for HTTP/HTTPS and RTSP streams. It must be between 1 and the service's `VISN_MAX_ANALYSIS_SECS` ceiling. When the window ends, detection output is analyzed and the deterministic/Gemma insight report is generated.

When `gemma_enabled` is true, the result also includes `view_description`. One representative video frame is sent to the selected `vlm_model` for physical-scene and layout description. Before the chat call, the service attempts to load that model through LM Studio's native `/api/v1/models/load` endpoint. If the model has no vision support, cannot be loaded, or returns invalid JSON, `generated_by_model` is false and `fallback_reason` explains why; detection and event processing still succeeds.

The `uri` must return decodable video bytes directly. Plain `http://` and TLS-backed `https://` are handled identically by the API; HTTP media is decoded by the bundled FFmpeg runtime rather than by the Rust HTTP client.

Network credentials and query tokens are used only by the execution task. Job/list responses replace the URI with its scheme plus `***`, and the command adapter passes the URI over standard input rather than exposing it in process arguments.

### `GET /api/v1/jobs`

Lists newest jobs first.

### `GET /api/v1/jobs/{id}`

Returns queued/running/completed/failed state and the final pipeline result.

## Multi-camera cluster jobs

### `POST /api/v1/cluster-jobs`

Overlapping synchronized cameras:

```json
{
  "name": "Entrance cluster window",
  "cluster_id": "entrance-cluster",
  "cameras": [
    {
      "camera_id": "camera-a",
      "label": "Main entrance",
      "uri": "http://camera-a/live.m3u8",
      "overlap_group": "entrance-overlap",
      "clock_offset_ms": 0,
      "policy": {}
    },
    {
      "camera_id": "camera-b",
      "label": "Side entrance",
      "uri": "http://camera-b/live.m3u8",
      "overlap_group": "entrance-overlap",
      "clock_offset_ms": 0,
      "policy": {}
    }
  ],
  "topology": [],
  "association": {},
  "detector_fps": 3,
  "monitor_duration_secs": 60,
  "gemma_enabled": true,
  "vlm_model": "google/gemma-4-26b-a4b-qat"
}
```

Non-overlapping cameras use directed edges:

```json
{
  "topology": [
    {
      "edge_id": "main-to-lobby",
      "source_camera_id": "camera-a",
      "target_camera_id": "camera-c",
      "edge_type": "transition",
      "minimum_travel_ms": 3000,
      "maximum_travel_ms": 20000,
      "confidence": 0.9
    }
  ]
}
```

The service never creates all-to-all edges. Without a shared `overlap_group` or explicit topology, local identities remain separate while camera-wise and aggregate cluster insights are still generated. Camera URLs are redacted from returned job records.

Association defaults:

```json
{
  "minimum_appearance_similarity": 0.70,
  "provisional_threshold": 0.75,
  "final_threshold": 0.90,
  "overlap_tolerance_ms": 1500
}
```

### `GET /api/v1/cluster-jobs`

Lists cluster runs newest first.

### `GET /api/v1/cluster-jobs/{id}`

Returns camera results, partial failures, association decisions, cluster identity records, an aggregated cluster `view_description`, and the cluster report. Every successful `camera_results[].pipeline` also contains its own `view_description`. Singleton records from unrelated cameras must not be interpreted as a unique-person count.

## Error format

```json
{"error":"human-readable validation or processing error"}
```

## V1 retrieval memory

### `POST /api/v1/memory-jobs`

Records and indexes a bounded interval for one or more cameras. It accepts the backend camera keys exactly as currently supplied; responses serialize stable snake_case names and redact `live_url`.

```json
{
  "name": "Scrapyard cluster memory",
  "cluster_id": "scrapyard-cluster",
  "cameras": [
    {
      "camera_id": "yard-a",
      "liveurl": "http://camera.example/mjpg/video.mjpg",
      "Country": "Sweden",
      "Country code": "SE",
      "Region": "Hallands Lan",
      "City": "Halmstad",
      "Latitude": 56.67446,
      "Longitude": 12.85676,
      "ZIP": 10116,
      "Timezone": "+01:00",
      "Manufacturer": "Axis",
      "description": "An outside view of a scrapyard with ships and caravans"
    }
  ],
  "monitor_duration_secs": 15,
  "observer_fps": 1.0,
  "vlm_enabled": true,
  "vlm_model": "google/gemma-4-26b-a4b-qat"
}
```

`observer_fps` is limited to 0.1–5 FPS. Source evidence is retained even though only sparse frames are decoded for indexing. Exact duplicate live URLs are rejected within one request.

### `GET /api/v1/memory-jobs`

Lists in-memory and locally rehydrated memory jobs.

### `GET /api/v1/memory-jobs/{id}`

Returns indexing status, partial camera failures, compute counts, camera metadata, adaptive events, descriptions, and evidence URLs.

### `GET /api/v1/memory-events/{id}/thumbnail`

Returns the representative JPEG.

### `GET /api/v1/memory-events/{id}/clip`

Returns the browser-playable event MP4 derivative. This is for convenient review; source evidence remains authoritative.

### `GET /api/v1/memory-events/{id}/source`

Returns the recorded source Matroska artifact.

### `POST /api/v1/memory-query`

```json
{
  "query": "Show the periods with the most movement and describe what is visible",
  "cluster_id": "scrapyard-cluster",
  "camera_ids": [],
  "limit": 10,
  "vlm_enabled": true,
  "vlm_model": "zai-org/glm-4.6v-flash"
}
```

Local metadata/description recall always runs. With `vlm_enabled`, LM Studio receives only the shortlisted event records to produce an evidence-grounded summary and ordering. Failure falls back to local recall and is reported in `fallback_reason`.


### Retrieval readiness and report confidence

`GET /api/v1/retrieval-status` returns `configured`, `available`, and a user-facing `message`. A reachable worker does not prove model inference; successful `POST /api/v1/memory-query` returns `retrieval_mode`, coverage notes, and `retrieval_provenance`.

Rule-generated `Report.confidence` is now nullable: `null` means no calibrated overall confidence is available. Model reports still require a numeric value in [0, 1]. Cluster association report scores, when present, are explicitly described as mean association scores. Track summaries and events use a confidence-weighted class consensus; class changes are disclosed in quality notes. Event detector scores remain tied to their original observations.

`POST /api/v1/memory-query` also accepts optional `job_id` (UUID). When supplied, it restricts retrieval to that recording before cluster/camera/time filters and model calls. Empty candidate sets return `not_run_empty_index` or `not_run_filtered_empty`, with a diagnostic in `retrieval_notes`; no model is called in either case.

## Experiment 2: ordered visual interpretation

`POST /api/v1/memory-jobs` and `POST /api/v1/memory-query` accept `temporal_enabled` (boolean, default `false`). When enabled, sequence interpretation takes precedence over legacy `vlm_enabled` caption/synthesis. Retrieval settings and filters are unchanged.

Memory events include `source_duration_ms` and optional `interpretation`. Query responses include `interpretations` and `interpretation_notes`. Each interpretation contains `event_id`, `status` (`supported`, `not_observed`, or `uncertain`, a model judgment), `answer`, source-relative `start_ms`/`end_ms`, `sampled_timestamps_ms`, ordered `frames` (`timestamp_ms`, JPEG data URL `image`), `observations` (`frame_index`, `timestamp_ms`, `target_visibility` = `visible|absent|unclear`, `description`), `limitations`, `provenance`, `cached`, and `elapsed_secs`. Sample timestamps are seek targets, not precise action boundaries.

The local Qwen worker exposes `POST /interpret` for Rust, using the same evidence-root restrictions and serialized inference lock as retrieval. Query-time interpretation covers at most three ranked events, expands each interval by up to two seconds per side, clamps to source duration, and caps each window at twenty seconds. Indexing uses the existing priority-event budget. Unavailable/invalid interpretation preserves retrieval results with an explicit note. No model claim updates deterministic tracking facts or retrieval scores.

## Experiment 3: linked entity memory

- `POST /api/v1/memory-jobs`: optional `long_memory_enabled` (default `false`) runs tracking on the retained recording and builds source-cited histories. Tracking failure leaves indexed clips intact and adds a camera quality note.
- `POST /api/v1/memory-jobs/{id}/long-memory`: rebuild a completed recording's entity memory from retained source. Returns the updated job. The request may take time; errors preserve the previous manifest. Rebuilds are serialized.
- `MemoryCameraResult.long_memory`: optional object containing `version`, `source_sha256`, `detector_model`, `detector_fps`, `status`, `entities`, `links`, and `notes`.
- Entities have stable `entity_id`, scoped `job_id`, `camera_id`, `track_id`, `class_name`, `observations`, and `uncertainty`. Each observation has `observation_id`, evidence `event_id`, source-relative `timestamp_ms`, `kind`, `description`, optional normalized `bbox`, and optional `detector_confidence` (not identity confidence).
- Links contain `from`/`to` observation IDs, `entity_id`, `relation: same_tracker_order`, and `gap_ms`. These express tracker association and observation ordering only.
- `POST /api/v1/memory-query`: optional `long_memory_enabled` and `entity_id`. Response `long_memory` is null when disabled, otherwise contains `status`, `summary`, `entities`, `links`, `supporting_events`, and `notes`. Status is `tracker_linked`, `ambiguous_entities`, or `insufficient_evidence`. Ordinary `matches` and their ranking are unchanged.
- All existing filters apply to expansion; observation times are additionally filtered. At most 12 histories are returned, with an explicit truncation note. Cross-recording/camera identity and global chronology are not inferred.

See [experiment 3 validation and limitations](EXPERIMENT3_ENTITY_MEMORY.md).

### Entity-memory sampling version

Capabilities now include `entity_memory_version` (`entity-memory-v2-pts`). Graphs from previous detector sampling versions must be rebuilt; the UI explains this and suppresses outdated entity selections. Entity UUIDs include the memory version, recording, camera and local tracker ID. Embedding/interpretation caches also record the bounded decoder version; incompatible entries are not reused.

Query-focused interpretation uses an independent per-image target audit before comparing visible target frames. `not_observed` means absent from sampled images, not proof of absence throughout the recording. Action claims require two visible citations. Visibility contradictions abstain. Prompt/cache version: `independent-target-audit-v6`; visibility judgments remain unverified model outputs.

Supported temporal answers must cite at least two distinct encoded images as well as two visible target observations. The Python worker additionally abstains when cited pixels show no measurable change, and reapplies this check to cached model answers. The optional `--interpretation-api-url` backend accepts a loopback HTTP `/v1` endpoint, requires an explicit `--interpretation-revision`, and verifies response model identity. Returned provenance distinguishes `local-openai-chat-v1` from `pytorch-transformers`; local model snapshots are pinned content fingerprints supplied for the evaluated files.

### Combining temporal interpretation with an explicit entity

`POST /api/v1/memory-query` accepts `temporal_enabled: true` together with `entity_id` and optional `long_memory_enabled: true`. Retrieval is constrained to the selected entity's current graph, recording and camera, then ordinary query filters still apply. Unmatched entity/scope combinations yield no candidates. A changed source hash prevents interpretation with stale boxes; retrieval remains available with an interpretation failure note.

`interpretations[].frames[]` now additionally returns optional `decoded_timestamp_ms` (actual decoded PTS, distinct from the existing seek-target `timestamp_ms`) and `target_crop` (JPEG data URL). `provenance.track_grounding` identifies `explicit_entity` or `unconstrained`, the entity UUID and aligned frame indices. `tracker-pts-v1-no-interpolation` samples 2–8 selected detector observation times; ordinary interpretation still samples eight seek midpoints. Matching boxes tolerate at most 1 ms of timestamp rounding. Unsupported/missing target alignment cannot support an action claim. These fields do not verify tracker identity or model reasoning.

### Blind visual-state check

For prompt `blind-state-consistency-v9`, sequence responses may include `claimed_transition`. `provenance.state_verification` contains the gate `version`, `status` (`passed`, `rejected`, `not_applicable`), and `reason`; executed checks also contain `comparison`, `identity`, `reference_visible`, `before`, `after`, cited `frame_indices`, and derived `transition`. Tracker distance checks may include `tracker_area_ratio`.

A supported v9 response must have a passed gate, matching identity, a visible reference, and transition/citation agreement. Rejected checks return `uncertain` with retained images and observations. Passed checks are fallible model comparisons, not verified facts or confidence estimates. A model/transport failure still preserves retrieval results with an explicit interpretation failure note. Cached generations are checked again against their cited images.

### Selected-track visibility quality (v10)

For `track-visibility-quality-v10`, `provenance.selected_track_visibility_min_edge` records the minimum selected crop short edge (16 pixels). Missing/tiny crops produce unclear observations. Failed recognition of a selected crop does not establish scene-wide absence, so selected-entity ambiguity returns `uncertain` rather than `not_observed`, including on cached generation reads. Rust rejects v10 `not_observed` with `track_grounding.status: explicit_entity`. Unconstrained sampled-absence behavior is unchanged. The supported-answer state-proof contract applies to v9 and v10 alike.

### Source-detail crop provenance

For explicitly selected entities, the worker's default `--interpretation-crop-mode source` decodes source detail without upscaling (long edge ≤1920), crops only timestamp-aligned boxes, and caps model crop long edges at 768 by downsampling. `preview` is a comparison/historical-review mode. No new query parameter is required.

`provenance.crop_policy` records the cache policy/version. `provenance.crop_evidence` records `version`, `mode` (`source`, `preview`, `none`), `max_edge`, `no_upscale`, `model_crop_max_edge`, `context_sizes`, `detail_sizes`, `original_crop_sizes`, `crop_sizes` and `decoded_timestamps_ms`. Frames continue to expose the small full context as `image` and the selected detail as `target_crop`. Crop dimensions describe actual returned pixels; they do not measure confidence or prove identity. A context/detail timestamp mismatch fails interpretation and preserves retrieval via the existing failure note.

### Repeated-work cache instrumentation

`retrieval_provenance.work_cache` reports `enabled`, per-search `counters` (`decode_hits`, `ffmpeg_calls`, `decode_evictions`, `query_hits`, `query_calls`, `rerank_hits`, `rerank_calls`) and current `decode_bytes`, `decode_entries`, `query_entries`, `rerank_entries`. These are diagnostic measurements, not accuracy/confidence scores. Interpretation work occurs after retrieval and is not included in the per-search counters.

The worker caches lossless compressed frame bytes (32 MiB/128 entries), exact query vectors (32 entries) and finite rerank scores (256 entries) in RAM. Source validation remains active on hits, model revisions/settings partition entries, and callers receive fresh decoded images. `--disable-work-caches` bypasses these RAM caches without disabling existing persistent embedding/generation caches. Visual-state checks still run on cached generations.

Source fingerprints use a separate 256-path LRU registry. Every access validates scope and file metadata (device, inode, size, modification and change times); eviction only causes an exact SHA256 recomputation. A source that changes during hashing is rejected. Fingerprints are not video evidence or generated model facts.

### Experimental canonical vectors

The worker's `--vector-policy` defaults to `legacy-renormalize-v1`. The experimental `canonical-unit-v2` validates unit vectors without renormalizing them on cache reads and uses a separate `semantic-canonical-v2.sqlite3` database. Search provenance exposes `vector_policy`. Existing index entries remain available to the legacy profile; selecting the candidate can require new indexing. See the [Milestone A validation and replay guide](GENERAL_VIDEO_MILESTONE_A.md).

### Optional workflow diagnostics

Detailed Python telemetry defaults to off. Enable it with worker `--telemetry` or `VISN_WORKFLOW_TELEMETRY=1`; `--disable-telemetry` overrides the environment. Setting the environment on the Rust service separately enables observer-child diagnostics. Worker `/health` returns `telemetry_enabled` without loading models. Lightweight Rust stage timing logs remain available regardless of this switch.

Enabled successful queries may contain `retrieval_provenance.telemetry` and `interpretations[].provenance.telemetry`. Version `workflow-telemetry-v1` reports request/run IDs, operation/status, host elapsed/process CPU seconds, per-stage calls/failures/inclusive/exclusive host seconds and resource snapshots. The interpretation snapshot describes the whole `/interpret` request and is repeated per item; deduplicate by request ID and operation. It is not a per-clip measurement. These diagnostics are never model evidence, confidence or prompt/cache-key inputs.

Response snapshots have `final: false` and `report_scope: before_response_serialization`. Final `WORKFLOW_TRACE` JSON logs include response serialization/write and cleanup when reached. Rust passes its query UUID to both worker endpoints via `X-Visn-Query-ID`; direct worker callers may also pass `X-Visn-Run-ID`. Missing/invalid IDs become generated UUIDs. Observer traces use the memory-job UUID and are forwarded from successful child stderr into Rust logs; observer stdout is unchanged.

Stage times measure host wall time without accelerator synchronization. Nested inclusive values overlap. Process CPU and RSS high-water marks are process-wide; high-water marks are not request peaks. Current RSS is unavailable on macOS. MPS allocation is a point-in-time measurement, with no peak reported. Child decoders/external model servers are excluded. Do not sum RSS and accelerator memory on unified-memory hosts. Package 4 adds bounded interactive admission; admission spans now include queue wait. See [package 3 historical measurements](GENERAL_VIDEO_PACKAGE_3.md) and the current scheduling contract below.

### Durable visual preparation and inference admission

`POST /api/v1/memory-jobs/{id}/visual-index` submits all intervals of a completed recording to a durable background embedding task. It requires no body and returns 202 with task state. `GET` on the same endpoint returns `{jobs, scheduler}` for that recording. `POST /api/v1/memory-jobs/{id}/visual-index/{task_id}/cancel` cancels and `/retry` resumes cancelled/failed work. Cross-recording task actions are rejected. The Rust API derives evidence paths from stored manifests and forwards worker error statuses; an unavailable worker returns 503 without deleting recording evidence.

The worker exposes `POST /index-jobs` (`scope_id`, `events`), `GET /index-jobs?scope_id=...`, `GET /index-jobs/{id}` and corresponding `/cancel` and `/retry` POST actions. Events use the existing `event_id`, `source_path`, `start_ms` and `end_ms` contract and evidence-root restriction. Identical submissions deduplicate; changed source/profile inputs yield a new task. States are `queued|running|completed|failed|cancelled`. Task responses include counts (`total`, `completed`, `failed`, `pending`), bounded `item_errors`, embedding provenance, vector policy and `profile_current` (boolean or null before profile resolution). Completion means the requested interval artifacts committed successfully under the recorded profile, not verified action knowledge or exhaustive scene coverage.

Task progress persists separately from vectors in `index-jobs-<policy>-v1.sqlite3`. Interrupted tasks recover on worker restart; cancelled jobs require explicit retry. Cancellation is cooperative between intervals, so an in-flight interval may finish and retain its valid vector. Explicit retry preserves successful intervals and renews the unfinished work's attempt budget. Source/profile checks prevent stale completion, and checking a completed preparation again repairs missing/corrupt artifacts through queued work. Direct search remains compatible and can prepare missing intervals itself.

One Python inference slot is shared by search, interpretation and preparation. Defaults: eight waiting interactive requests (`--interactive-queue-limit`), 120 seconds admission wait (`--interactive-wait-secs`), eight unfinished background jobs (`--index-queue-limit`), two interval attempts (`--index-max-attempts`). Interactive operations normally take priority; a waiting background unit gets a turn after four admitted interactive operations. Background tasks yield between clips. Work already executing is not preempted. Full/timed-out interactive admission returns 503; background overflow returns 429. Worker errors include `Retry-After: 2`. Query fallback remains explicit and no verification/sampling reduction is performed to meet load.

Worker `/health` includes `background_indexing` and `scheduler` with `active`, `interactive_waiting`, `background_waiting`, `interactive_limit`, `interactive_burst` and policy. Metadata progress/cancellation bypass the inference gate. This local scheduler remains available with cross-process coordination disabled. See [package 4 validation, limitations and rollback](GENERAL_VIDEO_PACKAGE_4.md).

### Optional cross-process model residency

Set `VISN_RESOURCE_DIR` to the same absolute local directory in Rust and all participating Python workers. The default is unset. A bounded FIFO file-lock queue admits one resident process group; warm weights can stay loaded until a waiter appears. An owner must finish its current unit and unload before releasing the lock. Persistent detector tracker sessions survive weight reloads. Isolated detector children inherit ownership until exit. This does not set a hard memory-byte ceiling, manage unrelated applications or change model precision/sampling.

Defaults are 32 process waiters (`VISN_RESOURCE_QUEUE_LIMIT`, valid 1–256) and 180 seconds admission wait (`VISN_RESOURCE_WAIT_SECS`, valid 0–3600). Rust uses integer seconds; Python accepts fractional deadlines. No deadline expires active inference. Worker resource admission failure returns 503 with `Retry-After: 2`. Background resource waits do not consume interval retry attempts. Existing keyword fallback remains explicitly labeled; it is not visual retrieval success.

Worker `/health` and `GET /index-jobs?scope_id=...` add `resources`: `{enabled:false}` when disabled; otherwise `group`, `policy`, `owner` (PID, role, acquisition time or null), `waiting`, `queue_limit`, `this_process_resident`, `this_process_waiting` and `external_recovery_required`. These are scheduling diagnostics, not memory allocation measurements. The Rust visual-index proxy carries them through unchanged; the workspace displays busy/recovery states.

Coordinated external VLM calls require `VISN_MANAGED_LMSTUDIO=true` and a dedicated loopback native-v1 endpoint, initially without loaded instances. Native model lists must disclose loaded instances. Cleanup uses the official `instance_id` field and verifies no instances remain before granting another owner. Endpoint/authentication travel to the Rust guardian over stdin. An interrupted or unacknowledged client leaves a durable quarantine marker; process death alone cannot prove a remote kernel stopped. Explicit recovery requires first verifying that the dedicated server is quiescent. OpenAI-compatible-only/MLX servers are not qualified for this managed profile. Ordinary uncoordinated endpoints remain supported with the feature disabled. See [package 5 operation, validation and recovery](GENERAL_VIDEO_PACKAGE_5.md).
