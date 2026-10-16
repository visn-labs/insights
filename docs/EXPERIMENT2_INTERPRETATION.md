# Experiment 2: ordered evidence interpretation

## Outcome

The end-to-end experiment is implemented, with experiment 1 retained as the default. **The current 2B model does not reliably distinguish all action directions.** Passing API and browser checks is not an accuracy pass. Keep this opt-in until a larger model and labeled real-world action clips pass the same direction tests.

## Use it

Start the existing stack with `sh scripts/run_memory.sh`, then open `/#/workspace` → Video memory.

- **Indexing:** enable **Experiment 2 · Interpret ordered frames**. The existing priority-event budget controls how many events receive sequence descriptions. This replaces single-frame enrichment for that request.
- **Questions:** enable **Experiment 2 · Explain visual evidence**. Retrieval first selects candidates; the interpreter then reads up to three clips, with the actual ordered images and event metadata. It does not change retrieval ranks.
- Expand **Review ordered frames and observations** to inspect the eight sampled images, source-relative timestamps, observations, and limitations. The original event clip remains playable underneath. Expanded context can extend beyond that original clip; the displayed images show the expanded window.
- Leave both switches off to use experiment 1. Legacy LM Studio caption/synthesis switches are disabled while the corresponding experiment 2 switch is active.

The model is `Qwen/Qwen3-VL-2B-Instruct`, downloaded lazily into `.retrieval/huggingface`. It uses the existing retrieval environment, with no additional Python dependencies. See the [official model card](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct). The evaluated weight snapshot is `89644892e4d85e24eaac8bacfd4f463576704203`.

```bash
.venv-retrieval/bin/python tools/semantic_memory_server.py \
  --data-dir "$PWD/data" --port 8186 \
  --interpretation-model Qwen/Qwen3-VL-2B-Instruct \
  --interpretation-revision 89644892e4d85e24eaac8bacfd4f463576704203
```

`--interpretation-frames` defaults to 8 (2–16); `--interpretation-frame-size` defaults to 384 (128–512). Model arguments are independent of the experiment 1 embedding/reranking models. Retrieval and generation share a serialized worker; weights are released between tasks to bound memory. This is a local experimental throughput ceiling, not concurrent production inference.

## Evidence contract

1. Filter and retrieve as in experiment 1.
2. Expand each selected event by up to two seconds on either side, clamp to retained source duration, and cap the window at twenty seconds. Long events use the first bounded window, so later activity may be missed.
3. Decode interval-midpoint images in order. Timestamps are source-relative seek targets, not exact decoded presentation timestamps or action boundaries.
4. Supply the question, ordered images, timestamps, and event metadata. Metadata is context, not proof. The prompt describes first and last states before choosing an action.
5. Validate event IDs, response shape, source bounds, ordered sample times, visual frame data, and citations. A model `supported` claim needs two distinct citations marked `target_visibility: visible`. `not_observed` requires absent first/last citations and no visible/unclear citations. `uncertain` covers ambiguous targets or transitions. These structural checks cannot establish semantic truth or detect every contradictory explanation.
6. Near-identical sampled images trigger deterministic `uncertain` abstention. The guard compares mean absolute per-channel RGB difference against 0.01 on the 0–255 scale, allowing only tiny codec variation. It can miss subtle/unsampled action; it is not a motion detector.
7. Persist interpretation JSON in the worker SQLite database. Cache keys include source content hash, interval, question, metadata, resolved model snapshot, prompt version, and sampling/guard settings. Returned frame JPEGs are decoded from the retained source; corrupt cache entries are regenerated.
8. Return explicit interpretation notes on failure while preserving retrieval matches. No generated claim updates deterministic tracking facts. The sequence result has no calibrated confidence; the legacy indexing description's confidence field remains zero for this path and is not a probability.

Both job and query requests use `temporal_enabled: true`. See [API](API.md). Existing manifests load with defaults and source durations are rehydrated from their camera records. The experiment 1 source snapshot is retained locally in `data/baselines/experiment1-2026-10-15/` with a SHA256 manifest.

