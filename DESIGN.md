# Visn: street, camera, recording

## Approved scope

Implement only the first two story scenes specified by the user. All logo designs are frozen: SVG, SVG construction/pose and the existing 3D mark. `docs/LOGO_FREEZE.json` and `tools/check_logo_freeze.cjs` guard those definitions. Later story stages are deferred until the user defines them.

## One connected world

The original Victorian intersection remains the landing view. CAM 01 is northwest at (-4.5, 3.3, -4.3); its diagonal opposite is CAM 04 southeast at (5.1, 3.2, 5.0), with its head mounted at z+0.48. Native scrolling moves the same Three.js perspective camera into that existing CCTV, then into the recording compact transport enclosed inside its body. There is one canvas and one renderer, not an unrelated desk or a separate film illustration.

## Scene one

A close three-quarter view reveals optics, an aged painted enclosure, thin weather hood, sealed end caps, captive screws, cable and recessed optical glass. The compact recording cassette is fully enclosed, with no external projector reels. The unit sits slightly upper-left, with the first story at lower-right. Procedural cloud noise gives the fog a clear optical centre and denser perimeter. A separate background veil keeps the street visible without allowing its adjacent lamp to obstruct the macro subject. Small packets travel into that mist behind the unit.

## Scene two

The lens moves through a progressive shell cutaway to the same internal reel transport. The physical top and return runs belong to a single closed film loop; equal travel speed makes their screen directions oppose naturally. Reel angular travel equals film distance divided by radius. The upper run dominates; the lower return and inner semicircular portions of the end reels remain partially visible. A local reel up-vector is used before the final 30-degree roll, so the projected film axis actually dips right by 30 degrees. Text sits lower-left.

## Materials and meaning

Retain coffee, cream, graphite, aged metal and restrained optical green. The imagery is a cinematic metaphor for digital recording. The moving frames are captured from the street scene using this camera's view; neither personal footage nor a remote feed is transmitted. The existing local draft workflow remains available.

## Responsiveness and motion

Device-specific lens distance/FOV preserve the two subjects and keep phone text in the lower clear area. Native scrolling stays in control. Manual/system reduced motion stops time-based movement and retains static scene navigation. Initial reduced-motion/no-WebGL visits receive actual desktop/phone renders of the same camera and reel scenes. Offscreen and contributor routes suspend the renderer.

## People and actions

Adult proportions, a fuller neck and jaw, narrower blinking eyes and integrated pleated clothing refine the existing figures. Standing actors shift weight with both soles planted. Readers follow a hinged page with their hand and gaze; vendors pick up, inspect and replace produce; conversational partners alternate gestures and listening nods. Carried bags attach to the hand and follow the walking phase. The existing grounded walking solver remains in use.

## Temporal refinement

Walking corners use segment arc-length mapping. Smooth pelvis reach constraints prevent abrupt drops when the limiting leg changes, while preserving world-space foot contacts. Action cycles use fifth-order easing and continuous head motion. Gesture with the free hand when carrying a prop. Validate movement with consecutive-frame metrics and continuous recordings, in addition to still-image composition checks.

The recording housing is a continuous radiused extrusion with gaskets and a cast mounting yoke. The cutaway contains a sensor carrier, flexible circuit, processor/heatsink, capacitors, connectors and driven cassette spindles. The film remains the agreed cinematic recording metaphor.

## Logo reveal motion

The logo geometry, colours and final proportions remain frozen. The user subsequently requested a new motion treatment: inverse-depth light streaks converge toward the viewport centre; the same six blades assemble inward; nodes and their pathways expand outward along the aperture gaps. A native dialog provides a skippable initial reveal and explicit replay. The introduction precedes, rather than adds chapters to, the existing two-scene street story.
