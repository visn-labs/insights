# Package 6 — Bounded grouped FFmpeg decoding

Date: 20 October 2026. First P2 increment in the [general video plan](GENERAL_VIDEO_IMPLEMENTATION_PLAN.md), following [package 5](GENERAL_VIDEO_PACKAGE_5.md).

## Implementation

The retrieval worker now accepts `--decode-policy grouped-select-v1`. The default remains `single-seek-v1`. Both paths use the existing bundled FFmpeg, PNG encoder, scaling expressions, RGB conversion and sampler contract. No dependencies, model precision, prompts, frame budgets or verification thresholds change.

For each frame-sampling request, the candidate deduplicates missing seek targets, sorts them for decoding, then restores the caller's original order and duplicates. A bounded FFmpeg `select` expression emits the first available frame crossing each target. A single frame may satisfy several targets. There is no FPS conversion, interpolation, or seeking across a retained-file boundary. The existing behavior for empty seeks is retained: use an in-window first frame when possible, then hold the last valid frame for empty later seeks. Existing interpretation warnings about sparse/repeated evidence remain in place.

The group uses at most 32 unique targets, spans at most 20 seconds from its first target to the interval end, and has a conservative 32 MiB PNG bound. Oversized requests retain independent seeks. A 30-second subprocess timeout matches the existing path. PNGs live in a request-scoped temporary directory, deleted on completion/failure; no persistent decoder session is introduced.

The parser checks a single time base, sequential output numbering, ordered/in-window source PTS, stable output format/dimensions, corresponding PNG/PTS counts and payload size. Decoder errors, corruption, premature/truncated input warnings, ambiguous output or timeout discard the group and use independent seeks. Source identity/content is rechecked before publishing decoded cache entries and returning frames. A changed source fails the request rather than mixing evidence. This is conservative fallback, not a new source-quality certification: the legacy decoder can itself recover/hold frames from damaged footage.

Selection uses FFmpeg's documented [`select` timestamp variables](https://ffmpeg.org/ffmpeg-filters.html#select_002c-aselect); source-time handling retains the established [`-ss`, `-copyts` and `-start_at_zero` behavior](https://ffmpeg.org/ffmpeg.html). Integer PTS and time base validate grouped order, while the public decoded timestamp remains the existing rounded millisecond value. This package does not claim a new sub-millisecond provenance API.

Implementation is in `tools/grouped_decode.py` and the shared `Retrieval.frames` path. Embedding, reranking and sequence context therefore use the same policy. Equivalent-transform work-cache entries are reused; requests with different size/upscale policies remain distinct. Full-resolution target detail at the existing 1920-pixel limit is outside the conservative group byte estimate and continues through independent seeks. Context/detail are not derived by a new resize chain, which could change pixels.

`/health` reports `decode_policy`; search provenance records it inside `work_cache`, together with `grouped_calls`, `grouped_fallbacks` and existing `ffmpeg_calls`. Telemetry adds `ffmpeg_grouped_process`. An attempted group and its independent fallback both count toward subprocess work. Metadata, score ordering, image-return isolation and detached RGB images remain compatible.

## Operation and rollback

Add `--decode-policy grouped-select-v1` to the existing worker launch command, preserving pinned model revisions and other arguments. Use an isolated `--data-dir` when evaluating a new source format. Rust uses the configured retrieval worker without a new API or UI setting. The model/sampler numerical contract and persistent vector schema remain unchanged; matching pixels/PTS are required for this engineering candidate, rather than accepting approximate frames.

To revert, stop the worker and restart with `--decode-policy single-seek-v1` or omit the flag. Preserve source recordings, labels, task/vector databases and prior outputs. The frozen pre-package implementation is in `output/general-video-2026-10-20/package6-baseline`; its manifest is verified before replay. The current default-off path is also covered by the complete regression suite.

## Validation

`tools/check_grouped_decode.py` adds actual decoder checks for:

- CFR lossless/H.264 with B-frames/MJPEG, VFR, sparse timestamps, nonzero starting PTS, fractional clocks and 10-bit input converted by the unchanged RGB path.
- Repeated/unsorted tracker targets, duplicate-only requests, resizing and no upscaling.
- Short intervals, empty first/late seeks, end-of-file, hold-last behavior and genuinely empty windows.
- Cache transform separation, returned-image mutation isolation, failed-group fallback, ambiguous clocks, byte bounds and source mutation during decoding.
- A physically truncated recording: retain exact baseline pixels/PTS/repeats through conservative fallback.
- The public sequence interpreter with real context/detail decoding and fixture generation: crops, citations, cached-result validation and safeguard outcomes match across policies. This checks interpretation plumbing, not real action accuracy.

Development testing found that FFmpeg can report truncated input as “File ended prematurely” without an error return code. The warning guard now includes premature/truncation messages. Earlier exploratory output and the failed pre-fix unit log are retained; final claims use the final replay and unit log.

A separate 60 FPS boundary reproducer found that `(pts * numerator) / denominator` can round differently from FFmpeg's `pts * av_q2d(time_base)`. At a 1.85-second target, the initial candidate incorrectly reused the previous selected image. Group assignment now uses the same double multiplication as FFmpeg; the reproducer verifies that the next frame and its PTS are selected. This is why ordinary corpus parity alone did not justify promotion.

The final replay compares every one of the 13 retained development intervals at 256 and 384 pixels, five times each, with work caches disabled and alternating control/candidate order. It compares RGB hashes, dimensions and existing decoded millisecond timestamps against frozen pre-package code. Every timing starts a fresh decoder process; the operating-system file cache is not flushed.

