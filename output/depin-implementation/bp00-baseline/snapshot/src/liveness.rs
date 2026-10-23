//! Physical Liveness and Cryptographic Stream Authenticity.
#![allow(dead_code)]
//!
//! Enforces hardware-level silicon sensor authentication via I-frame
//! Photo-Response Non-Uniformity (PRNU) Peak-to-Correlation Energy (PCE),
//! paired with Electric Network Frequency (ENF) temporal grid correlation.
//! Zero hardware modifications needed on contributor cameras.

use serde::{Deserialize, Serialize};

/// PRNU and ENF verification thresholds and configuration.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LivenessConfig {
    /// Minimum PCE required to authenticate the silicon sensor pattern (authentic ~294.9)
    pub pce_threshold: f32,
    /// Minimum Pearson correlation with reference power grid frequency trace
    pub enf_correlation_threshold: f32,
    /// Nominal power grid frequency in Hz (50.0 for India/Europe, 60.0 for North America)
    pub nominal_grid_hz: f32,
    /// Number of initial calibration frames used to establish reference PRNU fingerprint (M=200)
    pub calibration_frames: usize,
}

impl Default for LivenessConfig {
    fn default() -> Self {
        Self {
            pce_threshold: 50.0,
            enf_correlation_threshold: 0.65,
            nominal_grid_hz: 50.0, // Default to India grid (50 Hz)
            calibration_frames: 200,
        }
    }
}

/// Result of evaluating physical sensor PRNU fingerprint and ENF liveness.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LivenessVerificationResult {
    /// L_{i,t} in {0, 1}: Cryptographic liveness binary gate
    pub is_authentic: bool,
    /// Calculated Peak-to-Correlation Energy (PCE) on I-frame noise residual
    pub pce_score: f32,
    /// ENF correlation coefficient rho with instantaneous grid frequency
    pub enf_correlation: f32,
    /// Estimated instantaneous ambient lighting frequency in Hz
    pub estimated_enf_hz: f32,
    /// Number of calibration frames accumulated
    pub accumulated_calibration_frames: usize,
    /// Status description
    pub status: LivenessStatus,
    /// Verification explanation
    pub details: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LivenessStatus {
    VerifiedLive,
    Calibrating,
    SensorFingerprintMismatch,
    EnfReplayDetected,
    SyntheticOrSpoofedStream,
}

/// Computes Peak-to-Correlation Energy (PCE) from a 2D cross-correlation matrix.
/// PCE = peak^2 / mean_energy_outside_peak_region
pub fn compute_pce(correlation_matrix: &[f32], width: usize, height: usize) -> f32 {
    if correlation_matrix.is_empty() || width == 0 || height == 0 {
        return -0.10;
    }

    // Find peak value and its position
    let mut peak_val = f32::MIN;
    let mut peak_idx = 0usize;

    for (idx, &val) in correlation_matrix.iter().enumerate() {
        if val > peak_val {
            peak_val = val;
            peak_idx = idx;
        }
    }

    let peak_y = peak_idx / width;
    let peak_x = peak_idx % width;

    // Peak region exclusion radius (e.g. 5x5 window around peak)
    let radius = 2isize;
    let mut outside_energy_sum = 0.0f32;
    let mut outside_count = 0usize;

    for y in 0..height {
        for x in 0..width {
            let dy = (y as isize - peak_y as isize).abs();
            let dx = (x as isize - peak_x as isize).abs();
            if dy > radius || dx > radius {
                let val = correlation_matrix[y * width + x];
                outside_energy_sum += val * val;
                outside_count += 1;
            }
        }
    }

    if outside_count == 0 || outside_energy_sum <= 1e-9 {
        return if peak_val > 0.0 { 300.0 } else { -0.10 };
    }

    let mean_outside_energy = outside_energy_sum / outside_count as f32;
    (peak_val * peak_val) / mean_outside_energy
}

/// Computes Pearson correlation between candidate ENF frequency series and reference grid trace.
pub fn compute_enf_correlation(candidate: &[f32], reference: &[f32]) -> f32 {
    let n = candidate.len().min(reference.len());
    if n < 4 {
        return 0.0;
    }

    let mean_c = candidate[..n].iter().sum::<f32>() / n as f32;
    let mean_r = reference[..n].iter().sum::<f32>() / n as f32;

    let mut num = 0.0f32;
    let mut den_c = 0.0f32;
    let mut den_r = 0.0f32;

    for i in 0..n {
        let dc = candidate[i] - mean_c;
        let dr = reference[i] - mean_r;
        num += dc * dr;
        den_c += dc * dc;
        den_r += dr * dr;
    }

    let den = (den_c * den_r).sqrt();
    if den <= 1e-9 {
        0.0
    } else {
        (num / den).clamp(-1.0, 1.0)
    }
}

