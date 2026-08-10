use std::{
    collections::{HashMap, VecDeque},
    process::Stdio,
    sync::Arc,
    time::Duration,
};

use anyhow::Context;
use serde::Deserialize;
use serde_json::json;
use tokio::{
    io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, Command},
    sync::{mpsc, oneshot},
    time::{self, Instant, MissedTickBehavior},
};
use tracing::{debug, info, warn};
use uuid::Uuid;

use crate::{
    config::Config,
    domain::{DetectorOutput, Observation},
};

const WORKER_OUTPUT_PREFIX: &[u8] = b"VISN_WORKER_JSON:";
const MAX_WORKER_LINE_BYTES: usize = 8 * 1024 * 1024;
const MAX_WORKER_STDERR_BYTES: usize = 128 * 1024;
const SESSION_EVENT_CAPACITY: usize = 32;
const PROCESS_EVENT_CAPACITY: usize = 16;

#[derive(Clone)]
pub struct DetectorWorker {
    commands: Option<mpsc::UnboundedSender<SupervisorCommand>>,
}

#[derive(Debug)]
pub struct DetectorRequest {
    pub source: String,
    pub fps: f32,
    pub max_seconds: u64,
    pub confidence: f32,
    pub appearance_mode: &'static str,
    pub appearance_interval_secs: f32,
}

#[derive(Debug)]
pub enum DetectorWorkerEvent {
    Observations(Vec<Observation>),
    Complete(DetectorOutput),
    Error(String),
}

pub struct DetectorSession {
    request_id: Uuid,
    events: mpsc::Receiver<DetectorWorkerEvent>,
    commands: mpsc::UnboundedSender<SupervisorCommand>,
    terminal_received: bool,
}

impl DetectorSession {
    pub async fn recv(&mut self) -> Option<DetectorWorkerEvent> {
        let event = self.events.recv().await;
        if matches!(
            event,
            Some(DetectorWorkerEvent::Complete(_) | DetectorWorkerEvent::Error(_))
        ) {
            self.terminal_received = true;
        }
        event
    }
}

impl Drop for DetectorSession {
    fn drop(&mut self) {
        if !self.terminal_received {
            let _ = self.commands.send(SupervisorCommand::Cancel {
                request_id: self.request_id,
            });
        }
    }
}

impl DetectorWorker {
    pub fn new(config: Arc<Config>) -> Self {
        if !config.persistent_detector {
            return Self { commands: None };
        }
        let (commands, receiver) = mpsc::unbounded_channel();
        tokio::spawn(Supervisor::new(config, receiver).run());
        Self {
            commands: Some(commands),
        }
    }

    pub fn enabled(&self) -> bool {
        self.commands.is_some()
    }

    pub async fn start(&self, request: DetectorRequest) -> anyhow::Result<DetectorSession> {
        let commands = self
            .commands
            .as_ref()
            .context("persistent detector worker is disabled")?;
        let request_id = Uuid::now_v7();
        let (events_tx, events) = mpsc::channel(SESSION_EVENT_CAPACITY);
        let (accepted_tx, accepted) = oneshot::channel();
        commands
            .send(SupervisorCommand::Start {
                request_id,
                request,
                events: events_tx,
                accepted: accepted_tx,
            })
            .map_err(|_| anyhow::anyhow!("persistent detector supervisor stopped"))?;
        accepted
            .await
            .context("persistent detector supervisor dropped the start response")?
            .map_err(anyhow::Error::msg)?;
        Ok(DetectorSession {
            request_id,
            events,
            commands: commands.clone(),
            terminal_received: false,
        })
    }

    /// Stop and reap the shared YOLO process once all active camera sessions
    /// finish. Gemma calls this after claiming the entire media budget so the
    /// resident detector model cannot inflate the VLM peak-memory window.
    pub async fn shutdown_when_idle(&self) -> anyhow::Result<()> {
        let Some(commands) = &self.commands else {
            return Ok(());
        };
        let (done_tx, done) = oneshot::channel();
        commands
            .send(SupervisorCommand::ShutdownWhenIdle { done: done_tx })
            .map_err(|_| anyhow::anyhow!("persistent detector supervisor stopped"))?;
        done.await
            .context("persistent detector supervisor dropped the shutdown response")?
            .map_err(anyhow::Error::msg)
    }

