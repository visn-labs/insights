# Backend inference optimization and artifact validation

This guide covers backend workflow optimization, detector conversion, and quality/performance validation. It does not change the frontend, lower YOLO26 input resolution, replace `yolo26s`, or claim that quantization is quality-neutral.

## Optimizations implemented in the service

The backend now removes avoidable work before changing model accuracy:

- The YOLO runner emits one compact `VISN_OBSERVATIONS_JSON` record per inference frame. Rust consumes it immediately and maintains only per-track rule state, confidence, zone/line state, and a weighted appearance prototype. It no longer retains every observation in Python, duplicates the full JSON in Rust stdout, or stores the observation history for post-processing.
- The default detector path is now a persistent multi-camera worker. It loads one YOLO model for all active cameras, batches frames that become ready within a bounded wait, and retains one independent ByteTrack instance and appearance schedule per camera. This removes the previous model copy and Python/Torch runtime copy for every concurrent camera.
- Every live camera owns only one pending full-resolution frame; a newer live frame replaces a stale unprocessed frame. Uploaded files use backpressure so sampled evidence frames are not silently dropped. The Rust media semaphore still bounds the total active sessions.
- Detector stderr is drained concurrently and retains only its newest 128 KiB. A noisy FFmpeg/Ultralytics process can no longer grow the service response buffer without a bound.
- HTTP decoding applies the requested detector FPS inside FFmpeg, before raw BGR frames cross into Python. Local-file and RTSP skip paths use `grab()` so skipped frames are not converted into Python arrays.
- Ordinary single-camera jobs disable appearance extraction because their API result does not use it. Cluster jobs calculate appearance for people only, retain the first sample, and then sample each track at `VISN_APPEARANCE_INTERVAL_SECS` instead of recomputing a nearly identical 62-float vector every frame.
- OpenCV and PyTorch thread counts are explicit. A four-camera run no longer lets four Python processes independently claim every host CPU core.
- YOLO and sparse-memory subprocesses share one global permit budget across every submitted job. `VISN_MAX_CONCURRENT_CAMERAS` is therefore a process-wide media-worker bound, not merely a per-job bound.
- If a cluster camera waits for a permit, its real processing-start offset is carried into cross-camera temporal association. Queued intervals are no longer compared as though all of them began at timestamp zero.
- Representative frames are quality-scored on a small image and retained at no more than 960 pixels, avoiding a persistent full-resolution copy per camera.
- The retrieval-memory runner uses a zero-copy hard link for local evidence when the filesystem permits it (streaming copy fallback), bounded activity history, cached histograms, `CV_32F` sharpness on a reduced image, and `grab()` for unsampled frames. It returns thumbnail paths and metadata rather than base64-copying every event JPEG through stdout. Rust base64-encodes only VLM-selected frames, one at a time.
- Sparse-memory runner stdout is capped at 2 MiB and stderr retains only its newest 128 KiB while both pipes are drained concurrently. Before starting, it reaps a warm shared detector if and only if no camera session is active.
- Memory clips first attempt codec stream-copy. `VISN_MEMORY_CLIP_MODE=reference` removes all duplicate event clips; `transcode` remains available when browser-compatible H.264 derivatives are required.
- LM Studio model selection, switching, and completion are serialized by one shared gate. By default a VLM call temporarily owns the complete local media-worker budget, so large VLM allocation/generation does not overlap YOLO or observer processes. The selected instance is cached, load requests have bounded context/evaluation settings with Flash Attention, prompts use compact JSON, and every completion has an explicit output-token ceiling.
- Local retrieval ranks borrowed event records and clones only the final top matches. Completed ephemeral YOLO/cluster job histories are capped by `VISN_MAX_EPHEMERAL_JOBS`.

These changes preserve the `yolo26s` checkpoint, 640-pixel detector input, confidence threshold, tracker, event rules, VLM choice, and evidence source. The batch event-engine path and streaming path share the same implementation and have a focused equivalence test.

### Resource controls

