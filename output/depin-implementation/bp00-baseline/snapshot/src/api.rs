use std::path::Path;

use axum::{
    body::Body,
    extract::{DefaultBodyLimit, Multipart, Path as AxumPath, State},
    http::{header, Request, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;
use tower::ServiceExt;
use tower_http::{
    cors::{Any, CorsLayer},
    services::ServeFile,
    trace::TraceLayer,
};
use uuid::Uuid;

use crate::{
    domain::{
        CapabilityResponse, ClusterJobRequest, JobRequest, MemoryJobRequest, MemoryQueryRequest,
        UploadRecord,
    },
    gemma::known_vlm_models,
    pipeline::{sample_observations, sample_policy},
    store::AppState,
    ui::{
        ANIME_JS, APP_JS, CONTRIBUTOR_CSS, CONTRIBUTOR_JS, ENGINE_JS, INDEX_HTML, INTRO_JS,
        JOURNEY_JS, STYLES_CSS, SVGS_JS, THREE_CORE_JS, THREE_JS, WALKING_JS,
    },
};

pub fn router(state: AppState) -> Router {
    let max_upload = state.config.max_upload_bytes;
    Router::new()
        .route("/", get(index))
        .route("/app.js", get(app_js))
        .route("/engine.js", get(engine_js))
        .route("/intro.js", get(intro_js))
        .route("/svgs.js", get(svgs_js))
        .route("/vendor/three.module.min.js", get(three_js))
        .route("/vendor/three.core.min.js", get(three_core_js))
        .route("/vendor/anime.umd.min.js", get(anime_js))
        .route("/styles.css", get(styles))
        .route("/assets/walking.js", get(walking_js))
        .route("/assets/contributor.js", get(contributor_js))
        .route("/assets/contributor.css", get(contributor_css))
        .route("/assets/journey.js", get(journey_js))
        .route("/assets/{filename}", get(serve_asset))
        .route("/svgs/{filename}", get(serve_svg))
        .route("/healthz", get(health))
        .route("/api/v1/capabilities", get(capabilities))
        .route("/api/v1/models", get(models))
        .route("/api/v1/sample", get(sample))
        .route("/api/v1/uploads", post(upload).get(list_uploads))
        .route("/api/v1/uploads/{id}/content", get(upload_content))
        .route("/api/v1/jobs", post(create_job).get(list_jobs))
        .route("/api/v1/jobs/{id}", get(get_job))
        .route(
            "/api/v1/cluster-jobs",
            post(create_cluster_job).get(list_cluster_jobs),
        )
        .route("/api/v1/cluster-jobs/{id}", get(get_cluster_job))
        .route(
            "/api/v1/memory-jobs",
            post(create_memory_job).get(list_memory_jobs),
        )
        .route("/api/v1/memory-jobs/{id}", get(get_memory_job))
        .route(
            "/api/v1/memory-jobs/{id}/visual-index",
            get(visual_index_status).post(start_visual_index),
        )
        .route(
            "/api/v1/memory-jobs/{id}/visual-index/{task_id}/{action}",
            post(visual_index_action),
        )
        .route(
            "/api/v1/memory-jobs/{id}/long-memory",
            post(build_entity_memory),
        )
        .route("/api/v1/memory-events/{id}/{kind}", get(memory_artifact))
        .route("/api/v1/memory-query", post(memory_query))
        .route("/api/v1/retrieval-status", get(retrieval_status))
        .route("/api/v1/telemetry/status", get(telemetry_status))
        .route("/api/v1/telemetry/gating", post(telemetry_gating))
        .route("/api/v1/telemetry/liveness", post(telemetry_liveness))
        .route("/api/v1/telemetry/redaction", post(telemetry_redaction))
        .route(
            "/api/v1/telemetry/adaptive-tracking",
            post(telemetry_adaptive_tracking),
        )
        .route("/api/v1/telemetry/anomaly", post(telemetry_anomaly))
        .route("/api/v1/telemetry/olap-query", post(telemetry_olap_query))
        .route("/api/v1/telemetry/olap-ingest", post(telemetry_olap_ingest))
        .route(
            "/api/v1/telemetry/quantized-search",
            post(telemetry_quantized_search),
        )
        .route(
            "/api/v1/telemetry/quantized-insert",
            post(telemetry_quantized_insert),
        )
        .route("/api/v1/telemetry/video-rag", post(telemetry_video_rag))
        .route("/api/v1/telemetry/poc-epoch", post(telemetry_poc_epoch))
        .route(
            "/api/v1/telemetry/clickhouse-schema",
            get(telemetry_clickhouse_schema),
        )
        .route(
            "/api/v1/telemetry/vector-sizing",
            get(telemetry_vector_sizing),
        )
        .layer(DefaultBodyLimit::max(max_upload))
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_headers(Any)
                .allow_methods(Any),
        )
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

async fn index() -> Html<&'static str> {
    Html(INDEX_HTML)
}

async fn app_js() -> impl IntoResponse {
    (
        [(
            header::CONTENT_TYPE,
            "application/javascript; charset=utf-8",
        )],
        APP_JS,
    )
}

async fn intro_js() -> impl IntoResponse {
    javascript(INTRO_JS)
}

async fn svgs_js() -> impl IntoResponse {
    javascript(SVGS_JS)
}

async fn three_js() -> impl IntoResponse {
    javascript(THREE_JS)
}

async fn three_core_js() -> impl IntoResponse {
    javascript(THREE_CORE_JS)
}

async fn anime_js() -> impl IntoResponse {
    javascript(ANIME_JS)
}

async fn engine_js() -> impl IntoResponse {
    javascript(ENGINE_JS)
}

async fn walking_js() -> impl IntoResponse {
    javascript(WALKING_JS)
}
async fn contributor_js() -> impl IntoResponse {
    javascript(CONTRIBUTOR_JS)
}
async fn journey_js() -> impl IntoResponse {
    javascript(JOURNEY_JS)
}
async fn contributor_css() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "text/css; charset=utf-8")],
        CONTRIBUTOR_CSS,
    )
}

