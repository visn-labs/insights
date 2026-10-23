//! Algorithmic Proof-of-Contribution (PoC) and Sustainable DePIN Tokenomics.
//!
//! Multiplicative 5-Factor Scoring Function:
//!   W_{i,t} = L_{i,t} * Q_{i,t} * H_{i,t} * D_{H3(i),t} * M_{loyalty}(i,t)
//! Payout:
//!   R_{i,t} = P_t * (W_{i,t} / \sum_j W_{j,t})
//!
//! Designed to prevent runaway token velocity (NATIX/Hivemapper failure modes),
//! enforce silicon sensor liveness, reward real entropy and commercial demand,
//! and comply with the Indian DPDP Act via non-staking forward loyalty tiers.

use serde::{Deserialize, Serialize};

/// Input telemetry parameters for a single camera node in an epoch.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CameraNodeEpochTelemetry {
    pub camera_id: String,
    pub contributor_id: String,
    pub h3_resolution_14_cell: u64,
    /// Number of overlapping contributor cameras in the same H3 res-14 cell (k >= 1)
    pub overlapping_cameras_in_cell: usize,
    /// Sensor verification: I-frame PRNU PCE score
    pub pce_score: f32,
    /// Temporal verification: ENF grid correlation rho
    pub enf_correlation: f32,
    /// Optical quality: effective resolution ratio [0.0, 1.0]
    pub resolution_quality: f32,
    /// Walkable Field-of-View ratio [0.0, 1.0]
    pub walkable_fov_ratio: f32,
    /// Detection confidence stability across lighting transitions [0.0, 1.0]
    pub detection_stability: f32,
    /// Compressed-domain motion vector entropy [0.0, 1.0]
    pub motion_entropy: f32,
    /// Visual frame appearance entropy [0.0, 1.0]
    pub visual_entropy: f32,
    /// Enterprise query volume hitting this H3 cell during epoch
    pub enterprise_queries_in_cell: usize,
    /// Commercial zoning prior weight [0.5, 3.0]
    pub commercial_zoning_prior: f32,
    /// Consecutive active uptime days S_{i,t}
    pub active_uptime_days: f32,
}

/// Global Proof-of-Contribution configuration parameters.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PocEngineConfig {
    /// PRNU PCE threshold tau_pce (authentic ~294.9, spoofed < 0)
    pub pce_threshold: f32,
    /// ENF correlation threshold tau_enf
    pub enf_threshold: f32,
    /// Minimum optical quality score (7000 basis points = 0.70)
    pub min_optical_quality: f32,
    /// Maximum forward loyalty boost alpha_max (0.50 -> 1.0 to 1.5 multiplier)
    pub loyalty_alpha_max: f32,
    /// Loyalty rate parameter lambda
    pub loyalty_lambda: f32,
}

impl Default for PocEngineConfig {
    fn default() -> Self {
        Self {
            pce_threshold: 50.0,
            enf_threshold: 0.65,
            min_optical_quality: 0.70,
            loyalty_alpha_max: 0.50,
            loyalty_lambda: 0.05, // Reaches ~1.39 at 30 days, ~1.48 at 60 days
        }
    }
}

/// Detailed 5-factor scoring breakdown for a camera node.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NodeContributionScore {
    pub camera_id: String,
    pub contributor_id: String,
    /// L_{i,t} in {0, 1}: Cryptographic liveness gate
    pub factor_l_liveness: f32,
    /// Q_{i,t} in [0.0, 1.0]: Optical and placement quality
    pub factor_q_quality: f32,
    /// H_{i,t} in [0.1, 2.0]: Spatio-temporal entropy & marginal spatial novelty
    pub factor_h_entropy: f32,
    /// D_{H3,t} in [0.5, 5.0]: Enterprise demand multiplier
    pub factor_d_demand: f32,
    /// M_{loyalty} in [1.0, 1.5]: Forward loyalty multiplier
    pub factor_m_loyalty: f32,
    /// Composite contribution weight W_{i,t}
    pub composite_weight: f32,
    /// Relative share of total epoch weight
    pub epoch_weight_share: f32,
    /// Final allocated payout R_{i,t} in platform points / fiat credits
    pub allocated_payout: f64,
    /// Status description
    pub status: String,
}

