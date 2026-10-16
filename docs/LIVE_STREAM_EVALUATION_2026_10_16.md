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

## Target-grounded interpretation stage

Implemented the next stage with prompt `separate-presence-transition-v8`:

- Isolated physical-target parsing from single-image presence checks. An initial extraction prompt incorrectly rejected indefinite targets such as “a helicopter”; the real negative-case run exposed this and the final prompt extracts them. Earlier v7 output is retained separately.
- Added explicit-entity grounding through the Rust query API and UI. Linked-history retrieval and visual explanation can run together. Entity evidence is scoped by recording, camera, graph version, interval, and source hash. No generated action is written into persistent entity histories.
- Added actual decoded timestamps to the existing FFmpeg sampler. Tracker-mode sampling selects 2–8 detector observation times, matches boxes within 1 ms, supplies crops plus full context, and never interpolates boxes. Frame/crop previews and actual PTS are visible in the UI.
- Added guards against repeated decoded times, missing crop alignment, and unchanged target crops/boxes despite background changes. Existing repeated-image safeguards remain active on fresh and cached answers. Before/after/reference fields are mandatory for supported comparisons, but their semantic truth is not automatically established.

### Real inference findings

The same cached 4B MLX model and six frozen development cases were used; labels and source files were not changed. The harness accepts an explicit prompt-version override and records it without modifying labels. Final-source cached replay is under `output/target-grounding-v8/frozen-4b-final`, initial fresh v8 inference under `frozen-4b`.

- Two toll-truck answers have correct toward-camera direction labels. The tarp-truck explanation still treats disappearing/reappearing as motion evidence; it is not a fully grounded success.
- Yellow-truck direction is rejected because the cited images repeat. The night static control abstains.
- Train is reported absent. Helicopter remains uncertain after inconsistent single-frame visibility judgments. This regresses from the prior candidate's explicit absence on both negatives; no accuracy improvement is claimed.

Explicit tracker tests on all three original live-derived recordings (`output/target-grounding-v8/tracks-4b`) produced:

| Recording | Selected detector class | Exact-PTS frames/crops | Model status |
| --- | --- | --- | --- |
| live-1 toll road | car | 3/3 | supported |
| live-2 night intersection | car | 2/2 | supported |
| live-3 highway | truck | 3/3 | uncertain |

These are successful alignment/workflow checks, not three verified action outcomes. Toll-road audits call the car stationary while synthesis says approaching. Night-car interpretation relies on weak blurred observations. The highway track's audit changes from truck to silver van, illustrating possible class/identity switches despite one tracker ID. Crops constrain evidence but cannot repair a tracker ID switch by themselves. No ground-truth identity or vehicle count claim is made.

Six schematic action reversals were rerun (`direction-probes.json`). Opening now yields an opening label rather than false target absence, but its cited final observation still says closed. Closing gives the expected label with a more coherent state comparison. Placing/removing both return supported narratives saying the object remains on the table; arriving contradicts its own visible citation, and leaving abstains. **The action-grounding acceptance gate still fails.** Structural state fields and target cropping have not solved semantic consistency.

### Workflow and regression validation

- 26 Rust tests and 33 Python checks pass: interpretation 10, API adapter 4, corpus harness 5, retrieval 3, exact-PTS grounding 4, detector sampling 3, evaluation metrics 3, memory runner 1.
- A real Playwright UI submission selected the toll-road entity, enabled both experiments, ran semantic retrieval/reranking, and returned three interpretations with eleven visible crops and a single selected linked history. No mocked inference responses. No browser errors or horizontal overflow at 320/390/1440px. Screenshots and actual response: `output/playwright/track-grounding-v8`.
- The initial browser attempt ran while the evaluation worker was busy and correctly retained retrieval results with an explicit 503 interpretation note. The isolated rerun passed; this does not establish concurrent inference throughput.
- Rust formatting/build/tests, JavaScript syntax, Python contracts, shell syntax, and diff whitespace checks pass.

