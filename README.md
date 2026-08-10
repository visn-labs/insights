# Visn Phase 0 — Video Pipeline Lab

A self-contained Phase 0 implementation of the video analytics service. It is intentionally usable before MongoDB and Kafka contracts are available:

- Rust/Axum service and embedded operator UI.
- File upload, HTTP/HTTPS stream, RTSP, built-in sample, and manually supplied detector facts.
- Deterministic tracking summaries, normalized zone/line rules, stable event IDs, and reports.
- YOLO26 local development adapter.
- LM Studio integration for selectable local VLMs through native model loading and OpenAI-compatible chat.
- Automatic deterministic report fallback when Gemma is offline or returns unsupported claims.
- Optional Kafka producer behind a compile-time feature and runtime switch.
- Concurrent HTTP camera-cluster jobs with camera-wise and cluster-wise insights.
- Representative-frame scene descriptions through the local LM Studio vision API, with detector-only fallback.
- Explicit overlap/topology-gated cross-camera person association and deterministic global IDs.
- Production DeepStream/TensorRT/NvDCF adapter contract and configuration templates.

## Local V1 retrieval memory

The service now includes the first retrieval-first V1 slice from `docs/RETRIEVAL_FIRST_VIDEO_MEMORY_PLAN.md`:

- Accepts one or more backend camera payloads using the current `liveurl`, `Country`, `Country code`, `Region`, `City`, `Latitude`, `Longitude`, `ZIP`, `Timezone`, `Manufacturer`, and `description` field names.
- Records each bounded HTTP(S)/RTSP interval as encoded source evidence before analysis.
- Retrieves analysis frames at a sparse observer rate and creates adaptive novelty/maximum-duration event boundaries without running YOLO continuously; inter-frame codecs may still require decoder work for skipped frames.
- Stores representative JPEGs, browser-playable MP4 event derivatives, activity/quality scores, and lightweight visual signatures locally.
- Optionally describes only the highest-priority events with the selected LM Studio VLM. Calls are capped and serialized to protect a local LM Studio instance.
- Retrieves camera/cluster events locally and can ask the selected VLM to synthesize the shortlisted records.
- Persists completed memory manifests and rehydrates them after restart. Kafka and MongoDB remain unnecessary.

Open the **Memory** workspace in the UI to load authorized-camera presets, paste backend JSON payloads, inspect evidence, and search it. See [the V1 local guide](docs/V1_LOCAL_MEMORY.md) for exact steps and limitations.

## 3D town introduction

The locally served frontend opens with a procedural isometric 1800s town rendered in Three.js. Coffee-brown etched buildings and curve-drawn period townspeople are contrasted with prominent moss-green CCTV cameras and blue-green handheld phones. Townspeople walk with articulated motion, converse, tend a market stall, and pass a horse-drawn carriage while birds, trees, smoke, water, luminous camera lenses, and surveillance cones animate around them.

The introduction can be skipped with the button or `Escape`, is shown once per browser tab session, and can be replayed with the rook-shaped button in the application header. Add `?intro=1` to the URL to force it open. Reduced-motion preferences produce a static rendered tableau. See [the intro implementation guide](docs/INTRO_3D.md).

## Quick start: zero external services

```bash
cargo run
```

Open <http://127.0.0.1:8080>, leave **Sample** and **Deterministic simulator** selected, optionally turn Gemma off, and click **Run pipeline**.

The built-in sample processes two tracks through a line and restricted zone. No model, database, Kafka broker, video file, or network connection is required.

## Connect LM Studio

1. Start LM Studio's local server on port `1234` with OpenAI-compatible endpoints enabled.
2. Make sure the VLMs you want to use are downloaded in LM Studio.
3. Verify it independently:

   ```bash
   curl http://127.0.0.1:1234/v1/models
   curl http://127.0.0.1:1234/api/v1/models
   ```

4. Start this service. Its defaults already target `http://127.0.0.1:1234/v1`:

   ```bash
   VISN_GEMMA_MODEL='your-exact-loaded-model-id' cargo run
   ```

`VISN_GEMMA_MODEL` is optional. In the UI, choose one of:

- `prism-ml/bonsai-27b`
- `moondream2`
- `qwen/qwen3.6-35b-a3b`
- `google/gemma-4-26b-a4b-qat`
- `zai-org/glm-4.6v-flash`

For each VLM-enabled run, the service calls LM Studio's native `/api/v1/models/load` endpoint for the selected model, then uses that same model ID in `/v1/chat/completions`. If your LM Studio version does not support native loading but the selected model is already loaded, the run still proceeds. The System page shows the selected model and what LM Studio reports as downloaded or loaded.

## Test a real video with YOLO26

