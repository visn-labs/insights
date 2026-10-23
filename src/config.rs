use std::{env, path::PathBuf};

use anyhow::{bail, Context};

#[derive(Clone, Debug)]
pub struct Config {
    pub bind: String,
    pub data_dir: PathBuf,
    pub max_upload_bytes: usize,
    pub log_filter: String,
    pub gemma_base_url: String,
    pub lmstudio_api_base_url: String,
    pub gemma_model: Option<String>,
    pub gemma_api_key: String,
    pub gemma_timeout_secs: u64,
    pub vlm_context_length: usize,
    pub vlm_eval_batch_size: usize,
    pub vlm_max_output_tokens: usize,
    pub vlm_flash_attention: bool,
    pub vlm_offload_kv_cache_to_gpu: bool,
    pub vlm_exclusive_media: bool,
    pub detector_executable: String,
    pub detector_args: Vec<String>,
    pub detector_worker_args: Vec<String>,
    pub memory_runner_args: Vec<String>,
    pub yolo_model: String,
    pub persistent_detector: bool,
    pub persistent_detector_fallback: bool,
    pub detector_batch_size: usize,
    pub detector_batch_wait_ms: u64,
    pub detector_worker_idle_secs: u64,
    pub detector_image_size: usize,
    pub detector_device: Option<String>,
    pub detector_warmup: bool,
    pub detector_threads: usize,
    pub appearance_interval_secs: f32,
    pub max_analysis_secs: u64,
    pub max_memory_events_per_camera: usize,
    pub max_vlm_events_per_camera: usize,
    pub max_cluster_cameras: usize,
    pub max_concurrent_cameras: usize,
    pub max_ephemeral_jobs: usize,
    pub memory_clip_mode: String,
    pub media_policy: String,
    pub privacy_python: String,
    pub privacy_model_dir: PathBuf,
    pub retrieval_url: Option<String>,
    pub retrieval_expected_profile: Option<String>,
    pub retrieval_deployment_generation: Option<uuid::Uuid>,
    pub retrieval_timeout_secs: u64,
    pub resource_dir: Option<PathBuf>,
    pub resource_wait_secs: u64,
    pub resource_runner_args: Vec<String>,
    pub managed_lmstudio: bool,
    pub kafka_enabled: bool,
    #[cfg(feature = "kafka")]
    pub kafka_brokers: String,
    #[cfg(feature = "kafka")]
    pub kafka_event_topic: String,
    #[cfg(feature = "kafka")]
    pub kafka_report_topic: String,
}

impl Config {
    pub fn from_env() -> anyhow::Result<Self> {
        let retrieval_expected_profile =
            optional("VISN_RETRIEVAL_EXPECTED_PROFILE").map(|value| value.to_ascii_lowercase());
        if retrieval_expected_profile.as_ref().is_some_and(|value| {
            value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit())
        }) {
            bail!("VISN_RETRIEVAL_EXPECTED_PROFILE must be a SHA-256 fingerprint");
        }
        let resource_wait_secs = nonnegative_u64("VISN_RESOURCE_WAIT_SECS", "180")?;
        if resource_wait_secs > 3600 {
            bail!("VISN_RESOURCE_WAIT_SECS must not exceed 3600");
        }
        let resource_dir = optional("VISN_RESOURCE_DIR")
            .map(std::path::absolute)
            .transpose()?;
        let managed_lmstudio = bool_value("VISN_MANAGED_LMSTUDIO", false)?;
        if managed_lmstudio && resource_dir.is_none() {
            bail!("VISN_MANAGED_LMSTUDIO requires VISN_RESOURCE_DIR");
        }
        let max_upload_mb = value("VISN_MAX_UPLOAD_MB", "2048")
            .parse::<usize>()
            .context("VISN_MAX_UPLOAD_MB must be a positive integer")?;
        if max_upload_mb == 0 {
            bail!("VISN_MAX_UPLOAD_MB must be greater than zero");
        }