    /// Reap a warm worker only when it has no active camera. Sparse media
    /// tasks use this as a best-effort idle-memory release without delaying on
    /// unrelated live analysis.
    pub async fn shutdown_if_idle(&self) -> anyhow::Result<bool> {
        let Some(commands) = &self.commands else {
            return Ok(false);
        };
        let (done_tx, done) = oneshot::channel();
        commands
            .send(SupervisorCommand::ShutdownIfIdle { done: done_tx })
            .map_err(|_| anyhow::anyhow!("persistent detector supervisor stopped"))?;
        done.await
            .context("persistent detector supervisor dropped the idle-shutdown response")?
            .map_err(anyhow::Error::msg)
    }
}

enum SupervisorCommand {
    Start {
        request_id: Uuid,
        request: DetectorRequest,
        events: mpsc::Sender<DetectorWorkerEvent>,
        accepted: oneshot::Sender<Result<(), String>>,
    },
    Cancel {
        request_id: Uuid,
    },
    ShutdownWhenIdle {
        done: oneshot::Sender<Result<(), String>>,
    },
    ShutdownIfIdle {
        done: oneshot::Sender<Result<bool, String>>,
    },
}

struct PendingRequest {
    events: mpsc::Sender<DetectorWorkerEvent>,
    source: String,
}

enum ProcessEvent {
    StdoutLine { generation: u64, line: Vec<u8> },
    StdoutClosed { generation: u64 },
    Fault { generation: u64, message: String },
    Stderr { generation: u64, bytes: Vec<u8> },
}

struct WorkerProcess {
    generation: u64,
    child: Child,
    stdin: ChildStdin,
    stderr: VecDeque<u8>,
    stderr_truncated: bool,
    stdout_closed: bool,
}

struct Supervisor {
    config: Arc<Config>,
    commands: mpsc::UnboundedReceiver<SupervisorCommand>,
    process_events_tx: mpsc::Sender<ProcessEvent>,
    process_events: mpsc::Receiver<ProcessEvent>,
    process: Option<WorkerProcess>,
    next_generation: u64,
    pending: HashMap<Uuid, PendingRequest>,
    drain_waiters: Vec<oneshot::Sender<Result<(), String>>>,
    idle_since: Option<Instant>,
}

impl Supervisor {
    fn new(config: Arc<Config>, commands: mpsc::UnboundedReceiver<SupervisorCommand>) -> Self {
        let (process_events_tx, process_events) = mpsc::channel(PROCESS_EVENT_CAPACITY);
        Self {
            config,
            commands,
            process_events_tx,
            process_events,
            process: None,
            next_generation: 1,
            pending: HashMap::new(),
            drain_waiters: Vec::new(),
            idle_since: None,
        }
    }

    async fn run(mut self) {
        let mut maintenance = time::interval(Duration::from_millis(250));
        maintenance.set_missed_tick_behavior(MissedTickBehavior::Delay);
        loop {
            tokio::select! {
                command = self.commands.recv() => {
                    let Some(command) = command else {
                        self.fail_all("persistent detector supervisor is shutting down").await;
                        self.stop_process().await;
                        break;
                    };
                    self.handle_command(command).await;
                }
                event = self.process_events.recv() => {
                    if let Some(event) = event {
                        self.handle_process_event(event).await;
                    }
                }
                _ = maintenance.tick() => {
                    self.maintain_process().await;
                }
            }
        }
    }

    async fn handle_command(&mut self, command: SupervisorCommand) {
        match command {
            SupervisorCommand::Start {
                request_id,
                request,
                events,
                accepted,
            } => {
                if let Err(error) = self.ensure_process().await {
                    let _ =
                        accepted.send(Err(format!("could not start detector worker: {error:#}")));
                    return;
                }
                self.idle_since = None;
                let source = request.source.clone();
                self.pending.insert(
                    request_id,
                    PendingRequest {
                        events,
                        source: source.clone(),
                    },
                );
                let command = json!({
                    "type": "analyze",
                    "request_id": request_id,
                    "source": source,
                    "fps": request.fps,
                    "max_seconds": request.max_seconds,
                    "confidence": request.confidence,
                    "appearance_mode": request.appearance_mode,
                    "appearance_interval_secs": request.appearance_interval_secs,
                });
                match self.write_command(&command).await {
                    Ok(()) => {
                        let _ = accepted.send(Ok(()));
                    }
                    Err(error) => {
                        self.pending.remove(&request_id);
                        let message = format!("could not submit detector request: {error:#}");
                        let _ = accepted.send(Err(message.clone()));
                        self.abort_process(&message).await;
                    }
                }
            }
            SupervisorCommand::Cancel { request_id } => {
                if self.pending.remove(&request_id).is_some() {
                    let _ = self
                        .write_command(&json!({"type": "cancel", "request_id": request_id}))
                        .await;
                    self.after_pending_change().await;
                }
            }
            SupervisorCommand::ShutdownWhenIdle { done } => {
                if self.pending.is_empty() {
                    self.stop_process().await;
                    let _ = done.send(Ok(()));
                } else {
                    self.drain_waiters.push(done);
                }
            }
            SupervisorCommand::ShutdownIfIdle { done } => {
                if self.pending.is_empty() {
                    let stopped = self.process.is_some();
                    self.stop_process().await;
                    let _ = done.send(Ok(stopped));
                } else {
                    let _ = done.send(Ok(false));
                }
            }
        }
    }

