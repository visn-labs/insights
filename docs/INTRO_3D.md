# Living street landing page

## Art direction

A coffee-and-cream, cel-shaded nineteenth-century street junction seen from the southeast corner, looking along its 45-degree diagonal toward the centre. The two roads remain aligned to the world X and Z axes and meet at 90 degrees, with four square pavement corners visible in the desktop frame. Four street approaches remain open around timber and plaster shops. Pitched, tiled roofs, cornices, shutters, glazed storefronts, striped awnings, clocks, chimney smoke, market produce, a bench, trees, and gas lamps establish the period. Forty townspeople wear tailored coats, hats, aprons, and dresses, with facial details and jointed knees and elbows. Six walkers follow each corner pavement, eight use crossing routes, and eight local characters shop, read, converse, or sit. Two horse-drawn carriages have articulated horses, reins, drivers, and rotating spoked wheels; a porter pushes a loaded handcart, and ten pigeons peck and flutter. Recessed frames, iron balconies, masonry joints, individual roof joints, downpipes, scalloped awnings, leafy canopies, barrels, produce crates, and cafe tables refine the close-up artifacts. Modern CCTV cameras form the deliberate contemporary layer.

The landing copy explains the product and leads directly to the existing workspace. Scene telemetry is explicitly labelled **DEMO**; it does not claim a real camera connection or successful backend inference.

## Files and rendering

- `static/index.html`: landing, accessible controls, existing workspace, module-load fallback.
- `static/styles.css`: scoped landing palette, responsive composition, existing dashboard styles.
- `static/intro.js`: scene loading, story controls, pause/replay, reduced motion, visibility and workspace lifecycle.
- `static/engine.js`: exported `TownScene`, actual Three.js geometry and animated scene. It has no independent auto-starting renderer.
- `static/vendor/three.*.js`: pinned local Three.js modules. No CDN, build system, or new npm runtime dependency.
- `src/ui.rs` and `src/api.rs`: existing embedded asset routes already serve both modules and styles. **Rebuild/restart the Rust service after changing embedded frontend files.**

## Adult character proportions

All pedestrians, drivers, and the porter use the same adult rig: a 1.835 m reference body with a 0.25 m head (7.34 heads tall), varied to approximately 1.76–1.90 m across the crowd. Hats add height separately. The shoulders, waist, hips, thighs, calves, and neck are shaped individually; a continuous tapered head avoids overlapping facial seams. Fingers, elbows, knees, and ankles articulate independently. Rough shared materials soften faces and clothing while the architecture retains its illustrated finish.

`fitHands` fits forearms to newspapers, parasol handles, reins, and the handcart. `seatPerson` places the pelvis on its seat and the soles on the pavement or carriage footboard. These are proportion and pose adjustments; the existing gait timing is unchanged in this anatomy pass.

## Signal journey

The 18-second loop has three six-second chapters. Chapter buttons seek to their matching part of the loop; **Follow a signal** restarts it.

1. **See the moment:** four heads turn toward animated people. Frustums, ground rings, and tracking boxes show capture.
2. **Connect the dots:** packets move from those same cameras along cubic curves. Paths become more prominent.
3. **Find the story:** the streams converge above the roofs; packets shrink and fade toward the sky endpoint.

Each frame derives position from elapsed seconds. Long frame gaps are bounded. One animation loop owns the entire scene. The old sprite renderer, scroll runway, and fake backend-log interstitial are no longer mounted.

## Performance and accessibility

- Static architecture is batched by material; cobblestones use instanced geometry.
- Moving figures and vehicles share instanced geometry by material; their rig transforms update instance matrices each frame. Reused sphere, cylinder, lathe, torus and rounded-box geometries, a capped pixel ratio, and one shadow-casting light limit GPU work.
- Rendering stops when dismissed, paused, or the tab is hidden. Replay resumes the existing scene.
- `prefers-reduced-motion` starts with a static tableau and suppresses pointer parallax. Users may explicitly play motion.
- Story navigation remains available in the static state.
- The underlying workspace is inert while the landing is visible. Entry moves focus into the workspace; replay focuses the entry button.
- Narrow layouts retain the scene and all primary controls. Short screens can scroll.
- WebGL failure and module-load failure retain workspace access. No low-quality substitute image is claimed as a rendered scene.

## Verification

Run `cargo check --offline`, `node --check static/engine.js`, `node --check static/intro.js`, and `git diff --check`. Start `cargo run` and inspect `http://127.0.0.1:8080/`.

Browser acceptance checks: visible changing frames; all three chapter controls; restart; pause/resume; workspace entry; existing workspace navigation; replay; Escape; reduced motion; 390px layout without horizontal overflow; WebGL-unavailable entry; and no uncaught runtime errors. Local review captures and results are stored in ignored `output/playwright/` when run. Browser results do not establish performance on every mobile GPU.

The browser fixture additionally projects all four pavement corners through the real camera, checks the diagonal viewpoint, samples geometry transforms through 90 seconds of street activity, checks adult proportions, hand-to-prop alignment and seated sole contact, and reports renderer draw calls and triangle counts.

### Repeat the browser acceptance test

With the Rust server running and Playwright available:

```sh
VISN_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright node tools/check_landing.cjs
```

Alternatively install Playwright in your normal tooling environment and run `node tools/check_landing.cjs`. `VISN_PREVIEW_URL` overrides the default local URL; `VISN_BROWSER_PATH` selects a browser executable. On macOS the script defaults to installed Google Chrome. On other platforms it uses Playwright's installed Chromium. The script checks the assets served by Rust directly and writes desktop/mobile screenshots and `verification.json` under `output/playwright/`.
