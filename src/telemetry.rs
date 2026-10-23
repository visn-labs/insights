//! Experimental telemetry helpers.
//! These endpoints are not integrated or qualified video backends.
//! Caller-supplied measurements do not authenticate sources or authorize settlement.
#![allow(dead_code)]

use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use crate::adaptive_tracking::{
    evaluate_tracking_routing, AdaptiveTrackingConfig, AdaptiveTrackingDecision,
};
use crate::anomaly_cascade::{
    AnomalyCascadeConfig, AnomalyCascadeManager, AnomalyClass, AnomalyEvaluationResult,
    SceneGraphTriplet,
};
use crate::compressed_gating::{
    evaluate_compressed_gating, BitstreamMotionVector, CompressedGatingConfig,
    CompressedGatingDecision, MacroblockCategory,
};
use crate::liveness::{verify_stream_liveness, LivenessConfig, LivenessVerificationResult};
use crate::olap_h3::{
    generate_clickhouse_schema, InMemoryOlapTable, OlapFootfallQuery, OlapFootfallReport,
    TrajectoryOlapRecord,
};
use crate::poc_engine::{
    distribute_epoch_rewards, CameraNodeEpochTelemetry, EpochRewardDistribution, PocEngineConfig,
};
use crate::privacy_engine::{FaceDetection, PrivacyConfig, PrivacyManager, RedactionMask};
use crate::quantized_diskann::{
    calculate_vector_memory_sizing, QuantizedDiskAnnIndex, QuantizedSearchResult,
    VectorStorageSizingReport,
};
use crate::video_rag::{execute_video_rag, SpatioTemporalGraph, VideoRagResponse};

/// System-wide capabilities and telemetry status.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TelemetrySystemStatus {
    pub contract_version: String,
    pub capability_stage: String,
    pub limitations: Vec<String>,
    pub engine_version: String,
    pub compressed_domain_gating_enabled: bool,
    pub physical_liveness_prnu_enf_enabled: bool,
    pub privacy_three_tier_redaction_enabled: bool,
    pub adaptive_tracking_density_handover_enabled: bool,
    pub hierarchical_anomaly_cascade_enabled: bool,
    pub clickhouse_h3_olap_enabled: bool,
    pub quantized_1bit_diskann_enabled: bool,
    pub spatio_temporal_video_rag_enabled: bool,
    pub poc_five_factor_engine_enabled: bool,
    pub pinned_tracker_config: String,
    pub nominal_grid_frequency_hz: f32,
    pub memory_reduction_ratio: String,
}

/// Explicit state namespace. These identifiers scope prototypes; they do not authenticate a caller.
#[derive(Clone, Debug, Hash, PartialEq, Eq, Serialize, Deserialize)]
pub struct TelemetryScope {
    pub workspace_id: String,
    pub camera_id: String,
    pub session_id: String,
}

impl TelemetryScope {
    fn validate(&self) -> anyhow::Result<()> {
        for value in [&self.workspace_id, &self.camera_id, &self.session_id] {
            anyhow::ensure!(
                !value.is_empty()
                    && value.len() <= 128
                    && value
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || "_-".contains(c)),
                "invalid_telemetry_scope"
            );
        }
        Ok(())
    }
}

/// Shared coordinator for decentralized video network telemetry.
#[derive(Clone)]
pub struct TelemetryCoordinator {
    gating_config: CompressedGatingConfig,
    liveness_config: LivenessConfig,
    tracking_config: AdaptiveTrackingConfig,
    poc_config: PocEngineConfig,
    privacy_manager: Arc<Mutex<HashMap<TelemetryScope, (Option<u64>, PrivacyManager)>>>,
    anomaly_manager: Arc<Mutex<HashMap<TelemetryScope, (Option<u64>, AnomalyCascadeManager)>>>,
    olap_table: Arc<Mutex<InMemoryOlapTable>>,
    quantized_index: Arc<Mutex<QuantizedDiskAnnIndex>>,
    video_graph: Arc<Mutex<SpatioTemporalGraph>>,
}

impl TelemetryCoordinator {
    pub fn new() -> Self {
        let privacy_manager = Arc::new(Mutex::new(HashMap::new()));
        let anomaly_manager = Arc::new(Mutex::new(HashMap::new()));
        let olap_table = Arc::new(Mutex::new(InMemoryOlapTable::new()));
        let quantized_index = Arc::new(Mutex::new(QuantizedDiskAnnIndex::new(384)));
        let video_graph = Arc::new(Mutex::new(SpatioTemporalGraph::new()));

        Self {
            gating_config: CompressedGatingConfig::default(),
            liveness_config: LivenessConfig::default(),
            tracking_config: AdaptiveTrackingConfig::default(),
            poc_config: PocEngineConfig::default(),
            privacy_manager,
            anomaly_manager,
            olap_table,
            quantized_index,
            video_graph,
        }
    }

