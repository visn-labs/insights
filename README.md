# Visn Phase 0 — Video Pipeline Lab

A self-contained Phase 0 implementation of the video analytics service. It is intentionally usable before MongoDB and Kafka contracts are available:

- Rust/Axum service and embedded operator UI.
- File upload, HTTP/HTTPS stream, RTSP, built-in sample, and manually supplied detector facts.
- Deterministic tracking summaries, normalized zone/line rules, stable event IDs, and reports.
- YOLO26 local development adapter.
- LM Studio integration for `gemma-4-26B-a4B-QAT` through its OpenAI-compatible API.
- Automatic deterministic report fallback when Gemma is offline or returns unsupported claims.
- Optional Kafka producer behind a compile-time feature and runtime switch.
- Production DeepStream/TensorRT/NvDCF adapter contract and configuration templates.

## Quick start: zero external services

```bash
cargo run
```

Open <http://127.0.0.1:8080>, leave **Sample** and **Deterministic simulator** selected, optionally turn Gemma off, and click **Run pipeline**.

The built-in sample processes two tracks through a line and restricted zone. No model, database, Kafka broker, video file, or network connection is required.

## Connect LM Studio

1. Load `gemma-4-26B-a4B-QAT` in LM Studio.
2. Start the local server on port `1234` with OpenAI-compatible endpoints enabled.
3. Verify it independently:

   ```bash
   curl http://127.0.0.1:1234/v1/models
   ```

4. Start this service. Its defaults already target `http://127.0.0.1:1234/v1`:

   ```bash
   VISN_GEMMA_MODEL='your-exact-loaded-model-id' cargo run
   ```

`VISN_GEMMA_MODEL` is optional. When omitted, the service selects the first loaded model whose ID contains `gemma-4`, `26b`, and `a4b`, otherwise the first loaded model. The System page shows what LM Studio returned.

## Test a real video with YOLO26

The development adapter uses Ultralytics Python while the production adapter targets DeepStream/TensorRT. Create an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r tools/requirements-yolo.txt
VISN_DETECTOR_EXECUTABLE=.venv/bin/python cargo run
```

When the project-local `.venv/bin/python` exists, the service detects and uses it automatically, so after the setup above a plain `cargo run` is sufficient. `VISN_DETECTOR_EXECUTABLE` remains available as an explicit override.

In the UI:

1. Select **Upload** and choose an MP4/MKV/MOV file.
2. Select **YOLO26 local runner**.
3. Select a detector rate, initially 5 FPS.
4. Keep or edit the normalized zones/lines JSON.
5. Enable Gemma if LM Studio is running.
6. Click **Run pipeline**.

The first use may retrieve `yolo26s.pt` through Ultralytics. Set `VISN_YOLO_MODEL` to an approved local checkpoint path to avoid implicit downloads and to preserve release lineage.

HTTP and HTTPS are both first-class inputs to the YOLO26 command adapter. In the UI, select **HTTP(S)**, enter the stream URL, and choose **Monitor duration**. HTTP streams are decoded by the bundled, isolated FFmpeg executable; RTSP and local files use OpenCV. This avoids TLS assumptions and avoids loading two FFmpeg library copies into the detector process. HLS, MJPEG, MPEG-TS, and direct HTTP video work when FFmpeg can identify the media format.

`monitor_duration_secs` is set per job (120 seconds in the UI by default). `VISN_MAX_ANALYSIS_SECS` is the server-side safety ceiling and defaults to 3600 seconds. Temporary network read failures are retried until the requested duration expires, after which the accumulated observations are analyzed.

The HTTP value must be a direct media URL, not a webpage containing a player. If a job fails, the Run inspector now includes the complete decoder error chain. A `401` or `403` means the stream requires credentials; `Invalid data found when processing input` usually means the URL returned HTML/JSON rather than video.

## Configuration

See [.env.example](.env.example). The service reads environment variables directly; it does not automatically load `.env` files.

| Variable | Default | Purpose |
|---|---|---|
| `VISN_BIND` | `127.0.0.1:8080` | HTTP/UI listener |
| `VISN_DATA_DIR` | `./data` | Local uploaded media |
| `VISN_MAX_UPLOAD_MB` | `2048` | Bounded upload size |
| `VISN_GEMMA_BASE_URL` | `http://127.0.0.1:1234/v1` | LM Studio API base |
| `VISN_GEMMA_MODEL` | auto-discovered | Exact loaded model ID |
| `VISN_GEMMA_TIMEOUT_SECS` | `120` | Model-call deadline |
| `VISN_DETECTOR_EXECUTABLE` | `.venv/bin/python` if present, otherwise `python3` | Development detector executable |
| `VISN_DETECTOR_ARGS` | `tools/yolo26_runner.py` | Detector command arguments |
| `VISN_YOLO_MODEL` | `yolo26s.pt` | Approved checkpoint/path |
| `VISN_MAX_ANALYSIS_SECS` | `3600` | Maximum permitted per-job monitoring interval |
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
- [HTTP API and data contracts](docs/API.md)
- [DeepStream production adapter boundary](runtime/deepstream/README.md)
- [Original full implementation plan](IMPLEMENTATION_PLAN.md)

The exact NVIDIA and model compatibility matrix must be pinned on the target Linux/GPU host. This Apple Silicon development machine cannot execute CUDA, TensorRT, DeepStream, NVDEC, NVENC, or NvDCF.

## Upstream technical references

- [Ultralytics YOLO26 model documentation](https://docs.ultralytics.com/models/yolo26/)
- [Ultralytics TensorRT export documentation](https://docs.ultralytics.com/integrations/tensorrt/)
- [Google Gemma 4 documentation](https://ai.google.dev/gemma/docs/core)
- [NVIDIA DeepStream sample pipeline documentation](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_C_Sample_Apps.html)

Review Ultralytics licensing and Gemma terms for the exact commercial deployment before distributing model-backed images or services.
