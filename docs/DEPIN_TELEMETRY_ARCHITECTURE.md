# Historical DePIN research blueprint

**Historical proposals, not production capability or measured performance.** The descriptions and quantitative claims below were not established by the implementation. The [audit](DEPIN_IMPLEMENTATION_AUDIT.md) identifies the discrepancies; the [implementation plan](DEPIN_RESEARCH_IMPLEMENTATION_PLAN.md) governs delivery and acceptance. Treat subsequent sections as the original research proposal, not a current system description.

The telemetry API currently contains evaluation prototypes. Its capability flags remain disabled until real video integration and scoped acceptance are established. Supplied liveness values cannot authenticate a source, and contribution calculations do not authorize settlement.

---

## 1. Architectural Diagnosis & Quantified Performance Gains

| Pipeline Stage | Baseline Implementation | Failure Mode at Scale | Target SOTA Upgrade | Resource / Performance Gain | Code Implementation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Stream Ingestion & Gating** | Full H.264/H.265 RGB frame decoding at fixed 10–30 FPS cadence | 70–85% compute wasted on static/empty frames; massive cloud GPU decoding bills | Compressed-domain motion vector (`AV_FRAME_DATA_MOTION_VECTORS`) & macroblock gating + MPEG-VCM | Up to 3,600 FPS CPU pre-filtering; 60–70% GPU reduction | `src/compressed_gating.rs`, `tools/bitstream_gater.py` |
| **Stream Authenticity** | Basic RTSP/ONVIF credential & uptime heartbeat check | Vulnerable to RTSP replay attacks, looped footage, and synthetic generative injection | I-frame Photo-Response Non-Uniformity (PRNU) PCE correlation + Electric Network Frequency (ENF) | Hardware-level silicon authentication with zero extra bandwidth overhead | `src/liveness.rs`, `tools/liveness_verifier.py` |
| **Privacy Obfuscation** | Single-frame detector + Gaussian blur or feature obfuscation | Missed faces under occlusion leak PII; bounded feature transforms invertible via GAN-Invert | YuNet (1.6 ms) + Kalman bbox persistence + NullFace / iFADIT disentangled anonymization | Sub-2 ms detection latency; zero identity leakage; preserved pose utility | `src/privacy_engine.rs`, `tools/privacy_redactor.py` |
| **Core CV Extraction** | Generic YOLO + single tracker + uniform ViT frame encoding | FPS drops 41% in dense crowds; ViT memory bottleneck; bounding boxes fail >50 heads | Adaptive ByteTrack/BoT-SORT/TrackTrack + STEERER/FIDTM density + vid-TLDR token merging | 29.6–700+ FPS tracking; 8.6% MAE reduction in dense crowds; 50% ViT token pruning | `src/adaptive_tracking.rs`, `src/anomaly_cascade.rs`, `tools/adaptive_cv_worker.py` |
| **Storage & Runtime RAG** | Relational DB + uncompressed FP32 vector index with flat top-$k$ matching | 1.83 TB RAM per 1B vectors; loses temporal event causality and fails intent queries | ClickHouse H3 OLAP + 1-bit Binary Quantized DiskANN + VideoStir / EC-RAG event graphs | 96% vector memory reduction (32x compression); +4.96% to +11.67% QA accuracy | `src/olap_h3.rs`, `src/quantized_diskann.rs`, `src/video_rag.rs` |
| **Proof-of-Contribution** | Unbacked "camera connected" or "hours streamed" volume rewards | Runaway token velocity, price collapse, Sybil farming on idle alleys | Multiplicative 5-factor scoring: $W_{i,t} = L_{i,t} \cdot Q_{i,t} \cdot H_{i,t} \cdot D_{\text{H3}(i),t} \cdot M_{\text{loyalty}}(i,t)$ | 100% Sybil-resistant payouts tied to real entropy, demand, and hardware liveness | `src/poc_engine.rs` |

---

## 2. Ingestion, Compressed-Domain Gating & Physical Liveness

### 2.1 Compressed-Domain Motion Gating
- Parses Network Abstraction Layer (NAL) unit headers and partial entropy bitstream side-data (`AV_FRAME_DATA_MOTION_VECTORS`).
- Classifies prediction blocks into 6 Macroblock Type Categories (MTC):
  - `Intra4x4`: Structured foreground changes within P/B slices.
  - `Intra16x16`: Homogeneous intra macroblocks.
  - `Inter16x16`: Uniform motion.
  - `InterSubPartition`: Detailed motion down to $4 \times 4$.
  - `DirectBiPredictive`: Bi-directional interpolation.
  - `Skip`: Zero motion residual.
