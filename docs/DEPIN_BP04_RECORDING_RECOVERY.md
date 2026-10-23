# BP04 durable recording and retained-evidence recovery

Date: 21 October 2026. Status: integrated local candidate; BP04 remains incomplete.

## Implemented behavior

- A local POSIX file lease prevents two Rust services from owning the same data directory. PID text is diagnostic only. Kernel descriptor ownership determines release; there is no stale-PID unlock operation.
- Recording manifests use private temporary files, file sync, rename and parent-directory sync. Queued state is committed before submission acknowledgement, and terminal state is committed before completion is advertised.
- Optional `idempotency_key` accepts 16–128 ASCII alphanumeric, underscore or hyphen characters. Reusing a key with the same normalized request/media policy returns the original recording; a different payload returns HTTP 409. Active admission is bounded; excess new jobs return HTTP 429.
- `POST /api/v1/memory-jobs/{id}/cancel` persists cancellation and aborts the owned task. Worker process groups include child FFmpeg processes. Workers retain the owner lease and watch a parent-death channel so Rust-parent death does not leave the tested capture worker running indefinitely.
- `POST /api/v1/memory-jobs/{id}/resume` is retained-only recovery for the strict sanitized profile. It never reconnects to the original live source. At least one verified committed source must exist. No committed source returns an explicit error; a new live interval requires a new recording.
- Restart durably marks interrupted queued/running work failed. Recovery is explicit. Completed camera checkpoints have anchored hashes; unchanged checkpoints can be reused without an observation worker. Changed checkpoint metadata or evidence is rejected.
- Sanitized capture metadata records a session ID, observed frames, termination, observed last-frame time, transport wall-clock interval and observed timestamp-spacing jumps. Transport times are not certified capture times. Large spacing is not automatically labeled packet loss: variable frame rate and missing observations cannot always be distinguished. Continuity remains uncertified.
- The workspace exposes stop/resume controls and live status text. Submission retries after an uncertain acknowledgement preserve an operation key. Persisted retry state contains a digest and key, not camera URLs or credentials. The narrow-screen layout permits wrapping instead of hiding overflow.

## Persistence and concurrency boundary

This is single-node, single-writer manifest durability, not a cross-store transaction protocol. The lease is inherited only by owned workers. The process launch callback performs descriptor operations rather than allocation or mutex acquisition after fork. See the official [Rust process documentation](https://doc.rust-lang.org/std/os/unix/process/trait.CommandExt.html#method.pre_exec) and [flock semantics](https://man7.org/linux/man-pages/man2/flock.2.html).

Cancellation acknowledgement proves that cancellation was durably requested; it does not universally prove that every process has already been reaped at that instant. The fault replay separately waits for owned worker and descendant termination. Arbitrary kernel-level uninterruptible IO and Linux/NVIDIA deployment are not qualified by the local test.

Historical source credentials are redacted in durable jobs. Recovery therefore cannot recreate a lost live interval and must not fabricate one. Failed/partial camera outcomes stay explicit. Retained evidence still undergoes strict artifact verification before use.

## Validation evidence

| Check | Result | Artifact |
| --- | --- | --- |
| Rust regression suite | 67 passed, 0 failed, 2 ignored | `output/depin-implementation/bp04-durable-tests.log` |
| Real privacy suite | 8 passed, including VFR timestamp-gap reporting | `output/depin-implementation/bp04-privacy-tests.log` |
| Recovery fault replay | 63 HTTP checks plus ownership, parent crash, worker/descendant exit, hash and source-access assertions passed | `output/depin-implementation/bp04-recovery-final/results.json` and adjacent server logs |
| Media integration after worker lifecycle changes | 25 HTTP checks passed: real-model upload, encoded playback, recording, keyword query, changed-artifact rejection and restoration | `output/depin-implementation/bp04-media-regression/replay/results.json` |
| Browser recovery | Stop succeeded; native Enter on focused resume returned expected 400 when no source was committed; full status text remained visible | `output/playwright/bp04-recovery/mobile-recovery-controls.png`, `desktop-recovery-controls.png`, CLI snapshots/logs |
| Responsive widths | Document width equaled viewport at 320, 768, 1024 and 1440 pixels in the recovery-error state | `output/playwright/bp04-recovery/verification.json` |
| Client lost-ack retry | Earlier actual request-forward/response-abort probe observed two submissions sharing one key and one created recording | Prior browser probe; this does not prove all network failure modes |
| Static checks | Rust formatting, JS syntax and diff whitespace checks passed | Commands run after the final layout edit |

The recovery replay captures real sanitized fixture evidence before killing the owned Rust service. After restart, resume preserves the source hash and source-request count. It also checks checkpoint reuse with an unavailable observation worker, changed-checkpoint rejection, and cancellation before capture. The media replay uses actual privacy models and the regular worker, not the recovery pause fixture.

Browser checks used an isolated fixture stopped before source capture. Expected console HTTP failures include an unavailable optional visual-index service (503 with backoff) and retained-only resume without evidence (400). These are not a claim of semantic-model acceptance. One full-page screenshot exhibited capture/sticky rendering artifacts; viewport screenshots are the visual evidence for the controls. An earlier browser measurement timed out while requesting a focus attribute after resizing; the measurements were repeated successfully and focus/keyboard activation were verified separately.

## Reproduction

Build the current service and use fresh isolated output directories:

```sh
cargo build --offline
.venv-retrieval/bin/python tools/check_recording_recovery.py --output output/depin-implementation/recovery-new-run
```

Privacy models must already be prepared using the BP03 setup. This replay owns its loopback source/server and cleans up its processes; do not point destructive fault probes at a shared service.

For interactive browser checks:

```sh
.venv-retrieval/bin/python tools/run_recovery_ui_probe.py --output output/depin-implementation/ui-new-run --seconds 600
```

Read the emitted actual URL. The capture fixture pauses for 120 seconds, so exercise stop promptly. Touch `OUTPUT/done` to request cleanup, then confirm the controller exits. The fixture is test-only and is not a production capture mode.

The full media replay remains `tools/check_privacy_workflow.py`; it requires a separate owned normal service, the public fixture HTTP source, actual cached privacy models and fresh data/output directories. The configured recovery pause runner must be replaced with `tools/event_memory_runner.py` for this replay.

## Remaining BP04 work and next implementation

1. Define one canonical durable commit containing evidence revision and projection intent. Evaluate a narrow transactional outbox/store rather than claiming several manifest writes are atomic together.
2. Populate actual event/vector/entity-graph/analytics projections from committed evidence, with idempotent keys, retry state, version watermarks and restart replay. Existing query-time remote indexing is not this guarantee.
3. Add revocation tombstones, immediate access denial and replayable cache/index/graph/aggregate cleanup. Verify interrupted cleanup and stale-cache admission.
4. Extend live-session reconnect and source-clock-discontinuity handling beyond observed spacing metadata. Preserve quiet coverage and distinguish source failure from observed end-of-stream.
5. Qualify independent generic privacy/utility data, concurrent load and Linux/NVIDIA deployment. Existing traffic development sources and synthetic fixtures cannot supply that acceptance.

No research algorithm, physical-authenticity claim, reward settlement or general privacy guarantee is promoted by this increment. The complete plan remains active.
