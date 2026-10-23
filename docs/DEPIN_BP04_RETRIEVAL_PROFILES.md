# BP04: bound visual retrieval to its actual producer profile

## Implemented prerequisite — 23 October 2026

Atomic profile/index activation requires a trustworthy worker identity. The previous cache identity named the resolved embedding snapshot and sampling settings, but omitted resolved device/precision and other numerical runtime settings. It also did not content-bind the model files or local implementation. That allowed different producers to share a cache version.

The Qwen worker now:

- Resolves embedding and reranker snapshots once, hashing their actual files with bounded streaming memory. Both identities are independent of local cache directory names.
- Seals local retrieval tools and the pinned Qwen wrapper implementation, including file membership. Changing files beside an already running worker does not silently create a new profile for old loaded code.
- Fixes the selected device before loading and records dtype, package versions, Torch build, machine architecture, thread settings, matmul precision and deterministic/TF32/cuDNN settings.
- Content-binds the actual selected FFmpeg executable and rejects decoder-selection changes.
- Rechecks file identity/membership and numerical runtime before using caches and before committing new vectors/scores. Initial hashes read full artifact bytes; unchanged later checks use file identities rather than rehashing gigabytes per request.

The embedding version includes these identities, so old cache keys are not relabeled. Old entries remain separate. Rerank cache keys also include the reranker identity. A changed producer cannot reuse an old prepared result as a current embedding version.

## HTTP and Rust admission

`GET /profile` returns `visn-retrieval-profile-v1`, its fingerprint, numerical/decode policies and index schema/database identity. The manifest labels actual implementations as integration candidates, and fixture/unsealed models separately. Resolving file provenance is not a model-quality or instantaneous-residency claim.

Requests can provide `X-Visn-Expected-Profile`. The worker compares it before search, interpretation or preparation and checks profile consistency afterward. Mismatch or in-flight drift returns a conflict. Successful responses and preparation receipts include the profile actually used. Profile-bound task/status responses expose the current worker identity alongside the task's embedding version and `profile_current` state.

Rust supports an explicit deployment pin:

```sh
VISN_RETRIEVAL_URL=http://127.0.0.1:8186
VISN_RETRIEVAL_EXPECTED_PROFILE=THE_64_CHARACTER_FINGERPRINT_FROM_PROFILE
```

The fingerprint is validated at startup. Rust forwards it on search, sequence interpretation, visual preparation/status/action and projection replay. It also verifies returned profile provenance, rejecting a worker that ignores the header or omits the binding. Projection replay requires the receipt's embedding profile to be current. Existing unpinned configurations remain the compatibility control.

If semantic search cannot honor the selected profile, Rust retains its existing explicit lexical fallback and reports semantic unavailability. It does not claim that another semantic model ran successfully. Revocation cleanup remains independent of model-profile availability.

## Validation

| Check | Evidence |
| --- | --- |
| Rust | 85 passed, 0 failed, 2 ignored; `output/depin-implementation/bp04-retrieval-pin-rust-tests-final.log`; offline build passes |
| Existing retrieval | 3 contract checks pass in `bp04-retrieval-contracts-verified.log` |
| Durable indexing | 14 checks pass in `bp04-retrieval-indexing-verified.log` |
| Profile/HTTP | 2 checks pass in `bp04-retrieval-profile-http-verified.log`: file/membership changes, path-independent identity, matching/mismatched headers, bound preparation receipts and in-flight drift without query-cache commit |
| Actual Qwen | `bp04-real-retrieval-pin-final/`: cached embedding/reranker weights are hashed; actual CPU FP32 video and text embeddings run; preparation completes; matching search and repeated-result parity pass; a wrong profile is rejected |
| Actual Rust vertical slice | Same run records a new sanitized interval through Rust, retrieves it through the pinned Qwen worker and verifies returned provenance. Restarting Rust with a mismatched pin yields explicitly labeled lexical fallback; visual status carries the selected worker profile |

The actual run uses offline cached Qwen weights, an owned synthetic video and loopback services. No original weight files are modified or downloaded. Only the embedding model is inference-loaded in this smoke test; reranker file identity is verified but reranker inference is not acceptance-tested here. Expected transient busy status is retried by the read-only test probe. Source/semantic quality, inverse actions and generic performance are not qualified by a one-clip smoke test.

Reproduce with fresh output directories:

```sh
cargo test --offline
cargo build --offline
.venv-retrieval/bin/python tools/check_retrieval_profiles.py
.venv-retrieval/bin/python tools/check_real_retrieval_profile.py \
  --output output/depin-implementation/new-real-profile-check --rust
```

The actual script sets offline Hugging Face operation, uses two bounded 64px frames and CPU by default. Actual reranking, target accelerator profiles and independent quality acceptance remain separate.

## Activation follow-up

The subsequent [durable activation increment](DEPIN_BP04_RETRIEVAL_ACTIVATION.md) adds the local compare-and-swap registry, immutable request selection, declared-scope preparation checks, generation-bound controls and historical-target revocation. It validates compatible-generation rollback with the current binary. It does not establish older-binary/schema compatibility or quality promotion; see that document for evidence and remaining reconciliation limits.

Model/package file provenance is not remote hardware attestation. Native dependency inventories, external VLM weight identity, authenticated service access, concurrent load, independent generic/privacy quality and true old-binary rollback remain open. BP04 and the full goal remain active.
