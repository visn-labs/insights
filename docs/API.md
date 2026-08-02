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

RTSP credentials are used only by the execution task. Job/list responses replace the URI with `rtsp://***`, and the command adapter passes the URI over standard input rather than exposing it in process arguments.

### `GET /api/v1/jobs`

Lists newest jobs first.

### `GET /api/v1/jobs/{id}`

Returns queued/running/completed/failed state and the final pipeline result.

## Error format

```json
{"error":"human-readable validation or processing error"}
```