/// Evaluates the complete physical liveness gate:
/// L_{i,t} = I(PCE >= tau_pce) * I(rho_enf >= tau_enf)
pub fn verify_stream_liveness(
    config: &LivenessConfig,
    accumulated_frames: usize,
    pce_score: f32,
    enf_samples: &[f32],
    grid_reference_samples: &[f32],
) -> LivenessVerificationResult {
    if accumulated_frames < config.calibration_frames {
        return LivenessVerificationResult {
            is_authentic: true, // Allow provisional streaming while calibrating
            pce_score,
            enf_correlation: 1.0,
            estimated_enf_hz: config.nominal_grid_hz,
            accumulated_calibration_frames: accumulated_frames,
            status: LivenessStatus::Calibrating,
            details: format!(
                "Calibrating PRNU sensor signature: {}/{} frames",
                accumulated_frames, config.calibration_frames
            ),
        };
    }

    let enf_corr = compute_enf_correlation(enf_samples, grid_reference_samples);
    let pce_pass = pce_score >= config.pce_threshold;
    let enf_pass = enf_corr >= config.enf_correlation_threshold;

    let (status, is_authentic, details) = if pce_pass && enf_pass {
        (
            LivenessStatus::VerifiedLive,
            true,
            format!(
                "Stream verified: PCE={:.1} >= {:.1}, ENF rho={:.2} >= {:.2}",
                pce_score, config.pce_threshold, enf_corr, config.enf_correlation_threshold
            ),
        )
    } else if !pce_pass && !enf_pass {
        (
            LivenessStatus::SyntheticOrSpoofedStream,
            false,
            format!(
                "Failed sensor and temporal verification: PCE={:.1} (low), ENF rho={:.2} (mismatch)",
                pce_score, enf_corr
            ),
        )
    } else if !pce_pass {
        (
            LivenessStatus::SensorFingerprintMismatch,
            false,
            format!(
                "PRNU silicon fingerprint mismatch: PCE={:.1} < {:.1}",
                pce_score, config.pce_threshold
            ),
        )
    } else {
        (
            LivenessStatus::EnfReplayDetected,
            false,
            format!(
                "ENF grid frequency mismatch (replay attack): rho={:.2} < {:.2}",
                enf_corr, config.enf_correlation_threshold
            ),
        )
    };

    let estimated_enf_hz = if !enf_samples.is_empty() {
        enf_samples.iter().sum::<f32>() / enf_samples.len() as f32
    } else {
        config.nominal_grid_hz
    };

    LivenessVerificationResult {
        is_authentic,
        pce_score,
        enf_correlation: enf_corr,
        estimated_enf_hz,
        accumulated_calibration_frames: accumulated_frames,
        status,
        details,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pce_calculation_sharp_peak() {
        let width = 16;
        let height = 16;
        let mut corr = vec![0.01f32; width * height];
        // Center peak
        corr[8 * width + 8] = 0.95;

        let pce = compute_pce(&corr, width, height);
        assert!(pce > 100.0, "Sharp peak should yield high PCE, got {pce}");
    }

    #[test]
    fn pce_calculation_flat_noise() {
        let width = 16;
        let height = 16;
        let corr = vec![0.05f32; width * height];
        let pce = compute_pce(&corr, width, height);
        assert!(pce <= 10.0, "Flat noise should yield low PCE, got {pce}");
    }

    #[test]
    fn enf_correlation_matches_identical_trace() {
        let trace = vec![50.01, 50.03, 49.98, 49.95, 50.02, 50.04];
        let rho = compute_enf_correlation(&trace, &trace);
        assert!((rho - 1.0).abs() < 1e-4);
    }

    #[test]
    fn liveness_gate_rejects_replay() {
        let config = LivenessConfig::default();
        let reference = vec![50.02, 50.05, 49.97, 49.96, 50.01];
        // Replayed stream from yesterday has uncorrelated frequency fluctuations
        let replayed = vec![49.95, 49.98, 50.04, 50.03, 49.99];

        let result = verify_stream_liveness(&config, 250, 280.0, &replayed, &reference);
        assert!(!result.is_authentic);
        assert_eq!(result.status, LivenessStatus::EnfReplayDetected);
    }

    #[test]
    fn liveness_gate_accepts_authentic() {
        let config = LivenessConfig::default();
        let reference = vec![50.02, 50.04, 49.98, 49.96, 50.01];
        let authentic = vec![50.021, 50.039, 49.978, 49.961, 50.012];

        let result = verify_stream_liveness(&config, 250, 294.9, &authentic, &reference);
        assert!(result.is_authentic);
        assert_eq!(result.status, LivenessStatus::VerifiedLive);
    }
}
