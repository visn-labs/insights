use std::{
    cmp::Ordering,
    collections::{BTreeSet, HashMap, HashSet, VecDeque},
    path::{Path, PathBuf},
    process::Stdio,
    sync::Arc,
};

use anyhow::{bail, Context};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use serde::Deserialize;
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::Command,
    sync::Semaphore,
};
use tracing::{info, warn};
use uuid::Uuid;

use crate::{
    config::Config,
    detector_worker::DetectorWorker,
    domain::{
        CameraProfile, MemoryCameraResult, MemoryEvent, MemoryEventDescription, MemoryQueryMatch,
        MemoryQueryRequest, RepresentativeFrame,
    },
    gemma::GemmaClient,
};

const OUTPUT_PREFIX: &str = "VISN_MEMORY_JSON:";
const MAX_RUNNER_STDOUT_BYTES: usize = 2 * 1024 * 1024;
const MAX_RUNNER_STDERR_BYTES: usize = 128 * 1024;

#[derive(Clone)]
pub struct MemoryService {
    config: Arc<Config>,
    gemma: GemmaClient,
    observer_gate: Arc<Semaphore>,
    detector_worker: DetectorWorker,
}

#[derive(Debug, Deserialize)]
struct RunnerOutput {
    #[serde(default)]
    capture_extent: Option<crate::domain::CaptureExtent>,
    #[serde(default)]
    storage_session_id: Option<Uuid>,
    evidence_file: String,
    duration_ms: u64,
    frames_decoded: usize,
    events: Vec<RunnerEvent>,
    #[serde(default)]
    data_quality_notes: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct RunnerEvent {
    start_ms: u64,
    end_ms: u64,
    activity_mean: f32,
    activity_peak: f32,
    quality: f32,
    boundary_reason: String,
    thumbnail_file: String,
    clip_file: String,
    representative_frame: RunnerRepresentativeFrame,
    #[serde(default)]
    visual_signature: Vec<f32>,
}

#[derive(Debug, Deserialize)]
struct RunnerRepresentativeFrame {
    media_type: String,
    #[serde(default)]
    data_base64: String,
    frame_time_ms: u64,
    width: u32,
    height: u32,
}

impl MemoryService {
    pub fn new(
        config: Arc<Config>,
        gemma: GemmaClient,
        observer_gate: Arc<Semaphore>,
        detector_worker: DetectorWorker,
    ) -> Self {
        Self {
            observer_gate,
            detector_worker,
            config,
            gemma,
        }
    }

    pub async fn process_camera(
        &self,
        job_id: Uuid,
        cluster_id: Option<&str>,
        mut camera: CameraProfile,
        monitor_duration_secs: u64,
        observer_fps: f32,
        vlm_enabled: bool,
        temporal_enabled: bool,
        requested_vlm_model: Option<&str>,
        retained_only: bool,
        capture_sessions: bool,
    ) -> anyhow::Result<Vec<MemoryCameraResult>> {
        if capture_sessions && self.config.media_policy != "sanitized-v1" {
            bail!("source_sessions_require_sanitized_profile");
        }
        if camera.camera_id.trim().is_empty() {
            let generated = Uuid::new_v5(&Uuid::NAMESPACE_URL, camera.live_url.as_bytes());
            camera.camera_id = format!("camera-{}", &generated.simple().to_string()[..12]);
        }
        let directory = self
            .config
            .data_dir
            .join("memory")
            .join(job_id.to_string())
            .join(safe_component(&camera.camera_id));
        tokio::fs::create_dir_all(&directory)
            .await
            .context("create camera memory directory")?;

        info!(%job_id, camera_id = %camera.camera_id, monitor_duration_secs, observer_fps,
            "memory recording and indexing started");
        let output = self
            .run_observer(
                job_id,
                &camera.live_url,
                &directory,
                monitor_duration_secs,
                observer_fps,
                retained_only,
                capture_sessions,
            )
            .await?;
        let mut results = Vec::new();
        let mut sessions = HashSet::new();
        for output in output {
            anyhow::ensure!(
                capture_sessions == output.storage_session_id.is_some()
                    && capture_sessions == output.capture_extent.is_some()
                    && sessions.insert(output.storage_session_id),
                "memory_runner_session_scope_invalid"
            );
            let scoped_directory = match output.storage_session_id {
                Some(id) => {
                    let parent = directory.join("sessions");
                    let path = parent.join(id.to_string());
                    anyhow::ensure!(
                        !tokio::fs::symlink_metadata(&parent)
                            .await?
                            .file_type()
                            .is_symlink()
                            && !tokio::fs::symlink_metadata(&path)
                                .await?
                                .file_type()
                                .is_symlink(),
                        "memory_session_directory_symlink"
                    );
                    path
                }
                None => directory.clone(),
            };
            results.push(
                self.materialize_camera_output(
                    job_id,
                    cluster_id,
                    camera.clone(),
                    output,
                    &scoped_directory,
                    vlm_enabled,
                    temporal_enabled,
                    requested_vlm_model,
                )
                .await?,
            );
        }
        Ok(results)
    }

