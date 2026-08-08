use std::{
    collections::HashMap,
    sync::Arc,
    time::{Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{bail, Context};
use futures_util::{stream, StreamExt};
use tokio::sync::RwLock;
use tracing::error;
use uuid::Uuid;

use crate::{
    cluster,
    config::Config,
    domain::{
        BackendKind, CameraAnalyticsResult, CameraProcessingFailure, ClusterJobRecord,
        ClusterJobRequest, ClusterPipelineResult, JobRecord, JobRequest, JobStatus,
        MemoryCameraFailure, MemoryJobRecord, MemoryJobRequest, MemoryJobResult,
        MemoryQueryRequest, MemoryQueryResponse, SourceSpec, UploadRecord,
    },
    gemma::known_vlm_models,
    memory::{self, MemoryService},
    pipeline::{PipelineService, ResolvedSource},
};

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub pipeline: PipelineService,
    pub memory: MemoryService,
    jobs: Arc<RwLock<HashMap<Uuid, JobRecord>>>,
    cluster_jobs: Arc<RwLock<HashMap<Uuid, ClusterJobRecord>>>,
    memory_jobs: Arc<RwLock<HashMap<Uuid, MemoryJobRecord>>>,
    uploads: Arc<RwLock<HashMap<Uuid, UploadRecord>>>,
}

impl AppState {
    pub fn new(config: Arc<Config>, pipeline: PipelineService, memory: MemoryService) -> Self {
        let memory_jobs = load_memory_jobs(&config);
        Self {
            config,
            pipeline,
            memory,
            jobs: Arc::new(RwLock::new(HashMap::new())),
            cluster_jobs: Arc::new(RwLock::new(HashMap::new())),
            memory_jobs: Arc::new(RwLock::new(memory_jobs)),
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

    pub async fn cluster_jobs(&self) -> Vec<ClusterJobRecord> {
        let mut jobs: Vec<_> = self.cluster_jobs.read().await.values().cloned().collect();
        jobs.sort_by_key(|job| std::cmp::Reverse(job.created_at_ms));
        jobs.into_iter().map(redact_cluster_job).collect()
    }

    pub async fn cluster_job(&self, id: Uuid) -> Option<ClusterJobRecord> {
        self.cluster_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .map(redact_cluster_job)
    }

    pub async fn memory_jobs(&self) -> Vec<MemoryJobRecord> {
        let mut jobs: Vec<_> = self.memory_jobs.read().await.values().cloned().collect();
        jobs.sort_by_key(|job| std::cmp::Reverse(job.created_at_ms));
        jobs.into_iter().map(redact_memory_job).collect()
    }

    pub async fn memory_job(&self, id: Uuid) -> Option<MemoryJobRecord> {
        self.memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .map(redact_memory_job)
    }

    pub async fn memory_event_artifact(
        &self,
        event_id: Uuid,
        kind: &str,
    ) -> Option<std::path::PathBuf> {
        let jobs = self.memory_jobs.read().await;
        for job in jobs.values() {
            let Some(result) = &job.result else { continue };
            for camera in &result.camera_results {
                if let Some(event) = camera
                    .events
                    .iter()
                    .find(|event| event.event_id == event_id)
                {
                    return match kind {
                        "thumbnail" => Some(event.thumbnail_path.clone()),
                        "clip" => Some(event.clip_path.clone()),
                        "source" => Some(event.source_evidence_path.clone()),
                        _ => None,
                    };
                }
            }
        }
        None
    }

    pub async fn submit(&self, request: JobRequest) -> anyhow::Result<JobRecord> {
        validate_request(&request, self.config.max_analysis_secs)?;
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
            SourceSpec::Http { uri } => ResolvedSource::Http(uri.clone()),
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
                        job.error = Some(format!("{error:#}"));
                    }
                }
            }
            prune_jobs(&mut jobs, state.config.max_ephemeral_jobs);
        });

        Ok(redact_job(record))
    }

    pub async fn submit_cluster(
        &self,
        request: ClusterJobRequest,
    ) -> anyhow::Result<ClusterJobRecord> {
        validate_cluster_request(&request, &self.config)?;
        let id = Uuid::now_v7();
        let record = ClusterJobRecord {
            id,
            request: request.clone(),
            status: JobStatus::Queued,
            created_at_ms: now_ms(),
            started_at_ms: None,
            completed_at_ms: None,
            result: None,
            error: None,
        };
        self.cluster_jobs.write().await.insert(id, record.clone());

        let state = self.clone();
        tokio::spawn(async move {
            {
                if let Some(job) = state.cluster_jobs.write().await.get_mut(&id) {
                    job.status = JobStatus::Running;
                    job.started_at_ms = Some(now_ms());
                }
            }
            let result = state.process_cluster(id, &request).await;
            let mut jobs = state.cluster_jobs.write().await;
            if let Some(job) = jobs.get_mut(&id) {
                job.completed_at_ms = Some(now_ms());
                match result {
                    Ok(result) => {
                        job.status = JobStatus::Completed;
                        job.result = Some(result);
                    }
                    Err(error) => {
                        error!(%id, error = %error, "cluster pipeline job failed");
                        job.status = JobStatus::Failed;
                        job.error = Some(format!("{error:#}"));
                    }
                }
            }
            prune_cluster_jobs(&mut jobs, state.config.max_ephemeral_jobs);
        });

        Ok(redact_cluster_job(record))
    }

    pub async fn submit_memory(
        &self,
        mut request: MemoryJobRequest,
    ) -> anyhow::Result<MemoryJobRecord> {
        normalize_memory_camera_ids(&mut request);
        validate_memory_request(&request, &self.config)?;
        let id = Uuid::now_v7();
        let record = MemoryJobRecord {
            id,
            request: request.clone(),
            status: JobStatus::Queued,
            created_at_ms: now_ms(),
            started_at_ms: None,
            completed_at_ms: None,
            result: None,
            error: None,
        };
        self.memory_jobs.write().await.insert(id, record.clone());

        let state = self.clone();
        tokio::spawn(async move {
            {
                if let Some(job) = state.memory_jobs.write().await.get_mut(&id) {
                    job.status = JobStatus::Running;
                    job.started_at_ms = Some(now_ms());
                }
            }
            let result = state.process_memory(id, &request).await;
            let completed = {
                let mut jobs = state.memory_jobs.write().await;
                if let Some(job) = jobs.get_mut(&id) {
                    job.completed_at_ms = Some(now_ms());
                    match result {
                        Ok(result) => {
                            job.status = JobStatus::Completed;
                            job.result = Some(result);
                        }
                        Err(error) => {
                            error!(%id, error = %error, "memory indexing job failed");
                            job.status = JobStatus::Failed;
                            job.error = Some(format!("{error:#}"));
                        }
                    }
                    Some(job.clone())
                } else {
                    None
                }
            };
            if let Some(job) = completed {
                if let Err(error) = persist_memory_job(&state.config, &job).await {
                    error!(%id, error = %error, "could not persist memory-job manifest");
                }
            }
        });
        Ok(redact_memory_job(record))
    }

    async fn process_memory(
        &self,
        job_id: Uuid,
        request: &MemoryJobRequest,
    ) -> anyhow::Result<MemoryJobResult> {
        let service = self.memory.clone();
        let cluster_id = request.cluster_id.clone();
        let duration = request.monitor_duration_secs;
        let fps = request.observer_fps;
        let vlm_enabled = request.vlm_enabled;
        let model = request.vlm_model.clone();
        let outcomes = stream::iter(request.cameras.clone().into_iter().map(|camera| {
            let service = service.clone();
            let cluster_id = cluster_id.clone();
            let model = model.clone();
            async move {
                let camera_id = camera.camera_id.clone();
                let result = service
                    .process_camera(
                        job_id,
                        cluster_id.as_deref(),
                        camera,
                        duration,
                        fps,
                        vlm_enabled,
                        model.as_deref(),
                    )
                    .await;
                (camera_id, result)
            }
        }))
        .buffer_unordered(self.config.max_concurrent_cameras)
        .collect::<Vec<_>>()
        .await;

        let mut camera_results = Vec::new();
        let mut camera_failures = Vec::new();
        for (camera_id, outcome) in outcomes {
            match outcome {
                Ok(result) => camera_results.push(result),
                Err(error) => camera_failures.push(MemoryCameraFailure {
                    camera_id,
                    error: format!("{error:#}"),
                }),
            }
        }
        camera_results.sort_by(|left, right| left.camera.camera_id.cmp(&right.camera.camera_id));
        camera_failures.sort_by(|left, right| left.camera_id.cmp(&right.camera_id));
        if camera_results.is_empty() {
            bail!(
                "all memory cameras failed: {}",
                camera_failures
                    .iter()
                    .map(|failure| format!("{}: {}", failure.camera_id, failure.error))
                    .collect::<Vec<_>>()
                    .join("; ")
            );
        }
        Ok(MemoryJobResult {
            cluster_id: request.cluster_id.clone(),
            cameras_requested: request.cameras.len(),
            cameras_processed: camera_results.len(),
            events_indexed: camera_results
                .iter()
                .map(|camera| camera.events.len())
                .sum(),
            source_duration_ms: camera_results
                .iter()
                .map(|camera| camera.duration_ms)
                .max()
                .unwrap_or_default(),
            observer_frames_decoded: camera_results
                .iter()
                .map(|camera| camera.frames_decoded)
                .sum(),
            camera_results,
            camera_failures,
            algorithm_version: "retrieval-memory-v1-sparse-adaptive".to_owned(),
        })
    }

    pub async fn query_memory(
        &self,
        request: MemoryQueryRequest,
    ) -> anyhow::Result<MemoryQueryResponse> {
        validate_memory_query(&request)?;
        let (mut matches, considered) = {
            let jobs = self.memory_jobs.read().await;
            let mut output = Vec::new();
            for job in jobs.values() {
                let Some(result) = &job.result else { continue };
                for camera in &result.camera_results {
                    output.extend(camera.events.iter().map(|event| (event, &camera.camera)));
                }
            }
            memory::retrieve(&request, output)
        };
        let mut summary = if matches.is_empty() {
            "No indexed event matched the supplied filters. Index a camera interval or broaden the query filters.".to_owned()
        } else {
            format!(
                "Retrieved {} candidate event{} from {} indexed events. Results use local metadata and VLM-generated evidence descriptions where available.",
                matches.len(),
                if matches.len() == 1 { "" } else { "s" },
                considered
            )
        };
        let mut retrieval_mode = "local_lexical_metadata".to_owned();
        let mut model = None;
        let mut fallback_reason = None;
        if request.vlm_enabled && !matches.is_empty() {
            match self
                .memory
                .synthesize_query(&request.query, &matches, request.vlm_model.as_deref())
                .await
            {
                Ok((vlm_summary, used_model, relevant)) => {
                    summary = vlm_summary;
                    memory::reorder_by_vlm(&mut matches, &relevant);
                    retrieval_mode = "local_recall_plus_vlm_verification".to_owned();
                    model = Some(used_model);
                }
                Err(error) => fallback_reason = Some(error.to_string()),
            }
        }
        Ok(MemoryQueryResponse {
            query_id: Uuid::now_v7(),
            query: request.query,
            summary,
            matches,
            events_considered: considered,
            retrieval_mode,
            model,
            fallback_reason,
        })
    }

    async fn process_cluster(
        &self,
        job_id: Uuid,
        request: &ClusterJobRequest,
    ) -> anyhow::Result<ClusterPipelineResult> {
        let concurrency = self.config.max_concurrent_cameras;
        let pipeline = self.pipeline.clone();
        let cluster_name = request.name.clone();
        let detector_fps = request.detector_fps;
        let monitor_duration_secs = request.monitor_duration_secs;
        let vlm_model = request.vlm_model.clone();
        let cluster_started = Instant::now();
        let outcomes = stream::iter(request.cameras.clone().into_iter().map(|camera| {
            let pipeline = pipeline.clone();
            let cluster_name = cluster_name.clone();
            let vlm_model = vlm_model.clone();
            async move {
                let processing_start_offset_ms = cluster_started.elapsed().as_millis() as u64;
                let camera_job_id = Uuid::new_v5(&job_id, camera.camera_id.as_bytes());
                let label = if camera.label.trim().is_empty() {
                    camera.camera_id.clone()
                } else {
                    camera.label.clone()
                };
                let camera_request = JobRequest {
                    name: format!("{cluster_name} / {label}"),
                    source: SourceSpec::Http {
                        uri: camera.uri.clone(),
                    },
                    backend: BackendKind::Yolo26Command,
                    detector_fps,
                    monitor_duration_secs,
                    gemma_enabled: false,
                    vlm_model,
                    observations: Vec::new(),
                    policy: camera.policy.clone(),
                };
                let result = pipeline
                    .process_cluster_camera(
                        camera_job_id,
                        &camera_request,
                        ResolvedSource::Http(camera.uri.clone()),
                        request.gemma_enabled,
                    )
                    .await;
                (camera, label, processing_start_offset_ms, result)
            }
        }))
        .buffer_unordered(concurrency)
        .collect::<Vec<_>>()
        .await;

        let mut cameras = Vec::new();
        let mut failures = Vec::new();
        for (camera, label, processing_start_offset_ms, result) in outcomes {
            match result {
                Ok(pipeline) => cameras.push(CameraAnalyticsResult {
                    camera_id: camera.camera_id,
                    label,
                    overlap_group: camera.overlap_group,
                    clock_offset_ms: camera.clock_offset_ms,
                    processing_start_offset_ms,
                    pipeline,
                }),
                Err(error) => failures.push(CameraProcessingFailure {
                    camera_id: camera.camera_id,
                    label,
                    error: format!("{error:#}"),
                }),
            }
        }
        cameras.sort_by(|left, right| left.camera_id.cmp(&right.camera_id));
        failures.sort_by(|left, right| left.camera_id.cmp(&right.camera_id));

        let association = cluster::associate(job_id, request, &cameras);
        let view_description = cluster::aggregate_view_descriptions(&cameras);
        let deterministic_report = cluster::cluster_report(
            request,
            &cameras,
            &failures,
            &association.decisions,
            &association.global_tracks,
        );
        let (report, gemma) = self
            .pipeline
            .enrich_report(
                job_id,
                &deterministic_report,
                request.gemma_enabled,
                request.vlm_model.as_deref(),
            )
            .await;
        self.pipeline.publish_report(job_id, &report).await?;

        Ok(ClusterPipelineResult {
            cluster_id: request.cluster_id.clone(),
            cameras_requested: request.cameras.len(),
            cameras_processed: cameras.len(),
            camera_failures: failures,
            observations_processed: cameras
                .iter()
                .map(|camera| camera.pipeline.observations_processed)
                .sum(),
            local_tracks: cameras
                .iter()
                .map(|camera| camera.pipeline.tracks.len())
                .sum(),
            events: cameras
                .iter()
                .map(|camera| camera.pipeline.events.len())
                .sum(),
            duration_ms: cameras
                .iter()
                .map(|camera| camera.pipeline.duration_ms)
                .max()
                .unwrap_or(0),
            camera_results: cameras,
            associations: association.decisions,
            global_tracks: association.global_tracks,
            view_description,
            deterministic_report,
            report,
            gemma,
            algorithm_version: "mcmct-v1-explicit-topology-hungarian".to_owned(),
        })
    }
}