### Next stage

Keep the default model and opt-in experimental interpretation. Before claiming action accuracy, add independently reviewed per-target state/identity annotations and an independent state-consistency check that can reject contradictory audits/synthesis. Evaluate detector identity switches and crop resolution, then implement automatic target-to-track selection with explicit ambiguity handling. A state string that merely differs in wording or frame number is insufficient evidence of a transition. Use fresh independently reviewed clips for acceptance; these repeatedly used development cases cannot serve as untouched holdout data.

## Blind state consistency and human review phase

Implemented `blind-state-consistency-v9` with `blind-visual-states-v1`. A separate model call sees the physical target/reference and cited pixels, without the proposed answer, proposed action direction or prior audit text. Enum state transitions replace string-difference checks. Same-target identity and a visible reference are required; tracker camera-distance changes must also agree with detector box scale. Tiny crops (<16-pixel short edge) and <15% directional scale change abstain. These are conservative, uncalibrated safeguards. The same configured model performs both calls, so correlated visual errors remain possible.

The gate executes after generation and cache read. Accepted answer text comes from the checked transition rather than unchecked synthesis prose. Rust validates v9 proof status, identity, reference, claimed transition and citation agreement; the browser displays check status/reasons. Generated answers remain outside entity facts. Defaults remain 2B and opt-in temporal interpretation; actual stage evaluation used the existing pinned 4B MLX runtime.

### Development evaluation

- Six frozen development cases: two toll-truck toward-camera labels pass a blind middle→near comparison; night static and repeated yellow-truck evidence abstain; train is sampled-absent; helicopter is uncertain. This is not independently reviewed action accuracy.
- Three original explicit-track cases all abstain. The highway truck comparison rejects an apparent identity change. Night-car and toll-car claims fail detector box scale agreement (ratios approximately 0.9684 and 1.0685 despite model far→near judgments). These illustrate useful rejection, not proof that every abstention is correct.
- Six synthetic action reversals all abstain. Door comparisons report uncertain/different identity; placement yields unchanged on-reference states; removal contradicts identity/transition; arrival is assigned an unsupported comparison family; departure has insufficient visibility. This reduces accepted unsupported narratives but **does not solve action recognition**. Coverage remains poor and the acceptance gate is not passed.
- Raw outputs: `output/target-grounding-v9/frozen-4b`, `tracks-4b`, `direction-probes.json`. Frozen corpus files/labels remain unchanged; expected labels are never sent to inference.

### Fresh human-review evidence

The user elected to review the packet. Three new bounded 30-second recording requests completed in an isolated data root, with VLM/temporal interpretation disabled. Retained durations are 26.24/28.16/29.96 seconds for live-1/live-2/live-3. Only detector/tracker processing and local frame/video decoding were used. Fresh sources were **not** submitted to action inference.

Packet: `output/target-grounding-v9/human-review/review.html`, with `packet.json` and evaluation input envelopes. It contains five cases and 23 source-ordered frames: two vehicle tracks per daytime/sunset recording and one untracked full-scene night case. No action answers, state labels or suggested identity decisions are included. UI fields begin blank; `unclear` is a valid explicit annotation. Packet/source/context/frame/crop hashes bind the review to its evidence.

[Human review instructions](STATE_REVIEW.md). The human's saved annotations are the outstanding dependency for acceptance evaluation. The validator checks completeness and evidence hashes; it cannot prove reviewer independence. This same-camera packet is not an independent-camera benchmark, and it does not cover real-world doors or placement actions.

### Validation

- 26 Rust tests and 42 Python checks pass, including blind prompt isolation, semantic enum transitions, contradictory/unchanged states, identity/reference failures, crop resolution/geometry, cache revalidation, annotation completeness, evidence tampering and existing retrieval/decoder contracts.
- The actual review packet passes browser checks: five cases/23 frames, all state fields initially blank, incomplete download blocked, images loaded, context video plays, no errors or overflow at 320/390/1440px. No annotations were saved. Browser evidence: `output/playwright/state-review-v9`.
- A real application browser submission passed semantic retrieval/reranking, Rust v9 proof validation and state-check display, returning three selected-entity interpretations with eleven crops. No browser errors or horizontal overflow at 320/390/1440px. Actual response and screenshots: `output/playwright/track-grounding-v9`.
- Formatting, JavaScript syntax, shell syntax and diff whitespace checks pass.

