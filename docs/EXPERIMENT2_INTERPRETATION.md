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
5. Validate event IDs, response shape, source bounds, ordered sample times, visual frame data, and citations. A model `supported` claim needs two distinct frame citations. These structural checks cannot establish semantic truth or detect every contradictory explanation.
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
