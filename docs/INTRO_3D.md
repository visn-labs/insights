# Living street landing page

## Art direction

A coffee-and-cream, cel-shaded nineteenth-century street junction seen from the southeast corner, looking along its 45-degree diagonal toward the centre. The two roads remain aligned to the world X and Z axes and meet at 90 degrees, with four square pavement corners visible in the desktop frame. Four street approaches remain open around timber and plaster shops. Pitched, tiled roofs, cornices, shutters, glazed storefronts, striped awnings, clocks, chimney smoke, market produce, a bench, trees, and gas lamps establish the period. Forty townspeople wear tailored coats, hats, aprons, and dresses, with facial details and jointed knees and elbows. Six walkers follow each corner pavement, eight use crossing routes, and eight local characters shop, read, converse, or sit. Two horse-drawn carriages have articulated horses, reins, drivers, and rotating spoked wheels; a porter pushes a loaded handcart, and ten pigeons peck and flutter. Recessed frames, iron balconies, masonry joints, individual roof joints, downpipes, scalloped awnings, leafy canopies, barrels, produce crates, and cafe tables refine the close-up artifacts. Modern CCTV cameras form the deliberate contemporary layer.

The landing focuses on footage contributors and leads into onboarding and contributor home. The analysis workspace is a secondary destination. Scene telemetry is explicitly labelled **DEMO**; it does not claim a real camera connection or successful backend inference.

## Files and rendering

- `static/index.html`: scrollable contributor story, four-step onboarding, contributor home, existing analysis workspace.
- `static/styles.css`: scoped landing palette, responsive composition, existing dashboard styles.
- `static/intro.js`: hash routing, scene loading, story controls, pause/replay, reduced motion and visibility.
- `static/assets/contributor.js` and `contributor.css`: local contribution drafts, recording preview, segment validation, home and shared visual theme.
- `static/assets/walking.js`: route sampling, speed ramps, world-space foot targets and anatomical leg solving.
- `static/engine.js`: exported `TownScene`, actual Three.js geometry and animated scene. It has no independent auto-starting renderer.
- `static/vendor/three.*.js`: pinned local Three.js modules. No CDN, build system, or new npm runtime dependency.
- `src/ui.rs` and `src/api.rs`: existing embedded asset routes already serve the frontend modules and styles, including the three new `/assets/` resources. **Rebuild/restart the Rust service after changing embedded frontend files.**

## Adult character proportions

All pedestrians, drivers, and the porter use the same adult rig: a 1.835 m reference body with a 0.25 m head (7.34 heads tall), varied to approximately 1.76–1.90 m across the crowd. Hats add height separately. The shoulders, waist, hips, thighs, calves, and neck are shaped individually; a continuous tapered head avoids overlapping facial seams. Fingers, elbows, knees, and ankles articulate independently. Rough shared materials soften faces and clothing while the architecture retains its illustrated finish.

`fitHands` fits forearms to newspapers, parasol handles, reins, and the handcart. `seatPerson` places the pelvis on its seat and the soles on the pavement or carriage footboard. During walking, pelvis and spine pivots drive body weight transfer; inverse kinematics fits each leg to its world-space foot target.

## Contributor flow

- `/` or `/#/`: illustrated hero, three contributor story chapters, sharing/reward FAQs, and onboarding invitations.
- `/#/contribute`: camera context and permission → local recording or labelled example → segment selection and review → explicit local-draft save.
- `/#/home`: real draft count, saved metadata and next steps. Rewards/submissions are explicitly unavailable, without fabricated balances or approval states.
- `/#/workspace`: existing video analysis tools with the warm visual theme.

Draft metadata uses the `visn.contributions.v1` local-storage key. No contributor account, file upload, eligibility assessment or payout is performed. Video previews use a revocable browser object URL; videos are not stored. A selected segment is previewed without altering the original recording. Submission and payment integration require actual programme rules and backend services.

## Walking and story timing

The street clock and 18-second narrative clock are separate. Story buttons change explanatory chapters without teleporting pedestrians. **See how it works** scrolls into the contributor story.

A planted foot keeps a fixed world-space landing point for 62% of its stride. The heel rolls down, the sole bears weight, and the toe pushes off. The swing foot lifts and travels to the next landing. Its cycle advances with distance travelled, so changing speed changes cadence rather than causing skating. Stride size respects character scale. Leg solving preserves thigh and shin lengths, while the pelvis lowers enough to keep both foot targets reachable. The torso counter-rotates and arms swing opposite the legs; prop-holding hands retain their grip targets.

Pavement, kerb, crossing and cobble heights determine foot support. Routes round the pavement corners; crossing pedestrians accelerate, stop with feet grounded and step around when turning back. The porter uses the same walking solver and holds the moving cart handles. This is procedural animation, not a full crowd/collision simulation or motion-capture asset.

## Close-up walking review

Run `python3 -m http.server 8181 --bind 127.0.0.1` from the project root, then open `http://127.0.0.1:8181/tools/walking-preview.html`. It uses the production model and walking solver. Select side/front/three-quarter views, a pace, a corner or start/stop action, slow motion, or a precise time. Ground markers distinguish support from swing.

## Performance and accessibility

- Static architecture is batched by material; cobblestones use instanced geometry.
- Moving figures and vehicles share instanced geometry by material; their rig transforms update instance matrices each frame. Reused sphere, cylinder, lathe, torus and rounded-box geometries, a capped pixel ratio, and one shadow-casting light limit GPU work.
- Rendering stops when dismissed, paused, the hero scrolls offscreen, or the tab is hidden. Replay resumes the existing scene.
- `prefers-reduced-motion` starts with a static tableau and suppresses pointer parallax. Users may explicitly play motion.
- Story navigation remains available in the static state.
- Inactive pages are hidden; the analysis workspace is inert until opened. Route changes and onboarding steps move keyboard focus to the active content.
- Narrow layouts retain the scene and all primary controls. Short screens can scroll.
- WebGL failure retains contributor onboarding and workspace navigation. No low-quality substitute image is claimed as a rendered scene.

## Verification

Run `cargo check --offline`, `node --check static/engine.js`, `node --check static/intro.js`, and `git diff --check`. Start `cargo run` and inspect `http://127.0.0.1:8080/`.

Browser acceptance checks include animation/pause, story controls, missing permissions/files, invalid segment times, explicit local-storage consent, saved-draft persistence, a real browser-generated WebM preview, analysis workspace navigation, reduced motion, no-WebGL onboarding, and 320/768/1024/1440px layouts. Captures and results are stored in ignored `output/playwright/`. These do not establish performance on every mobile GPU.

Run `node tools/check_walking.mjs` for numerical checks on the real production model: support-foot drift, ground penetration, swing clearance, bone bending, turns, start/stop and independent story timing. It does not require a GPU or npm packages.

The browser fixture additionally projects all four pavement corners through the real camera, checks the diagonal viewpoint, samples geometry transforms through 90 seconds of street activity, checks adult proportions, hand-to-prop alignment and seated sole contact, and reports renderer draw calls and triangle counts.

### Repeat the browser acceptance test

With the Rust server running and Playwright available:

```sh
VISN_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright node tools/check_landing.cjs
```

Alternatively install Playwright in your normal tooling environment and run `node tools/check_landing.cjs`. `VISN_PREVIEW_URL` overrides the default local URL; `VISN_BROWSER_PATH` selects a browser executable. On macOS the script defaults to installed Google Chrome. On other platforms it uses Playwright's installed Chromium. The script checks the assets served by Rust directly and writes desktop/mobile screenshots and `verification.json` under `output/playwright/`.