Keep this phase experimental. Freeze the user's completed review before acceptance inference. Use its state and identity labels only in scoring, never in prompts. Diagnose rejection/coverage and identity/crop failures before attempting automatic target-to-track selection; no production or accuracy promotion is justified yet.

## Human-reviewed identity evaluation

The user's completed review passed annotation completeness and all packet/source/video/frame/crop hash checks. The original JSON is preserved byte-for-byte at `evals/human-state-review-2026-10-16/annotations.json`. The user's final clarification says the three scene-named entries describe the whole scene; `scopes.json` records that correction separately. The earlier, superseded selected-crop clarification is not used.

Only the two target-scoped highway histories were submitted for acceptance inference. The three scene-scoped cases were excluded before any request. Requests use the pre-review `evaluation-inputs.json` envelopes unchanged: no human object descriptions, identity labels, distance states, reviewer name or notes were sent. The run freezes review/scope/input snapshots and hashes before the first request, records model/revision/prompt/code hashes, checks returned source windows and decoded timestamps against the reviewed frames, and checks evidence integrity again after inference.

| Target history | Human history identity | Blind model identity | Final result |
| --- | --- | --- | --- |
| live-3-9788af39 | different | different | uncertain |
| live-3-27804ec1 | different | different | uncertain |

Both are fresh generations (`cached: false`) using the existing pinned 4B MLX configuration and v9 prompt. Both satisfy the safety policy of abstaining on unresolved/mixed selected-target histories: **2/2 identity-policy checks pass, 0 accepted action claims**. These are global history annotations, not pairwise identity ground truth. This result does not establish action-direction accuracy, recall, general identity accuracy, or production readiness. There are no valid same-identity positive action cases in this reviewed subset.

Raw responses, frozen inputs/review, manifest and scores: `output/target-grounding-v9/human-reviewed-acceptance`. Reproduction harness: `tools/evaluate_reviewed_states.py`; three contract checks cover scene exclusion, human-label isolation, pre-inference freezing, forged timestamps, wrong models, missing gate proof and identity-policy/action-score separation. The corpus is now used evaluation evidence; future prompt tuning against it must be described as development work.

### Review ambiguity correction

New review packets use `human-state-review-v2`. Every case requires explicit `target` or `scene` annotation scope, labels refer to the selected target, and an orange box identifies it in context when a detector box exists. The validator requires v2 scope and the evaluation harness rejects conflicts between explicit annotations and supplemental scope files. Existing v1 reviews remain valid with separately recorded scope clarification.

A separate follow-up packet contains the two car-crop cases previously reviewed at scene level: `output/target-grounding-v9/target-review-followup/review.html` (two cases/eight frames). The originals remain unchanged. These two excluded sources have not been sent to action inference; correct target-state review is still pending. The untracked night scene remains outside vehicle-action acceptance scoring.

The follow-up and original packets pass browser checks for blank initial fields, incomplete submission blocking, image loading, video playback, mobile widths and no browser errors. The v2 check also verifies target markers and scope controls. Integrity tests include the explicit-scope requirement. No annotations were generated or saved by browser tests.

Next: freeze the user's target-specific follow-up before inference, then measure supported-action coverage and cited state agreement. Keep automatic target-to-track selection deferred until identity/state evidence supports that step. No default-model change or production promotion was made in this evaluation phase.

## Follow-up review and selected-track visibility fix

