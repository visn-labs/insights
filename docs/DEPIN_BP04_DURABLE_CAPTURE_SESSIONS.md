# BP04: durable capture association sessions

## Implemented — 22 October 2026

Sanitized recordings now persist an ordered `capture.association_sessions` list. Each entry carries a session ID, first/last consumed frame index, first/last observed timestamp and start reason. IDs match the frame ledger's association session. All frames and their PTS remain intact; ranges describe observations rather than continuous coverage. The initial session and each observed timestamp spacing over 1,000 ms are represented. A recording exceeding 256 such sessions fails without publishing a partial commit.

Rust retains this contract in `CaptureTimeline` and validates ordered, exhaustive frame ranges, timestamp ordering, boundary spacing, unique sequential IDs, boundary reasons and agreement with observed frame totals. The canonical event evidence stores the typed capture contract. Index preparation, query admission, playback and retained checkpoint recovery now compare the typed source provenance, including capture metadata and ledger hash, rather than only the encoded video hash. A changed timing manifest is rejected even when encoded video bytes are unchanged. Unmodeled manifest fields are not claimed to be covered by this typed comparison.

New metadata projections include `capture_timeline` when durable session ranges exist, under their existing canonical input fingerprint and output hash. Absent legacy ranges remain absent: their optional serialized field is omitted to preserve old projection fingerprints. Empty/missing ranges mean unavailable session knowledge, never certified continuity. New files with ranges retain them through restart and explicit metadata rebuild.

The graph validator rejects an entity whose observations cross two recorded association sessions or fall outside recorded session observation ranges. Canonical graph admission validates the same contract before selecting either a projection or a fallback. This supplements detector-side tracker reset and namespaced IDs; it prevents a malformed saved graph from silently restoring cross-boundary identity.

## Validation

- Rust: 73 passed, 0 failed, 2 ignored in `output/depin-implementation/bp04-sessions-tests-validated.log`. New tests cover invalid ranges, repeated IDs, invalid spacing, legacy serialization and rejection of a saved entity spanning sessions.
- Actual cached privacy models: all 8 privacy tests passed in `bp04-sessions-privacy.log`, including local MJPEG and face/plate positives. The gap test checks persisted ranges and ledger IDs while retaining exact encoded timestamps.
- First real API replay: `bp04-durable-sessions-workflow/` passed actual sanitized recording, graph/statistics parity, corruption fallback, pending-publication hard restart, source mutation, changed typed capture metadata and revocation cleanup.
- The final graph-guard replay passed under `bp04-durable-sessions-guard/`, including equality of the projected capture timeline and canonical capture metadata after restart. Its terminal log/results cover the later binary with the graph-session guard enabled.

The earlier negative graph test initially reached the outside-session check because its synthetic range omitted a fixture observation at 1,500 ms. The fixture was corrected to include all intended within-session observations; the test now specifically proves cross-session rejection. The failed run is retained in `bp04-sessions-tests-guard.log`.

## Contributor timeline UI — 22 October 2026

The workspace now displays an observation timeline per camera using the canonical capture contract. Native keyboard-operable details expose each session's exact source-relative range, consumed frame count and session ID. Timing boundaries show observed spacing and explain that variable frame rate and missing observations cannot be distinguished. Decoder arrival clocks and unavailable legacy session data receive distinct explanations. Revoked recordings remove the timeline and disable statistics. The logo and landing animation are unchanged.

Actual Chrome verification on the saved gap recording passed keyboard expansion and 320/768/1024/1440 px layouts without horizontal overflow. The visible ranges were 0.000–0.960s (25 frames) and 3.000–4.360s (35 frames), separated by 2.040s between observations. Browser renderer checks also exercised unavailable legacy data and arrival-clock copy; those two are renderer checks, not separate real capture runs. The real recording, source mutation, restart and revocation harness is `output/depin-implementation/bp04-session-ui-built/`. Screenshots and browser measurements are under `output/playwright/bp04-recovery/session-timeline-*`.

The first browser attempt encountered a stale embedded UI because assets are compiled through `src/ui.rs`. After rebuilding the Rust binary, a fresh owned run tested the actual new UI. The semantic worker was deliberately absent (expected visual-index 503); forced service restarts also generated handled network failures against the old test port. These are not claims of semantic-service or outage UI acceptance.

## Remaining plan requirements

These are durable **association** sessions within a retained recording, not a finished source-session transport system. Automatic multi-file capture rollover after source clock reset, reconnect and codec-change ledgers, justified UTC mapping and unavailable coverage representation still need work. The browser now presents observed session boundaries, but cannot reconstruct transport information the capture layer never recorded. Strict clock regression still fails rather than inventing source times. Legacy OpenCV nominal timestamp fallback remains explicitly weaker than source PTS.

The conservative gap boundary can split a real object's history. Tracking accuracy and alternative thresholds require independent acceptance data. No model-quality, throughput, memory or general privacy improvement is claimed from these contract tests. The full BP04 and BP00–BP12 acceptance gates remain open.