    async fn materialize_camera_output(
        &self,
        job_id: Uuid,
        cluster_id: Option<&str>,
        camera: CameraProfile,
        output: RunnerOutput,
        directory: &Path,
        vlm_enabled: bool,
        temporal_enabled: bool,
        requested_vlm_model: Option<&str>,
    ) -> anyhow::Result<MemoryCameraResult> {
        let source_evidence_path = artifact_path(&directory, &output.evidence_file)?;
        ensure_file(&source_evidence_path).await?;
        let privacy_source = if self.config.media_policy == "sanitized-v1" {
            Some(crate::privacy_media::verify(&source_evidence_path).await?)
        } else {
            None
        };

        let mut selected = BTreeSet::new();
        let source_session_id = privacy_source
            .as_ref()
            .and_then(|p| p.capture.as_ref())
            .map(|c| c.session_id.clone());
        anyhow::ensure!(
            output.storage_session_id.is_none_or(|id| source_session_id
                .as_deref()
                .and_then(|s| Uuid::parse_str(s).ok())
                == Some(id)),
            "memory_session_provenance_changed"
        );
        if vlm_enabled || temporal_enabled {
            selected = select_vlm_events(&output.events, self.config.max_vlm_events_per_camera);
        }

        let mut events = Vec::with_capacity(output.events.len());
        for (index, event) in output.events.into_iter().enumerate() {
            let event_id = crate::domain::memory_event_id(
                job_id,
                &camera.camera_id,
                source_session_id.as_deref(),
                event.start_ms,
                event.end_ms,
            );
            let runner_thumbnail_path = artifact_path(&directory, &event.thumbnail_file)?;
            ensure_file(&runner_thumbnail_path).await?;
            let thumbnail_path = directory.join(format!("{event_id}.jpg"));
            tokio::fs::rename(&runner_thumbnail_path, &thumbnail_path)
                .await
                .context("finalize event thumbnail")?;
            let clip_path = if event.clip_file.is_empty() {
                source_evidence_path.clone()
            } else {
                let runner_path = artifact_path(&directory, &event.clip_file)?;
                if runner_path.is_file() {
                    let finalized = directory.join(format!("{event_id}.mp4"));
                    tokio::fs::rename(&runner_path, &finalized)
                        .await
                        .context("finalize event clip")?;
                    finalized
                } else {
                    source_evidence_path.clone()
                }
            };
            let description = if selected.contains(&index) && !temporal_enabled {
                match load_representative_frame(&event.representative_frame, &thumbnail_path).await
                {
                    Ok(frame) => match self
                        .gemma
                        .describe_memory_event(
                            &frame,
                            &camera,
                            event.activity_mean,
                            event.activity_peak,
                            requested_vlm_model,
                        )
                        .await
                    {
                        Ok((description, _)) => description,
                        Err(error) => {
                            warn!(%job_id, %event_id, error = %error, "VLM event description failed");
                            fallback_description(
                                &camera,
                                event.activity_peak,
                                Some(error.to_string()),
                            )
                        }
                    },
                    Err(error) => {
                        warn!(%job_id, %event_id, error = %error, "could not load selected VLM thumbnail");
                        fallback_description(&camera, event.activity_peak, Some(error.to_string()))
                    }
                }
            } else {
                fallback_description(
                    &camera,
                    event.activity_peak,
                    if vlm_enabled || temporal_enabled {
                        Some(format!(
                            "VLM enrichment was limited to the {} highest-priority events for this camera",
                            self.config.max_vlm_events_per_camera
                        ))
                    } else {
                        Some("VLM event enrichment was disabled".to_owned())
                    },
                )
            };
            events.push(MemoryEvent {
                capture_extent: output.capture_extent,
                source_session_id: source_session_id.clone(),
                privacy: if let Some(source) = &privacy_source {
                    Some(crate::privacy_media::SanitizedEventEvidence {
                        source: source.clone(),
                        thumbnail_sha256: crate::privacy_media::file_hash(&thumbnail_path).await?,
                        clip_sha256: crate::privacy_media::file_hash(&clip_path).await?,
                    })
                } else {
                    None
                },
                source_duration_ms: output.duration_ms,
                interpretation: None,
                event_id,
                job_id,
                camera_id: camera.camera_id.clone(),
                cluster_id: cluster_id.map(ToOwned::to_owned),
                start_ms: event.start_ms,
                end_ms: event.end_ms,
                duration_ms: event.end_ms.saturating_sub(event.start_ms),
                activity_mean: event.activity_mean,
                activity_peak: event.activity_peak,
                quality: event.quality,
                boundary_reason: event.boundary_reason,
                thumbnail_url: format!("/api/v1/memory-events/{event_id}/thumbnail"),
                evidence_url: format!("/api/v1/memory-events/{event_id}/clip"),
                description,
                visual_signature: event.visual_signature,
                thumbnail_path,
                clip_path,
                source_evidence_path: source_evidence_path.clone(),
            });
            if temporal_enabled && selected.contains(&index) {
                let event = events.last_mut().expect("event just inserted");
                match crate::interpretation::interpret(&self.config,
                    "Describe the visible state changes from the beginning to the end. Which actions, if any, are supported by the sequence?",
                    std::slice::from_ref(event)).await {
                    Ok(mut results) => {
                        let result = results.remove(0);
                        event.description.summary = format!("Sequence interpretation ({}): {}", result.status, result.answer);
                        event.description.scene_type = "timestamped sequence interpretation".into();
                        event.description.generated_by_model = true;
                        event.description.confidence = 0.0;
                        event.description.model = result.provenance["model"].as_str().map(str::to_owned);
                        event.description.fallback_reason = None;
                        event.interpretation = Some(result);
                    }
                    Err(error) => event.description.fallback_reason = Some(format!("Sequence interpretation unavailable: {error}")),
                }
            }
        }

        info!(%job_id, camera_id = %camera.camera_id, events = events.len(), duration_ms = output.duration_ms,
            frames_consumed = output.frames_decoded, "memory evidence indexed");
        Ok(MemoryCameraResult {
            capture_extent: output.capture_extent,
            storage_session_id: output.storage_session_id,
            source_session_id,
            long_memory: None,
            camera,
            duration_ms: output.duration_ms,
            frames_decoded: output.frames_decoded,
            evidence_url: events
                .first()
                .map(|event| format!("/api/v1/memory-events/{}/source", event.event_id))
                .unwrap_or_default(),
            events,
            data_quality_notes: output.data_quality_notes,
        })
    }

