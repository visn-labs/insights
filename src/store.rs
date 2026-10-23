use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
    time::{Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{bail, Context};
use futures_util::{stream, StreamExt};
use sha2::{Digest, Sha256};
use tokio::sync::{Mutex, RwLock};
use tracing::{error, info};
use uuid::Uuid;

use crate::{
    cluster,
    config::Config,
    domain::{
        BackendKind, CameraAnalyticsResult, CameraProcessingFailure, ClusterJobRecord,
        ClusterJobRequest, ClusterPipelineResult, JobRecord, JobRequest, JobStatus,
        MemoryCameraFailure, MemoryJobRecord, MemoryJobRequest, MemoryJobResult,
        MemoryQueryRequest, MemoryQueryResponse, SourceSpec, UploadRecord, VisualProjection,
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
    pub telemetry: crate::telemetry::TelemetryCoordinator,
    jobs: Arc<RwLock<HashMap<Uuid, JobRecord>>>,
    cluster_jobs: Arc<RwLock<HashMap<Uuid, ClusterJobRecord>>>,
    memory_jobs: Arc<RwLock<HashMap<Uuid, MemoryJobRecord>>>,
    uploads: Arc<RwLock<HashMap<Uuid, UploadRecord>>>,
    long_memory_gate: Arc<Mutex<()>>,
    memory_job_gate: Arc<Mutex<()>>,
    memory_tasks: Arc<Mutex<HashMap<Uuid, tokio::task::AbortHandle>>>,
    projection_gate: Arc<Mutex<()>>,
}

struct MemoryQuerySnapshot {
    indexed_count: usize,
    candidates: Vec<(crate::domain::MemoryEvent, crate::domain::CameraProfile)>,
    cameras: Vec<crate::domain::MemoryCameraResult>,
}

fn memory_query_snapshot<'a>(
    request: &MemoryQueryRequest,
    cameras: impl Iterator<Item = &'a crate::domain::MemoryCameraResult>,
) -> MemoryQuerySnapshot {
    let mut snapshot = MemoryQuerySnapshot {
        indexed_count: 0,
        candidates: Vec::new(),
        cameras: Vec::new(),
    };
    let needs_graph =
        request.long_memory_enabled || (request.temporal_enabled && request.entity_id.is_some());
    for camera in cameras {
        let entity_scope = !request.temporal_enabled
            || request.entity_id.is_none_or(|id| {
                camera.long_memory.as_ref().is_some_and(|graph| {
                    graph.version == crate::long_memory::VERSION
                        && graph.entities.iter().any(|entity| {
                            entity.entity_id == id
                                && entity.camera_id == camera.camera.camera_id
                                && entity.source_session_id == camera.source_session_id
                                && camera
                                    .events
                                    .iter()
                                    .any(|event| event.job_id == entity.job_id)
                        })
                })
            });
        if entity_scope {
            snapshot.indexed_count += camera.events.len();
        }
        let events: Vec<_> = camera
            .events
            .iter()
            .filter(|e| crate::retrieval::eligible(request, e))
            .collect();
        if entity_scope {
            snapshot
                .candidates
                .extend(events.iter().map(|e| ((*e).clone(), camera.camera.clone())));
        }
        if needs_graph && !events.is_empty() {
            snapshot.cameras.push(crate::domain::MemoryCameraResult {
                capture_extent: camera.capture_extent,
                storage_session_id: camera.storage_session_id,
                source_session_id: camera.source_session_id.clone(),
                long_memory: camera.long_memory.clone(),
                camera: camera.camera.clone(),
                duration_ms: camera.duration_ms,
                frames_decoded: camera.frames_decoded,
                evidence_url: camera.evidence_url.clone(),
                events: events.into_iter().cloned().collect(),
                data_quality_notes: camera.data_quality_notes.clone(),
            });
        }
    }
    snapshot
}

fn memory_policy<'a>(job: &MemoryJobRecord, active: &'a str) -> &'a str {
    if job.execution_profile.is_some()
        || job.result.as_ref().is_some_and(|result| {
            result
                .camera_results
                .iter()
                .any(|camera| camera.events.iter().any(|event| event.privacy.is_some()))
        })
    {
        "sanitized-v1"
    } else {
        active
    }
}

