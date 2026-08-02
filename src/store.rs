use std::{
    collections::HashMap,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{bail, Context};
use tokio::sync::RwLock;
use tracing::error;
use uuid::Uuid;

use crate::{
    config::Config,
    domain::{JobRecord, JobRequest, JobStatus, SourceSpec, UploadRecord},
    pipeline::{PipelineService, ResolvedSource},
};

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub pipeline: PipelineService,
    jobs: Arc<RwLock<HashMap<Uuid, JobRecord>>>,
    uploads: Arc<RwLock<HashMap<Uuid, UploadRecord>>>,
}

impl AppState {
    pub fn new(config: Arc<Config>, pipeline: PipelineService) -> Self {
        Self {
            config,
            pipeline,
            jobs: Arc::new(RwLock::new(HashMap::new())),
            uploads: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    pub async fn insert_upload(&self, upload: UploadRecord) {
        self.uploads.write().await.insert(upload.id, upload);
    }

    pub async fn upload(&self, id: Uuid) -> Option<UploadRecord> {
        self.uploads.read().await.get(&id).cloned()
    }

    pub async fn uploads(&self) -> Vec<UploadRecord> {
        let mut uploads: Vec<_> = self.uploads.read().await.values().cloned().collect();
        uploads.sort_by(|left, right| left.original_name.cmp(&right.original_name));
        uploads
    }

    pub async fn pipeline_models(&self) -> anyhow::Result<Vec<crate::gemma::ModelInfo>> {
        self.pipeline.models().await
    }

    pub async fn jobs(&self) -> Vec<JobRecord> {
        let mut jobs: Vec<_> = self.jobs.read().await.values().cloned().collect();
        jobs.sort_by_key(|job| std::cmp::Reverse(job.created_at_ms));
        jobs.into_iter().map(redact_job).collect()
    }

    pub async fn job(&self, id: Uuid) -> Option<JobRecord> {
        self.jobs.read().await.get(&id).cloned().map(redact_job)
    }

    pub async fn submit(&self, request: JobRequest) -> anyhow::Result<JobRecord> {
        validate_request(&request)?;
        let source = match &request.source {
            SourceSpec::Sample => ResolvedSource::Sample,
            SourceSpec::Upload { upload_id } => {
                let upload = self
                    .uploads
                    .read()
                    .await
                    .get(upload_id)
                    .cloned()
                    .with_context(|| format!("upload {upload_id} was not found"))?;
                ResolvedSource::Upload(upload.path)
            }
            SourceSpec::Rtsp { uri } => ResolvedSource::Rtsp(uri.clone()),
        };

        let id = Uuid::now_v7();
        let record = JobRecord {
            id,
            request: request.clone(),
            status: JobStatus::Queued,
            created_at_ms: now_ms(),
            started_at_ms: None,
            completed_at_ms: None,
            result: None,
            error: None,
        };
        self.jobs.write().await.insert(id, record.clone());

        let state = self.clone();
        tokio::spawn(async move {
            {
                if let Some(job) = state.jobs.write().await.get_mut(&id) {
                    job.status = JobStatus::Running;
                    job.started_at_ms = Some(now_ms());
                }
            }
            let result = state.pipeline.process(id, &request, source).await;
            let mut jobs = state.jobs.write().await;
            if let Some(job) = jobs.get_mut(&id) {
                job.completed_at_ms = Some(now_ms());
                match result {
                    Ok(result) => {
                        job.status = JobStatus::Completed;
                        job.result = Some(result);
                    }
                    Err(error) => {
                        error!(%id, error = %error, "pipeline job failed");
                        job.status = JobStatus::Failed;
                        job.error = Some(error.to_string());
                    }
                }
            }
        });

        Ok(redact_job(record))
    }
}

fn validate_request(request: &JobRequest) -> anyhow::Result<()> {
    if request.name.trim().is_empty() || request.name.len() > 120 {
        bail!("name must contain 1 to 120 characters");
    }
    if !(0.1..=60.0).contains(&request.detector_fps) {
        bail!("detector_fps must be between 0.1 and 60");
    }
    if let SourceSpec::Rtsp { uri } = &request.source {
        if !uri.starts_with("rtsp://") && !uri.starts_with("rtsps://") {
            bail!("RTSP source must start with rtsp:// or rtsps://");
        }
        if uri.chars().any(char::is_whitespace) {
            bail!("RTSP source must not contain whitespace");
        }
    }
    for zone in &request.policy.zones {
        if zone.id.trim().is_empty() || zone.points.len() < 3 {
            bail!("each zone needs an id and at least three points");
        }
        validate_points(&zone.points)?;
    }
    for line in &request.policy.lines {
        if line.id.trim().is_empty() {
            bail!("each line needs an id");
        }
        validate_points(&[line.start, line.end])?;
    }
    Ok(())
}

fn validate_points(points: &[[f32; 2]]) -> anyhow::Result<()> {
    if points
        .iter()
        .flatten()
        .any(|coordinate| !(0.0..=1.0).contains(coordinate))
    {
        bail!("policy geometry coordinates must be normalized between zero and one");
    }
    Ok(())
}

fn redact_job(mut job: JobRecord) -> JobRecord {
    if let SourceSpec::Rtsp { uri } = &mut job.request.source {
        *uri = "rtsp://***".to_owned();
    }
    job
}

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
