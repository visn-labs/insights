# Three-stream evaluation — 16 October 2026

## Outcome

Captured and indexed all three supplied HTTP MJPEG streams, ran the real retrieval and temporal models, and evaluated persistent tracker memory. The run exposed two implementation defects that are now fixed: nominal-FPS detector subsampling discarded valid observations from variable-rate footage, and sparse late seeks excluded a valid short interval from visual retrieval.

The corrected pipeline retrieves evidence well on this small pilot. Temporal action interpretation remains experimental: increasing the image count can correct an answer label while its cited explanation is still wrong. No claim of general CCTV accuracy or verified physical identity follows from this run.

The current evaluation workspace is `http://127.0.0.1:8192/#/workspace`.

## Capture and corpus

Recording requests began at **2026-10-16 05:40:46 UTC** (11:10:46 India time). Each requested a bounded 45 seconds, sparse observation at 2 FPS, and experiment 3 tracker memory. Optional model captions were disabled before labeling; queries could not benefit from generated captions.

| Camera | Supplied stream | Retained duration | Indexed intervals | Scene reviewed |
| --- | --- | ---: | ---: | --- |
| live-1 | `http://103.151.177.124:221/mjpg/video.mjpg` | 44.80s | 4 | Busy daylight toll lanes, trucks/cars, a person in a high-visibility vest |
| live-2 | `http://24.30.252.59/mjpg/video.mjpg` | 44.96s | 4 | Quiet illuminated night intersection; a brief vehicle crossing |
| live-3 | `http://103.151.177.124:89/mjpg/video.mjpg` | 44.88s | 5 | Faster daylight traffic on a divided highway |

All requests completed successfully. Source hashes and event intervals were verified unchanged across the implementation comparison. Indexed intervals are contiguous across each retained duration, including quiet periods. They are still non-overlapping; this run does not implement overlapping segmentation or continuous ingestion.

Camera overlays use their own clocks. Capturing the transports together does not establish synchronized camera chronology or shared vehicle identity.

## Fix 1: detector cadence follows timestamps

Both `tools/yolo26_runner.py` and `tools/yolo26_worker.py` previously calculated a frame stride from nominal codec FPS. All three retained files reported 25 FPS, despite substantially fewer irregularly timed frames. The old code therefore undersampled the actual footage.

The adapters now share `TimestampSampler`: select source presentation times for retained files, and receipt-clock times for live network analysis. Nominal FPS remains a fallback when source timing is unavailable. Backward/non-finite sampler timestamps fail explicitly. Local-file decoding preserves backpressure and retrieves only due frames.

| Camera | Decoded frames | Old stride-selected frames | Timestamp-selected frames | Tracker histories before → after |
| --- | ---: | ---: | ---: | ---: |
| live-1 | 221 | 45 | 193 | 37 → 60 |
| live-2 | 278 | 56 | 185 | 2 → 5 |
| live-3 | 108 | 22 | 108 | 1 → 11 |

The night result now includes a car track, and the fast highway result includes car/truck histories. More histories are **not** a measured improvement in unique-vehicle counting, recall, or identity accuracy: fragmentation, detector errors, and tracker ID switches still need ground truth. Post-fix graph rebuilds took approximately 30, 26, and 16 seconds respectively on this machine; pre-fix capture times also included recording and queueing, so they are not a fair compute comparison.

Entity memory is now `entity-memory-v2-pts`; the version participates in entity UUID construction. Previous graphs require a rebuild and their old identifiers are not silently reused. The API exposes `entity_memory_version`, and the UI suppresses outdated entity choices and explains how to rebuild. Sources and indexed clips remain intact.

## Fix 2: bounded decoding for sparse intervals

The first fast-highway interval is only 2.36 seconds. A seek near its end could have no later image before the interval boundary, although earlier images were valid. The original worker reported incomplete coverage and omitted this interval from embeddings. A single-candidate temporal query could consequently fall back from visual retrieval.

The decoder now preserves the source timeline and applies an explicit PTS selection predicate inside the requested half-open interval. Empty late seeks hold the last valid in-window image; an initial empty seek retries from the interval start. An interval containing no valid image still fails. Regression tests verify that an empty interval does not borrow an image from the following interval.

Sampler provenance is `ffmpeg-pts-bounded-midpoints-v4-hold-last`, which invalidates incompatible cached embeddings/interpretations. All 13 events achieved visual coverage after the fix. Repeated images do not prove continued visibility. Displayed sample times remain approximate seek targets, not decoded presentation timestamps or exact action boundaries; the UI marks them with `≈`.

