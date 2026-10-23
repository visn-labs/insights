# BP04: committed-prefix recovery and query consistency

## Validated candidate — 23 October 2026

`capture_extent` now accompanies session camera results, events, metadata projections, analytics and interpretation context. `finished` means the bounded capture journal completed; it does not certify uninterrupted footage or the entire requested duration. `retained_prefix` means recovery used only previously committed sessions from an interrupted capture. Absent legacy values remain unknown.

Extent consistency is checked across camera/event scope and anchored in the metadata fingerprint. A rehashed projection cannot silently change a retained prefix into a finished capture. The workspace displays **Recovered footage only** and explains that missing footage remains unavailable.

## Actual fault replay

`tools/check_multisession_workflow.py --prefix-recovery` uses real privacy inference and YOLO. Its owned pause fixture stops after the first sanitized source-clock session commits, before a camera checkpoint exists. The API cancels the job, verifies worker exit, restarts and resumes from retained evidence.

Results: `output/depin-implementation/bp04-prefix-recovery-real/results.json` and the adjacent `.log`.

- One retained session, 3,920 indexed camera-session milliseconds, two tracker histories, four detector observations and six observation links.
- Both camera/event and statistics extent are `retained_prefix`.
- Zero new source requests during recovery; retained media SHA-256 remains `eed6a1342fd9d2a0afdc7d65ecfb30e6a9f12dc818a3394c243906030b6873c6`.
- Recovered playback advances with four decoded frames and no media error. The explicit partial-capture notice and layouts pass at 320/768/1024/1440 px.
- Revocation denies recovered media/query/statistics access while preserving retained bytes under the existing retention policy.

Browser evidence: `output/playwright/bp04-recovery/prefix-recovery-browser.log` and `prefix-recovery-*.png`. Rust: `bp04-prefix-extent-tests-verified.log`, 77 passed, 2 ignored. Offline application build passed.

## In-flight search consistency

A real delayed response exposed a race: changing source A to source B while A was searching allowed A's answer to appear under B's controls. The UI now snapshots the complete normalized submitted request and compares it with current inputs before rendering. Permission/revocation revision checks remain separate. Editing search inputs also clears previously displayed evidence.

The browser held and forwarded an actual backend response. Before the fix stale evidence appeared; after the fix it remained hidden with an explicit retry message. A fresh B query then returned the correct single B match. Structured proof: `output/playwright/bp04-recovery/query-scope-race-verification.json`. This test reused retained evidence and made no new capture or model calls.

Evidence-card captions and metadata now use existing theme colors: measured contrast 6.41:1, headings 11.30:1. Summary text remains 5.35:1. Proof: `multisession-card-theme-final.log` and phone screenshot in the same browser evidence directory.

## Limits

These are development integration regressions, not independent tracking/privacy accuracy qualification. Semantic/VLM services were absent. Transport reconnect, UTC uncertainty, rollback/load, authenticated contribution accounting and generic held-out acceptance remain open. BP04 and the full implementation plan remain active.