impl AppState {
    pub fn new(
        config: Arc<Config>,
        pipeline: PipelineService,
        memory: MemoryService,
    ) -> anyhow::Result<Self> {
        let memory_jobs = load_memory_jobs(&config)?;
        Ok(Self {
            config,
            pipeline,
            memory,
            telemetry: crate::telemetry::TelemetryCoordinator::new(),
            jobs: Arc::new(RwLock::new(HashMap::new())),
            cluster_jobs: Arc::new(RwLock::new(HashMap::new())),
            memory_jobs: Arc::new(RwLock::new(memory_jobs)),
            uploads: Arc::new(RwLock::new(HashMap::new())),
            long_memory_gate: Arc::new(Mutex::new(())),
            memory_job_gate: Arc::new(Mutex::new(())),
            memory_tasks: Arc::new(Mutex::new(HashMap::new())),
            projection_gate: Arc::new(Mutex::new(())),
        })
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

    pub async fn memory_job_exists(&self, id: Uuid) -> bool {
        self.memory_jobs
            .read()
            .await
            .get(&id)
            .is_some_and(|job| job.revocation.is_none())
    }

    pub async fn visual_index_events(&self, id: Uuid) -> anyhow::Result<Vec<serde_json::Value>> {
        let job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("Recording not found")?;
        anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
        if job.status != JobStatus::Completed {
            bail!("Complete the recording before preparing visual search");
        }
        let result = job.result.as_ref().context("Recording has no evidence")?;
        if memory_policy(&job, &self.config.media_policy) == "sanitized-v1" {
            // One shared admission for indexing and tracker enrichment.
            for camera in &result.camera_results {
                crate::privacy_media::verify_camera(camera).await?;
            }
        }
        let events = crate::retrieval::event_payload(
            result
                .camera_results
                .iter()
                .flat_map(|camera| camera.events.iter()),
        )?;
        if events.is_empty() {
            bail!("Recording has no intervals to prepare");
        }
        Ok(events)
    }

    fn metadata_directory(&self, id: Uuid) -> std::path::PathBuf {
        self.config
            .data_dir
            .join("memory/projections")
            .join(id.to_string())
    }

    async fn load_metadata(
        &self,
        job: &MemoryJobRecord,
    ) -> anyhow::Result<crate::metadata_projection::Document> {
        use tokio::io::AsyncReadExt;
        anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
        let intent = job
            .metadata_projection
            .as_ref()
            .context("metadata_projection_not_ready")?;
        anyhow::ensure!(intent.state == "completed", "metadata_projection_not_ready");
        let file = tokio::fs::File::open(
            self.metadata_directory(job.id)
                .join(format!("{}.json", intent.revision)),
        )
        .await?;
        let limit = 128 * 1024 * 1024_u64;
        let mut bytes = Vec::new();
        file.take(limit + 1).read_to_end(&mut bytes).await?;
        anyhow::ensure!(
            bytes.len() as u64 <= limit,
            "metadata_projection_capacity_exceeded"
        );
        crate::metadata_projection::verify(
            job,
            memory_policy(job, &self.config.media_policy),
            &bytes,
        )
    }

    pub async fn rebuild_metadata(&self, id: Uuid) -> anyhow::Result<MemoryJobRecord> {
        self.visual_index_events(id).await?;
        let _gate = self.memory_job_gate.lock().await;
        let mut job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("recording_not_found")?;
        anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
        anyhow::ensure!(
            job.status == JobStatus::Completed,
            "recording_not_completed"
        );
        job.metadata_projection = Some(crate::metadata_projection::queued(
            &job,
            memory_policy(&job, &self.config.media_policy),
        ));
        persist_memory_job(&self.config, &job).await?;
        self.memory_jobs.write().await.insert(id, job.clone());
        Ok(redact_memory_job(job))
    }

    async fn replay_local_metadata(&self) -> anyhow::Result<()> {
        let _dispatch = self.projection_gate.lock().await;
        let (cleanup, mut pending) = {
            let jobs = self.memory_jobs.read().await;
            (
                jobs.values()
                    .filter(|job| {
                        job.revocation
                            .as_ref()
                            .is_some_and(|r| r.local_cleanup_state == "pending")
                    })
                    .map(|job| job.id)
                    .take(2)
                    .collect::<Vec<_>>(),
                jobs.values()
                    .filter(|job| job.revocation.is_none() && job.status == JobStatus::Completed)
                    .filter_map(|job| {
                        job.metadata_projection
                            .as_ref()
                            .filter(|p| p.state == "pending" && p.next_attempt_at_ms <= now_ms())
                            .map(|p| (p.next_attempt_at_ms, job.id))
                    })
                    .collect::<Vec<_>>(),
            )
        };
        for id in cleanup {
            let directory = self.metadata_directory(id);
            let result = tokio::task::spawn_blocking(move || -> anyhow::Result<()> {
                match std::fs::symlink_metadata(&directory) {
                    Ok(meta) => {
                        anyhow::ensure!(
                            !meta.file_type().is_symlink(),
                            "metadata_cleanup_symlink_rejected"
                        );
                        std::fs::remove_dir_all(&directory)?;
                        std::fs::File::open(directory.parent().unwrap())?.sync_all()?;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                    Err(error) => return Err(error.into()),
                }
                Ok(())
            })
            .await?;
            let _gate = self.memory_job_gate.lock().await;
            let mut job = self
                .memory_jobs
                .read()
                .await
                .get(&id)
                .cloned()
                .context("recording_not_found")?;
            if let Some(tombstone) = &mut job.revocation {
                match result {
                    Ok(()) => {
                        tombstone.local_cleanup_state = "completed".into();
                        tombstone.local_cleanup_error = None;
                    }
                    Err(error) => {
                        tombstone.local_cleanup_state = "blocked".into();
                        tombstone.local_cleanup_error = Some(error.to_string());
                    }
                }
                persist_memory_job(&self.config, &job).await?;
                self.memory_jobs.write().await.insert(id, job);
            }
        }
        pending.sort_unstable();
        for (_, id) in pending.into_iter().take(2) {
            let Some(job) = self.memory_jobs.read().await.get(&id).cloned() else {
                continue;
            };
            let mut intent = job
                .metadata_projection
                .clone()
                .context("metadata_projection_not_ready")?;
            let validated = async {
                if memory_policy(&job, &self.config.media_policy) == "sanitized-v1" {
                    self.visual_index_events(id).await?;
                }
                let document = crate::metadata_projection::document(
                    &job,
                    memory_policy(&job, &self.config.media_policy),
                )?;
                let bytes = serde_json::to_vec_pretty(&document)?;
                anyhow::ensure!(
                    bytes.len() <= 128 * 1024 * 1024,
                    "metadata_projection_capacity_exceeded"
                );
                Ok::<_, anyhow::Error>(bytes)
            }
            .await;
            let path = self
                .metadata_directory(id)
                .join(format!("{}.json", intent.revision));
            intent.attempts = intent.attempts.saturating_add(1);
            match validated {
                Err(error) => {
                    intent.state = "blocked".into();
                    intent.last_error = Some(error.to_string());
                }
                Ok(bytes) => {
                    let hash = crate::metadata_projection::digest(&bytes);
                    match crate::durable::publish(path.clone(), bytes).await {
                        Ok(()) => {
                            intent.state = "completed".into();
                            intent.output_sha256 = Some(hash);
                            intent.last_error = None;
                        }
                        Err(error) => {
                            intent.last_error = Some(error.to_string());
                            intent.next_attempt_at_ms =
                                now_ms().saturating_add(1000 * 2_u64.pow(intent.attempts.min(6)));
                        }
                    }
                }
            }
            let _gate = self.memory_job_gate.lock().await;
            let mut current = self
                .memory_jobs
                .read()
                .await
                .get(&id)
                .cloned()
                .context("recording_not_found")?;
            if current.revocation.is_none()
                && current.status == JobStatus::Completed
                && current
                    .metadata_projection
                    .as_ref()
                    .is_some_and(|p| p.revision == intent.revision)
            {
                current.metadata_projection = Some(intent);
                persist_memory_job(&self.config, &current).await?;
                self.memory_jobs.write().await.insert(id, current);
            } else {
                let _ = tokio::fs::remove_file(path).await;
            }
        }
        Ok(())
    }

    pub async fn evidence_statistics(
        &self,
        id: Uuid,
        query: crate::metadata_projection::AnalyticsQuery,
    ) -> anyhow::Result<serde_json::Value> {
        let job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("recording_not_found")?;
        anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
        if memory_policy(&job, &self.config.media_policy) == "sanitized-v1" {
            self.visual_index_events(id).await?;
        }
        let document = self.load_metadata(&job).await?;
        let cameras = crate::metadata_projection::statistics(&document, &query)?;
        let indexed_camera_ms = cameras.iter().try_fold(0_u64, |sum, c| {
            sum.checked_add(c.indexed_interval_ms)
                .context("metadata_interval_overflow")
        })?;
        let current = self.memory_jobs.read().await;
        let active = current.get(&id).context("recording_not_found")?;
        anyhow::ensure!(active.revocation.is_none(), "recording_revoked");
        anyhow::ensure!(
            active
                .metadata_projection
                .as_ref()
                .is_some_and(|p| p.revision == document.revision),
            "metadata_projection_changed_retry"
        );
        Ok(
            serde_json::json!({"job_id":id,"projection_version":document.version,"revision":document.revision,
            "input_fingerprint":document.input_fingerprint,"output_sha256":job.metadata_projection.as_ref().unwrap().output_sha256,
            "cameras_requested":job.result.as_ref().unwrap().cameras_requested,
            "failed_cameras":job.result.as_ref().unwrap().camera_failures.iter().map(|c| &c.camera_id).collect::<Vec<_>>(),
            "query":query,"cameras":cameras,"indexed_camera_ms":indexed_camera_ms,
            "semantics":"Half-open source-session-relative intervals; union within each physical camera/source session, then sum camera-session-ms. Independent session clocks are not combined into one elapsed timeline. Tracker entities are not physical identities or footfall. Missing/stale tracker memory is null, not zero. Indexed intervals do not certify gap-free capture."}),
        )
    }

    async fn apply_projected_graphs(
        &self,
        cameras: &mut [crate::domain::MemoryCameraResult],
    ) -> anyhow::Result<Vec<serde_json::Value>> {
        let ids: HashSet<_> = cameras
            .iter()
            .flat_map(|camera| camera.events.iter().map(|event| event.job_id))
            .collect();
        let mut provenance = Vec::new();
        for id in ids {
            let Some(job) = self.memory_jobs.read().await.get(&id).cloned() else {
                continue;
            };
            anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
            crate::metadata_projection::intent(
                &job,
                memory_policy(&job, &self.config.media_policy),
            )?;
            match self.load_metadata(&job).await {
                Ok(document) => {
                    let same_snapshot=cameras.iter().filter(|c| c.events.first().is_some_and(|e| e.job_id == id))
                        .all(|camera| document.cameras.get(&crate::domain::memory_projection_key(&camera.camera.camera_id, camera.source_session_id.as_deref())).is_some_and(|projected|
                            serde_json::to_vec(&camera.long_memory).ok() == serde_json::to_vec(&projected.graph).ok()));
                    if !same_snapshot {
                        provenance.push(serde_json::json!({"job_id":id,"mode":"canonical_graph_fallback","reason":"snapshot_graph_revision_changed"}));
                        continue;
                    }
                    let mut used=0;
                    for camera in cameras.iter_mut().filter(|c| c.events.first().is_some_and(|e| e.job_id == id)) {
                        if let Some(projected)=document.cameras.get(&crate::domain::memory_projection_key(&camera.camera.camera_id, camera.source_session_id.as_deref())) {
                            // Do not blend a newer graph revision into an older query snapshot.
                            if serde_json::to_vec(&camera.long_memory).ok() == serde_json::to_vec(&projected.graph).ok() {
                                camera.long_memory=projected.graph.clone();used+=1;
                            }
                        }
                    }
                    provenance.push(serde_json::json!({"job_id":id,"mode":"verified_projection","revision":document.revision,
                        "version":document.version,"graph_cameras_used":used,"output_sha256":job.metadata_projection.and_then(|p|p.output_sha256)}));
                }
                Err(error) => provenance.push(serde_json::json!({"job_id":id,"mode":"canonical_graph_fallback","reason":error.to_string()})),
            }
        }
        provenance.sort_by_key(|row| row["job_id"].as_str().unwrap_or("").to_owned());
        Ok(provenance)
    }

    /// Serialize explicit controls with replay through the remote acknowledgement.
    pub async fn visual_projection_control(&self) -> tokio::sync::MutexGuard<'_, ()> {
        self.projection_gate.lock().await
    }

    async fn cancel_projection_task(
        &self,
        id: Uuid,
        intent: &VisualProjection,
        selected: &Config,
    ) -> anyhow::Result<()> {
        let url = selected
            .retrieval_url
            .as_ref()
            .context("projection_worker_missing")?;
        let target = format!("{:x}", Sha256::digest(url.as_bytes()));
        anyhow::ensure!(
            intent
                .worker_fingerprint
                .as_ref()
                .is_none_or(|old| old == &target),
            "projection_worker_changed"
        );
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()?;
        let task = if let Some(task) = intent.task_id {
            task
        } else {
            // Submission is idempotent for the exact scope, producer and evidence.
            // Recover a lost acknowledgement before retiring the intent.
            let events = self.visual_index_events(id).await?;
            let response = selected
                .pin_retrieval(
                    client
                        .post(format!("{}/index-jobs", url.trim_end_matches('/')))
                        .json(&serde_json::json!({"scope_id":id,"events":events})),
                )
                .send()
                .await?;
            anyhow::ensure!(
                response.status().is_success(),
                "projection_worker_http_{}",
                response.status().as_u16()
            );
            let body = worker_receipt(response).await?;
            selected.verify_retrieval_profile(&body["worker_profile"])?;
            anyhow::ensure!(
                body["scope_id"].as_str() == Some(id.to_string().as_str()),
                "projection_receipt_scope_mismatch"
            );
            body["id"]
                .as_str()
                .context("projection_missing_task_id")?
                .parse::<Uuid>()?
        };
        let response = selected
            .pin_retrieval(client.post(format!(
                "{}/index-jobs/{task}/cancel",
                url.trim_end_matches('/')
            )))
            .send()
            .await?;
        if response.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(());
        }
        anyhow::ensure!(
            response.status().is_success(),
            "projection_worker_http_{}",
            response.status().as_u16()
        );
        let body = worker_receipt(response).await?;
        selected.verify_retrieval_profile(&body["worker_profile"])?;
        anyhow::ensure!(
            body["id"].as_str() == Some(task.to_string().as_str())
                && body["scope_id"].as_str() == Some(id.to_string().as_str())
                && matches!(
                    body["status"].as_str(),
                    Some("cancelled" | "completed" | "failed")
                ),
            "projection_cancel_receipt_invalid"
        );
        Ok(())
    }

    pub async fn queue_visual_projection(
        &self,
        id: Uuid,
        retry_task: Option<Uuid>,
        selected: &Config,
    ) -> anyhow::Result<()> {
        // Verify artifacts outside the job-store lock before committing intent.
        self.visual_index_events(id).await?;
        let previous = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .and_then(|job| job.visual_projection.clone());
        anyhow::ensure!(!previous.as_ref().is_some_and(|p| legacy_projection_needs_binding(p, selected)),
            "projection_legacy_binding_required: reconcile the saved task offline before preparation");
        if let Some(previous) = previous.filter(|p| {
            matches!(p.state.as_str(), "pending" | "submitted")
                && (p.deployment_generation != selected.retrieval_deployment_generation
                    || retry_task.is_some_and(|task| p.task_id != Some(task)))
        }) {
            let old =
                crate::retrieval_deployment::select(&self.config, previous.deployment_generation)
                    .await?;
            self.cancel_projection_task(id, &previous, &old)
                .await
                .map_err(|error| {
                    anyhow::anyhow!(
                        "projection_worker_unavailable: cancellation not acknowledged: {error}"
                    )
                })?;
        }
        let _gate = self.memory_job_gate.lock().await;
        let mut job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("Recording not found")?;
        anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
        anyhow::ensure!(
            job.status == JobStatus::Completed,
            "recording_not_completed"
        );
        if retry_task.is_some()
            || job.visual_projection.as_ref().is_some_and(|p| {
                p.deployment_generation != selected.retrieval_deployment_generation
            })
            || job.visual_projection.as_ref().is_none_or(|p| {
                matches!(
                    p.state.as_str(),
                    "failed" | "cancelled" | "blocked" | "completed"
                )
            })
        {
            job.visual_projection = Some(Default::default());
        }
        job.visual_projection
            .as_mut()
            .unwrap()
            .deployment_generation = selected.retrieval_deployment_generation;
        if let Some(task) = retry_task {
            let intent = job.visual_projection.as_mut().unwrap();
            intent.task_id = Some(task);
            intent.retry_requested = true;
            intent.worker_fingerprint = selected
                .retrieval_url
                .as_ref()
                .map(|url| format!("{:x}", Sha256::digest(url.as_bytes())));
        }
        persist_memory_job(&self.config, &job).await?;
        self.memory_jobs.write().await.insert(id, job);
        Ok(())
    }

