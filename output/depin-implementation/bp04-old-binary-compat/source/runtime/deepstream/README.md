# DeepStream production adapter boundary

The Phase 0 Rust service consumes a stable `DetectorOutput` contract:

```json
{"model":"yolo26s-site-v1","observations":[{"frame_time_ms":0,"track_id":"42","class_name":"person","confidence":0.94,"bbox":[0.1,0.2,0.2,0.5]}]}
```

The included YOLO26 command runner produces this contract for development. The production NVIDIA implementation replaces that process with a long-running DeepStream worker using this graph:

```text
HTTP(S)/RTSP/file -> parser -> NVDEC -> nvstreammux -> nvinfer(YOLO26 TensorRT)
          -> nvtracker(NvDCF) -> metadata adapter -> Rust observations
```

Before enabling it, pin and validate the exact NVIDIA driver, CUDA, TensorRT, DeepStream, GPU architecture, YOLO26 export, tensor layout, and custom parser together. TensorRT engine files are build-host/runtime specific and are intentionally excluded from this repository. The parser symbol is commented out in the inference template because inventing an ABI or tensor mapping before examining the exported engine would be unsafe.

Implementation steps on the selected Linux/NVIDIA host:

1. Export ONNX and TensorRT artifacts with `tools/export_yolo26.py`.
2. Compare exported-model accuracy with the original checkpoint.
3. Inspect tensor names/shapes and implement the parser in an isolated native library.
4. Run it through `nvinfer` with `cluster-mode=4` for the NMS-free one-to-one head.
5. Attach NvDCF and copy metadata into owned Rust records before the GStreamer buffer is released.
6. Make the worker emit the `DetectorOutput` contract during Phase 0 validation.
7. Benchmark 16/32/64 sources; set batch size and inference interval from measured results.

The web/API service remains unchanged when this adapter is substituted.