| Variable | Default | Effect |
|---|---:|---|
| `VISN_MAX_CONCURRENT_CAMERAS` | `4` | Global combined YOLO/sparse-observer process cap |
| `VISN_PERSISTENT_DETECTOR` | `true` | Use the single-model multi-camera worker |
| `VISN_PERSISTENT_DETECTOR_FALLBACK` | `true` | Use the isolated runner only after a pre-observation worker failure |
| `VISN_DETECTOR_BATCH_SIZE` | min(camera cap, 4) for `.pt`; `1` for exported artifacts | Maximum frames in one inference call |
| `VISN_DETECTOR_BATCH_WAIT_MS` | `12` | Bounded latency used to assemble a multi-camera batch |
| `VISN_DETECTOR_WORKER_IDLE_SECS` | `30` | Reap an unused shared model after this period |
| `VISN_DETECTOR_IMGSZ` | `640` | Common worker/fallback inference size |
| `VISN_DETECTOR_WARMUP` | `true` | Pay one batch-1 warm-up before accepting live frames |
| `VISN_DETECTOR_THREADS` | `1` | OpenCV and PyTorch threads per YOLO process |
| `VISN_APPEARANCE_INTERVAL_SECS` | `1.0` | Person appearance resampling period; first sample is immediate |
| `VISN_VLM_CONTEXT_LENGTH` | `4096` | LM Studio context allocated when this service loads a VLM |
| `VISN_VLM_EVAL_BATCH_SIZE` | `256` | LM Studio prompt-evaluation batch setting |
| `VISN_VLM_MAX_OUTPUT_TOKENS` | `768` | Hard output ceiling for every report/description call |
| `VISN_VLM_FLASH_ATTENTION` | `true` | Requests LM Studio Flash Attention |
| `VISN_VLM_OFFLOAD_KV_CACHE_TO_GPU` | `false` | Keeps the KV cache off the GPU unless explicitly enabled |
| `VISN_VLM_EXCLUSIVE_MEDIA` | `true` | Prevents detector/observer execution from overlapping VLM work |
| `VISN_MEMORY_CLIP_MODE` | `copy` | `copy`, `transcode`, or source-only `reference` evidence |
| `VISN_MAX_EPHEMERAL_JOBS` | `128` | Completed non-persistent results retained in RAM |

For cameras covering the same space, keep `VISN_MAX_CONCURRENT_CAMERAS` at least as large as the number of streams that must observe the same wall-clock interval. A lower value keeps association timestamps truthful, but cameras monitor in waves and therefore cover different intervals. The persistent worker batches only frames currently ready and never changes camera order into tracker identity: every request has its own ByteTrack object. The short batch window is therefore a throughput optimization rather than a synchronization claim.

`VISN_VLM_EXCLUSIVE_MEDIA=true` first acquires the complete media permit budget, waits for active camera sessions, then shuts down and reaps the persistent detector before LM Studio model loading/generation. This prevents a reusable idle YOLO allocation from undoing the VLM peak-memory isolation. When VLM is disabled, the worker stays warm until `VISN_DETECTOR_WORKER_IDLE_SECS` expires.

### Focused verification

The implementation can be checked without loading a model or contacting a stream:

```bash
cargo check
cargo test event_engine::tests
PYTHONPYCACHEPREFIX=/tmp/visn-pycache \
  .venv/bin/python -m py_compile \
  tools/yolo26_worker.py tools/yolo26_runner.py tools/event_memory_runner.py \
  tools/export_yolo26.py tools/compare_yolo26_backends.py
```

Use the comparison tool below for performance/quality measurements only when you are ready to run model inference manually. Structural changes above do not by themselves prove a speedup on every codec and host.

### Manual shared-worker check

Start the service normally and use the unchanged UI. With VLM disabled, submit two cameras in one cluster and watch the structured service log:

1. One `persistent detector worker launched` and one `persistent detector worker ready` message should appear for the cluster, rather than one Python/model load per camera.
2. Both camera results should retain independent track IDs and their requested monitoring intervals.
3. Submit another non-VLM job within 30 seconds; it should reuse the warm worker. After 30 idle seconds the worker is reaped.
4. Enable VLM and repeat. Once detector sessions finish, `persistent detector worker stopped` should appear before LM Studio generation begins.
5. Set `VISN_PERSISTENT_DETECTOR=false` and restart only when comparing against the isolated legacy behavior. Set `VISN_DETECTOR_BATCH_SIZE=1` to isolate sharing from batching without duplicating the model.

Do not infer batch efficiency from wall time alone. For a release comparison, save an authorized local clip and use the same-frame benchmark later in this guide. Tracking continuity must also be inspected because detector agreement does not validate identity persistence.

## Quality-first deployment rule

Keep `yolo26s.pt`, 640-pixel input, batch 1, and the YOLO26 end-to-end head as the reference configuration. A converted artifact is promoted only after it passes both of these checks on the deployment host:

1. It meets latency and peak-memory targets on losslessly materialized frames from representative camera footage.
2. It meets a predeclared mAP/per-class-recall tolerance on a labeled, site-representative validation set.

Prediction agreement with the PyTorch checkpoint is a useful regression signal, but it is not accuracy. Two models can agree and both be wrong. Conversely, a candidate may disagree at the confidence threshold while retaining similar mAP. Use the optional labeled-validation mode before accepting a release artifact.

## Runtime choice by host

| Deployment host | First candidate | Next candidate after validation | Portable fallback |
|---|---|---|---|
| Apple Silicon | Core ML FP16, static 640, batch 1 | Core ML W8A16 weight compression | ONNX Runtime or LiteRT |
| NVIDIA GPU | TensorRT FP16, built on the target GPU class | TensorRT INT8 with site calibration | ONNX Runtime CUDA |
| Intel CPU/iGPU | OpenVINO FP16, static 640, batch 1 | OpenVINO INT8 with site calibration | ONNX Runtime |
| Mixed CPU/edge hosts | ONNX FP32/FP16 | Calibrated ONNX INT8 or measured LiteRT | PyTorch reference |

Core ML is the preferred local Apple Silicon candidate because its static model can use Apple compute units directly. LiteRT is an additional portability/runtime candidate, not an automatic improvement over Core ML. OpenVINO on macOS ARM is CPU-only and is therefore not the first Apple Silicon choice. TensorRT engines are host-specific build products and must not be copied blindly between GPU architectures or incompatible TensorRT/CUDA versions.

## Separate export environment

The running service currently uses a Python 3.9 detector environment. Current Core ML, LiteRT, ONNX Runtime, OpenVINO and quantization toolchains should be kept out of that environment. Create a separate CPython 3.11 or 3.12 environment:

```bash
python3.12 -m venv .venv-export
source .venv-export/bin/activate
python -m pip install --upgrade pip
python -m pip install -r tools/requirements-export.txt
```

This environment is for exports and offline comparison. `tools/requirements-export.txt` intentionally omits TensorRT; create the equivalent isolated environment on the NVIDIA deployment host with CUDA and TensorRT versions matched to that host.

Do not replace `.venv` or its packages. After an artifact is accepted, point a dedicated detector worker/runtime at the validated environment and artifact. Keep the original `.pt` checkpoint and generated manifest for rollback and lineage.

For example, after a Core ML package passes the comparison gates, the existing backend runner can use it without a frontend change:

```bash
VISN_DETECTOR_EXECUTABLE=.venv-export/bin/python \
VISN_YOLO_MODEL=artifacts/models/yolo26s.mlpackage \
cargo run
```

Use the exact artifact name printed by the exporter. Returning to `VISN_DETECTOR_EXECUTABLE=.venv/bin/python` and `VISN_YOLO_MODEL=yolo26s.pt` is the rollback path.

## Export tool

