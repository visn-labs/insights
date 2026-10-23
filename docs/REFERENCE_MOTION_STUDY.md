# Reference study and implemented motion refinements

Reviewed on 21 October 2026. The existing coffee/cream world, two story chapters, contributor routes and logo design remain the baseline. Animation changes are custom adaptations of the relevant techniques, not imports of the demo applications or a framework migration.

## Sources inspected

| Repository | Verified revision | Relevant reading |
| --- | --- | --- |
| [3Dviz Pro Max](https://github.com/viettranx/3dviz-pro-max/tree/d077e0e68915c25be8e71d74684d3144fd1c2aca/examples) | `d077e0e68915c25be8e71d74684d3144fd1c2aca` | Catalogued all 37 example READMEs; closely inspected shared render loop, camera follow rig, studio lighting, material/geometry helpers, coupled gears, crafted props, body-motion helpers, and subject/lighting guidance. |
| [Liquid skills](https://github.com/benjaminsehl/liquid-skills/tree/483f969bed623c87c9e26ac34324d8d7d8fe68c3) | `483f969bed623c87c9e26ac34324d8d7d8fe68c3` | Language scope, theme standards, accessibility, defensive CSS, focus/keyboard patterns and progressive enhancement. |
| [Emil Kowalski skills](https://github.com/emilkowalski/skills/tree/2d228630ba1446365f614a108cb8252f1feda202) | `2d228630ba1446365f614a108cb8252f1feda202` | Design engineering, animation choice, spring interruption, animation performance and review standards. |

Liquid here is Shopify's template language. That repository does not supply fluid simulation, liquid shaders or morphing physics. Its transferable value is semantic controls, keyboard/focus behavior, native APIs and resilient styling. Shopify commerce objects and native-app skills are not applicable to this Rust-served frontend.

The most useful 3Dviz examples are the crafted props and constrained mechanisms, not the anatomy asset sets. Their contribution is reasoning about silhouettes, joins, support, material scale and authoritative motion state. No external anatomical meshes, demo runtime or package dependencies were added. The current walking contact solver was preserved.

## Decisions applied

| Before | After | Why |
| --- | --- | --- |
| Renderer and DOM each calculated their own story phases | Shared `recordingState()` score | Camera, cutaway, fog, copy and navigation use the same progress. |
| Linear camera interpolation with an added vertical sine | Cubic dolly paths driven by a fifth-order transition | Camera travel reaches the reading holds with zero endpoint velocity and acceleration; reverse scrolling selects the same pose. |
| Macro camera did not respond to inspection | Small critically damped pointer parallax | Reversals retain velocity. The response is elapsed-time based, while scroll itself stays exact and undelayed. |
| Chapter actions used the same smooth scroll for every input | Pointer uses interruptible native scrolling; keyboard jumps and focuses the destination | Manual input wins; keyboard navigation does not wait for decorative motion. |
| Separate chapter borders and unstaged copy | Sliding reading-position indicator, direct progress transform and coordinated copy reveal | The two views read as one connected journey. Hidden chapter actions cannot receive invisible focus. |
| Inherited scroll variables and repeated layout reads | Direct element opacity/transforms; batched label measurements | Avoid unnecessary descendant style work and read/write alternation. |
| Projection and shadow camera recomputed at every hold frame | Update only when their inputs change | Preserve exact rendering values without repeating unchanged work. |
| Street geometry initialized during the introductory shader | Defer the street build until the logo releases the page | Removes a measured startup frame hitch from the active reveal. |
| Uniform camera finish | Deterministic mipmapped enamel grain, brushed metal response and shaped reflection panels | Add surface identity at close range without replacing geometry or changing the palette. |
| Repeated onboarding reveal restarted from its first frame | WAAPI retargets an unfinished reveal from its visible state; keyboard skips it | Rapid reversal does not flash back to an earlier pose. |

The DOM changes use native browser APIs. The existing Three.js renderer remains responsible for the 3D world. Reduced motion retains the content and destination compositions; no artificial scroll inertia or new story stages were introduced.

## Inverted LightSpeed logo reveal

Reference: [Rahil Vahora's LightSpeed](https://21st.dev/@rahil1202/components/light-speed). Inspected the actual live iframe and its published bundle (`bundle.1759470911908.html`), not just the preview image. The bundled shader credits Matthias Hurrle. Its visible effect uses angular and inverse-radius coordinates to make a moving depth pattern appear as a light tunnel.

The Visn shader is a new implementation of this projection principle. Its depth coordinate subtracts elapsed time. A fixed feature therefore moves to increasing depth: `radius = atan(0.075 / depth)` decreases as time advances. The centre stays fixed at the viewport midpoint, and the SVG shares that exact midpoint. Green/mint and bronze light preserve the existing brand palette.

The 5.6-second sequence is:

1. Glowing depth layers converge inward toward the single centre.
2. Exactly six original aperture leaves arrive from outside and twist into their final positions.
3. The central network forms; green nodes expand along curved routes from the core toward the outer ring, with the pathway reveal following the same travel.
4. The original logo is restored exactly; the introductory playback releases the street. Explicit replay holds the final logo for inspection.

The original SVG asset and factory/pose hashes remain unchanged. This adds a separate reveal controller rather than editing the logo design. Native dialog behavior provides Escape dismissal and focus handling; Skip is always available. Contributor deep links bypass autoplay. Reduced-motion and missing-WebGL paths expose the static result. The initial street build is deferred until the introductory reveal finishes; an existing street renderer pauses while the reveal is open, and the tunnel's shader, program and buffer are released when playback ends.

## Verification and artifacts

- `tools/check_recording_state.mjs`: score ranges, endpoint continuity, rate-independent pointer response and reversal.
- `tools/check_journey_interaction.cjs`: keyboard destinations, scroll interruption, pointer inspection, onboarding keyboard behavior, route return and reduced motion.
- `tools/check_logo_direction.mjs`: 1,201 timeline samples covering convergence and outward node/pathway growth.
- `tools/check_logo_reveal.cjs`: real GPU compilation, assembly, alignment, final radius, replay, Escape, mobile skip and static fallbacks, with a continuous recording.
- Existing logo freeze, scene, contributor, walking, action-contact and transport tests remain in use.

Logo captures/video/report: `output/playwright/logo-convergence/`. Journey interaction report: `output/playwright/reference-refinement/interaction/`. Screenshots establish composition only; continuous recording and temporal checks establish the tested movement behavior. Local browser results do not constitute physical-phone validation or user artistic acceptance.

## Final validation

All 76 browser checks passed: 14 logo-reveal checks, 23 scene checks, 30 contributor workflow checks and 9 interaction checks. Logo geometry/factory hash checks and 1,201 directional timeline samples passed.

After deferring street construction, the final recorded introduction measured 333 frame intervals: median 16.7 ms, p95 16.7 ms, maximum 16.8 ms. The earlier run with competing street construction had a 650 ms interval. These are local Chrome measurements with video recording enabled, not physical-phone guarantees.

Final recording: `output/playwright/logo-convergence/logo-convergence.webm`.
