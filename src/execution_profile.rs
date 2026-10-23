//! Immutable recording recipe identity. This is not hardware/model attestation.
use crate::{config::Config, domain::MemoryJobRequest};
use anyhow::{ensure, Context};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::Path, process::Stdio};

pub const VERSION: &str = "recording-execution-v1";
pub const TRACKING_VERSION: &str = "tracker-enrichment-v1";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionProfile {
    pub version: String,
    pub fingerprint: String,
    pub components: BTreeMap<String, String>,
}

impl ExecutionProfile {
    pub fn validate_tracking(
        &self,
        camera: &crate::domain::MemoryCameraResult,
        source_sha256: &str,
    ) -> anyhow::Result<()> {
        self.validate()?;
        ensure!(
            self.version == TRACKING_VERSION,
            "metadata_graph_profile_stage_mismatch"
        );
        for key in [
            "compiled-rust",
            "model:tracker",
            "detector-runtime",
            "settings",
            "detector-identity",
            "evidence",
        ] {
            ensure!(
                self.components
                    .get(key)
                    .is_some_and(|value| value.len() == 64
                        && value.bytes().all(|byte| byte.is_ascii_hexdigit())),
                "metadata_graph_profile_component_missing_or_invalid"
            );
        }
        let mut detector = self.components.clone();
        detector.remove("evidence");
        detector.remove("detector-identity");
        ensure!(
            self.components.get("detector-identity")
                == Some(&crate::metadata_projection::digest(&serde_json::to_vec(
                    &detector
                )?)),
            "metadata_graph_detector_identity_changed"
        );
        ensure!(
            self.components.get("evidence") == Some(&tracking_evidence(camera, source_sha256)?),
            "metadata_graph_profile_evidence_changed"
        );
        Ok(())
    }

    pub fn validate(&self) -> anyhow::Result<()> {
        ensure!(
            matches!(self.version.as_str(), VERSION | TRACKING_VERSION)
                && !self.components.is_empty()
                && self.fingerprint
                    == crate::metadata_projection::digest(&serde_json::to_vec(&(
                        &self.version,
                        &self.components
                    ))?),
            "execution_profile_invalid"
        );
        Ok(())
    }
}

async fn runtime(executable: &str, mode: &str) -> anyhow::Result<String> {
    let mut command = tokio::process::Command::new(executable);
    command
        .arg("-c")
        .arg(include_str!("../tools/execution_runtime.py"))
        .arg(mode)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let mut child = command
        .spawn()
        .context("execution_runtime_probe_unavailable")?;
    let mut stdout = child
        .stdout
        .take()
        .context("execution_runtime_probe_stdout")?;
    use tokio::io::AsyncReadExt;
    let data = tokio::time::timeout(std::time::Duration::from_secs(30), async {
        let mut bytes = Vec::new();
        (&mut stdout).take(65537).read_to_end(&mut bytes).await?;
        ensure!(bytes.len() <= 65536, "execution_runtime_probe_capacity");
        ensure!(
            child.wait().await?.success(),
            "execution_runtime_probe_failed"
        );
        Ok::<_, anyhow::Error>(bytes)
    })
    .await
    .context("execution_runtime_probe_deadline")??;
    let value: serde_json::Value = serde_json::from_slice(&data)?;
    ensure!(
        value["schema"] == "visn-execution-runtime-v1",
        "execution_runtime_probe_contract"
    );
    Ok(crate::metadata_projection::digest(&serde_json::to_vec(
        &value,
    )?))
}

fn tool_files(directory: &Path, files: &mut Vec<std::path::PathBuf>) -> anyhow::Result<()> {
    for entry in std::fs::read_dir(directory)? {
        let path = entry?.path();
        if path.is_symlink() {
            anyhow::bail!("execution_source_symlink");
        }
        if path.is_dir() {
            if path.file_name().is_some_and(|name| name == "__pycache__") {
                continue;
            }
            tool_files(&path, files)?;
        } else if path.extension().is_some_and(|ext| ext == "py") {
            ensure!(
                files.len() < 1024 && path.metadata()?.len() <= 8 * 1024 * 1024,
                "execution_source_capacity"
            );
            files.push(path);
        }
    }
    Ok(())
}

