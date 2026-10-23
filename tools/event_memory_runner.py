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
import logging
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import uuid
from typing import Any

from workflow_telemetry import WorkflowTrace, measured, stage


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
    parser.add_argument("--resume-retained", action="store_true")
    parser.add_argument("--capture-sessions", action="store_true")
    parser.add_argument("--reconnect-attempts", type=int, default=0, help="Explicit live-source retry budget, 0–8; requires session capture")
    return parser.parse_args()


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def redacted(source: str) -> str:
    if source.startswith(("http://", "https://", "rtsp://", "rtsps://")):
        return f"{source.split(':', 1)[0]}://***"
    return "<local-video>"


@measured('capture')
def record_network_source(ffmpeg: str, source: str, target: Path, seconds: float) -> None:
    policy = os.environ.get('VISN_MEDIA_POLICY', 'research-raw-v1')
    if policy == 'sanitized-v1':
        from privacy_media import PrivacyDetector, sanitize
        detector = PrivacyDetector(os.environ.get('VISN_PRIVACY_MODEL_DIR', '.retrieval/privacy-models'))
        sanitize(source, target, seconds, detector)
        return
    if policy != 'research-raw-v1':
        raise ValueError('unsupported_media_policy')
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

    # Raw MJPEG has no reliable presentation clock. Preserve camera arrival time.
    if '.mjpg' in source.lower() or '.mjpeg' in source.lower():
        input_options += ["-use_wallclock_as_timestamps", "1"]
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


@measured('observer_quality')
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


@measured('observer_signature')
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


@measured('observer_activity')
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


@measured('playback_derivative')
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
    # MP4 can contain MJPEG and still be unplayable in browsers. Only copy H.264.
    import cv2
    probe = cv2.VideoCapture(str(evidence))
    fourcc = int(probe.get(cv2.CAP_PROP_FOURCC))
    probe.release()
    codec = ''.join(chr((fourcc >> (8*i)) & 255) for i in range(4)).lower()
    if clip_mode == "copy" and codec in ("h264", "avc1"):
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
        [*common, "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-threads", str(threads), "-c:a", "aac", "-movflags", "+faststart", "-y", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    return transcoded.returncode == 0 and target.is_file() and target.stat().st_size > 0


def privacy_timing_notes(manifest):
    capture=manifest.get('capture')
    if not capture:
        return []
    notes=[f"Observed {capture['observed_frames']} frames; capture stopped because {capture['termination']}. Transport times are not verified scene capture times; continuity is not certified."]
    if capture['timestamp_jump_count']:
        notes.append(f"{capture['timestamp_jump_count']} intervals have timestamp spacing over {capture['timestamp_jump_threshold_ms']}ms; variable frame rate and missing observations cannot be distinguished.")
    return notes


@measured('observer_analysis')
def analyze(cv2: Any, np: Any, ffmpeg: str, evidence: Path, output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(evidence))
    if not capture.isOpened():
        raise RuntimeError("recorded evidence is not decodable")
    nominal_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if nominal_fps <= 0 or nominal_fps > 240:
        nominal_fps = 25.0
    next_sample_ms = 0.0
    sample_period_ms = 1000.0 / args.observer_fps
    frame_period_ms = 1000.0 / nominal_fps

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
            with stage('observer_decode_advance'):
                grabbed = capture.grab()
            if not grabbed:
                break
            source_time_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0)
            if source_time_ms <= 0:
                source_time_ms = frame_index * frame_period_ms
            if source_time_ms >= args.max_seconds * 1000:
                break
            frame_index += 1
            frames_consumed += 1
            last_time_ms = max(last_time_ms, source_time_ms)
            if source_time_ms + 0.01 < next_sample_ms:
                continue
            with stage('observer_frame_retrieval'):
                ok, frame = capture.retrieve()
            if not ok:
                break
            next_sample_ms = source_time_ms + sample_period_ms
            source_time_ms = round(source_time_ms)
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
    finally:
        capture.release()

    duration_ms = round(min(args.max_seconds * 1000, last_time_ms + frame_period_ms))
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
        "data_quality_notes": privacy_timing_notes(getattr(args, 'privacy_manifest', {})) + getattr(args, 'privacy_manifest', {}).get('clock_limitations', []) + ([
            "Sanitized-pixel integration profile: face/plate detection ran before persistence and observation; independent privacy/utility qualification remains pending."
        ] if os.environ.get('VISN_MEDIA_POLICY') == 'sanitized-v1' else [
            "Research raw profile: this recording is not privacy sanitized."
        ]) + [
            "V1 activity boundaries use sparse luma and histogram change; the encoded source remains available for verification.",
            f"Observer analysis retrieved {observer_frames} of {frames_consumed} consumed source frames.",
            clip_note,
        ],
    }