## Evaluation, 2026-10-15

### Accuracy probes

`tools/evaluate_temporal.py` builds schematic six-second door, object/table, and parking-space clips. Each reverse probe contains the exact same frames in reverse order. Expected labels are recorded for evaluation but never sent to the model. This is a small diagnostic set, **not a CCTV benchmark**.

| Pair | Observed result with evidence-first prompt |
| --- | --- |
| Arriving / leaving | Both directions correctly named on the parking fixture. |
| Placing / removing | Placing identified; reversed removal incorrectly called placing. |
| Opening / closing | Opening identified; closing label appeared, but the explanation claimed closed-to-open motion. Counted as a failure, not a correct answer. |
| Repeated identical stills | Unguarded model hallucinated motion in all three controls. Final static-evidence guard abstains; this is an application safeguard, not improved model reasoning. |
| Retained highway video | Actual images reached the model, but it confused disappearance with movement away and misdescribed vehicle positions. Direction accuracy failed visual review. |

The first prompt also confused closing and removal; revisions and raw results are retained in `output/experiment2/`. The final prompt/guard version is `ordered-evidence-v3`. Never use these results to claim the action pairs are solved. The recommended next evaluation is a compatible larger instruction model with the same clips and an independently labeled real-world action set; preserve the reversal and static controls.

### Integration checks

- Rust tests cover timestamp/citation validation and baseline retrieval behavior.
- Python tests cover real frame decoding, chronological sampling, cache reuse/invalidation/recovery, malformed citations, and static abstention. Model outputs are stubbed only in these contract tests.
- Real Qwen experiment 1 browser regression returned four highway candidates for both truck and natural-language searches.
- Real question-time workflow returned three interpreted highway candidates, each with eight loaded frame images; desktop/mobile checks found no browser errors or horizontal overflow.
- Retained highway footage was served over local HTTP and indexed through the normal API: one event, one sequence interpretation. This reused saved footage; it was not a fresh live-stream availability test.
- An unavailable worker test preserved all four baseline matches and ranking, with explicit interpretation failure notes.

Artifacts are local and ignored by Git: `output/experiment2/` for direction probes, static controls, indexing and fallback results; `output/playwright/experiment2/` for browser JSON and screenshots. Do not confuse these integration successes with the failed accuracy cases above.

## Reproduce

```bash
cargo test --offline
.venv-retrieval/bin/python tools/check_retrieval.py
.venv-retrieval/bin/python tools/check_interpretation.py
.venv-retrieval/bin/python tools/check_memory_runner.py

# With the worker on 8198 and data/highway-validation as its evidence root:
.venv-retrieval/bin/python tools/evaluate_temporal.py
.venv-retrieval/bin/python tools/evaluate_temporal.py --still-only \
  --output output/experiment2/still-controls.json

# With the app on 8197 and the saved highway recording:
NODE_PATH=/private/tmp/visn-ui-qa/node_modules node tools/check_temporal_workflow.cjs
.venv-retrieval/bin/python tools/check_temporal_index.py
```

The browser/indexing scripts intentionally target the retained local validation recording. Set `VISN_TEST_JOB` for another completed recording in the browser test; adjust the indexing fixture path for another source. Run model checks sequentially because the worker accepts one inference request at a time.

## Three-stream follow-up

The [live-stream evaluation](LIVE_STREAM_EVALUATION_2026_10_16.md) compared eight and sixteen images. More images corrected one direction label but did not reliably ground the cited explanation, so eight remains the default. The decoder now enforces PTS interval bounds and repeats the last valid in-window image for empty late seeks; provenance/cache identities use `ffmpeg-pts-bounded-midpoints-v4-hold-last`. Sample times remain approximate seek targets.

### Query-focused grounding (v6)