fn javascript(body: &'static str) -> impl IntoResponse {
    (
        [(
            header::CONTENT_TYPE,
            "application/javascript; charset=utf-8",
        )],
        body,
    )
}

async fn serve_asset(
    AxumPath(filename): AxumPath<String>,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    serve_static_file("static/assets", &filename, request).await
}

async fn serve_svg(
    AxumPath(filename): AxumPath<String>,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    serve_static_file("static/svgs", &filename, request).await
}

async fn serve_static_file(
    dir: &str,
    filename: &str,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    // Prevent path traversal — only allow plain filenames with safe characters
    if filename.contains('/') || filename.contains('\\') || filename.starts_with('.') {
        return Err(ApiError::new(StatusCode::NOT_FOUND, "not found"));
    }
    let path = std::path::Path::new(dir).join(filename);
    if !path.is_file() {
        return Err(ApiError::new(StatusCode::NOT_FOUND, "asset not found"));
    }
    ServeFile::new(path)
        .oneshot(request)
        .await
        .map(IntoResponse::into_response)
        .map_err(|e| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))
}

async fn styles() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "text/css; charset=utf-8")],
        STYLES_CSS,
    )
}

async fn health(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "version": env!("CARGO_PKG_VERSION"),
        "sink": state.pipeline.sink_name()
    }))
}

