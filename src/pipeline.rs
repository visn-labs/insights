use std::{
    collections::{BTreeSet, VecDeque},
    path::PathBuf,
    process::Stdio,
    sync::Arc,
};

use anyhow::{bail, Context};
use tokio::{
    io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader},
    process::Command,
    sync::Semaphore,
};
use tracing::{info, warn};
use uuid::Uuid;

use crate::{
    config::Config,
    domain::{
        AnalyticsPolicy, BackendKind, DetectorOutput, GemmaRun, JobRequest, Line, Observation,
        PipelineResult, RepresentativeFrame, ViewDescription, Zone,
    },
    event_engine::{self, Analysis, StreamingAnalyzer},
    gemma::GemmaClient,
    sink::EventSink,
};

const DETECTOR_OUTPUT_PREFIX: &[u8] = b"VISN_DETECTOR_JSON:";
const OBSERVATIONS_OUTPUT_PREFIX: &[u8] = b"VISN_OBSERVATIONS_JSON:";
const MAX_RUNNER_LINE_BYTES: usize = 8 * 1024 * 1024;
const MAX_RUNNER_STDERR_BYTES: usize = 128 * 1024;

#[derive(Clone, Copy)]
enum DetectorAppearanceMode {
    Off,
    Person,
}

impl DetectorAppearanceMode {
    fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Person => "person",
        }
    }
}

struct ProcessedDetection {
    model: String,
    representative_frame: Option<RepresentativeFrame>,
    observations_processed: usize,
    detected_classes: Vec<String>,
    analysis: Analysis,
}

struct CapturedStderr {
    bytes: Vec<u8>,
    truncated: bool,
}

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
    detector_gate: Arc<Semaphore>,
}