## Retrieval pilot

The assistant visually reviewed 1 FPS contact sheets **before model queries** and froze six positive questions and three negative questions. Relevance IDs and expected answers were not changed after inference. Annotation-provenance wording was later corrected to make explicit that labels came from the assistant, not an independent human annotator; original files, hashes, and unchanged query-payload hashes are recorded in the audit artifact.

This is a nine-question diagnostic pilot over three short sources. It has no held-out set, confidence intervals, or independent expert annotation. Some scene questions are deliberately easy to distinguish. Small/brief events may be missed by the annotation sampling.

Frozen labels: [retrieval-labels.json](../evals/live-2026-10-16/retrieval-labels.json).

| Mode after decoder fix | Positive questions with relevant rank 1 | Mean interval Recall@5 | Mean nDCG@5 | Negative queries returning no candidates |
| --- | ---: | ---: | ---: | ---: |
| Lexical metadata | 0/6 | 0.0% | 0.000 | 3/3 |
| Embedding only | 5/6 | 90.0% | 0.891 | 0/3 |
| Hybrid fusion | 5/6 | 90.0% | 0.891 | 0/3 |
| Hybrid + reranker | 6/6 | 100.0% | 1.000 | 0/3 |

Hit@1 means at least one relevant event is first. Interval Recall@5 measures how much of each question's labeled relevant event set occurs in five results, then averages across positive questions. The evaluator counts failed attempts in effective-accuracy denominators and reports failures separately. All 36 post-fix retrieval calls completed in their intended mode; no fallback was counted as successful visual retrieval.

The caption-disabled lexical baseline had no useful semantic scene text, so its zero score does not establish superiority over caption-enriched lexical search. Hybrid matched embedding-only because lexical contributed no matches. Visual rankings still return nearest candidates for absent scenes; a calibrated no-match decision is not implemented and ranking candidates are not assertions that an event occurred.

Before the decoder fix, embedding-only/hybrid mean Recall@5 was 78.3% and reranked Recall@5 was 88.3%, with one event missing from embedding coverage. The same frozen labels were reused afterward.

Observed post-fix mean query latency was about 0.11s for warmed hybrid search and 21.91s with reranking over this tiny corpus. Embedding-only averaged 3.64s including its initial uncached indexing/loading work. These are sequential, cache/order-dependent measurements using `--keep-models-loaded`, not production throughput or model-only speed comparisons. Peak memory was not measured.

## Temporal comparison: eight versus sixteen images

The same Qwen3-VL-2B-Instruct snapshot, prompt `ordered-evidence-v3`, 384px image bound, bounded decoder and five frozen questions were used. Only image count changed. All five post-fix requests succeeded in each profile. Expected answers are in [temporal-labels.json](../evals/live-2026-10-16/temporal-labels.json); manual assistant review is in [temporal-review.json](../evals/live-2026-10-16/temporal-review.json).

| Question | Eight images | Sixteen images | Assessment |
| --- | --- | --- | --- |
| Green/yellow toll truck approaching | Correct toward-camera answer | Correct toward-camera label, with additional lateral detail | Coarse direction supported; extra detail needs review |
| Car crossing the night intersection | Detects its appearance, weak trajectory explanation | Detects movement but describes an incorrect left-to-right trajectory | Partial grounding; no full reasoning pass |
| Turquoise car approaching beside a container truck | Incorrectly says away from camera | Correctly says toward camera | Sixteen-image answer still claims the car is visible in the final image, where it has already left the view |
| Aircraft taking off at night | Correct absence/uncertainty | Correct absence, but `supported` status | Abstention/status consistency is unresolved |
| Boat travelling on water in toll scene | Rejects the premise, adds unrelated truck-motion claims | Rejects the premise, adds unestablished truck-visibility claims | Correct rejection does not make every generated claim grounded |

The direction label improved from one of two correct to two of two on these two probes, but **citation correctness did not become reliable**. Mean end-to-end latency across the five queries increased from 26.99s to 35.07s, with no repeated or randomized timing trials. The normal eight-image default is retained. A larger model was not downloaded or compared in this run.

The useful next change is to select and cite frames where the queried object is actually visible, rather than forcing the whole window's first/last images. This needs an independent test set and a compatible larger-model comparison before promotion. Negative answers also need a clear claim/polarity contract: `supported` must not be interpreted as proof that the requested action occurred.