    /// Replay is at-least-once; the existing worker hashes scope/model/input into its job ID.
    /// A lost acknowledgement cannot make a second logical embedding task there.
    pub fn start_projection_replay(&self) -> tokio::task::JoinHandle<()> {
        let state = self.clone();
        tokio::spawn(async move {
            loop {
                if let Err(error) = state.replay_local_metadata().await {
                    error!(%error,"local metadata projection replay failed");
                }
                if state.config.retrieval_url.is_some()
                    || crate::retrieval_deployment::path(&state.config).exists()
                {
                    let registry_revision = crate::retrieval_deployment::read(&state.config)
                        .await
                        .ok()
                        .flatten()
                        .map(|registry| registry.revision);
                    let cleanup: Vec<_> = state
                        .memory_jobs
                        .read()
                        .await
                        .values()
                        .filter(|job| {
                            job.revocation.as_ref().is_some_and(|r| {
                                (r.cleanup_state == "pending" && r.next_attempt_at_ms <= now_ms())
                                    || r.registry_revision != registry_revision
                            })
                        })
                        .map(|job| job.id)
                        .take(2)
                        .collect();
                    for id in cleanup {
                        if let Err(error) = state.replay_revocation(id).await {
                            error!(%id,%error,"revocation cleanup publication failed");
                        }
                    }
                    let mut pending: Vec<_> = state
                        .memory_jobs
                        .read()
                        .await
                        .values()
                        .filter_map(|job| {
                            job.visual_projection
                                .as_ref()
                                .filter(|p| {
                                    job.status == JobStatus::Completed
                                        && job.revocation.is_none()
                                        && matches!(p.state.as_str(), "pending" | "submitted")
                                        && p.next_attempt_at_ms <= now_ms()
                                })
                                .map(|p| (p.next_attempt_at_ms, job.id))
                        })
                        .collect();
                    pending.sort_unstable();
                    // ponytail: bounded sequential dispatch; parallelize only after load measurements.
                    for (_, id) in pending.into_iter().take(2) {
                        if let Err(error) = state.replay_visual_projection(id).await {
                            error!(%id,%error,"visual projection replay publication failed");
                        }
                    }
                }
                tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            }
        })
    }

    async fn replay_visual_projection(&self, id: Uuid) -> anyhow::Result<()> {
        let _dispatch = self.projection_gate.lock().await;
        let Some(mut intent) = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .and_then(|job| job.visual_projection.clone())
        else {
            return Ok(());
        };
        if !matches!(intent.state.as_str(), "pending" | "submitted")
            || intent.next_attempt_at_ms > now_ms()
        {
            return Ok(());
        }
        let selected =
            crate::retrieval_deployment::select(&self.config, intent.deployment_generation).await?;
        let Some(url) = &selected.retrieval_url else {
            return Ok(());
        };
        if legacy_projection_needs_binding(&intent, &selected) {
            let _gate = self.memory_job_gate.lock().await;
            let mut jobs = self.memory_jobs.write().await;
            let job = jobs.get_mut(&id).context("Recording not found")?;
            if job.revocation.is_none() {
                let mut saved = job.clone();
                intent.state = "blocked".into();
                intent.last_error = Some("projection_legacy_binding_required".into());
                saved.visual_projection = Some(intent);
                persist_memory_job(&self.config, &saved).await?;
                *job = saved;
            }
            return Ok(());
        }
        let binding_changed = intent.deployment_generation
            != selected.retrieval_deployment_generation
            || intent.worker_fingerprint.is_none();
        intent.deployment_generation = selected.retrieval_deployment_generation;
        let target = format!("{:x}", Sha256::digest(url.as_bytes()));
        if intent.worker_fingerprint.is_none() {
            intent.worker_fingerprint = Some(target.clone());
        }
        // Commit target binding before a remote side effect, including the first
        // attempt whose acknowledgement may be lost across a deployment switch.
        if binding_changed {
            let _gate = self.memory_job_gate.lock().await;
            let mut jobs = self.memory_jobs.write().await;
            let job = jobs.get_mut(&id).context("Recording not found")?;
            if job.revocation.is_some() {
                return Ok(());
            }
            let mut saved = job.clone();
            saved.visual_projection = Some(intent.clone());
            persist_memory_job(&self.config, &saved).await?;
            *job = saved;
        }
        let revision = intent.revision;
        let attempt = async {
            anyhow::ensure!(
                intent
                    .worker_fingerprint
                    .as_ref()
                    .is_none_or(|old| old == &target),
                "projection_worker_changed"
            );
            if crate::retrieval_deployment::incompatible(&self.config, intent.deployment_generation)
                .await?
            {
                self.cancel_projection_task(id, &intent, &selected)
                    .await
                    .map_err(|error| anyhow::anyhow!("projection_worker_unavailable: {error}"))?;
                bail!("projection_deployment_retired");
            }
            // Recheck even when polling: changed media must not retain an advertised watermark.
            let events = self.visual_index_events(id).await?;
            let client = reqwest::Client::builder()
                .connect_timeout(std::time::Duration::from_secs(2))
                .timeout(std::time::Duration::from_secs(
                    self.config.retrieval_timeout_secs.min(120),
                ))
                .build()?;
            let request = if let Some(task) = intent.task_id {
                if intent.retry_requested {
                    client.post(format!(
                        "{}/index-jobs/{task}/retry",
                        url.trim_end_matches('/')
                    ))
                } else {
                    client.get(format!("{}/index-jobs/{task}", url.trim_end_matches('/')))
                }
            } else {
                client
                    .post(format!("{}/index-jobs", url.trim_end_matches('/')))
                    .json(&serde_json::json!({"scope_id":id,"events":events}))
            };
            let response = selected
                .pin_retrieval(request)
                .send()
                .await
                .map_err(|_| anyhow::anyhow!("projection_worker_unavailable"))?;
            if response.status() == reqwest::StatusCode::NOT_FOUND && intent.task_id.is_some() {
                intent.task_id = None;
                intent.retry_requested = false;
                bail!("projection_task_missing_retry_submission");
            }
            anyhow::ensure!(
                response.status().is_success(),
                "projection_worker_http_{}",
                response.status().as_u16()
            );
            let body = worker_receipt(response).await?;
            selected.verify_retrieval_profile(&body["worker_profile"])?;
            if selected.retrieval_expected_profile.is_some() {
                anyhow::ensure!(
                    body["profile_current"] == true,
                    "projection_embedding_profile_changed"
                );
            }
            let task: Uuid = body["id"]
                .as_str()
                .context("projection_missing_task_id")?
                .parse()?;
            anyhow::ensure!(
                body["scope_id"].as_str() == Some(id.to_string().as_str()),
                "projection_receipt_scope_mismatch"
            );
            if let Some(expected) = intent.task_id {
                anyhow::ensure!(expected == task, "projection_receipt_task_mismatch");
            }
            if crate::retrieval_deployment::incompatible(&self.config, intent.deployment_generation)
                .await?
            {
                intent.task_id = Some(task);
                self.cancel_projection_task(id, &intent, &selected)
                    .await
                    .map_err(|error| anyhow::anyhow!("projection_worker_unavailable: {error}"))?;
                bail!("projection_deployment_retired");
            }
            let status = body["status"]
                .as_str()
                .context("projection_missing_state")?;
            anyhow::ensure!(
                matches!(
                    status,
                    "queued" | "running" | "completed" | "failed" | "cancelled"
                ),
                "projection_invalid_state"
            );
            if status == "completed" {
                let total = body["total"].as_u64().context("projection_missing_total")?;
                anyhow::ensure!(
                    total == events.len() as u64
                        && body["completed"].as_u64() == Some(total)
                        && body["failed"].as_u64() == Some(0)
                        && body["embedding"].is_object(),
                    "projection_incomplete_receipt"
                );
            }
            intent.task_id = Some(task);
            intent.retry_requested = false;
            intent.state = if matches!(status, "queued" | "running") {
                "submitted"
            } else {
                status
            }
            .into();
            intent.worker_fingerprint = Some(target);
            intent.receipt = Some(body);
            Ok::<_, anyhow::Error>(())
        }
        .await;
        intent.attempts = intent.attempts.saturating_add(1);
        match attempt {
            Ok(()) => {
                intent.last_error = None;
                intent.next_attempt_at_ms = now_ms().saturating_add(5_000);
            }
            Err(error) => {
                let message = format!("{error:#}");
                // Only transport/capacity failures retry automatically; evidence/contract failure needs review.
                if !message.starts_with("projection_worker_unavailable")
                    && !message.starts_with("projection_worker_http_5")
                    && !message.starts_with("projection_worker_http_429")
                    && !message.starts_with("projection_task_missing")
                {
                    intent.state = "blocked".into();
                }
                if message == "projection_deployment_retired" {
                    intent.state = "cancelled".into();
                }
                intent.last_error = Some(message);
                intent.next_attempt_at_ms = now_ms()
                    .saturating_add(1_000_u64.saturating_mul(2_u64.pow(intent.attempts.min(6))));
            }
        }
        let _gate = self.memory_job_gate.lock().await;
        let mut job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("Recording not found")?;
        // Do not publish a stale acknowledgement over a newer explicit prepare request.
        if job.status == JobStatus::Completed
            && job.revocation.is_none()
            && job
                .visual_projection
                .as_ref()
                .is_some_and(|p| p.revision == revision)
        {
            job.visual_projection = Some(intent);
            persist_memory_job(&self.config, &job).await?;
            self.memory_jobs.write().await.insert(id, job);
        }
        Ok(())
    }

    async fn enrich_entity_memory(
        &self,
        job_id: Uuid,
        camera: &mut crate::domain::MemoryCameraResult,
    ) -> anyhow::Result<()> {
        let source = camera
            .events
            .first()
            .context("No indexed evidence to link")?
            .source_evidence_path
            .clone();
        let needs_sanitized = self.config.media_policy == "sanitized-v1"
            || camera.events.iter().any(|event| event.privacy.is_some())
            || self
                .memory_jobs
                .read()
                .await
                .get(&job_id)
                .is_some_and(|job| job.execution_profile.is_some());
        let digest = if needs_sanitized {
            crate::privacy_media::verify_camera(camera)
                .await?
                .media_sha256
        } else {
            crate::privacy_media::file_hash(&source).await?
        };
        let profile = if needs_sanitized {
            Some(crate::execution_profile::tracking_snapshot(&self.config, camera, &digest).await?)
        } else {
            None
        };
        let camera_job = Uuid::new_v5(
            &job_id,
            crate::domain::memory_projection_key(
                &camera.camera.camera_id,
                camera.source_session_id.as_deref(),
            )
            .as_bytes(),
        );
        info!(%job_id, camera_id=%camera.camera.camera_id, "experiment 3 tracking retained source");
        let (model, analysis) = self
            .pipeline
            .memory_tracks(
                camera_job,
                source,
                camera.duration_ms.div_ceil(1000).max(1),
                &camera
                    .events
                    .iter()
                    .map(|e| (e.start_ms, e.end_ms))
                    .collect::<Vec<_>>(),
                profile
                    .as_ref()
                    .and_then(|p| p.components.get("detector-identity"))
                    .map(String::as_str),
            )
            .await?;
        if let Some(expected) = &profile {
            let actual_source = crate::privacy_media::verify_camera(camera).await?;
            let actual = crate::execution_profile::tracking_snapshot(
                &self.config,
                camera,
                &actual_source.media_sha256,
            )
            .await?;
            anyhow::ensure!(
                expected == &actual,
                "execution_profile_changed: tracker enrichment recipe changed during inference"
            );
        }
        let mut graph = crate::long_memory::build(job_id, camera, &analysis, model, digest);
        graph.execution_profile = profile;
        crate::metadata_projection::validate_graph(job_id, camera, &graph)?;
        camera.long_memory = Some(graph);
        info!(%job_id, camera_id=%camera.camera.camera_id, entities=camera.long_memory.as_ref().unwrap().entities.len(), "experiment 3 entity memory built");
        Ok(())
    }