`tools/export_yolo26.py` supports modern Ultralytics export formats, validates precision compatibility before loading the model, uses static shapes by default, hashes artifacts with bounded memory, and writes a reproducibility manifest beside the result.

Supported precision combinations for this toolchain are:

| Format | `32` | `16` | `8` | `w8a16` | `w8a32` | Activation calibration |
|---|---:|---:|---:|---:|---:|---|
| `coreml` | yes | yes | yes | yes | no | no; `8`/`w8a16` compress weights and retain FP16 ML Program compute |
| `litert` / `tflite` | yes | no | yes | yes | yes | required for `8` and `w8a16` |
| `onnx` | yes | yes | yes | no | no | required for `8` |
| `openvino` | yes | yes | yes | no | no | required for `8` |
| `engine` | yes | yes | yes | no | no | required for `8` |

`tflite` is accepted as a command-line alias for Ultralytics' current `litert` exporter. LiteRT does not expose a separate FP16 artifact through the current Ultralytics path; test FP32 with the intended delegate or one of its supported quantized modes instead of labeling it FP16.

### Apple Silicon: Core ML

Start with FP16:

```bash
python tools/export_yolo26.py \
  --model yolo26s.pt \
  --format coreml \
  --quantize 16 \
  --imgsz 640 \
  --batch 1
```

Only after FP16 validation, screen weight compression:

```bash
python tools/export_yolo26.py \
  --model yolo26s.pt \
  --format coreml \
  --quantize w8a16 \
  --imgsz 640 \
  --batch 1
```

### LiteRT

FP32 requires no calibration:

```bash
python tools/export_yolo26.py \
  --model yolo26s.pt \
  --format litert \
  --quantize 32 \
  --imgsz 640
```

W8A16 and full INT8 require representative calibration data:

```bash
python tools/export_yolo26.py \
  --model yolo26s.pt \
  --format litert \
  --quantize w8a16 \
  --data datasets/site-cameras.yaml \
  --fraction 1.0 \
  --imgsz 640
```

### ONNX and OpenVINO

Portable ONNX FP16:

```bash
python tools/export_yolo26.py \
  --model yolo26s.pt \
  --format onnx \
  --quantize 16 \
  --imgsz 640
```

Calibrated OpenVINO INT8 for an Intel target:

```bash
python tools/export_yolo26.py \
  --model yolo26s.pt \
  --format openvino \
  --quantize 8 \
  --data datasets/site-cameras.yaml \
  --fraction 1.0 \
  --imgsz 640
```

### TensorRT

Run this on the deployment-class NVIDIA host, not this Apple development machine:

```bash
python tools/export_yolo26.py \
  --model yolo26s.pt \
  --format engine \
  --quantize 16 \
  --device 0 \
  --workspace 4 \
  --imgsz 640
```

For INT8 add `--quantize 8 --data datasets/site-cameras.yaml --fraction 1.0`. Record GPU model, driver, CUDA, TensorRT and application runtime versions with the artifact. The generated manifest records the Python-side environment and artifact digest, but cannot infer every system library loaded by the eventual service.

### Static shapes and dynamic opt-in

Static 640×640, batch-1 exports are the default because camera inference normally has a fixed preprocessing contract and latency is more important than accepting arbitrary tensor shapes. Use `--dynamic` only when a real runtime requirement justifies its optimization and memory cost. The current LiteRT exporter is static-only, and the tool rejects `--dynamic` for it.

Use two `--imgsz` values only when the full preprocessing and validation contract is intentionally rectangular:

```bash
python tools/export_yolo26.py --format onnx --quantize 16 --imgsz 384 640
```

Changing input size is an accuracy/performance tradeoff, not a free optimization, so it requires a separate labeled result and artifact manifest.

## Calibration data

Activation-quantized artifacts are only as representative as their calibration set. Build the calibration YAML from authorized frames spanning:

- every camera type and common field of view;
- daylight, night, glare, rain/fog and exposure extremes;
- low-motion and crowded/high-motion periods;
- small/distant objects and the rare classes/events that matter operationally;
- the same resize/letterbox distribution used in deployment.