async fn code_components(
    config: &Config,
    components: &mut BTreeMap<String, String>,
    include_observer: bool,
) -> anyhow::Result<()> {
    let mut files = Vec::new();
    tool_files(Path::new("tools"), &mut files)?;
    files.sort();
    for path in files {
        components.insert(
            format!("code:{}", path.display()),
            crate::privacy_media::file_hash(&path).await?,
        );
    }
    for (index, argument) in config
        .detector_args
        .iter()
        .chain(&config.detector_worker_args)
        .chain(&config.resource_runner_args)
        .chain(
            config
                .memory_runner_args
                .iter()
                .filter(|_| include_observer),
        )
        .enumerate()
    {
        if Path::new(argument)
            .extension()
            .is_some_and(|ext| ext == "py")
        {
            components.insert(
                format!("configured-script:{index}"),
                crate::privacy_media::file_hash(Path::new(argument)).await?,
            );
        }
    }
    Ok(())
}

pub fn tracking_evidence(
    camera: &crate::domain::MemoryCameraResult,
    source_sha256: &str,
) -> anyhow::Result<String> {
    Ok(crate::metadata_projection::digest(&serde_json::to_vec(&(
        &camera.camera.camera_id,
        &camera.source_session_id,
        camera.storage_session_id,
        camera.capture_extent,
        camera.duration_ms,
        source_sha256,
        camera
            .events
            .first()
            .and_then(|event| event.privacy.as_ref())
            .map(|privacy| &privacy.source),
        camera
            .events
            .iter()
            .map(|event| (event.job_id, event.event_id, event.start_ms, event.end_ms))
            .collect::<Vec<_>>(),
    ))?))
}

pub async fn tracking_snapshot(
    config: &Config,
    camera: &crate::domain::MemoryCameraResult,
    source_sha256: &str,
) -> anyhow::Result<ExecutionProfile> {
    let mut components = BTreeMap::new();
    components.insert(
        "compiled-rust".into(),
        env!("VISN_EXECUTION_CODE_SHA256").into(),
    );
    code_components(config, &mut components, false).await?;
    components.insert(
        "model:tracker".into(),
        crate::privacy_media::file_hash(Path::new(&config.yolo_model)).await?,
    );
    components.insert(
        "detector-runtime".into(),
        runtime(&config.detector_executable, "detector").await?,
    );
    let settings = serde_json::json!({
        "executable":config.detector_executable,"args":config.detector_args,"worker_args":config.detector_worker_args,
        "model":config.yolo_model,"image_size":config.detector_image_size,"device":config.detector_device,
        "threads":config.detector_threads,"persistent":config.persistent_detector,"fallback":config.persistent_detector_fallback,
        "batch_size":config.detector_batch_size,"batch_wait_ms":config.detector_batch_wait_ms,
        "appearance_interval_secs":config.appearance_interval_secs,"resource_args":config.resource_runner_args,
        "resource_dir":config.resource_dir,"detector_fps":5.,"confidence":0.25,"appearance_mode":"off"
    });
    components.insert(
        "settings".into(),
        crate::metadata_projection::digest(&serde_json::to_vec(&settings)?),
    );
    let detector = crate::metadata_projection::digest(&serde_json::to_vec(&components)?);
    components.insert("detector-identity".into(), detector);
    components.insert("evidence".into(), tracking_evidence(camera, source_sha256)?);
    let fingerprint =
        crate::metadata_projection::digest(&serde_json::to_vec(&(TRACKING_VERSION, &components))?);
    Ok(ExecutionProfile {
        version: TRACKING_VERSION.into(),
        fingerprint,
        components,
    })
}

