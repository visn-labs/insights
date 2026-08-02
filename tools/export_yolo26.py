#!/usr/bin/env python3
"""Export a YOLO26 checkpoint for the selected NVIDIA deployment host."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ultralytics import YOLO


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="yolo26s.pt")
    parser.add_argument("--format", choices=["onnx", "engine"], default="onnx")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--workspace", type=float, default=4.0)
    parser.add_argument("--int8", action="store_true")
    parser.add_argument("--data", default="coco.yaml")
    parser.add_argument("--device", default="0")
    args = parser.parse_args()

    model = YOLO(args.model)
    options = {
        "format": args.format,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "dynamic": True,
        "simplify": True,
    }
    if args.format == "engine":
        options.update(
            workspace=args.workspace,
            quantize=8 if args.int8 else 16,
            data=args.data,
            device=args.device,
        )
    output = Path(model.export(**options))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest = {
        "source_model": args.model,
        "artifact": str(output),
        "sha256": digest,
        "format": args.format,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "precision": "int8" if args.int8 else "fp16" if args.format == "engine" else "fp32",
        "note": "Record GPU architecture, driver, CUDA, TensorRT, DeepStream and validation report before release.",
    }
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)


if __name__ == "__main__":
    main()

