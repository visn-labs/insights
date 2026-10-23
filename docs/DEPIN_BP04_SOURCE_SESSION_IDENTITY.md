# BP04: canonical source-session identity

## Implemented — 22 October 2026

Canonical camera results, events and tracker entities now carry an optional `source_session_id`. New sanitized recordings take it from the committed capture manifest. Existing physical camera IDs remain unchanged. Legacy records omit the field and retain their old event/entity identifiers and projection serialization; absent session identity is not retroactively inferred as verified continuity.

New event UUID inputs contain the physical camera, source session and source-relative interval. New entity UUID inputs contain camera, source session and local tracker ID. Therefore identical timestamps and reused tracker IDs in different declared sessions do not merge. Each camera/source-session pair has its own projection entry, interval union and graph. The canonical validator checks event/camera/entity session agreement and binds declared session identity to the saved privacy capture provenance.

Index admission, evidence access, retained checkpoint recovery and strict query admission validate that binding. Strict queries hash each source once and compare its verified typed provenance against every selected event; they no longer skip provenance comparison on subsequent events sharing that path. This preserves the existing source-hash cache behavior while checking every event's declared identity.

## Retrieval, interpretation and statistics

- `POST /api/v1/memory-query` accepts `source_session_ids` in addition to physical camera filters. Filtering happens before lexical/semantic retrieval and graph expansion. Up to 256 nonempty session identifiers of at most 128 bytes are accepted.
- Lexical retrieval reuses the shared eligibility function rather than maintaining a separate scope implementation. Graph observations and interpretation grounding require matching source-session identity. Interpretation and summary candidate metadata include the source session.
- `POST /api/v1/memory-jobs/{id}/analytics` accepts `source_session_ids`. Its rows retain physical `camera_id` and include `source_session_id` where known. Per-session interval coverage is computed independently. The legacy `indexed_camera_ms` field sums camera-session milliseconds; it is not one elapsed timeline or verified continuous coverage.
- Projection consumers use a structured camera/session key. Physical camera filters can select multiple sessions, while explicit session filters select the intended rows. Legacy projection keys are preserved.

The worker/model algorithms, confidence thresholds and feature precision are unchanged. This is evidence identity and scope work, not a tracking-accuracy improvement or a vector-model comparison.

## Evidence

- Rust suite: 74 passed, 0 failed, 2 ignored, in `output/depin-implementation/bp04-session-identity-tests-final.log`.
- A new contract constructs two source sessions with identical physical camera IDs, intervals and local tracker IDs. Actual graph construction produces distinct event/entity UUIDs, separate projections and 44,000 camera-session ms for two independent 22,000ms timelines. Session-scoped lexical retrieval, graph expansion and analytics select only the requested session. Swapped event provenance and entity session IDs are rejected. Legacy UUID construction is unchanged.
- Real API replay: `output/depin-implementation/bp04-source-session-identity-real/`. Actual sanitized capture and YOLO tracker memory carry a session ID through event, graph, analytics, projection and query. Positive/negative session filters, corruption fallback, hard restart, changed media/timing admission and revocation cleanup passed. The default recording in this replay still has one source-clock session; its long-spacing association boundaries are a separate concept.
- Capture rollover contracts: 5 passed in `bp04-session-identity-capture.log`. Tracker continuity contracts: 3 passed in `bp04-session-identity-continuity.log`.
- The initial new unit fixture had too few observations to satisfy the unchanged tracker-confirmation policy. Its next version requested a lexical word absent from its fixture descriptions. The final fixture supplies three observations and queries its actual evidence text; no production confirmation threshold or lexical matching rule was weakened. Earlier failing logs remain available.

## Remaining integration

The subsequent [multi-session recording increment](DEPIN_BP04_MULTISESSION_RECORDING.md) connects the producer, storage/checkpoint set, API and workspace. The following paragraph records the boundary of the identity-only increment; consult the newer document for current validation and remaining gates.

This implements the identity prerequisite for [clock rollover capture](DEPIN_BP04_CLOCK_ROLLOVER.md). The application recording path still materializes one source file per physical camera. It does not yet ingest the capture directory containing multiple clock-reset files. Equal-clock multi-session behavior is verified by the canonical/graph/query contract, not claimed as a completed multi-file API capture.

Next work must materialize each committed file into its own persisted source-session storage scope, extend per-camera checkpoint/recovery to a session set, and preserve physical camera totals separately from session totals. Then integrate the capture directory with indexing, playback, revocation, UI selection and interpretation negative cases. Do not join source-relative timestamps using estimated offsets. Transport reconnect, profile/version rollback, generic independent quality acceptance and target hardware qualification remain open. BP04 and the full implementation plan remain incomplete.
