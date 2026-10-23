# BP04: independently pinned tracker enrichment

## Implemented candidate — 23 October 2026

Tracker knowledge now has its own `tracker-enrichment-v1` execution recipe in `EntityMemory.execution_profile`. Building or rebuilding it does not replace `MemoryJobRecord.execution_profile`, which describes the original recording execution.

The derived recipe binds compiled code, Python tools/configured detector scripts, actual tracker weight bytes, detector runtime versions/settings and a source-evidence identity. Evidence identity covers the saved sanitizer descriptor, media digest, camera/session/storage scope, capture extent, duration and exact indexed event IDs/intervals. Recipe version, required components, detector identity and evidence binding are validated before publishing graphs or accepting them into metadata/checkpoints.

## Execution and publication

1. Verify saved sanitized source/ledger and canonical producer metadata before opening it.
2. Snapshot the actual tracker recipe without requiring the old observer executable or privacy-model files. This stage consumes already sanitized evidence; it does not claim to sanitize it again.
3. Run the existing actual detector/tracker at its existing cadence and thresholds.
4. Reverify retained evidence and recipe after inference. Reject drift before assigning the replacement graph.
5. For multi-session rebuilds, require one selected detector identity across all sessions and recheck it before publication. Validate the complete evidence-linked graph.
6. Persist the complete replacement result and new metadata intent atomically before exposing it. Any failed session/recheck leaves the previous canonical graphs intact. Metadata fingerprints include the graph's derived recipe; the original recording recipe remains unchanged.

Legacy graph recipes remain absent/unknown; they are not filled from current settings. The research-raw control keeps its existing behavior and does not acquire a sanitized derived profile.

## Correct resident-model reuse

The persistent detector now records the execution identity of its loaded generation. A matching derived recipe can reuse that generation across independently scoped evidence sessions. A different recipe restarts an idle generation before accepting work, preventing new code/weight hashes from being attached to an old resident model.

If an incompatible generation is serving other work, the supervisor rejects the incompatible admission without killing those requests. Existing configured isolated-runner fallback remains available. Concurrent-load qualification is still open; the local acceptance below explicitly checks that warm-cache tests used ready persistent workers and no isolated fallback.

## Validation evidence

| Check | Evidence |
| --- | --- |
| Rust | 84 passed, 0 failed, 2 ignored; `output/depin-implementation/bp04-derived-profile-tests-final-verified.log` |
| Profile/graph contracts | Recording and derived versions have distinct identities; derived profiles bind exact evidence and reject stage substitution or changed intervals |
| Real end-to-end replay | `bp04-derived-profile-final/`: actual privacy and YOLO inference, two independent source sessions, scoped retrieval/statistics/media, checkpoint recovery, post-hoc rebuilds and revocation |
| Separate histories | Initial recording recipe stays byte-equivalent while new derived recipe fingerprints replace the graph's producer history |
| Warm reuse | Repeating the same recipe keeps one ready persistent generation; no isolated fallback |
| Changed recipe | Editing the owned worker entry script creates a different derived recipe and exactly one additional ready generation; repeating that recipe reuses it |
| In-flight drift | The actual isolated detector finishes inference and pauses before process completion. Editing its configured entry script then releasing it returns a profile conflict; previously saved graphs and recording recipe remain unchanged |
| No recapture | All post-hoc rebuilds and rejected rebuilds use retained evidence with zero new source requests; media hashes remain unchanged |
| Retained recovery | Enriched ingest checkpoints remain independently verifiable and republish with observer/detector executables and model files unavailable |

Raw HTTP observations, profile component maps and the rejected rebuild response are in `results.json`. Worker ready/launch records are in the owned `server-*.log` files. The earlier `bp04-derived-profile-real/` is a separate passing development replay. Offline build, Python compilation, JavaScript syntax and diff checks pass.

Reproduce with a fresh output directory:

```sh
cargo test --offline
cargo build --offline
.venv-retrieval/bin/python tools/check_multisession_workflow.py \
  --source PATH_TO_RETAINED_DEVELOPMENT_VIDEO \
  --output output/depin-implementation/new-derived-replay \
  --derived-profiles
```

## Decision and remaining gates

Retain the existing tracker/model algorithm and repair producer identity/cache admission. No threshold was relaxed, no frames were synthesized, and no accuracy, memory or throughput improvement is claimed. Reusing an unchanged loaded model avoids unnecessary reloads while maintaining recipe identity.

This closes the standalone tracker recipe gap, not the full profile/release system. Atomic compatible profile/index activation, migrations, sealed native runtime identities, external VLM/semantic-index profiles, actual old-binary rollback, concurrent/mixed-load behavior and independent generic/privacy/hardware acceptance remain open. These traffic-derived clips are development integration evidence, not held-out tracking-quality evaluation. BP04 and the complete goal remain active.
