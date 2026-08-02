#!/usr/bin/env python3
"""Development-only YOLO26 adapter for the Rust Phase 0 service.

The production target is DeepStream/TensorRT. This adapter makes uploaded files and
bounded HTTP(S)/RTSP trials usable before an NVIDIA DeepStream host is available. It writes
exactly one DetectorOutput JSON object to stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import sys
import time
from typing import Any


OUTPUT_PREFIX = "VISN_DETECTOR_JSON:"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="-",
        help="Video path or HTTP(S)/RTSP URI; use '-' to read it from stdin without exposing credentials in the process list.",
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


class FfmpegHttpCapture:
    """Small VideoCapture-compatible adapter using an isolated FFmpeg process."""

    def __init__(
        self, imageio_ffmpeg: Any, np: Any, source: str, max_seconds: float
    ) -> None:
        self.np = np
        self.frames = imageio_ffmpeg.read_frames(
            source,
            pix_fmt="bgr24",
            input_params=[
                "-rw_timeout",
                "5000000",
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "2",
                "-user_agent",
                "Visn-Phase0/0.1",
            ],
            output_params=["-an", "-t", str(max_seconds)],
        )
        metadata = next(self.frames)
        size = metadata.get("size")
        if not size or len(size) != 2:
            self.frames.close()
            raise RuntimeError("FFmpeg did not report the HTTP video dimensions")
        self.width, self.height = size
        self.frame_rate = float(metadata.get("fps") or 25.0)
        self.opened = True

    def is_opened(self) -> bool:
        return self.opened

    def fps(self) -> float:
        return self.frame_rate

    def read(self) -> tuple[bool, Any]:
        try:
            frame_bytes = next(self.frames)
            frame = self.np.frombuffer(frame_bytes, dtype=self.np.uint8)
            expected_size = self.width * self.height * 3
            if frame.size != expected_size:
                return False, None
            return True, frame.reshape((self.height, self.width, 3))
        except (StopIteration, OSError, RuntimeError):
            return False, None

    def release(self) -> None:
        if self.opened:
            self.frames.close()
            self.opened = False


def open_cv_capture(cv2: Any, source: str, is_network_stream: bool) -> Any:
    """Open a capture with bounded network waits when the FFmpeg backend supports them."""
    if is_network_stream and all(
        hasattr(cv2, name)
        for name in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC")
    ):
        try:
            capture = cv2.VideoCapture(
                source,
                cv2.CAP_FFMPEG,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                    15_000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                    5_000,
                ],
            )
            if capture.isOpened():
                return capture
            capture.release()
        except cv2.error:
            # Some platform OpenCV builds expose the properties but not parameterized FFmpeg capture.
            pass
    return cv2.VideoCapture(source)


def safe_open_capture(
    cv2: Any,
    imageio_ffmpeg: Any,
    np: Any,
    source: str,
    is_http_stream: bool,
    max_seconds: float,
) -> Any:
    try:
        if is_http_stream:
            return FfmpegHttpCapture(imageio_ffmpeg, np, source, max_seconds)
        return open_cv_capture(cv2, source, source.startswith(("rtsp://", "rtsps://")))
    except Exception as error:
        message = redact_message(str(error), source)
        fail(f"Could not open video source {redact_source(source)}: {type(error).__name__}: {message}")


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

    is_http_stream = args.source.startswith(("http://", "https://"))
    imageio_ffmpeg = None
    np = None
    if is_http_stream:
        try:
            import imageio_ffmpeg  # type: ignore
            import numpy as np  # type: ignore
        except ImportError as error:
            fail(
                f"Missing HTTP decoder dependency: {error}. "
                "Install with: python3 -m pip install -r tools/requirements-yolo.txt"
            )

    with redirect_stdout(sys.stderr):
        model = YOLO(args.model)

    is_network_stream = is_http_stream or args.source.startswith(("rtsp://", "rtsps://"))
    capture = safe_open_capture(
        cv2, imageio_ffmpeg, np, args.source, is_http_stream, args.max_seconds
    )
    if not (capture.is_opened() if is_http_stream else capture.isOpened()):
        fail(f"Could not open video source: {redact_source(args.source)}")

    nominal_fps = capture.fps() if is_http_stream else float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if nominal_fps <= 0 or nominal_fps > 240:
        nominal_fps = 25.0
    stride = max(1, round(nominal_fps / args.fps))
    observations: list[dict[str, Any]] = []
    frame_index = 0
    wall_start = time.monotonic()

    try:
        while True:
            elapsed_seconds = time.monotonic() - wall_start
            if is_network_stream and elapsed_seconds >= args.max_seconds:
                break
            ok, frame = capture.read()
            if not ok:
                if not is_network_stream or time.monotonic() >= wall_start + args.max_seconds:
                    break
                capture.release()
                time.sleep(min(1.0, max(0.0, wall_start + args.max_seconds - time.monotonic())))
                capture = safe_open_capture(
                    cv2,
                    imageio_ffmpeg,
                    np,
                    args.source,
                    is_http_stream,
                    max(0.1, wall_start + args.max_seconds - time.monotonic()),
                )
                continue
            elapsed_seconds = time.monotonic() - wall_start
            if is_network_stream:
                source_time_ms = int(elapsed_seconds * 1000)
            else:
                source_time_ms = int(capture.get(cv2.CAP_PROP_POS_MSEC) or 0)
            if source_time_ms <= 0:
                source_time_ms = int(frame_index * 1000.0 / nominal_fps)
            if source_time_ms > args.max_seconds * 1000:
                break
            if not is_network_stream and elapsed_seconds > args.max_seconds * 2:
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

    payload = json.dumps(
        {"model": args.model, "observations": observations},
        separators=(",", ":"),
        allow_nan=False,
    )
    sys.stdout.write(f"{OUTPUT_PREFIX}{payload}\n")


def redact_source(source: str) -> str:
    if source.startswith(("rtsp://", "rtsps://")):
        return "rtsp://***"
    if source.startswith(("http://", "https://")):
        return f"{source.split(':', 1)[0]}://***"
    return source


def redact_message(message: str, source: str) -> str:
    return message.replace(source, redact_source(source))


if __name__ == "__main__":
    main()
