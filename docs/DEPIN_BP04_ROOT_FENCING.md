# BP04: fenced restore cutover and recovery

## Authority transition

Restore now records a durable, recoverable cutover instead of leaving two ordinary launchable roots. The current reader requires `restore-root-fencing` in the storage contract. Upgrade/admit that reader before taking the authoritative current cut; backup/restore will not quietly strengthen an older source contract after the operator has pinned its digest.

The existing backup/restore command remains the entry point. A transaction binds the source root, canonical destination, snapshot digest, current-state digest and prepared destination inventory. Its source journal is `recovery-cutover.json`; the destination carries `recovery-activation.json`.

| Durable state | Old source | Replacement | Recovery |
| --- | --- | --- | --- |
| Before fence | Ordinary source | Absent/private staging | A failed preflight has not transferred authority |
| Source journal pending, before rename | Startup denied | Absent | Repeat the same restore to rebuild private staging |
| Replacement published, source pending | Startup denied | Published but startup denied | Verify the exact prepared inventory and commit the source journal |
| Source committed, replacement pending | Startup denied | Startup denied | Finish the local activation proof |
| Replacement active | Startup denied | Launchable at its bound canonical path | Retrying the completed transaction does not overwrite newer state |

The source fence is fsynced **before** publication. A committed source journal precedes the active destination proof. The normal service, read-only release preflight and qualified launcher reject any fenced source or pending destination. The old and new roots cannot both become available through this workflow. Canonical recovery does not begin on either blocked root.

The journal is bounded to 64 MiB, including its final committed-state encoding. An oversized prepared inventory fails before the source is fenced or a destination is published. It cannot create a transaction that the recovery reader would refuse to reopen.

Snapshot payloads are explicitly marked `snapshot` and are not launchable roots. Copying an activated root to a different path also fails normal admission because its activation proof binds the canonical destination. The registry operator lock rejects updates on superseded/pending roots; offline legacy-binding migration uses that same lock.

## Resume rules

Repeat the original restore command with the same snapshot SHA, source-state cut and destination. The coordinator reads the existing journal under the source owner and registry leases. It rejects retargeting or changing the pinned cuts. Do **not** delete a fence to make an old root start.

If publication has occurred but activation has not, recovery compares the destination against the entire prepared file inventory before proceeding. A failed startup may have touched its owner lease; only the known owner/operator lock files are excluded from that comparison. Canonical changes are not ignored.

Once the local activation proof matches the committed source journal, repeat calls report `cutover_already_committed` without replaying old manifests. The active root may legitimately contain newer recordings or revocations. If that destination later becomes a source for another cutover, the old transaction reports it as superseded rather than reactivating it.

A committed destination that has disappeared is not rebuilt from the old source cut: it might have held later revocations. Recovery returns `committed_cutover_destination_missing`. Restore the authoritative newer state or its independent revocation record instead of manufacturing an empty replacement.

Private staging left by SIGKILL remains forensic material. Retrying an unpublished transaction can create a new private staging directory and update its prepared inventory while preserving the transaction identity. It never publishes an unrelated existing directory.

## Recovery inspection

The native executable has `--storage-check-recovery DATA`, a read-only format inspection that bypasses authority-marker admission. Its response is labeled `recovery_inspection_only`; it never opens the API. The backup coordinator uses it only after validating the matching source journal, or for private staging it is about to fence/publish.

The ordinary release launcher exposes no recovery bypass. Its normal `--check` and launch still require ordinary startup admission. Calling a recovery format check is not approval to run a fenced root.

These are trusted local operator controls, not OS restrictions on arbitrary old executables or malicious edits. Older readers that understand required-feature negotiation reject `restore-root-fencing`; Git `9d7e124` remains disallowed by the qualified launcher. Keep binaries, journals and activation proofs private and immutable except through the coordinator.

## Validation

Evidence under `output/depin-implementation/`:

| Artifact | Scope |
| --- | --- |
| `bp04-root-fencing-rust-verified.log` | 88 passed, 0 failed, 2 ignored; native source fencing, pending activation and bound-path checks included |
| `bp04-root-fencing-build-final.log` | Offline build passes |
| `bp04-root-fencing-release/` | All 11 executable/hash/future/corrupt-state release checks pass |
| `bp04-root-fencing-fixture/` | Complete owned sanitized-evidence/fixture-vector cutover with SIGKILL at all three pending boundaries; direct native startup and launcher rejection for blocked roots; same-transaction recovery, retarget rejection, registry-write denial, snapshot-payload denial and copied-root rejection |
| `bp04-root-fencing-fixture-final/` | Complete fixture workflow passed in 35.9 seconds, including forced journal-capacity rejection before fencing |
| `bp04-root-fencing-profile.log`, `bp04-root-fencing-indexing.log` | Worker profile/read-only lookup and existing indexing/revocation contracts |
| `bp04-root-fencing-qwen/` | Actual cached-Qwen full restore, fencing, crash recovery, semantic rebuild, restart and post-restore revocation passed in 80.8 seconds |
| `bp04-root-fencing-qwen-final/` | Final actual run passed in 77.2 seconds, including journal-capacity admission, all three SIGKILL boundaries and two-target post-restore revocation. Source journal is committed and destination activation active with matching transaction; two source requests are the two explicit captures, with no recapture |

The workflow also retries the completed transaction after a new revocation and confirms the evidence remains denied. It temporarily makes the committed destination unavailable and confirms recovery refuses to recreate it from stale state. Original and post-restore revocations both require all known target acknowledgements. Source request counts must not increase during recovery.

Reproduce after an offline build in fresh output directories:

```sh
cargo test --offline
cargo build --offline
.venv-retrieval/bin/python tools/check_retrieval_deployments.py --backup-restore \
  --output output/depin-implementation/FRESH_FENCING_FIXTURE
.venv-retrieval/bin/python tools/check_retrieval_deployments.py --actual --backup-restore \
  --output output/depin-implementation/FRESH_FENCING_QWEN
```

An initial unit assertion used a macOS `/var` alias rather than its canonical `/private/var` destination. The corrected test uses the same canonicalization as the coordinator; the original failed log remains archived. No compatibility/inference failure was counted as a pass.

## Remaining acceptance

This closes the supported local cutover's old-root restart gap. It does not establish an independent, unrollbackable revocation witness, authenticate manually supplied historical clones, recover a lost newer root from a stale snapshot, or prevent a privileged operator from modifying/removing control files. External worker databases and unreachable historical storage remain separate cleanup/restore obligations.

Power-loss/storage-hardware qualification, arbitrary I/O/disk-full fault coverage, cross-host ownership, supported external schema transitions, independent generic/privacy quality, authentication, load and Linux/NVIDIA qualification remain open. SIGKILL tests are not physical power-loss tests. Next establish independent revocation durability and authenticated contribution/evidence boundaries. BP04 and the full implementation plan remain active.
