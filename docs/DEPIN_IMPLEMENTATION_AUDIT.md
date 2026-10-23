# DePIN research blueprint: implementation and validation audit

Date: 2026-10-21. Scope: the supplied “Architectural Assessment and State-of-the-Art Optimization Blueprint” and the current working tree, including its uncommitted telemetry additions. This is an audit, not a production-code repair.

## Verdict

**The project does not currently implement the full blueprint, and the advertised improvements are not validated.** There are useful arithmetic, routing, graph-traversal and mask-persistence prototypes. Their new HTTP endpoints are reachable, but they are separate from the active recording/detection/memory/retrieval workflow. Naming a response `DiskANN`, `HyperGLM` or `cryptographic_erasure` does not execute those algorithms.

This conclusion applies to the new blueprint implementation. It does not mean the existing video processing, temporal evidence and retrieval experiments are all nonfunctional. Their existing paths remain separate and should remain the baseline.

## What was actually run

- `cargo test --offline`: **59 passed, 0 failed, 2 ignored**. The ignored tests were not exercised.
- `cargo build --offline`: passed.
- `python3 tools/check_depin_pipeline.py`: passed its synthetic assertions.
- Built and started a separate server on `127.0.0.1:8230`, with a fresh audit-only data directory and unavailable local VLM URL. No existing application service or data was used.
- Ran `tools/audit_depin_implementation.py` against that instance, including malformed input only on this disposable server. Results and panic logs are saved under `output/depin-audit-2026-10-21/`.
- Checked core algorithm claims against primary documentation and papers listed below.

No live camera was needed to reproduce these failures. No real-video privacy benchmark, GPU benchmark, production-scale index benchmark or downstream QA accuracy study was completed. Their improvements remain **unestablished**, rather than implicitly passing because helper tests pass.

## Implementation coverage

| Blueprint capability | Actual current implementation | Assessment |
|---|---|---|
| H.264/H.265 compressed-domain gating; MRF/K-means/EM; VCM | `src/compressed_gating.rs`, `tools/bitstream_gater.py` accept precomputed categories/vectors, compute thresholds/entropy and one bounding ROI. No video/NAL extraction, clustering or VCM codec. Rust and Python disagree on the same active fixture. | Heuristic prototype; not connected to ingestion. GPU/decode savings unmeasured. |
| PRNU enrollment and live ENF verification | Thresholds supplied PCE and correlates supplied arrays. No sensor fingerprint enrollment, video residual/ENF extraction, trusted grid reference or freshness binding. | Not physical authentication. Fail-open and forgery probes pass. |
| YuNet/SPD-Conv, tracked privacy, plates, irreversible erasure, non-PII attributes | IoU association, alpha-beta box smoothing and mask metadata; no image detector, plate detector, pixel operation or attribute model in these helpers. `KalmanState` lacks Kalman covariance estimation. | Mask prototype, not three-tier privacy enforcement. |
| ByteTrack/BoT-SORT/OC-SORT/SRTrack and FIDTM/STEERER handover | `src/adaptive_tracking.rs` selects enum names from box counts, shake and downsampling. Dense points are derived geometrically from existing boxes. No new tracker execution, density model, compensation or identity handover. | Routing policy only. This finding does not exclude trackers used separately by older workers. |
| Isolation Forest → VadCLIP/BN-WVAD/LAVAD → learned scene graphs and anticipation | `src/anomaly_cascade.rs` averages two input norms; accepts caller scores/classes/triplets; smooths them and recognizes words such as `push`/`strike`. Anticipation and token-pruning figures are assigned constants. | No Isolation Forest/model inference/token merging demonstrated. |
| ClickHouse + H3/Hilbert OLAP | DDL string plus sorted in-memory `Vec`; query scans all rows. `lat_lng_to_h3_index` constructs Hilbert-derived bits, not the H3 geographic algorithm. | No ClickHouse execution or interoperable H3 established; latency SLA unvalidated. |
| Quantized disk-backed ANN | Sign-bit packing, exhaustive Hamming scan/full sort, then FP32 cosine reranking. Both compressed and full vectors reside in Rust `Vec`s. | Real quantization arithmetic, but no DiskANN graph, NVMe provider or persistence. Recall guarantee fails a constructed case. |
| VideoStir/EC-RAG, intent scorer, OCR/ASR routing, bounded synthesis/KV reuse | Keyword entity extraction, BFS and fixed relevance increments; no learned scorer. OCR/ASR targets are always empty; synthesis is a formatted sentence. `take(target_keyframes.max(14))` enforces a floor rather than a 14-frame cap. | Graph prototype, not the paper implementation. No production graph population found. |
| Five-factor contribution rewards | Multiplicative weights and normalized allocation execute. Inputs are caller-controlled, not measurements bound to enrolled cameras; no persistent epoch settlement/idempotency in this module. | Arithmetic prototype. Quality eligibility and input validation defects reproduced. |