- Classifies motion vectors into:
  - `BMV` (Background MV)
  - `EMV` (Edge MV)
  - `FMV` (Foreground MV)
  - `NMV` (Noise MV)
- Normalizes H.265 prediction blocks into $4 \times 4$ index maps, applying spatial median filtering and bounding-box clustering to extract ROIs directly on CPU at up to 3,600 FPS.
- Rate-Task loss optimization (MPEG-VCM Part 2):
  $$\mathcal{L}_{\text{VCM}} = R + \lambda \cdot D_{\text{task}}$$

### 2.2 Physical Liveness & Sensor Fingerprinting
- **Photo-Response Non-Uniformity (PRNU)**:
  - Multiplicative sensor pattern noise extracted from compressed I-frames.
  - Peak-to-Correlation Energy (PCE) calculated against initial $M = 200$ calibration frame signature:
    $$\text{PCE} = \frac{C(r_0, c_0)^2}{\frac{1}{|A| - |\mathcal{N}|} \sum_{(r,c) \notin \mathcal{N}} C(r, c)^2}$$
  - Authentic streams yield $\text{PCE} \ge 50.0$ (baseline $\sim 294.9$), whereas spliced/synthetic feeds drop to $\le -0.10$.
- **Electric Network Frequency (ENF)**:
  - Sub-band luminance Power Spectral Density (PSD) extracted around regional nominal frequency ($50\text{ Hz}$ in India/Europe, $60\text{ Hz}$ in North America).
  - Cross-correlated against instantaneous real-time power grid trace ($\rho_{\text{ENF}} \ge \tau_{\text{ENF}}$).
- **Binary Liveness Gate**:
  $$L_{i,t} = \mathbb{I}(\overline{\text{PCE}}_{\text{I-frame}} \ge \tau_{\text{PCE}}) \cdot \mathbb{I}(\rho_{\text{ENF}} \ge \tau_{\text{ENF}})$$

---

## 3. Non-Negotiable 3-Tier Millisecond Privacy Pipeline

- **Tier 1 (YuNet Millisecond Detection)**: Ultra-compact face detector (75,856 parameters) executing in 1.6 ms with Space-to-Depth Convolution (SPD-Conv) preserving tiny distant face representations.
- **Tier 2 (Kalman Persistence & 2-Pass ByteTrack-Style Association)**:
  - High-confidence ($\ge 0.60$) and low-confidence recovery ($\ge 0.10$).
  - Constant-velocity Kalman state continues projecting and dilating redaction masks by 20% along tracklets for up to 15 frames grace window during occlusions, eliminating transient PII leaks.
- **Tier 3 (Decoupled Sanitization)**:
  - Irreversible cryptographic erasure (zero-entropy noise overwrite) defeating GAN-Invert reconstruction attacks.
  - Asynchronous 1-FPS disentangled non-PII attribute extraction (NullFace / iFADIT style: demographics S1, clothing/accessories S6, pose/gaze angles) with immediate visual crop purging from volatile memory.

---

## 4. State-of-the-Art Core CV Extraction & Anomaly Cascade

### 4.1 Adaptive Multi-Object Tracking & Density Handover
- Pinned tracker configurations (`bytetrack.yaml` and `botsort.yaml`), guarding against unpinned library default swaps (e.g. Ultralytics v8.4.76 swapping to TrackTrack).
- Dynamic routing:
  - Sparse/moderate scenes ($<20$ objects): ByteTrack or OC-SORT / Hybrid-SORT.
  - Camera vibration / pole shake: BoT-SORT / Deep OC-SORT with Camera Motion Compensation (CMC).
  - Extreme downsampling: SR-Track (AAAI 2024).
  - High-density threshold ($>25$ objects or $>35$ tight occlusions): Handover from bounding box tracking to crowd density estimation & point localization (FIDTM / STEERER / LCDnet) using Focal Inverse Distance Transform Maps.

### 4.2 Hierarchical Anomaly Cascade & HyperGLM VidSGG
- **Stage 1**: CPU unsupervised Isolation Forest on motion vector magnitude and feature norms (cuts GPU invocation by 60–70%).
- **Stage 2**: Deep anomaly scoring (VadCLIP / BN-WVAD / LAVAD) with temporal smoothing over 5–10 consecutive frames.
- **Stage 3**: Symbolic Video Scene Graphs (HyperGLM / TRACE):
  - Spatial triplets $G_t$ ($\text{subject} \multimap \text{relationship} \multimap \text{object}$).
  - Procedural temporal transitions $P$ ($\text{relationship} \to \text{relationship}$).
  - SceneSayer Scene Graph Anticipation (SGA) forecasting hazard interactions over horizon $H$.