    pub async fn build_entity_memory(&self, id: Uuid) -> anyhow::Result<MemoryJobRecord> {
        // Serialize manifest mutations; inference itself also shares the detector gate.
        let _guard = self.long_memory_gate.lock().await;
        let mut job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("Recording not found")?;
        anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
        if job.status != JobStatus::Completed {
            bail!("Choose a completed recording");
        }
        let result = job.result.as_mut().context("Recording has no evidence")?;
        let mut selected_detector = None;
        for camera in &mut result.camera_results {
            self.enrich_entity_memory(id, camera).await?;
            if let Some(profile) = camera
                .long_memory
                .as_ref()
                .and_then(|graph| graph.execution_profile.as_ref())
            {
                let detector = profile
                    .components
                    .get("detector-identity")
                    .context("execution_profile_detector_missing")?;
                anyhow::ensure!(
                    selected_detector
                        .as_ref()
                        .is_none_or(|selected| selected == detector),
                    "execution_profile_changed: tracker recipe changed between camera sessions"
                );
                selected_detector = Some(detector.clone());
            }
        }
        if let Some(camera) = result
            .camera_results
            .first()
            .filter(|_| selected_detector.is_some())
        {
            let actual = crate::privacy_media::verify_camera(camera).await?;
            let latest = crate::execution_profile::tracking_snapshot(
                &self.config,
                camera,
                &actual.media_sha256,
            )
            .await?;
            anyhow::ensure!(
                latest.components.get("detector-identity") == selected_detector.as_ref(),
                "execution_profile_changed: tracker recipe changed before publication"
            );
        }
        // Persist before exposing the replacement. Failure leaves the old memory intact.
        let _gate = self.memory_job_gate.lock().await;
        let mut current = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("Recording not found")?;
        anyhow::ensure!(current.revocation.is_none(), "recording_revoked");
        anyhow::ensure!(
            current.status == JobStatus::Completed,
            "recording_not_completed"
        );
        current.result = job.result;
        current.metadata_projection = Some(crate::metadata_projection::queued(
            &current,
            memory_policy(&current, &self.config.media_policy),
        ));
        persist_memory_job(&self.config, &current).await?;
        self.memory_jobs.write().await.insert(id, current.clone());
        Ok(redact_memory_job(current))
    }

