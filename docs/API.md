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