fn validate_request(request: &JobRequest, max_analysis_secs: u64) -> anyhow::Result<()> {
    if request.name.trim().is_empty() || request.name.len() > 120 {
        bail!("name must contain 1 to 120 characters");
    }
    if !(0.1..=60.0).contains(&request.detector_fps) {
        bail!("detector_fps must be between 0.1 and 60");
    }
    if !(1..=max_analysis_secs).contains(&request.monitor_duration_secs) {
        bail!("monitor_duration_secs must be between 1 and {max_analysis_secs}");
    }
    validate_vlm_model(request.vlm_model.as_deref())?;
    if let SourceSpec::Rtsp { uri } = &request.source {
        if !uri.starts_with("rtsp://") && !uri.starts_with("rtsps://") {
            bail!("RTSP source must start with rtsp:// or rtsps://");
        }
        if uri.chars().any(char::is_whitespace) {
            bail!("RTSP source must not contain whitespace");
        }
    }
    if let SourceSpec::Http { uri } = &request.source {
        if !uri.starts_with("http://") && !uri.starts_with("https://") {
            bail!("HTTP source must start with http:// or https://");
        }
        if uri.chars().any(char::is_whitespace) {
            bail!("HTTP source must not contain whitespace");
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

fn validate_vlm_model(model: Option<&str>) -> anyhow::Result<()> {
    let Some(model) = model.map(str::trim).filter(|model| !model.is_empty()) else {
        return Ok(());
    };
    if !known_vlm_models().contains(&model) {
        bail!(
            "vlm_model must be one of: {}",
            known_vlm_models().join(", ")
        );
    }
    Ok(())
}

fn validate_cluster_request(request: &ClusterJobRequest, config: &Config) -> anyhow::Result<()> {
    if request.name.trim().is_empty() || request.name.len() > 120 {
        bail!("name must contain 1 to 120 characters");
    }
    if request.cluster_id.trim().is_empty() || request.cluster_id.len() > 120 {
        bail!("cluster_id must contain 1 to 120 characters");
    }
    if request.cameras.len() < 2 || request.cameras.len() > config.max_cluster_cameras {
        bail!(
            "a cluster job requires 2 to {} cameras",
            config.max_cluster_cameras
        );
    }
    if !(0.1..=60.0).contains(&request.detector_fps) {
        bail!("detector_fps must be between 0.1 and 60");
    }
    if !(1..=config.max_analysis_secs).contains(&request.monitor_duration_secs) {
        bail!(
            "monitor_duration_secs must be between 1 and {}",
            config.max_analysis_secs
        );
    }
    validate_vlm_model(request.vlm_model.as_deref())?;
    let mut camera_ids = std::collections::HashSet::new();
    for camera in &request.cameras {
        if camera.camera_id.trim().is_empty() || camera.camera_id.len() > 80 {
            bail!("each camera_id must contain 1 to 80 characters");
        }
        if !camera_ids.insert(camera.camera_id.as_str()) {
            bail!("camera_id {} is duplicated", camera.camera_id);
        }
        if !camera.uri.starts_with("http://") && !camera.uri.starts_with("https://") {
            bail!(
                "camera {} must use an http:// or https:// URI",
                camera.camera_id
            );
        }
        if camera.uri.chars().any(char::is_whitespace) {
            bail!(
                "camera {} URI must not contain whitespace",
                camera.camera_id
            );
        }
        if camera.clock_offset_ms.unsigned_abs() > 3_600_000 {
            bail!(
                "camera {} clock_offset_ms exceeds one hour",
                camera.camera_id
            );
        }
        validate_policy(&camera.policy)?;
    }
    let mut edge_ids = std::collections::HashSet::new();
    for edge in &request.topology {
        if edge.edge_id.trim().is_empty() || !edge_ids.insert(edge.edge_id.as_str()) {
            bail!("topology edge IDs must be non-empty and unique");
        }
        if edge.source_camera_id == edge.target_camera_id {
            bail!(
                "topology edge {} cannot connect a camera to itself",
                edge.edge_id
            );
        }
        if !camera_ids.contains(edge.source_camera_id.as_str())
            || !camera_ids.contains(edge.target_camera_id.as_str())
        {
            bail!(
                "topology edge {} references an unknown camera",
                edge.edge_id
            );
        }
        if edge.maximum_travel_ms < edge.minimum_travel_ms {
            bail!(
                "topology edge {} has an invalid travel window",
                edge.edge_id
            );
        }
        if !(0.0..=1.0).contains(&edge.confidence) {
            bail!(
                "topology edge {} confidence must be between 0 and 1",
                edge.edge_id
            );
        }
    }
    let association = &request.association;
    if !(0.0..=1.0).contains(&association.minimum_appearance_similarity)
        || !(0.55..=1.0).contains(&association.provisional_threshold)
        || !(association.provisional_threshold..=1.0).contains(&association.final_threshold)
        || association.overlap_tolerance_ms == 0
    {
        bail!("association thresholds or overlap_tolerance_ms are invalid");
    }
    Ok(())
}

fn validate_policy(policy: &crate::domain::AnalyticsPolicy) -> anyhow::Result<()> {
    for zone in &policy.zones {
        if zone.id.trim().is_empty() || zone.points.len() < 3 {
            bail!("each zone needs an id and at least three points");
        }
        validate_points(&zone.points)?;
    }
    for line in &policy.lines {
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

fn normalize_memory_camera_ids(request: &mut MemoryJobRequest) {
    for (index, camera) in request.cameras.iter_mut().enumerate() {
        if camera.camera_id.trim().is_empty() {
            let fingerprint = Uuid::new_v5(&Uuid::NAMESPACE_URL, camera.live_url.as_bytes());
            camera.camera_id = format!(
                "camera-{}-{}",
                index + 1,
                &fingerprint.simple().to_string()[..8]
            );
        }
    }
}

fn validate_memory_request(request: &MemoryJobRequest, config: &Config) -> anyhow::Result<()> {
    if request.name.trim().is_empty() || request.name.len() > 120 {
        bail!("name must contain 1 to 120 characters");
    }
    if request.cameras.is_empty() || request.cameras.len() > config.max_cluster_cameras {
        bail!(
            "a memory job requires 1 to {} cameras",
            config.max_cluster_cameras
        );
    }
    if !(1..=config.max_analysis_secs).contains(&request.monitor_duration_secs) {
        bail!(
            "monitor_duration_secs must be between 1 and {}",
            config.max_analysis_secs
        );
    }
    if !(0.1..=5.0).contains(&request.observer_fps) {
        bail!("observer_fps must be between 0.1 and 5.0");
    }
    validate_vlm_model(request.vlm_model.as_deref())?;
    if request
        .cluster_id
        .as_ref()
        .is_some_and(|value| value.trim().is_empty() || value.len() > 120)
    {
        bail!("cluster_id must be absent or contain 1 to 120 characters");
    }
    let mut ids = std::collections::HashSet::new();
    let mut urls = std::collections::HashSet::new();
    for camera in &request.cameras {
        if camera.camera_id.trim().is_empty()
            || camera.camera_id.len() > 80
            || !ids.insert(camera.camera_id.as_str())
        {
            bail!("camera IDs must be non-empty, unique, and at most 80 characters");
        }
        if !camera.live_url.starts_with("http://")
            && !camera.live_url.starts_with("https://")
            && !camera.live_url.starts_with("rtsp://")
            && !camera.live_url.starts_with("rtsps://")
        {
            bail!("camera {} has an unsupported liveurl", camera.camera_id);
        }
        if camera.live_url.chars().any(char::is_whitespace) {
            bail!(
                "camera {} liveurl must not contain whitespace",
                camera.camera_id
            );
        }
        if !urls.insert(camera.live_url.as_str()) {
            bail!(
                "camera URL {} is duplicated; submit each physical stream once",
                camera.camera_id
            );
        }
        if camera.country.len() > 100
            || camera.country_code.len() > 10
            || camera.region.len() > 160
            || camera.city.len() > 160
            || camera.timezone.len() > 40
            || camera.manufacturer.len() > 120
            || camera.description.len() > 1_000
        {
            bail!("camera {} metadata exceeds field limits", camera.camera_id);
        }
        if camera
            .latitude
            .is_some_and(|value| !(-90.0..=90.0).contains(&value))
            || camera
                .longitude
                .is_some_and(|value| !(-180.0..=180.0).contains(&value))
        {
            bail!(
                "camera {} latitude or longitude is invalid",
                camera.camera_id
            );
        }
    }
    Ok(())
}

fn validate_memory_query(request: &MemoryQueryRequest) -> anyhow::Result<()> {
    if request.query.trim().is_empty() || request.query.len() > 1_000 {
        bail!("query must contain 1 to 1000 characters");
    }
    if !(1..=50).contains(&request.limit) {
        bail!("query limit must be between 1 and 50");
    }
    if request
        .end_ms
        .zip(request.start_ms)
        .is_some_and(|(end, start)| end < start)
    {
        bail!("query end_ms must not be earlier than start_ms");
    }
    validate_vlm_model(request.vlm_model.as_deref())
}

fn redact_job(mut job: JobRecord) -> JobRecord {
    if let SourceSpec::Rtsp { uri } = &mut job.request.source {
        *uri = "rtsp://***".to_owned();
    }
    if let SourceSpec::Http { uri } = &mut job.request.source {
        *uri = if uri.starts_with("https://") {
            "https://***".to_owned()
        } else {
            "http://***".to_owned()
        };
    }
    job
}

fn prune_jobs(jobs: &mut HashMap<Uuid, JobRecord>, limit: usize) {
    if jobs.len() <= limit {
        return;
    }
    let mut removable: Vec<_> = jobs
        .values()
        .filter(|job| matches!(&job.status, JobStatus::Completed | JobStatus::Failed))
        .map(|job| (job.created_at_ms, job.id))
        .collect();
    removable.sort_unstable();
    let remove_count = jobs.len().saturating_sub(limit).min(removable.len());
    for (_, id) in removable.into_iter().take(remove_count) {
        jobs.remove(&id);
    }
}

fn prune_cluster_jobs(jobs: &mut HashMap<Uuid, ClusterJobRecord>, limit: usize) {
    if jobs.len() <= limit {
        return;
    }
    let mut removable: Vec<_> = jobs
        .values()
        .filter(|job| matches!(&job.status, JobStatus::Completed | JobStatus::Failed))
        .map(|job| (job.created_at_ms, job.id))
        .collect();
    removable.sort_unstable();
    let remove_count = jobs.len().saturating_sub(limit).min(removable.len());
    for (_, id) in removable.into_iter().take(remove_count) {
        jobs.remove(&id);
    }
}

fn redact_cluster_job(mut job: ClusterJobRecord) -> ClusterJobRecord {
    for camera in &mut job.request.cameras {
        camera.uri = if camera.uri.starts_with("https://") {
            "https://***".to_owned()
        } else {
            "http://***".to_owned()
        };
    }
    job
}

fn redact_memory_job(mut job: MemoryJobRecord) -> MemoryJobRecord {
    for camera in &mut job.request.cameras {
        camera.live_url = redacted_uri(&camera.live_url);
    }
    if let Some(result) = &mut job.result {
        for camera in &mut result.camera_results {
            camera.camera.live_url = redacted_uri(&camera.camera.live_url);
        }
    }
    job
}

fn redacted_uri(uri: &str) -> String {
    uri.split_once(':')
        .map(|(scheme, _)| format!("{scheme}://***"))
        .unwrap_or_else(|| "***".to_owned())
}

async fn persist_memory_job(config: &Config, job: &MemoryJobRecord) -> anyhow::Result<()> {
    let directory = config.data_dir.join("memory").join("manifests");
    tokio::fs::create_dir_all(&directory)
        .await
        .context("create memory manifest directory")?;
    let path = directory.join(format!("{}.json", job.id));
    let temporary = directory.join(format!("{}.json.pending", job.id));
    let body = serde_json::to_vec_pretty(&redact_memory_job(job.clone()))?;
    tokio::fs::write(&temporary, body)
        .await
        .context("write memory manifest")?;
    tokio::fs::rename(&temporary, &path)
        .await
        .context("finalize memory manifest")?;
    Ok(())
}

fn load_memory_jobs(config: &Config) -> HashMap<Uuid, MemoryJobRecord> {
    let directory = config.data_dir.join("memory").join("manifests");
    let mut jobs = HashMap::new();
    let Ok(entries) = std::fs::read_dir(&directory) else {
        return jobs;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) != Some("json") {
            continue;
        }
        let parsed = std::fs::read(&path)
            .ok()
            .and_then(|body| serde_json::from_slice::<MemoryJobRecord>(&body).ok());
        if let Some(mut job) = parsed {
            rehydrate_memory_paths(config, &mut job);
            jobs.insert(job.id, job);
        } else {
            tracing::warn!(path = %path.display(), "ignoring invalid memory-job manifest");
        }
    }
    jobs
}

fn rehydrate_memory_paths(config: &Config, job: &mut MemoryJobRecord) {
    let Some(result) = &mut job.result else {
        return;
    };
    for camera in &mut result.camera_results {
        let directory = config
            .data_dir
            .join("memory")
            .join(job.id.to_string())
            .join(safe_memory_component(&camera.camera.camera_id));
        for event in &mut camera.events {
            event.thumbnail_path = directory.join(format!("{}.jpg", event.event_id));
            let clip = directory.join(format!("{}.mp4", event.event_id));
            event.source_evidence_path = directory.join("source.mkv");
            event.clip_path = if clip.is_file() {
                clip
            } else {
                event.source_evidence_path.clone()
            };
        }
    }
}

fn safe_memory_component(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .take(80)
        .collect()
}

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
