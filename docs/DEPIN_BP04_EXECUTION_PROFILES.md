# BP04: immutable recording execution recipes

## Implemented candidate — 23 October 2026

New strict sanitized recording jobs persist `execution_profile` before HTTP acknowledgement. Research-raw jobs retain their existing request identity and have no inferred sanitized recipe. This is a recording-execution compatibility guard, not a completed deployment/profile promotion system.

`recording-execution-v1` contains a version, component identities and an aggregate fingerprint. Component identities cover:

- Compiled Rust source, Cargo manifest/lock, build script, compiler version, target/features and build flags. The build identity is embedded in the binary; changing source files beside a running binary does not pretend to change the code already executing.
- The bounded Python tools tree and configured Python entry scripts, including scripts outside that tree. The tools scan is deliberately conservative: changing an unused test/helper also prevents unfinished execution under the old recipe. Use immutable deployment trees rather than editing active workers.
- Verified pinned face/plate model bytes and model lock; tracker weights when linked memory is requested.
- Python executable bytes, Python/distribution versions and the actual bundled/selected FFmpeg executable hash. Runtime probes do not load a model.
- Recording duration, observer cadence, camera/cluster scope, selected stages/model IDs, worker arguments, detector settings and configured VLM parameters. Public recipe components expose digests rather than source URLs, credentials or configuration values.

The request/idempotency fingerprint includes the recipe for new strict jobs. Reusing a key under a different recipe is a conflict, rather than silently assigning different execution to the same operation. Unavailable required models/runtime block new acknowledgement; they do not select another backend.

## Execution and recovery rules

The worker verifies recipe equality before starting new work, before checkpoint publication and before final result publication. A changed recipe leaves unfinished work failed/restricted; unqualified output is not anchored as a reusable checkpoint. Profile mismatch/unavailability returns an explicit conflict for an attempted resume. The original recipe is never replaced by current settings.

Recovery distinguishes:

1. **Unfinished computation:** the original recipe must match before processing retained source. Recovery still cannot reopen an old live interval.
2. **Read-only checkpoint publication:** every requested camera/session checkpoint must exist and pass its anchor, scope, source, derivative and graph checks. If linked memory was requested, the saved graph must exist and use the supported graph version. No active observer, detector or model file is required to republish this verified state.

Enriched camera results are now checkpointed after tracking, in addition to the source-observation checkpoint. A verified retained graph is reused without rerunning tracking. Previously the checkpoint could omit the graph, causing recovery to run the current tracker again.

Metadata projection fingerprints and documents bind the recording recipe fingerprint. Legacy missing recipe values remain missing; checkpoint-only read compatibility does not invent an execution history.

## Saved policy survives deployment changes

Saved sanitized jobs retain sanitized admission for queries, statistics, projection/visual preparation, tracker enrichment and media access even when the active deployment switches to `research-raw-v1`. Their metadata is verified using the saved policy. Switching the active mode cannot turn changed sanitized evidence into an accepted raw fallback.

New research-raw jobs remain a separately labeled control. This guard does not qualify raw material as sanitized or delete retained source bytes.

## Validation

| Check | Evidence |
| --- | --- |
| Rust | 83 passed, 0 failed, 2 ignored; `output/depin-implementation/bp04-execution-profile-policy-final-tests.log` |
| Version/identity contracts | Recipe roundtrip, component drift, unsupported version, recipe-bound metadata intent, and saved-policy regression |
| Real sanitizer fault replay | `bp04-execution-policy-final/`: persist before acknowledgement, cancel before checkpoint, reject settings/private-model drift, restore original recipe, resume with zero source requests |
| In-place script drift | The actual worker pauses after real sanitized commit; changing its configured script before release prevents checkpoint anchoring; restoring it permits retained-only recovery |
| Different active recipe | Anchored results republish with inference unavailable while preserving the original recipe and media hashes |
| Policy rollback check | Research-mode restart can read valid saved sanitized evidence but denies changed producer metadata at query/statistics/preparation/tracker/media boundaries |
| Actual tracker integration | `bp04-execution-profile-tracker-final/`: real privacy and YOLO, two source sessions, complete graph checkpoints, exact graph/media preservation, restart and revocation. Checkpoint recovery runs with observer, detector executable, tracker weights and privacy models all unavailable |

Both replay tools preserve their source fixtures and use owned loopback services. Offline build, Python compilation, JavaScript syntax and diff checks pass. They establish local integration behavior, not independent model accuracy.

The first tracker run (`bp04-execution-profile-tracker-real/`) failed safely at admission: its Python 3.9 environment lacks `hashlib.file_digest`. The probe now uses streaming SHA-256 compatible with that actual environment. Subsequent actual tracker runs passed; failed evidence is retained.

Reproduce with fresh output directories:

```sh
cargo test --offline
cargo build --offline
.venv-retrieval/bin/python tools/check_execution_profiles.py \
  --output output/depin-implementation/new-execution-replay
.venv-retrieval/bin/python tools/check_multisession_workflow.py \
  --source PATH_TO_RETAINED_DEVELOPMENT_VIDEO \
  --output output/depin-implementation/new-graph-checkpoint-replay
```

## Remaining profile/rollback gates

This does not complete BP04 or the full plan. [Standalone tracker enrichment now has a separately pinned recipe](DEPIN_BP04_DERIVED_PROFILES.md) and preserves the initial recording recipe. Other derived stages still require their own qualification. Atomic compatible profile/index activation, explicit migrations and a genuine older-binary rollback drill remain open. Merely pointing an older writer at these manifests is not a qualified rollback.

Distribution versions are recorded, but this is not a sealed hash inventory of every installed native package file. External VLM service/model weight identity, remote index/cache profiles, resolved automatic hardware/device selection and independent platform qualification remain open. Configured VLM IDs/endpoints are not presented as immutable model-weight proof. Load/capacity, generic held-out quality/privacy, authentication and reward acceptance remain separate gates. No performance or accuracy gain is claimed.