Do not calibrate from only the convenient sample used for the speed benchmark. Keep a separate labeled holdout for approval. `--fraction 1.0` is the release default; a smaller fraction is useful only for rapid experiments and is preserved in the manifest.

## Same-frame benchmark and quality comparison

The comparison tool accepts only a local image or video. It decodes that source once to temporary lossless PNG frames, then runs the baseline and every candidate sequentially in fresh subprocesses. A subprocess exit releases the model before the next backend loads, preventing the benchmark itself from accumulating model memory.

Run an unlabeled screening comparison:

```bash
python tools/compare_yolo26_backends.py \
  --baseline yolo26s.pt \
  --candidate artifacts/models/yolo26s.mlpackage \
  --candidate artifacts/models/yolo26s.tflite \
  --source testdata/authorized-camera-sample.mp4 \
  --imgsz 640 \
  --sample-fps 1 \
  --max-frames 120 \
  --warmup 3
```

Add true labeled validation:

```bash
python tools/compare_yolo26_backends.py \
  --baseline yolo26s.pt \
  --candidate artifacts/models/yolo26s.mlpackage \
  --source testdata/authorized-camera-sample.mp4 \
  --validation-data datasets/site-cameras.yaml \
  --validation-split val
```

The report is written atomically to `artifacts/benchmarks/yolo26-comparison.json` by default and contains:

- SHA-256, size and file count for every file/package artifact;
- model load and warm-up time;
- mean, median, p95, minimum and maximum end-to-end frame latency;
- Ultralytics preprocessing/inference/postprocessing timing where the backend exposes it;
- isolated peak resident memory before optional labeled validation;
- class-aware IoU agreement, confidence drift and detection-count drift versus the baseline;
- mAP50-95, mAP50, mAP75 and per-class mAP when labeled validation is requested.

The tool does not retain decoded frames after completion and does not run models concurrently. Use representative clips, repeat measurements after thermal warm-up, and measure on the actual deployment host. Do not compare Apple, Intel and NVIDIA timings as if they were results from the same runtime.

## Artifact promotion checklist

Use written site-specific limits. A conservative initial gate is: no newly missed critical event in the held-out event suite, no meaningful IDF1/HOTA or ID-switch regression, and no more than 0.5 absolute mAP50-95 point loss versus the `.pt` reference. Require a material resource win—such as at least 15% lower peak RSS or 20% lower sustained latency—before accepting a more complex runtime. These are release starting points, not universal accuracy claims.

1. Freeze the source checkpoint digest, model size, input size, end-to-end setting and class mapping.
2. Export FP16/static/batch-1 for the target-native runtime.
3. Run the same-frame comparison and inspect cold load, warm-up, p95 latency, peak RSS, count drift and class mapping.
4. Run labeled validation and inspect aggregate plus per-class metrics, especially operationally important rare classes.
5. Test the full stream runner for tracking continuity; detector mAP alone does not prove stable track IDs.
6. Screen W8A16/INT8 only if FP16 does not meet the resource target. Re-run every validation gate.
7. Retain the checkpoint, exported artifact, manifest, benchmark report, calibration dataset version and runtime versions together.
8. Promote by explicit configuration and keep the previous artifact available for rollback.

## Upstream references

- [Ultralytics YOLO26](https://docs.ultralytics.com/models/yolo26/)
- [Ultralytics export mode](https://docs.ultralytics.com/modes/export/)
- [Ultralytics Core ML integration](https://docs.ultralytics.com/integrations/coreml/)
- [Ultralytics benchmark mode](https://docs.ultralytics.com/modes/benchmark/)
- [ONNX Runtime quantization](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html)
- [ONNX Runtime CoreML execution provider](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html)
- [OpenVINO model optimization and NNCF](https://docs.openvino.ai/2025/openvino-workflow/model-optimization.html)
- [LiteRT post-training quantization](https://ai.google.dev/edge/litert/models/post_training_quantization)