The second user-completed annotation file validates against the v2 follow-up packet. It is frozen unchanged in `evals/human-state-review-2026-10-16/followup/annotations.json` with explicit target scopes. SHA256: `00c55e4b95b3316ec215f925deb10f8898d975f99caf37dad7c4f46744970acc`. The first completed review remains preserved separately.

Both car histories are labeled same identity, but the user marked all four frames unclear in `live-1-dce234f8` and only the final frame visible in `live-1-387fecde`. All distance labels are far. These are useful low-visibility controls, not positive directional-action labels. The unchanged coarse distance category does not prove the vehicle is stationary.

### Initial frozen v9 evaluation

Both input envelopes were submitted unchanged, without human descriptions, visibility, distance or identity labels. Raw responses, frozen review and manifest are under `output/target-grounding-v9/human-reviewed-followup`.

| Case | v9 response | Low-visibility abstention policy |
| --- | --- | --- |
| live-1-dce234f8 | not_observed | fail: uncertainty was misreported as absence |
| live-1-387fecde | uncertain | pass |

Thus the first evaluation passes **1/2** low-visibility checks, with no accepted action claims. The error exposed a gap: minimum crop inspection quality applied to supported-action verification, while single-image presence audits could still report absence for an unrecognizable selected tracker. The actual crops are 27×21–34×27 pixels in the first case and 13×12–23×24 in the second; even a crop above the minimum size may remain blurred.

### Implemented correction

Prompt/policy version `track-visibility-quality-v10` now checks selected crops before presence inference. Missing crops and crops below the existing 16-pixel short-edge threshold produce unclear observations without a visual model call. For larger selected crops, a model `absent` response is treated as an unresolved target match, not scene-wide absence. An explicit selected tracker can no longer produce a `not_observed` answer from that ambiguity. Unconstrained sampled-absence behavior remains available.

A shared post-cache safeguard also downgrades selected-track absence claims and their observations to uncertainty. A regression reuses a cached raw absence result with no regeneration and confirms the unsafe status cannot bypass the guard. Rust independently rejects v10 selected-entity absence responses, and enforces the existing supported-state proof for both v9 and v10. This correction does not increase source image detail or establish physical identity.

### Post-fix development validation

The reviewed cases motivated this fix; reruns are **development validation, not untouched held-out acceptance**.

- Both low-visibility cases now return uncertain: **2/2** abstention checks pass (`output/target-grounding-v10/reviewed-visibility-development`).
- Both earlier mixed-identity histories still return uncertain: **2/2** identity-policy checks pass; the three scene-scoped cases remain excluded (`output/target-grounding-v10/reviewed-identity-development`).
- There are zero accepted action claims across these four target histories. No same-identity, clearly visible positive action set exists yet, so action precision/recall and general readiness are not established.
- All original reviews, source recordings, packet hashes and prior raw outputs are preserved. Human labels remain scoring-only.
- 26 Rust tests and 51 Python checks pass, including four new visibility/cached-absence regressions and the low-visibility scoring contract. Formatting, JavaScript syntax and diff checks pass.
- Real browser submission passes semantic retrieval/reranking and Rust v10 validation, returning three interpretations and eleven reviewable crops. No browser errors or horizontal overflow at 320/390/1440px. Actual API response and screenshots: `output/playwright/track-grounding-v10`.

Next accuracy work should improve source-resolution target evidence and choose sufficiently visible vehicle sequences before advancing to automatic target selection. Do not repeatedly request labels for unreadable evidence or treat safe abstention as successful action recognition. The current interpretation remains experimental; default model and persistent entity facts are unchanged.

## Source-detail crop implementation and readable-sequence comparison

Implemented `source-detail-crops-v1` while retaining the v10 presence/state safeguards. The source crop path decodes source frames separately, never upscales them, and verifies their PTS against the unchanged full-scene previews. Detail decode long edges are bounded at 1920; target model-input long edges are bounded at 768 by downsampling only. Source crops are now the default for selected-track interpretation; the default interpretation model remains 2B and temporal interpretation remains opt-in. A `preview` worker mode supports controlled/historical comparisons.