async fn capabilities(State(state): State<AppState>) -> Json<CapabilityResponse> {
    Json(CapabilityResponse {
        entity_memory_version: crate::long_memory::VERSION.into(),
        service_version: env!("CARGO_PKG_VERSION").to_owned(),
        local_state: "memory + local uploads; no database required".to_owned(),
        simulator: true,
        yolo26_command: true,
        multi_camera_clusters: true,
        retrieval_memory_v1: true,
        max_cluster_cameras: state.config.max_cluster_cameras,
        stream_protocols: vec![
            "http".to_owned(),
            "https".to_owned(),
            "rtsp".to_owned(),
            "rtsps".to_owned(),
        ],
        max_analysis_secs: state.config.max_analysis_secs,
        gemma_endpoint: state.config.gemma_base_url.clone(),
        lmstudio_api_endpoint: state.config.lmstudio_api_base_url.clone(),
        kafka_compiled: cfg!(feature = "kafka"),
        kafka_enabled: state.config.kafka_enabled,
    })
}

async fn models(State(state): State<AppState>) -> Json<serde_json::Value> {
    let configured_vlms = known_vlm_models();
    match state.pipeline_models().await {
        Ok(models) => Json(serde_json::json!({
            "available": true,
            "configured_vlms": configured_vlms,
            "models": models
        })),
        Err(error) => Json(serde_json::json!({
            "available": false,
            "configured_vlms": configured_vlms,
            "models": [],
            "error": error.to_string()
        })),
    }
}

async fn sample() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "observations": sample_observations(),
        "policy": sample_policy()
    }))
}

async fn upload(
    State(state): State<AppState>,
    mut multipart: Multipart,
) -> Result<(StatusCode, Json<UploadRecord>), ApiError> {
    while let Some(mut field) = multipart.next_field().await? {
        if field.name() != Some("video") {
            continue;
        }
        let original_name = field.file_name().unwrap_or("video.bin").to_owned();
        let content_type = field
            .content_type()
            .unwrap_or("application/octet-stream")
            .to_owned();
        let id = Uuid::now_v7();
        let extension = Path::new(&original_name)
            .extension()
            .and_then(|value| value.to_str())
            .filter(|value| value.len() <= 10 && value.chars().all(|ch| ch.is_ascii_alphanumeric()))
            .map(|value| format!(".{value}"))
            .unwrap_or_default();
        let path = state.config.upload_dir().join(format!("{id}{extension}"));
        let mut file = tokio::fs::File::create(&path).await?;
        let mut size_bytes = 0_u64;
        let mut hash = Sha256::new();
        while let Some(chunk) = field.chunk().await? {
            size_bytes = size_bytes.saturating_add(chunk.len() as u64);
            if size_bytes > state.config.max_upload_bytes as u64 {
                drop(file);
                let _ = tokio::fs::remove_file(&path).await;
                return Err(ApiError::new(
                    StatusCode::PAYLOAD_TOO_LARGE,
                    "uploaded video exceeds VISN_MAX_UPLOAD_MB",
                ));
            }
            hash.update(&chunk);
            file.write_all(&chunk).await?;
        }
        file.flush().await?;
        let record = UploadRecord {
            id,
            original_name,
            content_type,
            size_bytes,
            sha256: format!("{:x}", hash.finalize()),
            path,
        };
        state.insert_upload(record.clone()).await;
        return Ok((StatusCode::CREATED, Json(record)));
    }
    Err(ApiError::new(
        StatusCode::BAD_REQUEST,
        "multipart field 'video' is required",
    ))
}

async fn list_uploads(State(state): State<AppState>) -> Json<Vec<UploadRecord>> {
    Json(state.uploads().await)
}

async fn upload_content(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<Uuid>,
) -> Result<Response, ApiError> {
    let upload = state
        .upload(id)
        .await
        .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "upload not found"))?;
    let mut response = ServeFile::new(upload.path)
        .oneshot(Request::new(Body::empty()))
        .await
        .map_err(|error| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, error.to_string()))?;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        upload
            .content_type
            .parse()
            .unwrap_or_else(|_| "application/octet-stream".parse().expect("valid mime")),
    );
    Ok(response.map(Body::new))
}