    pub fn status(&self) -> TelemetrySystemStatus {
        TelemetrySystemStatus {
            contract_version: "telemetry-evaluation-v2".into(),
            capability_stage: "prototype".into(),
            limitations: vec![
                "These helper endpoints are not integrated or qualified video backends".into(),
                "Scope identifiers do not authenticate callers; no reward settlement is authorized"
                    .into(),
                "Privacy responses contain masks only, not sanitized media".into(),
            ],
            engine_version: "2026.10-experimental-telemetry".to_string(),
            compressed_domain_gating_enabled: false,
            physical_liveness_prnu_enf_enabled: false,
            privacy_three_tier_redaction_enabled: false,
            adaptive_tracking_density_handover_enabled: false,
            hierarchical_anomaly_cascade_enabled: false,
            clickhouse_h3_olap_enabled: false,
            quantized_1bit_diskann_enabled: false,
            spatio_temporal_video_rag_enabled: false,
            poc_five_factor_engine_enabled: false,
            pinned_tracker_config: self.tracking_config.pinned_yaml_path.clone(),
            nominal_grid_frequency_hz: self.liveness_config.nominal_grid_hz,
            memory_reduction_ratio: "unmeasured; prototype retains FP32 and binary vectors in RAM"
                .to_string(),
        }
    }

    /// Evaluates compressed-domain motion vectors to decide if frame requires decoding.
    pub fn evaluate_gating(
        &self,
        macroblocks: &[MacroblockCategory],
        mvs: &[BitstreamMotionVector],
        bitrate_kbps: f32,
    ) -> CompressedGatingDecision {
        evaluate_compressed_gating(&self.gating_config, macroblocks, mvs, bitrate_kbps)
    }

    /// Performs PRNU sensor PCE correlation and ENF temporal grid cross-correlation.
    pub fn verify_liveness(
        &self,
        accumulated_frames: usize,
        pce_score: f32,
        enf_samples: &[f32],
        grid_reference: &[f32],
    ) -> LivenessVerificationResult {
        verify_stream_liveness(
            &self.liveness_config,
            accumulated_frames,
            pce_score,
            enf_samples,
            grid_reference,
        )
    }

    /// Executes 3-tier privacy redaction (YuNet detection + Kalman state persistence).
    pub fn process_privacy(
        &self,
        scope: &TelemetryScope,
        timestamp_ms: u64,
        detections: &[FaceDetection],
    ) -> anyhow::Result<Vec<RedactionMask>> {
        scope.validate()?;
        anyhow::ensure!(detections.len() <= 10000, "too_many_detections");
        for det in detections {
            let [x, y, w, h] = det.bbox;
            anyhow::ensure!(
                det.bbox.iter().all(|v| v.is_finite())
                    && x >= 0.0
                    && y >= 0.0
                    && w > 0.0
                    && h > 0.0
                    && x + w <= 1.0
                    && y + h <= 1.0
                    && det.confidence.is_finite()
                    && (0.0..=1.0).contains(&det.confidence),
                "invalid_face_detection"
            );
        }
        let mut states = self
            .privacy_manager
            .lock()
            .map_err(|_| anyhow::anyhow!("privacy_state_unavailable"))?;
        anyhow::ensure!(
            states.contains_key(scope) || states.len() < 256,
            "privacy_session_capacity"
        );
        let (last, manager) = states
            .entry(scope.clone())
            .or_insert_with(|| (None, PrivacyManager::new(PrivacyConfig::default())));
        anyhow::ensure!(
            last.is_none_or(|t| timestamp_ms > t),
            "nonmonotonic_privacy_frame"
        );
        let masks = manager.process_frame(detections);
        *last = Some(timestamp_ms);
        Ok(masks)
    }

    pub fn reset_session(&self, scope: &TelemetryScope) -> anyhow::Result<()> {
        scope.validate()?;
        self.privacy_manager
            .lock()
            .map_err(|_| anyhow::anyhow!("privacy_state_unavailable"))?
            .remove(scope);
        self.anomaly_manager
            .lock()
            .map_err(|_| anyhow::anyhow!("anomaly_state_unavailable"))?
            .remove(scope);
        Ok(())
    }

    /// Evaluates crowd density and camera vibration to route MOT or handover to FIDTM.
    pub fn route_tracking(
        &self,
        boxes: &[[f32; 4]],
        camera_shake_norm: f32,
        downsample_ratio: f32,
    ) -> AdaptiveTrackingDecision {
        evaluate_tracking_routing(
            &self.tracking_config,
            boxes,
            camera_shake_norm,
            downsample_ratio,
        )
    }

