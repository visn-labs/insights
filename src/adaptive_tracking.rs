//! Adaptive Multi-Object Tracking (MOT) and Crowd Density Handover.
//!
//! Pinning production trackers (ByteTrack / BoT-SORT) and routing dynamically:
//! - Sparse/Moderate (<20 objects): ByteTrack or OC-SORT/Hybrid-SORT (sub-5 ms, zero drift).
//! - Wind/Camera Shake: BoT-SORT with Camera Motion Compensation (CMC) and ephemeral Re-ID.
//! - Extreme Downsampling: SR-Track (AAAI 2024).
//! - High-Density (>20-25 objects or >35 tight heads): Handover to point localization
//!   and crowd density estimation (STEERER / FIDTM / LCDnet) to prevent tracker FPS collapse.

use serde::{Deserialize, Serialize};

/// Concrete pinned multi-object tracker algorithms.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PinnedTracker {
    /// ByteTrack (pinned: bytetrack.yaml) - fastest, 29.6+ FPS on MOT17
    ByteTrack,
    /// BoT-SORT (pinned: botsort.yaml) - includes Camera Motion Compensation (CMC)
    BoTSort,
    /// OC-SORT / Deep OC-SORT - observation-centric momentum for non-linear movement
    OcSort,
    /// Hybrid-SORT (AAAI 2024) - weak cues & height-state compensation
    HybridSort,
    /// SR-Track (AAAI 2024) - specialized for heavily downsampled video streams
    SrTrack,
    /// STEERER / FIDTM point localization - density handover for crowded scenes
    FidtmPointLocalization,
}

/// Crowd density regimes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CrowdDensityRegime {
    /// 0 - 19 objects: Standard bounding box tracking
    SparseToModerate,
    /// 20 - 34 objects: High density alert, ByteTrack begins dropping FPS
    Dense,
    /// 35+ objects: Bounding boxes collapse; mandatory handover to density point map
    UltraDenseHandover,
}

/// Dynamic routing configuration for MOT and density estimation.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AdaptiveTrackingConfig {
    /// Object count threshold to trigger density handover
    pub density_handover_threshold: usize,
    /// Minimum camera vibration / global motion residual to trigger BoT-SORT CMC
    pub camera_motion_threshold: f32,
    /// Frame reduction ratio (e.g. 1.0 = normal, 9.0 = 1-in-9 downsampled)
    pub frame_reduction_ratio: f32,
    /// Explicit pinned tracker config path
    pub pinned_yaml_path: String,
}

impl Default for AdaptiveTrackingConfig {
    fn default() -> Self {
        Self {
            density_handover_threshold: 25,
            camera_motion_threshold: 4.5,
            frame_reduction_ratio: 1.0,
            pinned_yaml_path: "bytetrack.yaml".to_string(),
        }
    }
}

/// Telemetry and routing decision for a video frame.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AdaptiveTrackingDecision {
    /// Pinned tracker selected for execution
    pub selected_tracker: PinnedTracker,
    /// Crowd density classification
    pub density_regime: CrowdDensityRegime,
    /// Total detected objects/heads
    pub object_count: usize,
    /// True if camera shake/wind was detected requiring Camera Motion Compensation (CMC)
    pub camera_motion_compensated: bool,
    /// True if bounding box tracking was suspended in favor of FIDTM/STEERER point localization
    pub point_localization_active: bool,
    /// Estimated point coordinates [x, y] in normalized space when point localization is active
    pub localized_points: Vec<[f32; 2]>,
    /// Expected tracker throughput in FPS on edge compute (e.g. Jetson Orin Nano / T4)
    pub expected_fps: Option<f32>,
    /// Explanation of dynamic routing choice
    pub routing_reason: String,
}

