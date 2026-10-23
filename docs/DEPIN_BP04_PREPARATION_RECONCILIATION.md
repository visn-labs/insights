# BP04: preparation acknowledgement and replacement consistency

## Changes

The visual projection dispatcher persists its selected deployment generation and worker URL fingerprint **before** sending its first remote request. A lost acknowledgement therefore retains the old selection through restart or activation; it cannot silently become a new-generation request.

Before retiring an incompatible intent without a saved task ID, the dispatcher resubmits the same verified scope/source intervals to the original pinned producer. The worker's existing deterministic scope/embedding/source job identity recovers the same logical task. Rust verifies the recovered scope/profile, then requests cancellation and checks the returned task, scope and terminal state. A transport failure keeps the intent retryable rather than declaring it cancelled.

Explicit preparation and task actions now share the dispatch mutex with background replay through the remote acknowledgement. Replacing a pending intent must first confirm retirement on its original target. If that fails, the API returns HTTP 503 and preserves the old durable revision. It does not erase the pending task association. A replacement can proceed once cancellation is acknowledged. Completed/failed tasks are terminal; cancelling them does not erase prepared vectors.

Projection receipts are verified against the selected configuration, fixing a separate use of the startup configuration's profile pin. Cancellation requests also forward the selected producer pin. Ordinary revocation remains independently available without model inference and retains all known target cleanup obligations.

## Evidence

Artifacts are under `output/depin-implementation/`:

- `bp04-reconciliation-rust-verified.log`: 86 passed, 0 failed, 2 ignored; offline build passes in `bp04-reconciliation-build-verified.log`.
- `bp04-reconciliation-projection/`: existing real sanitized-media/SQLite queue replay passes unavailable worker, lost initial acknowledgement, process restart, invalid scope, lost retry acknowledgement and tampered source rejection. Embeddings are fixtures.
- `bp04-reconciliation-replacement-verified/`: passes after dropping initial preparation acknowledgements, delaying automatic replay across restart, activating B, rejecting an unacknowledged replacement without changing A's durable revision, then retiring A and preparing B. Rollback, stale controls, wrong storage and cross-target revocation also pass. Embeddings are fixtures.
- `bp04-reconciliation-startup-pin/`: the same replacement workflow passes with Rust's startup expected-profile pin fixed to A while the registry selects B. B preparation and retrieval use B's profile; they do not incorrectly validate against A. This is a fixture-vector contract test, not model-quality evidence.
- `bp04-reconciliation-qwen/`: actual cached-Qwen lost-acknowledgement cutover run passed in 67.4 seconds, including rollback, restart and both target cleanup acknowledgements. Its two source requests are the two explicit recording jobs; no recapture was added. CPU FP32 bounded-frame embedding inference is exercised; generic quality and reranker inference remain unqualified.

Reproduce in fresh directories after an offline build:

```sh
.venv-retrieval/bin/python tools/check_retrieval_deployments.py \
  --lost-ack --replace-pending --output output/depin-implementation/FRESH_REPLACEMENT
.venv-retrieval/bin/python tools/check_retrieval_deployments.py \
  --actual --lost-ack --output output/depin-implementation/FRESH_QWEN
```

An initial replacement run exposed HTTP 400 for an unavailable cancellation worker. That run remains archived in `bp04-reconciliation-replacement/`; the corrected HTTP 503 behavior and preserved intent pass in the verified run. No inference/accuracy change was introduced.

## Remaining bounds

Recovery depends on the original registered producer and verified canonical interval inputs still being available. It does not infer a historical producer for legacy intents that were already unbound before this change, reconstruct deleted registries, or sweep arbitrary remote jobs outside the durable intent lineage. Explicit API calls and replay are serialized; cross-service distributed transactions and arbitrary external operators are not claimed.

This increment covers registered-generation acknowledgement/replacement races. The subsequent [legacy migration and actual old-binary assessment](DEPIN_BP04_LEGACY_MIGRATION.md) adds quarantine and offline exact-task reconciliation for known legacy tasks. Unknown legacy task identities, release/schema compatibility and broader quality/security/load/platform gates remain open. Current-binary rollback is not older-binary rollback. See the implementation plan and activation document for the full outstanding scope.
