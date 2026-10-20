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
- Local Chrome phone-viewport/DPR-2 timing: 271 frames per 4.5-second macro sample, median/p95 16.7 ms and maximum 16.8 ms for both scenes. This is synthetic local-browser timing, not physical-phone validation.
- JavaScript syntax and whitespace checks passed.

The walkthrough is `output/playwright/internal-camera/street-camera-reel.webm`. Full browser reports and rendered captures are in the same output directory. No backend video-processing, retrieval or reward implementation was modified.
