# Experiment 3: persistent entity and event memory

## What is implemented

An opt-in, FOLIO-inspired layer links separated detector observations to retained video evidence. It reuses YOLO26, ByteTrack, the Rust rule engine, and existing atomic JSON manifests. It does **not** reproduce FOLIO's trained memory writer or claim validated cross-recording identity.

Experiment 1 retrieval and experiment 2 visual interpretation remain available. Experiment 3 answers with a deterministic, evidence-linked tracker history; unreliable VLM action descriptions are not promoted into identity links.

### Use it

Open Video memory in the workspace:

1. For a new recording, enable **Experiment 3 · Build linked entity memory** before recording.
2. For an existing completed recording, click **Build entity memory for this recording**. This reads the retained source rather than reconnecting to the original camera.
3. Enable **Experiment 3 · Follow linked observations**, or select an **Entity track**. Ask, for example, “What observations occurred before and after for this vehicle?”
4. Expand the track history. Each observation links to its source clip at a relative playback offset. Supporting clips can extend beyond the original retrieval shortlist while staying inside the requested filters.
5. Use **Search all recordings** to query multiple saved memories. Similar objects and reused numeric tracker IDs remain separate candidates. Select an entity to disambiguate.

The local validation app runs at `http://127.0.0.1:8195/#/workspace`. The normal launcher remains `sh scripts/run_memory.sh`.

## Construction and persistence

- Run the existing detector at 5 FPS on the retained source. The normal detector/rule pipeline is reused without publishing duplicate analytics to the event sink.
- Retain only the first and last actual detection per track per indexed interval, plus first/last overall observations and rule events. This bounds anchors to two per track per interval instead of retaining every decoded detection. Ordinary detector runs do not collect these anchors.
- Use a stable entity UUID derived from **recording ID + camera ID + local tracker ID**. A class, caption, color, or resemblance is never an identity join key.
- Store timestamped observations with stable IDs, evidence event IDs, bounding boxes/detector scores when available, and explicit uncertainty. An observation is retained only if its timestamp belongs to an indexed interval. Intervals are half-open: a shared boundary belongs to the following clip.
- Store `same_tracker_order` edges between adjacent observations of the same entity. `gap_ms` is a gap between retained observations, not a claim about visibility or physical continuity.
- Record source SHA256, detector model name, detector FPS, and schema version. Model names refer to configured weights; changing a weight file requires a rebuild and its bytes are not independently fingerprinted here.
- Save the graph in `MemoryCameraResult.long_memory` in the existing manifest. Backfills persist the complete replacement before exposing it in memory. A failed rebuild preserves the old graph. New-recording detector failure preserves ordinary indexed evidence and adds a quality note.
- Existing manifests without this field load normally. Queries identify missing coverage explicitly. No graph database or new dependency is required.

The previous experiment's source snapshot is retained locally at `data/baselines/experiment2-2026-10-16/`, with a SHA256 manifest.

## Query behavior and uncertainty

Retrieval ranks remain unchanged. The experiment separately selects structured histories through an explicit entity ID, common detector class terms, or retrieved evidence seeds. It follows observations only within the same tracker entity and reapplies recording, camera, cluster, and timestamp filters to every citation. Links with missing endpoints or mismatched owners are excluded.

Supported class aliases currently cover person/people, car, truck, bus, motorcycle, bicycle, dog, cat, and vehicle, including plurals. Other questions can seed histories through ordinary retrieval. This is not a general natural-language graph query engine: color, action, causal, and identity-return predicates are not automatically verified.

Statuses:

- `tracker_linked`: one selected/candidate history has separated observation timestamps. This is **not** verified physical identity.
- `ambiguous_entities`: multiple candidate tracks remain; the answer does not arbitrarily choose or merge them.
- `insufficient_evidence`: no matching history or insufficient temporal observations.

At most 12 candidate histories are shown, with an explicit truncation note. Supporting evidence includes all citations for those histories. Source-relative timestamps are not synchronized across cameras or recordings. First/last observed is not synonymous with arriving/leaving; detector errors, fragmentation, and ID switches remain possible. No continuous visibility is inferred across an occlusion or sample gap.

The UI chooses either experiment 2 question interpretation or experiment 3 histories. If an API caller requests both, interpretation results remain in their separate field, while the top-level summary uses the structured history. Experiment 3 takes precedence over legacy textual VLM synthesis.