### Integration boundary

`src/main.rs` declares the modules; `src/store.rs:107` creates a coordinator; `src/api.rs:797` onward calls it from telemetry endpoints. The coordinator's privacy/anomaly/index/table/graph are independent shared in-memory managers (`src/telemetry.rs:63`).

No corresponding calls were found in `src/pipeline.rs` or the active memory workflow. `insert_rag_clip` and `add_rag_edge` have definitions but no production population call sites. The new Python workers are imported by the synthetic validation suite, not by the active worker configuration. `src/config.rs:141` onward still selects the existing YOLO and event-memory runners.

The active memory path records evidence before analysis: `tools/event_memory_runner.py:57` copies/links local source media or uses FFmpeg `-c copy` for network sources; `analyze` opens that evidence at line 228. `src/memory.rs:144` onward finalizes thumbnails and clips. Therefore the new code does **not** establish “no raw media persisted” or “blur before processing.” A function named `redacted` in the recorder masks source credentials, not image pixels. Whether raw data stays on camera premises also depends on deployment; these modules do not establish that boundary.

The previous highway URLs are MJPEG. They do not provide the H.264/H.265 inter-frame motion vectors assumed by the compressed-domain proposal. They can test downstream behavior, but cannot validate that particular codec optimization without a separate H.264/H.265 test set and an explicit fallback path.

## Reproduced correctness failures

Raw responses: [adversarial-results.json](../output/depin-audit-2026-10-21/adversarial-results.json). Server exceptions: [server.log](../output/depin-audit-2026-10-21/server.log).

| Severity | Probe and observed result | Consequence |
|---|---|---|
| Critical | Liveness with **0 calibration frames**, PCE **-100**, and empty traces returns HTTP 200, `is_authentic:true`, correlation 1.0, status `calibrating`. | Unknown authenticity is represented as authentic. Calibration must remain unverified. |
| Critical | Supplied PCE 300 and identical invented arrays `[1,2,4,3,5]` return `verified_live`, estimated frequency **3 Hz**. | Caller claims can manufacture liveness. Correlation alone does not validate the measurement or grid frequency. |
| Critical | A new face at confidence **0.5** produces **no mask**; only existing tracks receive low-confidence recovery. | Cannot claim zero leakage, even at the supplied-detection level. |
| Critical | Face moves from x=.10 to .15, width .10. Returned mask x=.125, width≈.120 ends at .245, while face ends at .25. | About **5% of face width** remains uncovered. Smoothing must never shrink coverage below the actual detection. No pixel-level guarantee exists yet. |
| High | Next request with no detections inherits the preceding face track. API has no camera/session key; coordinator uses one privacy manager and one anomaly history. | Cross-camera state contamination; unrelated calls advance each other's tracking/smoothing state. |
| High | Insert a 1-dimensional vector into the 384-dimensional API. Connection closes on assertion panic; a subsequent valid search also closes due to a poisoned mutex. Status still reports the feature enabled. | Malformed input disables vector operations until restart, rather than returning a validation error. Other endpoints remain alive. |
| High | Same active fixture: Python decodes with foreground ratio **.18**; Rust gates it out with **.0022058825**. | Python tests do not validate the deployed Rust policy. Input-frame geometry and counting semantics need a single contract. |
| High | 50-vector constructed corpus, dimension 384, k=10, oversampling2.5: exact top10 are IDs100–109; approximate result contains none. **Recall@10=0.0**. | FP32 reranking cannot recover neighbors excluded by sign-bit shortlisting. This is a counterexample, not an estimate of average real-data recall. |
| High | Quality .20 under configured minimum .70 is `FLAGGED`, but still gets the **entire 100-point pool** when alone. | Minimum quality is descriptive, not an eligibility gate. If intended as a soft rule, configuration/docs must say so. |
| High | Duplicate camera entries are accepted and allocated twice; a **negative reward pool** returns negative payouts. | Untrusted request values and duplicate identity are not rejected. No settlement safety demonstrated. |
| Medium | Empty RAG graph says “Temporal sequence confirmed across 0 clips.” | Unsupported confirmation despite absent evidence. |

