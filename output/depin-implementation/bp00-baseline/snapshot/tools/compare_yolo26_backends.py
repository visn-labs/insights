#!/usr/bin/env python3
"""Compare YOLO26 backend artifacts on one identical local frame set.

This is a release-screening tool, not a replacement for labeled validation. It
decodes a local image/video once, runs every model in a fresh subprocess, reports
latency and peak RSS, then measures each candidate's agreement with the baseline.
Use ``--validation-data`` to additionally collect true labeled mAP metrics.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from export_yolo26 import atomic_write_json, hash_artifact


WORKER_MARKER = "VISN_YOLO26_BENCHMARK_JSON:"
IMAGE_SUFFIXES = frozenset({".bmp", ".dib", ".jpeg", ".jpg", ".jpe", ".jp2", ".png", ".webp", ".avif"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark candidate YOLO26 artifacts on the exact same local image/video frames.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--baseline", type=Path, required=True, help="Local reference .pt or exported artifact")
    parser.add_argument(
        "--candidate",
        type=Path,
        action="append",
        required=True,
        help="Local candidate artifact; repeat for multiple backends/precisions",
    )
    parser.add_argument("--source", type=Path, required=True, help="Local image or video used for identical-frame tests")
    parser.add_argument("--imgsz", type=int, nargs="+", default=[640], metavar="PX")
    parser.add_argument("--device", help="Ultralytics device for every subprocess, e.g. cpu, mps, or 0")
    parser.add_argument("--confidence", type=float, default=0.25, help="Detection confidence threshold")
    parser.add_argument("--iou", type=float, default=0.7, help="Inference NMS IoU threshold when applicable")
    parser.add_argument("--match-iou", type=float, default=0.5, help="Class-aware IoU threshold for agreement matching")
    parser.add_argument("--sample-fps", type=float, default=1.0, help="Video sampling frequency")
    parser.add_argument("--max-frames", type=int, default=60, help="Maximum lossless PNG frames materialized once")
    parser.add_argument("--warmup", type=int, default=3, help="Untimed predictions before measurements")
    parser.add_argument("--validation-data", help="Optional labeled Ultralytics dataset YAML for true mAP")
    parser.add_argument("--validation-split", default="val", choices=["train", "val", "test"])
    parser.add_argument("--timeout", type=float, default=1800.0, help="Maximum seconds allowed per model subprocess")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/benchmarks/yolo26-comparison.json"),
        help="Atomic JSON report destination",
    )
    return parser


def build_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--frames-manifest", type=Path, required=True)
    parser.add_argument("--imgsz", type=int, nargs="+", required=True)
    parser.add_argument("--device")
    parser.add_argument("--confidence", type=float, required=True)
    parser.add_argument("--iou", type=float, required=True)
    parser.add_argument("--warmup", type=int, required=True)
    parser.add_argument("--validation-data")
    parser.add_argument("--validation-split", default="val")
    return parser


def validate_public_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    models = [args.baseline, *args.candidate]
    missing = [str(path) for path in models if not path.expanduser().exists()]
    if missing:
        parser.error(f"model artifact(s) do not exist: {', '.join(missing)}")
    if not args.source.expanduser().is_file():
        parser.error("--source must be an existing local image or video file")
    if len(args.imgsz) not in {1, 2} or any(value <= 0 for value in args.imgsz):
        parser.error("--imgsz requires one or two positive integers")
    if not 0 <= args.confidence <= 1 or not 0 <= args.iou <= 1 or not 0 < args.match_iou <= 1:
        parser.error("--confidence and --iou must be in [0, 1]; --match-iou must be in (0, 1]")
    if args.sample_fps <= 0 or args.max_frames <= 0 or args.warmup < 0 or args.timeout <= 0:
        parser.error("sampling, timeout and frame limits must be positive; --warmup may be zero")
    if args.validation_data and not Path(args.validation_data).expanduser().is_file():
        parser.error("--validation-data must be an existing local dataset YAML")


def _import_cv2() -> Any:
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError(
            "OpenCV is unavailable. Run this tool from the Python 3.11+ export/benchmark environment."
        ) from error
    return cv2


def materialize_frames(source: Path, destination: Path, sample_fps: float, max_frames: int) -> dict[str, Any]:
    """Decode the source once and retain exact, lossless inputs for all models."""

    cv2 = _import_cv2()
    destination.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    suffix = source.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"OpenCV could not decode image: {source}")
        frame_path = destination / "frame_000000.png"
        if not cv2.imwrite(str(frame_path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            raise RuntimeError(f"could not write materialized frame: {frame_path}")
        selected.append({"sample_index": 0, "source_frame": 0, "timestamp_seconds": 0.0, "path": str(frame_path)})
        return {
            "kind": "image",
            "source_fps": None,
            "decoded_frames": 1,
            "frames": selected,
        }

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {source}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    if not math.isfinite(source_fps) or source_fps <= 0:
        source_fps = 0.0
    sample_stride = max(1, round(source_fps / sample_fps)) if source_fps else 1
    decoded_frames = 0
    try:
        while len(selected) < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            source_index = decoded_frames
            decoded_frames += 1
            if source_index % sample_stride:
                continue
            timestamp_seconds = source_index / source_fps if source_fps else float(
                capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            )
            sample_index = len(selected)
            frame_path = destination / f"frame_{sample_index:06d}.png"
            if not cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise RuntimeError(f"could not write materialized frame: {frame_path}")
            selected.append(
                {
                    "sample_index": sample_index,
                    "source_frame": source_index,
                    "timestamp_seconds": timestamp_seconds,
                    "path": str(frame_path),
                }
            )
    finally:
        capture.release()
    if not selected:
        raise RuntimeError(f"video yielded no decodable frames: {source}")
    return {
        "kind": "video",
        "source_fps": source_fps or None,
        "effective_sample_fps": source_fps / sample_stride if source_fps else None,
        "sample_stride": sample_stride,
        "decoded_frames": decoded_frames,
        "frames": selected,
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def _synchronize_device() -> None:
    """Synchronize asynchronous accelerators before taking wall-clock timestamps."""

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            torch.mps.synchronize()
    except (ImportError, RuntimeError):
        return


def _peak_rss_bytes() -> int | None:
    try:
        import resource

        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return rss if sys.platform == "darwin" else rss * 1024
    except (ImportError, OSError):
        return None


def _normalize_names(names: Any) -> dict[str, str]:
    if isinstance(names, dict):
        return {str(key): str(value) for key, value in names.items()}
    if isinstance(names, (list, tuple)):
        return {str(index): str(value) for index, value in enumerate(names)}
    return {}


def _extract_detections(result: Any) -> list[dict[str, Any]]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        raise RuntimeError("comparison currently supports detection artifacts with result.boxes")
    coordinates = boxes.xyxy.detach().cpu().tolist()
    confidences = boxes.conf.detach().cpu().tolist()
    classes = boxes.cls.detach().cpu().tolist()
    return [
        {
            "class_id": int(class_id),
            "confidence": float(confidence),
            "xyxy": [float(value) for value in xyxy],
        }
        for xyxy, confidence, class_id in zip(coordinates, confidences, classes)
    ]


def _validation_metrics(model: Any, args: argparse.Namespace, imgsz: int | list[int]) -> dict[str, Any] | None:
    if not args.validation_data:
        return None
    options: dict[str, Any] = {
        "data": args.validation_data,
        "split": args.validation_split,
        "imgsz": imgsz,
        "batch": 1,
        "workers": 0,
        "plots": False,
        "save": False,
        "save_json": False,
        "verbose": False,
    }
    if args.device is not None:
        options["device"] = args.device
    metrics = model.val(**options)
    box = getattr(metrics, "box", None)
    if box is None:
        return {"available": False, "reason": "validation result has no detection box metrics"}
    return {
        "available": True,
        "map_50_95": float(box.map),
        "map_50": float(box.map50),
        "map_75": float(box.map75),
        "per_class_map": [float(value) for value in box.maps],
    }


def worker_main(argv: Sequence[str]) -> int:
    args = build_worker_parser().parse_args(argv)
    with args.frames_manifest.open("r", encoding="utf-8") as handle:
        frame_manifest = json.load(handle)
    frames = frame_manifest["frames"]
    if not frames:
        raise RuntimeError("frames manifest is empty")
    imgsz: int | list[int] = args.imgsz[0] if len(args.imgsz) == 1 else list(args.imgsz)

    try:
        with contextlib.redirect_stdout(sys.stderr):
            from ultralytics import YOLO
    except ImportError as error:
        raise RuntimeError("Ultralytics is unavailable in the benchmark environment") from error

    load_started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        model = YOLO(str(args.model))
    load_seconds = time.perf_counter() - load_started
    predict_options: dict[str, Any] = {
        "imgsz": imgsz,
        "conf": args.confidence,
        "iou": args.iou,
        "verbose": False,
        "save": False,
    }
    if args.device is not None:
        predict_options["device"] = args.device

    first_frame = frames[0]["path"]
    warmup_started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        for _ in range(args.warmup):
            model.predict(source=first_frame, **predict_options)
    _synchronize_device()
    warmup_seconds = time.perf_counter() - warmup_started

    wall_times_ms: list[float] = []
    speed_samples: dict[str, list[float]] = {"preprocess": [], "inference": [], "postprocess": []}
    predictions: list[dict[str, Any]] = []
    with contextlib.redirect_stdout(sys.stderr):
        for frame in frames:
            _synchronize_device()
            started = time.perf_counter()
            result = model.predict(source=frame["path"], **predict_options)[0]
            _synchronize_device()
            wall_times_ms.append((time.perf_counter() - started) * 1000.0)
            for stage in speed_samples:
                value = getattr(result, "speed", {}).get(stage)
                if value is not None:
                    speed_samples[stage].append(float(value))
            predictions.append(
                {
                    "sample_index": frame["sample_index"],
                    "detections": _extract_detections(result),
                }
            )
    benchmark_peak_rss = _peak_rss_bytes()
    detection_counts = [len(frame["detections"]) for frame in predictions]
    confidences = [
        detection["confidence"]
        for frame in predictions
        for detection in frame["detections"]
    ]
    validation = None
    with contextlib.redirect_stdout(sys.stderr):
        validation = _validation_metrics(model, args, imgsz)

    result = {
        "model": str(args.model.resolve()),
        "class_names": _normalize_names(getattr(model, "names", None)),
        "load_seconds": load_seconds,
        "warmup_seconds": warmup_seconds,
        "benchmark_peak_rss_bytes": benchmark_peak_rss,
        "latency_ms": {
            "mean_end_to_end": statistics.fmean(wall_times_ms),
            "median_end_to_end": statistics.median(wall_times_ms),
            "p95_end_to_end": _percentile(wall_times_ms, 0.95),
            "min_end_to_end": min(wall_times_ms),
            "max_end_to_end": max(wall_times_ms),
            "mean_ultralytics_preprocess": statistics.fmean(speed_samples["preprocess"])
            if speed_samples["preprocess"]
            else None,
            "mean_ultralytics_inference": statistics.fmean(speed_samples["inference"])
            if speed_samples["inference"]
            else None,
            "mean_ultralytics_postprocess": statistics.fmean(speed_samples["postprocess"])
            if speed_samples["postprocess"]
            else None,
        },
        "detections": {
            "total": sum(detection_counts),
            "mean_per_frame": statistics.fmean(detection_counts),
            "mean_confidence": statistics.fmean(confidences) if confidences else None,
        },
        "validation": validation,
        "predictions": predictions,
    }
    print(f"{WORKER_MARKER}{json.dumps(result, separators=(',', ':'))}")
    return 0


def run_worker(args: argparse.Namespace, model: Path, frames_manifest: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--model",
        str(model),
        "--frames-manifest",
        str(frames_manifest),
        "--imgsz",
        *[str(value) for value in args.imgsz],
        "--confidence",
        str(args.confidence),
        "--iou",
        str(args.iou),
        "--warmup",
        str(args.warmup),
        "--validation-split",
        args.validation_split,
    ]
    if args.device is not None:
        command.extend(["--device", args.device])
    if args.validation_data is not None:
        command.extend(["--validation-data", args.validation_data])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"model timed out after {args.timeout:.0f}s: {model}") from error
    if completed.returncode:
        diagnostic = (completed.stderr or completed.stdout)[-6000:]
        raise RuntimeError(f"model worker failed for {model} (exit {completed.returncode}):\n{diagnostic}")
    payload = next(
        (line[len(WORKER_MARKER) :] for line in reversed(completed.stdout.splitlines()) if line.startswith(WORKER_MARKER)),
        None,
    )
    if payload is None:
        raise RuntimeError(f"model worker returned no benchmark payload for {model}")
    return json.loads(payload)


def _box_iou(left: list[float], right: list[float]) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def compare_predictions(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]], match_iou: float
) -> dict[str, Any]:
    if len(baseline) != len(candidate):
        raise ValueError("baseline and candidate produced different frame counts")
    total_baseline = 0
    total_candidate = 0
    total_matches = 0
    matched_ious: list[float] = []
    confidence_deltas: list[float] = []
    both_empty_frames = 0
    for baseline_frame, candidate_frame in zip(baseline, candidate):
        if baseline_frame["sample_index"] != candidate_frame["sample_index"]:
            raise ValueError("baseline and candidate sample indices are misaligned")
        left = baseline_frame["detections"]
        right = candidate_frame["detections"]
        total_baseline += len(left)
        total_candidate += len(right)
        if not left and not right:
            both_empty_frames += 1
            continue
        possible: list[tuple[float, int, int]] = []
        for left_index, left_detection in enumerate(left):
            for right_index, right_detection in enumerate(right):
                if left_detection["class_id"] != right_detection["class_id"]:
                    continue
                iou = _box_iou(left_detection["xyxy"], right_detection["xyxy"])
                if iou >= match_iou:
                    possible.append((iou, left_index, right_index))
        used_left: set[int] = set()
        used_right: set[int] = set()
        for iou, left_index, right_index in sorted(possible, reverse=True):
            if left_index in used_left or right_index in used_right:
                continue
            used_left.add(left_index)
            used_right.add(right_index)
            matched_ious.append(iou)
            confidence_deltas.append(
                abs(left[left_index]["confidence"] - right[right_index]["confidence"])
            )
        total_matches += len(used_left)
    return {
        "metric_type": "baseline_agreement_not_ground_truth_accuracy",
        "match_iou": match_iou,
        "matched_detections": total_matches,
        "baseline_detections": total_baseline,
        "candidate_detections": total_candidate,
        "baseline_recall": total_matches / total_baseline if total_baseline else None,
        "candidate_precision": total_matches / total_candidate if total_candidate else None,
        "mean_matched_iou": statistics.fmean(matched_ious) if matched_ious else None,
        "mean_absolute_confidence_delta": statistics.fmean(confidence_deltas) if confidence_deltas else None,
        "detection_count_delta": total_candidate - total_baseline,
        "both_empty_frames": both_empty_frames,
    }


def _artifact_record(path: Path) -> dict[str, Any]:
    digest = hash_artifact(path)
    return {
        "path": str(path.resolve()),
        "sha256": digest.sha256,
        "size_bytes": digest.size_bytes,
        "file_count": digest.file_count,
        "kind": digest.kind,
        "digest_algorithm": digest.algorithm,
    }


def public_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_public_args(parser, args)
    source = args.source.expanduser().resolve()
    baseline = args.baseline.expanduser().resolve()
    candidates = [path.expanduser().resolve() for path in args.candidate]
    if args.validation_data:
        args.validation_data = str(Path(args.validation_data).expanduser().resolve())

    with tempfile.TemporaryDirectory(prefix="visn-yolo26-compare-") as temporary:
        temporary_path = Path(temporary)
        sample = materialize_frames(source, temporary_path / "frames", args.sample_fps, args.max_frames)
        frames_manifest = temporary_path / "frames.json"
        atomic_write_json(frames_manifest, sample)

        baseline_result = run_worker(args, baseline, frames_manifest)
        candidate_results: list[dict[str, Any]] = []
        for candidate in candidates:
            result = run_worker(args, candidate, frames_manifest)
            result["class_mapping_matches_baseline"] = result["class_names"] == baseline_result["class_names"]
            result["agreement_with_baseline"] = compare_predictions(
                baseline_result["predictions"], result["predictions"], args.match_iou
            )
            candidate_results.append(result)

    baseline_result.pop("predictions", None)
    for result in candidate_results:
        result.pop("predictions", None)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "interpretation": {
            "agreement": "Agreement uses the baseline predictions as a reference and is not ground-truth accuracy.",
            "quality": "Use validation.map_50_95 from --validation-data to approve quantized artifacts.",
            "memory": "Peak RSS is captured before optional labeled validation and includes one isolated model process.",
            "latency": "End-to-end latency includes lossless PNG read/decode plus prediction; Ultralytics stages are also reported.",
        },
        "environment": {
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "settings": {
            "imgsz": args.imgsz,
            "device": args.device,
            "confidence": args.confidence,
            "iou": args.iou,
            "match_iou": args.match_iou,
            "sample_fps": args.sample_fps,
            "max_frames": args.max_frames,
            "warmup": args.warmup,
            "validation_data": args.validation_data,
            "validation_split": args.validation_split,
        },
        "sample": {
            "source": _artifact_record(source),
            "kind": sample["kind"],
            "source_fps": sample.get("source_fps"),
            "effective_sample_fps": sample.get("effective_sample_fps"),
            "sample_stride": sample.get("sample_stride"),
            "decoded_frames": sample["decoded_frames"],
            "selected_frames": len(sample["frames"]),
            "timestamps_seconds": [frame["timestamp_seconds"] for frame in sample["frames"]],
        },
        "baseline": {"artifact": _artifact_record(baseline), **baseline_result},
        "candidates": [
            {"artifact": _artifact_record(path), **result}
            for path, result in zip(candidates, candidate_results)
        ],
    }
    report_path = args.report.expanduser().resolve()
    atomic_write_json(report_path, report)
    print(report_path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if "--worker" in effective_argv:
        return worker_main(effective_argv)
    return public_main(effective_argv)


if __name__ == "__main__":
    raise SystemExit(main())