## Validation results — 16 October 2026

### Real retained highway footage

Source: the recording previously captured from the supplied highway MJPEG URL. These checks reused saved video; they do not establish current live-camera availability.

- Built **35 tracker histories and 221 observation links** on the roughly 30-second recording.
- Track 15, classified as a car, supplied **11 observations across four indexed clips**, even with retrieval limited to one result.
- Fetched every supporting clip successfully and checked all citation timestamps against the source intervals.
- Manually reviewed a contact sheet with detection boxes across the four intervals. The sequence appeared visually consistent, but this is not a ground-truth identity or ID-switch benchmark.
- Verified ambiguity handling for multiple vehicles and no expansion outside recording, camera, cluster, or time filters.
- Verified entity/observation IDs survived application restart unchanged.
- Rebuilt memory through the browser; tested entity selection, timestamped clip navigation, desktop layout and 390px mobile layout without browser errors or horizontal overflow. The final browser rerun also verified that successful structured-memory results show a linked-history heading even when lexical retrieval returns no candidates.
- Recorded a separate 16-second local HTTP replay through the standard indexing workflow: **two indexed clips and 21 tracker histories**.
- Injected a broken detector command: existing-memory rebuild failed without replacing the old result; a new recording still produced two usable clips, with no graph and an explicit failure note.

### Deterministic checks

All 26 Rust tests and six Python checks passed. Real-model browser regressions for experiment 1 retrieval and experiment 2 interpretation also passed; experiment 2's documented accuracy limitations remain unchanged.

The Rust suite includes eight experiment-specific tests, covering similar objects with overlapping times, reused IDs in different cameras/recordings, expansion beyond a shortlist, persistence, all filters, missing evidence, forged cross-entity links, unknown entities, old schema versions, plural class lookup, stable IDs, and bounded anchors. The anchor check verifies ordinary track/rule outputs remain identical with collection enabled.

**Interpretation:** the evidence-linking and isolation contracts pass these tests. That establishes a useful persistent tracker memory, not a general solution to long-term visual identity or action reasoning. The next accuracy evaluation needs independently labeled occlusions, re-entry, night scenes, and similar-object crossings; unsupported identity questions should continue to abstain.

## Reproduce

```bash
cargo test --offline
cargo fmt --check
git diff --check

# Local app on 8195 with the saved highway validation directory:
.venv-retrieval/bin/python tools/check_long_memory.py --build
# Restart the app, then verify the saved identities:
.venv-retrieval/bin/python tools/check_long_memory.py --restart-check

REBUILD_MEMORY=1 NODE_PATH=/private/tmp/visn-ui-qa/node_modules \
  node tools/check_long_memory_browser.cjs
.venv-retrieval/bin/python tools/check_long_memory_index.py
```

The failure test expects a separate local app on 8194 started with `VISN_PERSISTENT_DETECTOR=false` and `VISN_DETECTOR_ARGS=tools/nonexistent-experiment3-test.py`, sharing the validation data directory:

```bash
.venv-retrieval/bin/python tools/check_long_memory_index.py \
  --url http://127.0.0.1:8194 --expect-detector-failure
```

`check_long_memory.py` accepts `--url` and `--job`. The browser test accepts `VISN_TEST_URL` and `VISN_TEST_JOB`. The replay test uses the retained validation source, intentionally avoiding the live camera.

Local artifacts: `output/experiment3/` contains query/index/fallback JSON, stable-ID snapshots and a visual track contact sheet. `output/playwright/experiment3/` contains screenshots and browser query JSON. These directories are ignored by Git.

## Current scale limits

Memories are durable JSON manifests loaded into the existing in-process store; querying scans them, and recording polling includes the graph. This fits the current local prototype. Large multi-day archives need paginated summaries and a database index before claiming production scale. Rebuilds are serialized, detector execution uses the existing process gate, and observations without indexed clip coverage are omitted explicitly.

## Timestamp-cadence correction after three-stream evaluation

The [live-stream evaluation](LIVE_STREAM_EVALUATION_2026_10_16.md) found nominal-FPS stride undersampling on variable-rate recordings. Both adapters now sample presentation timestamps. New memory is `entity-memory-v2-pts`, with versioned entity UUIDs; old graphs require rebuilding. Historical counts above describe the previous run and are not unique-object accuracy measurements.