The development adapter uses Ultralytics Python while the production adapter targets DeepStream/TensorRT. Create an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r tools/requirements-yolo.txt
VISN_DETECTOR_EXECUTABLE=.venv/bin/python cargo run
```

When the project-local `.venv/bin/python` exists, the service detects and uses it automatically, so after the setup above a plain `cargo run` is sufficient. `VISN_DETECTOR_EXECUTABLE` remains available as an explicit override.

By default the backend starts `tools/yolo26_worker.py` lazily, loads YOLO once, and shares that model across the currently active cameras. Live cameras have a one-frame latest-value buffer, uploaded files are backpressured instead of dropping sampled frames, and each camera owns a separate ByteTrack state. Frames that arrive within the short batch window are inferred together. If the worker fails before emitting an observation, the service automatically retries that request through the isolated `tools/yolo26_runner.py` path. Set `VISN_PERSISTENT_DETECTOR=false` to force the older path while diagnosing a runtime-specific problem.

In the UI:

1. Select **Upload** and choose an MP4/MKV/MOV file.
2. Select **YOLO26 local runner**.
3. Select a detector rate, initially 5 FPS.
4. Keep or edit the normalized zones/lines JSON.
5. Enable VLM generation if LM Studio is running, then choose the VLM from the dropdown. This generates the general view description and report enrichment.
6. Click **Run pipeline**.

The first use may retrieve `yolo26s.pt` through Ultralytics. Set `VISN_YOLO_MODEL` to an approved local checkpoint path to avoid implicit downloads and to preserve release lineage.

HTTP and HTTPS are both first-class inputs to the YOLO26 command adapter. In the UI, select **HTTP(S)**, enter the stream URL, and choose **Monitor duration**. HTTP streams are decoded by the bundled, isolated FFmpeg executable; RTSP and local files use OpenCV. This avoids TLS assumptions and avoids loading two FFmpeg library copies into the detector process. HLS, MJPEG, MPEG-TS, and direct HTTP video work when FFmpeg can identify the media format.

`monitor_duration_secs` is set per job (120 seconds in the UI by default). `VISN_MAX_ANALYSIS_SECS` is the server-side safety ceiling and defaults to 3600 seconds. Temporary network read failures are retried until the requested duration expires, after which the accumulated observations are analyzed.

The HTTP value must be a direct media URL, not a webpage containing a player. If a job fails, the Run inspector now includes the complete decoder error chain. A `401` or `403` means the stream requires credentials; `Invalid data found when processing input` usually means the URL returned HTML/JSON rather than video.

## Run a multi-camera cluster

In the UI select **Cluster** and enter one camera per line:

```text
camera-a | Main entrance | http://camera-a/live.m3u8
camera-b | Side entrance | http://camera-b/live.m3u8
camera-c | Lobby | http://camera-c/live.m3u8
```

Choose the relationship mode:

- **Camera-wise only** processes every camera concurrently and creates aggregate cluster insights without merging identities. This is the safe default.
- **Synchronized overlapping views** permits simultaneous person-track association between the supplied cameras.
- **Directed topology edges** uses manually supplied camera-to-camera travel windows for non-overlapping handovers. Reverse movement needs a separate edge.

The result contains camera-local tracks and reports, camera failures, one-to-one association decisions, deterministic cluster identity records, camera-wise view descriptions, an aggregated cluster view description, and a grounded cluster report. Unrelated singleton records are not reported as a unique-person count. Gemma never assigns identities or creates camera edges.

For each successfully decoded camera, the runner retains only one bounded JPEG representative frame in memory. A sharp, normally exposed sampled frame is selected and sent to LM Studio using the OpenAI-compatible `image_url` message format. The response describes the scene type, physical layout, visible areas, static elements and visibility conditions. The image is not included in the job API response or Kafka placeholder payload. If the loaded model cannot accept images, the job still completes with detected-class context and an explicit fallback reason.

This local V1 follows the implementation plan's first algorithm stage. Appearance uses deterministic color/texture prototypes so the complete flow can run locally today. Production deployment must replace that descriptor with a site-calibrated OSNet/equivalent ReID TensorRT model before identity accuracy claims are made.

## Configuration

See [.env.example](.env.example). The service reads environment variables directly; it does not automatically load `.env` files.

| Variable | Default | Purpose |
|---|---|---|
| `VISN_BIND` | `127.0.0.1:8080` | HTTP/UI listener |
| `VISN_DATA_DIR` | `./data` | Local uploaded media |
| `VISN_MAX_UPLOAD_MB` | `2048` | Bounded upload size |
| `VISN_GEMMA_BASE_URL` | `http://127.0.0.1:1234/v1` | LM Studio API base |
| `VISN_LMSTUDIO_API_BASE_URL` | derived as `http://127.0.0.1:1234/api/v1` | LM Studio native API for model discovery/loading |
| `VISN_GEMMA_MODEL` | auto-discovered | Exact loaded model ID |
| `VISN_GEMMA_TIMEOUT_SECS` | `120` | Model-call deadline |
| `VISN_VLM_CONTEXT_LENGTH` | `4096` | Context requested when loading a VLM in LM Studio |
| `VISN_VLM_EVAL_BATCH_SIZE` | `256` | LM Studio prompt-evaluation batch size |
| `VISN_VLM_MAX_OUTPUT_TOKENS` | `768` | Per-call generation ceiling |
| `VISN_VLM_FLASH_ATTENTION` | `true` | Request Flash Attention from LM Studio |
| `VISN_VLM_EXCLUSIVE_MEDIA` | `true` | Pause local detector/observer workers during each VLM call |
| `VISN_DETECTOR_EXECUTABLE` | `.venv/bin/python` if present, otherwise `python3` | Development detector executable |
| `VISN_DETECTOR_ARGS` | `tools/yolo26_runner.py` | Detector command arguments |
| `VISN_PERSISTENT_DETECTOR` | `true` | Load one YOLO model for all concurrently active camera sessions |
| `VISN_PERSISTENT_DETECTOR_FALLBACK` | `true` | Retry through the isolated runner only if the shared worker produced no observations |
| `VISN_DETECTOR_WORKER_ARGS` | `tools/yolo26_worker.py` | Persistent detector worker command arguments |
| `VISN_YOLO_MODEL` | `yolo26s.pt` | Approved checkpoint/path |
| `VISN_DETECTOR_BATCH_SIZE` | min(camera limit, 4) for `.pt`, otherwise `1` | Maximum simultaneous frames per inference call |
| `VISN_DETECTOR_BATCH_WAIT_MS` | `12` | Maximum wait used to assemble a camera batch |
| `VISN_DETECTOR_WORKER_IDLE_SECS` | `30` | Unload an unused YOLO worker after this interval |
| `VISN_DETECTOR_IMGSZ` | `640` | Detector input size shared by worker and fallback |
| `VISN_DETECTOR_DEVICE` | auto | Optional Ultralytics device selection |
| `VISN_DETECTOR_WARMUP` | `true` | Run one batch-1 synthetic inference before accepting cameras |
| `VISN_DETECTOR_THREADS` | `1` | OpenCV/PyTorch threads per detector process |
| `VISN_APPEARANCE_INTERVAL_SECS` | `1.0` | Cluster person-appearance sampling interval |
| `VISN_MAX_ANALYSIS_SECS` | `3600` | Maximum permitted per-job monitoring interval |
| `VISN_MAX_CLUSTER_CAMERAS` | `16` | Maximum HTTP cameras accepted by one cluster job |
| `VISN_MAX_CONCURRENT_CAMERAS` | `4` | Global combined YOLO/sparse-observer process budget |
| `VISN_MAX_EPHEMERAL_JOBS` | `128` | Completed ordinary/cluster results retained in RAM |
| `VISN_MEMORY_CLIP_MODE` | `copy` | Event evidence: stream-copy, transcode, or source reference |
| `VISN_KAFKA_ENABLED` | `false` | Enable Kafka only in a Kafka-feature build |