async fn create_job(
    State(state): State<AppState>,
    Json(request): Json<JobRequest>,
) -> Result<(StatusCode, Json<crate::domain::JobRecord>), ApiError> {
    let record = state.submit(request).await?;
    Ok((StatusCode::ACCEPTED, Json(record)))
}

async fn list_jobs(State(state): State<AppState>) -> Json<Vec<crate::domain::JobRecord>> {
    Json(state.jobs().await)
}

async fn get_job(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<Uuid>,
) -> Result<Json<crate::domain::JobRecord>, ApiError> {
    state
        .job(id)
        .await
        .map(Json)
        .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "job not found"))
}

async fn create_cluster_job(
    State(state): State<AppState>,
    Json(request): Json<ClusterJobRequest>,
) -> Result<(StatusCode, Json<crate::domain::ClusterJobRecord>), ApiError> {
    let record = state.submit_cluster(request).await?;
    Ok((StatusCode::ACCEPTED, Json(record)))
}

async fn list_cluster_jobs(
    State(state): State<AppState>,
) -> Json<Vec<crate::domain::ClusterJobRecord>> {
    Json(state.cluster_jobs().await)
}

async fn get_cluster_job(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<Uuid>,
) -> Result<Json<crate::domain::ClusterJobRecord>, ApiError> {
    state
        .cluster_job(id)
        .await
        .map(Json)
        .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "cluster job not found"))
}

async fn create_memory_job(
    State(state): State<AppState>,
    Json(request): Json<MemoryJobRequest>,
) -> Result<(StatusCode, Json<crate::domain::MemoryJobRecord>), ApiError> {
    let record = state.submit_memory(request).await?;
    Ok((StatusCode::ACCEPTED, Json(record)))
}

async fn list_memory_jobs(
    State(state): State<AppState>,
) -> Json<Vec<crate::domain::MemoryJobRecord>> {
    Json(state.memory_jobs().await)
}

async fn get_memory_job(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<Uuid>,
) -> Result<Json<crate::domain::MemoryJobRecord>, ApiError> {
    state
        .memory_job(id)
        .await
        .map(Json)
        .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "memory job not found"))
}

async fn memory_artifact(
    State(state): State<AppState>,
    AxumPath((id, kind)): AxumPath<(Uuid, String)>,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    if !matches!(kind.as_str(), "thumbnail" | "clip" | "source") {
        return Err(ApiError::new(
            StatusCode::NOT_FOUND,
            "memory artifact not found",
        ));
    }
    let path = state
        .memory_event_artifact(id, &kind)
        .await
        .filter(|path| path.is_file())
        .ok_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "memory artifact not found"))?;
    ServeFile::new(path)
        .oneshot(request)
        .await
        .map(IntoResponse::into_response)
        .map_err(|error| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, error.to_string()))
}

async fn build_entity_memory(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<Uuid>,
) -> Result<Json<crate::domain::MemoryJobRecord>, ApiError> {
    Ok(Json(state.build_entity_memory(id).await?))
}

async fn memory_query(
    State(state): State<AppState>,
    Json(request): Json<MemoryQueryRequest>,
) -> Result<Json<crate::domain::MemoryQueryResponse>, ApiError> {
    Ok(Json(state.query_memory(request).await?))
}

async fn worker_control(
    state: &AppState,
    method: reqwest::Method,
    path: &str,
    body: Option<serde_json::Value>,
) -> Result<(StatusCode, Json<serde_json::Value>), ApiError> {
    let url = state.config.retrieval_url.as_ref().ok_or_else(|| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Visual search is not configured",
        )
    })?;
    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(2))
        .timeout(std::time::Duration::from_secs(
            state.config.retrieval_timeout_secs,
        ))
        .build()
        .map_err(|e| ApiError::new(StatusCode::SERVICE_UNAVAILABLE, e.to_string()))?;
    let mut request = client.request(method, format!("{}{path}", url.trim_end_matches('/')));
    if let Some(body) = body {
        request = request.json(&body);
    }
    let response = request.send().await.map_err(|_| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Visual worker unavailable; retained recordings and keyword search remain available",
        )
    })?;
    let status = response.status();
    let value = response
        .json()
        .await
        .map_err(|_| ApiError::new(StatusCode::BAD_GATEWAY, "Invalid visual worker response"))?;
    Ok((status, Json(value)))
}

