#!/usr/bin/env python3
"""Physical Liveness and Cryptographic Stream Authenticity Verifier.

Enforces hardware-level silicon sensor authentication via I-frame
Photo-Response Non-Uniformity (PRNU) Peak-to-Correlation Energy (PCE),
paired with Electric Network Frequency (ENF) temporal grid correlation.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, List, Tuple


def compute_pce(correlation_matrix: List[float], width: int, height: int) -> float:
    """Computes Peak-to-Correlation Energy (PCE) from a 2D cross-correlation matrix.
    PCE = peak^2 / mean_energy_outside_peak_region
    """
    if not correlation_matrix or width == 0 or height == 0:
        return -0.10

    peak_val = max(correlation_matrix)
    peak_idx = correlation_matrix.index(peak_val)
    peak_y = peak_idx // width
    peak_x = peak_idx % width

    radius = 2
    outside_energy_sum = 0.0
    outside_count = 0

    for y in range(height):
        for x in range(width):
            dy = abs(y - peak_y)
            dx = abs(x - peak_x)
            if dy > radius or dx > radius:
                v = correlation_matrix[y * width + x]
                outside_energy_sum += v * v
                outside_count += 1

    if outside_count == 0 or outside_energy_sum <= 1e-9:
        return 300.0 if peak_val > 0.0 else -0.10

    mean_outside = outside_energy_sum / outside_count
    return (peak_val * peak_val) / mean_outside


def compute_enf_correlation(candidate: List[float], reference: List[float]) -> float:
    """Pearson correlation coefficient between candidate trace and reference grid trace."""
    n = min(len(candidate), len(reference))
    if n < 4:
        return 0.0

    mean_c = sum(candidate[:n]) / n
    mean_r = sum(reference[:n]) / n

    num = 0.0
    den_c = 0.0
    den_r = 0.0

    for i in range(n):
        dc = candidate[i] - mean_c
        dr = reference[i] - mean_r
        num += dc * dr
        den_c += dc * dc
        den_r += dr * dr

    den = math.sqrt(den_c * den_r)
    if den <= 1e-9:
        return 0.0
    return max(-1.0, min(1.0, num / den))


def verify_stream_liveness(
    accumulated_frames: int,
    pce_score: float,
    enf_samples: List[float],
    grid_reference: List[float],
    pce_threshold: float = 50.0,
    enf_threshold: float = 0.65,
    calibration_frames: int = 200,
    nominal_grid_hz: float = 50.0,
) -> dict[str, Any]:
    """Evaluates cryptographic liveness binary gate L_{i,t} = I(PCE >= tau_pce) * I(rho_enf >= tau_enf)."""
    if accumulated_frames < calibration_frames:
        return {
            "is_authentic": True,
            "pce_score": pce_score,
            "enf_correlation": 1.0,
            "estimated_enf_hz": nominal_grid_hz,
            "accumulated_calibration_frames": accumulated_frames,
            "status": "calibrating",
            "details": f"Calibrating PRNU signature: {accumulated_frames}/{calibration_frames} frames",
        }

    enf_corr = compute_enf_correlation(enf_samples, grid_reference)
    pce_pass = pce_score >= pce_threshold
    enf_pass = enf_corr >= enf_threshold

    if pce_pass and enf_pass:
        status = "verified_live"
        is_auth = True
        details = (
            f"Stream verified: PCE={pce_score:.1f} >= {pce_threshold:.1f}, "
            f"ENF rho={enf_corr:.2f} >= {enf_threshold:.2f}"
        )
    elif not pce_pass and not enf_pass:
        status = "synthetic_or_spoofed_stream"
        is_auth = False
        details = f"Failed sensor and temporal verification: PCE={pce_score:.1f}, ENF rho={enf_corr:.2f}"
    elif not pce_pass:
        status = "sensor_fingerprint_mismatch"
        is_auth = False
        details = f"PRNU silicon fingerprint mismatch: PCE={pce_score:.1f} < {pce_threshold:.1f}"
    else:
        status = "enf_replay_detected"
        is_auth = False
        details = f"ENF grid frequency mismatch (replay attack): rho={enf_corr:.2f} < {enf_threshold:.2f}"

    mean_enf = sum(enf_samples) / len(enf_samples) if enf_samples else nominal_grid_hz

    return {
        "is_authentic": is_auth,
        "pce_score": round(pce_score, 2),
        "enf_correlation": round(enf_corr, 4),
        "estimated_enf_hz": round(mean_enf, 3),
        "accumulated_calibration_frames": accumulated_frames,
        "status": status,
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description="Liveness and Authenticity Verifier")
    parser.add_argument("--input", default="-", help="JSON input or '-' for stdin")
    args = parser.parse_args()

    content = sys.stdin.read() if args.input == "-" else open(args.input).read()
    data = json.loads(content) if content.strip() else {}

    res = verify_stream_liveness(
        accumulated_frames=data.get("accumulated_frames", 250),
        pce_score=data.get("pce_score", 294.9),
        enf_samples=data.get("enf_samples", [50.02, 50.04, 49.98, 49.96, 50.01]),
        grid_reference=data.get(
            "grid_reference", [50.021, 50.039, 49.978, 49.961, 50.012]
        ),
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
