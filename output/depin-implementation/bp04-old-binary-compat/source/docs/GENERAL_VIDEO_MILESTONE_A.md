# General video: first Milestone A work package

Date: 18 October 2026. Status: P0 experiment foundations and the P1 canonical-vector candidate implemented and development-tested. Full P0 acceptance, the remaining P1 work and general-video accuracy promotion are pending.

## Delivered

- Frozen pre-change pipeline code and a consistent read-only SQLite backup under `output/general-video-2026-10-18/baseline-prechange`.
- Source/label/request fingerprints, corpus split checks and code/host inventory via `tools/general_video_experiment.py`.
- Versioned profiles under `evals/general-video/profiles`, a source-scoped corpus registry and two development replay requests. Labels remain scoring-only; only allowlisted inference fields reach the worker.
- A replay runner that saves raw HTTP responses, model/configuration proof, actual work-cache counters, end-to-end request times, raw-result hashes and explicit decisions. It rejects output-directory reuse, source/request/label mutation, corpus/profile changes during replay, model mismatch and cross-split source/session/site leakage.
- Exact comparisons retain scores and ranking while excluding only named timing/cache/policy diagnostic fields. Coverage notes retain their coverage meaning while their cached-embedding counts are normalized for comparison. A tiny score difference or a changed raw result cannot pass exact comparison.
- `--vector-policy canonical-unit-v2` validates already-normalized vectors without normalizing them again. It uses `semantic-canonical-v2.sqlite3`; the default `legacy-renormalize-v1` retains the original `semantic-v1.sqlite3` behavior and database.

The numerical contract is the model's existing normalized Python values, persisted as losslessly round-trippable JSON numbers. No embedding dimensions, tensor dtype, image size or frame count are reduced. Invalid/non-unit values fail or regenerate instead of being silently repaired by rounding or rescaling. Dimension agreement is still checked during search.

## Reproduction and real-model validation

The frozen legacy database contains 25 vectors. Nine change if normalized again, with maximum component drift `1.3877787807814457e-17`. This is a numerical consistency defect, not demonstrated action/retrieval accuracy loss. All original key/version/vector records remain unchanged after the comparison.

Regression tests reproduce the drift and verify exact candidate values and near-tied ranking/scores on fresh, cached and restarted paths. They also check corrupt-cache regeneration, invalid model vectors, split leakage, altered inputs/results, profile mismatch and output isolation.

Validation passed 79 Python unit checks across 17 unit-test modules, 26 Rust tests, formatting, document links and diff whitespace checks. Unit modules were loaded explicitly: broad `check_*.py` discovery also imports standalone integration scripts that parse command-line arguments or bind servers at import time, so it is not a valid all-tests command in this repository. No browser acceptance or complete application replay is claimed for this worker-only change.

The actual pinned Qwen3-VL-Embedding-2B and Qwen3-VL-Reranker-2B workers ran sequentially on MPS, with four source-aligned traffic intervals. The positive query and negative indoor-door query each ran twice, followed by a candidate-worker restart and both queries again:

| Comparison | Result |
| --- | --- |
| Baseline vs canonical candidate | Exact ranks, scores, retrieval mode, non-cache notes and model/settings proof on all four paired requests |
| Candidate vs restarted candidate | Same exact comparison on all four paired requests |
| Candidate vector persistence | Four 2048-component vectors; reused after restart |
| Legacy database preservation | All 25 stored key/version/vector records unchanged |
| Positive development relevance | Same retrieval metrics across profiles |
| Negative indoor-door question | Both profiles still return four candidates; negative-query rejection remains unresolved |

These four real candidate vectors do not themselves drift on another normalization; the affected-vector reproduction uses the frozen legacy database and targeted regression fixtures. Exact results here therefore establish integration/parity, not the prevalence or practical ranking impact of the defect.

Artifacts:

- `output/general-video-2026-10-18/frozen-inputs-final`: source/label/request/code fingerprint inventory.
- `baseline-http-final`, `canonical-http-final`, `canonical-http-restart`: profiles, run manifests, raw outputs, counters and paired development metrics.
- `baseline-vs-canonical.json`, `canonical-restart-comparison.json`: exact comparisons.
- `precision-and-preservation.json`: numerical audit and legacy preservation check.
- Earlier `frozen-inputs` and `baseline-http` directories are exploratory snapshots; use the final directories above for the paired results.