## Linked memory, UI and regression checks

- All three corrected graphs passed evidence timestamp ownership, chronology and recording/camera/cluster/time-filter isolation checks: 12 exclusion checks total.
- A selected history in each source returned supporting evidence from four clips while the original retrieval result limit was one. This verifies graph expansion, not physical-identity correctness.
- Raw sources and original event records were unchanged by retracking. New entity namespaces were disjoint from the old namespaces.
- The isolated detector adapter processed the first ten seconds of the fast-highway source successfully: 80 raw detections at 26 distinct timestamps, bounded to 0–9.72s. These are detections, not unique objects.
- Browser checks passed actual playback across polling, entity-history queries and 320/390/768px layouts for all three recordings, with no browser errors. An explicitly UI-only legacy-version simulation verified the rebuild message and removal of outdated entity options.
- 26 Rust tests and 13 Python checks passed, including the new detector-cadence and evaluation-denominator checks. Formatting, JavaScript/Python syntax, and diff checks passed.

## Reproduce and inspect

All clips/models remain local. Sources are under `data/live-evaluation-2026-10-16/`. Run artifacts and original/fixed results are under `output/live-evaluation-2026-10-16/`; browser screenshots are under `output/playwright/live-evaluation-2026-10-16/`. These are Git-ignored; labels, tools, and this report are source artifacts.

```bash
# Existing captured corpus, application on 8192, worker on 8193:
.venv-retrieval/bin/python tools/evaluate_retrieval_corpus.py \
  evals/live-2026-10-16/retrieval-labels.json
.venv-retrieval/bin/python tools/evaluate_stream_behaviors.py \
  --url http://127.0.0.1:8192
NODE_PATH=/private/tmp/visn-ui-qa/node_modules \
  node tools/check_live_corpus_browser.cjs

# Cadence and evaluator regression checks:
.venv-retrieval/bin/python tools/check_detector_sampling.py
.venv-retrieval/bin/python tools/check_evaluation_metrics.py
```

The capture helper `tools/capture_evaluation_streams.py` accepts the authorized stream URLs, `--seconds`, `--url` and `--output`. Repeated captures need a new isolated corpus and new labels; the frozen evaluation intentionally rejects results from outside its labeled job set.

## Next milestone

1. Extend these labels into a held-out, independently reviewed corpus with more recording times, brief actions and similar-object crossings.
2. Add query-focused visible-object evidence/citations and compare a larger compatible temporal model against the same frozen cases.
3. Evaluate negative-query abstention and claim grounding before allowing model answers to drive entity/event links.
4. Then join the temporal and tracker-memory paths, with explicit provenance and conflicting-evidence handling. Overlapping retrieval windows and exact decoded timestamps remain pending evidence-spine work.

## Implementation follow-up: query-focused grounding, v6

Implemented `independent-target-audit-v6` in the existing opt-in Experiment 2 workflow. Each sampled image receives a separate target visibility judgment. The motion pass receives only samples marked visible, the independent observations, original indices, and event metadata. Public citations use the per-image descriptions. Python and Rust require two visible citations for a supported action; missing targets have a separate `not_observed` state. Invalid image responses become `unclear`; invalid or contradictory motion responses become structured uncertainty. The UI shows all three outcomes and visibility beside each citation. Prompt-version cache invalidation preserves previous results.

**Software validation passes; model accuracy does not.** The unchanged five-case corpus completed through the real API with Qwen3-VL-2B-Instruct at eight images/384px. No larger-model comparison was performed. Mean end-to-end latency was 39.696 seconds in this serial local run, including retrieval/model switching; this is not a throughput benchmark.

| Frozen case | v6 result | Assistant visual review |
| --- | --- | --- |
| Green/yellow toll truck | Supported: away | Incorrect. The truck approaches. |
| Night crossing car | Uncertain | Action abstains; audit hallucinates early car presence. |
| Turquoise highway car | Uncertain | Action abstains; audit incorrectly marks late images visible after the car exits. |
| Aircraft at night intersection | Not observed | Correct sampled-evidence rejection. |
| Boat at toll road | Uncertain | Avoids a boat action claim; some audit labels/descriptions remain contradictory. |

Strict positive action correctness is **0/3**, with two abstentions and one incorrect supported answer. This is not a verified quality improvement over v3 and must remain experimental. Structural visibility checks cannot establish actual pixel-level visibility, object identity, or direction. The same 2B model performs both passes and can repeat its mistakes. Retrieval rankings and tracker facts remain separate from these judgments.