/// Evaluates detected objects and camera stability to determine the optimal tracker routing.
pub fn evaluate_tracking_routing(
    config: &AdaptiveTrackingConfig,
    detected_boxes: &[[f32; 4]],
    camera_shake_norm: f32,
    downsample_ratio: f32,
) -> AdaptiveTrackingDecision {
    let count = detected_boxes.len();

    // 1. Check crowd density regime
    let regime = if count >= 35 || count >= config.density_handover_threshold {
        CrowdDensityRegime::UltraDenseHandover
    } else if count >= 20 {
        CrowdDensityRegime::Dense
    } else {
        CrowdDensityRegime::SparseToModerate
    };

    // 2. Evaluate handover condition
    if regime == CrowdDensityRegime::UltraDenseHandover {
        // Handover to STEERER / FIDTM (Focal Inverse Distance Transform Maps)
        let points: Vec<[f32; 2]> = detected_boxes
            .iter()
            .map(|b| [b[0] + b[2] / 2.0, b[1] + b[3] * 0.25]) // Head approximate point
            .collect();

        return AdaptiveTrackingDecision {
            selected_tracker: PinnedTracker::FidtmPointLocalization,
            density_regime: regime,
            object_count: count,
            camera_motion_compensated: false,
            point_localization_active: true,
            localized_points: points,
            expected_fps: None, // No tracker runtime benchmark has been performed.
            routing_reason: format!(
                "density_handover: {count} objects exceeds threshold {}, switching from bbox tracking to FIDTM/STEERER",
                config.density_handover_threshold
            ),
        };
    }

    // 3. Evaluate frame reduction downsampling
    if downsample_ratio >= 6.0 || config.frame_reduction_ratio >= 6.0 {
        return AdaptiveTrackingDecision {
            selected_tracker: PinnedTracker::SrTrack,
            density_regime: regime,
            object_count: count,
            camera_motion_compensated: false,
            point_localization_active: false,
            localized_points: Vec::new(),
            expected_fps: None, // No tracker runtime benchmark has been performed.
            routing_reason: format!(
                "downsampled_stream: frame reduction ratio {downsample_ratio:.1} >= 6.0 requires SR-Track (AAAI 2024)"
            ),
        };
    }

    // 4. Evaluate camera motion / pole vibration
    if camera_shake_norm >= config.camera_motion_threshold {
        return AdaptiveTrackingDecision {
            selected_tracker: PinnedTracker::BoTSort,
            density_regime: regime,
            object_count: count,
            camera_motion_compensated: true,
            point_localization_active: false,
            localized_points: Vec::new(),
            expected_fps: None, // No tracker runtime benchmark has been performed.
            routing_reason: format!(
                "camera_motion_detected: shake norm {camera_shake_norm:.2} >= {:.2}, routing to BoT-SORT with CMC",
                config.camera_motion_threshold
            ),
        };
    }

    // 5. Default pinned ByteTrack or OC-SORT for sparse/moderate static mounts
    AdaptiveTrackingDecision {
        selected_tracker: PinnedTracker::ByteTrack,
        density_regime: regime,
        object_count: count,
        camera_motion_compensated: false,
        point_localization_active: false,
        localized_points: Vec::new(),
        expected_fps: None, // No tracker runtime benchmark has been performed.
        routing_reason: format!(
            "sparse_scene: {count} objects on stable mount, running pinned ByteTrack (bytetrack.yaml)"
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routes_to_bytetrack_in_sparse_stable_scenes() {
        let config = AdaptiveTrackingConfig::default();
        let boxes = vec![[0.1, 0.1, 0.1, 0.2]; 5]; // 5 people
        let decision = evaluate_tracking_routing(&config, &boxes, 1.2, 1.0);

        assert_eq!(decision.selected_tracker, PinnedTracker::ByteTrack);
        assert!(!decision.point_localization_active);
        assert!(!decision.camera_motion_compensated);
    }

    #[test]
    fn routes_to_botsort_when_camera_shakes() {
        let config = AdaptiveTrackingConfig::default();
        let boxes = vec![[0.1, 0.1, 0.1, 0.2]; 12];
        let decision = evaluate_tracking_routing(&config, &boxes, 6.8, 1.0);

        assert_eq!(decision.selected_tracker, PinnedTracker::BoTSort);
        assert!(decision.camera_motion_compensated);
    }

    #[test]
    fn executes_handover_to_fidtm_in_dense_crowds() {
        let config = AdaptiveTrackingConfig {
            density_handover_threshold: 25,
            ..Default::default()
        };
        let boxes = vec![[0.1, 0.1, 0.05, 0.08]; 42]; // 42 pedestrians in plaza
        let decision = evaluate_tracking_routing(&config, &boxes, 0.5, 1.0);

        assert_eq!(
            decision.selected_tracker,
            PinnedTracker::FidtmPointLocalization
        );
        assert!(decision.point_localization_active);
        assert_eq!(decision.localized_points.len(), 42);
        assert!(decision.routing_reason.starts_with("density_handover"));
    }
}
