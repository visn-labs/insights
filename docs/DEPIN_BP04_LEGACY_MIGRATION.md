# BP04: legacy preparation migration and actual older-binary assessment

## Legacy policy

An attempted preparation intent without a deployment generation cannot inherit the active registry selection. Replay quarantines it as `blocked` with `projection_legacy_binding_required`, retaining its task, revision and unbound generation. Explicit prepare/retry cannot bypass that restriction. Fresh, never-attempted intents can select the active deployment. Existing configured-worker operation without an active registry remains supported.

`tools/reconcile_projection_binding.py` provides an offline reconciliation path for **known** tasks. It requires:

1. Rust and its inherited workers have released the existing `.writer.lock`.
2. The operator supplies the expected manifest SHA-256, registry revision and ready generation.
3. The request covers exactly the canonical event IDs, source hashes and intervals; source paths remain inside that recording's storage.
4. The registered URL agrees with any saved worker URL fingerprint, and the actual worker profile matches the registered profile.
5. Read-only exact-task resolution returns the saved task ID, scope, current embedding contract and complete declared interval count.

The command does not run an embedding model or submit a queue job. `/index-jobs` now accepts boolean `resolve_only: true`; it computes the existing deterministic identity after source/profile admission and returns an existing task or HTTP 404. It does not create, retry, repair or enqueue that task. Existing indexing uses the same shared identity function.

Dry-run is the default and leaves the manifest unchanged. Applying first archives the exact original bytes and a separate verification proof under `memory/migrations/projection-binding/`, then atomically publishes the reconciled manifest. File/directory fsync and private file modes are used. A persisted `binding_migration` field carries the proof across normal Rust serialization. The original recording recipe, camera results and event producer descriptors are not relabeled. The proof establishes an equivalent current task contract, **not historical hardware or producer attestation**.

```sh
# Stop Rust and its owned workers. Obtain the current registry revision and
# SHA-256 of DATA/memory/manifests/JOB.json through the local operator workflow.
python tools/reconcile_projection_binding.py --data-dir DATA --job-id JOB \
  --generation GENERATION --expect-revision REGISTRY_REVISION \
  --expect-manifest-sha256 MANIFEST_SHA256 --request exact-preparation.json
# Repeat with --apply after reviewing the dry-run result.
```

`exact-preparation.json` is one existing preparation body (`scope_id`, `events`), not the list used by deployment staging. Each event carries `event_id`, `source_path`, `expected_source_sha256`, `start_ms` and `end_ms`.

Unknown saved task IDs, unavailable original workers, changed contracts and historical unregistered targets remain unresolved and blocked. The command never guesses a missing ID. Revocation remains available to deny local evidence and clean known targets. Arbitrary historical/external jobs still need a separately reviewed cleanup policy. The original/proof archives survive subsequent preparation-intent replacement; they contain private operator metadata and must remain private.

## Validation

Evidence under `output/depin-implementation/`:

| Run | What it proves |
| --- | --- |
| `bp04-legacy-binding-rust.log` | 86 passed, 0 failed, 2 ignored; offline build passes in `bp04-legacy-binding-build.log` |
| `bp04-legacy-indexing.log` | 14 existing durable indexing checks pass after sharing task identity |
| `bp04-legacy-resolve-contracts.log` | 3 profile/HTTP checks pass, including absent-task resolution without queue/inference work, boolean validation, matching existing task and unchanged update timestamp |
| `bp04-legacy-binding-fixture/` | Actual sanitized recording and fixture-vector integration: quarantine, normal-prepare rejection, live-owner exclusion, stale manifest/revision and wrong target rejection, unknown-task rejection, unchanged dry-run, exact archive, preserved canonical payload, no added tasks, migrated-proof restart |
| `bp04-legacy-binding-proof/` | Adds separate durable proof archive verification to the same complete cutover/rollback/revocation workflow |
| `bp04-legacy-projection-regression/` | Configured-worker operation without a registry still passes delivery loss, process restart, retry loss, scope mismatch and source tamper checks |
| `bp04-legacy-binding-qwen/` | Actual cached-Qwen CPU FP32 migration/cutover/rollback passes in 74.4 seconds, with no recapture |
| `bp04-legacy-binding-qwen-final/` | Final actual run including separate proof archive passes in 68.7 seconds; two explicit recording requests, no recapture, both revocation target acknowledgements complete |

Reproduce after `cargo build --offline` using fresh output directories:

```sh
.venv-retrieval/bin/python tools/check_retrieval_profiles.py
.venv-retrieval/bin/python tools/check_retrieval_deployments.py --legacy-binding \
  --output output/depin-implementation/FRESH_FIXTURE
.venv-retrieval/bin/python tools/check_retrieval_deployments.py --actual --legacy-binding \
  --output output/depin-implementation/FRESH_QWEN
```

No independent generic retrieval/privacy/tracking quality or performance benefit is inferred from these integration tests. Reranker inference and target accelerator qualification remain separate.

## Actual older-binary decision

Git revision `9d7e12460ba3dc50447f842af158440d742ec41d` was exported into an isolated source directory and built offline with its own locked dependencies and target directory. It was not reconstructed from current code or substituted with a fixture reader. The current and actual old executable each received an independent copy of an owned sanitized recording, originally valid against its committed hash. Only those copies were modified.

`bp04-old-binary-compat/admission-port-verified/results.json` records the comparison below. `admission-final/results.json` reproduces the same decision using the final migration run and additionally asserts the original media remains unchanged:

| Executable | SHA-256 | Original media | Same source after hash-changing append |
| --- | --- | --- | --- |
| Current | `442d3efcaf025e26ac11613ee1c2dc0c896c7e8d45b3aeb25c2b762f8051b9a4` | HTTP 200, exact original hash | HTTP 404; no bytes served |
| Git `9d7e124` | `8efb0ab850c2a5c02e2e65cbd43c5c1f8a1af60b1311b0a996b89e64c471f849` | HTTP 200, exact original hash | HTTP 200; changed bytes served |

The old revision lacks the current committed-media admission contract. This is sufficient to reject it as a sanitized-v1 rollback target; the test does not claim that the appended bytes contain private pixels. Older-state compatibility and unknown-field roundtrips are not established by successfully loading a JSON file.

**Decision:** `unsupported` for direct rollback to this old binary against sanitized-v1 state. `retain_baseline` for the current binary's verified integrity admission and compatible-generation activation/rollback. No automatic downgrade is enabled. Backporting and independently qualifying the required privacy, revocation, ownership and storage contracts—or qualifying another compatible release—is required before old-binary rollback can be accepted. Copying only an old data snapshot could resurrect revoked state and is not a qualified workaround.

The first two probe attempts remain archived: one expected a different fail-closed status, and one discovered the old revision logs configured port zero rather than the assigned listener port. The verified run uses explicit owned loopback ports and tests actual HTTP behavior. Neither setup failure was counted as compatibility evidence.

## Remaining release work

Full release admission/launch enforcement, supported schema-version negotiation, upgrade/downgrade crash recovery, complete restore with revocation continuity, authentication, generic/privacy acceptance, mixed load and Linux/NVIDIA validation remain open. Legacy unknown-task reconciliation is explicitly unsupported rather than silently converted. BP04 and the complete BP00–BP12 goal remain active.
