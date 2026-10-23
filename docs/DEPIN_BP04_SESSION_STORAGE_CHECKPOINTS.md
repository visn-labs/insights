# BP04: session storage and camera checkpoint sets

## Implemented — 22 October 2026

Camera results now optionally declare a typed `storage_session_id` UUID. Evidence for that layout lives below the physical camera directory at `sessions/<uuid>/`, with independent `source.mkv`, thumbnails and clips. Path derivation uses the typed UUID, rather than interpolating arbitrary source-session text into a path. The declared storage UUID must match the parsed source-session identifier. Existing records omit the field and continue using their original camera directory.

Camera checkpoints can now contain an ordered set of session results. The decoder accepts both the existing single-result object and a session-result array. Publication still uses the existing atomic/fsynced checkpoint and a hash anchored in the job manifest. Single-result checkpoints retain the old JSON shape. Checkpoints are capped at 32 MiB and 256 members. Empty sets, duplicate session/event IDs, foreign job/camera scopes, mismatched storage/source identities and mixed unscoped multi-member sets are rejected.

Retained recovery verifies the anchored set, derives every member's paths and checks each source, thumbnail and clip before reuse. A checkpoint can qualify a recording for a recovery attempt even when no legacy camera-root `source.mkv` exists; invalid checkpoint contents fail the attempt instead of falling back to a live source. Revocation still clears the checkpoint anchor and denies recovery. Camera-result processing now accepts a set and counts distinct physical cameras rather than treating every session as another camera.

## Evidence and scope

- Rust suite: 75 passed, 0 failed, 2 ignored in `output/depin-implementation/bp04-session-checkpoints-tests-storage-alias.log`.
- The new durable-storage contract writes independent same-name fixture files in two UUID-scoped directories, atomically publishes/reads the checkpoint array, verifies its content hash and preserves member order/path selection. It also reads the old checkpoint shape and rejects wrong camera IDs, mismatched/duplicate session IDs, missing multi-member storage scope and empty sets. These are synthetic storage fixtures, not video-quality tests.
- Distinct textual forms of the same storage UUID are also rejected as duplicates by checkpoint and projection validation, preventing alias-based directory reuse. The regression explicitly covers that case.
- Actual recording recovery replay: `output/depin-implementation/bp04-session-set-recovery/`, 64 HTTP checks plus crash/ownership/child checks. Cancellation, restart, anchored reuse without an observation worker, modified-checkpoint rejection/restoration and missing-retained-evidence behavior passed. This API replay uses the existing single-source producer, so it qualifies compatibility and recovery orchestration, not multiple source-session media ingestion.
- Actual sanitized recording/YOLO/metadata replay: `bp04-session-set-metadata/` passed source-session filters, graph/analytics parity, corruption fallback, changed source/timing admission, hard restart and local revocation cleanup.
- Build, format and diff checks passed. No frontend or logo changes were made in this increment.

## Next producer integration

The subsequent [multi-session recording increment](DEPIN_BP04_MULTISESSION_RECORDING.md) implements the producer integration and validates real clock-reset media through the API and browser. The scope below describes what remained at the checkpoint-consumer increment; the newer document records current acceptance limits.

The storage/checkpoint consumer is ready for session results, but `MemoryService` still produces one result from one observer output. The next increment must connect the capture directory to that producer: analyze each verified sanitized session independently, materialize under its declared storage UUID, return the ordered result set and checkpoint that whole set. It must verify the bundle lineage before consumption and preserve retained-only recovery without reopening the source.

Only after that integration can clock-reset capture be claimed through the actual recording API. Playback, graph/interpretation scope, projection cleanup, browser selection and recovery then need a real multi-session replay. Automatic transport reconnect, source-clock/UTC uncertainty, profile rollback, generic quality acceptance and hardware qualification remain open. BP04 and the complete plan remain active.