## Verification

```bash
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
cargo test
./scripts/smoke_test.sh
```

Compile-check the optional Kafka adapter separately:

```bash
cargo check --features kafka
```

The Kafka feature builds `librdkafka` and therefore requires CMake and a native C/C++ toolchain. Kafka stays disabled until both the feature and `VISN_KAFKA_ENABLED=true` are present. Topic names and broker settings are environment variables so the backend team can provide final contracts later.

## Documentation

- [Phase 0 architecture and build details](docs/PHASE0_BUILD.md)
- [Backend inference optimization, export, and validation](docs/BACKEND_INFERENCE_OPTIMIZATION.md)
- [HTTP API and data contracts](docs/API.md)
- [DeepStream production adapter boundary](runtime/deepstream/README.md)
- [Original full implementation plan](IMPLEMENTATION_PLAN.md)

The exact NVIDIA and model compatibility matrix must be pinned on the target Linux/GPU host. This Apple Silicon development machine cannot execute CUDA, TensorRT, DeepStream, NVDEC, NVENC, or NvDCF.

## Upstream technical references

- [Ultralytics YOLO26 model documentation](https://docs.ultralytics.com/models/yolo26/)
- [Ultralytics model export documentation](https://docs.ultralytics.com/modes/export/)
- [Ultralytics TensorRT export documentation](https://docs.ultralytics.com/integrations/tensorrt/)
- [Google Gemma 4 documentation](https://ai.google.dev/gemma/docs/core)
- [NVIDIA DeepStream sample pipeline documentation](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_C_Sample_Apps.html)

Review Ultralytics licensing and Gemma terms for the exact commercial deployment before distributing model-backed images or services.
