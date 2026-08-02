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
    domain::{CapabilityResponse, JobRequest, UploadRecord},
    pipeline::{sample_observations, sample_policy},
    store::AppState,
    ui::{APP_JS, INDEX_HTML, STYLES_CSS},
};

pub fn router(state: AppState) -> Router {
    let max_upload = state.config.max_upload_bytes;
    Router::new()
        .route("/", get(index))
        .route("/app.js", get(app_js))
        .route("/styles.css", get(styles))
        .route("/healthz", get(health))
        .route("/api/v1/capabilities", get(capabilities))
        .route("/api/v1/models", get(models))
        .route("/api/v1/sample", get(sample))
        .route("/api/v1/uploads", post(upload).get(list_uploads))
        .route("/api/v1/uploads/{id}/content", get(upload_content))
        .route("/api/v1/jobs", post(create_job).get(list_jobs))
        .route("/api/v1/jobs/{id}", get(get_job))
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
        service_version: env!("CARGO_PKG_VERSION").to_owned(),
        local_state: "memory + local uploads; no database required".to_owned(),
        simulator: true,
        yolo26_command: true,
        stream_protocols: vec![
            "http".to_owned(),
            "https".to_owned(),
            "rtsp".to_owned(),
            "rtsps".to_owned(),
        ],
        max_analysis_secs: state.config.max_analysis_secs,
        gemma_endpoint: state.config.gemma_base_url.clone(),
        kafka_compiled: cfg!(feature = "kafka"),
        kafka_enabled: state.config.kafka_enabled,
    })
}

async fn models(State(state): State<AppState>) -> Json<serde_json::Value> {
    match state.pipeline_models().await {
        Ok(models) => Json(serde_json::json!({"available": true, "models": models})),
        Err(error) => Json(serde_json::json!({
            "available": false,
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
