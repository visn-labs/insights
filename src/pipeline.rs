use std::{path::PathBuf, process::Stdio, sync::Arc};

use anyhow::{bail, Context};
use tokio::{io::AsyncWriteExt, process::Command};
use tracing::{info, warn};
use uuid::Uuid;

use crate::{
    config::Config,
    domain::{
        AnalyticsPolicy, BackendKind, DetectorOutput, GemmaRun, JobRequest, Line, Observation,
        PipelineResult, Zone,
    },
    event_engine,
    gemma::GemmaClient,
    sink::EventSink,
};

#[derive(Clone, Debug)]
pub enum ResolvedSource {
    Sample,
    Upload(PathBuf),
    Rtsp(String),
    Http(String),
}

impl ResolvedSource {
    fn detector_value(&self) -> anyhow::Result<String> {
        match self {
            Self::Sample => {
                bail!("the YOLO26 command backend needs an upload or network stream source")
            }
            Self::Upload(path) => Ok(path.to_string_lossy().into_owned()),
            Self::Rtsp(uri) => Ok(uri.clone()),
            Self::Http(uri) => Ok(uri.clone()),
        }
    }
}

#[derive(Clone)]
pub struct PipelineService {
    config: Arc<Config>,
    gemma: GemmaClient,
    sink: Arc<dyn EventSink>,
}

impl PipelineService {
    pub fn new(config: Arc<Config>, gemma: GemmaClient, sink: Arc<dyn EventSink>) -> Self {
        Self {
            config,
            gemma,
            sink,
        }
    }

    pub fn sink_name(&self) -> &'static str {
        self.sink.name()
    }

    pub async fn models(&self) -> anyhow::Result<Vec<crate::gemma::ModelInfo>> {
        self.gemma.models().await
    }

    pub async fn process(
        &self,
        job_id: Uuid,
        request: &JobRequest,
        source: ResolvedSource,
    ) -> anyhow::Result<PipelineResult> {
        let detector = match request.backend {
            BackendKind::Simulator => self.simulate(request, &source)?,
            BackendKind::Yolo26Command => self.run_yolo26(request, &source).await?,
        };
        validate_observations(&detector.observations)?;

        let policy = if matches!(source, ResolvedSource::Sample)
            && request.policy.zones.is_empty()
            && request.policy.lines.is_empty()
        {
            sample_policy()
        } else {
            request.policy.clone()
        };
        let analysis = event_engine::analyze(job_id, &detector.observations, &policy);
        let deterministic_report = analysis.report.clone();

        let (report, gemma_run) = if request.gemma_enabled {
            match self.gemma.generate_report(&deterministic_report).await {
                Ok((report, model)) => (
                    report,
                    GemmaRun {
                        requested: true,
                        used: true,
                        model: Some(model),
                        fallback_reason: None,
                    },
                ),
                Err(error) => {
                    warn!(%job_id, error = %error, "Gemma unavailable or invalid; using deterministic report");
                    (
                        deterministic_report.clone(),
                        GemmaRun {
                            requested: true,
                            used: false,
                            model: self.config.gemma_model.clone(),
                            fallback_reason: Some(error.to_string()),
                        },
                    )
                }
            }
        } else {
            (
                deterministic_report.clone(),
                GemmaRun {
                    requested: false,
                    used: false,
                    model: None,
                    fallback_reason: None,
                },
            )
        };

        for event in &analysis.events {
            self.sink.publish_event(job_id, event).await?;
        }
        self.sink.publish_report(job_id, &report).await?;

        info!(
            %job_id,
            backend = ?request.backend,
            observations = detector.observations.len(),
            tracks = analysis.tracks.len(),
            events = analysis.events.len(),
            sink = self.sink.name(),
            "pipeline completed"
        );
        Ok(PipelineResult {
            backend: request.backend,
            model: detector.model,
            detector_fps: request.detector_fps,
            observations_processed: detector.observations.len(),
            duration_ms: analysis.duration_ms,
            tracks: analysis.tracks,
            events: analysis.events,
            deterministic_report,
            report,
            gemma: gemma_run,
        })
    }

    fn simulate(
        &self,
        request: &JobRequest,
        source: &ResolvedSource,
    ) -> anyhow::Result<DetectorOutput> {
        let observations = if !request.observations.is_empty() {
            request.observations.clone()
        } else if matches!(source, ResolvedSource::Sample) {
            sample_observations()
        } else {
            bail!("simulator mode requires manual observations for uploaded or stream sources");
        };
        Ok(DetectorOutput {
            model: "phase0-deterministic-simulator".to_owned(),
            observations,
        })
    }

    async fn run_yolo26(
        &self,
        request: &JobRequest,
        source: &ResolvedSource,
    ) -> anyhow::Result<DetectorOutput> {
        let source = source.detector_value()?;
        let mut command = Command::new(&self.config.detector_executable);
        command
            .args(&self.config.detector_args)
            .arg("--source")
            .arg("-")
            .arg("--model")
            .arg(&self.config.yolo_model)
            .arg("--fps")
            .arg(request.detector_fps.to_string())
            .arg("--max-seconds")
            .arg(request.monitor_duration_secs.to_string())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let mut child = command
            .spawn()
            .with_context(|| format!("launch {}", self.config.detector_executable))?;
        let mut stdin = child
            .stdin
            .take()
            .context("open YOLO26 runner standard input")?;
        stdin
            .write_all(source.as_bytes())
            .await
            .context("write source to YOLO26 runner")?;
        drop(stdin);
        let output = child
            .wait_with_output()
            .await
            .context("wait for YOLO26 runner")?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr)
                .replace(&source, redacted_source_label(&source));
            bail!(
                "YOLO26 runner failed with {}: {}",
                output.status,
                stderr.trim()
            );
        }
        decode_detector_output(&output.stdout)
    }
}

