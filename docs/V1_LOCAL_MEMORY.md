# Local V1 Camera Memory

## What V1 implements

V1 is a bounded local implementation of the retrieval-first vertical slice. It is intended for workflow and accuracy testing on the developer machine, not internet-facing production deployment.

```text
backend camera payloads
  → encoded evidence recording
  → sparse adaptive observation
  → temporal event intervals
  → local manifest + thumbnails + clips
  → bounded VLM descriptions
  → local recall + optional VLM synthesis
```

The existing YOLO26 workspace is unchanged. Use it for deterministic objects, tracks, zones, lines, and dwell. Use the Memory workspace for broad scene/event recall and evidence retrieval.

## Prerequisites

1. Rust 1.85 or newer.
2. The project virtual environment with `tools/requirements-yolo.txt` installed. The V1 runner uses OpenCV, NumPy, and the bundled `imageio-ffmpeg` binary; it does not load Ultralytics or YOLO.
3. LM Studio at `http://127.0.0.1:1234` only when VLM event descriptions or query synthesis are enabled.
4. Direct network access to the supplied camera URLs.

Verify the lightweight runtime:

```bash
.venv/bin/python -c 'import cv2, imageio_ffmpeg, numpy; print(cv2.__version__); print(imageio_ffmpeg.get_ffmpeg_exe())'
cargo check
```

## Start the service

```bash
cd /Users/anagha/projects/visn_rust
cargo run
```

Open `http://127.0.0.1:8080` and choose **Memory**.

## First test without LM Studio

1. Click **Cluster A**.
2. Set monitor duration to `10` seconds.
3. Set observer rate to `1` FPS.
4. Disable **Enrich highest-priority events with the selected VLM**.
5. Click **Record and index**.
6. Wait for `completed`.
7. Confirm camera count, event count, sparse decoded-frame count, thumbnails, and evidence playback.
8. Search for `show periods with movement` with **VLM synthesis** disabled.

This proves camera connectivity, evidence recording, segmentation, local persistence, retrieval, and media endpoints without loading YOLO or a VLM.

## Test VLM enrichment

1. Start LM Studio and leave enough free memory for one selected VLM.
2. In the Memory workspace, select the desired VLM from its dropdown.
3. Enable event enrichment and use a 10–20 second single-camera payload first.
4. Run the index job. V1 sends at most `VISN_MAX_VLM_EVENTS_PER_CAMERA` representative events to the VLM.
5. Verify `description.generated_by_model`, `description.model`, visible objects/actions/text, and any `fallback_reason`.
6. Enable **VLM synthesis** for a query and inspect the retrieval mode and fallback notes.

VLM calls from concurrent cameras are serialized so multiple cluster workers do not race to load or call LM Studio.

## Backend payload format

Paste either one object or an array. The current backend key names are accepted directly:

```json
[
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
]
```

`camera_id` is optional; V1 generates one when absent. Supplying a stable backend camera ID is recommended.

## Local files

```text
data/memory/
  manifests/<job-id>.json
  <job-id>/<camera-id>/
    source.mkv
    <event-id>.jpg
    <event-id>.mp4
```

The manifest is written atomically after completion and reloaded on restart. API responses redact live URLs. The source Matroska artifact is authoritative; MP4 files are review derivatives.

## Current V1 limitations

- Adaptive activity uses encoded recording followed by sparse luma/histogram analysis; packet-level motion vectors are a later optimization.
- V1 retrieval uses metadata and event descriptions, then optional query-time VLM synthesis. A dedicated temporal embedding/reranker worker is the next accuracy milestone.
- A representative frame cannot prove motion or a multi-frame action. The VLM prompt therefore treats actions conservatively and the evidence clip remains available.
- The memory job currently accepts network camera payloads. Uploaded-video indexing will be added through the same source-session interface.
- Local persistence is disposable and does not establish the backend MongoDB schema.
- Authentication, tenant policy, privacy controls, and production evidence authorization remain deferred as requested. Keep the service bound to `127.0.0.1`.