    pub async fn memory_event_artifact(
        &self,
        event_id: Uuid,
        kind: &str,
    ) -> Option<std::path::PathBuf> {
        let jobs = self.memory_jobs.read().await;
        for job in jobs.values() {
            if job.revocation.is_some() {
                continue;
            }
            let Some(result) = &job.result else { continue };
            for camera in &result.camera_results {
                if let Some(event) = camera
                    .events
                    .iter()
                    .find(|event| event.event_id == event_id)
                {
                    let path = match kind {
                        "thumbnail" => Some(event.thumbnail_path.clone()),
                        "clip" => Some(event.clip_path.clone()),
                        "source" => Some(event.source_evidence_path.clone()),
                        _ => None,
                    }?;
                    if memory_policy(&job, &self.config.media_policy) == "sanitized-v1" {
                        let privacy = event.privacy.as_ref()?;
                        let verified = crate::privacy_media::verify(&event.source_evidence_path)
                            .await
                            .ok()?;
                        if verified != privacy.source || !event.source_session_matches_provenance()
                        {
                            return None;
                        }
                        let expected = match kind {
                            "thumbnail" => &privacy.thumbnail_sha256,
                            "clip" => &privacy.clip_sha256,
                            _ => &privacy.source.media_sha256,
                        };
                        if crate::privacy_media::file_hash(&path).await.ok()?.as_str() != expected {
                            return None;
                        }
                    }
                    return Some(path);
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
        let execution_profile = if self.config.media_policy == "sanitized-v1" {
            Some(crate::execution_profile::snapshot(&self.config, &request).await?)
        } else {
            None
        };
        let _gate = self.memory_job_gate.lock().await;
        let mut canonical = request.clone();
        canonical.idempotency_key = None;
        let identity = match &execution_profile {
            Some(profile) => serde_json::to_vec(&(
                self.config.media_policy.as_str(),
                canonical,
                &profile.fingerprint,
            ))?,
            None => serde_json::to_vec(&(self.config.media_policy.as_str(), canonical))?,
        };
        let fingerprint = format!("{:x}", Sha256::digest(identity));
        if let Some(key) = &request.idempotency_key {
            if let Some(existing) = self
                .memory_jobs
                .read()
                .await
                .values()
                .find(|j| j.request.idempotency_key.as_ref() == Some(key))
                .cloned()
            {
                if existing.request_fingerprint.as_ref() != Some(&fingerprint) {
                    bail!("idempotency_key_conflict");
                }
                return Ok(redact_memory_job(existing));
            }
        }
        if self
            .memory_jobs
            .read()
            .await
            .values()
            .filter(|job| matches!(job.status, JobStatus::Queued | JobStatus::Running))
            .count()
            >= self.config.max_ephemeral_jobs
        {
            bail!("memory_recording_queue_full");
        }
        let id = Uuid::now_v7();
        let record = MemoryJobRecord {
            storage_version: 1,
            execution_profile,
            metadata_projection: None,
            revocation: None,
            visual_projection: None,
            request_fingerprint: Some(fingerprint),
            camera_checkpoints: Default::default(),
            recovery_attempts: 0,
            id,
            request: request.clone(),
            status: JobStatus::Queued,
            created_at_ms: now_ms(),
            started_at_ms: None,
            completed_at_ms: None,
            result: None,
            error: None,
        };
        // Durable before HTTP acknowledgement or worker admission.
        persist_memory_job(&self.config, &record).await?;
        self.memory_jobs.write().await.insert(id, record.clone());
        self.start_memory_task(id, request, false).await;
        Ok(redact_memory_job(record))
    }

    async fn assert_execution_profile(&self, id: Uuid) -> anyhow::Result<()> {
        let job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("recording_not_found")?;
        if memory_policy(&job, &self.config.media_policy) != "sanitized-v1" {
            return Ok(());
        }
        let expected = job
            .execution_profile
            .as_ref()
            .context("execution_profile_unavailable")?;
        expected.validate()?;
        anyhow::ensure!(
            expected.version == crate::execution_profile::VERSION,
            "execution_profile_stage_mismatch"
        );
        let actual = crate::execution_profile::snapshot(&self.config, &job.request).await?;
        anyhow::ensure!(expected == &actual, "execution_profile_changed: restore the original recording recipe before executing unfinished work");
        Ok(())
    }

    async fn read_only_checkpoints(&self, job: &MemoryJobRecord) -> anyhow::Result<bool> {
        for camera in &job.request.cameras {
            let Some(results) = self.retained_checkpoint(job.id, &camera.camera_id).await? else {
                return Ok(false);
            };
            if job.request.long_memory_enabled
                && results.iter().any(|result| result.long_memory.is_none())
            {
                return Ok(false);
            }
        }
        Ok(true)
    }

    async fn start_memory_task(&self, id: Uuid, request: MemoryJobRequest, retained_only: bool) {
        let mut tasks = self.memory_tasks.lock().await;
        let state = self.clone();
        let task = tokio::spawn(async move {
            let start = async {
                let _gate = state.memory_job_gate.lock().await;
                let mut job = state
                    .memory_jobs
                    .read()
                    .await
                    .get(&id)
                    .cloned()
                    .context("recording_job_missing")?;
                if job.status != JobStatus::Queued {
                    return Ok(None);
                }
                if let Some(profile) = &job.execution_profile {
                    profile.validate()?;
                    anyhow::ensure!(
                        profile.version == crate::execution_profile::VERSION,
                        "execution_profile_stage_mismatch"
                    );
                }
                let read_only_recovery = retained_only && state.read_only_checkpoints(&job).await?;
                if !read_only_recovery {
                    if let Err(error) = state.assert_execution_profile(id).await {
                        job.status = JobStatus::Failed;
                        job.completed_at_ms = Some(now_ms());
                        job.error = Some(format!("{error:#}"));
                        persist_memory_job(&state.config, &job).await?;
                        state.memory_jobs.write().await.insert(id, job);
                        return Ok(None);
                    }
                }
                job.status = JobStatus::Running;
                job.started_at_ms = Some(now_ms());
                persist_memory_job(&state.config, &job).await?;
                state.memory_jobs.write().await.insert(id, job);
                Ok::<_, anyhow::Error>(Some(read_only_recovery))
            }
            .await;
            let read_only_recovery = match start {
                Ok(Some(read_only)) => read_only,
                Ok(None) => {
                    state.memory_tasks.lock().await.remove(&id);
                    return;
                }
                Err(error) => {
                    error!(%id,%error,"could not durably start recording");
                    let _gate = state.memory_job_gate.lock().await;
                    let mut job = state.memory_jobs.read().await.get(&id).cloned();
                    if let Some(job) = job.as_mut().filter(|job| job.status == JobStatus::Queued) {
                        job.status = JobStatus::Failed;
                        job.completed_at_ms = Some(now_ms());
                        job.error = Some(format!("recording_start_failed: {error:#}"));
                        if persist_memory_job(&state.config, job).await.is_ok() {
                            state.memory_jobs.write().await.insert(id, job.clone());
                        }
                    }

                    state.memory_tasks.lock().await.remove(&id);
                    return;
                }
            };
            let result = match state
                .process_memory(id, &request, retained_only, read_only_recovery)
                .await
            {
                Ok(result) if !read_only_recovery => {
                    state.assert_execution_profile(id).await.map(|_| result)
                }
                other => other,
            };
            let finish = async {
                let _gate = state.memory_job_gate.lock().await;
                let mut job = state
                    .memory_jobs
                    .read()
                    .await
                    .get(&id)
                    .cloned()
                    .context("recording_job_missing")?;
                if job.status != JobStatus::Running {
                    return Ok(());
                }
                job.completed_at_ms = Some(now_ms());
                match result {
                    Ok(result) => {
                        job.status = JobStatus::Completed;
                        job.result = Some(result);
                        job.metadata_projection = Some(crate::metadata_projection::queued(
                            &job,
                            memory_policy(&job, &state.config.media_policy),
                        ));
                        job.visual_projection = Some(Default::default());
                        job.error = None;
                    }
                    Err(error) => {
                        job.status = JobStatus::Failed;
                        job.error = Some(format!("{error:#}"));
                    }
                }
                if let Err(error) = persist_memory_job(&state.config, &job).await {
                    job.status = JobStatus::Failed;
                    job.result = None;
                    job.error = Some(format!("recording_completion_not_durable: {error}"));
                    state.memory_jobs.write().await.insert(id, job);
                    return Err(error);
                }
                state.memory_jobs.write().await.insert(id, job);
                Ok::<_, anyhow::Error>(())
            }
            .await;
            if let Err(error) = finish {
                error!(%id,%error,"recording completion publication failed");
            }
            state.memory_tasks.lock().await.remove(&id);
        });
        tasks.insert(id, task.abort_handle());
    }

    pub async fn revoke_memory(&self, id: Uuid) -> anyhow::Result<MemoryJobRecord> {
        let _gate = self.memory_job_gate.lock().await;
        let mut job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("recording_not_found")?;
        if let Some(tombstone) = &mut job.revocation {
            let mut changed = false;
            if tombstone.local_cleanup_state == "blocked" {
                tombstone.local_cleanup_state = "pending".into();
                tombstone.local_cleanup_error = None;
                changed = true;
            }
            if tombstone.cleanup_state == "blocked" {
                tombstone.cleanup_state = "pending".into();
                tombstone.next_attempt_at_ms = 0;
                tombstone.last_error = None;
                changed = true;
            }
            if changed {
                persist_memory_job(&self.config, &job).await?;
                self.memory_jobs.write().await.insert(id, job.clone());
            }
            let data = self.config.data_dir.clone();
            let saved = redact_memory_job(job.clone());
            tokio::task::spawn_blocking(move || crate::revocation_witness::record(&data, &saved))
                .await?
                .map_err(|error| anyhow::anyhow!("revocation_witness_unavailable: {error}"))?;
            return Ok(redact_memory_job(job));
        }
        let worker_fingerprint = job
            .visual_projection
            .as_ref()
            .and_then(|p| p.worker_fingerprint.clone())
            .or_else(|| {
                self.config
                    .retrieval_url
                    .as_ref()
                    .map(|url| format!("{:x}", Sha256::digest(url.as_bytes())))
            });
        job.revocation = Some(crate::domain::RecordingRevocation {
            source_paths: {
                let mut paths: Vec<String> = job
                    .result
                    .as_ref()
                    .into_iter()
                    .flat_map(|result| result.camera_results.iter())
                    .flat_map(|camera| camera.events.iter())
                    .map(|event| {
                        std::path::absolute(&event.source_evidence_path)
                            .map(|path| path.to_string_lossy().into_owned())
                    })
                    .collect::<Result<_, _>>()?;
                paths.extend(
                    job.request
                        .cameras
                        .iter()
                        .map(|camera| {
                            std::path::absolute(
                                self.camera_directory(id, &camera.camera_id)
                                    .join("source.mkv"),
                            )
                            .map(|path| path.to_string_lossy().into_owned())
                        })
                        .collect::<Result<Vec<_>, _>>()?,
                );
                paths.sort();
                paths.dedup();
                paths
            },
            target_receipts: Default::default(),
            registry_revision: None,
            local_cleanup_state: "pending".into(),
            local_cleanup_error: None,
            revision: Uuid::now_v7(),
            revoked_at_ms: now_ms(),
            cleanup_state: "pending".into(),
            attempts: 0,
            next_attempt_at_ms: 0,
            worker_fingerprint,
            last_error: None,
            receipt: None,
        });
        job.status = JobStatus::Cancelled;
        job.completed_at_ms = Some(now_ms());
        job.error = Some("recording_revoked".into());
        // Retained media is not erased. Remove searchable event/entity descriptors from the canonical job.
        job.result = None;
        job.camera_checkpoints.clear();
        if let Some(intent) = &mut job.metadata_projection {
            intent.state = "cancelled".into();
            intent.output_sha256 = None;
        }
        if let Some(intent) = &mut job.visual_projection {
            intent.state = "cancelled".into();
            intent.receipt = None;
            intent.retry_requested = false;
        }
        persist_memory_job(&self.config, &job).await?;
        self.memory_jobs.write().await.insert(id, job.clone());
        if let Some(task) = self.memory_tasks.lock().await.remove(&id) {
            task.abort();
        }
        let data = self.config.data_dir.clone();
        let saved = redact_memory_job(job.clone());
        tokio::task::spawn_blocking(move || crate::revocation_witness::record(&data, &saved))
            .await?
            .map_err(|error| anyhow::anyhow!("revocation_witness_unavailable: {error}"))?;
        Ok(redact_memory_job(job))
    }

    async fn replay_revocation(&self, id: Uuid) -> anyhow::Result<()> {
        let _dispatch = self.projection_gate.lock().await;
        let (registry_revision, targets) =
            crate::retrieval_deployment::cleanup_targets(&self.config).await?;
        if targets.is_empty() {
            return Ok(());
        }
        let Some(job) = self.memory_jobs.read().await.get(&id).cloned() else {
            return Ok(());
        };
        let Some(mut tombstone) = job.revocation else {
            return Ok(());
        };
        let changed = tombstone.registry_revision != registry_revision;
        if !changed
            && (tombstone.cleanup_state == "completed"
                || tombstone.cleanup_state == "blocked"
                || tombstone.next_attempt_at_ms > now_ms())
        {
            return Ok(());
        }
        let revision = tombstone.revision;
        tombstone.cleanup_state = "pending".into();
        let attempt=async {
            anyhow::ensure!(tombstone.worker_fingerprint.as_ref().is_none_or(|old|targets.contains_key(old) || tombstone.target_receipts.contains_key(old)),"revocation_worker_changed");
            let paths:Vec<String>=if tombstone.source_paths.is_empty() {
                job.request.cameras.iter().map(|camera|std::path::absolute(self.camera_directory(id,&camera.camera_id).join("source.mkv"))
                    .map(|path|path.to_string_lossy().into_owned())).collect::<Result<_,_>>()?
            } else {tombstone.source_paths.clone()};
            let client=reqwest::Client::builder().connect_timeout(std::time::Duration::from_secs(2))
                .timeout(std::time::Duration::from_secs(self.config.retrieval_timeout_secs.min(120))).build()?;
            let mut failures=Vec::new();
            for (target,(url,storage)) in &targets {
                let valid=tombstone.target_receipts.get(target).is_some_and(|receipt|receipt["source_contract"]=="recording-scope-v1" && storage.as_ref().is_none_or(|expected|receipt["index_storage"].as_str()==Some(expected.as_str())));
                if valid {continue}
                let result=async {
                let response=client.post(format!("{}/revocations",url.trim_end_matches('/')))
                    .json(&serde_json::json!({"scope_id":id,"revision":revision,"source_paths":paths,"source_contract":"recording-scope-v1"})).send().await
                    .map_err(|_|anyhow::anyhow!("projection_worker_unavailable"))?;
                anyhow::ensure!(response.status().is_success(),"projection_worker_http_{}",response.status().as_u16());
                let receipt=worker_receipt(response).await?;
                anyhow::ensure!(receipt["scope_id"].as_str()==Some(id.to_string().as_str())
                    && receipt["revision"].as_str()==Some(revision.to_string().as_str())
                    && receipt["source_contract"]=="recording-scope-v1"
                    && receipt["status"]=="revoked" && receipt["cleanup_complete"]==true,"revocation_invalid_receipt");
                anyhow::ensure!(storage.as_ref().is_none_or(|expected|receipt["index_storage"].as_str()==Some(expected.as_str())),"revocation_storage_changed");
                tombstone.receipt=Some(receipt.clone());
                tombstone.target_receipts.insert(target.clone(),receipt);
                Ok::<_,anyhow::Error>(())
                }.await;
                if let Err(error)=result {failures.push(error.to_string());}
            }
            tombstone.registry_revision=registry_revision;
            if let Some(error)=failures.first() {bail!("{}",error);}
            Ok::<_,anyhow::Error>(())
        }.await;
        tombstone.registry_revision = registry_revision;
        tombstone.attempts = tombstone.attempts.saturating_add(1);
        match attempt {
            Ok(()) => {
                tombstone.cleanup_state = "completed".into();
                tombstone.last_error = None;
            }
            Err(error) => {
                let message = format!("{error:#}");
                if !message.starts_with("projection_worker_unavailable")
                    && !message.starts_with("projection_worker_http_5")
                    && !message.starts_with("projection_worker_http_429")
                {
                    tombstone.cleanup_state = "blocked".into();
                }
                tombstone.last_error = Some(message);
                tombstone.next_attempt_at_ms = now_ms()
                    .saturating_add(1000_u64.saturating_mul(2_u64.pow(tombstone.attempts.min(6))));
            }
        }
        let _gate = self.memory_job_gate.lock().await;
        let mut current = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("recording_not_found")?;
        if current
            .revocation
            .as_ref()
            .is_some_and(|r| r.revision == revision)
        {
            if let Some(active) = &current.revocation {
                tombstone.local_cleanup_state = active.local_cleanup_state.clone();
                tombstone.local_cleanup_error = active.local_cleanup_error.clone();
            }
            current.revocation = Some(tombstone);
            persist_memory_job(&self.config, &current).await?;
            self.memory_jobs.write().await.insert(id, current);
        }
        Ok(())
    }

    pub async fn cancel_memory(&self, id: Uuid) -> anyhow::Result<MemoryJobRecord> {
        let _gate = self.memory_job_gate.lock().await;
        let mut job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("recording_not_found")?;
        anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
        if matches!(job.status, JobStatus::Queued | JobStatus::Running) {
            job.status = JobStatus::Cancelled;
            job.completed_at_ms = Some(now_ms());
            job.error =
                Some("recording_cancelled: only already committed evidence can be resumed".into());
            persist_memory_job(&self.config, &job).await?;
            self.memory_jobs.write().await.insert(id, job.clone());
            if let Some(task) = self.memory_tasks.lock().await.remove(&id) {
                task.abort();
            }
        }
        Ok(redact_memory_job(job))
    }

    pub async fn resume_memory(&self, id: Uuid) -> anyhow::Result<MemoryJobRecord> {
        let _gate = self.memory_job_gate.lock().await;
        let mut job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("recording_not_found")?;
        anyhow::ensure!(job.revocation.is_none(), "recording_revoked");
        if matches!(
            job.status,
            JobStatus::Completed | JobStatus::Queued | JobStatus::Running
        ) {
            return Ok(redact_memory_job(job));
        }
        if memory_policy(&job, &self.config.media_policy) != "sanitized-v1" {
            bail!("retained_recovery_requires_sanitized_profile");
        }
        if !self.read_only_checkpoints(&job).await? {
            self.assert_execution_profile(id).await?;
        }
        if self
            .memory_jobs
            .read()
            .await
            .values()
            .filter(|job| matches!(job.status, JobStatus::Queued | JobStatus::Running))
            .count()
            >= self.config.max_ephemeral_jobs
        {
            bail!("memory_recording_queue_full");
        }
        let mut available = false;
        for camera in &job.request.cameras {
            if job.camera_checkpoints.contains_key(&camera.camera_id) {
                // The task verifies the anchored set and every artifact before
                // reuse. An invalid checkpoint fails; it never reconnects.
                available = true;
                break;
            }
            if job.request.capture_sessions {
                let marker = self
                    .camera_directory(id, &camera.camera_id)
                    .join("capture/capture.json");
                if tokio::fs::metadata(&marker)
                    .await
                    .is_ok_and(|m| m.is_file() && m.len() <= 256 * 1024)
                {
                    let bytes = tokio::fs::read(&marker).await?;
                    if bytes.len() <= 256 * 1024
                        && serde_json::from_slice::<serde_json::Value>(&bytes).is_ok_and(|value| {
                            value["schema"] == "visn-sanitized-capture-v1"
                                && value["sessions"].as_array().is_some_and(|sessions| {
                                    !sessions.is_empty() && sessions.len() <= 256
                                })
                        })
                    {
                        available = true;
                        break;
                    }
                }
            }
            let source = self
                .camera_directory(id, &camera.camera_id)
                .join("source.mkv");
            if crate::privacy_media::verify(&source).await.is_ok() {
                available = true;
                break;
            }
        }
        if !available {
            bail!("no_committed_evidence_to_resume: a new live recording needs a new operation");
        }
        job.status = JobStatus::Queued;
        job.completed_at_ms = None;
        job.error = None;
        job.result = None;
        job.recovery_attempts = job
            .recovery_attempts
            .checked_add(1)
            .context("recovery_attempt_limit")?;
        persist_memory_job(&self.config, &job).await?;
        self.memory_jobs.write().await.insert(id, job.clone());
        self.start_memory_task(id, job.request.clone(), true).await;
        Ok(redact_memory_job(job))
    }

    fn camera_directory(&self, id: Uuid, camera_id: &str) -> std::path::PathBuf {
        self.config
            .data_dir
            .join("memory")
            .join(id.to_string())
            .join(safe_memory_component(camera_id))
    }

    async fn checkpoint_camera(
        &self,
        id: Uuid,
        results: &[crate::domain::MemoryCameraResult],
    ) -> anyhow::Result<()> {
        self.assert_execution_profile(id).await?;
        let _gate = self.memory_job_gate.lock().await;
        let mut job = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .cloned()
            .context("recording_job_missing")?;
        if job.status != JobStatus::Running {
            bail!("recording_no_longer_running");
        }
        let camera_id = results
            .first()
            .context("camera_checkpoint_empty")?
            .camera
            .camera_id
            .clone();
        validate_checkpoint_set(id, &camera_id, results)?;
        let mut redacted = results.to_vec();
        for result in &mut redacted {
            result.camera.live_url = redacted_uri(&result.camera.live_url);
        }
        // Keep the old single-camera representation byte-compatible in shape.
        let bytes = if redacted.len() == 1 {
            serde_json::to_vec_pretty(&redacted[0])?
        } else {
            serde_json::to_vec_pretty(&redacted)?
        };
        anyhow::ensure!(
            bytes.len() <= 32 * 1024 * 1024,
            "camera_checkpoint_size_limit"
        );
        let hash = format!("{:x}", Sha256::digest(&bytes));
        let path = self
            .camera_directory(id, &camera_id)
            .join("camera-result.json");
        crate::durable::publish(path, bytes).await?;
        job.camera_checkpoints.insert(camera_id, hash);
        persist_memory_job(&self.config, &job).await?;
        self.memory_jobs.write().await.insert(id, job);
        Ok(())
    }

    async fn retained_checkpoint(
        &self,
        id: Uuid,
        camera_id: &str,
    ) -> anyhow::Result<Option<Vec<crate::domain::MemoryCameraResult>>> {
        let expected = self
            .memory_jobs
            .read()
            .await
            .get(&id)
            .and_then(|j| j.camera_checkpoints.get(camera_id))
            .cloned();
        let Some(expected) = expected else {
            return Ok(None);
        };
        let path = self
            .camera_directory(id, camera_id)
            .join("camera-result.json");
        anyhow::ensure!(
            tokio::fs::metadata(&path).await?.len() <= 32 * 1024 * 1024,
            "camera_checkpoint_size_limit"
        );
        let bytes = tokio::fs::read(path).await?;
        anyhow::ensure!(
            bytes.len() <= 32 * 1024 * 1024,
            "camera_checkpoint_size_limit"
        );
        if format!("{:x}", Sha256::digest(&bytes)) != expected {
            bail!("camera_checkpoint_changed");
        }
        let mut cameras = decode_camera_checkpoint(&bytes, id, camera_id)?;
        for camera in &mut cameras {
            rehydrate_camera_paths(&self.config, id, camera);
            for event in &camera.events {
                let privacy = event.privacy.as_ref().context("checkpoint_not_sanitized")?;
                let source = crate::privacy_media::verify(&event.source_evidence_path).await?;
                if source != privacy.source
                    || event.source_session_id != camera.source_session_id
                    || !event.source_session_matches_provenance()
                    || crate::privacy_media::file_hash(&event.thumbnail_path).await?
                        != privacy.thumbnail_sha256
                    || crate::privacy_media::file_hash(&event.clip_path).await?
                        != privacy.clip_sha256
                {
                    bail!("camera_checkpoint_artifact_changed");
                }
            }
        }
        for camera in &cameras {
            if let Some(graph) = &camera.long_memory {
                anyhow::ensure!(
                    graph.version == crate::long_memory::VERSION,
                    "checkpoint_graph_version_unsupported"
                );
                crate::metadata_projection::validate_graph(id, camera, graph)?;
            }
        }
        Ok(Some(cameras))
    }

    async fn process_memory(
        &self,
        job_id: Uuid,
        request: &MemoryJobRequest,
        retained_only: bool,
        read_only_recovery: bool,
    ) -> anyhow::Result<MemoryJobResult> {
        let service = self.memory.clone();
        let cluster_id = request.cluster_id.clone();
        let duration = request.monitor_duration_secs;
        let fps = request.observer_fps;
        let vlm_enabled = request.vlm_enabled;
        let temporal_enabled = request.temporal_enabled;
        let capture_sessions = request.capture_sessions;
        let model = request.vlm_model.clone();
        let outcomes = stream::iter(request.cameras.clone().into_iter().map(|camera| {
            let service = service.clone();
            let state = self.clone();
            let cluster_id = cluster_id.clone();
            let model = model.clone();
            async move {
                let camera_id = camera.camera_id.clone();
                if retained_only {
                    match state.retained_checkpoint(job_id, &camera_id).await {
                        Ok(Some(camera)) => return (camera_id, Ok(camera)),
                        Err(error) => return (camera_id, Err(error)),
                        Ok(None) => {}
                    }
                }
                let result = service
                    .process_camera(
                        job_id,
                        cluster_id.as_deref(),
                        camera,
                        duration,
                        fps,
                        vlm_enabled,
                        temporal_enabled,
                        model.as_deref(),
                        retained_only,
                        capture_sessions,
                    )
                    .await;
                let result = match result {
                    Ok(cameras) => match state.checkpoint_camera(job_id, &cameras).await {
                        Ok(()) => Ok(cameras),
                        Err(error) => Err(error),
                    },
                    Err(error) => Err(error),
                };
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
                Ok(results) => {
                    for mut result in results {
                        if request.long_memory_enabled
                            && !(retained_only && result.long_memory.is_some())
                        {
                            if let Err(error) = self.enrich_entity_memory(job_id, &mut result).await
                            {
                                result.data_quality_notes.push(format!(
                                    "Experiment 3 entity memory unavailable: {error:#}"
                                ));
                            }
                        }
                        camera_results.push(result);
                    }
                }
                Err(error) => camera_failures.push(MemoryCameraFailure {
                    camera_id,
                    error: format!("{error:#}"),
                }),
            }
        }
        if request.long_memory_enabled && !read_only_recovery {
            let mut groups =
                std::collections::BTreeMap::<String, Vec<crate::domain::MemoryCameraResult>>::new();
            for camera in &camera_results {
                groups
                    .entry(camera.camera.camera_id.clone())
                    .or_default()
                    .push(camera.clone());
            }
            for cameras in groups.values() {
                self.checkpoint_camera(job_id, cameras).await?;
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
            cameras_processed: camera_results
                .iter()
                .map(|c| &c.camera.camera_id)
                .collect::<HashSet<_>>()
                .len(),
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
        let selected_retrieval = if request.semantic_enabled || request.temporal_enabled {
            crate::retrieval_deployment::select(&self.config, None).await?
        } else {
            (*self.config).clone()
        };
        let query_id = Uuid::now_v7();
        let started = Instant::now();
        info!(%query_id, semantic = request.semantic_enabled, rerank = request.rerank_enabled, "memory retrieval started");
        let snapshot_started = Instant::now();
        let (snapshot, sanitized_jobs) = {
            let jobs = self.memory_jobs.read().await;
            if request
                .job_id
                .is_some_and(|id| jobs.get(&id).is_some_and(|job| job.revocation.is_some()))
            {
                bail!("recording_revoked");
            }
            let sanitized_jobs: HashSet<_> = jobs
                .values()
                .filter(|job| memory_policy(job, &self.config.media_policy) == "sanitized-v1")
                .map(|job| job.id)
                .collect();
            let snapshot = memory_query_snapshot(
                &request,
                jobs.values()
                    .filter(|job| job.revocation.is_none())
                    .filter_map(|job| job.result.as_ref())
                    .flat_map(|result| result.camera_results.iter()),
            );
            (snapshot, sanitized_jobs)
        };
        let input_jobs: HashSet<_> = snapshot
            .candidates
            .iter()
            .map(|(event, _)| event.job_id)
            .chain(
                snapshot
                    .cameras
                    .iter()
                    .flat_map(|camera| camera.events.iter().map(|event| event.job_id)),
            )
            .collect();
        {
            let mut checked = HashMap::new();
            for (event, _) in snapshot
                .candidates
                .iter()
                .filter(|(event, _)| sanitized_jobs.contains(&event.job_id))
            {
                let privacy = event
                    .privacy
                    .as_ref()
                    .context("query_evidence_not_sanitized")?;
                if !checked.contains_key(&event.source_evidence_path) {
                    let verified =
                        crate::privacy_media::verify(&event.source_evidence_path).await?;
                    checked.insert(event.source_evidence_path.clone(), verified);
                }
                if checked.get(&event.source_evidence_path) != Some(&privacy.source)
                    || !event.source_session_matches_provenance()
                {
                    bail!("query_privacy_provenance_changed");
                }
            }
        }
        info!(%query_id, snapshot_elapsed_us = snapshot_started.elapsed().as_micros() as u64,
            indexed_count = snapshot.indexed_count, copied_events = snapshot.candidates.len(),
            copied_camera_histories = snapshot.cameras.len(), "memory query snapshot ready");
        let mut cameras = snapshot.cameras;
        let metadata_provenance =
            serde_json::to_value(self.apply_projected_graphs(&mut cameras).await?)?;
        // No job-store lock is held during model loading, indexing or inference.
        let retrieval_started = Instant::now();
        let mut retrieval = crate::retrieval::search(
            &selected_retrieval,
            &request,
            snapshot.candidates,
            snapshot.indexed_count,
            Some(query_id),
        )
        .await;
        info!(%query_id, elapsed_us = retrieval_started.elapsed().as_micros() as u64, "memory retrieval stage completed");
        if let Some(generation) = selected_retrieval.retrieval_deployment_generation {
            retrieval.provenance["deployment_generation"] = serde_json::json!(generation);
        }
        let mut matches = retrieval.matches;
        let considered = retrieval.considered;
        let retrieval_mode = retrieval.mode;
        let retrieval_notes = retrieval.notes;
        let mut summary = if considered == 0 {
            retrieval_notes
                .first()
                .cloned()
                .unwrap_or_else(|| "No indexed clips were available to search.".into())
        } else if matches.is_empty() {
            "No matching evidence was retrieved. Index a camera interval or broaden the query and filters.".to_owned()
        } else {
            format!(
                "Retrieved {} candidate event{} from {} indexed events. Candidates are ranked by relevance, not verified facts or confidence.",
                matches.len(),
                if matches.len() == 1 { "" } else { "s" },
                considered
            )
        };
        let mut model = None;
        let mut fallback_reason = None;
        let mut interpretations = Vec::new();
        let mut interpretation_notes = Vec::new();
        if request.temporal_enabled && !matches.is_empty() {
            let interpretation_started = Instant::now();
            let events: Vec<_> = matches
                .iter()
                .take(3)
                .map(|item| item.event.clone())
                .collect();
            match crate::interpretation::interpret_with_tracks(&selected_retrieval, &request.query, &events, &cameras, request.entity_id, Some(query_id)).await {
                Ok(results) => {
                    interpretation_notes.push("Experiment 2: model observations from ordered visual frames plus metadata; inspect citations. Retrieval ranking is unchanged.".into());
                    summary = results.iter().map(|result| format!("Clip {} ({}): {}",result.event_id,result.status,result.answer)).collect::<Vec<_>>().join("\n");
                    interpretations = results;
                }
                Err(error) => interpretation_notes.push(format!("Sequence interpretation unavailable; retained experiment 1 retrieval results: {error}")),
            }
            info!(%query_id, elapsed_us = interpretation_started.elapsed().as_micros() as u64, "memory interpretation stage completed");
        }
        if request.temporal_enabled && matches.is_empty() {
            interpretation_notes
                .push("Sequence interpretation skipped: no retrieved visual evidence.".into());
        }
        if request.vlm_enabled
            && !request.temporal_enabled
            && !request.long_memory_enabled
            && !matches.is_empty()
        {
            let synthesis_started = Instant::now();
            match self
                .memory
                .synthesize_query(&request.query, &matches, request.vlm_model.as_deref())
                .await
            {
                Ok((vlm_summary, used_model, relevant)) => {
                    summary = vlm_summary;
                    if retrieval_mode == "local_lexical_metadata" {
                        memory::reorder_by_vlm(&mut matches, &relevant);
                    }
                    model = Some(used_model);
                }
                Err(error) => fallback_reason = Some(error.to_string()),
            }
            info!(%query_id, elapsed_us = synthesis_started.elapsed().as_micros() as u64, "memory synthesis stage completed");
        }
        let history_started = Instant::now();
        let long_memory = request
            .long_memory_enabled
            .then(|| crate::long_memory::answer(&request, &matches, &cameras));
        if let Some(answer) = &long_memory {
            summary = answer.summary.clone();
            info!(%query_id, elapsed_us = history_started.elapsed().as_micros() as u64, "memory history stage completed");
        }
        info!(%query_id, mode = %retrieval_mode, considered, matches = matches.len(),
            elapsed_ms = started.elapsed().as_millis() as u64,
            temporal_requested = request.temporal_enabled, interpreted = interpretations.len(), interpretation_notes = ?interpretation_notes,
            notes = ?retrieval_notes, provenance = %retrieval.provenance, "memory retrieval completed");
        if self
            .memory_jobs
            .read()
            .await
            .values()
            .any(|job| input_jobs.contains(&job.id) && job.revocation.is_some())
        {
            bail!("recording_revoked");
        }
        Ok(MemoryQueryResponse {
            metadata_provenance,
            long_memory,
            interpretations,
            interpretation_notes,
            query_id,
            query: request.query,
            summary,
            matches,
            events_considered: considered,
            retrieval_mode,
            retrieval_notes,
            retrieval_provenance: retrieval.provenance,
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
    if request.capture_sessions && config.media_policy != "sanitized-v1" {
        bail!("source_sessions_require_sanitized_profile");
    }
    if request.idempotency_key.as_ref().is_some_and(|key| {
        key.len() < 16
            || key.len() > 128
            || !key
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "_-".contains(c))
    }) {
        bail!(
            "idempotency_key must contain 16 to 128 ASCII letters, digits, underscores or hyphens"
        );
    }
    let mut directory_names = HashSet::new();
    for camera in &request.cameras {
        if !directory_names.insert(safe_memory_component(&camera.camera_id)) {
            bail!("camera_directory_namespace_collision");
        }
    }
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
    if request.source_session_ids.len() > 256
        || request
            .source_session_ids
            .iter()
            .any(|id| id.is_empty() || id.len() > 128)
    {
        bail!(
            "source_session_ids must contain at most 256 nonempty identifiers of up to 128 bytes"
        );
    }
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

async fn worker_receipt(mut response: reqwest::Response) -> anyhow::Result<serde_json::Value> {
    let mut bytes = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| anyhow::anyhow!("projection_worker_unavailable: incomplete receipt"))?
    {
        anyhow::ensure!(
            bytes.len().saturating_add(chunk.len()) <= 1024 * 1024,
            "projection_receipt_too_large"
        );
        bytes.extend_from_slice(&chunk);
    }
    serde_json::from_slice(&bytes).map_err(|error| {
        if error.is_eof() {
            anyhow::anyhow!("projection_worker_unavailable: incomplete receipt")
        } else {
            anyhow::anyhow!("projection_invalid_receipt")
        }
    })
}

async fn persist_memory_job(config: &Config, job: &MemoryJobRecord) -> anyhow::Result<()> {
    let path = config
        .data_dir
        .join("memory/manifests")
        .join(format!("{}.json", job.id));
    let bytes = serde_json::to_vec_pretty(&redact_memory_job(job.clone()))?;
    crate::durable::publish(path, bytes).await
}

fn legacy_projection_needs_binding(
    intent: &crate::domain::VisualProjection,
    selected: &Config,
) -> bool {
    intent.deployment_generation.is_none()
        && selected.retrieval_deployment_generation.is_some()
        && matches!(intent.state.as_str(), "pending" | "submitted" | "blocked")
        && (intent.attempts > 0
            || intent.task_id.is_some()
            || intent.receipt.is_some()
            || intent.worker_fingerprint.is_some())
}

fn load_memory_jobs(config: &Config) -> anyhow::Result<HashMap<Uuid, MemoryJobRecord>> {
    let mut jobs = HashMap::new();
    for (path, mut job) in crate::storage_contract::manifests(&config.data_dir)? {
        if job.revocation.is_none()
            && config.media_policy == "sanitized-v1"
            && job.result.as_ref().is_some_and(|r| {
                r.camera_results
                    .iter()
                    .any(|c| c.events.iter().any(|e| e.privacy.is_none()))
            })
        {
            tracing::warn!("historical unsanitized recording excluded from sanitized profile");
            continue;
        }
        if matches!(job.status, JobStatus::Queued | JobStatus::Running) {
            job.status = JobStatus::Failed;
            job.completed_at_ms = Some(now_ms());
            job.error=Some("recording_interrupted_by_restart: resume retained evidence only; live time cannot be recaptured".into());
            let bytes = serde_json::to_vec_pretty(&redact_memory_job(job.clone()))?;
            crate::durable::atomic_write(&path, &bytes)?;
        }
        rehydrate_memory_paths(config, &mut job);
        jobs.insert(job.id, job);
    }
    Ok(jobs)
}

fn rehydrate_memory_paths(config: &Config, job: &mut MemoryJobRecord) {
    let Some(result) = &mut job.result else {
        return;
    };
    for camera in &mut result.camera_results {
        rehydrate_camera_paths(config, job.id, camera);
    }
}

fn validate_checkpoint_set(
    id: Uuid,
    camera_id: &str,
    cameras: &[crate::domain::MemoryCameraResult],
) -> anyhow::Result<()> {
    anyhow::ensure!(
        !cameras.is_empty() && cameras.len() <= 256,
        "camera_checkpoint_session_limit"
    );
    let mut sessions = HashSet::new();
    let mut storage_sessions = HashSet::new();
    let mut events = HashSet::new();
    for camera in cameras {
        anyhow::ensure!(
            camera.camera.camera_id == camera_id
                && camera.storage_session_matches_provenance()
                && camera
                    .storage_session_id
                    .is_none_or(|id| storage_sessions.insert(id))
                && (cameras.len() == 1 || camera.storage_session_id.is_some())
                && sessions.insert(camera.source_session_id.clone()),
            "camera_checkpoint_scope_mismatch"
        );
        for event in &camera.events {
            anyhow::ensure!(
                event.job_id == id
                    && event.camera_id == camera_id
                    && event.capture_extent == camera.capture_extent
                    && event.source_session_id == camera.source_session_id
                    && event.source_session_matches_provenance()
                    && events.insert(event.event_id),
                "camera_checkpoint_event_scope_mismatch"
            );
        }
    }
    Ok(())
}

fn decode_camera_checkpoint(
    bytes: &[u8],
    id: Uuid,
    camera_id: &str,
) -> anyhow::Result<Vec<crate::domain::MemoryCameraResult>> {
    #[derive(serde::Deserialize)]
    #[serde(untagged)]
    enum Checkpoint {
        One(crate::domain::MemoryCameraResult),
        Sessions(Vec<crate::domain::MemoryCameraResult>),
    }
    let cameras = match serde_json::from_slice(bytes)? {
        Checkpoint::One(camera) => vec![camera],
        Checkpoint::Sessions(cameras) => cameras,
    };
    validate_checkpoint_set(id, camera_id, &cameras)?;
    Ok(cameras)
}

fn rehydrate_camera_paths(
    config: &Config,
    id: Uuid,
    camera: &mut crate::domain::MemoryCameraResult,
) {
    let directory = config
        .data_dir
        .join("memory")
        .join(id.to_string())
        .join(safe_memory_component(&camera.camera.camera_id));
    let directory = camera.evidence_directory(&directory);
    for event in &mut camera.events {
        event.source_duration_ms = camera.duration_ms;
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

#[cfg(test)]
mod query_snapshot_tests {
    use super::*;
    use crate::domain::MemoryCameraResult;
    use serde_json::json;

    #[test]
    fn saved_sanitized_policy_survives_a_research_mode_switch() {
        let id = Uuid::from_u128(799);
        let camera = crate::long_memory::tests::fixture(id, "junction");
        let mut job: MemoryJobRecord = serde_json::from_value(json!({
            "id":id,"request":{"name":"policy","cameras":[camera.camera.clone()]},
            "status":"completed","created_at_ms":1,"started_at_ms":1,"completed_at_ms":2,
            "result":{"cluster_id":null,"cameras_requested":1,"cameras_processed":1,"camera_failures":[],
                "events_indexed":3,"source_duration_ms":22000,"observer_frames_decoded":1,
                "camera_results":[camera],"algorithm_version":"fixture"},"error":null
        })).unwrap();
        assert_eq!(memory_policy(&job, "research-raw-v1"), "research-raw-v1");
        assert_eq!(memory_policy(&job, "sanitized-v1"), "sanitized-v1");
        job.result.as_mut().unwrap().camera_results[0].events[0].privacy = Some(
            serde_json::from_value(json!({
                "source":{"schema":"visn-sanitized-media-v1","policy":"sanitized-pixels-v1",
                    "qualification":"fixture","media_sha256":"fixture","media_bytes":1,"frames":1,
                    "width":1,"height":1,"ledger_sha256":"fixture","audio":"removed"},
                "thumbnail_sha256":"fixture","clip_sha256":"fixture"
            }))
            .unwrap(),
        );
        assert_eq!(memory_policy(&job, "research-raw-v1"), "sanitized-v1");
        job.result = None;
        let components = std::collections::BTreeMap::from([("model".into(), "fixture".into())]);
        let version = crate::execution_profile::VERSION;
        job.execution_profile = Some(crate::execution_profile::ExecutionProfile {
            version: version.into(),
            fingerprint: crate::metadata_projection::digest(
                &serde_json::to_vec(&(version, &components)).unwrap(),
            ),
            components,
        });
        assert_eq!(memory_policy(&job, "research-raw-v1"), "sanitized-v1");
    }

    #[tokio::test]
    async fn session_checkpoint_sets_are_scoped_durable_and_legacy_readable() {
        let job = Uuid::from_u128(800);
        let mut legacy = crate::long_memory::tests::fixture(job, "junction");
        legacy.events.clear();
        legacy.long_memory = None;
        let bytes = serde_json::to_vec(&legacy).unwrap();
        assert_eq!(
            decode_camera_checkpoint(&bytes, job, "junction")
                .unwrap()
                .len(),
            1
        );
        let root =
            std::env::temp_dir().join(format!("visn-session-checkpoints-{}", Uuid::now_v7()));
        let mut members = Vec::new();
        for id in [Uuid::from_u128(1), Uuid::from_u128(2)] {
            let mut camera = legacy.clone();
            camera.storage_session_id = Some(id);
            camera.source_session_id = Some(id.simple().to_string());
            let directory = camera.evidence_directory(&root);
            tokio::fs::create_dir_all(&directory).await.unwrap();
            crate::durable::publish(directory.join("source.mkv"), id.as_bytes().to_vec())
                .await
                .unwrap();
            members.push(camera);
        }
        assert_ne!(
            members[0].evidence_directory(&root),
            members[1].evidence_directory(&root)
        );
        let bytes = serde_json::to_vec(&members).unwrap();
        let expected = Sha256::digest(&bytes);
        crate::durable::publish(root.join("camera-result.json"), bytes)
            .await
            .unwrap();
        let saved = tokio::fs::read(root.join("camera-result.json"))
            .await
            .unwrap();
        assert_eq!(Sha256::digest(&saved), expected);
        let restored = decode_camera_checkpoint(&saved, job, "junction").unwrap();
        for (index, camera) in restored.iter().enumerate() {
            assert_eq!(
                tokio::fs::read(camera.evidence_directory(&root).join("source.mkv"))
                    .await
                    .unwrap(),
                members[index].storage_session_id.unwrap().as_bytes()
            );
        }
        let mut wrong = restored.clone();
        wrong[1].storage_session_id = wrong[0].storage_session_id;
        wrong[1].source_session_id = Some(wrong[0].storage_session_id.unwrap().to_string());
        assert!(validate_checkpoint_set(job, "junction", &wrong).is_err());
        wrong = restored.clone();
        wrong[1].source_session_id = wrong[0].source_session_id.clone();
        assert!(validate_checkpoint_set(job, "junction", &wrong).is_err());
        assert!(validate_checkpoint_set(job, "other-camera", &restored).is_err());
        wrong = restored;
        wrong[1].storage_session_id = None;
        assert!(validate_checkpoint_set(job, "junction", &wrong).is_err());
        assert!(decode_camera_checkpoint(b"[]", job, "junction").is_err());
        tokio::fs::remove_dir_all(root).await.unwrap();
    }

    fn reference(
        request: &MemoryQueryRequest,
        cameras: &[MemoryCameraResult],
    ) -> MemoryQuerySnapshot {
        let cameras = cameras.to_vec();
        let candidates: Vec<_> = cameras
            .iter()
            .filter(|camera| {
                !request.temporal_enabled
                    || request.entity_id.is_none_or(|id| {
                        camera.long_memory.as_ref().is_some_and(|graph| {
                            graph.version == crate::long_memory::VERSION
                                && graph.entities.iter().any(|e| {
                                    e.entity_id == id
                                        && e.camera_id == camera.camera.camera_id
                                        && camera
                                            .events
                                            .iter()
                                            .any(|event| event.job_id == e.job_id)
                                })
                        })
                    })
            })
            .flat_map(|c| c.events.iter().map(|e| (e.clone(), c.camera.clone())))
            .collect();
        MemoryQuerySnapshot {
            indexed_count: candidates.len(),
            candidates,
            cameras,
        }
    }

    #[tokio::test]
    async fn filtered_snapshot_preserves_retrieval_graphs_and_scope_diagnostics() {
        let a = crate::long_memory::tests::fixture(Uuid::from_u128(100), "a");
        let b = crate::long_memory::tests::fixture(Uuid::from_u128(101), "b");
        let mut missing = b.clone();
        missing.long_memory = None;
        let mut stale = b.clone();
        stale.long_memory.as_mut().unwrap().version = "old".into();
        let cameras = vec![a.clone(), b, missing, stale];
        let entity = a.long_memory.as_ref().unwrap().entities[0].entity_id;
        let mut cases = vec![
            json!({}),
            json!({"job_id":a.events[0].job_id}),
            json!({"camera_ids":["missing"]}),
            json!({"cluster_id":"missing"}),
            json!({"start_ms":4000,"end_ms":10000}),
            json!({"job_id":Uuid::nil()}),
            json!({"temporal_enabled":true,"entity_id":entity}),
            json!({"temporal_enabled":true,"entity_id":Uuid::nil()}),
            json!({"entity_id":entity}),
        ];
        for fields in &mut cases {
            fields["query"] = json!("trucks");
            fields["semantic_enabled"] = json!(false);
        }
        for long in [false, true] {
            for fields in &cases {
                let mut request: MemoryQueryRequest =
                    serde_json::from_value(fields.clone()).unwrap();
                request.long_memory_enabled = long;
                let before = reference(&request, &cameras);
                let after = memory_query_snapshot(&request, cameras.iter());
                assert_eq!(before.indexed_count, after.indexed_count);
                assert_eq!(
                    serde_json::to_value(
                        before
                            .candidates
                            .iter()
                            .filter(|(e, _)| crate::retrieval::eligible(&request, e))
                            .collect::<Vec<_>>()
                    )
                    .unwrap(),
                    serde_json::to_value(&after.candidates).unwrap()
                );
                if long {
                    assert_eq!(
                        serde_json::to_value(crate::long_memory::answer(
                            &request,
                            &[],
                            &before.cameras
                        ))
                        .unwrap(),
                        serde_json::to_value(crate::long_memory::answer(
                            &request,
                            &[],
                            &after.cameras
                        ))
                        .unwrap()
                    );
                }
                if !long && !(request.temporal_enabled && request.entity_id.is_some()) {
                    assert!(after.cameras.is_empty());
                }
                let config = Config::from_env().unwrap();
                let x = crate::retrieval::search(
                    &config,
                    &request,
                    before.candidates,
                    before.indexed_count,
                    None,
                )
                .await;
                let y = crate::retrieval::search(
                    &config,
                    &request,
                    after.candidates,
                    after.indexed_count,
                    None,
                )
                .await;
                assert_eq!(
                    json!([x.matches, x.considered, x.mode, x.notes, x.provenance]),
                    json!([y.matches, y.considered, y.mode, y.notes, y.provenance])
                );
            }
        }
    }

    #[test]
    #[ignore = "Controlled snapshot timing/RSS; run explicitly with --ignored --nocapture"]
    fn benchmark_filtered_snapshot() {
        let base = crate::long_memory::tests::fixture(Uuid::from_u128(200), "benchmark");
        let mut cameras = Vec::new();
        for i in 0..512u128 {
            let mut camera = base.clone();
            for event in &mut camera.events {
                event.job_id = Uuid::from_u128(1000 + i);
                event.visual_signature = vec![0.125; 4096];
            }
            cameras.push(camera);
        }
        let request: MemoryQueryRequest = serde_json::from_value(
            json!({"query":"trucks","semantic_enabled":false,"job_id":Uuid::from_u128(1000)}),
        )
        .unwrap();
        let mode = std::env::var("VISN_SNAPSHOT_BENCH_MODE").unwrap_or_else(|_| "filtered".into());
        assert!(matches!(mode.as_str(), "reference" | "filtered"));
        let mut times = Vec::new();
        let mut counts = (0, 0);
        for _ in 0..10 {
            let start = Instant::now();
            let snapshot = if mode == "reference" {
                reference(&request, &cameras)
            } else {
                memory_query_snapshot(&request, cameras.iter())
            };
            times.push(start.elapsed().as_secs_f64());
            counts = (snapshot.candidates.len(), snapshot.cameras.len());
            std::hint::black_box(&snapshot);
        }
        println!(
            "SNAPSHOT_BENCHMARK {}",
            json!({"mode":mode,"recordings":512,"indexed_events":1536,"copied_candidates":counts.0,"copied_cameras":counts.1,"seconds":times,"scope":"snapshot component in current build profile; measure whole-process RSS separately"})
        );
    }
}
