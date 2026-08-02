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
    #[serde(default)]
    pub gemma_enabled: bool,
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

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Observation {
    pub frame_time_ms: u64,
    pub track_id: String,
    pub class_name: String,
    pub confidence: f32,
    /// Normalized x, y, width, height in the source frame.
    pub bbox: BoundingBox,
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
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CapabilityResponse {
    pub service_version: String,
    pub local_state: String,
    pub simulator: bool,
    pub yolo26_command: bool,
    pub gemma_endpoint: String,
    pub kafka_compiled: bool,
    pub kafka_enabled: bool,
}