The worker first inspects each image independently for target visibility, then supplies only target-visible images to the sequence model. It compares the first/last **visible target** samples, rather than forcing window endpoints. Each observation declares `target_visibility` (`visible`, `absent`, `unclear`). Intermediate citations are allowed. A missing target is reported as `not_observed` in sampled evidence, not proof that an event never happened. Answers must stay focused on the queried target. Both Python and Rust enforce the status/visibility contract; neither can verify a model's pixel-level claims. The frontend displays the distinct absence state and each observation's visibility.

The prompt version changes the interpretation cache key, preserving previous results without reusing v3 answers. Old stored observations deserialize with empty visibility for compatibility; they cannot pass the current worker-response validator as supported evidence. Sampling remains eight sparse images with approximate seek times.

The independent audit can deterministically return `not_observed` only when every sampled image is classified absent. Fewer than two distinct visible target images produce `uncertain`. Motion synthesis cannot cite an absent or ambiguous audited frame; disagreement becomes `uncertain`. Saved citations use the independent image descriptions rather than a synthesized trajectory narrative. These are two passes of the same model, not independent annotators or guaranteed object detection, and add inference latency.

Malformed single-image JSON becomes `unclear` for that image. Invalid or contradictory motion synthesis becomes a structured `uncertain` response with the audited samples. Decoder/model availability failures still preserve retrieval with an interpretation note. No generated JSON is repaired by guessing missing fields. The motion pass returns a compact answer and two original indices; public citation descriptions come from the image audit.

## Stronger-model follow-up