impl PipelineService {
    pub fn new(
        config: Arc<Config>,
        gemma: GemmaClient,
        sink: Arc<dyn EventSink>,
        detector_gate: Arc<Semaphore>,
    ) -> Self {
        Self {
            detector_gate,
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
        self.process_with_gemma_options(
            job_id,
            request,
            source,
            request.gemma_enabled,
            request.gemma_enabled,
            request.vlm_model.as_deref(),
            DetectorAppearanceMode::Off,
        )
        .await
    }

    pub async fn process_cluster_camera(
        &self,
        job_id: Uuid,
        request: &JobRequest,
        source: ResolvedSource,
        view_description_enabled: bool,
    ) -> anyhow::Result<PipelineResult> {
        self.process_with_gemma_options(
            job_id,
            request,
            source,
            false,
            view_description_enabled,
            request.vlm_model.as_deref(),
            DetectorAppearanceMode::Person,
        )
        .await
    }

    async fn process_with_gemma_options(
        &self,
        job_id: Uuid,
        request: &JobRequest,
        source: ResolvedSource,
        report_gemma_enabled: bool,
        view_description_enabled: bool,
        requested_vlm_model: Option<&str>,
        appearance_mode: DetectorAppearanceMode,
    ) -> anyhow::Result<PipelineResult> {
        let policy = if matches!(source, ResolvedSource::Sample)
            && request.policy.zones.is_empty()
            && request.policy.lines.is_empty()
        {
            sample_policy()
        } else {
            request.policy.clone()
        };
        let detector = match request.backend {
            BackendKind::Simulator => {
                let detector = self.simulate(request, &source)?;
                validate_observations(&detector.observations)?;
                let detected_classes = distinct_classes(&detector.observations);
                let observations_processed = detector.observations.len();
                let analysis = event_engine::analyze(job_id, &detector.observations, &policy);
                ProcessedDetection {
                    model: detector.model,
                    representative_frame: detector.representative_frame,
                    observations_processed,
                    detected_classes,
                    analysis,
                }
            }
            BackendKind::Yolo26Command => {
                self.run_yolo26(job_id, request, &source, &policy, appearance_mode)
                    .await?
            }
        };
        let analysis = detector.analysis;
        let deterministic_report = analysis.report.clone();

        let view_description = self
            .describe_view(
                job_id,
                detector.representative_frame.as_ref(),
                &detector.detected_classes,
                view_description_enabled,
                requested_vlm_model,
            )
            .await;

        let (report, gemma_run) = self
            .enrich_report(
                job_id,
                &deterministic_report,
                report_gemma_enabled,
                requested_vlm_model,
            )
            .await;

        for event in &analysis.events {
            self.sink.publish_event(job_id, event).await?;
        }
        self.sink.publish_report(job_id, &report).await?;

        info!(
            %job_id,
            backend = ?request.backend,
            observations = detector.observations_processed,
            tracks = analysis.tracks.len(),
            events = analysis.events.len(),
            sink = self.sink.name(),
            "pipeline completed"
        );
        Ok(PipelineResult {
            backend: request.backend,
            model: detector.model,
            detector_fps: request.detector_fps,
            observations_processed: detector.observations_processed,
            duration_ms: analysis.duration_ms,
            tracks: analysis.tracks,
            events: analysis.events,
            view_description,
            deterministic_report,
            report,
            gemma: gemma_run,
        })
    }

    async fn describe_view(
        &self,
        job_id: Uuid,
        frame: Option<&RepresentativeFrame>,
        detected_classes: &[String],
        enabled: bool,
        requested_vlm_model: Option<&str>,
    ) -> ViewDescription {
        if !enabled {
            return fallback_view_description(
                detected_classes,
                "Gemma view description was disabled for this run".to_owned(),
            );
        }
        let Some(frame) = frame else {
            return fallback_view_description(
                detected_classes,
                "the detector did not capture a representative video frame".to_owned(),
            );
        };
        match self
            .gemma
            .describe_view(frame, detected_classes, requested_vlm_model)
            .await
        {
            Ok((view, model)) => ViewDescription {
                description: view.description,
                scene_type: view.scene_type,
                visible_areas: view.visible_areas,
                notable_static_elements: view.notable_static_elements,
                visibility_conditions: view.visibility_conditions,
                confidence: view.confidence,
                generated_by_model: true,
                model: Some(model),
                fallback_reason: None,
            },
            Err(error) => {
                warn!(%job_id, error = %error, "Gemma could not describe the camera view; using detector context");
                fallback_view_description(detected_classes, error.to_string())
            }
        }
    }

    pub async fn enrich_report(
        &self,
        job_id: Uuid,
        deterministic_report: &crate::domain::Report,
        gemma_enabled: bool,
        requested_vlm_model: Option<&str>,
    ) -> (crate::domain::Report, GemmaRun) {
        if gemma_enabled {
            match self
                .gemma
                .generate_report(&deterministic_report, requested_vlm_model)
                .await
            {
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
                            model: requested_vlm_model
                                .map(ToOwned::to_owned)
                                .or_else(|| self.config.gemma_model.clone()),
                            fallback_reason: Some(error.to_string()),
                        },
                    )
                }
            }
        } else {
            return (
                deterministic_report.clone(),
                GemmaRun {
                    requested: false,
                    used: false,
                    model: None,
                    fallback_reason: None,
                },
            );
        }
    }

    pub async fn publish_report(
        &self,
        job_id: Uuid,
        report: &crate::domain::Report,
    ) -> anyhow::Result<()> {
        self.sink.publish_report(job_id, report).await
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
            representative_frame: None,
        })
    }

    async fn run_yolo26(
        &self,
        job_id: Uuid,
        request: &JobRequest,
        source: &ResolvedSource,
        policy: &AnalyticsPolicy,
        appearance_mode: DetectorAppearanceMode,
    ) -> anyhow::Result<ProcessedDetection> {
        let _permit = self
            .detector_gate
            .acquire()
            .await
            .context("acquire global detector-process gate")?;
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
            .arg("--stream-output")
            .arg("--appearance-mode")
            .arg(appearance_mode.as_str())
            .arg("--appearance-interval-secs")
            .arg(self.config.appearance_interval_secs.to_string())
            .arg("--threads")
            .arg(self.config.detector_threads.to_string())
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

        let stdout = child
            .stdout
            .take()
            .context("open YOLO26 runner standard output")?;
        let stderr = child
            .stderr
            .take()
            .context("open YOLO26 runner standard error")?;
        let stderr_task = tokio::spawn(capture_bounded_stderr(stderr));
        let mut stdout = BufReader::new(stdout);
        let mut analyzer = StreamingAnalyzer::new(job_id, policy);
        let mut detected_classes = BTreeSet::new();
        let mut detector_output = None;
        let mut saw_stream_output = false;
        let mut line = Vec::new();

        let parse_result: anyhow::Result<()> = async {
            loop {
                line.clear();
                let bytes_read = stdout
                    .read_until(b'\n', &mut line)
                    .await
                    .context("read YOLO26 runner standard output")?;
                if bytes_read == 0 {
                    break;
                }
                if line.len() > MAX_RUNNER_LINE_BYTES {
                    bail!(
                        "YOLO26 runner emitted a line larger than {} bytes",
                        MAX_RUNNER_LINE_BYTES
                    );
                }
                while matches!(line.last(), Some(b'\n' | b'\r')) {
                    line.pop();
                }
                let trimmed = trim_ascii_whitespace(&line);
                if let Some(payload) = trimmed.strip_prefix(OBSERVATIONS_OUTPUT_PREFIX) {
                    saw_stream_output = true;
                    let observations: Vec<Observation> = serde_json::from_slice(payload)
                        .context("decode streamed YOLO26 observations")?;
                    validate_observations(&observations)?;
                    for observation in &observations {
                        if !detected_classes.contains(observation.class_name.as_str()) {
                            detected_classes.insert(observation.class_name.clone());
                        }
                        analyzer
                            .observe(observation)
                            .context("aggregate streamed YOLO26 observation")?;
                    }
                } else if let Some(payload) = trimmed.strip_prefix(DETECTOR_OUTPUT_PREFIX) {
                    if detector_output.is_some() {
                        bail!("YOLO26 runner emitted more than one final result");
                    }
                    detector_output = Some(
                        serde_json::from_slice::<DetectorOutput>(payload)
                            .context("decode final YOLO26 detector output")?,
                    );
                }
            }
            Ok(())
        }
        .await;

        if parse_result.is_err() {
            let _ = child.kill().await;
        }
        let status = child.wait().await.context("wait for YOLO26 runner")?;
        let captured_stderr = stderr_task
            .await
            .context("join YOLO26 stderr capture task")??;
        parse_result?;

        if !status.success() {
            let stderr = captured_stderr
                .render()
                .replace(&source, redacted_source_label(&source));
            bail!("YOLO26 runner failed with {}: {}", status, stderr.trim());
        }

        let mut detector_output = detector_output.context(
            "YOLO26 runner returned no framed result; check that VISN_DETECTOR_ARGS points to the current tools/yolo26_runner.py",
        )?;
        if saw_stream_output && !detector_output.observations.is_empty() {
            bail!("YOLO26 runner mixed streamed observations into its final result");
        }
        if !saw_stream_output {
            validate_observations(&detector_output.observations)?;
            for observation in &detector_output.observations {
                if !detected_classes.contains(observation.class_name.as_str()) {
                    detected_classes.insert(observation.class_name.clone());
                }
                analyzer
                    .observe(observation)
                    .context("aggregate fallback YOLO26 observation")?;
            }
        }

        let observations_processed = analyzer.observation_count();
        Ok(ProcessedDetection {
            model: detector_output.model,
            representative_frame: detector_output.representative_frame.take(),
            observations_processed,
            detected_classes: detected_classes.into_iter().collect(),
            analysis: analyzer.finish(),
        })
    }
}

