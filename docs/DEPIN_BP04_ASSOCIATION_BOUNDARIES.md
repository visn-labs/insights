# BP04: association boundaries across reconnects and observed gaps

## Implemented — 22 October 2026

Both `tools/yolo26_runner.py` and `tools/yolo26_worker.py` previously retained tracker state across live-source reconnects. They now reset the tracker and appearance-sampling state at a reconnect or observed decoded-frame spacing greater than 1,000 ms. The spacing check runs before sampling, so a deliberately low detector FPS alone does not create boundaries. A backwards observed clock fails instead of silently retiming an observation.

`TrackingContinuity` in the existing runner is shared by both paths. The persistent worker carries its epoch on each queued frame: replacing a pending live frame cannot lose the boundary. IDs after a boundary use `session-<epoch>:<local-id>`, preventing tracker-reset ID reuse from merging histories in the event engine, saved graph or metadata statistics. Epoch zero preserves existing control identifiers within the existing recording/camera namespace. The command adapter explicitly invokes the installed tracker reset API; changing `persist` alone would not reset an already registered Ultralytics callback.

The privacy sanitizer also starts fresh sensitive-region association state at the same long-spacing boundary. It retains all decoded frames and their original relative PTS, runs the same current-frame detectors and writes an `association_session_id` into each frame-ledger entry. No model, inference threshold or pixel replacement rule changes. Source clock regression/missing clocks remain subject to existing strict rejection; transport arrival is still not presented as source capture time.

New saved graphs with session-prefixed IDs include a note explaining the boundary. Legacy graph notes and results remain unchanged; old unprefixed IDs do not certify continuity. Statistics count separate observed histories, not physical identities.

## Evidence

- `tools/check_tracking_continuity.py`: three passing contracts for gap thresholds, backwards clocks and an actual capture-loop reconnect carrying a new packet epoch. Synthetic clocks test contracts, not tracking quality.
- `tools/check_tracking_continuity_replay.py`: real cached YOLO26 replay through both detector paths on 60 retained development frames with an injected 2,000 ms PTS gap. Both sides contain detections; track ID sets are disjoint and all post-gap tracks have the new namespace. Source and model hashes are retained in `output/depin-implementation/bp04-continuity-real/results.json`. The input master is unchanged. The two adapters are tested independently; their detection totals are not claimed equivalent.
- Full strict recording/API/graph/projection replay passed in `output/depin-implementation/bp04-continuity-workflow/`. An independent check over the saved canonical graph found two histories before the gap and six after, with none spanning both sides. Verified graph use, analytics parity, corruption fallback, hard restart, source mutation denial and local revocation cleanup passed.
- Rust suite: 71 passed, 0 failed, 2 ignored, in `bp04-continuity-rust-fixed.log`. The first run caught an unconditional diagnostic changing legacy graph serialization. Diagnostics now appear only for actual new session-prefixed histories; the earlier failed log is retained.
- Privacy fixture suite: 8 tests executed, 2 optional real-model tests skipped in `bp04-continuity-privacy.log`; the gap case verifies ledger-session rollover and unchanged encoded PTS.
- Full cached-model privacy suite: all 8 passed, including actual face/plate positives and local multipart MJPEG, in `bp04-continuity-privacy-real-fixed.log`. The first invocation supplied `1` instead of the fixture-directory path required by `VISN_PRIVACY_REAL_MODELS`; its missing-fixture failure is retained in `bp04-continuity-privacy-real.log`. The memory-runner regression passed its one test. Format, Python compilation and diff checks passed.

Reproduce the detector and graph checks with fresh output directories:

```sh
.venv-retrieval/bin/python tools/check_tracking_continuity.py
.venv-retrieval/bin/python tools/check_tracking_continuity_replay.py \
  --source PATH_TO_RETAINED_VIDEO --output output/depin-implementation/continuity-new
.venv-retrieval/bin/python tools/check_metadata_projection.py \
  --source output/depin-implementation/continuity-new/gap.mkv --seconds 6 \
  --real-tracker --output output/depin-implementation/continuity-workflow-new
.venv-retrieval/bin/python tools/check_tracking_continuity.py \
  --workflow-report output/depin-implementation/continuity-workflow-new/results.json
```

## Remaining requirements

This is an association-safety increment. A long observed gap may be legitimate variable-frame-rate input, not missing footage. Conservative separation can fragment a real object's history; generic tracking-quality acceptance and threshold alternatives remain necessary. No accuracy or speed improvement is claimed.

Formal persisted source sessions, automatic clock-reset capture rollover, reliable reconnect/codec discontinuity metadata, mapped UTC uncertainty and explicit unavailable coverage still need integration. The existing OpenCV nominal-clock fallback and live receipt clock cannot reconstruct source timestamps. Retained legacy graphs need explicit rebuild to gain new boundaries. Browser gap/session presentation, profile/version rollback, multi-tenant authorization and target hardware acceptance remain open. BP04 and the full implementation plan remain incomplete.