Intermediate prompt-only v4 and initial two-stage v5 runs were retained under `output/live-evaluation-2026-10-16/behaviors-grounding-v4` and `behaviors-grounding-v5`. Both exposed malformed outputs and visual hallucinations; they are not successful accuracy results. v6 uses a compact internal synthesis schema and conservative invalid-output handling. Frozen labels were not changed to match any run.

Validation:

- 26 Rust tests; 15 Python tests across interpretation (6), retrieval (3), detector sampling (3), and evaluation metrics (3).
- Three real-worker static controls (door, placement, parking) all abstain through the static-evidence guard. These verify the guard, not model action recognition.
- Existing browser regression passes for all three camera recordings, linked histories, migration handling, and responsive layouts.
- Saved real-model responses for all five questions replay correctly through the rebuilt application renderer, with status text, eight images, visibility citations, and no browser errors at 320/390/1440px. This replay verifies rendering; the Python evaluation separately exercises the real API and inference.
- Rust formatting, JavaScript syntax, Python compilation, and `git diff --check` pass.

Review with result/source hashes: `evals/live-2026-10-16/grounding-v6-review.json`. Raw API responses, ordered contact sheets, and static controls: `output/live-evaluation-2026-10-16/behaviors-grounding-v6`. Browser evidence: `output/playwright/grounding-v6`.

Next accuracy work should compare a stronger compatible vision model on the frozen cases and new held-out clips, then condition interpretation on reviewed detector tracks/crops. Do not join unsupported model narration into persistent entity facts. No production-readiness or independent annotation claim is made.

## Stronger-model comparison and citation safeguard

The stronger-model comparison is complete. **The candidate remains opt-in: the action-grounding gate still fails.** A cached Qwen3-VL-4B-Instruct MLX 4-bit model improved several answers, but did not solve reliable action interpretation.

### Configurations and corpus

- Baseline: Qwen3-VL-2B-Instruct, PyTorch/MPS float16, original pinned snapshot.
- Candidate: existing `lmstudio-community/Qwen3-VL-4B-Instruct-MLX-4bit` files, served by isolated MLX-VLM 0.7.8. File-manifest fingerprint: `d4f53934afba23177de2a6caef3c7cdd5b868b5f2220b609584b1fe35a63beec`. All source model files were hashed before and after inference and remained unchanged.
- Prompts remained `independent-target-audit-v6`, with eight 384px samples. This compares practical configurations; model size, quantization, runtime, and internal processing differ. It does not isolate a parameter-count effect.
- The original five frozen questions were exercised through the real Rust API. Three additional 30-second recordings from the authorized streams supplied six pre-labeled cases: three vehicle directions, two absent-object questions, and one static control. Expected labels were never sent to either model. Request payloads, timestamps, and returned frame images match exactly across the guarded six-case comparison.
- These are small, assistant-reviewed development sets from the same cameras, not independently annotated or independent-camera acceptance tests. One frozen visibility label marks the last yellow-truck sample unclear although its saved held image contains the truck. That label was preserved; no aggregate visibility accuracy is reported.

The planned 8B download was not completed: Xet/parallel transfer stalled and later ranged requests failed. Partial files are preserved under `.retrieval`; **no 8B inference or accuracy result is claimed**. The already-cached 4B model enabled a completed stronger-model test. LM Studio's daemon also timed out, so its model files were read by the separate native MLX host instead. Retrieval's Transformers 4.57.3 environment was preserved.

### Results after the shared citation guard

| Six-case development split | 2B | 4B MLX |
| --- | --- | --- |
| Correct direction labels accepted on three vehicle questions | 1/3 | 2/3 |
| Positive questions answered uncertain | 2/3 | 1/3 |
| Explicit correct sampled-absence answers on two negatives | 1/2 | 2/2 |
| Other negative answer | Uncertain after repeated-image guard | None |
| Static control abstention | 1/1 | 1/1 |

These are label/status counts, **not fully grounded action accuracy**. The 4B tarp-truck explanation is weak, relying on facing the camera rather than clearly explaining size change. Its raw yellow-truck answer had the correct direction word but cited identical images; the shared guard rejects that action claim. The 2B model's raw train hallucination was also rejected by the same guard in the guarded run.

On the original five cases, 4B rejects both absent-object questions, abstains on the toll-truck question, gives a wrong left-to-right trajectory for the night car, and contradicts its toward-camera answer with an away-camera observation for the turquoise car. The original positive grounding gate remains failed.