Crop policy/settings partition generation cache entries. Provenance records actual context/detail/crop dimensions, and the application displays each source crop's pixel dimensions. Ordinary retrieval context images/rankings are unchanged. Source crops cannot silently be scored against human labels created for different-sized preview crops: reviewed-evidence validation now rejects changed crop dimensions. Both completed human reviews and their original results remain unchanged.

### Readable development examples

A deterministic selector uses source crop size (≥48-pixel short edge), at least two observations, ≥1-second span, ≥1.25× detector area variation and a ≤20-second window. It selects geometry suitable for inspection, not verified actions or identities. It found four candidates in the retained daytime recordings; the night camera had zero qualifying vehicle histories and was excluded without substituting poor evidence.

| Recording / source frame size | Selected histories | Minimum source crop short edges | Crop pixel area vs preview |
| --- | --- | --- | --- |
| live-3 / 640×480 | f201c072, 95e75c97 | ~144px, ~101px | 2.73–2.79× |
| live-1 / 480×360 | 3349b3f6, fa284fe6 | ~111px, ~92px | 1.56–1.57× |
| live-2 / 1280×720 | none | insufficient qualifying geometry | not evaluated |

These are existing development recordings, not new independent acceptance footage. Saved full context, lossless preview/source crops, selected boxes, source hashes and frozen query envelopes are under `output/target-grounding-v10/source-crop-comparison/candidates`.

### Controlled real-model results

Preview and source workers use the same cached pinned 4B MLX runtime, v10 prompt, state gate, question, event window and tracker observations. Requests run sequentially. The harness verifies identical actual context image bytes and sampled/decoded timestamps between modes, expected model/revision/mode, source integrity, and state-proof structure. It records inputs/code/model hashes. No expected action labels or human annotations are sent.

Both modes return uncertain for all four candidates. For the two highway sequences, the blind model judges identity different as framing changes; these judgments are not independently verified identity truth. For the two toll-road sequences, proposed approaching claims conflict with cited detector area ratios (~0.817 and ~0.811), and the geometry guard rejects them. The larger crops supply more recorded detail but **do not improve action recognition on these cases**. No direction accuracy or acceptance promotion is claimed, and there is no throughput comparison: cache/cold state and an extra decode differ.

Fresh bounded-implementation responses are under `source-crop-comparison/inference-final`. Final-source cached-generation revalidation and full code hashes are under `inference-verified`; blind state checks still run on cached generations. Earlier exploratory outputs remain separately preserved.

### Review generation and validation

Future review generation uses the same readability selection/source crop extraction. It skips cameras with no eligible target. Reused/inferred clips can be explicitly marked `--development`, and the page visibly states this status. A development-only export of four cases and fourteen frames is under `source-crop-comparison/development-review-final`; it contains blank annotation fields and no displayed model predictions. No further human review is requested for this implementation experiment.

59 Python checks and 26 Rust tests pass. New checks verify actual lossless detail preservation (320×240 source → 160×120 crop versus 32×24 preview crop), no source upscaling, decode caps, context/detail PTS mismatch failure, the 768-pixel model crop budget, crop-mode cache isolation, geometry selection exclusions, comparison parity controls and rejection of changed human-review crop resolution. JavaScript syntax, formatting and diff checks pass.

The remaining bottleneck is interpretation/identity reasoning, not merely preview resolution. Keep the guards and experimental status; diagnose sequence-level identity and physical-reference reasoning before automatic target-to-track selection or any production accuracy claim.

Browser validation of source crops returned three interpretations and eleven labeled crops, with no browser errors or horizontal overflow at 320/390/1440px (`output/playwright/source-crops`). The development review page also passed blank-field, incomplete-submission, video and responsive checks (`output/playwright/source-crop-review`); no annotations were saved.

## Lossless repeated-work optimization

