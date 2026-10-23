#!/usr/bin/env python3
"""Export YOLO26 artifacts reproducibly without changing the runtime venv.

Run this script from the dedicated Python 3.11+ export environment documented in
``docs/BACKEND_INFERENCE_OPTIMIZATION.md``. Third-party imports are deliberately
lazy so ``--help`` and argument validation work in the normal service environment.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import struct
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


CANONICAL_FORMAT = {"tflite": "litert"}
FORMAT_PRECISIONS = {
    "coreml": frozenset({"32", "16", "8", "w8a16"}),
    "litert": frozenset({"32", "8", "w8a16", "w8a32"}),
    "onnx": frozenset({"32", "16", "8"}),
    "openvino": frozenset({"32", "16", "8"}),
    "engine": frozenset({"32", "16", "8"}),
}
CALIBRATED_PRECISIONS = {
    "onnx": frozenset({"8"}),
    "openvino": frozenset({"8"}),
    "engine": frozenset({"8"}),
    "litert": frozenset({"8", "w8a16"}),
}
HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ArtifactDigest:
    """A stable digest for either a regular file or an exported directory."""

    sha256: str
    size_bytes: int
    file_count: int
    kind: str
    algorithm: str


def _stream_file(path: Path, digest: Any) -> int:
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return size


def _directory_files(path: Path) -> Iterable[Path]:
    """Yield files in a deterministic order without following directory symlinks."""

    for candidate in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        if candidate.is_symlink():
            raise ValueError(f"artifact packages containing symlinks are not supported: {candidate}")
        if candidate.is_file():
            yield candidate


def hash_artifact(path: Path | str) -> ArtifactDigest:
    """Hash an exported file or package using bounded memory.

    Directory hashes include each relative path and byte length, so two packages
    with the same concatenated contents but different layouts cannot collide.
    """

    artifact = Path(path)
    if not artifact.exists():
        raise FileNotFoundError(f"artifact does not exist: {artifact}")

    digest = hashlib.sha256()
    if artifact.is_file():
        size = _stream_file(artifact, digest)
        return ArtifactDigest(digest.hexdigest(), size, 1, "file", "sha256")
    if not artifact.is_dir():
        raise ValueError(f"artifact is neither a file nor a directory: {artifact}")

    digest.update(b"visn-artifact-directory-v1\0")
    total_size = 0
    file_count = 0
    for file_path in _directory_files(artifact):
        relative = file_path.relative_to(artifact).as_posix().encode("utf-8")
        file_size = file_path.stat().st_size
        digest.update(struct.pack(">Q", len(relative)))
        digest.update(relative)
        digest.update(struct.pack(">Q", file_size))
        streamed_size = _stream_file(file_path, digest)
        if streamed_size != file_size:
            raise RuntimeError(f"artifact changed while hashing: {file_path}")
        total_size += streamed_size
        file_count += 1
    return ArtifactDigest(digest.hexdigest(), total_size, file_count, "directory", "sha256-tree-v1")


def atomic_write_json(path: Path, value: Any) -> None:
    """Write JSON next to its final destination, then atomically replace it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def _installed_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _canonical_format(requested: str) -> str:
    return CANONICAL_FORMAT.get(requested, requested)


def _quantize_value(value: str) -> int | str:
    return int(value) if value in {"8", "16", "32"} else value


def _runtime_guidance(fmt: str, quantize: str) -> dict[str, str]:
    if fmt == "coreml":
        return {
            "runtime": "Core ML (Ultralytics/Apple CoreML execution provider)",
            "target": "Apple Silicon; static batch=1 is the default latency profile",
            "validation": "Compare FP16 first, then accept INT8 or W8A16 only after agreement and labeled validation",
        }
    if fmt == "litert":
        return {
            "runtime": "LiteRT",
            "target": "macOS/Linux x86; benchmark CPU and Metal/GPU delegate on the deployment host",
            "validation": "LiteRT has no FP16 export in current Ultralytics; W8A16 uses INT16 activations",
        }
    if fmt == "openvino":
        return {
            "runtime": "OpenVINO Runtime",
            "target": "Intel CPU/iGPU; use throughput mode only when latency permits batching/queuing",
            "validation": "INT8 is calibration-sensitive; validate against the same camera distribution",
        }
    if fmt == "engine":
        return {
            "runtime": "TensorRT",
            "target": "NVIDIA GPU matching the build CUDA/TensorRT architecture",
            "validation": "Do not copy engines across incompatible GPU/TensorRT hosts; retain this manifest",
        }
    return {
        "runtime": "ONNX Runtime",
        "target": "Portable fallback; select and benchmark the host execution provider explicitly",
        "validation": f"Validate quantize={quantize} on representative camera frames before release",
    }


