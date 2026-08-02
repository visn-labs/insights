use std::{env, path::PathBuf};

use anyhow::{bail, Context};

#[derive(Clone, Debug)]
pub struct Config {
    pub bind: String,
    pub data_dir: PathBuf,
    pub max_upload_bytes: usize,
    pub log_filter: String,
    pub gemma_base_url: String,
    pub gemma_model: Option<String>,
    pub gemma_api_key: String,
    pub gemma_timeout_secs: u64,
    pub detector_executable: String,
    pub detector_args: Vec<String>,
    pub yolo_model: String,
    pub max_analysis_secs: u64,
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
        let max_upload_mb = value("VISN_MAX_UPLOAD_MB", "2048")
            .parse::<usize>()
            .context("VISN_MAX_UPLOAD_MB must be a positive integer")?;
        if max_upload_mb == 0 {
            bail!("VISN_MAX_UPLOAD_MB must be greater than zero");
        }

        Ok(Self {
            bind: value("VISN_BIND", "127.0.0.1:8080"),
            data_dir: PathBuf::from(value("VISN_DATA_DIR", "./data")),
            max_upload_bytes: max_upload_mb.saturating_mul(1024 * 1024),
            log_filter: value("VISN_LOG", "visn_phase0=info,tower_http=info"),
            gemma_base_url: value("VISN_GEMMA_BASE_URL", "http://127.0.0.1:1234/v1")
                .trim_end_matches('/')
                .to_owned(),
            gemma_model: optional("VISN_GEMMA_MODEL"),
            gemma_api_key: value("VISN_GEMMA_API_KEY", "lm-studio"),
            gemma_timeout_secs: value("VISN_GEMMA_TIMEOUT_SECS", "120")
                .parse()
                .context("VISN_GEMMA_TIMEOUT_SECS must be an integer")?,
            detector_executable: value("VISN_DETECTOR_EXECUTABLE", "python3"),
            detector_args: value("VISN_DETECTOR_ARGS", "tools/yolo26_runner.py")
                .split_whitespace()
                .map(ToOwned::to_owned)
                .collect(),
            yolo_model: value("VISN_YOLO_MODEL", "yolo26s.pt"),
            max_analysis_secs: value("VISN_MAX_ANALYSIS_SECS", "120")
                .parse()
                .context("VISN_MAX_ANALYSIS_SECS must be an integer")?,
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