fn fallback_view_description(detected_classes: &[String], reason: String) -> ViewDescription {
    let class_context = if detected_classes.is_empty() {
        "No detector classes were observed during the monitoring window.".to_owned()
    } else {
        format!(
            "The detector observed these class types during the monitoring window: {}. The physical setting and layout could not be inferred without vision-model output.",
            detected_classes.join(", ")
        )
    };
    ViewDescription {
        description: class_context,
        scene_type: "undetermined".to_owned(),
        visible_areas: Vec::new(),
        notable_static_elements: Vec::new(),
        visibility_conditions: "Not assessed".to_owned(),
        confidence: 0.0,
        generated_by_model: false,
        model: None,
        fallback_reason: Some(reason),
    }
}

fn distinct_classes(observations: &[Observation]) -> Vec<String> {
    observations
        .iter()
        .map(|observation| observation.class_name.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

async fn capture_bounded_stderr<R>(mut reader: R) -> std::io::Result<CapturedStderr>
where
    R: AsyncRead + Unpin,
{
    let mut bytes = VecDeque::with_capacity(MAX_RUNNER_STDERR_BYTES);
    let mut truncated = false;
    let mut buffer = [0_u8; 8 * 1024];
    loop {
        let count = reader.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        bytes.extend(&buffer[..count]);
        if bytes.len() > MAX_RUNNER_STDERR_BYTES {
            let excess = bytes.len() - MAX_RUNNER_STDERR_BYTES;
            bytes.drain(..excess);
            truncated = true;
        }
    }
    Ok(CapturedStderr {
        bytes: bytes.into_iter().collect(),
        truncated,
    })
}

impl CapturedStderr {
    fn render(&self) -> String {
        let body = String::from_utf8_lossy(&self.bytes);
        if self.truncated {
            format!("[earlier runner diagnostics truncated]\n{body}")
        } else {
            body.into_owned()
        }
    }
}

fn trim_ascii_whitespace(mut value: &[u8]) -> &[u8] {
    while value.first().is_some_and(u8::is_ascii_whitespace) {
        value = &value[1..];
    }
    while value.last().is_some_and(u8::is_ascii_whitespace) {
        value = &value[..value.len() - 1];
    }
    value
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
            appearance: None,
        });
    }
    for (index, x) in [0.82, 0.72, 0.58, 0.43, 0.28].into_iter().enumerate() {
        output.push(Observation {
            frame_time_ms: 500 + index as u64 * 900,
            track_id: "vehicle-001".to_owned(),
            class_name: "car".to_owned(),
            confidence: 0.88,
            bbox: [x, 0.56, 0.16, 0.2],
            appearance: None,
        });
    }
    output.sort_by_key(|observation| observation.frame_time_ms);
    output
}