async fn visual_index_status(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<Uuid>,
) -> Result<(StatusCode, Json<serde_json::Value>), ApiError> {
    if !state.memory_job_exists(id).await {
        return Err(ApiError::new(StatusCode::NOT_FOUND, "Recording not found"));
    }
    worker_control(
        &state,
        reqwest::Method::GET,
        &format!("/index-jobs?scope_id={id}"),
        None,
    )
    .await
}

async fn start_visual_index(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<Uuid>,
) -> Result<(StatusCode, Json<serde_json::Value>), ApiError> {
    let events = state.visual_index_events(id).await?;
    worker_control(
        &state,
        reqwest::Method::POST,
        "/index-jobs",
        Some(serde_json::json!({"scope_id":id,"events":events})),
    )
    .await
}

async fn visual_index_action(
    State(state): State<AppState>,
    AxumPath((id, task_id, action)): AxumPath<(Uuid, Uuid, String)>,
) -> Result<(StatusCode, Json<serde_json::Value>), ApiError> {
    if !state.memory_job_exists(id).await {
        return Err(ApiError::new(StatusCode::NOT_FOUND, "Recording not found"));
    }
    if !matches!(action.as_str(), "cancel" | "retry") {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "Unknown indexing action",
        ));
    }
    let (status, Json(task)) = worker_control(
        &state,
        reqwest::Method::GET,
        &format!("/index-jobs/{task_id}"),
        None,
    )
    .await?;
    if !status.is_success() {
        return Ok((status, Json(task)));
    }
    if task["scope_id"].as_str() != Some(id.to_string().as_str()) {
        return Err(ApiError::new(
            StatusCode::NOT_FOUND,
            "Index task does not belong to this recording",
        ));
    }
    worker_control(
        &state,
        reqwest::Method::POST,
        &format!("/index-jobs/{task_id}/{action}"),
        None,
    )
    .await
}

async fn retrieval_status(State(state): State<AppState>) -> Json<serde_json::Value> {
    let Some(url) = &state.config.retrieval_url else {
        return Json(serde_json::json!({"available": false, "configured": false,
            "message": "Visual search is not configured. Start scripts/run_memory.sh, or set VISN_RETRIEVAL_URL and run the Qwen worker. Keyword search remains available."}));
    };
    let result = async {
        reqwest::Client::new()
            .get(format!("{}/health", url.trim_end_matches('/')))
            .timeout(std::time::Duration::from_secs(2))
            .send()
            .await?
            .error_for_status()?
            .json::<serde_json::Value>()
            .await
    }
    .await;
    match result {
        Ok(worker) if worker["status"] == "ready" => Json(serde_json::json!({
            "available": true, "configured": true, "worker": worker,
            "message": "Qwen worker reachable. Prepare visual search in the background or search directly. Queue state and actual retrieval mode are reported separately."})),
        _ => Json(serde_json::json!({"available": false, "configured": true,
            "message": "Qwen worker unavailable. Check the worker process and VISN_RETRIEVAL_URL. Keyword fallback will be reported explicitly."})),
    }
}

#[derive(Debug)]
struct ApiError {
    status: StatusCode,
    message: String,
}

impl ApiError {
    fn new(status: StatusCode, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
        }
    }
}

impl From<anyhow::Error> for ApiError {
    fn from(error: anyhow::Error) -> Self {
        Self::new(StatusCode::BAD_REQUEST, error.to_string())
    }
}

impl From<std::io::Error> for ApiError {
    fn from(error: std::io::Error) -> Self {
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, error.to_string())
    }
}