/// Summary result of an epoch reward distribution.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EpochRewardDistribution {
    pub epoch_index: u64,
    pub total_reward_pool: f64,
    pub total_nodes_evaluated: usize,
    pub verified_active_nodes: usize,
    pub spoofed_or_disqualified_nodes: usize,
    pub total_composite_weight: f32,
    pub node_scores: Vec<NodeContributionScore>,
}

/// Calculates the 5-factor contribution score for a single camera node.
pub fn calculate_node_weight(
    config: &PocEngineConfig,
    telemetry: &CameraNodeEpochTelemetry,
) -> NodeContributionScore {
    // Factor 1: Cryptographic Liveness Binary Gate L_{i,t}
    let pce_valid = telemetry.pce_score >= config.pce_threshold;
    let enf_valid = telemetry.enf_correlation >= config.enf_threshold;
    let factor_l = if pce_valid && enf_valid { 1.0 } else { 0.0 };

    // Factor 2: Placement & Optical Quality Q_{i,t}
    // Weighted combination of resolution, walkable FOV, and lighting stability
    let raw_quality = telemetry.resolution_quality * 0.40
        + telemetry.walkable_fov_ratio * 0.35
        + telemetry.detection_stability * 0.25;
    let factor_q = raw_quality.clamp(0.0, 1.0);

    // Factor 3: Information Entropy & Spatial Novelty H_{i,t}
    // Discounted by 1/sqrt(k) for k overlapping cameras in the same H3 resolution-14 cell
    let k = telemetry.overlapping_cameras_in_cell.max(1) as f32;
    let spatial_discount = 1.0 / k.sqrt();
    let combined_entropy = (telemetry.motion_entropy * 0.6 + telemetry.visual_entropy * 0.4) * 2.0;
    let factor_h = (combined_entropy * spatial_discount).clamp(0.1, 2.0);

    // Factor 4: Enterprise Demand Multiplier D_{H3(i),t}
    // Dynamic adjustment based on query volume + commercial zoning prior
    let query_boost = (telemetry.enterprise_queries_in_cell as f32 * 0.05).min(2.0);
    let factor_d = (telemetry.commercial_zoning_prior + query_boost).clamp(0.5, 5.0);

    // Factor 5: Compliance-Safe Forward Loyalty Multiplier M_{loyalty}(i,t)
    // M = 1 + alpha_max * (1 - exp(-lambda * S_{i,t}))
    let s = telemetry.active_uptime_days.max(0.0);
    let factor_m = 1.0 + config.loyalty_alpha_max * (1.0 - (-config.loyalty_lambda * s).exp());

    // Composite Weight W_{i,t} = L * Q * H * D * M
    let composite_weight = factor_l * factor_q * factor_h * factor_d * factor_m;

    let status = if factor_l == 0.0 {
        if !pce_valid && !enf_valid {
            "DISQUALIFIED: Spliced or synthetic injection detected (low PCE and uncorrelated ENF)"
        } else if !pce_valid {
            "DISQUALIFIED: Silicon PRNU sensor mismatch"
        } else {
            "DISQUALIFIED: ENF grid frequency mismatch (replay attack)"
        }
    } else if factor_q < config.min_optical_quality {
        "FLAGGED: Below minimum optical quality threshold (7000 bps)"
    } else {
        "VERIFIED: Eligible for epoch reward distribution"
    }
    .to_string();

    NodeContributionScore {
        camera_id: telemetry.camera_id.clone(),
        contributor_id: telemetry.contributor_id.clone(),
        factor_l_liveness: factor_l,
        factor_q_quality: factor_q,
        factor_h_entropy: factor_h,
        factor_d_demand: factor_d,
        factor_m_loyalty: factor_m,
        composite_weight,
        epoch_weight_share: 0.0, // Populated during epoch distribution
        allocated_payout: 0.0,
        status,
    }
}