## Performance claims: supported versus unsupported

1. **Throughput is reported, not measured.** Rust assigns `cpu_throughput_fps` 3450 or 3600; Python assigns 3480 or 3600. The Python suite asserts that this assigned number exceeds3000. No extraction/decode/GPU time is measured. Tracker expected FPS is also assigned. These cannot support savings of60–70%GPU or70–85%idle decode.
2. **Memory claim is internally inconsistent.** For d=384, M=16, N=100M, the implemented sizing formula returns183.04GB FP32 versus19.36GB BQ: **89.42% savings, 9.45×**, not96%/32×. These are decimal-GB projections, not measured allocations. Actual `QuantizedDiskAnnIndex` retains all FP32 payloads in RAM plus sign bits, so its vector payload grows from1536 to1584bytes/vector before container overhead. The advertised RAM saving does not apply to this implementation.
3. **Latency is partly cosmetic.** OLAP reports `elapsed.max(0.85)`; RAG reports `elapsed.max(1.2)`. An in-memory tiny-query duration, particularly with an imposed floor, cannot validate a ClickHouse or model-backed latency claim. All OLAP rows are visited, so its reported pruning does not represent a real storage index.
4. **Accuracy and precision have no before/after evidence here.** There are no paired model evaluations establishing preserved event recall, tracking identity, privacy recall or grounded QA gains for the proposed substitutions. The adversarial vector fixture shows accuracy loss is possible.
5. **No scale or deployment acceptance.** No100M-vector/NVMe run, GPU energy study, concurrent-camera load test, restart durability test or Linux/NVIDIA run was performed in this audit. Local compilation and unit tests establish neither those gains nor production readiness.

### Why the existing green suite is insufficient

`tools/check_depin_pipeline.py` exercises hand-authored dictionaries, supplied liveness scores and helper return values. Its phase4 repeats storage sizing and payout arithmetic inside the test; it does not call the Rust storage/reward endpoints. It supplies anomaly scores/triplets rather than detecting anomalies from video. Its “100% success” therefore describes those assertions, not completion of the blueprint. The Rust tests similarly cover prototype functions and happy paths; passing them is useful but does not negate the above failures.

## Research claims checked against primary sources

