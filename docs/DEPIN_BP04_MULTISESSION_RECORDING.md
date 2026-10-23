# BP04: clock-reset recording through the application

## Implemented candidate — 22 October 2026

The recording API and workspace now support opt-in multi-session sanitized capture. Source clock resets produce separate retained media timelines, canonical events, tracker histories and projections. There is no synthetic offset joining the clocks. Physical camera IDs remain unchanged, and physical camera totals remain distinct from the number of source sessions.

The control path remains unchanged when `capture_sessions` is omitted or false. Session capture requires `VISN_MEDIA_POLICY=sanitized-v1`; other profiles reject it. This is an integration candidate under the existing privacy qualification limits, not a promoted general privacy or tracking profile.

## Data flow

1. The existing observer worker calls the verified capture-session interface and validates its session journal before consumption. New capture requires a complete journal; retained-only recovery can inspect a verified committed prefix.
2. Each sanitized session is linked into its own UUID-scoped camera directory, preserving source media, privacy manifest and frame ledger. Existing targets must match the verified source bytes; symlink substitutions are rejected.
3. The observer analyzes each source-relative timeline separately. The framed worker response contains an ordered, bounded session-result set with typed storage UUIDs. Legacy one-result responses remain supported.
4. Rust materializes each result under its session directory and validates the committed source-session identity. Events and entities use session-aware IDs. The camera checkpoint atomically anchors the entire result set.
5. Tracker worker requests also use session-scoped namespaces. Retrieval filters and interpretation grounding preserve those scopes. Summary candidate metadata includes recording/session IDs and explicitly disallows invented cross-session ordering or identity.
6. Restart rehydrates each member's paths. Checkpoint reuse verifies all retained artifacts and never reopens the original camera URL. Revocation denies all session events and removes local metadata projections while retaining media under the existing policy.

The API's legacy `source_duration_ms` remains the maximum individual source-session span; it is not elapsed capture time across reset clocks. The workspace labels it **Longest session** for this mode. Analytics union intervals within each camera/session and report summed camera-session milliseconds with explicit semantics.

## API / workspace

```json
{
  "name": "Authorized source-clock recording",
  "capture_sessions": true,
  "cameras": [{"camera_id": "junction", "live_url": "http://permitted-camera/video"}],
  "monitor_duration_secs": 15,
  "observer_fps": 2,
  "long_memory_enabled": true,
  "vlm_enabled": false
}
```

Submit to `POST /api/v1/memory-jobs`. The workspace exposes **Keep separate sessions on clock reset**, enabled only for sanitized capture. Search has a native source-session selector; requests carry `source_session_ids`. Statistics identify their source rows. Entity choices carry source context and are filtered by the selected session. The existing theme and logo are unchanged.

## Validation evidence

| Check | Evidence |
| --- | --- |
| Rust | 76 passed, 0 failed, 2 ignored; `output/depin-implementation/bp04-multisession-tests-verified.log` |
| Real clock-reset API | `bp04-multisession-real-v2/`: two independent sessions with equal relative intervals, distinct events/entities, one physical camera, scoped graph queries, analytics and media access |
| Real restart/recovery | Session media hashes/identities survive restart. An interrupted post-checkpoint state recovers the whole set with an intentionally unavailable observer and no additional source request |
| Revocation | Both sessions' media endpoints return 404; statistics/query scope deny access; retained source hashes remain unchanged |
| Legacy recovery | `bp04-multisession-legacy-recovery/`: 68 HTTP plus crash/ownership/child checks passed |
| Capture / observer contracts | 5 capture tests and 1 observer regression passed in `bp04-multisession-capture-contracts.log` and `bp04-multisession-observer-contracts.log` |
| Actual UI submission | Keyboard-enabled capture control sent `capture_sessions=true`; queued job selection and form rendering passed after correcting a null-report bug |
| Scoped UI query | Native keyboard type-ahead selected one source; request/results stayed within it, with one matching clip and two source-scoped tracker histories |
| Entity picker | Final real-data check showed only the selected session's two entities; explicit entity search returned exactly the selected entity; all four widths passed |
| Browser media | Both session clips advanced beyond 0.2 seconds with four decoded frames each, no media error and 3.92s duration |
| Responsive UI | No overflow at 320/768/1024/1440 px; source-first labels remain readable on mobile; statistics refresh preserves the query result |
| UI revocation | Native confirmation passed; timelines/answers disappeared and source/statistics controls were cleared/disabled; retained source hashes still matched |
| Answer text contrast | Final rendered summary and metadata text use the existing theme token and measure 5.35:1 against the cream card; checked using retained owned evidence without new capture/model inference |