    async fn ensure_process(&mut self) -> anyhow::Result<()> {
        if self.process.is_some() {
            return Ok(());
        }
        let generation = self.next_generation;
        self.next_generation = self.next_generation.wrapping_add(1).max(1);
        let mut command = Command::new(&self.config.detector_executable);
        command
            .args(&self.config.detector_worker_args)
            .arg("--model")
            .arg(&self.config.yolo_model)
            .arg("--threads")
            .arg(self.config.detector_threads.to_string())
            .arg("--max-sessions")
            .arg(self.config.max_concurrent_cameras.to_string())
            .arg("--max-batch-size")
            .arg(self.config.detector_batch_size.to_string())
            .arg("--batch-wait-ms")
            .arg(self.config.detector_batch_wait_ms.to_string())
            .arg("--imgsz")
            .arg(self.config.detector_image_size.to_string())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if let Some(device) = &self.config.detector_device {
            command.arg("--device").arg(device);
        }
        if self.config.detector_warmup {
            command.arg("--warmup");
        }
        let mut child = command.spawn().with_context(|| {
            format!(
                "launch persistent detector through {}",
                self.config.detector_executable
            )
        })?;
        let stdin = child.stdin.take().context("open detector worker stdin")?;
        let stdout = child.stdout.take().context("open detector worker stdout")?;
        let stderr = child.stderr.take().context("open detector worker stderr")?;
        tokio::spawn(read_worker_stdout(
            generation,
            stdout,
            self.process_events_tx.clone(),
        ));
        tokio::spawn(read_worker_stderr(
            generation,
            stderr,
            self.process_events_tx.clone(),
        ));
        self.process = Some(WorkerProcess {
            generation,
            child,
            stdin,
            stderr: VecDeque::with_capacity(MAX_WORKER_STDERR_BYTES),
            stderr_truncated: false,
            stdout_closed: false,
        });
        info!(generation, model = %self.config.yolo_model, "persistent detector worker launched");
        Ok(())
    }

    async fn write_command(&mut self, command: &serde_json::Value) -> anyhow::Result<()> {
        let process = self
            .process
            .as_mut()
            .context("persistent detector process is not running")?;
        let mut encoded = serde_json::to_vec(command).context("encode detector worker command")?;
        encoded.push(b'\n');
        process
            .stdin
            .write_all(&encoded)
            .await
            .context("write detector worker command")?;
        process
            .stdin
            .flush()
            .await
            .context("flush detector worker command")
    }

    async fn handle_process_event(&mut self, event: ProcessEvent) {
        let event_generation = match &event {
            ProcessEvent::StdoutLine { generation, .. }
            | ProcessEvent::StdoutClosed { generation }
            | ProcessEvent::Fault { generation, .. }
            | ProcessEvent::Stderr { generation, .. } => *generation,
        };
        if self
            .process
            .as_ref()
            .is_none_or(|process| process.generation != event_generation)
        {
            return;
        }
        match event {
            ProcessEvent::StdoutLine { line, .. } => {
                if let Err(error) = self.handle_worker_line(&line).await {
                    let message = format!("invalid persistent detector output: {error:#}");
                    self.abort_process(&message).await;
                }
            }
            ProcessEvent::StdoutClosed { .. } => {
                if let Some(process) = self.process.as_mut() {
                    process.stdout_closed = true;
                }
            }
            ProcessEvent::Fault { message, .. } => {
                self.abort_process(&message).await;
            }
            ProcessEvent::Stderr { bytes, .. } => {
                if let Some(process) = self.process.as_mut() {
                    process.stderr.extend(bytes);
                    if process.stderr.len() > MAX_WORKER_STDERR_BYTES {
                        let excess = process.stderr.len() - MAX_WORKER_STDERR_BYTES;
                        process.stderr.drain(..excess);
                        process.stderr_truncated = true;
                    }
                }
            }
        }
    }