        let max_analysis_secs = value("VISN_MAX_ANALYSIS_SECS", "3600")
            .parse::<u64>()
            .context("VISN_MAX_ANALYSIS_SECS must be an integer")?;
        if max_analysis_secs == 0 {
            bail!("VISN_MAX_ANALYSIS_SECS must be greater than zero");
        }
        let max_cluster_cameras = positive_usize("VISN_MAX_CLUSTER_CAMERAS", "16")?;
        let max_concurrent_cameras = positive_usize("VISN_MAX_CONCURRENT_CAMERAS", "4")?;
        if max_concurrent_cameras > max_cluster_cameras {
            bail!("VISN_MAX_CONCURRENT_CAMERAS must not exceed VISN_MAX_CLUSTER_CAMERAS");
        }
        let default_detector_executable = if PathBuf::from(".venv/bin/python").is_file() {
            ".venv/bin/python"
        } else {
            "python3"
        };
        let yolo_model = value("VISN_YOLO_MODEL", "yolo26s.pt");
        let detector_batch_size = match optional("VISN_DETECTOR_BATCH_SIZE") {
            Some(raw) => raw
                .parse::<usize>()
                .context("VISN_DETECTOR_BATCH_SIZE must be a positive integer")?,
            None if yolo_model.to_ascii_lowercase().ends_with(".pt") => {
                max_concurrent_cameras.min(4)
            }
            None => 1,
        };
        if detector_batch_size == 0 || detector_batch_size > max_concurrent_cameras {
            bail!("VISN_DETECTOR_BATCH_SIZE must be between 1 and VISN_MAX_CONCURRENT_CAMERAS");
        }
        let detector_batch_wait_ms = nonnegative_u64("VISN_DETECTOR_BATCH_WAIT_MS", "12")?;
        if detector_batch_wait_ms > 1_000 {
            bail!("VISN_DETECTOR_BATCH_WAIT_MS must not exceed 1000");
        }

        let media_policy = value("VISN_MEDIA_POLICY", "research-raw-v1");
        if !matches!(media_policy.as_str(), "research-raw-v1" | "sanitized-v1") {
            bail!("VISN_MEDIA_POLICY must be research-raw-v1 or sanitized-v1");
        }
        let clip_mode = memory_clip_mode()?;
        if media_policy == "sanitized-v1" && clip_mode == "reference" {
            bail!("sanitized-v1 requires copy or transcode clips for browser-compatible sanitized playback");
        }
        Ok(Self {
            bind: value("VISN_BIND", "127.0.0.1:8080"),
            data_dir: PathBuf::from(value("VISN_DATA_DIR", "./data")),
            max_upload_bytes: max_upload_mb.saturating_mul(1024 * 1024),
            log_filter: value("VISN_LOG", "visn_phase0=info,tower_http=info"),
            gemma_base_url: value("VISN_GEMMA_BASE_URL", "http://127.0.0.1:1234/v1")
                .trim_end_matches('/')
                .to_owned(),
            lmstudio_api_base_url: optional("VISN_LMSTUDIO_API_BASE_URL")
                .unwrap_or_else(|| {
                    native_api_base_from_openai_base(&value(
                        "VISN_GEMMA_BASE_URL",
                        "http://127.0.0.1:1234/v1",
                    ))
                })
                .trim_end_matches('/')
                .to_owned(),
            gemma_model: optional("VISN_GEMMA_MODEL"),
            gemma_api_key: value("VISN_GEMMA_API_KEY", "lm-studio"),
            gemma_timeout_secs: value("VISN_GEMMA_TIMEOUT_SECS", "120")
                .parse()
                .context("VISN_GEMMA_TIMEOUT_SECS must be an integer")?,
            vlm_context_length: positive_usize("VISN_VLM_CONTEXT_LENGTH", "4096")?,
            vlm_eval_batch_size: positive_usize("VISN_VLM_EVAL_BATCH_SIZE", "256")?,
            vlm_max_output_tokens: positive_usize("VISN_VLM_MAX_OUTPUT_TOKENS", "768")?,
            vlm_flash_attention: bool_value("VISN_VLM_FLASH_ATTENTION", true)?,
            vlm_offload_kv_cache_to_gpu: bool_value("VISN_VLM_OFFLOAD_KV_CACHE_TO_GPU", false)?,
            vlm_exclusive_media: bool_value("VISN_VLM_EXCLUSIVE_MEDIA", true)?,
            detector_executable: value("VISN_DETECTOR_EXECUTABLE", default_detector_executable),
            detector_args: value("VISN_DETECTOR_ARGS", "tools/yolo26_runner.py")
                .split_whitespace()
                .map(ToOwned::to_owned)
                .collect(),
            detector_worker_args: value("VISN_DETECTOR_WORKER_ARGS", "tools/yolo26_worker.py")
                .split_whitespace()
                .map(ToOwned::to_owned)
                .collect(),
            memory_runner_args: value("VISN_MEMORY_RUNNER_ARGS", "tools/event_memory_runner.py")
                .split_whitespace()
                .map(ToOwned::to_owned)
                .collect(),
            yolo_model,
            persistent_detector: bool_value("VISN_PERSISTENT_DETECTOR", true)?,
            persistent_detector_fallback: bool_value("VISN_PERSISTENT_DETECTOR_FALLBACK", true)?,
            detector_batch_size,
            detector_batch_wait_ms,
            detector_worker_idle_secs: nonnegative_u64("VISN_DETECTOR_WORKER_IDLE_SECS", "30")?,
            detector_image_size: positive_usize("VISN_DETECTOR_IMGSZ", "640")?,
            detector_device: optional("VISN_DETECTOR_DEVICE"),
            detector_warmup: bool_value("VISN_DETECTOR_WARMUP", true)?,
            detector_threads: positive_usize("VISN_DETECTOR_THREADS", "1")?,
            appearance_interval_secs: nonnegative_f32("VISN_APPEARANCE_INTERVAL_SECS", "1.0")?,
            max_analysis_secs,
            max_memory_events_per_camera: positive_usize(
                "VISN_MAX_MEMORY_EVENTS_PER_CAMERA",
                "48",
            )?,
            max_vlm_events_per_camera: positive_usize("VISN_MAX_VLM_EVENTS_PER_CAMERA", "8")?,
            max_cluster_cameras,
            max_concurrent_cameras,
            max_ephemeral_jobs: positive_usize("VISN_MAX_EPHEMERAL_JOBS", "128")?,
            media_policy,
            privacy_python: value("VISN_PRIVACY_PYTHON", ".venv-retrieval/bin/python"),
            privacy_model_dir: PathBuf::from(value(
                "VISN_PRIVACY_MODEL_DIR",
                ".retrieval/privacy-models",
            )),
            memory_clip_mode: clip_mode,
            retrieval_url: optional("VISN_RETRIEVAL_URL"),
            retrieval_expected_profile,
            retrieval_deployment_generation: None,
            retrieval_timeout_secs: positive_usize("VISN_RETRIEVAL_TIMEOUT_SECS", "600")? as u64,
            resource_dir,
            resource_wait_secs,
            resource_runner_args: value("VISN_RESOURCE_RUNNER_ARGS", "tools/workflow_resources.py")
                .split_whitespace()
                .map(ToOwned::to_owned)
                .collect(),
            managed_lmstudio,
            kafka_enabled: bool_value("VISN_KAFKA_ENABLED", false)?,
            #[cfg(feature = "kafka")]
            kafka_brokers: value("VISN_KAFKA_BROKERS", "127.0.0.1:9092"),
            #[cfg(feature = "kafka")]
            kafka_event_topic: value("VISN_KAFKA_EVENT_TOPIC", "event.observed.v1"),
            #[cfg(feature = "kafka")]
            kafka_report_topic: value("VISN_KAFKA_REPORT_TOPIC", "insight.completed.v1"),
        })
    }

    pub fn upload_dir(&self) -> PathBuf {
        self.data_dir.join("uploads")
    }

    pub fn pin_retrieval(&self, request: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        match &self.retrieval_expected_profile {
            Some(profile) => request.header("X-Visn-Expected-Profile", profile),
            None => request,
        }
    }

    pub fn verify_retrieval_profile(&self, profile: &serde_json::Value) -> anyhow::Result<()> {
        if let Some(expected) = &self.retrieval_expected_profile {
            anyhow::ensure!(
                profile["fingerprint"].as_str() == Some(expected.as_str()),
                "retrieval_profile_changed_or_unbound"
            );
        }
        Ok(())
    }

    pub fn detector_worker_idle_duration(&self) -> std::time::Duration {
        std::time::Duration::from_secs(self.detector_worker_idle_secs)
    }
}