def _precision_description(fmt: str, quantize: str) -> str:
    if fmt == "coreml" and quantize in {"8", "w8a16"}:
        return "8-bit palettized weights with FP16 ML Program compute"
    return {
        "32": "FP32 weights and activations",
        "16": "FP16 weights and activations",
        "8": "INT8 weights and activations",
        "w8a16": "INT8 weights with 16-bit activations",
        "w8a32": "INT8 weights with FP32 activations",
    }[quantize]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a static YOLO26 artifact plus a reproducibility manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", default="yolo26s.pt", help="YOLO26 .pt checkpoint or Ultralytics model name")
    parser.add_argument(
        "--format",
        choices=["coreml", "litert", "tflite", "onnx", "openvino", "engine"],
        default="coreml",
        help="Target backend; tflite is accepted as an alias for the modern LiteRT exporter",
    )
    parser.add_argument(
        "--quantize",
        choices=["32", "16", "8", "w8a16", "w8a32"],
        default="16",
        help="32=FP32, 16=FP16, 8=INT8, W8A16/W8A32 are weight/activation combinations",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        nargs="+",
        default=[640],
        metavar="PX",
        help="One value for square input or two values for height width",
    )
    parser.add_argument("--batch", type=int, default=1, help="Static batch size, or maximum batch when dynamic")
    parser.add_argument(
        "--dynamic",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable dynamic input shapes; static shapes are faster and safer by default",
    )
    parser.add_argument(
        "--end2end",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the YOLO26 end-to-end/NMS-free head; omit to retain checkpoint behavior",
    )
    parser.add_argument(
        "--simplify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Simplify ONNX graphs (ONNX and TensorRT only)",
    )
    parser.add_argument("--opset", type=int, help="Explicit ONNX opset for ONNX/TensorRT; omit for auto-selection")
    parser.add_argument("--workspace", type=float, default=4.0, help="TensorRT workspace in GiB")
    parser.add_argument("--device", help="Ultralytics export device, e.g. cpu, mps, or CUDA index 0")
    parser.add_argument(
        "--data",
        help="Representative Ultralytics dataset YAML required for activation-calibrated quantization",
    )
    parser.add_argument(
        "--fraction",
        type=float,
        default=1.0,
        help="Fraction of the calibration dataset to use; keep 1.0 for release candidates",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/models"),
        help="Directory that receives the artifact and manifest",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an exact existing destination artifact")
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, str]:
    fmt = _canonical_format(args.format)
    quantize = args.quantize.lower()
    allowed = FORMAT_PRECISIONS[fmt]
    if quantize not in allowed:
        parser.error(
            f"--format {args.format} does not support --quantize {quantize}; "
            f"supported values: {', '.join(sorted(allowed))}"
        )
    if len(args.imgsz) not in {1, 2} or any(value <= 0 for value in args.imgsz):
        parser.error("--imgsz requires one or two positive integers")
    if args.batch <= 0:
        parser.error("--batch must be positive")
    if not 0 < args.fraction <= 1:
        parser.error("--fraction must be greater than 0 and no greater than 1")
    requires_calibration = quantize in CALIBRATED_PRECISIONS.get(fmt, frozenset())
    if requires_calibration and not args.data:
        parser.error(
            f"--format {args.format} --quantize {quantize} requires --data with a representative dataset YAML"
        )
    if fmt == "engine" and args.device is None:
        parser.error("TensorRT export requires an NVIDIA CUDA device; pass --device 0 (or another CUDA index)")
    if fmt == "litert" and args.dynamic:
        parser.error("the current Ultralytics LiteRT exporter supports static shapes only; remove --dynamic")
    if args.workspace <= 0:
        parser.error("--workspace must be positive")
    return fmt, quantize