    async fn handle_worker_line(&mut self, line: &[u8]) -> anyhow::Result<()> {
        let trimmed = trim_ascii_whitespace(line);
        let Some(payload) = trimmed.strip_prefix(WORKER_OUTPUT_PREFIX) else {
            debug!("ignored non-protocol detector worker stdout");
            return Ok(());
        };
        let envelope: WorkerEnvelope =
            serde_json::from_slice(payload).context("decode detector worker JSON")?;
        match envelope {
            WorkerEnvelope::Ready { model } => {
                info!(%model, "persistent detector worker ready");
            }
            WorkerEnvelope::Observations {
                request_id,
                observations,
            } => {
                let Some(pending) = self.pending.get(&request_id) else {
                    return Ok(());
                };
                if pending
                    .events
                    .send(DetectorWorkerEvent::Observations(observations))
                    .await
                    .is_err()
                {
                    self.pending.remove(&request_id);
                    let _ = self
                        .write_command(&json!({"type": "cancel", "request_id": request_id}))
                        .await;
                    self.after_pending_change().await;
                }
            }
            WorkerEnvelope::Complete { request_id, result } => {
                if let Some(pending) = self.pending.remove(&request_id) {
                    let _ = pending
                        .events
                        .send(DetectorWorkerEvent::Complete(result))
                        .await;
                }
                self.after_pending_change().await;
            }
            WorkerEnvelope::Error {
                request_id,
                message,
            } => {
                if let Some(request_id) = request_id {
                    if let Some(pending) = self.pending.remove(&request_id) {
                        let message = redact_source(&message, &pending.source);
                        let _ = pending
                            .events
                            .send(DetectorWorkerEvent::Error(message))
                            .await;
                    }
                    self.after_pending_change().await;
                } else {
                    warn!(%message, "persistent detector reported a command error");
                }
            }
        }
        Ok(())
    }

    async fn after_pending_change(&mut self) {
        if !self.pending.is_empty() {
            self.idle_since = None;
            return;
        }
        self.idle_since = Some(Instant::now());
        if !self.drain_waiters.is_empty() {
            self.stop_process().await;
            for waiter in self.drain_waiters.drain(..) {
                let _ = waiter.send(Ok(()));
            }
        }
    }

    async fn maintain_process(&mut self) {
        let status = self
            .process
            .as_mut()
            .and_then(|process| match process.child.try_wait() {
                Ok(status) => status,
                Err(error) => {
                    warn!(%error, "could not inspect persistent detector process");
                    None
                }
            });
        if let Some(status) = status {
            let diagnostics = self.render_stderr();
            let message = format!(
                "persistent detector exited with {status}: {}",
                diagnostics.trim()
            );
            self.process = None;
            self.fail_all(&message).await;
            self.resolve_drain_waiters();
            return;
        }

        let stdout_closed = self
            .process
            .as_ref()
            .is_some_and(|process| process.stdout_closed);
        if stdout_closed {
            self.abort_process("persistent detector closed its output unexpectedly")
                .await;
            return;
        }

        if self.pending.is_empty()
            && self.process.is_some()
            && self
                .idle_since
                .is_some_and(|idle| idle.elapsed() >= self.config.detector_worker_idle_duration())
        {
            self.stop_process().await;
        }
    }

    async fn abort_process(&mut self, message: &str) {
        let diagnostics = self.render_stderr();
        let combined = if diagnostics.trim().is_empty() {
            message.to_owned()
        } else {
            format!("{message}: {}", diagnostics.trim())
        };
        if let Some(mut process) = self.process.take() {
            let _ = process.child.kill().await;
        }
        self.fail_all(&combined).await;
        self.resolve_drain_waiters();
    }

    async fn fail_all(&mut self, message: &str) {
        let pending = std::mem::take(&mut self.pending);
        for (_, request) in pending {
            let redacted = redact_source(message, &request.source);
            let _ = request
                .events
                .send(DetectorWorkerEvent::Error(redacted))
                .await;
        }
        self.idle_since = None;
    }