Six schematic reversal probes were also run. Opening produces a false absent-target answer; closing and leaving abstain. Placing and arriving contain correct labels with unreliable state/reference explanations. Removal has a `supported` status while saying removal is not confirmed. These do not establish that the action pairs are solved. Three repeated-still controls all abstain through the deterministic application safeguard, not improved model reasoning.

### Implemented safeguard and integration

A globally changing sequence can still yield two cited copies of one image. Python now checks the actual cited images and turns such supported claims into `uncertain`. This check runs after both generation and cache retrieval, so previously cached claims cannot bypass it. A regression verifies cache reuse without another model call while the unsafe claim is rejected. Rust independently rejects supported responses citing only one distinct encoded image. The safeguard catches repeated evidence; it cannot detect incorrect identity, target states, or contradictory natural-language reasoning.

The worker now supports an explicitly configured local OpenAI-compatible interpretation API. It sends lossless PNGs, preserves the original prompt text, requires image input and a pinned model revision/fingerprint, and rejects a response from another model. API endpoints are restricted to loopback HTTP. Existing retrieval/interpretation failure handling retains retrieval evidence. `scripts/run_memory.sh` accepts `VISN_INTERPRETATION_MODEL`, `VISN_INTERPRETATION_REVISION`, and optional `VISN_INTERPRETATION_API_URL`. Its default remains the existing 2B configuration.

Validation completed:

- 26 Rust tests and 27 Python checks: interpretation 8, API adapter 4, corpus harness 5, retrieval 3, detector sampling 3, metrics 3, MJPEG/browser encoding 1.
- All five original real-API cases complete; entity evidence and all twelve exclusion-filter checks pass.
- Browser replay of all five real candidate responses passes at 320/390/1440px, with status text, model identity, eight images, citations, and no browser errors. This is renderer validation, separate from real inference/API testing.
- Three-camera playback, linked histories, migration handling, and responsive browser regression pass.
- Frozen labels/payloads and actual input frame parity verified; external model files unchanged. Formatting, syntax and diff checks pass.

The repeated-citation safeguard was motivated by this development set. Its post-change results are development validation, not an untouched final holdout. Model comparison latencies include different cold/cache states and are not a throughput benchmark.

### Reproduce and inspect

Optional native host, isolated from retrieval:

```bash
.venv-retrieval/bin/python -m venv .venv-mlx-evaluation
.venv-mlx-evaluation/bin/pip install -r tools/requirements-mlx-evaluation.txt
HF_HUB_OFFLINE=1 HF_HOME="$PWD/.retrieval/huggingface" \
  MLX_VLM_CACHE_HOME="$PWD/.retrieval/mlx-cache" \
  .venv-mlx-evaluation/bin/mlx_vlm.server --host 127.0.0.1 --port 1244 \
  --model /Users/anagha/.lmstudio/models/lmstudio-community/Qwen3-VL-4B-Instruct-MLX-4bit \
  --max-num-seqs 1
```

In another terminal, explicitly select the experimental host:

```bash
VISN_INTERPRETATION_API_URL=http://127.0.0.1:1244/v1 \
VISN_INTERPRETATION_MODEL=/Users/anagha/.lmstudio/models/lmstudio-community/Qwen3-VL-4B-Instruct-MLX-4bit \
VISN_INTERPRETATION_REVISION=d4f53934afba23177de2a6caef3c7cdd5b868b5f2220b609584b1fe35a63beec \
sh scripts/run_memory.sh
```

The fingerprint above identifies this installation's complete cached model-file manifest. A different model/conversion requires its own fingerprint. See the [MLX-VLM project](https://github.com/Blaizzy/mlx-vlm) for the native host.

Frozen labels and manual review: `evals/live-2026-10-16/heldout-model-labels.json`, `heldout-2b-review.json`, and `stronger-model-review.json`. Raw and guarded outputs, payload/image parity checks, runtime dependency snapshots, and model-file fingerprints are under `output/model-comparison-2026-10-16`. Browser evidence is under `output/playwright/model-comparison-4b`.

**Next implementation:** separate object presence from the requested action, ground vehicle targets with existing detector tracks and consistent crops, retain full-frame context, and require genuinely different target states before accepting a transition. A larger model alone did not remove these failure modes. Keep generated interpretations out of persistent entity facts until that workflow passes an independently reviewed action set.
