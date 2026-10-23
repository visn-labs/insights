# Camera and recording journey

## Scope

The landing story now contains exactly the two user-defined scenes. The former five-station observation desk is not mounted. Its source remains dormant, including the frozen 3D mark. No later story phase has been invented or implemented.

The logo is unchanged. Verify with:

```sh
node tools/check_logo_freeze.cjs
```

## Implementation

- `static/engine.js` retains the Victorian street, traffic, people and walking solver. It mounts the recording attachment on the existing camera diagonally opposite CAM 01.
- `static/assets/camera-recording.js` builds the detailed enclosure, optics, an enclosed cassette with reel flanges, wound film, sprocket perforations, closed belt, mist and packets. It controls the continuous close-up/reel camera path in the same world.
- `static/assets/journey.js` maps native scroll to the two views. Its original logo construction and pose functions are unchanged.
- `static/assets/camera-story.css` positions first text lower-right and second text lower-left, with phone-specific framing and mist clearance.
- `static/intro.js` keeps the same renderer active throughout the complete street/camera/reel region, and suspends it on other routes or offscreen.

The second view uses local reel up orientation plus a 30-degree camera roll. It reports the projected film-axis angle for browser verification. The upper run and lower return are sampled from one stadium loop; their opposite directions and identical speeds follow from its geometry, rather than independent animations.

At two-second intervals the renderer captures a 512×384 view of the street from the selected CCTV lens. Eight bounded frame canvases populate the film texture. The texture is 4096×512. This is illustrative local rendering, not video upload or a remote recording service. Other cameras' diagnostics fade away as the macro shot begins.

Foreground/background render layers preserve the camera's actual position while keeping the adjacent lamp behind the mist during the macro view. This does not move street buildings or the lamp. Source capture excludes the recording model to prevent recursive film imagery.

The cassette uses 0.26 of its former transport scale and is aligned lengthwise inside the hollow housing. Geometry bounds are checked against the interior envelope. The exterior remains opaque in scene one; its shell fades during the second cutaway. A separate foreground key light and locally fitted shadow map illuminate the small mechanism without changing the street lighting.

`static/assets/street-activity.js` links reading hands to the turning page, the vendor hand to moving produce, and conversation gestures to alternating speaking cycles. Standing leg IK holds both soles fixed during subtle weight shifts. Clothing, eye blinks, neck proportions and hand-carried bags were refined in the production figures.

## Static and lifecycle paths

`camera-close.jpg`, `reel-close.jpg` and their `-phone` variants are captures of the actual 3D scenes. They are used for initial reduced-motion and graphics-failure visits. Existing scene instances retain their full static composition when motion is disabled. Route and visibility changes stop continuous rendering. Capture targets, reflection maps and fog resources are released through `TownScene.dispose()`.

## Validation commands

```sh
cargo build --offline
cargo test --offline
node tools/check_logo_freeze.cjs
node tools/check_recording_transport.mjs
node tools/check_walking.mjs
node tools/check_street_activity.mjs
node tools/check_motion_continuity.mjs
# Needs Playwright video recorder (ffmpeg) in PLAYWRIGHT_BROWSERS_PATH:
NODE_PATH=/private/tmp/visn-ui-qa/node_modules PLAYWRIGHT_BROWSERS_PATH=/private/tmp/visn-ui-qa/browsers node tools/check_motion_playback.cjs
NODE_PATH=/private/tmp/visn-ui-qa/node_modules node tools/check_scene_flow.cjs
NODE_PATH=/private/tmp/visn-ui-qa/node_modules node tools/check_contributor_flow.cjs
```

Browser suites run against the rebuilt isolated preview at `127.0.0.1:8220`. Index, engine, intro and journey are embedded by Rust; rebuild and restart after changing them. New assets use the existing local asset route. Browser evidence lives under `output/playwright/internal-camera`. The former observation-desk validation and scores do not describe this two-scene implementation.

## Verified results

