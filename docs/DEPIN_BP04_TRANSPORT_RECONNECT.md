# BP04: bounded live-source reconnect

## Implemented candidate — 23 October 2026

Sanitized multi-session capture supports an explicit live-source reconnect budget. The default remains zero: finite HTTP files stop at EOF without being replayed. Local files and encoded stdin cannot enable reconnect.

Enable the deployment candidate with the existing runner configuration:

```sh
VISN_MEDIA_POLICY=sanitized-v1
VISN_MEMORY_RUNNER_ARGS='tools/event_memory_runner.py --reconnect-attempts 2'
```

Recording requests on this configured path must set `capture_sessions=true`. Budgets are integers from zero through eight. Direct sanitizer CLI use also supports `--capture-sessions --reconnect-attempts 2`. Retained-only resume never opens a source, even when the configured runner has a retry policy.

## Failure and timeline rules

- Explicit live mode can reopen EOF, connection failures/timeouts, HTTP server/429 failures and FFmpeg transport EIO. Other decoder errors, authorization failures, sanitizer/model failures and output failures remain fatal.
- On a handled decoder disconnect, emitted frames finish sanitization and verification before their session commits. Undecoded packets/buffered frames are unavailable; no synthetic recovery frames are created.
- Every reopened connection starts a new sanitized file, source-session UUID and tracker namespace. The prior session ends with `transport_disconnected`; the next starts with `transport_reconnected`. Reconnect lineage and the bounded retry count are checked when consuming the journal.
- Retries use bounded exponential backoff, starting at 250 ms and capped at two seconds. A retry is not admitted after the capture deadline. Open/read timeouts are bounded by ten seconds and the remaining budget at connection admission; an in-flight read or sanitization/verification can finish after that deadline. This is not a hard real-time completion guarantee.
- Exhausting retries before the bounded capture completes fails the recording and leaves verified committed sessions for explicit retained-prefix recovery. It is not reported as successful uninterrupted footage.
- No cross-session UTC mapping, elapsed gap duration, camera authenticity or physical identity is inferred. `finished` means the capture journal completed, not continuous coverage.

The existing frame sanitizer, model thresholds, source PTS ledgers, pixel verification and no-raw-staging policy remain in use. There is no new transport library or alternate detector.

## Validation

| Check | Result / evidence |
| --- | --- |
| Owned HTTP and actual codecs | Five checks pass in `output/depin-implementation/bp04-reconnect-http-contracts-deadline-verified.log`: clean EOF, truncated HTTP body, exact emitted-frame preservation, deterministic deadline admission, retry exhaustion/default EOF, server failure, fatal authorization/corrupt video/model errors, and journal tamper rejection |
| Existing capture control | Five reset/geometry/fault/tamper/kill checks pass in `bp04-reconnect-capture-control-final.log` |
| Actual inference / API, clean EOF | `bp04-transport-reconnect-real/`: real privacy and YOLO, two sessions, scoped graph/metadata retrieval, statistics and media, restart, checkpoint recovery without an observer/new source request, and revocation |
| Actual inference / API, abrupt body | `bp04-transport-reconnect-abrupt-real/`: same workflow passes after the server closes before the declared body length; two source requests, independent sessions with 22 and 48 decoded frames, two tracker histories in each session |

The abrupt replay reports separate indexed intervals of 3,760 and 8,240 ms. They are camera-session interval totals, not one continuous 12-second timeline. Fixture footage comes from owned retained development evidence, preserving within-segment source spacing. Repetition in the second transport fixture tests bounded ingestion, not real-world behavior or tracking accuracy.

Reproduce in fresh output directories:

```sh
.venv-retrieval/bin/python tools/check_capture_reconnect.py
.venv-retrieval/bin/python tools/check_capture_sessions.py
.venv-retrieval/bin/python tools/check_multisession_workflow.py \
  --source PATH_TO_RETAINED_DEVELOPMENT_VIDEO \
  --output output/depin-implementation/new-reconnect-replay \
  --transport-reconnect
```

## Debugging record and remaining gates

The first truncated-body test failed because the policy omitted FFmpeg EIO. After adding this transport-only case, its encoded-frame-count assertion failed: the decoder withheld its final buffered frame at EIO. The corrected test independently decodes the same failing transport and requires exact PTS/timebase equality for every emitted frame. It does not relax an accuracy threshold or invent the unavailable tail. Failed logs remain as `bp04-reconnect-abrupt-http.log` and `bp04-reconnect-http-contracts-final.log`.

RTSP-specific failure modes, broader codecs, long-running/overload behavior, profile rollback, actual semantic/VLM multi-session acceptance, authorization and independent generic/privacy/hardware qualification remain open. No measured performance or accuracy improvement is claimed. BP04 and the full implementation plan remain active.