    async fn run_observer(
        &self,
        job_id: Uuid,
        source: &str,
        directory: &Path,
        monitor_duration_secs: u64,
        observer_fps: f32,
        retained_only: bool,
        capture_sessions: bool,
    ) -> anyhow::Result<Vec<RunnerOutput>> {
        if retained_only && !capture_sessions {
            if self.config.media_policy != "sanitized-v1" {
                bail!("retained_recovery_requires_sanitized_profile");
            }
            crate::privacy_media::verify(&directory.join("source.mkv")).await?;
        }
        let waiting = std::time::Instant::now();
        let _permit = self
            .observer_gate
            .acquire()
            .await
            .context("acquire global sparse-observer gate")?;
        info!(%job_id, elapsed_us = waiting.elapsed().as_micros() as u64, "memory observer admission completed");
        if let Err(error) = self.detector_worker.shutdown_if_idle().await {
            warn!(%error, "could not release an idle detector before sparse observation");
        }
        let executable = if self.config.media_policy == "sanitized-v1" {
            &self.config.privacy_python
        } else {
            &self.config.detector_executable
        };
        let mut command = Command::new(executable);
        command
            .env("VISN_TRACE_ID", job_id.to_string())
            .env("VISN_MEDIA_POLICY", &self.config.media_policy)
            .env("VISN_PRIVACY_MODEL_DIR", &self.config.privacy_model_dir)
            .args(&self.config.memory_runner_args)
            .arg("--source")
            .arg("-")
            .arg("--output-dir")
            .arg(directory)
            .arg("--observer-fps")
            .arg(observer_fps.to_string())
            .arg("--max-seconds")
            .arg(monitor_duration_secs.to_string())
            .arg("--max-events")
            .arg(self.config.max_memory_events_per_camera.to_string())
            .arg("--clip-mode")
            .arg(&self.config.memory_clip_mode)
            .arg("--threads")
            .arg("1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if retained_only {
            command.arg("--resume-retained");
        }
        if capture_sessions {
            command.arg("--capture-sessions");
        }
        let lifeline = crate::durable::isolate_worker(&mut command)?;
        let mut child = command
            .spawn()
            .with_context(|| format!("launch {} memory runner", self.config.detector_executable))?;
        let mut group = crate::durable::WorkerGroup::new(child.id(), lifeline);
        let mut stdin = child.stdin.take().context("open memory runner stdin")?;
        stdin
            .write_all(source.as_bytes())
            .await
            .context("write source to memory runner")?;
        drop(stdin);
        let stdout = child.stdout.take().context("open memory runner stdout")?;
        let stderr = child.stderr.take().context("open memory runner stderr")?;
        let stdout_task = tokio::spawn(capture_bounded_head(stdout, MAX_RUNNER_STDOUT_BYTES));
        let stderr_task = tokio::spawn(capture_bounded_tail(stderr, MAX_RUNNER_STDERR_BYTES));
        let status = child.wait().await.context("wait for memory runner")?;
        group.finish();
        let stdout = stdout_task
            .await
            .context("join memory runner stdout task")??;
        let stderr = stderr_task
            .await
            .context("join memory runner stderr task")??;
        if !status.success() {
            let stderr = stderr
                .render()
                .replace(source, redacted_source_label(source));
            bail!(
                "event-memory runner failed with {}: {}",
                status,
                stderr.trim()
            );
        }
        for line in stderr.render().lines() {
            if let Some((_, payload)) = line.split_once("WORKFLOW_TRACE ") {
                if let Ok(telemetry) = serde_json::from_str::<serde_json::Value>(payload) {
                    info!(%job_id, %telemetry, "memory observer telemetry");
                }
            }
        }
        if stdout.truncated {
            bail!(
                "event-memory runner output exceeded the {} byte limit",
                MAX_RUNNER_STDOUT_BYTES
            );
        }
        decode_runner_output(&stdout.bytes)
    }

