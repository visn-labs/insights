# BP04: independent source-clock capture rollover

## Capture-layer candidate — 22 October 2026

`tools/privacy_media.py --capture-sessions` captures independently timestamped sanitized media files when the decoded source clock regresses, clock kind changes, resolution changes or pixel format changes. The boundary frame stays in memory and becomes the first frame of the next file. No raw staging file, frame interpolation or synthetic cross-session clock is introduced. Every new file receives a fresh privacy-association state, source session ID, per-frame ledger, lossless pixel verification and committed privacy manifest.

Clock-kind transitions still obey source policy: missing timestamps in local evidence are rejected, while permitted network sources can use explicitly labeled observed arrival time. Higher-bit-depth input remains rejected rather than silently converted.

The [application integration](DEPIN_BP04_MULTISESSION_RECORDING.md) now enables this interface through opt-in `capture_sessions=true` recording requests and the workspace control. The default single-file path remains the control. Session-aware identities, storage, graph/query filters and checkpoints preserve separate timelines rather than adding estimated cross-session offsets.

## Files and recovery boundary

The output is a fresh, owned directory containing `capture.json`, `session-0000.mkv` and subsequent session files with their privacy manifests and frame ledgers. The journal publishes each committed session using fsync plus atomic rename and directory sync. Entries contain unique session IDs, lineage reasons, frame counts and media/ledger/manifest hashes. Limits are 256 sessions and the existing bounded capture duration. A duplicate output directory is rejected rather than overwritten.

Failure retains only the explicitly committed sanitized prefix and marks the journal failed where possible. A hard process kill leaves the last published journal in `capturing` state; unfinished files remain unadvertised. `verify_capture_sessions()` requires a complete journal by default. Explicit `allow_partial=True` can verify a committed prefix for future retained-evidence recovery; it does not reopen a live source or label the prefix complete. The verifier rejects altered provenance, unexpected paths and substituted symlinks. It is a local owned-directory verifier, not authentication against malicious replacement of an entire root journal.

Each file has its own relative playback timeline and retains exact source PTS/timebase in its ledger. `observed_session_span` sums observed within-session spans for the duration budget; it is neither elapsed wall time nor certified coverage. No duration is invented between sessions. Live capture also has an observed wall-clock deadline; an already decoded rollover frame is finished even when prior-session verification reaches that deadline. MPEG-JPEG sources remain explicitly on the decoder-arrival clock when source timestamps are unavailable.

## Validation

- `tools/check_capture_sessions.py`: five checks pass in `output/depin-implementation/bp04-clock-rollover-contracts-verified.log`.
- A real concatenated MPEG-TS reset preserves all 20 decoded frames, including the boundary frame, with exact source PTS/timebase and independent 0–360ms playback timelines. Both session derivatives decode and verify. The old single-file path rejects that reset without publishing a file.
- Resolution-change replay preserves every frame actually returned by the decoder. The particular fixture has 20 encoded frames but the pinned decoder returns 19 around the change; the test compares source-decoder output and retained ledgers exactly instead of claiming recovery of the unavailable frame. The original failed 20-frame expectation is retained in `bp04-clock-rollover-contracts.log`.
- Detector exception and session-capacity failure retain a verified committed prefix and deny normal complete-capture verification. A process-kill check pauses in second-session inference, kills that owned process and verifies the first session from the published journal; the second session has no published commit.
- Changed manifest metadata, path traversal, symlink substitution and output-directory reuse are rejected.
- Actual cached privacy models through the CLI: `output/depin-implementation/bp04-clock-rollover-real/verification.json`. Two genuine clock-reset sessions contain ten frames each; actual face detection runs on every frame, solid pixel replacement verifies and both H.264 derivatives pass. This is a positive integration fixture, not independent privacy recall.
- Existing real privacy suite: all 8 tests passed in `bp04-clock-rollover-privacy-real.log`, including face/plate positives and local multipart MJPEG. The shared decoder refactor preserves the default single-file interface.
- Existing application replay passed in `bp04-rollover-singlefile-regression/`: real sanitized recording, graph/analytics parity, tamper admission, pending-publication restart and revocation cleanup. This validates the unchanged single-file application behavior after the shared decoder refactor; it does not validate multi-session application integration.

Example using cached models:

```sh
.venv-retrieval/bin/python tools/privacy_media.py --capture-sessions \
  --source PATH_TO_SOURCE --output output/depin-implementation/new-session-capture \
  --seconds 30
.venv-retrieval/bin/python tools/check_capture_sessions.py
```

Do not substitute the directory for a source file in existing APIs. Use the recording API's `capture_sessions` option to select the integrated worker/materialization path.

## Integration status and remaining requirements

Canonical identities, scoped file materialization, tracker namespaces, query/graph/analytics, checkpoint sets, media APIs and the workspace are integrated and tested in [multi-session recording](DEPIN_BP04_MULTISESSION_RECORDING.md). Pre-checkpoint API cancellation and retained-prefix recovery pass the [dedicated fault replay](DEPIN_BP04_COMMITTED_PREFIX_RECOVERY.md). [Explicit bounded reconnect](DEPIN_BP04_TRANSPORT_RECONNECT.md) passes clean EOF and abrupt HTTP disconnects without joining independent source clocks.

Actual semantic/VLM temporal negative cases, justified UTC uncertainty, broader codec/RTSP failures, profile rollback, independent generic/privacy acceptance and target hardware qualification remain open.

BP04 and the complete implementation plan remain active and incomplete. No quality, throughput or memory improvement is claimed from this capture primitive.
