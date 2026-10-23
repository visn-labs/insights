# BP04: native storage contract and hash-pinned release launch

## Implemented behavior

The native service now declares `visn-storage-reader-v1`. Before recovery, API startup or projection replay, it acquires the existing data-directory owner lease and validates the complete canonical manifest set and the local `storage-contract.json`.

The contract names schema `visn-storage-contract`, version `1`, and required reader features: committed-media hash admission, saved privacy policy, durable revocation, retrieval generation binding and legacy preparation quarantine. The subsequent [backup/restore increment](DEPIN_BP04_BACKUP_RESTORE.md) adds revocation source contracts and worker recording-scope revocation; [root fencing](DEPIN_BP04_ROOT_FENCING.md) adds restore authority admission. An unknown version, required feature, malformed contract or contract symlink rejects startup. A supported subset is strengthened to the current writer requirements; an unknown contract is never weakened.

Canonical recording manifests now persist numeric `storage_version: 1`. Missing version is the explicitly supported legacy format; it defaults to 1 without inventing missing sanitizer/model provenance. Future versions, string/bool versions, invalid JSON, invalid record shapes, symlinked files and filename/record ID mismatch reject the complete startup. Invalid records are no longer silently skipped. Operators must repair or restore the original authoritative record; moving a revocation manifest aside is not a valid recovery procedure.

All manifests are parsed before any interrupted job is rewritten. A future record found after an otherwise valid running job therefore cannot leave that earlier job partly recovered. Rejected startup preserves canonical files and the storage contract; the owner lease file may be created/updated while acquiring ownership. These are compatibility checks, not a multi-file transactional recovery migration.

Two read-only commands run before ordinary configuration/model initialization:

```sh
target/debug/visn-phase0 --storage-capabilities
target/debug/visn-phase0 --storage-check DATA
```

The capability response includes the compiled execution-code hash, reader/version support and features. Storage check parses supported canonical state and returns compatibility without creating a data directory, writing a contract or starting the API.

## Qualified local launch

Use a reviewed executable hash from the release artifact record:

```sh
python tools/run_visn_release.py --binary /absolute/path/to/visn-phase0 \
  --sha256 APPROVED_EXECUTABLE_SHA256 --data-dir DATA --check
python tools/run_visn_release.py --binary /absolute/path/to/visn-phase0 \
  --sha256 APPROVED_EXECUTABLE_SHA256 --data-dir DATA
```

The launcher hashes the executable with bounded read buffers, rejects a pin mismatch and refuses an executable missing the native reader marker **before executing it**. This matters because the actual old revision ignores unknown arguments and would otherwise start a service during a capability probe. Supported executables must return capabilities and pass their own read-only state check. The launcher rechecks the executable hash before replacing itself with the service; startup revalidates state under the owner lease.

`VISN_DATA_DIR` is set from the explicit argument. Media policy defaults to `sanitized-v1` when unset; an explicit environment policy remains explicit, and saved strict evidence retains its admission checks. No model/algorithm quality approval is inferred from a compatible binary or a matching hash.

This is a trusted local release workflow, not hostile-binary attestation or an OS execution restriction. Keep approved binaries and release hashes immutable and access-controlled. Directly executing an old binary bypasses this launcher; that old program cannot understand the new marker. Service/deployment configuration must use the qualified launcher. The previously tested Git `9d7e124` remains unsupported for sanitized-v1 rollback.

The offline preparation migration tool also rejects unsupported storage contracts and manifest versions before modifying a binding. The native feature set and migration-tool supported feature names must evolve together.

## Validation

Artifacts under `output/depin-implementation/`:

| Evidence | Result/scope |
| --- | --- |
| `bp04-release-admission-rust-final.log` | 87 passed, 0 failed, 2 ignored; offline build passes in `bp04-release-admission-build.log` |
| `bp04-release-admission/` | 11 actual executable/launcher cases: wrong hash and actual old binary rejected without execution; future contract, unknown feature, broken contract, contract symlink, future/string manifest version, broken manifest and ID mismatch rejected by direct startup and launcher without canonical changes; compatible exec launch persists the contract |
| `bp04-release-projection/` | Actual sanitized capture and SQLite preparation delivery/retry/restart regression passes with the versioned native build; vectors are fixtures |
| `bp04-release-qwen/` | Passed in 67.7 seconds: actual cached-Qwen sanitized recording, legacy quarantine/offline proof migration, generation cutover/rollback, restart, source/registry tamper and both target revocation acknowledgements. Two source requests are the two explicit recordings; no recapture was added |

The rejection cases include a valid running record alongside the incompatible state and compare canonical file hashes before/after both startup attempts. The read-only empty-root check creates no files. The actual old executable is the isolated build previously documented in [legacy migration](DEPIN_BP04_LEGACY_MIGRATION.md).

Reproduce after an offline build, using fresh output directories:

```sh
cargo test --offline
cargo build --offline
.venv-retrieval/bin/python tools/check_release_admission.py \
  --old-binary output/depin-implementation/bp04-old-binary-compat/target/debug/visn-phase0 \
  --evidence-run output/depin-implementation/bp04-legacy-binding-qwen-final \
  --output output/depin-implementation/FRESH_RELEASE_CHECK
.venv-retrieval/bin/python tools/check_retrieval_deployments.py --actual --legacy-binding \
  --output output/depin-implementation/FRESH_RELEASE_QWEN
```

## Remaining gates

This versions the native canonical manifest/global reader boundary. It is not a claim that every external worker SQLite schema has upgrade/downgrade coverage. Retrieval registries and evidence profiles retain their own admission checks. A broader supported-release matrix, deliberate schema transitions, crash-safe multi-file upgrade recovery and backup restore preserving all revocations remain required.

Host/deployment launch enforcement, authentication, native/external runtime qualification, independent generic/privacy quality, real reranker inference, mixed load and Linux/NVIDIA acceptance remain open. BP04 and the full implementation plan remain active. Next qualify recovery/restore compatibility without reviving revoked evidence; do not replace that test with a cold startup or JSON roundtrip.
