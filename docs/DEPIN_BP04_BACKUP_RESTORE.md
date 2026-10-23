# BP04: revocation-preserving canonical evidence backup and restore

## Supported workflow

`tools/evidence_backup.py` implements an offline canonical-evidence snapshot and restore. Stop Rust and its inherited recording workers first. The existing writer lease and registry operator lock exclude active canonical writers; the hash-pinned native release preflight must accept the source state.

Backup copies canonical `memory/`, the storage contract, recovery lineage and retrieval deployment registry. A descriptor records every payload file's SHA-256 and size, the source-state digest and the executable hash. The snapshot is published by one directory rename after file/directory fsync. It is not a copy of an actively changing service tree.

External worker SQLite indexes, job queues, revocation databases, model weights, uploads and ephemeral service jobs are **not** included. Remote workers retain their own authority and storage identities. The copied memory may contain retained sanitized media for revoked recordings: revocation is access denial/cleanup, not physical media erasure. Keep backup directories private.

On the first backup, a durable recovery-lineage UUID is created under the source owner lease. Restore requires the same lineage in the backup and an authoritative current state, plus the expected snapshot descriptor digest and current-state digest. The current digest covers all canonical job manifests, the registry, contract and lineage. This prevents accidentally using a different root or a stale current cut; it is not a way to detect tombstones deliberately withheld from an allegedly authoritative root.

Restore creates a separate destination and never overwrites an existing root. It verifies the complete snapshot inventory, copies into a private staging directory, rechecks copied bytes, then carries forward the union of snapshot and current revocations. A recording revoked after backup stays revoked, with searchable results and checkpoint associations removed. New revoked jobs absent from the backup are also carried forward. Non-revoked jobs created only after the backup are not silently claimed recovered; their IDs are listed as excluded in `restore.json`.

Known historical/staged/current retrieval targets are retained. Active semantic selection is cleared, and non-revoked visual intents are blocked with `projection_restored_requires_preparation`. Explicitly prepare and activate an appropriate worker generation for the restored paths. Old task receipts and external SQLite databases are not reused as proof of restored coverage. Local and remote cleanup acknowledgements for restored revocations are reset to pending and replayed, rather than copied as completed.

The entire staged root passes native recovery-format checks before atomic publication. The subsequent [root-fencing coordinator](DEPIN_BP04_ROOT_FENCING.md) fences the source before publication and keeps a published destination non-launchable until commit/activation completes. A failed or killed restore cannot publish half of that destination. SIGKILL can leave a private `.evidence-pending-*` directory; preserve it for investigation and repeat the same pinned command to resume. Removal of an orphan must be an explicit operator action after checking its provenance; no broad automatic deletion is performed.

## Commands

Use a reviewed executable hash and fresh destinations. JSON output from backup contains `snapshot_sha256`; obtain the current cut only after stopping the authoritative current service.

```sh
python tools/evidence_backup.py --binary BINARY --sha256 APPROVED_SHA \
  backup --data-dir CURRENT_DATA --destination SNAPSHOT
python tools/evidence_backup.py --binary BINARY --sha256 APPROVED_SHA \
  current-cut --data-dir CURRENT_DATA
python tools/evidence_backup.py --binary BINARY --sha256 APPROVED_SHA \
  restore --snapshot SNAPSHOT --snapshot-sha256 SNAPSHOT_SHA \
  --current-data-dir CURRENT_DATA --current-state-sha256 CURRENT_CUT_SHA \
  --destination RESTORED_DATA
```

Run the restored service through the [qualified release launcher](DEPIN_BP04_RELEASE_ADMISSION.md). Confirm blocked evidence remains denied, inspect `restore.json`, prepare non-revoked intervals against the intended worker, then activate the validated generation. Retain the original root, immutable snapshot descriptor and authoritative revocation state until independent verification completes.

## Revocation fixes required by restore

Two restore replays exposed problems beyond copying files:

1. Historical workers require a stable source contract. Canonical revocation now saves all actual event/session source paths before results are removed, alongside nominal camera paths. Those paths remain an audit record through restore instead of being recomputed under a new directory.
2. A still-valid recording can be revoked **after** restoration. Workers now advertise and acknowledge `recording-scope-v1`: for a canonical recording UUID they derive their own source paths from scope-bound queue items and tracked canonical artifacts across their vector policies. No external sender path outside the worker root is trusted or opened. Existing path-based revocation remains available as `paths-v1`.