impl From<axum::extract::multipart::MultipartError> for ApiError {
    fn from(error: axum::extract::multipart::MultipartError) -> Self {
        Self::new(StatusCode::BAD_REQUEST, error.to_string())
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let body = Json(ErrorBody {
            error: self.message,
        });
        (self.status, body).into_response()
    }
}

#[derive(Serialize)]
struct ErrorBody {
    error: String,
}

#[derive(serde::Deserialize)]
struct TelemetryGatingPayload {
    #[serde(default)]
    macroblocks: Vec<crate::compressed_gating::MacroblockCategory>,
    #[serde(default)]
    motion_vectors: Vec<crate::compressed_gating::BitstreamMotionVector>,
    #[serde(default = "default_bitrate")]
    bitrate_kbps: f32,
}

fn default_bitrate() -> f32 {
    1500.0
}

#[derive(serde::Deserialize)]
struct TelemetryLivenessPayload {
    #[serde(default = "default_calibration_frames")]
    accumulated_frames: usize,
    #[serde(default = "default_pce")]
    pce_score: f32,
    #[serde(default)]
    enf_samples: Vec<f32>,
    #[serde(default)]
    grid_reference: Vec<f32>,
}

fn default_calibration_frames() -> usize {
    200
}

fn default_pce() -> f32 {
    294.9
}

#[derive(serde::Deserialize)]
struct TelemetryRedactionPayload {
    detections: Vec<crate::privacy_engine::FaceDetection>,
}

#[derive(serde::Deserialize)]
struct TelemetryAdaptiveTrackingPayload {
    detected_boxes: Vec<[f32; 4]>,
    #[serde(default)]
    camera_shake_norm: f32,
    #[serde(default = "default_downsample")]
    downsample_ratio: f32,
}

fn default_downsample() -> f32 {
    1.0
}

#[derive(serde::Deserialize)]
struct TelemetryAnomalyPayload {
    timestamp_ms: u64,
    motion_vector_norm: f32,
    embedding_norm: f32,
    #[serde(default)]
    raw_classifier_score: Option<f32>,
    #[serde(default)]
    detected_class: Option<crate::anomaly_cascade::AnomalyClass>,
    #[serde(default)]
    triplets: Vec<crate::anomaly_cascade::SceneGraphTriplet>,
}

#[derive(serde::Deserialize)]
struct TelemetryOlapIngestPayload {
    records: Vec<crate::olap_h3::TrajectoryOlapRecord>,
}

#[derive(serde::Deserialize)]
struct TelemetryQuantizedSearchPayload {
    query: Vec<f32>,
    #[serde(default = "default_top_k")]
    top_k: usize,
    #[serde(default = "default_oversample")]
    oversample_factor: f32,
}

fn default_top_k() -> usize {
    10
}

fn default_oversample() -> f32 {
    2.5
}

#[derive(serde::Deserialize)]
struct TelemetryQuantizedInsertPayload {
    id: u64,
    vector: Vec<f32>,
}

#[derive(serde::Deserialize)]
struct TelemetryVideoRagPayload {
    query: String,
}

#[derive(serde::Deserialize)]
struct TelemetryPocEpochPayload {
    epoch_index: u64,
    reward_pool: f64,
    nodes: Vec<crate::poc_engine::CameraNodeEpochTelemetry>,
}

#[derive(serde::Deserialize)]
struct TelemetryVectorSizingParams {
    #[serde(default = "default_dim")]
    dimension: usize,
    #[serde(default = "default_m")]
    graph_degree_m: usize,
    #[serde(default = "default_count")]
    vector_count: usize,
}

fn default_dim() -> usize {
    384
}

fn default_m() -> usize {
    16
}

fn default_count() -> usize {
    100_000_000
}

async fn telemetry_status(
    State(state): State<AppState>,
) -> Json<crate::telemetry::TelemetrySystemStatus> {
    Json(state.telemetry.status())
}