def _move_artifact(source: Path, output_dir: Path, overwrite: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / source.name
    if source.resolve() == destination.resolve():
        return source
    if destination.exists():
        if not overwrite:
            raise FileExistsError(
                f"destination already exists: {destination}; pass --overwrite to replace this exact artifact"
            )
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    return Path(shutil.move(str(source), str(destination)))


def _export_options(args: argparse.Namespace, fmt: str, quantize: str) -> dict[str, Any]:
    imgsz: int | list[int] = args.imgsz[0] if len(args.imgsz) == 1 else list(args.imgsz)
    options: dict[str, Any] = {
        "format": fmt,
        "imgsz": imgsz,
        "batch": args.batch,
        "dynamic": args.dynamic,
        "quantize": _quantize_value(quantize),
    }
    if args.device is not None:
        options["device"] = args.device
    if args.end2end is not None:
        options["end2end"] = args.end2end
    if fmt in {"onnx", "engine"}:
        options["simplify"] = args.simplify
        if args.opset is not None:
            options["opset"] = args.opset
    if fmt == "engine":
        options["workspace"] = args.workspace
    if quantize in CALIBRATED_PRECISIONS.get(fmt, frozenset()):
        options["data"] = args.data
        options["fraction"] = args.fraction
    return options


def _source_metadata(model: str) -> dict[str, Any]:
    path = Path(model).expanduser()
    metadata: dict[str, Any] = {"requested": model}
    if path.exists():
        resolved = path.resolve()
        metadata["resolved_path"] = str(resolved)
        metadata["digest"] = asdict(hash_artifact(resolved))
    return metadata


def _environment_metadata() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "ultralytics": _installed_version("ultralytics"),
        "torch": _installed_version("torch"),
        "coremltools": _installed_version("coremltools"),
        "onnxruntime": _installed_version("onnxruntime"),
        "openvino": _installed_version("openvino"),
        "ai_edge_litert": _installed_version("ai-edge-litert"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    fmt, quantize = validate_args(parser, args)
    export_options = _export_options(args, fmt, quantize)

    try:
        from ultralytics import YOLO
    except ImportError as error:
        parser.error(
            "Ultralytics export dependencies are unavailable. Create the separate Python 3.11+ "
            "environment from tools/requirements-export.txt. "
            f"Original error: {error}"
        )

    model = YOLO(args.model)
    exported = Path(model.export(**export_options)).expanduser().resolve()
    if not exported.exists():
        raise FileNotFoundError(f"Ultralytics reported an artifact that does not exist: {exported}")
    artifact = _move_artifact(exported, args.output_dir.expanduser().resolve(), args.overwrite)
    artifact_digest = hash_artifact(artifact)

    manifest = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_model": _source_metadata(args.model),
        "artifact": {
            "path": str(artifact.resolve()),
            **asdict(artifact_digest),
        },
        "export": {
            "requested_format": args.format,
            "format": fmt,
            "quantize": quantize,
            "precision": _precision_description(fmt, quantize),
            "imgsz": list(args.imgsz),
            "batch": args.batch,
            "dynamic": args.dynamic,
            "static_shapes": not args.dynamic,
            "end2end_override": args.end2end,
            "options": export_options,
        },
        "calibration": {
            "required": quantize in CALIBRATED_PRECISIONS.get(fmt, frozenset()),
            "data": args.data,
            "fraction": args.fraction if args.data else None,
            "note": "Use camera-representative frames and retain full fraction for release validation.",
        },
        "runtime_guidance": _runtime_guidance(fmt, quantize),
        "environment": _environment_metadata(),
    }
    manifest_path = artifact.parent / f"{artifact.name}.manifest.json"
    atomic_write_json(manifest_path, manifest)
    print(json.dumps({"artifact": str(artifact), "manifest": str(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
