# Phase 0 HTTP API

All endpoints are local and unauthenticated in this Phase 0 lab build. Add service authentication before binding beyond loopback.

## Runtime

### `GET /healthz`

Returns service status, version, and active sink.

### `GET /api/v1/capabilities`

Returns runtime capabilities and whether Kafka was compiled/enabled.

### `GET /api/v1/models`

Proxies LM Studio model discovery as a safe availability response. LM Studio failure returns `available: false` rather than failing the service.

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
  "observations": [],
  "policy": {}
}
```

`monitor_duration_secs` controls the wall-clock monitoring window for HTTP/HTTPS and RTSP streams. It must be between 1 and the service's `VISN_MAX_ANALYSIS_SECS` ceiling. When the window ends, detection output is analyzed and the deterministic/Gemma insight report is generated.

The `uri` must return decodable video bytes directly. Plain `http://` and TLS-backed `https://` are handled identically by the API; HTTP media is decoded by the bundled FFmpeg runtime rather than by the Rust HTTP client.

Network credentials and query tokens are used only by the execution task. Job/list responses replace the URI with its scheme plus `***`, and the command adapter passes the URI over standard input rather than exposing it in process arguments.

### `GET /api/v1/jobs`

Lists newest jobs first.

### `GET /api/v1/jobs/{id}`

Returns queued/running/completed/failed state and the final pipeline result.

## Error format

```json
{"error":"human-readable validation or processing error"}
```