- Rust build passed; 30 tests passed, 2 controlled benchmarks ignored.
- Logo freeze passed for the SVG asset, SVG factory/pose and existing 3D mark.
- 2,000 film-loop/seam samples passed; top and lower runs have opposite directions and equal physical speed.
- 1,002 reading/vendor poses passed hand-target and standing foot-contact checks.
- Existing walking solver passed 12,978 contact, planted-foot, start/stop and story-clock samples.
- 23 scene browser checks passed, including the measured projected 30-degree film axis, reversible zoom, continuous reels, motion suspension, device layouts and fallback paths.
- 30 contributor browser checks passed, with no unhandled application errors and no reported axe violations across the tested routes and form states.
- A simulated WebGL context loss showed the still; context restoration resumed reel animation.
- JavaScript syntax and whitespace checks passed.

The walkthrough is `output/playwright/internal-camera/street-camera-reel.webm`. Full browser reports and rendered captures are in the same output directory. No backend video-processing, retrieval or reward implementation was modified.

## Camera remodel and temporal validation — 21 October 2026

The camera uses a continuous rounded hollow extrusion, sealed end-cap seams, captive screws, thin rain-hood returns, optical rings and a cast tilt yoke. The cutaway includes sensor carrier, a flexible ribbon with individual conductors, routed board traces, capacitors, processor/heatsink, rear connectors, standoffs and cassette drive motors. The cassette remains fully enclosed at its existing scale.

Walking now resolves each rounded path segment by arc length. The pelvis reach constraint uses a smooth upper bound instead of abruptly switching its limiting leg. Swing lift has zero acceleration at its endpoints; torso bounce and arm swing are restrained. Conversation nods use an unbroken clock across cycle boundaries; gestures use the free hand when carrying a bag or parasol. Activity envelopes use fifth-order easing.

A new 120 Hz production-model test measures joint velocities, head acceleration, distance uniformity and reader/vendor/conversation cycle seams. In the same corner trajectory, the hard pelvis constraint produced a peak head acceleration of 99.90 m/s²; the smooth constraint measures 15.75 m/s². Planted-foot and prop-contact tests continue to pass. These are deterministic motion diagnostics, not a claim of human motion-capture fidelity.

`tools/check_motion_playback.cjs` records 28 seconds of the actual TownScene: a wide street view followed by a close-up of the conversation area. It measures consecutive rendered frames and both partners’ head/hand movement. The recording is `output/playwright/camera-remodel/street-continuous.webm`; its timing/error report is alongside it. This supplements screenshots, which cannot validate jitter. The imported production scene uses a diagnostic camera for the second half.

The ten standalone SVG assets in `static/svgs/` were redrawn using curves, shaded surfaces, tailored character details, sash windows, masonry, coach spokes, lantern framing and layered foliage. Their legacy inline library is synchronized. Reproduce with `python3 tools/refine_scene_vectors.py`. These are legacy reusable vectors; the active street uses Three.js geometry. The rendered library sheet is `output/playwright/camera-remodel/vector-library.png`. The frozen logo is untouched.

Current scene and contributor browser reports are under `output/playwright/camera-remodel/scene` and `workflow`; all 23 and 30 checks passed respectively. Fallback stills and the connected walkthrough were refreshed.

Final isolated playback sample: 1679 consecutive intervals; median 16.6 ms, p95 17.4 ms, maximum 18.4 ms; 0 intervals over 33.4 ms. No application errors. These are local desktop Chrome measurements with video recording enabled, not physical-device guarantees.

## Shared journey and logo introduction — 21 October 2026

The two story chapters now share a pure progress score with the renderer. Native scrolling remains authoritative; pointer inspection uses a critically damped response. Keyboard chapter navigation focuses its destination immediately. See `docs/REFERENCE_MOTION_STUDY.md` for the source review, changes and verification commands.

A separate 5.6-second logo introduction reverses the LightSpeed reference toward one centre, assembles six aperture leaves from outside inward, then expands green nodes and pathways outward. The logo design/factory hashes are unchanged. Skip, Escape, replay, reduced-motion and no-WebGL paths are covered by `tools/check_logo_reveal.cjs`. The street pauses while that dialog is open. The continuous recording is `output/playwright/logo-convergence/logo-convergence.webm`.