---

## 5. Storage Architecture & Sub-Second Runtime Retrieval

### 5.1 Columnar Spatio-Temporal OLAP (ClickHouse H3)
- ClickHouse `MergeTree` tables indexed via UInt64 Uber H3 hierarchical hexagonal spatial cells (res 10–12 neighborhood, res 14–15 storefront) in `ORDER BY` + `hilbertEncode(x,y)` space-filling curves.
- `h3PolygonToCells` polygon rollups and sub-20 ms aggregations.

### 5.2 1-Bit Binary Quantized (BQ) DiskANN Vector Engine
- 1-bit quantization ($>0 \to 1, \le 0 \to 0$):
  $$\text{Memory}_{\text{BQ}} = 1.1 \times \left(\text{bits} \cdot \frac{d}{8} + 8m\right) \times N_{\text{vectors}}\text{ bytes}$$
- Shrinks 100M 384-dimensional vectors from 183.0 GB down to 19.4 GB (96% memory reduction, 32x compression).
- Hardware-accelerated XOR and POPCNT Hamming distance matching with 2x–3x oversampled disk-resident rescoring preserving $\ge 0.95$ Recall@100.

### 5.3 Spatio-Temporal Video-RAG (VideoStir & EC-RAG)
- Phase 1: Query Decoupling ($R = \{R_{\text{asr}}, R_{\text{ocr}}, R_{\text{det}}, R_{\text{type}}\}$) and OLAP fast path routing.
- Phase 2: Spatio-temporal graph $G=(V,E)$ multi-hop traversal ($V_{\text{hop}}$) and EC-RAG temporal event chains.
- Phase 3: Intent-Aware Reranking filtering candidates to ~14 intent-aligned keyframes (~2000 context tokens).

---

## 6. Algorithmic Proof-of-Contribution (PoC) Engine

Multiplicative 5-factor scoring function:
$$W_{i,t} = L_{i,t} \cdot Q_{i,t} \cdot H_{i,t} \cdot D_{\text{H3}(i),t} \cdot M_{\text{loyalty}}(i,t)$$
Payout:
$$R_{i,t} = P_t \cdot \frac{W_{i,t}}{\sum_{j=1}^N W_{j,t}}$$

1. **$L_{i,t} \in \{0, 1\}$**: Cryptographic liveness binary gate ($\overline{\text{PCE}} \ge \tau_{\text{PCE}} \land \rho_{\text{ENF}} \ge \tau_{\text{ENF}}$).
2. **$Q_{i,t} \in [0, 1]$**: Placement and optical quality (effective resolution, walkable FOV ratio, lighting stability).
3. **$H_{i,t} \in [0.1, 2.0]$**: Spatio-temporal entropy discounted by $1/\sqrt{k}$ for $k$ overlapping cameras in the same H3 resolution-14 cell.
4. **$D_{\text{H3}(i),t} \in [0.5, 5.0]$**: Enterprise commercial demand multiplier.
5. **$M_{\text{loyalty}}(i,t) = 1 + \alpha_{\max}(1 - e^{-\lambda \cdot S_{i,t}}) \in [1.0, 1.5]$**: Non-staking compliance-safe forward loyalty multiplier on active uptime days $S_{i,t}$.

---

## 7. REST API Endpoints

- `GET /api/v1/telemetry/status`
- `POST /api/v1/telemetry/gating`
- `POST /api/v1/telemetry/liveness`
- `POST /api/v1/telemetry/redaction`
- `POST /api/v1/telemetry/adaptive-tracking`
- `POST /api/v1/telemetry/anomaly`
- `POST /api/v1/telemetry/olap-query`
- `POST /api/v1/telemetry/olap-ingest`
- `POST /api/v1/telemetry/quantized-search`
- `POST /api/v1/telemetry/quantized-insert`
- `POST /api/v1/telemetry/video-rag`
- `POST /api/v1/telemetry/poc-epoch`
- `GET /api/v1/telemetry/clickhouse-schema`
- `GET /api/v1/telemetry/vector-sizing`

---

## 8. Verification and Test Suite

Execute the automated test suites:

```bash
# Rust unit & integration test suite (61 tests)
cargo test

# Python end-to-end DePIN pipeline validation suite
python3 tools/check_depin_pipeline.py
```
