//! Sanitized-media commit boundary for the real worker, not mask-only telemetry.
use anyhow::{ensure, Context};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    sync::Arc,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    process::Command,
};

use crate::config::Config;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct SanitizedMedia {
    // Preserve the complete committed producer recipe, including future fields.
    // Dropping unknown fields here silently weakens every later equality check.
    #[serde(flatten)]
    pub producer_metadata: std::collections::BTreeMap<String, serde_json::Value>,
    #[serde(default)]
    pub capture: Option<CaptureTimeline>,
    #[serde(default)]
    pub clock_kind: Option<String>,
    #[serde(default)]
    pub clock_limitations: Vec<String>,
    pub schema: String,
    pub policy: String,
    pub qualification: String,
    pub media_sha256: String,
    pub media_bytes: u64,
    pub frames: usize,
    pub width: usize,
    pub height: usize,
    pub ledger_sha256: String,
    pub audio: String,
}

impl SanitizedMedia {
    fn validate_producer(&self) -> anyhow::Result<()> {
        let metadata = &self.producer_metadata;
        let hash = |value: Option<&serde_json::Value>| {
            value
                .and_then(|v| v.as_str())
                .is_some_and(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
        };
        ensure!(
            hash(metadata.get("sanitizer_code_sha256"))
                && hash(metadata.get("tracker_code_sha256"))
                && metadata.get("sanitizer").and_then(|v| v.as_str())
                    == Some("solid_pixel_replacement")
                && metadata
                    .get("detector_threshold")
                    .and_then(|v| v.as_f64())
                    .is_some_and(|v| v.is_finite() && v > 0. && v <= 1.)
                && metadata.get("timestamp_policy").and_then(|v| v.as_str())
                    == Some("source_PTS_recorded; relative_PTS_rounded_to_ms; collisions_rejected")
                && metadata
                    .get("max_timestamp_quantization_ms")
                    .and_then(|v| v.as_f64())
                    == Some(0.5),
            "privacy_producer_profile_missing_or_invalid"
        );
        let models = metadata
            .get("models")
            .and_then(|v| v.as_object())
            .context("privacy_producer_models_missing")?;
        for kind in ["yunet", "plate"] {
            let model = models.get(kind).context("privacy_producer_model_missing")?;
            ensure!(
                hash(model.get("sha256"))
                    && model
                        .get("bytes")
                        .and_then(|v| v.as_u64())
                        .is_some_and(|v| v > 0)
                    && model.get("file").and_then(|v| v.as_str()).is_some_and(|v| {
                        !v.is_empty()
                            && Path::new(v).file_name().and_then(|p| p.to_str()) == Some(v)
                    }),
                "privacy_producer_model_invalid"
            );
        }
        let runtime = metadata
            .get("runtime")
            .context("privacy_producer_runtime_missing")?;
        for key in ["opencv", "onnxruntime", "pyav", "numpy"] {
            ensure!(
                runtime
                    .get(key)
                    .and_then(|v| v.as_str())
                    .is_some_and(|v| !v.is_empty() && v.len() <= 128),
                "privacy_producer_runtime_invalid"
            );
        }
        ensure!(
            runtime.get("precision").and_then(|v| v.as_str()) == Some("FP32")
                && runtime
                    .get("threads")
                    .and_then(|v| v.as_u64())
                    .is_some_and(|v| (1..=32).contains(&v))
                && runtime
                    .get("providers")
                    .and_then(|v| v.as_array())
                    .is_some_and(|v| {
                        !v.is_empty()
                            && v.len() <= 32
                            && v.iter().all(|provider| {
                                provider
                                    .as_str()
                                    .is_some_and(|s| !s.is_empty() && s.len() <= 128)
                            })
                    }),
            "privacy_producer_runtime_invalid"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct CaptureTimeline {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub association_sessions: Vec<AssociationSession>,
    pub session_id: String,
    pub requested_seconds: f64,
    pub termination: String,
    pub observed_frames: usize,
    pub last_observed_frame_ms: u64,
    pub transport_started_wall_ms: u64,
    pub transport_finished_wall_ms: u64,
    pub transport_time_is_not_capture_time: bool,
    pub continuity_certified: bool,
    pub timestamp_jump_threshold_ms: u64,
    pub timestamp_jump_count: usize,
    pub timestamp_jump_samples: Vec<[u64; 2]>,
    pub gap_interpretation: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct AssociationSession {
    pub session_id: String,
    pub first_frame_index: usize,
    pub last_frame_index: usize,
    pub first_observed_ms: u64,
    pub last_observed_ms: u64,
    pub start_reason: String,
}

impl CaptureTimeline {
    fn validate_sessions(&self, frames: usize) -> anyhow::Result<()> {
        // Empty means legacy/unavailable, never a claim of one continuous session.
        if self.association_sessions.is_empty() {
            return Ok(());
        }
        ensure!(
            self.association_sessions.len() <= 256
                && self.observed_frames == frames
                && self.timestamp_jump_threshold_ms == 1000
                && self.timestamp_jump_count == self.association_sessions.len() - 1,
            "privacy_session_contract"
        );
        let mut next_frame = 0;
        let mut previous_time = None;
        for (index, session) in self.association_sessions.iter().enumerate() {
            ensure!(
                session.session_id == format!("{}:{index}", self.session_id)
                    && session.first_frame_index == next_frame
                    && session.last_frame_index >= session.first_frame_index
                    && session.last_frame_index < frames
                    && session.first_observed_ms <= session.last_observed_ms
                    && session.start_reason
                        == if index == 0 {
                            "initial_capture"
                        } else {
                            "observed_timestamp_spacing"
                        },
                "privacy_session_contract"
            );
            if let Some(previous) = previous_time {
                ensure!(
                    session
                        .first_observed_ms
                        .checked_sub(previous)
                        .is_some_and(|gap| gap > 1000),
                    "privacy_session_spacing"
                );
            } else {
                ensure!(session.first_observed_ms == 0, "privacy_session_origin");
            }
            next_frame = session.last_frame_index + 1;
            previous_time = Some(session.last_observed_ms);
        }
        ensure!(
            next_frame == frames && previous_time == Some(self.last_observed_frame_ms),
            "privacy_session_coverage"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SanitizedEventEvidence {
    pub source: SanitizedMedia,
    pub thumbnail_sha256: String,
    pub clip_sha256: String,
}

#[cfg(test)]
mod session_tests {
    use super::*;

    fn producer_fixture() -> serde_json::Value {
        let lock: serde_json::Value =
            serde_json::from_str(include_str!("../tools/privacy_models.lock.json")).unwrap();
        serde_json::json!({
            "schema":"visn-sanitized-media-v1", "policy":"sanitized-pixels-v1",
            "qualification":"integration_candidate_not_general_privacy_acceptance",
            "media_sha256":"a".repeat(64), "media_bytes":1, "frames":1,
            "width":128, "height":128, "ledger_sha256":"b".repeat(64), "audio":"removed",
            "sanitizer_code_sha256":"c".repeat(64), "tracker_code_sha256":"d".repeat(64),
            "models":lock["models"], "detector_threshold":0.1, "sanitizer":"solid_pixel_replacement",
            "timestamp_policy":"source_PTS_recorded; relative_PTS_rounded_to_ms; collisions_rejected",
            "max_timestamp_quantization_ms":0.5,
            "runtime":{"opencv":"4.14.0","onnxruntime":"1.31.0","pyav":"19.0.1","numpy":"2.5.4",
                "precision":"FP32","providers":["CPUExecutionProvider"],"threads":1},
            "future_producer_field":{"version":"candidate-2","enabled":true}
        })
    }

    #[test]
    fn complete_producer_recipe_survives_canonical_roundtrip_and_detects_changes() {
        let original = producer_fixture();
        let pinned: SanitizedMedia = serde_json::from_value(original.clone()).unwrap();
        pinned.validate_producer().unwrap();
        let saved = serde_json::to_value(&pinned).unwrap();
        for key in [
            "models",
            "runtime",
            "sanitizer_code_sha256",
            "tracker_code_sha256",
            "detector_threshold",
            "timestamp_policy",
            "future_producer_field",
        ] {
            assert_eq!(saved[key], original[key]);
        }
        for pointer in [
            "/models/yunet/sha256",
            "/models/plate/sha256",
            "/sanitizer_code_sha256",
            "/tracker_code_sha256",
        ] {
            let mut changed = original.clone();
            *changed.pointer_mut(pointer).unwrap() = serde_json::json!("e".repeat(64));
            let changed: SanitizedMedia = serde_json::from_value(changed).unwrap();
            changed.validate_producer().unwrap();
            assert_ne!(
                pinned, changed,
                "producer changes cannot disappear: {pointer}"
            );
        }
        // Both historical and newer versions remain explicit; current runtime
        // configuration never rewrites the saved producer recipe on read.
        let mut old = original.clone();
        old["runtime"]["opencv"] = serde_json::json!("4.10.0");
        let old: SanitizedMedia = serde_json::from_value(old).unwrap();
        old.validate_producer().unwrap();
        assert_ne!(old, pinned);
        let mut changed = original.clone();
        changed["detector_threshold"] = serde_json::json!(0.2);
        assert_ne!(
            pinned,
            serde_json::from_value::<SanitizedMedia>(changed).unwrap()
        );
        let mut changed = original;
        changed["future_producer_field"]["enabled"] = serde_json::json!(false);
        assert_ne!(
            pinned,
            serde_json::from_value::<SanitizedMedia>(changed).unwrap()
        );
    }

    #[test]
    fn incomplete_or_unsupported_producer_profiles_are_not_implicitly_upgraded() {
        let complete = producer_fixture();
        for key in [
            "models",
            "runtime",
            "sanitizer_code_sha256",
            "tracker_code_sha256",
            "detector_threshold",
            "timestamp_policy",
            "max_timestamp_quantization_ms",
        ] {
            let mut legacy = complete.clone();
            legacy.as_object_mut().unwrap().remove(key);
            let legacy: SanitizedMedia = serde_json::from_value(legacy).unwrap();
            assert!(legacy.validate_producer().is_err(), "missing {key}");
        }
        for (pointer, value) in [
            ("/runtime/precision", serde_json::json!("FP16")),
            ("/runtime/threads", serde_json::json!(0)),
            ("/runtime/providers", serde_json::json!([])),
            ("/models/plate/sha256", serde_json::json!("unknown")),
            ("/models/plate/file", serde_json::json!("../outside.onnx")),
            ("/detector_threshold", serde_json::json!(0.)),
        ] {
            let mut invalid = complete.clone();
            *invalid.pointer_mut(pointer).unwrap() = value;
            let invalid: SanitizedMedia = serde_json::from_value(invalid).unwrap();
            assert!(invalid.validate_producer().is_err(), "invalid {pointer}");
        }
    }

    #[test]
    fn session_ranges_are_ordered_exhaustive_and_legacy_is_explicit() {
        let mut capture: CaptureTimeline = serde_json::from_value(serde_json::json!({
            "session_id":"owned", "requested_seconds":3.0, "termination":"end_of_stream",
            "observed_frames":3, "last_observed_frame_ms":2500,
            "transport_started_wall_ms":1, "transport_finished_wall_ms":2,
            "transport_time_is_not_capture_time":true, "continuity_certified":false,
            "timestamp_jump_threshold_ms":1000, "timestamp_jump_count":1,
            "timestamp_jump_samples":[[100,2500]], "gap_interpretation":"uncertain spacing",
            "association_sessions":[
                {"session_id":"owned:0","first_frame_index":0,"last_frame_index":1,
                 "first_observed_ms":0,"last_observed_ms":100,"start_reason":"initial_capture"},
                {"session_id":"owned:1","first_frame_index":2,"last_frame_index":2,
                 "first_observed_ms":2500,"last_observed_ms":2500,"start_reason":"observed_timestamp_spacing"}
            ]
        })).unwrap();
        capture.validate_sessions(3).unwrap();
        let original = capture.clone();
        capture.association_sessions[1].first_frame_index = 1;
        assert!(capture.validate_sessions(3).is_err());
        capture = original.clone();
        capture.association_sessions[1].first_observed_ms = 500;
        assert!(capture.validate_sessions(3).is_err());
        capture = original.clone();
        capture.association_sessions[1].session_id = "owned:0".into();
        assert!(capture.validate_sessions(3).is_err());
        capture = original;
        capture.association_sessions.clear();
        capture.validate_sessions(3).unwrap();
        assert!(serde_json::to_value(capture)
            .unwrap()
            .get("association_sessions")
            .is_none());
    }
}

pub fn manifest_path(path: &Path) -> PathBuf {
    PathBuf::from(format!("{}.privacy.json", path.display()))
}

pub async fn file_hash(path: &Path) -> anyhow::Result<String> {
    let mut file = tokio::fs::File::open(path).await?;
    let mut hash = Sha256::new();
    let mut buffer = vec![0; 128 * 1024];
    loop {
        let count = file.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

pub fn playback_path(source: &Path) -> PathBuf {
    PathBuf::from(format!("{}.playback.mp4", source.display()))
}

pub async fn verify_playback(source: &Path) -> anyhow::Result<PathBuf> {
    let canonical = verify(source).await?;
    let path = playback_path(source);
    let marker: serde_json::Value =
        serde_json::from_slice(&tokio::fs::read(manifest_path(&path)).await?)?;
    ensure!(
        marker["schema"] == "visn-sanitized-playback-v1"
            && marker["source_sha256"] == canonical.media_sha256
            && marker["media_sha256"] == file_hash(&path).await?
            && marker["audio"] == "removed"
            && marker["codec"] == "h264",
        "invalid_privacy_playback"
    );
    Ok(path)
}

pub async fn render_playback(config: &Arc<Config>, source: &Path) -> anyhow::Result<()> {
    verify(source).await?;
    let mut command = Command::new(&config.privacy_python);
    command
        .arg("tools/privacy_media.py")
        .arg("--playback")
        .arg("--source")
        .arg(source)
        .arg("--output")
        .arg(playback_path(source))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let lifeline = crate::durable::isolate_worker(&mut command)?;
    let mut child = command.spawn().context("launch_privacy_playback")?;
    let mut group = crate::durable::WorkerGroup::new(child.id(), lifeline);
    let status = tokio::time::timeout(std::time::Duration::from_secs(600), child.wait())
        .await
        .context("privacy_playback_deadline")??;
    group.finish();
    ensure!(status.success(), "privacy_playback_failed");
    verify_playback(source).await?;
    Ok(())
}

pub async fn verify(path: &Path) -> anyhow::Result<SanitizedMedia> {
    ensure!(
        tokio::fs::metadata(manifest_path(path)).await?.len() <= 64 * 1024,
        "privacy_manifest_size_limit"
    );
    let data = tokio::fs::read(manifest_path(path))
        .await
        .context("privacy_commit_missing")?;
    ensure!(data.len() <= 64 * 1024, "privacy_manifest_size_limit");
    let manifest: SanitizedMedia = serde_json::from_slice(&data)?;
    manifest.validate_producer()?;
    if let Some(capture) = &manifest.capture {
        capture.validate_sessions(manifest.frames)?;
    }
    ensure!(
        manifest.schema == "visn-sanitized-media-v1"
            && manifest.policy == "sanitized-pixels-v1"
            && manifest.audio == "removed"
            && manifest.frames > 0,
        "privacy_manifest_contract"
    );
    ensure!(
        manifest.width > 0
            && manifest.height > 0
            && manifest
                .width
                .checked_mul(manifest.height)
                .is_some_and(|v| v <= 16_777_216)
            && tokio::fs::metadata(path).await?.len() == manifest.media_bytes,
        "privacy_media_geometry_or_size"
    );
    ensure!(
        file_hash(path).await? == manifest.media_sha256,
        "privacy_media_changed"
    );
    let ledger = PathBuf::from(format!("{}.frames.jsonl", path.display()));
    ensure!(
        file_hash(&ledger).await? == manifest.ledger_sha256,
        "privacy_ledger_changed"
    );
    Ok(manifest)
}

/// Shared admission before any model/index worker opens retained camera evidence.
pub async fn verify_camera(
    camera: &crate::domain::MemoryCameraResult,
) -> anyhow::Result<SanitizedMedia> {
    let first = camera.events.first().context("camera_has_no_evidence")?;
    let actual = verify(&first.source_evidence_path).await?;
    ensure!(
        camera.storage_session_matches_provenance(),
        "camera_storage_scope_changed"
    );
    for event in &camera.events {
        let expected = event
            .privacy
            .as_ref()
            .context("camera_source_not_sanitized")?;
        ensure!(
            event.source_evidence_path == first.source_evidence_path
                && event.source_session_id == camera.source_session_id
                && event.capture_extent == camera.capture_extent
                && event.source_session_matches_provenance()
                && expected.source == actual,
            "camera_privacy_source_changed"
        );
    }
    Ok(actual)
}

/// Source URL/path arrives over stdin so camera credentials never enter argv.
pub async fn sanitize(
    config: &Arc<Config>,
    source: Option<&str>,
    encoded: Option<Vec<u8>>,
    output: &Path,
    seconds: u64,
) -> anyhow::Result<SanitizedMedia> {
    ensure!(
        config.media_policy == "sanitized-v1",
        "sanitizer_requires_strict_profile"
    );
    let mut command = Command::new(&config.privacy_python);
    command
        .arg("tools/privacy_media.py")
        .arg("--source")
        .arg(if source.is_some() { "@stdin-url" } else { "-" })
        .arg("--output")
        .arg(output)
        .arg("--seconds")
        .arg(seconds.to_string())
        .arg("--model-dir")
        .arg(&config.privacy_model_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let lifeline = crate::durable::isolate_worker(&mut command)?;
    let mut child = command.spawn().context("launch_privacy_worker")?;
    let mut group = crate::durable::WorkerGroup::new(child.id(), lifeline);
    let mut input = child.stdin.take().context("privacy_worker_stdin")?;
    let operation = async {
        if let Some(source) = source {
            ensure!(
                !source.contains(['\r', '\n']) && source.len() <= 16384,
                "invalid_privacy_source"
            );
            input.write_all(source.as_bytes()).await?;
            input.write_all(b"\n").await?;
        } else {
            let encoded = encoded.context("missing_privacy_encoded_input")?;
            ensure!(
                encoded.len() <= 64 * 1024 * 1024,
                "privacy_encoded_input_capacity"
            );
            input.write_all(&encoded).await?;
        }
        drop(input);
        let status = child.wait().await?;
        group.finish();
        ensure!(status.success(), "privacy_processing_blocked: detector/decode/verification failed; raw fallback prohibited");
        verify(output).await
    };
    tokio::time::timeout(
        std::time::Duration::from_secs(120 + seconds.saturating_mul(10)),
        operation,
    )
    .await
    .context("privacy_worker_deadline")?
}