async fn telemetry_gating(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryGatingPayload>,
) -> Json<crate::compressed_gating::CompressedGatingDecision> {
    let decision = state.telemetry.evaluate_gating(
        &payload.macroblocks,
        &payload.motion_vectors,
        payload.bitrate_kbps,
    );
    Json(decision)
}

async fn telemetry_liveness(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryLivenessPayload>,
) -> Json<crate::liveness::LivenessVerificationResult> {
    let result = state.telemetry.verify_liveness(
        payload.accumulated_frames,
        payload.pce_score,
        &payload.enf_samples,
        &payload.grid_reference,
    );
    Json(result)
}

async fn telemetry_redaction(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryRedactionPayload>,
) -> Json<Vec<crate::privacy_engine::RedactionMask>> {
    let masks = state.telemetry.process_privacy(&payload.detections);
    Json(masks)
}

async fn telemetry_adaptive_tracking(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryAdaptiveTrackingPayload>,
) -> Json<crate::adaptive_tracking::AdaptiveTrackingDecision> {
    let decision = state.telemetry.route_tracking(
        &payload.detected_boxes,
        payload.camera_shake_norm,
        payload.downsample_ratio,
    );
    Json(decision)
}

async fn telemetry_anomaly(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryAnomalyPayload>,
) -> Json<crate::anomaly_cascade::AnomalyEvaluationResult> {
    let result = state.telemetry.evaluate_anomaly(
        payload.timestamp_ms,
        payload.motion_vector_norm,
        payload.embedding_norm,
        payload.raw_classifier_score,
        payload.detected_class,
        payload.triplets,
    );
    Json(result)
}

async fn telemetry_olap_query(
    State(state): State<AppState>,
    Json(query): Json<crate::olap_h3::OlapFootfallQuery>,
) -> Json<crate::olap_h3::OlapFootfallReport> {
    let report = state.telemetry.query_olap(&query);
    Json(report)
}

async fn telemetry_olap_ingest(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryOlapIngestPayload>,
) -> Json<serde_json::Value> {
    let count = payload.records.len();
    state.telemetry.ingest_olap_records(payload.records);
    Json(serde_json::json!({ "ingested_count": count }))
}

async fn telemetry_quantized_search(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryQuantizedSearchPayload>,
) -> Json<Vec<crate::quantized_diskann::QuantizedSearchResult>> {
    let results = state.telemetry.search_quantized(
        &payload.query,
        payload.top_k,
        payload.oversample_factor,
    );
    Json(results)
}

async fn telemetry_quantized_insert(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryQuantizedInsertPayload>,
) -> Json<serde_json::Value> {
    state
        .telemetry
        .insert_quantized_vector(payload.id, &payload.vector);
    Json(serde_json::json!({ "inserted": true, "id": payload.id }))
}

async fn telemetry_video_rag(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryVideoRagPayload>,
) -> Json<crate::video_rag::VideoRagResponse> {
    let response = state.telemetry.query_video_rag(&payload.query);
    Json(response)
}

async fn telemetry_poc_epoch(
    State(state): State<AppState>,
    Json(payload): Json<TelemetryPocEpochPayload>,
) -> Json<crate::poc_engine::EpochRewardDistribution> {
    let dist = state.telemetry.distribute_poc(
        payload.epoch_index,
        payload.reward_pool,
        &payload.nodes,
    );
    Json(dist)
}

async fn telemetry_clickhouse_schema(
    State(state): State<AppState>,
) -> Json<serde_json::Value> {
    let ddl = state.telemetry.clickhouse_ddl();
    Json(serde_json::json!({ "schema_ddl": ddl }))
}