    pub async fn synthesize_query(
        &self,
        query: &str,
        matches: &[MemoryQueryMatch],
        requested_vlm_model: Option<&str>,
    ) -> anyhow::Result<(String, String, Vec<Uuid>)> {
        let (result, model) = self
            .gemma
            .synthesize_memory_query(query, matches, requested_vlm_model)
            .await?;
        Ok((result.summary, model, result.relevant_event_ids))
    }
}

pub fn retrieve<'a>(
    request: &MemoryQueryRequest,
    candidates: Vec<(&'a MemoryEvent, &'a CameraProfile)>,
) -> (Vec<MemoryQueryMatch>, usize) {
    let considered = candidates.len();
    let query_terms = expanded_terms(&tokenize(&request.query));
    let mut document_frequency: HashMap<String, usize> = HashMap::new();
    let prepared: Vec<_> = candidates
        .into_iter()
        .filter(|(event, _)| crate::retrieval::eligible(request, event))
        .map(|(event, camera)| {
            let terms = document_terms(event, camera);
            for term in &terms {
                *document_frequency.entry(term.clone()).or_default() += 1;
            }
            (event, terms)
        })
        .collect();
    let document_count = prepared.len().max(1) as f32;
    let mut matches: Vec<_> = prepared
        .into_iter()
        .map(|(event, document_terms)| {
            let matched_terms: Vec<_> = query_terms
                .iter()
                .filter(|term| document_terms.contains(*term))
                .cloned()
                .collect();
            let lexical = matched_terms.iter().fold(0.0, |score, term| {
                let frequency = *document_frequency.get(term).unwrap_or(&1) as f32;
                score + ((document_count + 1.0) / (frequency + 1.0)).ln() + 1.0
            });
            let normalization = query_terms.len().max(1) as f32;
            let metadata_prior = if event.description.generated_by_model {
                0.04
            } else {
                0.0
            };
            let score = lexical / normalization
                + metadata_prior
                + 0.03 * event.activity_peak
                + 0.02 * event.quality;
            MemoryQueryMatch {
                rank: 0,
                score,
                matched_terms,
                event: event.clone(),
            }
        })
        .filter(|candidate| !candidate.matched_terms.is_empty())
        .collect();
    matches.sort_by(|left, right| {
        right
            .score
            .partial_cmp(&left.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| left.event.start_ms.cmp(&right.event.start_ms))
    });
    matches.truncate(request.limit.clamp(1, 50));
    for (index, candidate) in matches.iter_mut().enumerate() {
        candidate.rank = index + 1;
    }
    (matches, considered)
}