pub async fn snapshot(
    config: &Config,
    request: &MemoryJobRequest,
) -> anyhow::Result<ExecutionProfile> {
    let mut components = BTreeMap::new();
    components.insert(
        "compiled-rust".into(),
        env!("VISN_EXECUTION_CODE_SHA256").into(),
    );
    let settings = serde_json::json!({
        "media_policy":config.media_policy,"memory_runner_args":config.memory_runner_args,
        "clip_mode":config.memory_clip_mode,"max_events":config.max_memory_events_per_camera,
        "privacy_python":config.privacy_python,"privacy_model_dir":config.privacy_model_dir,
        "detector_executable":config.detector_executable,"detector_args":config.detector_args,
        "detector_worker_args":config.detector_worker_args,"yolo_model":config.yolo_model,
        "persistent_detector":config.persistent_detector,"persistent_detector_fallback":config.persistent_detector_fallback,
        "detector_image_size":config.detector_image_size,"detector_device":config.detector_device,
        "detector_threads":config.detector_threads,"batch_size":config.detector_batch_size,
        "batch_wait_ms":config.detector_batch_wait_ms,"appearance_interval_secs":config.appearance_interval_secs,
        "max_vlm_events":config.max_vlm_events_per_camera,"vlm_model":config.gemma_model,
        "vlm_endpoint":config.gemma_base_url,"vlm_context":config.vlm_context_length,
        "vlm_batch":config.vlm_eval_batch_size,"vlm_tokens":config.vlm_max_output_tokens,
        "vlm_flash_attention":config.vlm_flash_attention,"vlm_kv_gpu":config.vlm_offload_kv_cache_to_gpu,
        "capture_sessions":request.capture_sessions,"long_memory":request.long_memory_enabled,
        "vlm":request.vlm_enabled,"temporal":request.temporal_enabled,"requested_model":request.vlm_model,
        "duration_secs":request.monitor_duration_secs,"observer_fps":request.observer_fps,
        "cluster_id":request.cluster_id,"camera_ids":request.cameras.iter().map(|camera| &camera.camera_id).collect::<Vec<_>>()
    });
    components.insert(
        "settings".into(),
        crate::metadata_projection::digest(&serde_json::to_vec(&settings)?),
    );
    code_components(config, &mut components, true).await?;
    let lock: serde_json::Value =
        serde_json::from_slice(&tokio::fs::read("tools/privacy_models.lock.json").await?)?;
    components.insert(
        "privacy-model-lock".into(),
        crate::privacy_media::file_hash(Path::new("tools/privacy_models.lock.json")).await?,
    );
    for kind in ["yunet", "plate"] {
        let model = &lock["models"][kind];
        let name = model["file"]
            .as_str()
            .context("execution_model_lock_invalid")?;
        ensure!(
            Path::new(name).file_name().and_then(|v| v.to_str()) == Some(name),
            "execution_model_lock_invalid"
        );
        let hash = crate::privacy_media::file_hash(&config.privacy_model_dir.join(name)).await?;
        ensure!(
            Some(hash.as_str()) == model["sha256"].as_str(),
            "execution_privacy_model_changed"
        );
        components.insert(format!("model:{kind}"), hash);
    }
    components.insert(
        "privacy-runtime".into(),
        runtime(&config.privacy_python, "privacy").await?,
    );
    if request.long_memory_enabled {
        components.insert(
            "model:tracker".into(),
            crate::privacy_media::file_hash(Path::new(&config.yolo_model)).await?,
        );
        components.insert(
            "detector-runtime".into(),
            runtime(&config.detector_executable, "detector").await?,
        );
    }
    let fingerprint =
        crate::metadata_projection::digest(&serde_json::to_vec(&(VERSION, &components))?);
    Ok(ExecutionProfile {
        version: VERSION.into(),
        fingerprint,
        components,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recipe_identity_is_versioned_and_any_component_change_is_detected() {
        let components = BTreeMap::from([
            ("runtime".into(), "a".repeat(64)),
            ("model".into(), "b".repeat(64)),
        ]);
        let fingerprint = crate::metadata_projection::digest(
            &serde_json::to_vec(&(VERSION, &components)).unwrap(),
        );
        let original = ExecutionProfile {
            version: VERSION.into(),
            fingerprint,
            components,
        };
        original.validate().unwrap();
        let mut tracking = original.clone();
        tracking.version = TRACKING_VERSION.into();
        tracking.fingerprint = crate::metadata_projection::digest(
            &serde_json::to_vec(&(TRACKING_VERSION, &tracking.components)).unwrap(),
        );
        tracking.validate().unwrap();
        assert_ne!(tracking.fingerprint, original.fingerprint);
        let restored: ExecutionProfile =
            serde_json::from_slice(&serde_json::to_vec(&original).unwrap()).unwrap();
        assert_eq!(restored, original);
        let mut changed = original.clone();
        changed.components.insert("model".into(), "c".repeat(64));
        assert!(changed.validate().is_err());
        changed.fingerprint = crate::metadata_projection::digest(
            &serde_json::to_vec(&(VERSION, &changed.components)).unwrap(),
        );
        changed.validate().unwrap();
        assert_ne!(changed, original);
        changed.version = "future-version".into();
        assert!(changed.validate().is_err());
    }
}