async fn telemetry_vector_sizing(
    axum::extract::Query(params): axum::extract::Query<TelemetryVectorSizingParams>,
    State(state): State<AppState>,
) -> Json<crate::quantized_diskann::VectorStorageSizingReport> {
    let report = state.telemetry.vector_sizing(
        params.dimension,
        params.graph_degree_m,
        params.vector_count,
    );
    Json(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use tower::ServiceExt;

    fn build_test_state() -> AppState {
        let temp_dir = std::env::temp_dir().join(format!("visn_test_{}", Uuid::now_v7()));
        let config = Arc::new(crate::config::Config {
            bind: "127.0.0.1:0".into(),
            data_dir: temp_dir,
            max_upload_bytes: 10 * 1024 * 1024,
            log_filter: "warn".into(),
            gemma_base_url: "http://127.0.0.1:1234/v1".into(),
            lmstudio_api_base_url: "http://127.0.0.1:1234/api/v1".into(),
            gemma_model: None,
            gemma_api_key: "".into(),
            gemma_timeout_secs: 5,
            vlm_context_length: 2048,
            vlm_eval_batch_size: 1,
            vlm_max_output_tokens: 256,
            vlm_flash_attention: false,
            vlm_offload_kv_cache_to_gpu: false,
            vlm_exclusive_media: false,
            detector_executable: "python3".into(),
            detector_args: vec![],
            detector_worker_args: vec![],
            memory_runner_args: vec![],
            yolo_model: "yolo26s.pt".into(),
            persistent_detector: false,
            persistent_detector_fallback: true,
            detector_batch_size: 1,
            detector_batch_wait_ms: 10,
            detector_worker_idle_secs: 10,
            detector_image_size: 640,
            detector_device: None,
            detector_warmup: false,
            detector_threads: 1,
            appearance_interval_secs: 0.0,
            max_analysis_secs: 60,
            max_memory_events_per_camera: 10,
            max_vlm_events_per_camera: 5,
            max_cluster_cameras: 4,
            max_concurrent_cameras: 2,
            max_ephemeral_jobs: 10,
            memory_clip_mode: "browser".into(),
            retrieval_url: None,
            retrieval_timeout_secs: 5,
            resource_dir: None,
            resource_wait_secs: 10,
            resource_runner_args: vec![],
            managed_lmstudio: false,
            kafka_enabled: false,
        });

        let media_worker_gate = Arc::new(tokio::sync::Semaphore::new(2));
        let detector_worker = crate::detector_worker::DetectorWorker::new(config.clone());
        let gemma = crate::gemma::GemmaClient::new(
            config.clone(),
            media_worker_gate.clone(),
            detector_worker.clone(),
        )
        .unwrap();
        let memory = crate::memory::MemoryService::new(
            config.clone(),
            gemma.clone(),
            media_worker_gate.clone(),
            detector_worker.clone(),
        );
        let sink = crate::sink::build_sink(config.clone()).unwrap();
        let pipeline = crate::pipeline::PipelineService::new(
            config.clone(),
            gemma,
            sink,
            media_worker_gate,
            detector_worker,
        );
        AppState::new(config, pipeline, memory)
    }

    #[tokio::test]
    async fn test_telemetry_status_endpoint() {
        let state = build_test_state();
        let app = router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/api/v1/telemetry/status")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_telemetry_gating_endpoint() {
        let state = build_test_state();
        let app = router(state);

        let body = serde_json::json!({
            "macroblocks": ["skip", "skip"],
            "motion_vectors": [],
            "bitrate_kbps": 1200.0
        });

        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/v1/telemetry/gating")
                    .header("content-type", "application/json")
                    .body(Body::from(serde_json::to_vec(&body).unwrap()))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_telemetry_liveness_endpoint() {
        let state = build_test_state();
        let app = router(state);

        let body = serde_json::json!({
            "accumulated_frames": 250,
            "pce_score": 294.9,
            "enf_samples": [50.01, 50.02, 49.99, 49.98],
            "grid_reference": [50.012, 50.019, 49.991, 49.979]
        });

        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/v1/telemetry/liveness")
                    .header("content-type", "application/json")
                    .body(Body::from(serde_json::to_vec(&body).unwrap()))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
    }
}
