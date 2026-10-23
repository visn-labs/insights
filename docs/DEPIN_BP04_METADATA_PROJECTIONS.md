# BP04: durable evidence metadata and statistics

## Implementation status — 22 October 2026

The local candidate now connects saved evidence to graph queries and exact interval statistics. This is a BP04 increment, not completion of the full research plan or an algorithm quality promotion.

`src/metadata_projection.rs` defines `evidence-metadata-v1`. Recording completion commits the projection intent with the canonical result. Entity-memory rebuilds create a fresh intent. The replay loop publishes bounded, atomic JSON documents under `memory/projections/<job>/<revision>.json`, independently of the optional semantic worker. Pending publication survives restart; invalid evidence blocks publication and storage failures retry with backoff.

Readers verify the version, revision, canonical input fingerprint, source provenance and output SHA-256. Graph queries consume a matching projection and expose `metadata_provenance`. Missing, changed or corrupt projections fall back explicitly to canonical graph evidence without changing the existing ranking algorithm. The existing graph links connect **observation IDs**, with validated entity membership, forward timestamps and exact time gaps; they do not represent learned causality or physical identity.

## API and workspace

- `POST /api/v1/memory-jobs/{id}/metadata/rebuild`: queue a rebuild from retained evidence.
- `POST /api/v1/memory-jobs/{id}/analytics`: accept typed camera/time filters; reject unknown fields and unsupported physical-footfall metrics.
- Workspace controls show statistics, rebuild metadata and distinguish unavailable tracker counts from a measured zero.

Time filters use camera-relative, half-open `[start_ms,end_ms)` intervals. Coverage unions overlapping intervals within each camera, then sums camera-milliseconds. This is indexed coverage, not proof of continuous capture. Tracker histories, detector observations and ordered observation links describe saved evidence, not verified people or real-world identity. Missing tracker evidence returns `null`.

Revocation immediately denies access and cancels projection intent. Local cleanup removes all owned projection revisions without following a substituted job-directory symlink. Failures remain visible and retryable; retries never restore access. Local and remote cleanup have separate states. Canonical media retention is unchanged: revocation is not physical erasure.

## Validation evidence

| Check | Result / retained evidence |
| --- | --- |
| Rust suite | 71 passed, 0 failed, 2 ignored; `output/depin-implementation/bp04-metadata-tests.log` |
| Real privacy + YOLO replay | `output/depin-implementation/bp04-metadata-real/` |
| Real replay counts | 2 intervals, 7,960 ms coverage, 10 tracker histories, 29 detector observations, 39 ordered observation links; independent reference agrees |
| Graph consumer | Verified projection used; corruption produces explicit canonical fallback with the same answer |
| Recovery | Publication blocked by storage fault, hard restart with pending intent, repair and automatic publication |
| Admission | Changed sanitized source rejected; invalid time/camera/metric inputs rejected |
| Revocation | Symlink cleanup blocked safely; restart preserves denial; retry removes owned revisions while preserving the sentinel and retained-media hashes |
| Existing consumers | `bp04-metadata-revocation-regression/` and `bp04-metadata-visual-regression/` passed |
| Browser | Real statistics fit 320/768/1440 px; blocked cleanup denies statistics and supports keyboard retry; final keyboard rebuild preserves unavailable tracker counts at 320 px with no horizontal overflow; artifacts under `output/playwright/bp04-recovery/` |

The harness is `tools/check_metadata_projection.py`; use a fresh output directory for every run:

```sh
.venv-retrieval/bin/python -u tools/check_metadata_projection.py \
  --output output/depin-implementation/metadata-new-run
```

Add `--source PATH --seconds 8 --real-tracker` for the cached real detector, or `--ui-seconds 300` to hold the ready/revoked phases for browser checks. Phase files identify the current owned service URL. Reports retain source/model hashes and request results. A first harness run incorrectly assumed error responses were always JSON; it now handles Axum's plain-text validation errors. Unit testing also caught and corrected an initial event-ID versus observation-ID assumption.

## Alternatives, costs and remaining gates

The existing local manifest/outbox avoids another service and preserves the control query behavior. Each document is capped at 128 MiB; verification can read metadata for cameras outside the selected subset. Old private revisions remain until revocation. No latency, memory or throughput gain is claimed. Measure these costs before choosing per-camera partitions or SQLite; ClickHouse/H3 remain separate, unqualified alternatives.

Remaining BP04 work includes reconnect/session boundaries, clock discontinuities, retention quotas, version migration, profile rollback, load and deployment recovery. Multi-tenant authorization remains open. Generic independent labels, privacy/utility acceptance, tracking quality and Linux/NVIDIA qualification remain separate gates. The eight-second traffic replay is development integration evidence, not a generic accuracy benchmark. This projection reuses tracker memory; it does not implement a learned event/intent graph or establish research-paper performance claims.
