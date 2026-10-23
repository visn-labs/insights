# Sanitized media integration candidate

## Status and scope

The BP03 media path now executes real YuNet face detection and a published YOLOv9 plate ONNX detector, replaces detected regions before persistence, verifies every lossless encoded frame, and connects the resulting evidence to uploads, camera recording, detector inputs, retrieval and playback.

**This is a working integration candidate, not completed privacy or generic-video acceptance.** The default remains the explicitly reported `research-raw-v1` control profile while independent coverage/utility evaluation and alternative-detector comparisons are open. Enable `sanitized-v1` only for its declared candidate scope. This increment does not complete BP00–BP12 or promote a research algorithm as the best implementation.

## Implemented boundaries

1. The sanitized recorder opens the source in memory through PyAV. It does not first copy raw encoded media to disk. Every consumed video frame goes through both detectors before encoding or ordinary observation. Detector/shape/output failures abort the job; they never become empty successful detection lists or a raw-copy fallback.
2. Uploaded encoded content is bounded to the smaller of the configured upload limit and 64MiB in memory. There is no raw staging file. Larger uploads are explicitly rejected in this profile; resumable large-file edge ingestion remains later work.
3. Face inference uses native-size padding rather than shrinking the source. Plate inference uses the author's FP32 letterbox/output contract. Each class has its own conservative mask history, and original current detection extents cannot be lost to smoothing. Masks use outward integer rounding and solid replacement.
4. Only sanitized frames enter the lossless FFV1/Matroska evidence encoder. Audio, source metadata and other source streams are not copied. Geometry changes and unsupported submillisecond/nonmonotonic timestamp sequences fail rather than silently dropping frames. This candidate accepts 8-bit decoded input; higher-bit-depth sources are explicitly rejected rather than silently reduced to 8 bits.
5. A per-frame ledger stores actual source PTS/timebase where available, mapped evidence time, replacement regions and sanitized BGR hashes. Encoded pixels, masks, frame count and timestamps are checked by decoding every output frame. A hashed manifest is written as the final commit marker. Partially created files have no valid committed status.
6. The Matroska evidence clock uses milliseconds; the ledger preserves exact rational source time and discloses the maximum 0.5ms mapping quantization. Timestamp collisions are rejected. No exact-encoded-PTS claim is made for arbitrary source timebases.
7. Untimestamped live MJPEG explicitly uses decoder-observed monotonic arrival. It does not pretend synthesized decoder timing is a camera capture timestamp. The manifest reports that pre-decoder gaps and undelivered frames cannot be reconstructed. Reconnect/session splitting and comprehensive live gap accounting remain open.
8. Browser upload derivatives are created from verified sanitized evidence, never raw compressed packets. They contain H.264 video only and are hash-bound to the canonical sanitized source. Odd dimensions are padded, not rescaled. Representative JPEGs and event clips are likewise derived after sanitation and bound to their source by recorded hashes.
9. Rust verifies committed evidence before using detector inputs or exposing memory results. Artifact serving verifies source/ledger and requested derivative hashes. Question retrieval verifies selected source commits before accessing evidence. Changed artifacts are rejected rather than silently reindexed under an old privacy claim.
10. Strict-profile restart excludes historical job manifests that lack sanitation provenance; original recordings and labels are not modified or retroactively relabeled. Existing source-scoped memory and experiment paths remain the baseline in the raw research profile.

`src/privacy_media.rs` provides the common commit checks and worker entry point. `tools/privacy_media.py` performs inference, encoding and encoded-pixel verification. `tools/event_memory_runner.py`, `src/memory.rs`, `src/pipeline.rs`, `src/api.rs`, `src/domain.rs` and `src/store.rs` carry the actual wiring.

## Models and runtime

`tools/privacy_models.lock.json` pins model bytes and SHA-256 values. Preparation refuses changed or oversized downloads. No inference-time automatic weight download or pickle loading is used.

- YuNet: OpenCV Zoo `face_detection_yunet_2023mar.onnx`, pinned repository revision and publisher LFS digest.
- Plate candidate: Open Image Models `yolo-v9-s-608-license-plates-end2end.onnx`, local SHA-256 pin of the published release asset.
- Local replay runtime: Python 3.13, PyAV 19.0.1, OpenCV 4.14.0, ONNX Runtime 1.31.0 CPUExecutionProvider and NumPy 2.5.4. Each manifest records actual runtime versions/settings and sanitizer/tracker code hashes.

The plate adapter follows the [author's preprocessing/output implementation](https://github.com/ankandrew/open-image-models/tree/f22000e02b30642f317cdba7755c0631638b109e/open_image_models/detection/core/yolo_v9); it deliberately treats inference exceptions as fatal rather than returning no detections. [YuNet's official model source](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet) supplies the pinned face model. Neither source establishes project-specific privacy accuracy or speed.

## Configuration and reproduction

Prepare the existing project environment and model cache:

```sh
uv pip install --python .venv-retrieval/bin/python -r tools/requirements-privacy.txt
.venv-retrieval/bin/python tools/prepare_privacy_models.py
cargo build --offline
```

Run a separate candidate service with an isolated data directory:

```sh
VISN_BIND=127.0.0.1:8232 VISN_MEDIA_POLICY=sanitized-v1 VISN_PRIVACY_PYTHON=.venv-retrieval/bin/python VISN_PRIVACY_MODEL_DIR=.retrieval/privacy-models VISN_DATA_DIR=output/depin-implementation/bp03-service-data target/debug/visn-phase0
```

Use `copy` or `transcode` event clips. `reference` is explicitly unsupported by this profile because canonical FFV1 is not a browser playback codec. Production capability flags stay unqualified; the capabilities response reports the actual media policy and upload bound. Availability of this profile is not proof of raw media remaining at camera premises: the worker must be deployed inside the owner's boundary for that deployment claim.

Fast regression tests:

```sh
.venv-retrieval/bin/python tools/check_privacy_media.py
.venv-retrieval/bin/python tools/check_memory_runner.py
cargo test --offline
cargo fmt --check
node --check static/app.js
git diff --check
```

The first command skips the two explicitly opt-in real-model tests unless `VISN_PRIVACY_REAL_MODELS` names a directory containing the cached public `lena.jpg` and `plate.png` smoke images. Fixture detection tests establish pipeline behavior, not model recall. Run the full local smoke suite with:

```sh
VISN_PRIVACY_REAL_MODELS=output/depin-implementation/bp03-fixtures .venv-retrieval/bin/python tools/check_privacy_media.py
```

Source URLs and image hashes are recorded in `output/depin-implementation/bp03-fixtures/manifest.json`. These are public demonstration images, not independent acceptance clips. The test suite creates temporary sequences and a loopback multipart-MJPEG server; the model path must already exist.

For the real HTTP workflow, serve the cached `face-sequence.mkv` fixture on loopback and use a newly named output directory:

```sh
python3 -m http.server 8233 --bind 127.0.0.1 --directory output/depin-implementation/bp03-fixtures
.venv-retrieval/bin/python tools/check_privacy_workflow.py --data-dir output/depin-implementation/bp03-service-data --output output/depin-implementation/bp03-next-replay
```

The workflow requires the candidate service on 8232 and exercises real upload/recording APIs. Its source is the public face-demo sequence, not a user's private camera. The script writes new artifacts, checks tamper rejection and restores any test mutations in `finally` blocks. Do not aim it at a shared production service.

## Validation evidence

| Check | Observed result | Artifact |
| --- | --- | --- |
| Full privacy suite |7 tests pass, including real face/plate sequences, encoded-pixel verification, audio removal, VFR mapping, model absence/tamper, high-bit-depth rejection and live MJPEG arrival clock | `output/depin-implementation/bp03-pixel-tests.log` |
| Rust regressions |64 pass,0 fail,2 ignored | `output/depin-implementation/bp03-rust-tests.log` |
| Existing experiment-runner regressions |5 pass | `output/depin-implementation/bp03-experiment-regressions.log` |
| Existing memory-runner regression |1 pass | `output/depin-implementation/bp03-memory-baseline.log` |
| HTTP integration |Latest replay passed 24 requests/assertions; earlier replay passed 25 with a different asynchronous polling count | `output/depin-implementation/bp03-workflow-latest/results.json` |
| Missing-model HTTP behavior |Upload rejected; recording failed; no media files created | `output/depin-implementation/bp03-fail-closed-http.json` |
| Browser playback and restart |Recovered recording played in Chrome;2s duration, advancing time,4 decoded frames, no media error;390px viewport without horizontal overflow | `output/playwright/bp03-privacy/verification.json`, desktop/mobile screenshots |
| Optional-model outage behavior |Visual preparation service deliberately absent; keyword search/media remain usable. Added 5–60s retry backoff; browser observed only 2 failed GETs over 8.5s instead of the normal 1.5s polling cadence | Browser verification and CLI snapshots under `output/playwright/bp03-privacy/` |

Native PyAV/OpenCV duplicate Objective-C class warnings and ONNX Runtime device-ID fallback warnings appear in local logs. No crash was observed in these tests. Runtime/platform qualification remains open; local smoke success is not evidence of Linux/NVIDIA readiness.

## Remaining acceptance and next work

- Independent dense privacy annotations across small/occluded/profile faces, plates, lighting, blur, compression and movement, including negative cases; statistical miss/leakage reporting and sanitized task utility.
- Compare stronger face candidates and plate scale/tiling alternatives before selecting a default. The current pair is an integration candidate, not an accuracy winner. Appearance redaction also does not establish complete anonymity.
- Camera reconnect/clock-reset segmentation, gap/completeness ledger, bounded long-running edge capture, durable cancellation/retry, orphan cleanup and multi-process commit coordination.
- Explicit per-workspace authorization and authenticated edge enrollment; historical-media migration/revocation, downstream cache/index invalidation and privacy policy changes.
- Real semantic indexing and temporal interpretation acceptance on sanitized evidence. This HTTP/browser replay deliberately used keyword retrieval while the optional visual service was unavailable; it does not establish VLM accuracy.
- Resource/latency comparisons under independent mixed load and target hardware, final profile promotion/rollback, contributor and insight workflow acceptance, and the remaining BP04–BP12 alternatives.

Source evidence remains available as the research control. Strict-profile media is separately derived, versioned and labeled. No original masters, existing reviewer labels or logo design were changed.
