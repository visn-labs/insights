#!/usr/bin/env python3
"""Prototype bounding-box mask persistence. No detector or pixel redaction is performed."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


def box_iou(a: List[float], b: List[float]) -> float:
    """Computes Intersection over Union (IoU) of [x, y, w, h] normalized boxes."""
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[0] + a[2], b[0] + b[2])
    y2 = min(a[1] + a[3], b[1] + b[3])

    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter_area = inter_w * inter_h

    area_a = a[2] * a[3]
    area_b = b[2] * b[3]
    union_area = area_a + area_b - inter_area

    if union_area <= 1e-9:
        return 0.0
    return inter_area / union_area


@dataclass
class KalmanTrack:
    id: int
    # State: [x, y, w, h, vx, vy, vw, vh]
    state: List[float]
    confidence: float
    occlusion_count: int = 0
    mode: str = "cryptographic_erasure"

    def predict(self) -> None:
        self.state[0] += self.state[4]
        self.state[1] += self.state[5]
        self.state[2] += self.state[6]
        self.state[3] += self.state[7]
        self.occlusion_count += 1

    def update(self, bbox: List[float], confidence: float) -> None:
        alpha = 0.70
        beta = 0.30

        rx = bbox[0] - self.state[0]
        ry = bbox[1] - self.state[1]
        rw = bbox[2] - self.state[2]
        rh = bbox[3] - self.state[3]

        self.state[0] += alpha * rx
        self.state[1] += alpha * ry
        self.state[2] += alpha * rw
        self.state[3] += alpha * rh

        self.state[4] += beta * rx
        self.state[5] += beta * ry
        self.state[6] += beta * rw
        self.state[7] += beta * rh

        self.confidence = confidence
        self.occlusion_count = 0

    def current_bbox(self) -> List[float]:
        return [
            max(0.0, min(1.0, self.state[0])),
            max(0.0, min(1.0, self.state[1])),
            max(0.01, min(1.0, self.state[2])),
            max(0.01, min(1.0, self.state[3])),
        ]


class PrivacyRedactionEngine:
    def __init__(
        self,
        primary_threshold: float = 0.60,
        recovery_threshold: float = 0.10,
        occlusion_grace_frames: int = 15,
        mask_dilation_factor: float = 0.20,
        sanitization_mode: str = "cryptographic_erasure",
    ):
        self.primary_threshold = primary_threshold
        self.recovery_threshold = recovery_threshold
        self.occlusion_grace_frames = occlusion_grace_frames
        self.mask_dilation_factor = mask_dilation_factor
        self.sanitization_mode = sanitization_mode
        self.tracks: List[KalmanTrack] = []
        self.next_id = 1

    def process_frame(
        self, detections: List[dict[str, Any]]
    ) -> List[dict[str, Any]]:
        if len(detections) > 10000:
            raise ValueError("too_many_detections")
        for detection in detections:
            box = detection.get("bbox", [])
            confidence = detection.get("confidence", 0)
            if len(box) != 4 or not all(map(math.isfinite, box)) or not math.isfinite(confidence):
                raise ValueError("invalid_detection")
            x, y, w, h = box
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1 or y+h > 1 or not 0 <= confidence <= 1:
                raise ValueError("invalid_detection")
        # Step 1: Predict
        for t in self.tracks:
            t.predict()

        # Step 2: Two-pass association
        high_dets = [
            d for d in detections if d.get("confidence", 0.0) >= self.primary_threshold
        ]
        low_dets = [
            d
            for d in detections
            if self.recovery_threshold
            <= d.get("confidence", 0.0)
            < self.primary_threshold
        ]

        matched_tracks = set()
        matched_high = set()
        matched_low = set()

        # Pass 1: Match high confidence
        for d_idx, det in enumerate(high_dets):
            bbox = det.get("bbox", [0, 0, 0, 0])
            best_iou = 0.25
            best_t = None
            for t_idx, track in enumerate(self.tracks):
                if t_idx in matched_tracks:
                    continue
                iou = box_iou(track.current_bbox(), bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_t = t_idx

            if best_t is not None:
                self.tracks[best_t].update(bbox, det.get("confidence", 0.8))
                matched_tracks.add(best_t)
                matched_high.add(d_idx)

        # Pass 2: Match low confidence
        for d_idx, det in enumerate(low_dets):
            bbox = det.get("bbox", [0, 0, 0, 0])
            best_iou = 0.20
            best_t = None
            for t_idx, track in enumerate(self.tracks):
                if t_idx in matched_tracks:
                    continue
                iou = box_iou(track.current_bbox(), bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_t = t_idx

            if best_t is not None:
                self.tracks[best_t].update(bbox, det.get("confidence", 0.2))
                matched_tracks.add(best_t)
                matched_low.add(d_idx)

        # Step 3: Initialize new tracks for unmatched high confidence detections
        for d_idx, det in enumerate(high_dets):
            if d_idx not in matched_high:
                bbox = det.get("bbox", [0, 0, 0, 0])
                self.tracks.append(
                    KalmanTrack(
                        id=self.next_id,
                        state=[
                            bbox[0],
                            bbox[1],
                            bbox[2],
                            bbox[3],
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                        ],
                        confidence=det.get("confidence", 0.8),
                        mode=self.sanitization_mode,
                    )
                )
                self.next_id += 1

        for d_idx, det in enumerate(low_dets):
            if d_idx not in matched_low:
                self.tracks.append(KalmanTrack(self.next_id, list(det["bbox"]) + [0.] * 4,
                                               det["confidence"], mode=self.sanitization_mode))
                self.next_id += 1

        # Step 4: Prune tracks exceeding grace period
        self.tracks = [
            t
            for t in self.tracks
            if t.occlusion_count <= self.occlusion_grace_frames
        ]

        # Step 5: Generate dilated masks
        masks = []
        df = self.mask_dilation_factor
        for track in self.tracks:
            x, y, w, h = track.current_bbox()
            pad_w = w * df
            pad_h = h * df
            x0, y0 = max(0., x-pad_w/2), max(0., y-pad_h/2)
            x1, y1 = min(1., x+w+pad_w/2), min(1., y+h+pad_h/2)
            dilated = [x0, y0, max(0., x1-x0), max(0., y1-y0)]
            masks.append(
                {
                    "mask_id": track.id,
                    "dilated_bbox": dilated,
                    "confidence": round(track.confidence, 3),
                    "occlusion_frames": track.occlusion_count,
                    "is_occluded_projection": track.occlusion_count > 0,
                    "sanitization": track.mode,
                }
            )

        for det in detections:
            if det["confidence"] < self.recovery_threshold:
                continue
            x, y, w, h = det["bbox"]
            covered = any(b[0] <= x and b[1] <= y and b[0]+b[2] >= x+w and b[1]+b[3] >= y+h
                          for b in (m["dilated_bbox"] for m in masks))
            if not covered:
                x0, y0 = max(0., x-w*df/2), max(0., y-h*df/2)
                x1, y1 = min(1., x+w*(1+df/2)), min(1., y+h*(1+df/2))
                masks.append(dict(mask_id=self.next_id, dilated_bbox=[x0,y0,x1-x0,y1-y0],
                                  confidence=det["confidence"], occlusion_frames=0,
                                  is_occluded_projection=False, sanitization=self.sanitization_mode))
                self.next_id += 1

        return masks


def main():
    parser = argparse.ArgumentParser(description="Privacy Redaction Engine")
    parser.add_argument("--input", default="-", help="JSON input or '-' for stdin")
    args = parser.parse_args()

    content = sys.stdin.read() if args.input == "-" else open(args.input).read()
    data = json.loads(content) if content.strip() else {}

    engine = PrivacyRedactionEngine()
    frames = data.get("frames", [[{"bbox": [0.3, 0.3, 0.1, 0.1], "confidence": 0.85}]])

    results = []
    for f in frames:
        masks = engine.process_frame(f)
        results.append(masks)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
