use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub type BoundingBox = [f32; 4];
pub type Point = [f32; 2];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceSpec {
    Sample,
    Upload { upload_id: Uuid },
    Rtsp { uri: String },
    Http { uri: String },
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BackendKind {
    Simulator,
    Yolo26Command,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct JobRequest {
    pub name: String,
    pub source: SourceSpec,
    #[serde(default = "default_backend")]
    pub backend: BackendKind,
    #[serde(default = "default_detector_fps")]
    pub detector_fps: f32,
    #[serde(default = "default_monitor_duration_secs")]
    pub monitor_duration_secs: u64,
    #[serde(default)]
    pub gemma_enabled: bool,
    #[serde(default)]
    pub vlm_model: Option<String>,
    #[serde(default)]
    pub observations: Vec<Observation>,
    #[serde(default)]
    pub policy: AnalyticsPolicy,
}

fn default_backend() -> BackendKind {
    BackendKind::Simulator
}

fn default_detector_fps() -> f32 {
    5.0
}

fn default_monitor_duration_secs() -> u64 {
    120
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Observation {
    pub frame_time_ms: u64,
    pub track_id: String,
    pub class_name: String,
    pub confidence: f32,
    /// Normalized x, y, width, height in the source frame.
    pub bbox: BoundingBox,
    /// Development ReID descriptor. Production replaces this with a versioned OSNet/TensorRT embedding.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub appearance: Option<Vec<f32>>,
}

impl Observation {
    pub fn center(&self) -> Point {
        [
            self.bbox[0] + self.bbox[2] / 2.0,
            self.bbox[1] + self.bbox[3] / 2.0,
        ]
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct AnalyticsPolicy {
    #[serde(default)]
    pub zones: Vec<Zone>,
    #[serde(default)]
    pub lines: Vec<Line>,
    #[serde(default = "default_confirmation")]
    pub minimum_confirmation_observations: usize,
    #[serde(default = "default_dwell")]
    pub dwell_threshold_ms: u64,
}

fn default_confirmation() -> usize {
    3
}

fn default_dwell() -> u64 {
    10_000
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Zone {
    pub id: String,
    pub points: Vec<Point>,
    #[serde(default)]
    pub restricted: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Line {
    pub id: String,
    pub start: Point,
    pub end: Point,
    #[serde(default = "default_positive_label")]
    pub positive_to_negative_label: String,
    #[serde(default = "default_negative_label")]
    pub negative_to_positive_label: String,
}

fn default_positive_label() -> String {
    "inbound".to_owned()
}

fn default_negative_label() -> String {
    "outbound".to_owned()
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Queued,
    Running,
    Completed,
    Failed,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct JobRecord {
    pub id: Uuid,
    pub request: JobRequest,
    pub status: JobStatus,
    pub created_at_ms: u64,
    pub started_at_ms: Option<u64>,
    pub completed_at_ms: Option<u64>,
    pub result: Option<PipelineResult>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PipelineResult {
    pub backend: BackendKind,
    pub model: String,
    pub detector_fps: f32,
    pub observations_processed: usize,
    pub duration_ms: u64,
    pub tracks: Vec<TrackSummary>,
    pub events: Vec<ObservedEvent>,
    pub view_description: ViewDescription,
    pub deterministic_report: Report,
    pub report: Report,
    pub gemma: GemmaRun,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TrackSummary {
    pub track_id: String,
    pub class_name: String,
    pub started_at_ms: u64,
    pub ended_at_ms: u64,
    pub duration_ms: u64,
    pub observations: usize,
    pub maximum_confidence: f32,
    pub zones_visited: Vec<String>,
    #[serde(default, skip_serializing)]
    pub appearance_prototype: Option<Vec<f32>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ObservedEvent {
    pub event_id: Uuid,
    pub event_type: String,
    pub event_time_ms: u64,
    pub track_id: String,
    pub class_name: String,
    pub confidence: f32,
    pub zone_id: Option<String>,
    pub line_id: Option<String>,
    pub direction: Option<String>,
    pub description: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Report {
    pub headline: String,
    pub summary: String,
    pub notable_event_ids: Vec<Uuid>,
    pub observations: Vec<String>,
    pub data_quality_notes: Vec<String>,
    pub confidence: f32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GemmaRun {
    pub requested: bool,
    pub used: bool,
    pub model: Option<String>,
    pub fallback_reason: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ViewDescription {
    pub description: String,
    pub scene_type: String,
    pub visible_areas: Vec<String>,
    pub notable_static_elements: Vec<String>,
    pub visibility_conditions: String,
    pub confidence: f32,
    pub generated_by_model: bool,
    pub model: Option<String>,
    pub fallback_reason: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct UploadRecord {
    pub id: Uuid,
    pub original_name: String,
    pub content_type: String,
    pub size_bytes: u64,
    pub sha256: String,
    #[serde(skip)]
    pub path: std::path::PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct DetectorOutput {
    pub model: String,
    pub observations: Vec<Observation>,
    #[serde(default, skip_serializing)]
    pub representative_frame: Option<RepresentativeFrame>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RepresentativeFrame {
    pub media_type: String,
    pub data_base64: String,
    pub frame_time_ms: u64,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CapabilityResponse {
    pub service_version: String,
    pub local_state: String,
    pub simulator: bool,
    pub yolo26_command: bool,
    pub multi_camera_clusters: bool,
    pub retrieval_memory_v1: bool,
    pub max_cluster_cameras: usize,
    pub stream_protocols: Vec<String>,
    pub max_analysis_secs: u64,
    pub gemma_endpoint: String,
    pub lmstudio_api_endpoint: String,
    pub kafka_compiled: bool,
    pub kafka_enabled: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ClusterCameraInput {
    pub camera_id: String,
    #[serde(default)]
    pub label: String,
    pub uri: String,
    #[serde(default)]
    pub overlap_group: Option<String>,
    #[serde(default)]
    pub clock_offset_ms: i64,
    #[serde(default)]
    pub policy: AnalyticsPolicy,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CameraEdgeType {
    Overlap,
    Transition,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CameraTopologyEdge {
    pub edge_id: String,
    pub source_camera_id: String,
    pub target_camera_id: String,
    pub edge_type: CameraEdgeType,
    #[serde(default)]
    pub minimum_travel_ms: u64,
    #[serde(default = "default_maximum_travel_ms")]
    pub maximum_travel_ms: u64,
    #[serde(default = "default_edge_confidence")]
    pub confidence: f32,
}

fn default_maximum_travel_ms() -> u64 {
    30_000
}

fn default_edge_confidence() -> f32 {
    1.0
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AssociationConfig {
    #[serde(default = "default_appearance_threshold")]
    pub minimum_appearance_similarity: f32,
    #[serde(default = "default_provisional_threshold")]
    pub provisional_threshold: f32,
    #[serde(default = "default_final_threshold")]
    pub final_threshold: f32,
    #[serde(default = "default_overlap_tolerance_ms")]
    pub overlap_tolerance_ms: u64,
}

impl Default for AssociationConfig {
    fn default() -> Self {
        Self {
            minimum_appearance_similarity: default_appearance_threshold(),
            provisional_threshold: default_provisional_threshold(),
            final_threshold: default_final_threshold(),
            overlap_tolerance_ms: default_overlap_tolerance_ms(),
        }
    }
}

fn default_appearance_threshold() -> f32 {
    0.70
}

fn default_provisional_threshold() -> f32 {
    0.75
}

fn default_final_threshold() -> f32 {
    0.90
}

fn default_overlap_tolerance_ms() -> u64 {
    1_500
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ClusterJobRequest {
    pub name: String,
    pub cluster_id: String,
    pub cameras: Vec<ClusterCameraInput>,
    #[serde(default)]
    pub topology: Vec<CameraTopologyEdge>,
    #[serde(default)]
    pub association: AssociationConfig,
    #[serde(default = "default_detector_fps")]
    pub detector_fps: f32,
    #[serde(default = "default_monitor_duration_secs")]
    pub monitor_duration_secs: u64,
    #[serde(default)]
    pub gemma_enabled: bool,
    #[serde(default)]
    pub vlm_model: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ClusterJobRecord {
    pub id: Uuid,
    pub request: ClusterJobRequest,
    pub status: JobStatus,
    pub created_at_ms: u64,
    pub started_at_ms: Option<u64>,
    pub completed_at_ms: Option<u64>,
    pub result: Option<ClusterPipelineResult>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CameraAnalyticsResult {
    pub camera_id: String,
    pub label: String,
    pub overlap_group: Option<String>,
    pub clock_offset_ms: i64,
    /// Actual local worker start relative to the cluster job. This prevents
    /// queued camera intervals from being treated as simultaneous.
    #[serde(default)]
    pub processing_start_offset_ms: u64,
    pub pipeline: PipelineResult,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CameraProcessingFailure {
    pub camera_id: String,
    pub label: String,
    pub error: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AssociationDecisionState {
    FinalMatch,
    Provisional,
    Ambiguous,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AssociationDecision {
    pub association_id: Uuid,
    pub edge_id: Option<String>,
    pub source_camera_id: String,
    pub source_track_id: String,
    pub target_camera_id: String,
    pub target_track_id: String,
    pub edge_type: CameraEdgeType,
    pub appearance_similarity: f32,
    pub temporal_score: f32,
    pub score: f32,
    pub state: AssociationDecisionState,
    pub explanation: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GlobalTrackSegment {
    pub camera_id: String,
    pub local_track_id: String,
    pub class_name: String,
    pub started_at_ms: u64,
    pub ended_at_ms: u64,
    pub observations: usize,
    pub track_confidence: f32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GlobalTrack {
    pub global_id: Uuid,
    pub state: String,
    pub identity_confidence: f32,
    pub camera_ids: Vec<String>,
    pub segments: Vec<GlobalTrackSegment>,
    pub association_ids: Vec<Uuid>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ClusterPipelineResult {
    pub cluster_id: String,
    pub cameras_requested: usize,
    pub cameras_processed: usize,
    pub camera_failures: Vec<CameraProcessingFailure>,
    pub observations_processed: usize,
    pub local_tracks: usize,
    pub events: usize,
    pub duration_ms: u64,
    pub camera_results: Vec<CameraAnalyticsResult>,
    pub associations: Vec<AssociationDecision>,
    pub global_tracks: Vec<GlobalTrack>,
    pub view_description: ViewDescription,
    pub deterministic_report: Report,
    pub report: Report,
    pub gemma: GemmaRun,
    pub algorithm_version: String,
}

/// Camera metadata supplied by the backend. The aliases intentionally accept the
/// field names in the current backend example while serializing a stable snake_case API.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CameraProfile {
    #[serde(default)]
    pub camera_id: String,
    #[serde(alias = "liveurl")]
    pub live_url: String,
    #[serde(default, alias = "Country")]
    pub country: String,
    #[serde(default, alias = "Country code")]
    pub country_code: String,
    #[serde(default, alias = "Region")]
    pub region: String,
    #[serde(default, alias = "City")]
    pub city: String,
    #[serde(default, alias = "Latitude")]
    pub latitude: Option<f64>,
    #[serde(default, alias = "Longitude")]
    pub longitude: Option<f64>,
    #[serde(default, alias = "ZIP")]
    pub zip: Option<serde_json::Value>,
    #[serde(default, alias = "Timezone")]
    pub timezone: String,
    #[serde(default, alias = "Manufacturer")]
    pub manufacturer: String,
    #[serde(default)]
    pub description: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryJobRequest {
    pub name: String,
    #[serde(default)]
    pub cluster_id: Option<String>,
    pub cameras: Vec<CameraProfile>,
    #[serde(default = "default_monitor_duration_secs")]
    pub monitor_duration_secs: u64,
    #[serde(default = "default_observer_fps")]
    pub observer_fps: f32,
    #[serde(default)]
    pub vlm_enabled: bool,
    #[serde(default)]
    pub vlm_model: Option<String>,
}

fn default_observer_fps() -> f32 {
    1.0
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryJobRecord {
    pub id: Uuid,
    pub request: MemoryJobRequest,
    pub status: JobStatus,
    pub created_at_ms: u64,
    pub started_at_ms: Option<u64>,
    pub completed_at_ms: Option<u64>,
    pub result: Option<MemoryJobResult>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryJobResult {
    pub cluster_id: Option<String>,
    pub cameras_requested: usize,
    pub cameras_processed: usize,
    pub camera_failures: Vec<MemoryCameraFailure>,
    pub events_indexed: usize,
    pub source_duration_ms: u64,
    pub observer_frames_decoded: usize,
    pub camera_results: Vec<MemoryCameraResult>,
    pub algorithm_version: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryCameraFailure {
    pub camera_id: String,
    pub error: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryCameraResult {
    pub camera: CameraProfile,
    pub duration_ms: u64,
    pub frames_decoded: usize,
    pub evidence_url: String,
    pub events: Vec<MemoryEvent>,
    pub data_quality_notes: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct MemoryEventDescription {
    pub summary: String,
    pub scene_type: String,
    #[serde(default)]
    pub visible_objects: Vec<String>,
    #[serde(default)]
    pub visible_people: Vec<String>,
    #[serde(default)]
    pub apparent_actions: Vec<String>,
    #[serde(default)]
    pub visible_text: Vec<String>,
    #[serde(default)]
    pub conditions: String,
    pub confidence: f32,
    pub generated_by_model: bool,
    pub model: Option<String>,
    pub fallback_reason: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryEvent {
    pub event_id: Uuid,
    pub job_id: Uuid,
    pub camera_id: String,
    pub cluster_id: Option<String>,
    pub start_ms: u64,
    pub end_ms: u64,
    pub duration_ms: u64,
    pub activity_mean: f32,
    pub activity_peak: f32,
    pub quality: f32,
    pub boundary_reason: String,
    pub thumbnail_url: String,
    pub evidence_url: String,
    pub description: MemoryEventDescription,
    #[serde(default)]
    pub visual_signature: Vec<f32>,
    #[serde(skip)]
    pub thumbnail_path: std::path::PathBuf,
    #[serde(skip)]
    pub clip_path: std::path::PathBuf,
    #[serde(skip)]
    pub source_evidence_path: std::path::PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryQueryRequest {
    pub query: String,
    #[serde(default)]
    pub cluster_id: Option<String>,
    #[serde(default)]
    pub camera_ids: Vec<String>,
    #[serde(default)]
    pub start_ms: Option<u64>,
    #[serde(default)]
    pub end_ms: Option<u64>,
    #[serde(default = "default_query_limit")]
    pub limit: usize,
    #[serde(default)]
    pub vlm_enabled: bool,
    #[serde(default)]
    pub vlm_model: Option<String>,
}

fn default_query_limit() -> usize {
    10
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryQueryMatch {
    pub rank: usize,
    pub score: f32,
    pub matched_terms: Vec<String>,
    pub event: MemoryEvent,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MemoryQueryResponse {
    pub query_id: Uuid,
    pub query: String,
    pub summary: String,
    pub matches: Vec<MemoryQueryMatch>,
    pub events_considered: usize,
    pub retrieval_mode: String,
    pub model: Option<String>,
    pub fallback_reason: Option<String>,
}