def analyze_capture_sessions(cv2, np, ffmpeg, output_dir, args):
    from privacy_media import PrivacyDetector, capture_sessions, verify_capture_sessions, verify_commit, digest
    if os.environ.get('VISN_MEDIA_POLICY') != 'sanitized-v1':
        raise ValueError('source_sessions_require_sanitized_profile')
    bundle = output_dir/'capture'
    if not args.resume_retained:
        detector = PrivacyDetector(os.environ.get('VISN_PRIVACY_MODEL_DIR', '.retrieval/privacy-models'))
        capture_sessions(args.source, bundle, args.max_seconds, detector, reconnect_attempts=args.reconnect_attempts)
    manifest = verify_capture_sessions(bundle, allow_partial=args.resume_retained)
    parent = output_dir/'sessions'
    if parent.is_symlink():
        raise ValueError('session_storage_symlink')
    parent.mkdir(exist_ok=True)
    results = []
    for index, session in enumerate(manifest['sessions']):
        identifier = str(uuid.UUID(session['session_id']))
        directory = parent/identifier
        if directory.is_symlink():
            raise ValueError('session_storage_symlink')
        directory.mkdir(exist_ok=True)
        source = bundle/session['file']
        evidence = directory/'source.mkv'
        for suffix in ('', '.privacy.json', '.frames.jsonl'):
            original, target = Path(str(source)+suffix), Path(str(evidence)+suffix)
            if target.is_symlink():
                raise ValueError('session_storage_symlink')
            if target.exists():
                if digest(target) != digest(original):
                    raise ValueError('retained_session_artifact_changed')
            else:
                os.link(original, target)
        args.privacy_manifest = verify_commit(evidence)
        result = analyze(cv2, np, ffmpeg, evidence, directory, args)
        result['storage_session_id'] = identifier
        result['capture_extent'] = 'finished' if manifest['state'] == 'completed' else 'retained_prefix'
        result['data_quality_notes'].append(f"Source session {index+1}: {session['start_reason']}. Independent source-relative clock; no elapsed time between sessions is inferred. Capture journal state: {manifest['state']}.")
        results.append(result)
    return dict(sessions=results)


def main() -> None:
    from worker_lifeline import start
    start()
    args = parse_args()
    if args.source == "-":
        args.source = sys.stdin.read().strip()
    if not args.source:
        fail("a video source is required")
    if not 0 <= args.reconnect_attempts <= 8:
        fail("reconnect attempts must be between zero and eight")
    if args.reconnect_attempts and (not args.capture_sessions or os.environ.get("VISN_MEDIA_POLICY") != "sanitized-v1"):
        fail("reconnect attempts require sanitized source-session capture")
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

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with WorkflowTrace(os.environ.get('VISN_TRACE_ID'), 'ingest',
                       enabled=os.environ.get('VISN_WORKFLOW_TELEMETRY','').lower() in ('1','true')):
        try:
            import cv2  # type: ignore
            import imageio_ffmpeg  # type: ignore
            import numpy as np  # type: ignore

            cv2.setNumThreads(args.threads)
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            logging.info("Recording bounded camera interval: %.1fs", args.max_seconds)
            if args.capture_sessions:
                result = analyze_capture_sessions(cv2, np, ffmpeg, output_dir, args)
            elif args.resume_retained:
                if os.environ.get('VISN_MEDIA_POLICY') != 'sanitized-v1':
                    raise ValueError('retained_recovery_requires_sanitized_profile')
                from privacy_media import verify_commit
                verify_commit(evidence)
                logging.info('Resuming verified retained evidence without opening the camera stream')
            else:
                record_network_source(ffmpeg, args.source, evidence, args.max_seconds)
            if not args.capture_sessions and os.environ.get('VISN_MEDIA_POLICY') == 'sanitized-v1':
                from privacy_media import verify_commit
                args.privacy_manifest = verify_commit(evidence)
            if not args.capture_sessions:
                logging.info("Recording retained; analyzing intervals and generating browser clips")
                result = analyze(cv2, np, ffmpeg, evidence, output_dir, args)
                logging.info("Indexed %d intervals over %dms", len(result['events']), result['duration_ms'])
        except ImportError as error:
            fail(f"missing memory-runner dependency: {error}; install tools/requirements-yolo.txt")
        except Exception as error:
            fail(str(error).replace(args.source, redacted(args.source)))

        with stage('observer_result_serialization'):
            encoded=json.dumps(result, separators=(',', ':'), allow_nan=False)
        sys.stdout.write(f"{OUTPUT_PREFIX}{encoded}\n")


if __name__ == "__main__":
    main()