pub fn reorder_by_vlm(matches: &mut [MemoryQueryMatch], relevant: &[Uuid]) {
    let positions: HashMap<_, _> = relevant
        .iter()
        .enumerate()
        .map(|(index, event_id)| (*event_id, index))
        .collect();
    matches.sort_by(|left, right| {
        positions
            .get(&left.event.event_id)
            .copied()
            .unwrap_or(usize::MAX)
            .cmp(
                &positions
                    .get(&right.event.event_id)
                    .copied()
                    .unwrap_or(usize::MAX),
            )
            .then_with(|| {
                right
                    .score
                    .partial_cmp(&left.score)
                    .unwrap_or(Ordering::Equal)
            })
    });
    for (index, candidate) in matches.iter_mut().enumerate() {
        candidate.rank = index + 1;
    }
}

fn select_vlm_events(events: &[RunnerEvent], limit: usize) -> BTreeSet<usize> {
    let target = limit.min(events.len());
    let mut selected = BTreeSet::new();
    if target == 0 {
        return selected;
    }

    // Reserve one call for the clearest scene anchor, then one for the strongest
    // activity peak. Remaining calls favor both salience and visual diversity so
    // a static camera does not send near-duplicate frames to the VLM.
    let scene_anchor = events
        .iter()
        .enumerate()
        .max_by(|left, right| {
            left.1
                .quality
                .partial_cmp(&right.1.quality)
                .unwrap_or(Ordering::Equal)
        })
        .map(|(index, _)| index)
        .unwrap_or(0);
    selected.insert(scene_anchor);

    if selected.len() < target {
        if let Some((index, _)) = events
            .iter()
            .enumerate()
            .filter(|(index, _)| !selected.contains(index))
            .max_by(|left, right| {
                let left_score = left.1.activity_peak + 0.15 * left.1.quality;
                let right_score = right.1.activity_peak + 0.15 * right.1.quality;
                left_score
                    .partial_cmp(&right_score)
                    .unwrap_or(Ordering::Equal)
            })
        {
            selected.insert(index);
        }
    }

    while selected.len() < target {
        let next = events
            .iter()
            .enumerate()
            .filter(|(index, _)| !selected.contains(index))
            .map(|(index, event)| {
                let novelty = selected
                    .iter()
                    .map(|selected_index| {
                        signature_distance(
                            &event.visual_signature,
                            &events[*selected_index].visual_signature,
                        )
                    })
                    .fold(1.0_f32, f32::min);
                let score = event.activity_peak + 0.15 * event.quality + 0.25 * novelty;
                (index, score)
            })
            .max_by(|left, right| left.1.partial_cmp(&right.1).unwrap_or(Ordering::Equal))
            .map(|(index, _)| index);
        let Some(index) = next else { break };
        selected.insert(index);
    }
    selected
}

fn signature_distance(left: &[f32], right: &[f32]) -> f32 {
    if left.is_empty() || left.len() != right.len() {
        return 0.5;
    }
    let similarity: f32 = left
        .iter()
        .zip(right)
        .map(|(left, right)| left * right)
        .sum();
    (1.0 - similarity).clamp(0.0, 1.0)
}

struct CapturedHead {
    bytes: Vec<u8>,
    truncated: bool,
}

struct CapturedTail {
    bytes: Vec<u8>,
    truncated: bool,
}

impl CapturedTail {
    fn render(&self) -> String {
        let body = String::from_utf8_lossy(&self.bytes);
        if self.truncated {
            format!("[earlier runner diagnostics truncated]\n{body}")
        } else {
            body.into_owned()
        }
    }
}

