# Visn: street, camera, recording

## Approved scope

Implement only the first two story scenes specified by the user. All logo designs are frozen: SVG, SVG construction/pose and the existing 3D mark. `docs/LOGO_FREEZE.json` and `tools/check_logo_freeze.cjs` guard those definitions. Later story stages are deferred until the user defines them.

## One connected world

The original Victorian intersection remains the landing view. CAM 01 is northwest at (-4.5, 3.3, -4.3); its diagonal opposite is CAM 04 southeast at (5.1, 3.2, 5.0), with its head mounted at z+0.48. Native scrolling moves the same Three.js perspective camera into that existing CCTV, then into the recording transport mounted at its rear. There is one canvas and one renderer, not an unrelated desk or a separate film illustration.

## Scene one

A close three-quarter view reveals optics, an aged painted enclosure, weather hood, fasteners, ventilation, cable and continuously rotating pierced cinema reels. The unit sits slightly upper-left, with the first story at lower-right. Procedural cloud noise gives the fog a clear optical centre and denser perimeter. A separate background veil keeps the street visible without allowing its adjacent lamp to obstruct the macro subject. Small packets travel into that mist behind the unit.

## Scene two

The lens moves to the same reel transport. The physical top and return runs belong to a single closed film loop; equal travel speed makes their screen directions oppose naturally. Reel angular travel equals film distance divided by radius. The upper run dominates; the lower return and inner semicircular portions of the end reels remain partially visible. A local reel up-vector is used before the final 30-degree roll, so the projected film axis actually dips right by 30 degrees. Text sits lower-left.

## Materials and meaning

Retain coffee, cream, graphite, aged metal and restrained optical green. The imagery is a cinematic metaphor for digital recording. The moving frames are captured from the street scene using this camera's view; neither personal footage nor a remote feed is transmitted. The existing local draft workflow remains available.

## Responsiveness and motion

Device-specific lens distance/FOV preserve the two subjects and keep phone text in the lower clear area. Native scrolling stays in control. Manual/system reduced motion stops time-based movement and retains static scene navigation. Initial reduced-motion/no-WebGL visits receive actual desktop/phone renders of the same camera and reel scenes. Offscreen and contributor routes suspend the renderer.