    /// Evaluates hierarchical anomaly cascade with temporal smoothing.
    pub fn evaluate_anomaly(
        &self,
        scope: &TelemetryScope,
        timestamp_ms: u64,
        mv_norm: f32,
        embedding_norm: f32,
        raw_score: Option<f32>,
        class: Option<AnomalyClass>,
        triplets: Vec<SceneGraphTriplet>,
    ) -> anyhow::Result<AnomalyEvaluationResult> {
        scope.validate()?;
        anyhow::ensure!(
            [mv_norm, embedding_norm]
                .iter()
                .all(|v| v.is_finite() && (0.0..=1.0).contains(v))
                && raw_score.is_none_or(|v| v.is_finite() && (0.0..=1.0).contains(&v)),
            "invalid_anomaly_score"
        );
        anyhow::ensure!(
            triplets.len() <= 1000
                && triplets.iter().all(|t| t.confidence.is_finite()
                    && (0.0..=1.0).contains(&t.confidence)
                    && t.subject.len() <= 256
                    && t.object.len() <= 256
                    && t.relationship.len() <= 256),
            "invalid_triplets"
        );
        let mut states = self
            .anomaly_manager
            .lock()
            .map_err(|_| anyhow::anyhow!("anomaly_state_unavailable"))?;
        anyhow::ensure!(
            states.contains_key(scope) || states.len() < 256,
            "anomaly_session_capacity"
        );
        let (last, manager) = states.entry(scope.clone()).or_insert_with(|| {
            (
                None,
                AnomalyCascadeManager::new(AnomalyCascadeConfig::default()),
            )
        });
        anyhow::ensure!(
            last.is_none_or(|t| timestamp_ms > t),
            "nonmonotonic_anomaly_frame"
        );
        let result = manager.evaluate_frame(
            timestamp_ms,
            mv_norm,
            embedding_norm,
            raw_score,
            class,
            triplets,
        );
        *last = Some(timestamp_ms);
        Ok(result)
    }

    /// Executes ClickHouse-compatible Spatio-Temporal OLAP query.
    pub fn query_olap(&self, query: &OlapFootfallQuery) -> OlapFootfallReport {
        self.olap_table
            .lock()
            .unwrap()
            .execute_footfall_query(query)
    }

    /// Ingests trajectory records into the Spatio-Temporal OLAP table.
    pub fn ingest_olap_records(&self, records: Vec<TrajectoryOlapRecord>) {
        self.olap_table.lock().unwrap().insert_batch(records);
    }

    /// Generates ClickHouse DDL schema.
    pub fn clickhouse_ddl(&self) -> String {
        generate_clickhouse_schema()
    }

    /// Calculates vector storage sizing comparison.
    pub fn vector_sizing(&self, dim: usize, m: usize, count: usize) -> VectorStorageSizingReport {
        calculate_vector_memory_sizing(dim, m, count)
    }

    /// Executes 1-bit Binary Quantized vector search with 2-stage rescoring.
    pub fn search_quantized(
        &self,
        query: &[f32],
        top_k: usize,
        oversample: f32,
    ) -> anyhow::Result<Vec<QuantizedSearchResult>> {
        self.quantized_index
            .lock()
            .map_err(|_| anyhow::anyhow!("vector_index_unavailable"))?
            .search(query, top_k, oversample)
    }

    /// Ingests vector into the 1-bit quantized index.
    pub fn insert_quantized_vector(&self, id: u64, vector: &[f32]) -> anyhow::Result<()> {
        self.quantized_index
            .lock()
            .map_err(|_| anyhow::anyhow!("vector_index_unavailable"))?
            .insert(id, vector)
    }

    /// Executes 4-phase VideoStir / EC-RAG Spatio-Temporal Video RAG query.
    pub fn query_video_rag(&self, query: &str) -> VideoRagResponse {
        let graph = self.video_graph.lock().unwrap();
        execute_video_rag(&graph, query, 2, 0.70, 14)
    }

    /// Calculates Peak-to-Correlation Energy (PCE).
    pub fn compute_pce(&self, correlation_matrix: &[f32], width: usize, height: usize) -> f32 {
        crate::liveness::compute_pce(correlation_matrix, width, height)
    }

    /// Computes Hilbert space-filling curve index.
    pub fn hilbert_encode(&self, x: u32, y: u32, order: u32) -> u64 {
        crate::olap_h3::hilbert_encode(x, y, order)
    }

    /// Converts latitude/longitude to UInt64 H3 index.
    pub fn lat_lng_to_h3_index(&self, lat: f64, lng: f64, resolution: u8) -> u64 {
        crate::olap_h3::lat_lng_to_h3_index(lat, lng, resolution)
    }

    /// Ingests clip node into VideoStir spatio-temporal graph.
    pub fn insert_rag_clip(&self, clip: crate::video_rag::ClipNode) {
        self.video_graph.lock().unwrap().insert_clip(clip);
    }

