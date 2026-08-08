#!/usr/bin/env python3
"""Bounded, retrieval-first event-memory adapter.

The runner records the authorized network interval as an encoded Matroska artifact,
decodes only at the requested observer cadence, derives adaptive temporal boundaries,
and emits compact event metadata plus representative JPEGs. It intentionally performs
no YOLO or large-model inference.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from typing import Any


OUTPUT_PREFIX = "VISN_MEMORY_JSON:"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="-")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--observer-fps", type=float, default=1.0)
    parser.add_argument("--max-seconds", type=float, default=120.0)
    parser.add_argument("--max-events", type=int, default=48)
    parser.add_argument("--minimum-event-seconds", type=float, default=2.0)
    parser.add_argument("--maximum-event-seconds", type=float, default=15.0)
    parser.add_argument("--clip-mode", choices=("copy", "transcode", "reference"), default="copy")
    parser.add_argument("--threads", type=int, default=1)
    return parser.parse_args()


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def redacted(source: str) -> str:
    if source.startswith(("http://", "https://", "rtsp://", "rtsps://")):
        return f"{source.split(':', 1)[0]}://***"
    return "<local-video>"


def record_network_source(ffmpeg: str, source: str, target: Path, seconds: float) -> None:
    network = source.startswith(("http://", "https://", "rtsp://", "rtsps://"))
    if not network:
        try:
            target.unlink(missing_ok=True)
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
        return

    input_options = ["-rw_timeout", "10000000"]
    if source.startswith(("http://", "https://")):
        input_options += [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "2",
            "-user_agent", "Visn-Memory-V1/0.1",
        ]
    elif source.startswith(("rtsp://", "rtsps://")):
        input_options += ["-rtsp_transport", "tcp"]

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        *input_options,
        "-i", source,
        "-t", str(seconds),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c", "copy",
        "-y", str(target),
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=max(30.0, seconds + 25.0),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"evidence recorder exceeded its bounded deadline for {redacted(source)}") from error
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        detail = completed.stderr.decode("utf-8", errors="replace").replace(source, redacted(source))
        raise RuntimeError(f"could not record {redacted(source)}: {detail.strip()[-1200:]}")


def frame_quality(cv2: Any, frame: Any) -> float:
    height, width = frame.shape[:2]
    scale = min(1.0, 480.0 / max(height, width))
    if scale < 1.0:
        frame = cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    exposure = max(0.0, 1.0 - abs(brightness - 127.5) / 127.5)
    return max(0.0, min(1.0, 0.45 * min(sharpness / 350.0, 1.0) + 0.35 * min(contrast / 70.0, 1.0) + 0.20 * exposure))


def visual_signature(cv2: Any, np: Any, frame: Any) -> list[float]:
    sample = cv2.resize(frame, (96, 64), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)
    parts = [cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).flatten()]
    parts.extend(cv2.calcHist([sample], [channel], None, [8], [0, 256]).flatten() for channel in range(3))
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 160)
    parts.append(np.array([gray.mean(), gray.std(), edges.mean(), cv2.Laplacian(gray, cv2.CV_32F).var()], dtype=np.float32))
    signature = np.concatenate(parts).astype(np.float32)
    norm = float(np.linalg.norm(signature))
    return (signature / norm).tolist() if math.isfinite(norm) and norm > 1e-8 else []


def activity_score(cv2: Any, np: Any, previous: Any, previous_hist: Any, current: Any) -> tuple[float, Any]:
    current_hist = cv2.calcHist([current], [0], None, [32], [0, 256])
    cv2.normalize(current_hist, current_hist)
    if previous is None:
        return 0.0, current_hist
    delta = float(np.mean(cv2.absdiff(previous, current))) / 255.0
    histogram_change = float(cv2.compareHist(previous_hist, current_hist, cv2.HISTCMP_BHATTACHARYYA))
    score = max(0.0, min(1.0, 0.68 * min(delta * 5.0, 1.0) + 0.32 * histogram_change))
    return score, current_hist


def adaptive_threshold(history: Any) -> float:
    if len(history) < 4:
        return 0.18
    window = list(history)
    median = statistics.median(window)
    deviations = [abs(value - median) for value in window]
    mad = statistics.median(deviations) if deviations else 0.0
    return max(0.10, min(0.55, median + max(0.05, 3.5 * mad)))


def encode_frame(cv2: Any, frame: Any, frame_time_ms: int, path: Path) -> dict[str, Any]:
    height, width = frame.shape[:2]
    scale = min(1.0, 960.0 / max(height, width))
    if scale < 1.0:
        frame = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        raise RuntimeError("could not encode an event representative frame")
    encoded_bytes = encoded.tobytes()
    path.write_bytes(encoded_bytes)
    encoded_height, encoded_width = frame.shape[:2]
    return {
        "media_type": "image/jpeg",
        "frame_time_ms": frame_time_ms,
        "width": encoded_width,
        "height": encoded_height,
    }


def extract_clip(
    ffmpeg: str,
    evidence: Path,
    target: Path,
    start_ms: int,
    end_ms: int,
    clip_mode: str,
    threads: int,
) -> bool:
    if clip_mode == "reference":
        return False
    duration = max(0.2, (end_ms - start_ms) / 1000.0)
    common = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_ms / 1000.0:.3f}",
        "-i", str(evidence),
        "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
    ]
    if clip_mode == "copy":
        copied = subprocess.run(
            [*common, "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-y", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if copied.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            return True
        target.unlink(missing_ok=True)

    transcoded = subprocess.run(
        [*common, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-threads", str(threads), "-c:a", "aac", "-movflags", "+faststart", "-y", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    return transcoded.returncode == 0 and target.is_file() and target.stat().st_size > 0


def analyze(cv2: Any, np: Any, ffmpeg: str, evidence: Path, output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(evidence))
    if not capture.isOpened():
        raise RuntimeError("recorded evidence is not decodable")
    nominal_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if nominal_fps <= 0 or nominal_fps > 240:
        nominal_fps = 25.0
    stride = max(1, round(nominal_fps / args.observer_fps))
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    reported_duration_ms = int(total_frames * 1000.0 / nominal_fps) if total_frames > 0 else 0

    events: list[dict[str, Any]] = []
    activity_history: Any = deque(maxlen=60)
    previous_gray = None
    previous_hist = None
    frame_index = 0
    frames_consumed = 0
    observer_frames = 0
    event_start_ms = 0
    event_scores: list[float] = []
    best_frame = None
    best_quality = -1.0
    best_time_ms = 0
    best_signature: list[float] = []
    last_time_ms = 0
    current_boundary_reason = "stream_start"

    def finish_event(end_ms: int, reason: str) -> None:
        nonlocal event_start_ms, event_scores, best_frame, best_quality, best_time_ms, best_signature, current_boundary_reason
        if best_frame is None or end_ms <= event_start_ms or len(events) >= args.max_events:
            return
        index = len(events)
        thumbnail_name = f"event_{index:04d}.jpg"
        clip_name = f"event_{index:04d}.mp4"
        representative = encode_frame(cv2, best_frame, best_time_ms, output_dir / thumbnail_name)
        clip_ok = extract_clip(
            ffmpeg,
            evidence,
            output_dir / clip_name,
            event_start_ms,
            end_ms,
            args.clip_mode,
            args.threads,
        )
        scores = event_scores or [0.0]
        events.append({
            "start_ms": event_start_ms,
            "end_ms": end_ms,
            "activity_mean": float(sum(scores) / len(scores)),
            "activity_peak": float(max(scores)),
            "quality": float(max(0.0, best_quality)),
            "boundary_reason": reason if events else current_boundary_reason,
            "thumbnail_file": thumbnail_name,
            "clip_file": clip_name if clip_ok else "",
            "representative_frame": representative,
            "visual_signature": best_signature,
        })
        event_start_ms = end_ms
        event_scores = []
        best_frame = None
        best_quality = -1.0
        best_time_ms = end_ms
        best_signature = []
        current_boundary_reason = reason

    try:
        while True:
            if frame_index % stride != 0:
                ok = capture.grab()
                if not ok:
                    break
                frame_index += 1
                frames_consumed += 1
                continue
            ok, frame = capture.read()
            if not ok:
                break
            frames_consumed += 1
            source_time_ms = int(capture.get(cv2.CAP_PROP_POS_MSEC) or 0)
            if source_time_ms <= 0:
                source_time_ms = int(frame_index * 1000.0 / nominal_fps)
            last_time_ms = max(last_time_ms, source_time_ms)
            small = cv2.resize(frame, (160, 96), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            activity, current_hist = activity_score(
                cv2, np, previous_gray, previous_hist, gray
            )
            previous_gray = gray
            previous_hist = current_hist
            threshold = adaptive_threshold(activity_history)
            elapsed = (source_time_ms - event_start_ms) / 1000.0
            novelty_boundary = activity >= threshold and elapsed >= args.minimum_event_seconds
            duration_boundary = elapsed >= args.maximum_event_seconds
            if (novelty_boundary or duration_boundary) and len(events) + 1 < args.max_events:
                finish_event(source_time_ms, "novelty" if novelty_boundary else "maximum_duration")
            quality = frame_quality(cv2, frame)
            if quality > best_quality:
                height, width = frame.shape[:2]
                scale = min(1.0, 960.0 / max(height, width))
                best_frame = (
                    cv2.resize(
                        frame,
                        (max(1, round(width * scale)), max(1, round(height * scale))),
                        interpolation=cv2.INTER_AREA,
                    )
                    if scale < 1.0
                    else frame.copy()
                )
                best_quality = quality
                best_time_ms = source_time_ms
                best_signature = visual_signature(cv2, np, frame)
            event_scores.append(activity)
            activity_history.append(activity)
            observer_frames += 1
            frame_index += 1
    finally:
        capture.release()

    duration_ms = max(last_time_ms + round(1000.0 / args.observer_fps), reported_duration_ms)
    finish_event(duration_ms, "stream_end")
    if not events:
        raise RuntimeError("no observer frames were decoded from the recorded evidence")
    clip_note = {
        "copy": "Event clips use stream copy when compatible and fall back to H.264 only when required.",
        "transcode": "Event clips are explicitly transcoded to H.264 for browser compatibility.",
        "reference": "Event clip URLs reference the retained source evidence; no duplicate clips were materialized.",
    }[args.clip_mode]
    return {
        "evidence_file": evidence.name,
        "duration_ms": duration_ms,
        "frames_decoded": frames_consumed,
        "events": events,
        "data_quality_notes": [
            "V1 activity boundaries use sparse luma and histogram change; the encoded source remains available for verification.",
            f"Observer analysis retrieved {observer_frames} of {frames_consumed} consumed source frames.",
            clip_note,
        ],
    }


def main() -> None:
    args = parse_args()
    if args.source == "-":
        args.source = sys.stdin.read().strip()
    if not args.source:
        fail("a video source is required")
    if (
        not math.isfinite(args.observer_fps)
        or args.observer_fps <= 0
        or not math.isfinite(args.max_seconds)
        or args.max_seconds <= 0
        or args.max_events <= 0
        or args.threads <= 0
        or not math.isfinite(args.minimum_event_seconds)
        or args.minimum_event_seconds <= 0
        or not math.isfinite(args.maximum_event_seconds)
        or args.maximum_event_seconds < args.minimum_event_seconds
    ):
        fail(
            "observer FPS, duration, event limits and thread count must be finite and positive; "
            "maximum event duration must not be below the minimum"
        )
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = output_dir / "source.mkv"

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
        import imageio_ffmpeg  # type: ignore
        import numpy as np  # type: ignore

        cv2.setNumThreads(args.threads)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        record_network_source(ffmpeg, args.source, evidence, args.max_seconds)
        result = analyze(cv2, np, ffmpeg, evidence, output_dir, args)
    except ImportError as error:
        fail(f"missing memory-runner dependency: {error}; install tools/requirements-yolo.txt")
    except Exception as error:
        fail(str(error).replace(args.source, redacted(args.source)))

    sys.stdout.write(f"{OUTPUT_PREFIX}{json.dumps(result, separators=(',', ':'), allow_nan=False)}\n")


if __name__ == "__main__":
    main()