Browser evidence is under `output/playwright/bp04-recovery/multisession-*`, including `multisession-browser-verification.json`. The full owned replay with corrected queued UI is `bp04-multisession-browser-fixed/`. Actual privacy and YOLO inference ran; semantic/VLM services were deliberately absent. Local metadata retrieval, tracker histories and media were verified; this does not qualify semantic indexing or VLM interpretation across sessions.

The final filtered-entity UI run is `bp04-multisession-entity-ui-final/`, with browser measurements in `multisession-entity-picker-final.log`. An earlier short UI hold expired and the controlled server restarted during the check; that connection failure is retained separately in `multisession-entity-picker.log`. The successful retry used the new owned service and a longer hold, rather than treating the expired endpoint as a product failure.

Reproduce with fresh output directories:

```sh
cargo test --offline
cargo build --offline
.venv-retrieval/bin/python tools/check_multisession_workflow.py \
  --source PATH_TO_RETAINED_DEVELOPMENT_VIDEO \
  --output output/depin-implementation/new-multisession-replay
```

The fixture builder requires timestamps exactly representable on its MPEG2 test clock. It preserves their original spacing rather than changing the model cadence.

## Failures found and corrected

- The first fixture encoded retained variable-rate frames at fixed consecutive ticks, speeding motion up and preventing confirmed tracks. The corrected fixture preserves its source timestamps; no detector or confirmation threshold was relaxed. The failed run remains under `bp04-multisession-real/`.
- Browser creation found an existing statistics guard comparing two absent revisions as equal, then dereferencing a null report while the job was queued. The guard now requires an actual report. Corrected UI submission verifies selection with no form error.
- Final mobile review found legacy dark-theme answer colors left on cream cards. Summary and metadata text now use the existing theme token; rendered contrast is 5.35:1.
- Headless native arrow-key selection was not reliable in the tested platform path. Native type-ahead selection was explicitly verified; no custom keyboard handler replaces native select behavior.

## 23 October follow-up

[Committed-prefix recovery and query race validation](DEPIN_BP04_COMMITTED_PREFIX_RECOVERY.md) passed. Rust regressions now pass 77 tests, 2 ignored. Evidence-card caption/metadata contrast measures 6.41:1 and heading contrast 11.30:1 on the existing cream panel. A delayed actual search response is rejected when the current request inputs differ, and a fresh source-scoped search succeeds.

## Remaining acceptance gates

Justified UTC mapping, broader clock/codec transitions, profile/version rollback and overload/long-duration behavior remain open. [Explicit bounded transport reconnect](DEPIN_BP04_TRANSPORT_RECONNECT.md) now passes owned EOF and abrupt HTTP API replays; RTSP-specific qualification remains open. The capture primitive tests committed-prefix survival after process kill, and the application has a retained-prefix path, but API-level cancellation/resume before a camera checkpoint now passes the dedicated real multi-session fault replay; see [committed-prefix recovery](DEPIN_BP04_COMMITTED_PREFIX_RECOVERY.md). Prefix recovery explicitly discloses incomplete original capture and made zero new source requests.

Semantic worker delivery/cleanup and VLM temporal negative cases need multi-session replay with their actual qualified services. Generic independent labels, privacy recall/utility, tracking precision and Linux/NVIDIA qualification remain separate requirements. No speed, memory, general accuracy, source authenticity or reward-settlement improvement is claimed. BP04 and the full implementation plan remain active.
