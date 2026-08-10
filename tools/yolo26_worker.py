#!/usr/bin/env python3
"""Persistent, bounded multi-camera YOLO26 worker.

The Rust service sends newline-delimited JSON commands on stdin. The worker loads
one Ultralytics model, decodes each camera independently, batches frames that are
ready at approximately the same time, and keeps a dedicated ByteTrack instance
for every request. Only framed protocol messages are written to stdout; model and
decoder diagnostics stay on stderr.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
import json
import math
import os
import queue
import sys
import threading
import time
from typing import Any

import yolo26_runner as runner


OUTPUT_PREFIX = "VISN_WORKER_JSON:"
MAX_COMMAND_BYTES = 256 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="yolo26s.pt")
    parser.add_argument("--device", default=None)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--max-batch-size", type=int, default=4)
    parser.add_argument("--batch-wait-ms", type=float, default=12.0)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--tracker", default="bytetrack.yaml")
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="Run one batch-1 synthetic inference before accepting cameras.",
    )
    return parser.parse_args()


def emit_lock_guarded(lock: threading.Lock, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    with lock:
        sys.stdout.write(f"{OUTPUT_PREFIX}{encoded}\n")
        sys.stdout.flush()


def validate_startup_args(args: argparse.Namespace) -> None:
    if args.threads <= 0:
        raise ValueError("--threads must be greater than zero")
    if args.max_sessions <= 0:
        raise ValueError("--max-sessions must be greater than zero")
    if args.max_batch_size <= 0 or args.max_batch_size > args.max_sessions:
        raise ValueError("--max-batch-size must be in [1, --max-sessions]")
    if not math.isfinite(args.batch_wait_ms) or not 0 <= args.batch_wait_ms <= 1000:
        raise ValueError("--batch-wait-ms must be finite and in [0, 1000]")
    if args.imgsz <= 0:
        raise ValueError("--imgsz must be greater than zero")


def configure_threads(thread_count: int) -> None:
    value = str(thread_count)
    for variable in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[variable] = value


@dataclass
class FramePacket:
    frame: Any
    source_time_ms: int


class CameraSession:
    """One capture and tracker with a single bounded pending-frame slot."""

    def __init__(
        self,
        request: dict[str, Any],
        cv2: Any,
        np: Any,
        imageio_ffmpeg: Any,
        tracker: Any,
        frame_ready: threading.Event,
    ) -> None:
        self.request_id = request["request_id"]
        self.source = request["source"]
        self.requested_fps = float(request["fps"])
        self.max_seconds = float(request["max_seconds"])
        self.confidence = float(request.get("confidence", 0.25))
        self.appearance_mode = request.get("appearance_mode", "off")
        self.appearance_interval_ms = round(
            float(request.get("appearance_interval_secs", 1.0)) * 1000.0
        )
        self.cv2 = cv2
        self.np = np
        self.imageio_ffmpeg = imageio_ffmpeg
        self.tracker = tracker
        self.frame_ready = frame_ready
        self.condition = threading.Condition()
        self.pending: FramePacket | None = None
        self.capture_done = False
        self.error: str | None = None
        self.cancelled = False
        self.best_frame = None
        self.best_frame_time_ms = 0
        self.best_frame_quality = float("-inf")
        self.appearance_sample_times: dict[str, int] = {}
        self.thread = threading.Thread(
            target=self._capture_loop,
            name=f"capture-{self.request_id[:8]}",
            daemon=True,
        )

    @property
    def is_http(self) -> bool:
        return self.source.startswith(("http://", "https://"))

    @property
    def is_network(self) -> bool:
        return self.is_http or self.source.startswith(("rtsp://", "rtsps://"))

    def start(self) -> None:
        self.thread.start()

    def cancel(self) -> None:
        with self.condition:
            self.cancelled = True
            self.pending = None
            self.condition.notify_all()
        self.frame_ready.set()

    def take_pending(self) -> FramePacket | None:
        with self.condition:
            packet = self.pending
            self.pending = None
            if packet is not None:
                self.condition.notify_all()
            return packet

    def snapshot_state(self) -> tuple[bool, str | None, bool, bool]:
        with self.condition:
            return (
                self.capture_done,
                self.error,
                self.pending is not None,
                self.cancelled,
            )

    def _offer(self, packet: FramePacket) -> bool:
        with self.condition:
            if self.is_network:
                if self.cancelled:
                    return False
                # Live sources favor the freshest frame. At most one decoded
                # full-resolution frame waits behind inference.
                self.pending = packet
            else:
                # Uploaded files are deterministic evidence: backpressure the
                # decoder instead of dropping sampled frames.
                while self.pending is not None and not self.cancelled:
                    self.condition.wait(timeout=0.1)
                if self.cancelled:
                    return False
                self.pending = packet
            self.condition.notify_all()
        self.frame_ready.set()
        return True

    def _finish(self, error: Exception | str | None = None) -> None:
        with self.condition:
            if error is not None:
                rendered = str(error)
                self.error = runner.redact_message(rendered, self.source)
                self.pending = None
            self.capture_done = True
            self.condition.notify_all()
        self.frame_ready.set()

    def _open_capture(self, remaining_seconds: float) -> Any:
        if self.is_http:
            if self.imageio_ffmpeg is None:
                raise RuntimeError("imageio-ffmpeg is required for HTTP streams")
            return runner.FfmpegHttpCapture(
                self.imageio_ffmpeg,
                self.np,
                self.source,
                remaining_seconds,
                self.requested_fps,
            )
        return runner.open_cv_capture(
            self.cv2,
            self.source,
            self.source.startswith(("rtsp://", "rtsps://")),
        )

    def _capture_loop(self) -> None:
        wall_start = time.monotonic()
        deadline = wall_start + self.max_seconds
        frame_index = 0
        capture = None
        try:
            while not self.cancelled:
                remaining = deadline - time.monotonic()
                if self.is_network and remaining <= 0:
                    break
                capture = self._open_capture(max(0.1, remaining))
                opened = capture.is_opened() if self.is_http else capture.isOpened()
                if not opened:
                    raise RuntimeError(
                        f"Could not open video source: {runner.redact_source(self.source)}"
                    )

                nominal_fps = (
                    capture.fps()
                    if self.is_http
                    else float(capture.get(self.cv2.CAP_PROP_FPS) or 0.0)
                )
                if nominal_fps <= 0 or nominal_fps > 240:
                    nominal_fps = 25.0
                stride = max(1, round(nominal_fps / self.requested_fps))

                reconnect = False
                while not self.cancelled:
                    elapsed = time.monotonic() - wall_start
                    if self.is_network and elapsed >= self.max_seconds:
                        break
                    process_frame = frame_index % stride == 0
                    if process_frame:
                        ok, frame = capture.read()
                    else:
                        ok = capture.grab()
                        frame = None
                    if not ok:
                        reconnect = self.is_network and time.monotonic() < deadline
                        break

                    elapsed = time.monotonic() - wall_start
                    if self.is_network:
                        source_time_ms = int(elapsed * 1000)
                    else:
                        source_time_ms = int(
                            capture.get(self.cv2.CAP_PROP_POS_MSEC) or 0
                        )
                    if source_time_ms <= 0:
                        source_time_ms = int(frame_index * 1000.0 / nominal_fps)
                    if source_time_ms > self.max_seconds * 1000:
                        reconnect = False
                        break
                    frame_index += 1
                    if not process_frame:
                        continue
                    if not self._offer(FramePacket(frame, source_time_ms)):
                        break

                capture.release()
                capture = None
                if not reconnect or self.cancelled:
                    break
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                error = RuntimeError(str(error) or type(error).__name__)
            self._finish(error)
            return
        finally:
            if capture is not None:
                capture.release()
        self._finish()


def validate_analyze_command(command: dict[str, Any]) -> None:
    required_strings = ("request_id", "source")
    for name in required_strings:
        if not isinstance(command.get(name), str) or not command[name].strip():
            raise ValueError(f"{name} must be a non-empty string")
    if len(command["request_id"]) > 128 or len(command["source"]) > 16_384:
        raise ValueError("request_id or source exceeds its length limit")
    for name in ("fps", "max_seconds"):
        value = float(command.get(name, 0))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    confidence = float(command.get("confidence", 0.25))
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be finite and in [0, 1]")
    appearance_interval = float(command.get("appearance_interval_secs", 1.0))
    if not math.isfinite(appearance_interval) or appearance_interval < 0:
        raise ValueError("appearance_interval_secs must be finite and non-negative")
    if command.get("appearance_mode", "off") not in {"off", "person", "all"}:
        raise ValueError("appearance_mode must be off, person, or all")


def command_reader(commands: queue.Queue[dict[str, Any]]) -> None:
    try:
        for raw_line in sys.stdin.buffer:
            if len(raw_line) > MAX_COMMAND_BYTES:
                commands.put(
                    {
                        "type": "invalid",
                        "message": f"command exceeds {MAX_COMMAND_BYTES} bytes",
                    }
                )
                continue
            try:
                decoded = json.loads(raw_line)
                if not isinstance(decoded, dict):
                    raise ValueError("command must be a JSON object")
                commands.put(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                commands.put({"type": "invalid", "message": str(error)})
    finally:
        commands.put({"type": "shutdown"})


def main() -> None:
    args = parse_args()
    try:
        validate_startup_args(args)
    except ValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(2)
    configure_threads(args.threads)

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from ultralytics import YOLO  # type: ignore
        from ultralytics.trackers.byte_tracker import BYTETracker  # type: ignore
        from ultralytics.utils import IterableSimpleNamespace, YAML  # type: ignore
        from ultralytics.utils.checks import check_yaml  # type: ignore
    except ImportError as error:
        print(
            f"Missing YOLO worker dependency: {error}. Install tools/requirements-yolo.txt",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        import imageio_ffmpeg  # type: ignore
    except ImportError:
        imageio_ffmpeg = None

    cv2.setNumThreads(args.threads)
    torch.set_num_threads(args.threads)
    try:
        torch.set_num_interop_threads(args.threads)
    except RuntimeError:
        # A parent embedding may have initialized the inter-op pool already.
        pass

    class CameraBYTETracker(BYTETracker):
        @staticmethod
        def reset_id() -> None:
            # Ultralytics' tracker ID counter is process-global. Resetting it
            # whenever another camera starts can reuse IDs inside an older
            # camera session, so the shared worker keeps it monotonic.
            return None

    tracker_config = IterableSimpleNamespace(**YAML.load(check_yaml(args.tracker)))
    tracker_config.device = args.device or "cpu"
    with redirect_stdout(sys.stderr):
        model = YOLO(args.model)
        if args.warmup:
            model.predict(
                np.zeros((args.imgsz, args.imgsz, 3), dtype=np.uint8),
                verbose=False,
                conf=0.25,
                device=args.device,
                imgsz=args.imgsz,
            )

    emit_lock = threading.Lock()
    commands: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
    frame_ready = threading.Event()
    sessions: dict[str, CameraSession] = {}
    shutting_down = False

    reader_thread = threading.Thread(
        target=command_reader,
        args=(commands,),
        name="worker-command-reader",
        daemon=True,
    )
    reader_thread.start()
    emit_lock_guarded(
        emit_lock,
        {
            "type": "ready",
            "model": args.model,
            "max_sessions": args.max_sessions,
            "max_batch_size": args.max_batch_size,
        },
    )

    def emit_error(request_id: Any, message: str) -> None:
        emit_lock_guarded(
            emit_lock,
            {"type": "error", "request_id": request_id, "message": message},
        )

    def process_commands() -> None:
        nonlocal shutting_down
        while True:
            try:
                command = commands.get_nowait()
            except queue.Empty:
                return
            command_type = command.get("type")
            request_id = command.get("request_id")
            if command_type == "analyze":
                try:
                    validate_analyze_command(command)
                    if request_id in sessions:
                        raise ValueError("request_id is already active")
                    if len(sessions) >= args.max_sessions:
                        raise ValueError("persistent detector session limit reached")
                    tracker = CameraBYTETracker(args=tracker_config)
                    session = CameraSession(
                        command,
                        cv2,
                        np,
                        imageio_ffmpeg,
                        tracker,
                        frame_ready,
                    )
                    sessions[request_id] = session
                    session.start()
                except (TypeError, ValueError) as error:
                    emit_error(request_id, str(error))
            elif command_type == "cancel":
                session = sessions.get(str(request_id))
                if session is not None:
                    session.cancel()
            elif command_type == "shutdown":
                shutting_down = True
                for session in sessions.values():
                    session.cancel()
                sessions.clear()
                return
            else:
                emit_error(request_id, command.get("message", "unknown command type"))

    def take_batch() -> list[tuple[CameraSession, FramePacket]]:
        selected: list[tuple[CameraSession, FramePacket]] = []
        selected_ids: set[str] = set()

        def take_ready() -> None:
            for session in list(sessions.values()):
                if len(selected) >= args.max_batch_size:
                    return
                if session.request_id in selected_ids:
                    continue
                if session.cancelled:
                    continue
                packet = session.take_pending()
                if packet is not None:
                    selected.append((session, packet))
                    selected_ids.add(session.request_id)

        take_ready()
        if not selected or len(selected) >= args.max_batch_size:
            return selected
        deadline = time.monotonic() + args.batch_wait_ms / 1000.0
        while len(selected) < args.max_batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            frame_ready.wait(timeout=remaining)
            frame_ready.clear()
            process_commands()
            take_ready()
        return selected

    def observations_for(
        session: CameraSession, packet: FramePacket, result: Any
    ) -> list[dict[str, Any]]:
        frame = packet.frame
        frame_time_ms = packet.source_time_ms
        height, width = frame.shape[:2]
        quality = runner.representative_frame_quality(cv2, frame)
        if quality > session.best_frame_quality:
            scale = min(1.0, 960.0 / max(height, width))
            session.best_frame = (
                cv2.resize(
                    frame,
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
                if scale < 1.0
                else frame.copy()
            )
            session.best_frame_time_ms = frame_time_ms
            session.best_frame_quality = quality

        boxes = result.boxes
        if boxes is None:
            return []
        detections = boxes[boxes.conf >= session.confidence].cpu().numpy()
        tracks = session.tracker.update(detections, frame)
        observations: list[dict[str, Any]] = []
        for row in tracks:
            x1, y1, x2, y2 = (float(value) for value in row[:4])
            track_id = str(int(row[4]))
            confidence = float(row[5])
            class_id = int(row[6])
            if confidence < session.confidence:
                continue
            x1 = min(max(0.0, x1), float(width))
            y1 = min(max(0.0, y1), float(height))
            x2 = min(max(x1, x2), float(width))
            y2 = min(max(y1, y2), float(height))
            if x2 <= x1 or y2 <= y1:
                continue
            class_name = str(result.names[class_id])
            appearance = None
            appearance_enabled = session.appearance_mode == "all" or (
                session.appearance_mode == "person" and class_name == "person"
            )
            if appearance_enabled:
                previous = session.appearance_sample_times.get(track_id)
                sample_due = (
                    previous is None
                    or frame_time_ms < previous
                    or frame_time_ms - previous >= session.appearance_interval_ms
                )
                if sample_due:
                    crop = frame[int(y1) : int(y2), int(x1) : int(x2)]
                    appearance = runner.appearance_descriptor(cv2, np, crop)
                    if appearance is not None:
                        session.appearance_sample_times[track_id] = frame_time_ms
            observations.append(
                {
                    "frame_time_ms": frame_time_ms,
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
        return observations

    while not shutting_down:
        process_commands()
        if shutting_down:
            break

        frame_ready.wait(timeout=0.05)
        frame_ready.clear()
        process_commands()
        batch = take_batch()
        if batch:
            try:
                min_confidence = min(session.confidence for session, _ in batch)
                with redirect_stdout(sys.stderr):
                    results = model.predict(
                        [packet.frame for _, packet in batch],
                        verbose=False,
                        conf=min_confidence,
                        device=args.device,
                        imgsz=args.imgsz,
                    )
                if len(results) != len(batch):
                    raise RuntimeError("detector returned an unexpected batch size")
                for (session, packet), result in zip(batch, results):
                    observations = observations_for(session, packet, result)
                    emit_lock_guarded(
                        emit_lock,
                        {
                            "type": "observations",
                            "request_id": session.request_id,
                            "observations": observations,
                        },
                    )
            except Exception as error:
                message = f"batched inference failed: {type(error).__name__}: {error}"
                for session, _ in batch:
                    with session.condition:
                        session.error = message
                        session.capture_done = True
                        session.pending = None
                        session.cancelled = True
                        session.condition.notify_all()

        for request_id, session in list(sessions.items()):
            done, error, has_pending, cancelled = session.snapshot_state()
            if error is not None:
                emit_error(request_id, error)
                session.cancel()
                sessions.pop(request_id, None)
            elif cancelled and done:
                sessions.pop(request_id, None)
            elif done and not has_pending:
                representative = runner.encode_representative_frame(
                    cv2, session.best_frame, session.best_frame_time_ms
                )
                emit_lock_guarded(
                    emit_lock,
                    {
                        "type": "complete",
                        "request_id": request_id,
                        "result": {
                            "model": args.model,
                            "observations": [],
                            "representative_frame": representative,
                        },
                    },
                )
                sessions.pop(request_id, None)


if __name__ == "__main__":
    main()