/// Distributes an epoch's enterprise reward pool P_t across all contributor nodes.
pub fn distribute_epoch_rewards(
    config: &PocEngineConfig,
    epoch_index: u64,
    reward_pool_points: f64,
    nodes: &[CameraNodeEpochTelemetry],
) -> EpochRewardDistribution {
    let mut scores: Vec<_> = nodes
        .iter()
        .map(|node| calculate_node_weight(config, node))
        .collect();

    let total_weight: f32 = scores.iter().map(|s| s.composite_weight).sum();
    let mut verified_count = 0usize;
    let mut disqualified_count = 0usize;

    for score in &mut scores {
        if score.factor_l_liveness > 0.0 {
            verified_count += 1;
            if total_weight > 0.0 {
                score.epoch_weight_share = score.composite_weight / total_weight;
                score.allocated_payout = reward_pool_points * score.epoch_weight_share as f64;
            }
        } else {
            disqualified_count += 1;
            score.epoch_weight_share = 0.0;
            score.allocated_payout = 0.0;
        }
    }

    EpochRewardDistribution {
        epoch_index,
        total_reward_pool: reward_pool_points,
        total_nodes_evaluated: nodes.len(),
        verified_active_nodes: verified_count,
        spoofed_or_disqualified_nodes: disqualified_count,
        total_composite_weight: total_weight,
        node_scores: scores,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn liveness_gate_zeroes_replayed_stream_payout() {
        let config = PocEngineConfig::default();
        let telemetry = CameraNodeEpochTelemetry {
            camera_id: "cam_replay".into(),
            contributor_id: "user_123".into(),
            h3_resolution_14_cell: 123456,
            overlapping_cameras_in_cell: 1,
            pce_score: 290.0, // Valid sensor
            enf_correlation: 0.12, // Replayed yesterday -> ENF mismatch!
            resolution_quality: 0.95,
            walkable_fov_ratio: 0.90,
            detection_stability: 0.92,
            motion_entropy: 0.85,
            visual_entropy: 0.80,
            enterprise_queries_in_cell: 10,
            commercial_zoning_prior: 2.0,
            active_uptime_days: 45.0,
        };

        let score = calculate_node_weight(&config, &telemetry);
        assert_eq!(score.factor_l_liveness, 0.0);
        assert_eq!(score.composite_weight, 0.0);
        assert!(score.status.contains("DISQUALIFIED"));
    }

    #[test]
    fn distributes_epoch_pool_pro_rata() {
        let config = PocEngineConfig::default();
        let cam1 = CameraNodeEpochTelemetry {
            camera_id: "cam_1".into(),
            contributor_id: "contributor_a".into(),
            h3_resolution_14_cell: 100,
            overlapping_cameras_in_cell: 1,
            pce_score: 294.9,
            enf_correlation: 0.88,
            resolution_quality: 0.90,
            walkable_fov_ratio: 0.85,
            detection_stability: 0.90,
            motion_entropy: 0.80,
            visual_entropy: 0.75,
            enterprise_queries_in_cell: 20,
            commercial_zoning_prior: 2.5,
            active_uptime_days: 60.0,
        };

        let cam2 = CameraNodeEpochTelemetry {
            camera_id: "cam_2".into(),
            contributor_id: "contributor_b".into(),
            h3_resolution_14_cell: 200,
            overlapping_cameras_in_cell: 4, // 4 overlapping cameras -> spatial discount
            pce_score: 280.0,
            enf_correlation: 0.82,
            resolution_quality: 0.80,
            walkable_fov_ratio: 0.70,
            detection_stability: 0.85,
            motion_entropy: 0.50,
            visual_entropy: 0.50,
            enterprise_queries_in_cell: 5,
            commercial_zoning_prior: 1.0,
            active_uptime_days: 10.0,
        };

        let distribution = distribute_epoch_rewards(&config, 1, 10_000.0, &[cam1, cam2]);
        assert_eq!(distribution.verified_active_nodes, 2);
        assert_eq!(distribution.spoofed_or_disqualified_nodes, 0);

        // Cam1 should receive a significantly higher share than Cam2 due to entropy, commercial demand, and loyalty
        let score1 = &distribution.node_scores[0];
        let score2 = &distribution.node_scores[1];
        assert!(score1.allocated_payout > score2.allocated_payout);

        let payout_sum = score1.allocated_payout + score2.allocated_payout;
        assert!((payout_sum - 10_000.0).abs() < 0.1);
    }
}
