# Landing vector animation

The landing town uses a fixed 16 FPS simulation step (`62.5 ms`) and a 2D
canvas renderer. The 24 source PNGs are 640 x 640 sprite sheets with a 4 x 4,
row-major frame grid. `tools/vectorize_landing_assets.py` traces every frame to
coffee-brown SVG paths and removes opaque registration lines at cell edges.
The four 256 x 16 JPG files are color-reference strips, so they are preserved
as vector swatches rather than animated as characters. The SVG files that were
already present in `static/assets` and `static/svgs` remain vector sources.

Every source character faces right. `direction: -1` mirrors the sheet while
drawing. Speeds are world pixels per second in a 1600 x 900 scene; different
frame cadences retain each action's intended weight while the simulation itself
always advances at 16 FPS.

| Asset | Behaviour | Direction | Speed | Pose cadence |
|---|---|---:|---:|---:|
| apple_seller | stationary selling gesture | right | 0 | 6 FPS |
| aristocrat | measured walk | left | 31 | 9 FPS |
| beggar_child | stationary begging gesture | right | 0 | 6 FPS |
| blacksmith | short work-yard patrol | reverses | 18 | 8 FPS |
| blind_beggar | cane-led slow walk | right | 14 | 7 FPS |
| chimney_sweep | brisk walk | left | 39 | 11 FPS |
| constable | regular patrol | right | 29 | 9 FPS |
| drunkard | slow stagger with body sway | left | 12 | 7 FPS |
| flower_girl | slow selling walk | right | 18 | 7 FPS |
| gentleman | measured walk | right | 28 | 9 FPS |
| hansom_cab | road crossing / gallop | right | 88 | 16 FPS |
| high_society_lady | slow promenade | left | 17 | 7 FPS |
| lamplighter | equipment-bearing walk | right | 23 | 8 FPS |
| maid | purposeful walk | left | 27 | 9 FPS |
| newsboy | fast street walk | right | 53 | 13 FPS |
| pickpocket | crouched creep | left | 22 | 10 FPS |
| postman | brisk delivery walk | right | 42 | 11 FPS |
| priest | slow procession | left | 15 | 7 FPS |
| rat_catcher | burdened walk | right | 24 | 8 FPS |
| sailor | steady walk | left | 31 | 9 FPS |
| seamstress | unhurried walk | right | 21 | 8 FPS |
| street_musician | stationary instrument cycle | right | 0 | 8 FPS |
| urchin | run | right | 69 | 16 FPS |
| wealthy_couple | promenade | left | 18 | 7 FPS |

The five CCTV heads are drawn in green above roofs and poles. Their heads scan
at a slower independent rhythm, while lens glow and view cones pulse on the
same deterministic scene clock. Selected townspeople receive small teal phone
overlays to keep the deliberate old-town/modern-device contrast.
