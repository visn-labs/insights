# Procedural 3D Town Introduction

## Visual concept

The introduction deliberately combines two periods:

- An isometric 1800s town drawn as a parchment-and-coffee-brown etched illustration.
- Green CCTV cameras mounted on period lamp posts and building corners.
- Blue-green mobile phones carried by people in top hats, bonnets, dresses, coats, caps, and aprons.

The town includes gabled buildings, arched shopfronts, a clock tower, market stall, fountain, horse-drawn carriage, road, pavement, trees, chimney smoke, birds, and animated townspeople. Camera scan cones move independently and pointer movement creates restrained scene parallax.

The focal illustration uses curve-first geometry. People use Bézier garment silhouettes, rounded heads and hats, and tube-rendered articulated limbs instead of straight line segments. Water, foliage, smoke, horse details, awnings, and architectural flourishes use curved strokes and rounded forms. The buildings remain deliberately geometric so the town still reads clearly from the isometric view.

The five cameras are the strongest color accent. Each has a rounded green housing, emissive blue-green lens, animated focus ring, scanning cone, and floating active label. A compact observation card reinforces the camera count without competing with the primary entry action.

## Runtime design

- Three.js 0.185.1 renders the town through one local WebGL canvas.
- Anime.js 4.5.0 choreographs the introductory text and action reveal.
- Both libraries are pinned and served from `static/vendor`; the page makes no CDN request.
- Geometry, people, buildings, cameras, phones, and motion are generated in `static/intro.js`; there are no stock scene assets.
- The Rust binary embeds and serves all intro files through `src/ui.rs` and `src/api.rs`.
- The entrance uses one primary pill-shaped action, a secondary skip action, a staged scene rise/zoom, and quiet status cues. This interaction hierarchy was informed by the restrained hero-to-product flow of the referenced Antigravity landing page, without reproducing its assets or branding.

Motion.dev was reviewed but not added because its DOM-animation role overlaps with Anime.js on this page. Keeping a single DOM animator avoids an unnecessary runtime and coordination layer.

## User behavior

1. The intro appears on the first page load of a browser-tab session.
2. **Enter the observation room**, **Skip introduction**, or `Escape` dismisses it.
3. The scene stops rendering after dismissal so it consumes no background GPU time.
4. The rook-shaped header control replays the intro.
5. `http://127.0.0.1:8080/?intro=1` forces it open for testing.
6. Pointer devices receive parallax guidance; coarse-pointer devices receive touch-and-drag guidance and smaller camera labels.
7. A page with `prefers-reduced-motion: reduce` renders one static town frame and removes transition duration.
8. If WebGL initialization fails, the text and entry controls remain available over an illustrated CSS fallback.

## Manual test checklist

- Desktop: verify title readability, staged scene arrival, parallax, natural walking cycles, fabric/limb movement, carriage movement, birds, smoke, water, blue-green phones, green cameras, active labels, lens pulses, and scan cones.
- Narrow viewport: verify the copy moves below the town, camera labels become compact, the live-camera card is hidden, and all entry controls remain reachable.
- Press `Escape`, then use the header replay button.
- Refresh in the same tab and confirm the intro remains skipped; use `?intro=1` to force it.
- Enable the operating system's reduced-motion preference and confirm the tableau is static.
- Disable WebGL in the browser and confirm the fallback still allows entry.

## Palette

| Purpose | Color |
| --- | --- |
| Parchment | `#f0ddba` |
| Light paper | `#fff1d3` |
| Coffee outline | `#70472f` |
| Dark ink | `#4b3024` |
| CCTV green | `#5f9362` |
| Phone blue-green | `#3fa8a2` |
