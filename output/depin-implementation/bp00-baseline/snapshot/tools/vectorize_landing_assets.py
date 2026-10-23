#!/usr/bin/env python3
"""Vectorize the landing-page sprite sheets into compact, sepia SVG artwork.

The source characters are 640x640 RGBA sheets arranged as a 4x4 grid.  This
script traces every frame into layered SVG paths, removes generator/grid edge
artifacts, and maps the artwork to the landing page's coffee-brown palette.
It intentionally keeps the source PNGs as provenance and writes separate
``vector_*.svg`` assets next to them.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "static" / "assets"
SHEET_SIZE = 640
FRAME_SIZE = 160
TRACE_SIZE = 80
GRID = 4
ALPHA_CUTOFF = 72
PALETTE = (
    (48, "#4b3024"),
    (88, "#65412f"),
    (132, "#845b40"),
    (180, "#aa8059"),
    (224, "#d1ad7d"),
    (256, "#ead6ae"),
)


Point = tuple[int, int]


def luminance(pixel: tuple[int, int, int, int]) -> int:
    red, green, blue, _ = pixel
    return (red * 54 + green * 183 + blue * 19) >> 8


def simplify_collinear(points: Sequence[Point]) -> list[Point]:
    if len(points) < 4:
        return list(points)
    simplified: list[Point] = []
    for index, point in enumerate(points):
        previous = points[index - 1]
        following = points[(index + 1) % len(points)]
        if (point[0] - previous[0]) * (following[1] - point[1]) == (
            point[1] - previous[1]
        ) * (following[0] - point[0]):
            continue
        simplified.append(point)
    return simplified


def point_line_distance(point: Point, start: Point, end: Point) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    numerator = abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0])
    return numerator / ((dx * dx + dy * dy) ** 0.5)


def rdp(points: Sequence[Point], epsilon: float) -> list[Point]:
    if len(points) < 3:
        return list(points)
    start, end = points[0], points[-1]
    furthest_index = 0
    furthest_distance = 0.0
    for index in range(1, len(points) - 1):
        distance = point_line_distance(points[index], start, end)
        if distance > furthest_distance:
            furthest_distance = distance
            furthest_index = index
    if furthest_distance <= epsilon:
        return [start, end]
    left = rdp(points[: furthest_index + 1], epsilon)
    right = rdp(points[furthest_index:], epsilon)
    return left[:-1] + right


def mask_loops(mask: Sequence[Sequence[bool]]) -> list[list[Point]]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    outgoing: dict[Point, list[Point]] = defaultdict(list)

    def filled(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height and mask[y][x]

    for y in range(height):
        for x in range(width):
            if not mask[y][x]:
                continue
            # Clockwise boundary edges around each filled cell.
            if not filled(x, y - 1):
                outgoing[(x, y)].append((x + 1, y))
            if not filled(x + 1, y):
                outgoing[(x + 1, y)].append((x + 1, y + 1))
            if not filled(x, y + 1):
                outgoing[(x + 1, y + 1)].append((x, y + 1))
            if not filled(x - 1, y):
                outgoing[(x, y + 1)].append((x, y))

    loops: list[list[Point]] = []
    while outgoing:
        start = next(iter(outgoing))
        current = start
        loop = [start]
        safety = 0
        while safety < width * height * 8:
            safety += 1
            options = outgoing.get(current)
            if not options:
                break
            following = options.pop()
            if not options:
                del outgoing[current]
            current = following
            if current == start:
                break
            loop.append(current)
        if current == start and len(loop) >= 4:
            loop = simplify_collinear(loop)
            # Close the ring temporarily so RDP can simplify the curved outline.
            if len(loop) >= 5:
                loop = rdp(loop + [loop[0]], 0.62)[:-1]
            if len(loop) >= 3:
                loops.append(loop)
    return loops


def smooth_path(loop: Sequence[Point]) -> str:
    if len(loop) < 3:
        return ""
    first_mid = ((loop[-1][0] + loop[0][0]) / 2, (loop[-1][1] + loop[0][1]) / 2)
    commands = [f"M{first_mid[0]:.2f},{first_mid[1]:.2f}"]
    for index, point in enumerate(loop):
        following = loop[(index + 1) % len(loop)]
        midpoint = ((point[0] + following[0]) / 2, (point[1] + following[1]) / 2)
        commands.append(f"Q{point[0]},{point[1]} {midpoint[0]:.2f},{midpoint[1]:.2f}")
    commands.append("Z")
    return "".join(commands)


def combined_path(mask: Sequence[Sequence[bool]], minimum_area: int = 2) -> str:
    parts: list[str] = []
    for loop in mask_loops(mask):
        xs = [point[0] for point in loop]
        ys = [point[1] for point in loop]
        if (max(xs) - min(xs)) * (max(ys) - min(ys)) < minimum_area:
            continue
        path = smooth_path(loop)
        if path:
            parts.append(path)
    return "".join(parts)


def frame_masks(frame: Image.Image) -> tuple[list[list[bool]], list[list[list[bool]]]]:
    reduced = frame.resize((TRACE_SIZE, TRACE_SIZE), Image.Resampling.LANCZOS).convert("RGBA")
    pixels = reduced.load()
    silhouette = [[False] * TRACE_SIZE for _ in range(TRACE_SIZE)]
    tones = [[[False] * TRACE_SIZE for _ in range(TRACE_SIZE)] for _ in PALETTE]
    for y in range(TRACE_SIZE):
        for x in range(TRACE_SIZE):
            # Source sheets with opaque registration/grid lines are cleaned at
            # every cell edge. Characters do not occupy this two-source-pixel rim.
            if x in (0, TRACE_SIZE - 1) or y in (0, TRACE_SIZE - 1):
                continue
            pixel = pixels[x, y]
            if pixel[3] < ALPHA_CUTOFF:
                continue
            silhouette[y][x] = True
            value = luminance(pixel)
            for index, (upper, _) in enumerate(PALETTE):
                if value < upper:
                    tones[index][y][x] = True
                    break
    return silhouette, tones


def vectorize_sheet(source: Path, target: Path) -> None:
    sheet = Image.open(source).convert("RGBA")
    if sheet.size != (SHEET_SIZE, SHEET_SIZE):
        raise ValueError(f"{source.name}: expected 640x640, got {sheet.size}")

    groups: list[str] = []
    for row in range(GRID):
        for col in range(GRID):
            frame = sheet.crop(
                (
                    col * FRAME_SIZE,
                    row * FRAME_SIZE,
                    (col + 1) * FRAME_SIZE,
                    (row + 1) * FRAME_SIZE,
                )
            )
            silhouette, tones = frame_masks(frame)
            transform = f"translate({col * FRAME_SIZE} {row * FRAME_SIZE}) scale(2)"
            layers: list[str] = []
            silhouette_path = combined_path(silhouette, minimum_area=3)
            if silhouette_path:
                layers.append(
                    f'<path d="{silhouette_path}" fill="#ead6ae" fill-rule="evenodd"/>'
                )
            for tone, (_, color) in zip(tones, PALETTE):
                path = combined_path(tone, minimum_area=2)
                if path:
                    layers.append(f'<path d="{path}" fill="{color}" fill-rule="evenodd"/>')
            if silhouette_path:
                layers.append(
                    f'<path d="{silhouette_path}" fill="none" stroke="#4b3024" '
                    'stroke-width=".82" stroke-linejoin="round" stroke-linecap="round" '
                    'fill-rule="evenodd"/>'
                )
            frame_index = row * GRID + col
            groups.append(
                f'<g id="frame-{frame_index}" data-frame="{frame_index}" transform="{transform}">'
                + "".join(layers)
                + "</g>"
            )

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="640" '
        'viewBox="0 0 640 640" shape-rendering="geometricPrecision">'
        f'<title>{source.stem.replace("_", " ").title()} — 16 traced frames</title>'
        '<desc>Vectorized from the original 4 by 4 landing animation sheet; row-major frame order.</desc>'
        + "".join(groups)
        + "</svg>\n"
    )
    target.write_text(svg, encoding="utf-8")


def vectorize_palette(source: Path, target: Path) -> None:
    image = Image.open(source).convert("RGB")
    if image.size != (256, 16):
        raise ValueError(f"{source.name}: expected 256x16 palette strip, got {image.size}")
    swatches = []
    for index in range(16):
        red, green, blue = image.getpixel((index * 16 + 8, 8))
        swatches.append(
            f'<rect x="{index * 16}" width="16" height="16" fill="#{red:02x}{green:02x}{blue:02x}"/>'
        )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="16" viewBox="0 0 256 16">'
        f'<title>{source.stem.title()} source palette</title>'
        + "".join(swatches)
        + '</svg>\n'
    )
    target.write_text(svg, encoding="utf-8")


def generated_targets() -> Iterable[Path]:
    for source in sorted(ASSET_DIR.glob("*.png")):
        target = ASSET_DIR / f"vector_{source.stem}.svg"
        vectorize_sheet(source, target)
        yield target
    for source in sorted(ASSET_DIR.glob("*.jpg")):
        target = ASSET_DIR / f"vector_{source.stem}_palette.svg"
        vectorize_palette(source, target)
        yield target


def main() -> None:
    targets = list(generated_targets())
    total_bytes = sum(path.stat().st_size for path in targets)
    print(f"Vectorized {len(targets)} assets ({total_bytes / 1024 / 1024:.2f} MiB)")
    for path in targets:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