- FFmpeg's official [motion-vector example](https://ffmpeg.org/doxygen/7.0/extract_mvs_8c-example.html) sends packets to a decoder and receives frames before reading side data. `export_mvs` alone is not evidence of zero-decode parsing. A claimed decode-saving design needs measured extraction cost and a precise definition of which decoding stages it avoids.
- [H3's documented representation](https://h3geo.org/docs/core-library/h3Indexing/) describes a geographic hierarchical cell system. Filling a similar integer layout with Hilbert bits does not reproduce its cell geometry, hierarchy or interoperability. Use a conforming implementation and round-trip/geographic tests.
- [Microsoft DiskANN](https://github.com/microsoft/DiskANN) provides graph-based ANN and storage providers. The project's two in-memory vectors and exhaustive scan are a different architecture.
- [Elastic BBQ documentation](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/bbq) describes lossy binary quantization, corrective data, oversampling and reranking. A vector-payload compression factor is not total index RAM reduction; oversampling does not establish this project's recall guarantee.
- [VideoStir](https://arxiv.org/abs/2604.05418), ACL2026, uses a clip graph and an MLLM-backed intent scorer with a dedicated alignment dataset. The paper exists, but keyword increments do not implement its learned scorer or validate its results locally.
- [Camera Fingerprinting Authentication Revisited](https://www.usenix.org/conference/raid2020/presentation/maier), RAID2020, evaluates constraints and attacks on PRNU authentication. PRNU should not be presented as an unconditional cryptographic or100%Sybil-resistance guarantee.

The report itself says its original repository assessment was inferred without repository access. Its numerical targets are research hypotheses, not measurements of Visn. Its company financial statements, absolute legal-compliance conclusions and every cited model's relative ranking were not independently established by this implementation audit. They should not be used as acceptance evidence.

## Recommended sequence and acceptance gates

### 1. Correct misleading capability reporting and unsafe contracts

Mark each capability as prototype/available/wired/validated with evidence, actual model/config versions and measured timing fields. Validate dimensions, finite/ranged values, timestamps, frame geometry and reward pools at boundaries; eliminate panic-under-lock behavior. Partition state by camera/session and handle restart/reset. Keep calibration unverified; require authenticated, server-derived telemetry for rewards. Correct the quality gate and duplicate-identity handling. Regression checks must include every reproduced failure above.

### 2. Enforce privacy in the actual media path before claiming privacy

Integrate a real face/plate detector and actual irreversible pixel replacement before analytics, thumbnails, clips, external model calls and persistence. Ensure each mask includes the current detected extent and conservative uncertainty, even while smoothing. Define behavior for low-confidence new faces, missed detections, detector failure and camera changes. Test at the pixel/artifact boundary rather than only comparing boxes. This requires a deliberate deployment/data-retention policy, not merely a mode label.

### 3. Establish the generic baseline and paired evaluation

Freeze existing successful experiment behavior and model/config hashes. Use held-out clips across indoor rooms, retail, sidewalks, industrial settings, low light, empty/static scenes, camera shake, dense crowds and high-activity roads. Include MJPEG,H.264,H.265, timestamp discontinuities and occlusions. Keep tuning and acceptance sets separate; split related clips by source/time to reduce leakage. Use reviewed evidence and real action distinctions, not only object labels.

For every candidate, run the same clips/queries against the frozen baseline. Record paired quality deltas, confidence intervals, CPU/GPU time, peak RSS/VRAM, disk/network bytes and p50/p95/p99 latency after warmup. Count dropped/filtered intervals and failures in the denominator. Published FPS from another dataset or GPU is not a substitute.

### 4. Implement one real replacement per experiment

| Candidate | Minimum acceptance evidence |
|---|---|
| Compressed gating | Real codec extraction plus its full cost; retain all reviewed critical events and compare downstream recall/precision, including slow/stationary actions. MJPEG fallback stays explicit. |
| Tracking/density handover | Actual selected runtime and handover; HOTA/IDF1/ID switches and count/dwell error across sparse/dense transitions. Validate density points independently of detector boxes. |
| Anomaly cascade | Trained/calibrated stage1 and actual stage2 models; event recall/precision, false alerts per camera-hour and onset latency, including low-motion anomalies. |
| Quantized retrieval | Actual chosen index/storage backend; exact-FP32 oracle, real visual/text query distributions, recall@k/nDCG and downstream QA. Sweep shortlist size; use exact fallback where quality gates fail. Measure total resident RAM including reranking vectors. |
| OLAP/H3 | Real ClickHouse execution, official H3 conversion, polygon/track correctness, durable writes, representative concurrent query latency and ingestion load. |
| Video RAG | Populate graph from existing evidence; temporal/camera scope enforcement, evidence-grounded answers and abstention. Evaluate action direction, temporal ordering and multi-hop questions; measure scorer cost and actual visual/token budgets. |
| Contribution rewards | Trusted measurement provenance, known-camera identity, unique/idempotent epochs, quality eligibility, conservation/rounding and replay/tampering checks. |

The user's requirement is no tradeoff in accuracy/precision. Predeclare quality gates; if a candidate cannot meet them after documented diagnosis, retain the baseline or test another candidate. A non-significant result alone does not prove equivalence. Report any tolerated margin explicitly rather than hiding it in a resource-saving average.

### 5. Promote only measured gains

Keep each candidate behind a reversible flag; record the exact backend/model/version selected and failures/fallbacks. First verify correctness, then shadow paired workloads, then test target Linux/NVIDIA hardware and restart/concurrency behavior. Promote only after its acceptance record passes. Do not wire the current prototypes into production merely to make their capability flags true.

## Reproduction

Start a fresh, disposable instance using the current build:

```sh
cargo build --offline
VISN_BIND=127.0.0.1:8230 VISN_DATA_DIR=output/depin-audit-2026-10-21/isolated-data VISN_GEMMA_BASE_URL=http://127.0.0.1:9/v1 target/debug/visn-phase0
```

In another shell:

```sh
python3 tools/audit_depin_implementation.py http://127.0.0.1:8230
```

**Use a newly started instance each time.** The script intentionally ends with an invalid-dimension request that poisons that instance's vector mutex. It is not suitable for a shared service. Its output records observations; successful script completion does not mean the implementation passed acceptance.

Artifacts: `cargo-test.log`, `cargo-build.log`, `existing-python-suite.log`, `adversarial-results.json`, `source-locations.txt`, and `server.log` in `output/depin-audit-2026-10-21/`. Production source and pre-existing work were not changed by this audit.
