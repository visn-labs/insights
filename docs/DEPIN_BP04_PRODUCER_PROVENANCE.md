# BP04: preserve the actual sanitizer producer recipe

## Implemented and validated — 23 October 2026

The Python sanitizer already wrote model hashes, code hashes, detection threshold, runtime versions/providers/precision and timestamp policy. Rust deserialization discarded these fields, so canonical saved evidence could not detect later changes to that recipe. Media-byte equality alone did not close this gap.

`SanitizedMedia` now retains every committed field, including unknown future fields, through a flattened ordered metadata map. Canonical event records and anchored checkpoints preserve those values. Existing source-equality checks therefore reject producer changes even when media and frame-ledger bytes are unchanged.

The current sanitized policy validates required code/model hashes, positive model sizes, descriptive model filenames, threshold bounds, solid replacement, timestamp quantization and FP32 runtime/provider/version/thread metadata. These are provenance/schema checks, not model accuracy acceptance or hardware attestation. A different runtime version can remain explicitly recorded and readable; current server configuration never substitutes for a historical recipe.

## Shared admission and projection identity

Indexing and tracker enrichment now share camera-source admission. They verify retained media/ledger hashes, complete producer recipe, event source scope, session/storage identity and capture extent before any worker opens the source. Tracker enrichment reuses the verified source hash instead of reading the source again just to hash it.

Metadata projections include `source_provenance_sha256`, derived from the complete typed source descriptor, in their input fingerprint. Changing the producer recipe invalidates the prior projection even when pixels and intervals remain identical. Unknown legacy descriptors do not acquire an invented producer digest.

The workspace exposes a native keyboard-accessible **Processing details · sanitized evidence** disclosure with actual saved model/code hashes, threshold and runtime. It labels the sanitizer as an integration candidate and makes no general privacy accuracy claim. Hashes wrap within the existing coffee/cream layout.

## Validation evidence

| Check | Result |
| --- | --- |
| Rust contracts/regressions | 80 passed, 0 failed, 2 ignored; `output/depin-implementation/bp04-producer-provenance-final-tests.log` |
| Complete descriptor | Roundtrip retains models/code/runtime/threshold/unknown fields; producer changes remain unequal; incomplete/unsupported profiles fail validation |
| Projection identity | Same-pixel producer changes alter the projection fingerprint and reject the previous intent |
| Real sanitizer API replay | `bp04-producer-provenance-boundaries-real/` and final `bp04-producer-provenance-browser-final/`; actual pinned privacy models, no semantic/VLM or tracker inference |
| Seven mutation cases | Model, sanitizer code, redaction tracker code, threshold, runtime, unknown future field and missing profile: deny query, analytics, metadata rebuild, visual preparation, tracker enrichment and all three media routes |
| Restore/restart | Restoring exact original metadata restores access. Restart with unavailable inference models/runner preserves the saved recipe and media hashes without recapturing |
| Anchored recovery | Changed producer metadata is rejected before checkpoint reuse. Restoring it permits checkpoint-only recovery with inference unavailable and zero new source requests |
| Revocation | All media denied after revocation; source fixture bytes unchanged |
| Browser | Keyboard disclosure, exact API-saved hashes/runtime and no overflow at 320/768/1024/1440 px; `output/playwright/bp04-recovery/producer-profile-browser.json`, `.log` and screenshots |

Offline build, Python compilation, JavaScript syntax and diff checks passed. The first browser attempt used an older binary containing embedded frontend assets; the service was closed, rebuilt and verified in the final owned replay. That attempt remains separate and is not accepted as UI evidence. Expected 503 responses from the deliberately absent visual worker are not JavaScript exceptions.

Reproduce:

```sh
cargo test --offline
cargo build --offline
.venv-retrieval/bin/python tools/check_producer_provenance.py \
  --output output/depin-implementation/new-producer-replay
```

Optional `--ui-seconds 600` holds completed evidence for browser review; touch `OUTPUT/ui-done` to continue the fault/recovery tests.

## Compatibility and decision

Decision: retain the existing sanitizer/model algorithms and repair provenance admission. No model, accuracy, throughput or memory improvement is claimed. The flattened schema preserves future fields rather than silently discarding them.

Historical canonical strict records that omitted the producer fields cannot be silently upgraded from current settings. Evidence access/recovery remains blocked when the recorded recipe cannot equal the complete verified sidecar. They require an explicitly audited migration or a new evidence job; listing an old job is not evidence acceptance. Source media are not deleted or relabeled. Research-raw records do not become sanitized.

This closes a producer-provenance prerequisite, not the complete profile/rollback gate. Job-wide immutable execution recipes, atomic compatible profile/index activation, migrations, cancellation of incompatible jobs and a genuine old-binary rollback drill remain open. Actual semantic/VLM profile/cache qualification, generic independent privacy/quality evaluation, load and Linux/NVIDIA qualification also remain open. The full implementation goal remains active.