fn memory_clip_mode() -> anyhow::Result<String> {
    let mode = value("VISN_MEMORY_CLIP_MODE", "copy").to_ascii_lowercase();
    if !matches!(mode.as_str(), "copy" | "transcode" | "reference") {
        bail!("VISN_MEMORY_CLIP_MODE must be copy, transcode, or reference");
    }
    Ok(mode)
}

fn positive_usize(name: &str, default: &str) -> anyhow::Result<usize> {
    let parsed = value(name, default)
        .parse::<usize>()
        .with_context(|| format!("{name} must be a positive integer"))?;
    if parsed == 0 {
        bail!("{name} must be greater than zero");
    }
    Ok(parsed)
}

fn nonnegative_f32(name: &str, default: &str) -> anyhow::Result<f32> {
    let parsed = value(name, default)
        .parse::<f32>()
        .with_context(|| format!("{name} must be a non-negative number"))?;
    if !parsed.is_finite() || parsed < 0.0 {
        bail!("{name} must be finite and zero or greater");
    }
    Ok(parsed)
}

fn nonnegative_u64(name: &str, default: &str) -> anyhow::Result<u64> {
    value(name, default)
        .parse::<u64>()
        .with_context(|| format!("{name} must be a non-negative integer"))
}

fn value(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_owned())
}

fn optional(name: &str) -> Option<String> {
    env::var(name).ok().filter(|value| !value.trim().is_empty())
}

fn bool_value(name: &str, default: bool) -> anyhow::Result<bool> {
    match env::var(name) {
        Ok(value) => value
            .parse()
            .with_context(|| format!("{name} must be true or false")),
        Err(_) => Ok(default),
    }
}

fn native_api_base_from_openai_base(openai_base: &str) -> String {
    let trimmed = openai_base.trim_end_matches('/');
    if let Some(root) = trimmed.strip_suffix("/v1") {
        format!("{root}/api/v1")
    } else {
        format!("{trimmed}/api/v1")
    }
}
