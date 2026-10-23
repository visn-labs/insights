# BP04: durable retrieval activation and rollback

## Implemented contract

The local operator registry at `DATA/retrieval/deployments.json` binds a generation to one immutable worker URL, full producer profile, storage identity and declared preparation scopes. Registry publication uses a writer lock, fsync and atomic rename. Activation requires the expected registry revision; stale operators cannot overwrite a newer selection.

Staging registers an inactive target **before** indexing footage. Preparation requires explicit source SHA-256 values and matching task, scope, coverage and producer receipts. Failed or interrupted stages remain registered for cleanup and cannot become active. Activation and rollback revalidate the worker and declared preparation inputs before publishing the active pointer. This checks declared scopes, not completeness of every recording in the installation.

Each foreground request holds one selected configuration across retrieval and interpretation. A switch preserves already selected requests. Visual preparation records its generation, and retry/cancel controls forward that generation. Pending tracked preparation under an incompatible embedding/vector contract is cancelled; retrying it while retired returns a conflict. Compatible historical work retains its original identity.

Revocation immediately denies local evidence and separately collects acknowledgements from configured, active, historical and staged targets. Partial acknowledgements persist. New registry revisions reopen cleanup coverage. A wrong or unavailable historical storage instance cannot acknowledge the old instance's cleanup. The storage identity includes canonical root/device/inode and a UUID persisted in the revocation database: normal restart preserves it; rebuilding that database changes it. Model loading is not required to revoke.

Malformed registries fail semantic selection closed. Lexical-only requests remain independent of the registry. Without a registry, existing configured-target behavior remains available. A URL cannot be registered against a different profile or storage identity; use a separate endpoint and retain historical services until their cleanup obligations are resolved.

## Operator workflow

Use the same data directory as Rust. `requests.json` is a list of preparation bodies containing `scope_id` and `events`; every event must include `expected_source_sha256` and the existing source/interval fields.

```sh
python tools/retrieval_deployments.py --data-dir DATA status
python tools/retrieval_deployments.py --data-dir DATA stage \
  --url http://127.0.0.1:WORKER_PORT --requests requests.json
# For subsequent stages, also pass --expect-revision CURRENT_REVISION.
python tools/retrieval_deployments.py --data-dir DATA activate GENERATION \
  --expect-revision CURRENT_REVISION
```

Rollback uses the same activation command with a previously ready generation and the current revision. It rechecks that generation; it does not rewrite old evidence or copy model identities onto new artifacts. Fixture producers require the explicit `--allow-fixture` testing flag. Actual producers are still experimental integration candidates, not quality-approved models.

Capacity bounds: 4 MiB registry, 256 targets, 128 declared scopes per target, 1–10,000 events per scope, 1,024 activation-history entries, and bounded preparation polling. No public HTTP administration endpoint was added. Keep operator files private and preserve historical targets.

## Validation

Final evidence is recorded in `output/depin-implementation/`:

- `bp04-registry-rust-final.log`: 86 passed, 0 failed, 2 ignored; offline build in `bp04-registry-build-final.log`.
- `bp04-registry-retrieval-final.log`: 3 existing retrieval checks.
- `bp04-registry-indexing-final.log`: 14 durable indexing checks.
- `bp04-registry-profile-contracts-final.log`: 3 checks, including persistent storage identity and database-reset distinction.
- `bp04-registry-storage-instance-qwen/`: full Rust/sanitized-media/cached-Qwen replay passed in 71.3 seconds; `results.json` records both generations and two completed revocation acknowledgements. The two source requests are the two explicit recording jobs; recovery and rollback added none.

The end-to-end harness exercises A→B activation while an A search response is held in flight, B retrieval, incompatible pending-task retirement, stale-generation retry rejection, failed staging, stale revision rejection, rollback, restart, corrupt-registry denial, wrong-storage rejection and revocation across both roots. It checks that recovery and rollback make no additional source requests. Expected bad-source and revoked-evidence failures are intentional assertions.

```sh
cargo test --offline
cargo build --offline
.venv-retrieval/bin/python tools/check_retrieval_profiles.py
.venv-retrieval/bin/python tools/check_retrieval_deployments.py --actual \
  --output output/depin-implementation/FRESH_DIRECTORY
```

## Qualification limits and next gates

The actual test uses owned synthetic video, two bounded 64px frames and cached Qwen embedding inference on CPU FP32. Model residency is serialized between workers. Reranker files are identified but reranker inference, concurrent capacity, latency improvements and generic semantic quality are not qualified. The HTTP workflow is exercised; this increment does not claim new browser acceptance.

This is local generation rollback using the current compatible binary, **not** an older-binary/schema-migration drill. Authentication, native runtime sealing, external VLM weight identity, independent generic/privacy quality, load and Linux/NVIDIA qualification remain open. Operational readiness does not promote a model on quality grounds.

The registry retains known roots for revocation; it is not a distributed transaction. The subsequent [preparation reconciliation increment](DEPIN_BP04_PREPARATION_RECONCILIATION.md) covers registered-generation lost acknowledgements and explicit replacement races. Historical unbound legacy intents and arbitrary external jobs still require migration/reconciliation policy. Cleanup receipts do not establish physical media erasure. Do not delete old registry targets or silently reuse an old endpoint for a replacement storage instance.

Continue the implementation plan with reconciliation and release compatibility gates, followed by the remaining BP04–BP12 acceptance work. BP04 and the full project goal remain active.