    /// Adds edge into VideoStir spatio-temporal graph.
    pub fn add_rag_edge(&self, edge: crate::video_rag::GraphEdge) {
        self.video_graph.lock().unwrap().add_edge(edge);
    }

    /// Distributes epoch enterprise data rewards via the 5-factor PoC function.
    pub fn distribute_poc(
        &self,
        epoch_index: u64,
        reward_pool: f64,
        nodes: &[CameraNodeEpochTelemetry],
    ) -> anyhow::Result<EpochRewardDistribution> {
        distribute_epoch_rewards(&self.poc_config, epoch_index, reward_pool, nodes)
    }
}

impl Default for TelemetryCoordinator {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coordinator_reports_full_blueprint_capabilities() {
        let coord = TelemetryCoordinator::new();
        let status = coord.status();

        assert!(!status.compressed_domain_gating_enabled);
        assert!(!status.physical_liveness_prnu_enf_enabled);
        assert!(!status.privacy_three_tier_redaction_enabled);
        assert!(!status.adaptive_tracking_density_handover_enabled);
        assert!(!status.hierarchical_anomaly_cascade_enabled);
        assert!(!status.clickhouse_h3_olap_enabled);
        assert!(!status.quantized_1bit_diskann_enabled);
        assert!(!status.spatio_temporal_video_rag_enabled);
        assert!(!status.poc_five_factor_engine_enabled);
        assert_eq!(status.nominal_grid_frequency_hz, 50.0);
    }

    #[test]
    fn coordinator_end_to_end_telemetry_flow() {
        let coord = TelemetryCoordinator::new();

        // 1. Bitstream gating
        let mbs = vec![MacroblockCategory::Skip; 50];
        let gating = coord.evaluate_gating(&mbs, &[], 1000.0);
        assert!(!gating.should_decode_frame);

        // 2. Physical liveness
        let ref_enf = vec![50.01, 50.02, 49.99, 49.97];
        let auth_enf = vec![50.011, 50.019, 49.989, 49.972];
        let live = coord.verify_liveness(250, 294.9, &auth_enf, &ref_enf);
        assert!(!live.is_authentic);

        // 3. Privacy redaction
        let dets = vec![FaceDetection {
            bbox: [0.2, 0.2, 0.1, 0.1],
            confidence: 0.85,
            landmarks: None,
        }];
        let masks = coord
            .process_privacy(
                &TelemetryScope {
                    workspace_id: "test".into(),
                    camera_id: "cam1".into(),
                    session_id: "s1".into(),
                },
                1,
                &dets,
            )
            .unwrap();
        assert_eq!(masks.len(), 1);

        // 4. Adaptive tracking
        let boxes = vec![[0.1, 0.1, 0.1, 0.1]; 30]; // 30 objects
        let track_decision = coord.route_tracking(&boxes, 1.0, 1.0);
        assert!(track_decision.point_localization_active);

        // 5. Anomaly cascade
        let anomaly = coord
            .evaluate_anomaly(
                &TelemetryScope {
                    workspace_id: "test".into(),
                    camera_id: "cam1".into(),
                    session_id: "s1".into(),
                },
                1000,
                0.1,
                0.1,
                None,
                None,
                Vec::new(),
            )
            .unwrap();
        assert!(!anomaly.stage1_passed_to_gpu);

        // 6. Quantized vector index
        let v1 = vec![1.0; 384];
        let v2 = vec![-1.0; 384];
        coord.insert_quantized_vector(101, &v1).unwrap();
        coord.insert_quantized_vector(102, &v2).unwrap();
        let q_results = coord.search_quantized(&v1, 2, 2.0).unwrap();
        assert_eq!(q_results.len(), 2);
        assert_eq!(q_results[0].vector_id, 101);

        // 7. Video RAG
        let rag = coord.query_video_rag("pedestrian entering crosswalk");
        assert_eq!(rag.decoupled_query.query_type, "visual_semantic_search");

        // 8. 5-factor PoC distribution
        let node = CameraNodeEpochTelemetry {
            camera_id: "cam_1".into(),
            contributor_id: "contributor_1".into(),
            h3_resolution_14_cell: 555,
            overlapping_cameras_in_cell: 1,
            pce_score: 294.9,
            enf_correlation: 0.85,
            resolution_quality: 0.90,
            walkable_fov_ratio: 0.85,
            detection_stability: 0.90,
            motion_entropy: 0.80,
            visual_entropy: 0.80,
            enterprise_queries_in_cell: 15,
            commercial_zoning_prior: 2.0,
            active_uptime_days: 30.0,
        };
        let epoch_dist = coord.distribute_poc(1, 5000.0, &[node]).unwrap();
        assert_eq!(epoch_dist.verified_active_nodes, 1);
        assert_eq!(epoch_dist.node_scores[0].allocated_payout, 5000.0);
    }
}