    fn resolve_drain_waiters(&mut self) {
        for waiter in self.drain_waiters.drain(..) {
            let _ = waiter.send(Ok(()));
        }
    }

    fn render_stderr(&self) -> String {
        let Some(process) = &self.process else {
            return String::new();
        };
        let bytes: Vec<u8> = process.stderr.iter().copied().collect();
        let body = String::from_utf8_lossy(&bytes);
        if process.stderr_truncated {
            format!("[earlier worker diagnostics truncated]\n{body}")
        } else {
            body.into_owned()
        }
    }

    async fn stop_process(&mut self) {
        let Some(mut process) = self.process.take() else {
            self.idle_since = None;
            return;
        };
        let shutdown = serde_json::to_vec(&json!({"type": "shutdown"}));
        if let Ok(mut shutdown) = shutdown {
            shutdown.push(b'\n');
            let _ = process.stdin.write_all(&shutdown).await;
            let _ = process.stdin.shutdown().await;
        }
        match time::timeout(Duration::from_secs(3), process.child.wait()).await {
            Ok(Ok(status)) => {
                info!(%status, "persistent detector worker stopped");
            }
            Ok(Err(error)) => {
                warn!(%error, "failed to reap persistent detector worker");
            }
            Err(_) => {
                warn!("persistent detector did not stop promptly; terminating it");
                let _ = process.child.kill().await;
            }
        }
        self.idle_since = None;
    }
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum WorkerEnvelope {
    Ready {
        model: String,
    },
    Observations {
        request_id: Uuid,
        observations: Vec<Observation>,
    },
    Complete {
        request_id: Uuid,
        result: DetectorOutput,
    },
    Error {
        #[serde(default)]
        request_id: Option<Uuid>,
        message: String,
    },
}

async fn read_worker_stdout<R>(generation: u64, reader: R, events: mpsc::Sender<ProcessEvent>)
where
    R: AsyncRead + Unpin,
{
    let mut reader = BufReader::new(reader);
    let mut line = Vec::new();
    loop {
        line.clear();
        match reader.read_until(b'\n', &mut line).await {
            Ok(0) => {
                let _ = events.send(ProcessEvent::StdoutClosed { generation }).await;
                break;
            }
            Ok(_) if line.len() > MAX_WORKER_LINE_BYTES => {
                let _ = events
                    .send(ProcessEvent::Fault {
                        generation,
                        message: format!(
                            "persistent detector emitted a line larger than {MAX_WORKER_LINE_BYTES} bytes"
                        ),
                    })
                    .await;
                break;
            }
            Ok(_) => {
                let _ = events
                    .send(ProcessEvent::StdoutLine {
                        generation,
                        line: line.clone(),
                    })
                    .await;
            }
            Err(error) => {
                let _ = events
                    .send(ProcessEvent::Fault {
                        generation,
                        message: format!("read persistent detector output: {error}"),
                    })
                    .await;
                break;
            }
        }
    }
}

async fn read_worker_stderr<R>(generation: u64, mut reader: R, events: mpsc::Sender<ProcessEvent>)
where
    R: AsyncRead + Unpin,
{
    let mut buffer = [0_u8; 8 * 1024];
    loop {
        match reader.read(&mut buffer).await {
            Ok(0) => break,
            Ok(count) => {
                let _ = events
                    .send(ProcessEvent::Stderr {
                        generation,
                        bytes: buffer[..count].to_vec(),
                    })
                    .await;
            }
            Err(error) => {
                let _ = events
                    .send(ProcessEvent::Fault {
                        generation,
                        message: format!("read persistent detector diagnostics: {error}"),
                    })
                    .await;
                break;
            }
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

fn redact_source(message: &str, source: &str) -> String {
    if source.is_empty() {
        return message.to_owned();
    }
    message.replace(source, redacted_source_label(source))
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_observation_envelope() {
        let payload = br#"{"type":"observations","request_id":"018f0f45-6e04-7a10-8000-000000000001","observations":[]}"#;
        assert!(matches!(
            serde_json::from_slice::<WorkerEnvelope>(payload).unwrap(),
            WorkerEnvelope::Observations { observations, .. } if observations.is_empty()
        ));
    }

    #[test]
    fn redacts_network_sources() {
        assert_eq!(
            redact_source(
                "failed http://user:secret@example.test/live",
                "http://user:secret@example.test/live"
            ),
            "failed http://***"
        );
    }
}