fn decode_detector_output(stdout: &[u8]) -> anyhow::Result<DetectorOutput> {
    const PREFIX: &str = "VISN_DETECTOR_JSON:";
    let text = String::from_utf8_lossy(stdout);
    if let Some(payload) = text
        .lines()
        .rev()
        .find_map(|line| line.trim().strip_prefix(PREFIX))
    {
        return serde_json::from_str(payload).context("decode framed YOLO26 detector output JSON");
    }

    serde_json::from_slice(stdout).context(
        "decode YOLO26 detector output JSON (runner returned no framed result; check that VISN_DETECTOR_ARGS points to the current tools/yolo26_runner.py)",
    )
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

fn validate_observations(observations: &[Observation]) -> anyhow::Result<()> {
    for (index, observation) in observations.iter().enumerate() {
        if observation.track_id.trim().is_empty() || observation.class_name.trim().is_empty() {
            bail!("observation {index} requires track_id and class_name");
        }
        if !(0.0..=1.0).contains(&observation.confidence) {
            bail!("observation {index} confidence must be between zero and one");
        }
        let [x, y, width, height] = observation.bbox;
        if x < 0.0
            || y < 0.0
            || width <= 0.0
            || height <= 0.0
            || x + width > 1.0001
            || y + height > 1.0001
        {
            bail!("observation {index} bbox must be normalized inside the frame");
        }
    }
    Ok(())
}

pub fn sample_policy() -> AnalyticsPolicy {
    AnalyticsPolicy {
        zones: vec![Zone {
            id: "restricted-loading-area".to_owned(),
            points: vec![[0.64, 0.08], [0.96, 0.08], [0.96, 0.94], [0.64, 0.94]],
            restricted: true,
        }],
        lines: vec![Line {
            id: "main-entry".to_owned(),
            start: [0.5, 0.0],
            end: [0.5, 1.0],
            positive_to_negative_label: "inbound".to_owned(),
            negative_to_positive_label: "outbound".to_owned(),
        }],
        minimum_confirmation_observations: 3,
        dwell_threshold_ms: 4_000,
    }
}

pub fn sample_observations() -> Vec<Observation> {
    let mut output = Vec::new();
    for (index, x) in [0.12, 0.24, 0.38, 0.51, 0.67, 0.76].into_iter().enumerate() {
        output.push(Observation {
            frame_time_ms: index as u64 * 1_000,
            track_id: "person-001".to_owned(),
            class_name: "person".to_owned(),
            confidence: 0.91 + index as f32 * 0.01,
            bbox: [x, 0.38, 0.12, 0.38],
        });
    }
    for (index, x) in [0.82, 0.72, 0.58, 0.43, 0.28].into_iter().enumerate() {
        output.push(Observation {
            frame_time_ms: 500 + index as u64 * 900,
            track_id: "vehicle-001".to_owned(),
            class_name: "car".to_owned(),
            confidence: 0.88,
            bbox: [x, 0.56, 0.16, 0.2],
        });
    }
    output.sort_by_key(|observation| observation.frame_time_ms);
    output
}