async fn capture_bounded_head<R>(mut reader: R, limit: usize) -> std::io::Result<CapturedHead>
where
    R: AsyncRead + Unpin,
{
    let mut bytes = Vec::with_capacity(limit.min(64 * 1024));
    let mut truncated = false;
    let mut buffer = [0_u8; 8 * 1024];
    loop {
        let count = reader.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        let remaining = limit.saturating_sub(bytes.len());
        bytes.extend_from_slice(&buffer[..count.min(remaining)]);
        if count > remaining {
            truncated = true;
        }
    }
    Ok(CapturedHead { bytes, truncated })
}

async fn capture_bounded_tail<R>(mut reader: R, limit: usize) -> std::io::Result<CapturedTail>
where
    R: AsyncRead + Unpin,
{
    let mut bytes = VecDeque::with_capacity(limit);
    let mut truncated = false;
    let mut buffer = [0_u8; 8 * 1024];
    loop {
        let count = reader.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        bytes.extend(&buffer[..count]);
        if bytes.len() > limit {
            let excess = bytes.len() - limit;
            bytes.drain(..excess);
            truncated = true;
        }
    }
    Ok(CapturedTail {
        bytes: bytes.into_iter().collect(),
        truncated,
    })
}

fn redacted_source_label(source: &str) -> &'static str {
    if source.starts_with("https://") {
        "https://***"
    } else if source.starts_with("http://") {
        "http://***"
    } else if source.starts_with("rtsps://") {
        "rtsps://***"
    } else if source.starts_with("rtsp://") {
        "rtsp://***"
    } else {
        "<local-video>"
    }
}

fn decode_runner_output(stdout: &[u8]) -> anyhow::Result<Vec<RunnerOutput>> {
    let text = String::from_utf8_lossy(stdout);
    let payload = text
        .lines()
        .rev()
        .find_map(|line| line.trim().strip_prefix(OUTPUT_PREFIX))
        .context("memory runner returned no framed result")?;
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Capture {
        One(RunnerOutput),
        Sessions { sessions: Vec<RunnerOutput> },
    }
    let outputs = match serde_json::from_str(payload).context("decode memory runner JSON")? {
        Capture::One(output) => vec![output],
        Capture::Sessions { sessions } => sessions,
    };
    anyhow::ensure!(
        !outputs.is_empty() && outputs.len() <= 256,
        "memory_runner_session_limit"
    );
    Ok(outputs)
}

fn fallback_description(
    camera: &CameraProfile,
    activity_peak: f32,
    reason: Option<String>,
) -> MemoryEventDescription {
    let location = [
        camera.city.as_str(),
        camera.region.as_str(),
        camera.country.as_str(),
    ]
    .into_iter()
    .filter(|value| !value.trim().is_empty())
    .collect::<Vec<_>>()
    .join(", ");
    let supplied = if camera.description.trim().is_empty() {
        "No backend scene description was supplied.".to_owned()
    } else {
        format!(
            "Backend metadata describes this camera as: {}.",
            camera.description.trim()
        )
    };
    let location = if location.is_empty() {
        String::new()
    } else {
        format!(" Location metadata: {location}.")
    };
    MemoryEventDescription {
        summary: format!(
            "{supplied}{location} The lightweight observer recorded an activity peak of {:.2}; inspect the linked evidence for visual confirmation.",
            activity_peak
        ),
        scene_type: "metadata-only fallback".to_owned(),
        conditions: "Not visually assessed".to_owned(),
        confidence: 0.2,
        generated_by_model: false,
        model: None,
        fallback_reason: reason,
        ..MemoryEventDescription::default()
    }
}

fn artifact_path(directory: &Path, name: &str) -> anyhow::Result<PathBuf> {
    if name.is_empty()
        || Path::new(name).is_absolute()
        || Path::new(name).components().count() != 1
        || name.contains("..")
    {
        bail!("memory runner returned an invalid artifact name");
    }
    Ok(directory.join(name))
}

async fn ensure_file(path: &Path) -> anyhow::Result<()> {
    let metadata = tokio::fs::metadata(path)
        .await
        .with_context(|| format!("memory artifact {} is missing", path.display()))?;
    if !metadata.is_file() || metadata.len() == 0 {
        bail!("memory artifact {} is empty", path.display());
    }
    Ok(())
}