Timing values are retained but are **not an efficiency acceptance comparison**: the final baseline worker was warm while the candidate had a fresh index/model load. Repetitions are integration smoke checks, below the plan's performance repetition targets. Peak backend/accelerator memory and full stage timings are not yet instrumented. No speedup or generic accuracy improvement is claimed for this candidate.

## Coverage inventory and decision

The registry currently covers three development camera sources and 13 event clips. All are traffic development material; the four night clips also populate difficult-capture coverage. Cameras on the same original host are conservatively grouped at site level. Counts overlap between families.

The inventory references the nine-question assistant-reviewed retrieval pilot and two human annotation files (five initial entries plus two follow-up entries). These are existing development labels, not new independent acceptance data. Human-state scopes remain in the adjacent original `scopes.json` files and are not scored by this retrieval-only runner. Use `evaluate_reviewed_states.py` with those scopes for state evaluation.

There are zero validation/acceptance sources and no represented coverage for quiet/rare events, object interactions, state changes, people/animals as a task family, text/detail or long-history acceptance. A negative indoor-door question on traffic footage is not positive indoor action coverage.

**Decision: keep the canonical policy experimental; retain the legacy default.** The candidate resolves the numerical inconsistency, but full quality/resource gates and independent general-video acceptance are not satisfied. The runner intentionally cannot promote a profile automatically from a successful development replay.

## Running the delivered tools

From the repository root:

```bash
.venv-retrieval/bin/python tools/general_video_experiment.py inventory \
  --corpus evals/general-video/corpus.json

# Always choose a new output directory; existing artifacts are never overwritten.
.venv-retrieval/bin/python tools/general_video_experiment.py freeze \
  --corpus evals/general-video/corpus.json \
  --output output/general-video-next-freeze
```

Start one worker at a time for an isolated comparison. The profile validates the following pinned configuration:

```bash
HF_HUB_OFFLINE=1 HF_HOME="$PWD/.retrieval/huggingface" \
.venv-retrieval/bin/python tools/semantic_memory_server.py \
  --port 8198 --device mps \
  --data-dir "$PWD/data/live-evaluation-2026-10-16" \
  --keep-models-loaded --vector-policy canonical-unit-v2 \
  --embedding-revision 9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda \
  --reranker-revision 4bd860ac4f15ad1897a214615cccc700f8f71818
```

Then run:

```bash
.venv-retrieval/bin/python tools/general_video_experiment.py run \
  --corpus evals/general-video/corpus.json \
  --profile evals/general-video/profiles/canonical.json \
  --split development --worker http://127.0.0.1:8198 --repeats 2 \
  --output output/general-video-next-canonical
```

For the baseline use `--vector-policy legacy-renormalize-v1` and `profiles/baseline.json`. Stop the first worker before loading the other to avoid competing allocations. Comparisons require the same corpus fingerprint, split and repeat count:

```bash
.venv-retrieval/bin/python tools/general_video_experiment.py compare \
  --baseline output/general-video-next-baseline \
  --candidate output/general-video-next-canonical \
  --output output/general-video-next-comparison.json
```

Rollback consists of selecting the legacy worker policy and original database; candidate artifacts remain available for diagnosis. Temporary comparison workers from this work package were stopped. The application's default policy was not changed.

## Remaining work in plan order

Update: [package 2](GENERAL_VIDEO_PACKAGE_2.md) completed filtered-query snapshots and entity lookups. [Package 3](GENERAL_VIDEO_PACKAGE_3.md) added opt-in worker stage/resource diagnostics and correlated Rust logs, with model/browser/ingestion parity checks. The earlier measurement limitations above describe the first package's original runs.

1. Continue general corpus acquisition/review and locked acceptance splits. Expand measurement to sustained/concurrent workloads and untested runtimes; inventory and point-in-time diagnostics alone do not satisfy P0.
2. Proceed to durable background indexing, admission and progress handling. Use package 3's measurements to diagnose cleanup and model/decode overhead before changing those behaviors.
3. Then evaluate grouped decoding and the research candidates as specified in the [implementation plan](GENERAL_VIDEO_IMPLEMENTATION_PLAN.md).