The worker durably denies the scope before cache deletion. Canonical `memory/RECORDING_UUID/` media is denied by scope even if its absolute path changes, including restored aliases. Existing revision conflicts still reject; source-path contracts are not silently reassigned to unrelated scope IDs. Scope cleanup does not load a model.

Rust attempts every known cleanup target and retains independent acknowledgements even when another target fails. A wrong storage identity cannot acknowledge its predecessor's cleanup; the other reachable target can still finish. Completion requires all known target acknowledgements. Reader features `revocation-source-contract` and `worker-recording-scope-revocation` prevent a compatible-reader downgrade from silently discarding these requirements. The offline binding migration tool supports the same features.

## Evidence

Artifacts under `output/depin-implementation/`:

| Run | Scope |
| --- | --- |
| `bp04-backup-restore-scope-rust.log` | 87 passed, 0 failed, 2 ignored; offline build in `bp04-backup-restore-scope-build.log` passes |
| `bp04-backup-restore-scope-indexing.log` | 14 existing queue/cache/revocation checks pass; legacy path-contract behavior remains available |
| `bp04-backup-restore-fixture-verified/` | Backup before revocation; stale digest/payload tamper denial; owned restore SIGKILL before publication, absent destination and unchanged current state; retry, valid playback, lexical retrieval, explicit semantic rebuild, restart and old revocation denial |
| `bp04-backup-restore-scope-fixture/` | Adds successful revocation of the still-valid recording after restore, acknowledgements from both roots, direct worker restored-path denial and partial acknowledgement despite a wrong-storage target |
| `bp04-restore-session-paths-traffic/` | Actual privacy/YOLO source-clock rollover, session retrieval/media/checkpoint recovery and revocation; every actual session source is in the saved contract, retained-media hashes unchanged |
| `bp04-restore-release-regression/` | All 11 executable/contract admission cases pass after the new required features |
| `bp04-backup-restore-qwen-final/` | Passed in 76.2 seconds: wrong-lineage and existing-destination rejection, complete snapshot/crash/restore/rebuild/restart workflow, both original and post-restore revocation cleanup acknowledged by two targets, direct worker denial. Two source requests are the two explicit captures; no recapture |

The earlier `bp04-backup-restore-fixture/` failure remains archived: it found original/restored path-contract mismatch, and a test assumed a description string rather than the actual structured description. The initial multi-session fixture in `bp04-restore-session-paths/` lacked detected entities and failed that workflow's prerequisite; retained traffic with independently observed vehicle detections passes. These failures are not counted as successful acceptance.

Reproduce with a fresh output directory after `cargo build --offline`:

```sh
.venv-retrieval/bin/python tools/check_retrieval_deployments.py --backup-restore \
  --output output/depin-implementation/FRESH_RESTORE_FIXTURE
.venv-retrieval/bin/python tools/check_retrieval_deployments.py --actual --backup-restore \
  --output output/depin-implementation/FRESH_RESTORE_QWEN
```

The actual inference profile remains cached Qwen CPU FP32 with bounded frames and owned synthetic footage. Session validation additionally uses retained traffic and actual privacy/YOLO inference. These are recovery/integration checks, not independent generic privacy/semantic/tracker quality or performance qualification.

## Remaining boundaries

The authoritative current revocation cut must survive independently. If it is lost, an old backup alone cannot prove there were no later revocations; the supported tool refuses to manufacture a replacement current lineage/cut. Independent disaster-recovery tombstone replication/witnessing remains open. The [subsequent fenced cutover](DEPIN_BP04_ROOT_FENCING.md) prevents supported old-root restart and rejects changing a recorded transaction's destination/cuts. Hashes and a shared lineage UUID still cannot authenticate an arbitrary historical clone whose control files were withheld or changed.

This does not restore external worker databases or claim their erased root/storage identities can be recreated. Unreachable historical roots keep cleanup incomplete. Authentication, a supported external schema/release matrix, migration fault coverage beyond this canonical snapshot transaction, independently approved datasets/quality, mixed load, and Linux/NVIDIA qualification remain open. Ephemeral upload/cluster jobs and future accounting stores require their own restore contracts. Hostile-filesystem or hostile-operator protection is not established by local hashes/locks.

Keep BP04 and the complete implementation plan active. Supported local root fencing/cutover recovery is now covered by the linked increment; next close independent revocation durability and authenticated connector/evidence access before treating this as a release-ready contribution workflow.