After completing the source-detail experiment, added bounded process-local LRU caches for compressed lossless decoded frames, exact query vectors and reranker scores. Decoded frames are returned as fresh images so downstream mutation cannot alter cached evidence. Keys include source hash, interval, decode settings, embedding configuration and pinned reranker snapshot as applicable. Source/scope checks still execute on hits. Invalid scores are not cached. Restart adopts a new model snapshot; existing persistent embedding/generation cache contracts remain intact.

Decode payloads are capped at 32 MiB and 128 entries; query vectors at 32 entries; scalar rerank scores at 256. No new GPU tensor cache is introduced. The external API interpreter avoids importing Torch or forcing whole-process collection when it owns no local weights. Cleanup still runs when retrieval weights were loaded. `--disable-work-caches` disables the new RAM caches for controlled comparisons.

### Actual pinned-model replay

`output/target-grounding-v10/workflow-optimization-final` records frozen inputs, source/code/model hashes, raw responses and exact output comparison. The selected-car question retrieves four candidates and interprets three clips from the retained live-1 recording. Existing generation caching is held constant.

| Phase | Seconds | FFmpeg calls | Query encoder calls | Reranker calls | Visual-state checks |
| --- | --- | --- | --- | --- | --- |
| Baseline repeat, new caches disabled | 11.562 | 54 | 1 | 4 | 3 |
| Optimized cold | 10.811 | 54 | 1 | 4 | 3 |
| Optimized repeat | 2.216 | 0 | 0 | 0 | 3 |

The repeated workflow was approximately 5.2× faster (81% less elapsed time). Compressed frame payload was 9,516,764 bytes (9.08 MiB), with one query vector and four reranker scores. Rankings, exact floating-point scores, evidence image bytes, timestamps, answers and safeguard outcomes matched across all phases. Semantic output SHA256: `553e03e3367f1a3be28fb5d3776b38dcb91dd6e7f7c69f68d59d31f7ddb90995`.

This is a same-process development replay, not a general throughput or peak-memory benchmark. Frame counts, image resolution, model precision and verification policy are unchanged. Repeated cache hits do not establish action accuracy; the source-detail interpretation limitations above remain. Final regression validation passed 67 Python checks and 26 Rust tests, plus formatting, JavaScript/shell syntax and whitespace checks.

The updated worker also passed real browser submission and repeated-query validation (`output/playwright/workflow-optimized`): identical matches and interpretation statuses/answers/frame evidence; one query-vector hit and four rerank-score hits with zero query/rerank calls on repeat. Three interpretations and eleven source crops rendered without browser errors or horizontal overflow at 320/390/1440px. Screenshots were inspected. The main application and optimized worker remain running on ports 8192/8193; the temporary preview comparison worker was stopped.

### Follow-up: bounded source fingerprint registry

The source-hash registry previously retained an entry for every recording and rebuilt/scanned the dictionary whenever a new file version was hashed. It now uses a 256-path LRU with direct lookup/replacement and bounded retention. Eviction recomputes SHA256 without changing evidence. Fingerprints also validate device/inode and nanosecond change time, in addition to size/modification time, so same-sized rewrites with restored modification timestamps invalidate old results. Files changing during fingerprinting fail without a cache entry. This does not replace source/scope validation or relax evidence checks.

27 targeted checks pass, including exact hash reuse, LRU eviction/recomputation, restored-timestamp mutation, concurrent hashing mutation, decoded pixel/PTS parity, invalid-score rejection with caching enabled, source detail, grounding and API interpretation. The actual pinned-model replay in `output/target-grounding-v10/workflow-optimization-source-lru` passes exact output parity across all four phases and matches the previous semantic output SHA256. Baseline repeat: 10.892s; optimized cold: 10.477s; optimized repeat: 2.134s. All three visual-state checks remain; warm execution uses zero FFmpeg/query-encoder/reranker calls and the same 9.08 MiB compressed-frame payload. These timings measure the combined cache workflow, not an isolated speedup from the small registry change. The worker was restarted and its health endpoint reports ready. Model precision, sampling, resolution and accuracy limitations remain unchanged.
