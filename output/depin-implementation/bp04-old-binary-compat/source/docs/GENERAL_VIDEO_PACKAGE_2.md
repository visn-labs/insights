# Milestone A, package 2: query snapshots and entity lookup indexes

Date: 18 October 2026. Status: implemented and validated as exact-preserving engineering changes. This package advances the [general video implementation plan](GENERAL_VIDEO_IMPLEMENTATION_PLAN.md); generic model/accuracy acceptance and the remaining P0/P1 work are still pending.

## Changes

`src/store.rs` now creates a request-scoped snapshot while borrowing stored recordings. Recording, cluster, camera and time filters run before event copies. Ordinary retrieval copies no entity histories; linked-memory answers and selected-entity interpretation copy only eligible camera records. Full tracker observations for selected records remain available, including neighboring observations needed by expanded interpretation windows. The store lock is released before network/model work.

The original pre-filter count, including the existing selected-entity scope behavior, is carried into `src/retrieval.rs`. This preserves empty-index versus excluded-filter diagnostics even when the snapshot contains no candidates. Retrieval reapplies filters at its boundary. Snapshot duration and copied candidate/camera counts are logged with the query ID.

`src/long_memory.rs` groups rule events and detector observations by track once, uses a first-observation lookup by timestamp, and builds an interval-boundary index. The winning evidence interval remains the earliest start, then smallest event UUID, with original input order breaking complete ties. Shared boundaries stay half-open; gaps and invalid intervals remain unmatched. Repeated observations retain the original first-match box/score behavior. Graph versions, IDs, descriptions, ordering and uncertainty are preserved.

The prior entity builder is frozen in `tools/fixtures/long_memory_build_reference.rs` and compiled only into tests. No model, tensor precision, sampling, frame transformation or action-verification policy changes in this package.

## Equivalence and application validation

- 30 Rust unit tests pass. Two additional benchmark tests were run explicitly rather than in the default suite.
- New tests compare old/new candidate contents, complete retrieval results and linked-history answers across recording/camera/cluster/time/entity scopes, stale/missing graphs and empty filters.
- Indexed interval selection matches the original scan across 1,100 timestamps and 150 overlapping/duplicate/zero-length intervals, including gaps and shared boundaries.
- Existing entity fixtures compare the complete serialized graph with the frozen builder. Additional checks exercise duplicate observations and overlapping evidence. A larger paired benchmark compares full graphs over seven runs.
- Two Python comparator tests verify that scores, ranking, observation ordering, link fields and duplicate edges cannot be hidden by normalization.
- `tools/check_query_snapshot_workflow.py` compared the frozen and updated Rust applications through 29 real HTTP queries on retained development recordings. Scope diagnostics, answers, matches, entity histories and citations agree. All 13 referenced evidence videos match SHA256 and return video content successfully.

Separate application processes enumerate HashMap-backed camera graphs differently. The first HTTP comparison exposed this pre-existing graph-link array ordering difference. The final comparison normalizes **only** query UUID and unordered graph-link enumeration; every link field and multiplicity still match, while retrieval and observation order remain exact. In-process equivalence tests use the same source order and compare complete graph/result ordering directly. Raw initial and final responses are retained; no production ordering behavior was changed to make the test pass.

HTTP validation used lexical retrieval, linked histories and interpretation-unavailable behavior without new detector/VLM inference. It verifies API/evidence integration, not new action accuracy or successful visual interpretation. Frontend assets did not change and no new browser acceptance is claimed.

## Controlled component measurements

Measurements use the same debug test binary, with five isolated processes per algorithm, interleaving reference and candidate. Snapshot runs contain ten timed repetitions per process; entity runs contain seven. Resource measurements come from macOS `/usr/bin/time -l`. Initial sandboxed resource collection could not access `kern.clockrate`; final resource runs completed with the required host-statistics access.

| Workload | Reference median | Updated median | Resource result |
| --- | --- | --- | --- |
| One-recording query among 512 records / 1,536 events, each with a 4,096-component visual signature | 4.087 ms | 0.0722 ms | Median whole-process peak RSS: 94,388,224 → 36,700,160 bytes (~61% lower) |
| Entity build: 512 intervals, 64 tracks, 4,096 retained observations | 341.505 ms | 19.688 ms | Median whole-process peak RSS: 12,763,136 → 13,041,664 bytes (+0.266 MiB / ~2.2%) |

The snapshot workload copies three candidates and zero camera histories instead of 1,536 candidates and 512 complete camera records. This is a scoped ordinary-retrieval workload; an all-recordings linked-memory query still needs the eligible histories. Entity lookups add bounded temporary index storage in exchange for avoiding repeated scans. Peak RSS includes fixture setup, runtime and output allocations, not solely the optimized function.

The measured component speed ratios are ~56.6× for snapshots and ~17.3× for entity construction. These are synthetic stress/component results in a debug build, not production query throughput, model speedups or generic-camera accuracy claims. Small recordings may see less benefit and interval-index setup has a cost. Production/load measurements remain part of the plan.

## Artifacts and reproduction

Artifacts under `output/general-video-2026-10-18`:

- `package-2-baseline/`: frozen pre-change Rust sources.
- `package2-manifest.json`: source/test/binary hashes and unchanged retained-source fingerprints.
- `package2-resource-results.json`: all timing samples, process RSS, host and test-binary fingerprint.
- `package2-entity-benchmark.log`: full paired graph equivalence and timing.
- `package2-*-reference-*.log`, `package2-*-filtered-*.log`, `package2-*-indexed-*.log`: isolated resource runs.
- `package2-http/`: initial cross-process ordering diagnosis; `package2-http-final/`: passing 29-case comparison and raw responses.

```bash
cargo test --offline
cargo fmt --check
.venv-retrieval/bin/python tools/check_query_snapshot_comparison.py

cargo test --offline benchmark_indexed_entity_build -- --ignored --nocapture
cargo test --offline benchmark_filtered_snapshot -- --ignored --nocapture
```

For isolated resource runs, execute the compiled test binary with `VISN_SNAPSHOT_BENCH_MODE=reference|filtered` or `VISN_ENTITY_BENCH_MODE=reference|indexed`, the corresponding exact test name, `--ignored --nocapture --test-threads=1`, and `/usr/bin/time -l`. The raw logs record the tested executable/configuration.

The HTTP comparison requires independently started baseline/current applications using the same retained data:

```bash
.venv-retrieval/bin/python tools/check_query_snapshot_workflow.py \
  --baseline http://127.0.0.1:8210 --candidate http://127.0.0.1:8211 \
  --output output/general-video-next-package2-http
```

Output directories must be new. The temporary validation applications were stopped after testing. No source footage or saved entity graph was rebuilt or overwritten.

## Decision and next work

Keep both engineering changes: equivalent outputs pass the targeted contract/integration tests, the scoped snapshot workload reduces allocation/time materially, and entity indexing reduces build time with a small measured temporary-memory increase. Existing code/binary snapshots and the frozen reference permit reproduction and rollback. Rollback should reverse only this package's Rust changes or use the saved baseline binary; do not discard unrelated pending work or delete derived/source artifacts.

Next in plan order: finish stage/resource instrumentation and evaluation coverage; then durable background embedding tasks, admission/resource scheduling and progress reporting. Recording-list summaries and sustained service measurements are still outstanding. General-video validation/acceptance coverage remains unchanged from the first package.

