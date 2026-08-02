#!/usr/bin/env python3
"""Development-only YOLO26 adapter for the Rust Phase 0 service.

The production target is DeepStream/TensorRT. This adapter makes uploaded files and
short RTSP trials usable before an NVIDIA DeepStream host is available. It writes
exactly one DetectorOutput JSON object to stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import sys
import time
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="-",
        help="Video path/RTSP URI, or '-' to read it from stdin without exposing credentials in the process list.",
    )
    parser.add_argument("--model", default="yolo26s.pt")
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--max-seconds", type=float, default=120.0)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--device", default=None)
    return parser.parse_args()


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    args = parse_args()
    if args.source == "-":
        args.source = sys.stdin.read().strip()
    if not args.source:
        fail("A video source is required")
    if args.fps <= 0 or args.max_seconds <= 0:
        fail("--fps and --max-seconds must be greater than zero")

    try:
        import cv2  # type: ignore
        from ultralytics import YOLO  # type: ignore
    except ImportError as error:
        fail(
            f"Missing YOLO development dependencies: {error}. "
            "Install with: python3 -m pip install -r tools/requirements-yolo.txt"
        )

    capture = cv2.VideoCapture(args.source)
    if not capture.isOpened():
        fail(f"Could not open video source: {redact_source(args.source)}")

    nominal_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if nominal_fps <= 0 or nominal_fps > 240:
        nominal_fps = 25.0
    stride = max(1, round(nominal_fps / args.fps))
    with redirect_stdout(sys.stderr):
        model = YOLO(args.model)
    observations: list[dict[str, Any]] = []
    frame_index = 0
    wall_start = time.monotonic()

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            source_time_ms = int(capture.get(cv2.CAP_PROP_POS_MSEC) or 0)
            if source_time_ms <= 0:
                source_time_ms = int(frame_index * 1000.0 / nominal_fps)
            if source_time_ms > args.max_seconds * 1000:
                break
            if time.monotonic() - wall_start > args.max_seconds * 2:
                fail("Video read exceeded the bounded wall-clock deadline")
            if frame_index % stride != 0:
                frame_index += 1
                continue

            height, width = frame.shape[:2]
            with redirect_stdout(sys.stderr):
                results = model.track(
                    frame,
                    persist=True,
                    verbose=False,
                    conf=args.confidence,
                    device=args.device,
                )
            result = results[0]
            boxes = result.boxes
            if boxes is not None:
                xyxy = boxes.xyxy.cpu().tolist()
                confidences = boxes.conf.cpu().tolist()
                classes = boxes.cls.int().cpu().tolist()
                track_ids = (
                    boxes.id.int().cpu().tolist()
                    if boxes.id is not None
                    else [f"untracked-{frame_index}-{index}" for index in range(len(xyxy))]
                )
                for index, coordinates in enumerate(xyxy):
                    x1, y1, x2, y2 = coordinates
                    x1 = min(max(0.0, x1), float(width))
                    y1 = min(max(0.0, y1), float(height))
                    x2 = min(max(x1, x2), float(width))
                    y2 = min(max(y1, y2), float(height))
                    if x2 <= x1 or y2 <= y1:
                        continue
                    observations.append(
                        {
                            "frame_time_ms": source_time_ms,
                            "track_id": str(track_ids[index]),
                            "class_name": str(result.names[int(classes[index])]),
                            "confidence": float(confidences[index]),
                            "bbox": [
                                x1 / width,
                                y1 / height,
                                (x2 - x1) / width,
                                (y2 - y1) / height,
                            ],
                        }
                    )
            frame_index += 1
    finally:
        capture.release()

    json.dump({"model": args.model, "observations": observations}, sys.stdout)
    sys.stdout.write("\n")


def redact_source(source: str) -> str:
    if source.startswith(("rtsp://", "rtsps://")):
        return "rtsp://***"
    return source


if __name__ == "__main__":
    main()