Pinned-model validation uses separate empty vector databases, the same cached Qwen embedding/reranker 2B revisions, MPS FP16, 8×256 frames and unchanged verification. Each profile first indexes all 13 intervals. Two queries then run five times each with warm models and disabled decode/query/rerank work caches; persisted video vectors remain present. The reranker is warmed separately. Control and candidate model processes run sequentially, not concurrently. This provides one model-cold indexing pass per profile, not five independent cold model runs.

The Rust API check prepares a recording, searches its actual visual evidence and reads an evidence endpoint. Browser checks use the real rebuilt Rust/worker services, require grouped-decode provenance, load and play a retrieved video, check 320/768/1440-pixel layouts and collect script errors. No responses are mocked in this workflow.

Final checks pass: **119 Python tests, 30 Rust tests** (two unchanged component benchmarks excluded), **130 pixel/PTS sampling pairs**, all **13 vector rows** and **10 repeated real-model retrieval pairs**. The actual Rust API prepares 4/4 intervals and returns Qwen-reranked evidence; browser playback decodes frames and advances, with no script errors or horizontal overflow at the tested widths.

Descriptive final measurements, seconds (independent control → grouped candidate):

| Workload | Control median | Candidate median | FFmpeg launches, control → candidate |
| --- | ---: | ---: | ---: |
| Fresh 256px sampling, 65 pairs | 0.175 | 0.100 | 520 → 65 |
| Fresh 384px sampling, 65 pairs | 0.228 | 0.141 | 520 → 65 |
| Warm models / disabled work caches, 10 paired queries | 6.196 | 4.610 | 320 → 40 |
| Empty-index/model-cold pass, 13 intervals, one pass each | 30.332 | 24.461 | 104 → 13 |

No grouped fallback occurred in those retained sampling comparisons. The eight-to-one process reduction is observed directly; it is not an eightfold end-to-end speedup. Five repeats per interval do not create independent sources. Query runs keep persisted video vectors but recompute query embeddings, reranking and frame decoding.

The aggregate decode p95 regressed: 256px 0.220 → 0.311s and 384px 0.239 → 0.313s. The slowest window is `live-2`, 23.52–38.76s: its 720p MJPEG group decodes 108 native frames, versus 24 total across eight independent seeks. A single sequential decode processes intervening frames, so fewer launches do not guarantee lower cost. Verbose diagnostic logs/counts are saved separately; their benign zero-error summary triggers the conservative fallback, so the group count is taken from its first process, excluding diagnostic-induced fallback. The actual info-level validation had no group fallback. This tail regression and the timing qualification below block default promotion; no data-tuned dispatch threshold is introduced from three traffic recordings.

**Timing qualification:** the final run's UTC stage timestamps contain long gaps that substantially exceed its timed monotonic work. Host suspension/descheduling is suspected; the cause was not established. Keep these durations as descriptive development measurements, not uninterrupted service latency, sustained throughput or a formal efficiency-promotion result. Raw timestamps, durations and this decision are retained in `measurements.json`/`decision.json`. No model precision, evidence budget or safeguard is reduced to obtain the measurements.

These recordings remain traffic development cases. Generic action accuracy, independent acceptance sources, live reconnect/replacement, multi-worker decode capacity and Linux/NVIDIA behavior are not qualified by this package.

Artifacts under `output/general-video-2026-10-20`:

- `package6-baseline/`: frozen source and its verified manifest.
- `package6-validation-final/`: corrected-code replay, runtime hashes, RGB/PTS comparison records, raw model responses, separate indexes, application results, logs, measurements and decision.
- `package6-python-tests-verified.log` and `package6-rust-tests.log`: complete regression output.
- `package6-manifest.json`: final code/model/input/artifact hashes and scope.
- `package6-tail-diagnosis/decoder-work.json`: native decoder-work comparison and instrumentation caveats for the regressed window.
- `output/playwright/package6-validation-final/`: actual grouped query, playback summary and inspected desktop/mobile screenshots.
- `package6-decode/`: exploratory decode comparison before final boundary correction. `package6-validation/` was deliberately invalidated by the source-fingerprint gate after the boundary bug was discovered/fixed during its run; do not use it as final validation. Older unit logs retain the truncated-input test failure that led to the warning fix.

## Reproduce

```bash
.venv-retrieval/bin/python tools/check_grouped_decode.py
cargo test --offline
cargo fmt --check
cargo build --offline
node --check tools/check_grouped_decode_browser.cjs
git diff --check
.venv-retrieval/bin/python tools/check_grouped_decode_workflow.py \
  --output output/general-video-2026-10-20/package6-next-replay \
  --repeats 5 --models --browser
```

Use a new output directory each run. Omitting `--models --browser` runs only source pixel/timestamp and decode timing comparisons without model weights or network listeners. Full validation requires the retained development corpus, frozen baseline, cached pinned models/MPS, Chrome and the existing Playwright test installation at `/private/tmp/visn-ui-qa/node_modules`. It uses ports 8230 and 8220; they must be free. Services are stopped in `finally`. Code/input fingerprints are checked again at completion.

## Scope and next work

Keep grouped decoding opt-in while qualifying more independent codecs, capture conditions and workloads. A measured engineering improvement on these inputs does not promote a generic video-understanding model or prove universal exactness. Unsupported or oversized groups retain independent seeks.

This increment groups a bounded request's missing targets and reuses the existing transform-specific cache. It does not consolidate arbitrary adjacent event requests, keep native frames across processes, replace the source archive or change ingestion/playback encoding. Persistent decoders/TorchCodec remain unnecessary until measurements justify comparing them. Remaining P2 work can extend source/PTS coverage and high-resolution decode sharing where exact equivalence is demonstrated. Actual inference microbatching remains a separate experiment with its own numerical parity gate.
