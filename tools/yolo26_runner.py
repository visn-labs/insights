#!/usr/bin/env python3
"""Development-only YOLO26 adapter for the Rust Phase 0 service.

The production target is DeepStream/TensorRT. This adapter makes uploaded files and
bounded HTTP(S)/RTSP trials usable before an NVIDIA DeepStream host is available. By
default it writes exactly one DetectorOutput JSON object to stdout; opt-in streaming
also emits bounded per-frame observation arrays. Diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stdout
import json
import math
import os
import sys
import time
from typing import Any


OUTPUT_PREFIX = "VISN_DETECTOR_JSON:"
OBSERVATIONS_OUTPUT_PREFIX = "VISN_OBSERVATIONS_JSON:"


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
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default=None)
    parser.add_argument(
        "--stream-output",
        action="store_true",
        help=(
            "Emit one VISN_OBSERVATIONS_JSON array per processed frame instead "
            "of retaining observations for the final result."
        ),
    )
    parser.add_argument(
        "--appearance-mode",
        choices=("off", "person", "all"),
        default="all",
        help="Choose which detections receive development appearance descriptors.",
    )
    parser.add_argument(
        "--appearance-interval-secs",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "Minimum time between appearance samples for one eligible track; "
            "zero preserves per-frame sampling."
        ),
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        metavar="N",
        help="Cap OpenCV and PyTorch worker threads; unchanged when omitted.",
    )
    return parser.parse_args()


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


class FfmpegHttpCapture:
    """Small VideoCapture-compatible adapter using an isolated FFmpeg process."""

    def __init__(
        self,
        imageio_ffmpeg: Any,
        np: Any,
        source: str,
        max_seconds: float,
        requested_fps: float,
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
            output_params=[
                "-an",
                "-vf",
                f"fps={requested_fps}:round=near",
                "-t",
                str(max_seconds),
            ],
        )
        metadata = next(self.frames)
        size = metadata.get("size")
        if not size or len(size) != 2:
            self.frames.close()
            raise RuntimeError("FFmpeg did not report the HTTP video dimensions")
        self.width, self.height = size
        # FFmpeg has already applied the requested output cadence. Do not apply
        # the input stream's nominal FPS a second time in the Python loop.
        self.frame_rate = requested_fps
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

    def grab(self) -> bool:
        """Consume one raw frame without constructing a NumPy array for it."""
        try:
            next(self.frames)
            return True
        except (StopIteration, OSError, RuntimeError):
            return False

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
    requested_fps: float,
) -> Any:
    try:
        if is_http_stream:
            return FfmpegHttpCapture(
                imageio_ffmpeg, np, source, max_seconds, requested_fps
            )
        return open_cv_capture(cv2, source, source.startswith(("rtsp://", "rtsps://")))
    except Exception as error:
        message = redact_message(str(error), source)
        fail(f"Could not open video source {redact_source(source)}: {type(error).__name__}: {message}")


def appearance_descriptor(cv2: Any, np: Any, crop: Any) -> Any:
    """Deterministic dev descriptor; production replaces this with OSNet/TensorRT ReID."""
    if crop is None or crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 4:
        return None
    resized = cv2.resize(crop, (32, 64), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    hs_hist = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).flatten()
    channel_histograms = [
        cv2.calcHist([resized], [channel], None, [8], [0, 256]).flatten()
        for channel in range(3)
    ]
    pixels = resized.reshape(-1, 3).astype(np.float32) / 255.0
    moments = np.concatenate([pixels.mean(axis=0), pixels.std(axis=0)])
    descriptor = np.concatenate([hs_hist, *channel_histograms, moments]).astype(np.float32)
    norm = float(np.linalg.norm(descriptor))
    if not np.isfinite(norm) or norm <= 1e-8:
        return None
    return (descriptor / norm).tolist()


def representative_frame_quality(cv2: Any, frame: Any) -> float:
    """Prefer a sharp, normally exposed frame without retaining the full stream."""
    height, width = frame.shape[:2]
    scale = min(1.0, 320.0 / max(height, width))
    sample = cv2.resize(
        frame,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    exposure_penalty = abs(brightness - 127.5) / 127.5
    return sharpness + contrast * 0.5 - exposure_penalty * 20.0


def encode_representative_frame(cv2: Any, frame: Any, frame_time_ms: int) -> Any:
    if frame is None or frame.size == 0:
        return None
    height, width = frame.shape[:2]
    scale = min(1.0, 960.0 / max(height, width))
    if scale < 1.0:
        frame = cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    encoded_ok, encoded = cv2.imencode(
        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78]
    )
    if not encoded_ok:
        return None
    encoded_height, encoded_width = frame.shape[:2]
    return {
        "media_type": "image/jpeg",
        "data_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
        "frame_time_ms": frame_time_ms,
        "width": encoded_width,
        "height": encoded_height,
    }


def emit_frame_observations(observations: list[dict[str, Any]]) -> None:
    payload = json.dumps(
        observations,
        separators=(",", ":"),
        allow_nan=False,
    )
    sys.stdout.write(f"{OBSERVATIONS_OUTPUT_PREFIX}{payload}\n")
    sys.stdout.flush()


def main() -> None:
    args = parse_args()
    if args.source == "-":
        args.source = sys.stdin.read().strip()
    if not args.source:
        fail("A video source is required")
    if (
        not math.isfinite(args.fps)
        or args.fps <= 0
        or not math.isfinite(args.max_seconds)
        or args.max_seconds <= 0
        or not math.isfinite(args.confidence)
        or not 0.0 <= args.confidence <= 1.0
    ):
        fail("--fps and --max-seconds must be finite and positive; --confidence must be in [0, 1]")
    if (
        not math.isfinite(args.appearance_interval_secs)
        or args.appearance_interval_secs < 0
    ):
        fail("--appearance-interval-secs must be finite and zero or greater")
    if args.threads is not None and args.threads <= 0:
        fail("--threads must be greater than zero")
    if args.imgsz <= 0:
        fail("--imgsz must be greater than zero")

    if args.threads is not None:
        thread_count = str(args.threads)
        for variable in (
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        ):
            os.environ[variable] = thread_count

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from ultralytics import YOLO  # type: ignore
    except ImportError as error:
        fail(
            f"Missing YOLO development dependencies: {error}. "
            "Install with: python3 -m pip install -r tools/requirements-yolo.txt"
        )

    if args.threads is not None:
        cv2.setNumThreads(args.threads)
        torch.set_num_threads(args.threads)
        try:
            torch.set_num_interop_threads(args.threads)
        except RuntimeError as error:
            fail(f"Could not apply --threads to PyTorch inter-op workers: {error}")

    is_http_stream = args.source.startswith(("http://", "https://"))
    imageio_ffmpeg = None
    if is_http_stream:
        try:
            import imageio_ffmpeg  # type: ignore
        except ImportError as error:
            fail(
                f"Missing HTTP decoder dependency: {error}. "
                "Install with: python3 -m pip install -r tools/requirements-yolo.txt"
            )

    with redirect_stdout(sys.stderr):
        model = YOLO(args.model)

    is_network_stream = is_http_stream or args.source.startswith(("rtsp://", "rtsps://"))
    capture = safe_open_capture(
        cv2,
        imageio_ffmpeg,
        np,
        args.source,
        is_http_stream,
        args.max_seconds,
        args.fps,
    )
    if not (capture.is_opened() if is_http_stream else capture.isOpened()):
        fail(f"Could not open video source: {redact_source(args.source)}")

    nominal_fps = capture.fps() if is_http_stream else float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if nominal_fps <= 0 or nominal_fps > 240:
        nominal_fps = 25.0
    stride = max(1, round(nominal_fps / args.fps))
    observations: list[dict[str, Any]] | None = (
        None if args.stream_output else []
    )
    appearance_interval_ms = round(args.appearance_interval_secs * 1000.0)
    track_appearance_sample_times: dict[str, int] = {}
    best_frame = None
    best_frame_time_ms = 0
    best_frame_quality = float("-inf")
    frame_index = 0
    wall_start = time.monotonic()

    try:
        while True:
            elapsed_seconds = time.monotonic() - wall_start
            if is_network_stream and elapsed_seconds >= args.max_seconds:
                break
            process_frame = frame_index % stride == 0
            if process_frame:
                ok, frame = capture.read()
            else:
                ok = capture.grab()
                frame = None
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
                    args.fps,
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
            if not process_frame:
                frame_index += 1
                continue

            frame_observations: list[dict[str, Any]] = []
            height, width = frame.shape[:2]
            frame_quality = representative_frame_quality(cv2, frame)
            if frame_quality > best_frame_quality:
                representative_scale = min(1.0, 960.0 / max(height, width))
                best_frame = (
                    cv2.resize(
                        frame,
                        (
                            max(1, round(width * representative_scale)),
                            max(1, round(height * representative_scale)),
                        ),
                        interpolation=cv2.INTER_AREA,
                    )
                    if representative_scale < 1.0
                    else frame.copy()
                )
                best_frame_time_ms = source_time_ms
                best_frame_quality = frame_quality
            with redirect_stdout(sys.stderr):
                results = model.track(
                    frame,
                    persist=True,
                    verbose=False,
                    conf=args.confidence,
                    device=args.device,
                    imgsz=args.imgsz,
                )
            result = results[0]
            boxes = result.boxes
            if boxes is not None:
                # Transfer the box tensor to CPU once. Accessing xyxy/conf/cls/id
                # independently creates redundant device copies on accelerated runtimes.
                box_rows = boxes.data.detach().cpu().tolist()
                tracked = bool(getattr(boxes, "is_track", False))
                for index, row in enumerate(box_rows):
                    x1, y1, x2, y2 = row[:4]
                    if tracked:
                        track_id = str(int(row[4]))
                        confidence = float(row[5])
                        class_id = int(row[6])
                    else:
                        track_id = f"untracked-{frame_index}-{index}"
                        confidence = float(row[4])
                        class_id = int(row[5])
                    x1 = min(max(0.0, x1), float(width))
                    y1 = min(max(0.0, y1), float(height))
                    x2 = min(max(x1, x2), float(width))
                    y2 = min(max(y1, y2), float(height))
                    if x2 <= x1 or y2 <= y1:
                        continue
                    class_name = str(result.names[class_id])
                    crop = frame[int(y1) : int(y2), int(x1) : int(x2)]
                    appearance = None
                    appearance_enabled = args.appearance_mode == "all" or (
                        args.appearance_mode == "person" and class_name == "person"
                    )
                    if appearance_enabled:
                        previous_sample_time = track_appearance_sample_times.get(track_id)
                        sample_due = (
                            previous_sample_time is None
                            or source_time_ms < previous_sample_time
                            or source_time_ms - previous_sample_time
                            >= appearance_interval_ms
                        )
                        if sample_due:
                            appearance = appearance_descriptor(cv2, np, crop)
                            if appearance is not None:
                                track_appearance_sample_times[track_id] = source_time_ms
                    frame_observations.append(
                        {
                            "frame_time_ms": source_time_ms,
                            "track_id": track_id,
                            "class_name": class_name,
                            "confidence": confidence,
                            "bbox": [
                                x1 / width,
                                y1 / height,
                                (x2 - x1) / width,
                                (y2 - y1) / height,
                            ],
                            "appearance": appearance,
                        }
                    )
            if observations is None:
                emit_frame_observations(frame_observations)
            else:
                observations.extend(frame_observations)
            frame_index += 1
    finally:
        capture.release()

    representative_frame = encode_representative_frame(
        cv2, best_frame, best_frame_time_ms
    )
    payload = json.dumps(
        {
            "model": args.model,
            "observations": observations if observations is not None else [],
            "representative_frame": representative_frame,
        },
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