async fn load_representative_frame(
    metadata: &RunnerRepresentativeFrame,
    thumbnail_path: &Path,
) -> anyhow::Result<RepresentativeFrame> {
    let data_base64 = if metadata.data_base64.is_empty() {
        let encoded = tokio::fs::read(thumbnail_path)
            .await
            .with_context(|| format!("read VLM thumbnail {}", thumbnail_path.display()))?;
        if encoded.len() > 3_000_000 {
            bail!("VLM thumbnail exceeds the three-megabyte encoded-image limit");
        }
        BASE64_STANDARD.encode(encoded)
    } else {
        // Backward compatibility for an older runner. New runners return only
        // the file-backed thumbnail metadata, avoiding base64 for every event.
        metadata.data_base64.clone()
    };
    Ok(RepresentativeFrame {
        media_type: metadata.media_type.clone(),
        data_base64,
        frame_time_ms: metadata.frame_time_ms,
        width: metadata.width,
        height: metadata.height,
    })
}

fn safe_component(value: &str) -> String {
    let output: String = value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .take(80)
        .collect();
    if output.is_empty() {
        "camera".to_owned()
    } else {
        output
    }
}

fn tokenize(value: &str) -> HashSet<String> {
    value
        .to_lowercase()
        .split(|character: char| !character.is_alphanumeric())
        .filter(|term| term.len() > 1 && !STOP_WORDS.contains(term))
        .map(ToOwned::to_owned)
        .collect()
}

fn expanded_terms(terms: &HashSet<String>) -> HashSet<String> {
    let mut expanded = terms.clone();
    for term in terms {
        if let Some(values) = SYNONYMS.iter().find_map(|group| {
            group
                .contains(&term.as_str())
                .then_some(group.iter().copied())
        }) {
            expanded.extend(values.map(ToOwned::to_owned));
        }
    }
    expanded
}

fn document_terms(event: &MemoryEvent, camera: &CameraProfile) -> HashSet<String> {
    let description = &event.description;
    tokenize(
        &[
            camera.camera_id.as_str(),
            camera.country.as_str(),
            camera.country_code.as_str(),
            camera.region.as_str(),
            camera.city.as_str(),
            camera.manufacturer.as_str(),
            camera.description.as_str(),
            description.summary.as_str(),
            description.scene_type.as_str(),
            description.conditions.as_str(),
            &description.visible_objects.join(" "),
            &description.visible_people.join(" "),
            &description.apparent_actions.join(" "),
            &description.visible_text.join(" "),
        ]
        .join(" "),
    )
}

const STOP_WORDS: &[&str] = &[
    "a", "an", "and", "are", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or",
    "the", "this", "to", "was", "what", "when", "where", "with",
];

const SYNONYMS: &[&[&str]] = &[
    &["person", "people", "pedestrian", "worker", "human"],
    &["car", "vehicle", "truck", "van", "automobile"],
    &["ship", "boat", "vessel"],
    &["outside", "outdoor", "exterior"],
    &["road", "street", "roadway"],
    &["move", "moving", "movement", "motion", "activity"],
    &["scrap", "scrapyard", "junkyard"],
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn framed_observer_outputs_accept_legacy_and_bounded_typed_session_sets() {
        let one = serde_json::json!({"evidence_file":"source.mkv","duration_ms":1000,"frames_decoded":2,"events":[]});
        let framed =
            |value: serde_json::Value| format!("diagnostic\n{OUTPUT_PREFIX}{value}\n").into_bytes();
        assert_eq!(decode_runner_output(&framed(one.clone())).unwrap().len(), 1);
        let mut scoped = one;
        scoped["storage_session_id"] = serde_json::json!(Uuid::from_u128(1));
        let parsed =
            decode_runner_output(&framed(serde_json::json!({"sessions":[scoped.clone()]})))
                .unwrap();
        assert_eq!(parsed[0].storage_session_id, Some(Uuid::from_u128(1)));
        assert!(decode_runner_output(&framed(serde_json::json!({"sessions":[]}))).is_err());
        scoped["storage_session_id"] = serde_json::json!("../escape");
        assert!(decode_runner_output(&framed(serde_json::json!({"sessions":[scoped]}))).is_err());
        assert!(decode_runner_output(b"unframed result").is_err());
    }

    #[test]
    fn safe_component_removes_path_syntax() {
        assert_eq!(safe_component("../camera/a"), "___camera_a");
    }

    #[test]
    fn query_terms_expand_common_camera_language() {
        let terms = tokenize("find a moving vehicle");
        let expanded = expanded_terms(&terms);
        assert!(expanded.contains("car"));
        assert!(expanded.contains("motion"));
    }
}
