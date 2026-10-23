> Historical observation-desk validation. Superseded by the two-scene street/camera/reel journey; see [RECORDING_JOURNEY.md](RECORDING_JOURNEY.md).

# Observation desk frontend validation

This document supersedes the earlier folio/flat-panel validation. Those screenshots, ratings and performance measurements do not describe the current implementation.

## Current experience

The existing Victorian street leads into a continuous local Three.js observation desk. Native scroll moves through five stations. Camera housing, film, six pivoted iris leaves, a sparse evidence network and the final mark are procedural geometry. A ray-marched smooth-union surface makes the green core's droplets physically merge and stretch. The core responds to interaction energy and node displacement; the network contracts into the final mark as the reader advances.

The project uses the supplied Kibori, Kage and Sylva HTML as reference material, plus the supplied fluid-design and frontend-design guidance. It does not redistribute their complete pages or present their workshop/temple content as Visn. Manim is useful for offline explanatory video, but is not a browser interaction engine and was not added as a dependency.

## Validation performed

- `cargo build --offline`: passed.
- `cargo test --offline`: 30 passed, 2 controlled benchmarks ignored.
- `node tools/check_walking.mjs`: 12,978 poses passed contact, planted-foot, start/stop and clock checks. Existing street geometry and walking solver are retained.
- Contributor browser suite: 36 checks passed; no unhandled application errors. Eleven axe surfaces had no WCAG A/AA violations in the final rerun.
- Observation desk suite: 24 checks passed, including node deformation, liquid gesture energy, network settling, five-station camera travel, keyboard rotation/reset, retained release velocity, spring settling, mechanical iris control, reduced-motion controls, continuous-render suspension, hidden-route suspension, WebGL context-loss fallback and all five phone chapters without horizontal overflow passed.
- JavaScript syntax and `git diff --check`: passed.

Commands (Playwright and axe installed outside the repository):

```sh
NODE_PATH=/private/tmp/visn-ui-qa/node_modules node tools/check_contributor_flow.cjs
NODE_PATH=/private/tmp/visn-ui-qa/node_modules node tools/check_scene_flow.cjs
```

Preview uses `127.0.0.1:8220` and isolated data under `output/frontend-liquid-baseline/data`. Rust embeds index/intro/journey, so rebuild and restart it after those files change. New desk modules and CSS use the existing asset route.

## Evidence

Browser screenshots and reports live under `output/playwright/atelier-*`. These generated artifacts are ignored by Git. Independent screenshot reviews identified phone cropping, competing adjacent objects, repetitive framing, illegible miniature plaques and dark materials. Revisions added phone-specific camera framing, active-exhibit isolation, labelled navigation, bounded aperture leaves, reflection lighting and removal of duplicate miniature labels. Follow-up refinements protect the phone navigation area, vary the film/network stations, and attach the selected example timestamp to a highlighted evidence node.

A focused 390×844 headless Chrome frame sample of the fluid station recorded 109 frames in approximately 1.8 seconds, median and p95 intervals of 16.7 ms. This was an unthrottled synthetic local run with software WebGL; it is not physical-phone or full-page performance acceptance. No production-domain SEO or deployment verification is claimed. A CDP touch swipe over the canvas advanced native page scroll by 129 px. Sampled phone focus checks cover coverage, both interval ranges and the boundary control; focused controls are kept below the sticky scene.

## Product limits

The landing scene is illustrative. It does not run video anonymisation or create model detections. Drafts store metadata in this browser, not video. Upload, acceptance and rewards remain planned. The existing analysis workspace remains available. No backend experiment or model workflow was modified by this redesign.

## Independent visual assessment

The latest independently scored capture was 7.46/10 overall (design 7.4, usability 7.3, creativity 7.8, content 7.7). Its remaining findings concerned repeated plinth composition, phone navigation clearance, synthetic surface finish and weak identification of the selected evidence. Subsequent changes removed the film/network plinths, protected phone navigation, improved shadow coverage and reflections, and connected a timestamped card to a highlighted node. These final changes have functional/browser evidence but have not received a new independent score. This report does not claim award-ready visual acceptance.