The [completed model comparison](LIVE_STREAM_EVALUATION_2026_10_16.md#stronger-model-comparison-and-citation-safeguard) adds an optional local API interpreter and tests a cached 4B MLX configuration on identical visual inputs. It improves some direction/absence answers but fails the full grounding gate; 2B remains the launcher default and temporal interpretation remains opt-in.

Supported actions must cite visually distinct evidence, including when served from cache. Python abstains on unchanged cited images; Rust rejects repeated encoded image citations. This guard does not verify semantic correctness. The optional API uses loopback HTTP, requires a pinned revision/fingerprint, sends lossless images, and verifies the returned model identifier. Its MLX runtime belongs in a separate environment because its Transformers requirements differ from retrieval.

## Target-grounded interpretation (2026-10-16)

Prompt `separate-presence-transition-v8` separates question parsing from visual evidence. A text-only pass extracts the physical object, requested action, and reference. Single-image presence audits receive only the physical object; they cannot see the action question. Motion synthesis receives the question, independent audits, and ordered images. Accepted action responses must supply two ordered visible citations, different before/after descriptions, and a reference. These are structural checks, not proof that the descriptions are true.

For query-time tracker grounding, select an entity and enable **Explain visual evidence**. **Follow linked observations** can remain enabled simultaneously. The server limits retrieval to that entity's recording/camera, checks the current graph version and source SHA256, and passes only in-window boxes. With at least two detector observation times, it samples up to eight evenly selected observation timestamps instead of eight uniform seek times. It does not rerun tracking or infer identity across gaps.

The decoder now exposes actual presentation timestamps separately from seek targets. A crop is accepted only within 1 ms of its detector observation and with a valid normalized box of at least 4×4 decoded pixels. Each audit/comparison sees both the full frame and selected crop. Missing/ambiguous alignment becomes uncertain; boxes are never interpolated. Supported citations need different decoded times. A static target box and static normalized crop trigger abstention even when the background changes. Both crop guards run after generation and cache reads. Full frames, crops, and decoded timestamps are reviewable in the UI.

No entity selected means unconstrained interpretation. Automatic matching of a color/appearance question to one of several tracker histories is deliberately not implemented: the current model cannot reliably resolve that ambiguity. Missing or old tracker graphs require rebuilding linked observations first. Generated answers do not update entity memory.

The default model remains 2B; the optional cached 4B model was used for this stage's actual inference checks. See the [live evaluation report](LIVE_STREAM_EVALUATION_2026_10_16.md#target-grounded-interpretation-stage) for failures as well as workflow validation.

## Blind state consistency (next phase)

Prompt `blind-state-consistency-v9` and gate `blind-visual-states-v1` add a separate post-generation visual check. It receives only the physical target, reference, comparison family, and two cited images (plus aligned crops). The proposed answer, proposed direction, and previous audit descriptions are withheld. This is a separate call to the **same configured model**, not an independent model or human truth source.

The verifier returns identity (`same`/`different`/`unclear`), a visible-reference boolean, and enum states. Deterministic tables map closed/ajar/open, off/on-reference, outside/inside, and far/middle/near changes to transitions. A supported answer needs matching identity, visible reference, a genuine enum transition, and agreement with the synthesis's explicit transition. Tracker camera-distance claims additionally require a box-area change of at least 15% in the matching direction; selected crops below a 16-pixel short edge abstain. These conservative thresholds are uncalibrated safeguards, not confidence scores.

The gate runs after both fresh generation and cache reads. A test reuses one cached generation but changes the blind state response, proving the old claim becomes uncertain. Accepted public answers use a template generated from the checked transition; unchecked synthesis prose is not published as the answer. Rust rejects v9 supported responses with missing, rejected, mismatched-transition or mismatched-citation gate evidence. The UI displays the separate-check result and reason.

Results and remaining failures are in the live evaluation report. Human review is pending: [review instructions](STATE_REVIEW.md). Generated interpretation never updates tracker facts.

## Selected-track visibility quality (v10)

Human review exposed an absence/uncertainty error. `track-visibility-quality-v10` applies the existing 16-pixel crop short-edge limit before presence audits, not only after motion synthesis. Missing/tiny selected crops become unclear without a visual model call. A failed target match in a larger selected crop also remains unclear; it cannot establish scene-wide absence. A post-cache guard revalidates raw `not_observed` selected-track responses. Unconstrained absence checks remain available. Rust rejects v10 selected-entity absence and still requires checked state proof for supported answers.

The follow-up review has completed. Initial v9 low-visibility validation passed 1/2; after the fix, both low-visibility and both prior identity controls abstain. These are development safeguards, not positive action accuracy. See the evaluation report for source-bound reviews, original failures and separate post-fix outputs.

## Source-detail crops

Selected trackers now default to `--interpretation-crop-mode source`. Context frames remain at the configured preview size. A separate bounded decode provides source-detail frames at the same seek targets, with a maximum long edge of 1920 pixels and no upscaling. Their decoded PTS must exactly match the context sequence before any box is applied. Crops are extracted from these detail frames, then downsampled only when needed to fit a 768-pixel model-input long edge.

`provenance.crop_policy` versions the crop path/settings in generation cache keys. `provenance.crop_evidence` records mode, decode/model limits, no-upscale policy, context/detail/original-crop/model-crop dimensions and decoded timestamps. The UI shows the dimensions of each returned source crop. Untracked interpretations and ordinary embedding/reranking context sampling do not need an extra detail decode. `--interpretation-crop-mode preview` retains the prior crop path for controlled comparisons and historical low-resolution review evaluation.

`tools/select_clear_tracks.py` chooses development examples with at least two detector observations, a source crop short edge of at least 48 pixels, at least one second of sampled history, and detector area variation of at least 1.25× inside a 20-second window. Geometry is a selection heuristic, not verified identity, sharpness or motion truth. Cameras without a qualifying track are skipped. Future review generation reuses this selection and source crop path instead of selecting the longest tiny track.

Old human visibility labels are bound to the inspected crop resolution. `tools/evaluate_reviewed_states.py` rejects a response whose crop dimensions differ from the reviewed packet. Do not transfer labels from blurry previews to source-detail crops without review. Existing completed packets remain unchanged. `--development` marks reused evidence explicitly; it is not presented as untouched acceptance footage.

The controlled four-case comparison improves actual target pixel area by approximately 1.6–2.8×, with identical context images and timestamps. Both modes still abstain on every case; action accuracy has not improved in this experiment. See the live report for artifacts and remaining interpretation errors.
