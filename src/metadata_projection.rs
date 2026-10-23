//! Rebuildable, evidence-linked graph and exact interval metadata. No inferred footfall.
use crate::{
    domain::{MemoryCameraResult, MemoryJobRecord},
    long_memory,
};
use anyhow::{ensure, Context};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use uuid::Uuid;

pub const VERSION: &str = "evidence-metadata-v1";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ProjectionIntent {
    pub revision: Uuid,
    pub version: String,
    pub input_fingerprint: String,
    pub state: String,
    pub output_sha256: Option<String>,
    pub attempts: u32,
    pub next_attempt_at_ms: u64,
    pub last_error: Option<String>,
}

pub fn queued(job: &MemoryJobRecord, media_policy: &str) -> ProjectionIntent {
    intent(job, media_policy).unwrap_or_else(|error| ProjectionIntent {
        revision: Uuid::now_v7(),
        version: VERSION.into(),
        input_fingerprint: String::new(),
        state: "blocked".into(),
        output_sha256: None,
        attempts: 0,
        next_attempt_at_ms: 0,
        last_error: Some(error.to_string()),
    })
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Interval {
    pub event_id: Uuid,
    pub start_ms: u64,
    pub end_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CameraProjection {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_provenance_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capture_extent: Option<crate::domain::CaptureExtent>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub camera_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_session_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capture_timeline: Option<crate::privacy_media::CaptureTimeline>,
    pub duration_ms: u64,
    pub source_sha256: Option<String>,
    pub intervals: Vec<Interval>,
    pub graph: Option<long_memory::EntityMemory>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Document {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub execution_profile_fingerprint: Option<String>,
    pub version: String,
    pub job_id: Uuid,
    pub revision: Uuid,
    pub media_policy: String,
    pub input_fingerprint: String,
    pub cameras: BTreeMap<String, CameraProjection>,
}

pub fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn camera_payload(job: &MemoryJobRecord) -> anyhow::Result<BTreeMap<String, CameraProjection>> {
    let mut cameras = BTreeMap::new();
    let mut ids = HashSet::new();
    let mut storage_sessions = HashSet::new();
    for camera in &job
        .result
        .as_ref()
        .context("metadata_recording_has_no_result")?
        .camera_results
    {
        ensure!(
            camera.storage_session_matches_provenance(),
            "metadata_storage_session_scope_mismatch"
        );
        ensure!(
            camera
                .storage_session_id
                .is_none_or(|id| storage_sessions.insert((camera.camera.camera_id.clone(), id))),
            "metadata_duplicate_storage_session"
        );
        let key = crate::domain::memory_projection_key(
            &camera.camera.camera_id,
            camera.source_session_id.as_deref(),
        );
        ensure!(!cameras.contains_key(&key), "metadata_duplicate_camera");
        let mut intervals = Vec::new();
        for event in &camera.events {
            ensure!(
                event.job_id == job.id
                    && event.camera_id == camera.camera.camera_id
                    && event.capture_extent == camera.capture_extent
                    && event.source_session_id == camera.source_session_id,
                "metadata_event_scope_mismatch"
            );
            ensure!(
                event.source_session_matches_provenance(),
                "metadata_source_session_provenance_changed"
            );
            ensure!(ids.insert(event.event_id), "metadata_duplicate_event");
            ensure!(
                event.start_ms < event.end_ms && event.end_ms <= camera.duration_ms,
                "metadata_invalid_interval"
            );
            intervals.push(Interval {
                event_id: event.event_id,
                start_ms: event.start_ms,
                end_ms: event.end_ms,
            });
        }
        if let Some(graph) = &camera.long_memory {
            validate_graph(job.id, camera, graph)?;
        }
        cameras.insert(
            key,
            CameraProjection {
                source_provenance_sha256: camera
                    .events
                    .first()
                    .and_then(|e| e.privacy.as_ref())
                    .filter(|p| !p.source.producer_metadata.is_empty())
                    .map(|p| serde_json::to_vec(&p.source).map(|bytes| digest(&bytes)))
                    .transpose()?,
                capture_extent: camera.capture_extent,
                camera_id: camera
                    .source_session_id
                    .as_ref()
                    .map(|_| camera.camera.camera_id.clone()),
                source_session_id: camera.source_session_id.clone(),
                capture_timeline: camera
                    .events
                    .first()
                    .and_then(|e| e.privacy.as_ref())
                    .and_then(|p| p.source.capture.clone())
                    .filter(|capture| !capture.association_sessions.is_empty()),
                duration_ms: camera.duration_ms,
                source_sha256: camera
                    .events
                    .first()
                    .and_then(|e| e.privacy.as_ref())
                    .map(|p| p.source.media_sha256.clone()),
                intervals,
                graph: camera.long_memory.clone(),
            },
        );
    }
    Ok(cameras)
}

pub(crate) fn validate_graph(
    job: Uuid,
    camera: &MemoryCameraResult,
    graph: &long_memory::EntityMemory,
) -> anyhow::Result<()> {
    if let Some(profile) = &graph.execution_profile {
        profile.validate_tracking(camera, &graph.source_sha256)?;
    }
    let events: BTreeMap<_, _> = camera.events.iter().map(|e| (e.event_id, e)).collect();
    let mut entities = HashSet::new();
    let mut observations = BTreeMap::new();
    let sessions = camera
        .events
        .first()
        .and_then(|event| event.privacy.as_ref())
        .and_then(|privacy| privacy.source.capture.as_ref())
        .map(|capture| capture.association_sessions.as_slice())
        .unwrap_or_default();
    for entity in &graph.entities {
        let mut entity_session = None;
        ensure!(
            entity.job_id == job
                && entity.camera_id == camera.camera.camera_id
                && entity.source_session_id == camera.source_session_id
                && entities.insert(entity.entity_id),
            "metadata_entity_scope_mismatch"
        );
        for point in &entity.observations {
            if !sessions.is_empty() {
                let index = sessions
                    .iter()
                    .position(|session| {
                        point.timestamp_ms >= session.first_observed_ms
                            && point.timestamp_ms <= session.last_observed_ms
                    })
                    .context("metadata_observation_outside_capture_session")?;
                ensure!(
                    entity_session.is_none_or(|previous| previous == index),
                    "metadata_entity_crosses_capture_sessions"
                );
                entity_session = Some(index);
            }
            let event = events
                .get(&point.event_id)
                .context("metadata_observation_event_missing")?;
            ensure!(
                point.timestamp_ms >= event.start_ms
                    && point.timestamp_ms < event.end_ms
                    && observations
                        .insert(point.observation_id, (entity.entity_id, point))
                        .is_none(),
                "metadata_invalid_observation"
            );
        }
    }
    for edge in &graph.links {
        let from = observations
            .get(&edge.from)
            .context("metadata_link_source_missing")?;
        let to = observations
            .get(&edge.to)
            .context("metadata_link_target_missing")?;
        ensure!(
            from.0 == edge.entity_id
                && to.0 == edge.entity_id
                && entities.contains(&edge.entity_id)
                && from.1.timestamp_ms <= to.1.timestamp_ms
                && edge.gap_ms == to.1.timestamp_ms - from.1.timestamp_ms,
            "metadata_link_scope_mismatch"
        );
    }
    if let Some(source) = camera
        .events
        .first()
        .and_then(|event| event.privacy.as_ref())
    {
        ensure!(
            graph.source_sha256 == source.source.media_sha256,
            "metadata_graph_source_changed"
        );
    }
    Ok(())
}

fn input_fingerprint(
    job: &MemoryJobRecord,
    media_policy: &str,
    cameras: &BTreeMap<String, CameraProjection>,
) -> anyhow::Result<String> {
    Ok(match &job.execution_profile {
        Some(profile) => {
            profile.validate()?;
            ensure!(
                profile.version == crate::execution_profile::VERSION,
                "metadata_recording_profile_stage_mismatch"
            );
            digest(&serde_json::to_vec(&(
                job.id,
                media_policy,
                &profile.fingerprint,
                cameras,
            ))?)
        }
        None => digest(&serde_json::to_vec(&(job.id, media_policy, cameras))?),
    })
}

pub fn intent(job: &MemoryJobRecord, media_policy: &str) -> anyhow::Result<ProjectionIntent> {
    let fingerprint = input_fingerprint(job, media_policy, &camera_payload(job)?)?;
    Ok(ProjectionIntent {
        revision: Uuid::now_v7(),
        version: VERSION.into(),
        input_fingerprint: fingerprint,
        state: "pending".into(),
        output_sha256: None,
        attempts: 0,
        next_attempt_at_ms: 0,
        last_error: None,
    })
}

pub fn document(job: &MemoryJobRecord, media_policy: &str) -> anyhow::Result<Document> {
    ensure!(job.revocation.is_none(), "recording_revoked");
    ensure!(
        job.status == crate::domain::JobStatus::Completed,
        "metadata_projection_not_ready"
    );
    let pending = job
        .metadata_projection
        .as_ref()
        .context("metadata_projection_not_ready")?;
    ensure!(
        pending.version == VERSION,
        "metadata_projection_version_mismatch"
    );
    let cameras = camera_payload(job)?;
    let input_fingerprint = input_fingerprint(job, media_policy, &cameras)?;
    ensure!(
        pending.input_fingerprint == input_fingerprint,
        "metadata_projection_input_changed"
    );
    Ok(Document {
        execution_profile_fingerprint: job
            .execution_profile
            .as_ref()
            .map(|profile| profile.fingerprint.clone()),
        version: VERSION.into(),
        job_id: job.id,
        revision: pending.revision,
        media_policy: media_policy.into(),
        input_fingerprint,
        cameras,
    })
}

pub fn verify(job: &MemoryJobRecord, media_policy: &str, bytes: &[u8]) -> anyhow::Result<Document> {
    let expected = document(job, media_policy)?;
    let pending = job.metadata_projection.as_ref().unwrap();
    ensure!(
        pending.state == "completed"
            && pending.output_sha256.as_deref() == Some(digest(bytes).as_str()),
        "metadata_projection_artifact_changed"
    );
    let parsed: Document =
        serde_json::from_slice(bytes).context("metadata_projection_invalid_document")?;
    ensure!(
        serde_json::to_vec(&parsed)? == serde_json::to_vec(&expected)?,
        "metadata_projection_payload_mismatch"
    );
    Ok(parsed)
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AnalyticsQuery {
    #[serde(default)]
    pub source_session_ids: Vec<String>,
    #[serde(default)]
    pub camera_ids: Vec<String>,
    pub start_ms: Option<u64>,
    pub end_ms: Option<u64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CameraStatistics {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capture_extent: Option<crate::domain::CaptureExtent>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_session_id: Option<String>,
    pub camera_id: String,
    pub indexed_events: usize,
    pub indexed_interval_ms: u64,
    pub observed_tracker_entities: Option<usize>,
    pub detector_observations: Option<usize>,
    pub tracker_observation_links: Option<usize>,
}

fn interval_union(mut intervals: Vec<(u64, u64)>) -> anyhow::Result<u64> {
    intervals.sort_unstable();
    let (mut total, mut current) = (0_u64, None::<(u64, u64)>);
    for (start, end) in intervals {
        if let Some((left, right)) = current {
            if start <= right {
                current = Some((left, right.max(end)));
                continue;
            }
            total = total
                .checked_add(right - left)
                .context("metadata_interval_overflow")?;
        }
        current = Some((start, end));
    }
    if let Some((left, right)) = current {
        total = total
            .checked_add(right - left)
            .context("metadata_interval_overflow")?;
    }
    Ok(total)
}

pub fn statistics(doc: &Document, query: &AnalyticsQuery) -> anyhow::Result<Vec<CameraStatistics>> {
    ensure!(
        query.camera_ids.len() <= 256
            && query.source_session_ids.len() <= 256
            && query
                .end_ms
                .is_none_or(|end| query.start_ms.unwrap_or(0) < end),
        "metadata_invalid_query"
    );
    ensure!(
        query
            .camera_ids
            .iter()
            .all(|id| doc.cameras.iter().any(|(key, camera)| camera
                .camera_id
                .as_ref()
                .unwrap_or(key)
                == id)),
        "metadata_camera_has_no_committed_evidence"
    );
    ensure!(
        query.source_session_ids.iter().all(|id| doc
            .cameras
            .values()
            .any(|camera| camera.source_session_id.as_ref() == Some(id))),
        "metadata_session_has_no_committed_evidence"
    );
    let mut reports = Vec::new();
    for (id, camera) in &doc.cameras {
        let physical_camera = camera.camera_id.as_ref().unwrap_or(id);
        if (!query.camera_ids.is_empty() && !query.camera_ids.contains(physical_camera))
            || (!query.source_session_ids.is_empty()
                && !camera
                    .source_session_id
                    .as_ref()
                    .is_some_and(|id| query.source_session_ids.contains(id)))
        {
            continue;
        }
        let start = query.start_ms.unwrap_or(0);
        let end = query
            .end_ms
            .unwrap_or(camera.duration_ms)
            .min(camera.duration_ms);
        let intervals: Vec<_> = camera
            .intervals
            .iter()
            .filter(|i| i.start_ms < end && i.end_ms > start && start < end)
            .collect();
        let selected: HashSet<_> = intervals.iter().map(|i| i.event_id).collect();
        let mut report = CameraStatistics {
            capture_extent: camera.capture_extent,
            camera_id: physical_camera.clone(),
            source_session_id: camera.source_session_id.clone(),
            indexed_events: intervals.len(),
            indexed_interval_ms: interval_union(
                intervals
                    .iter()
                    .map(|i| (i.start_ms.max(start), i.end_ms.min(end)))
                    .collect(),
            )?,
            observed_tracker_entities: None,
            detector_observations: None,
            tracker_observation_links: None,
        };
        if let Some(graph) = camera
            .graph
            .as_ref()
            .filter(|g| g.version == long_memory::VERSION && g.status == "ready")
        {
            let mut entity_ids = HashSet::new();
            let mut detector_points = HashSet::new();
            let mut observed_points = HashSet::new();
            for entity in &graph.entities {
                for point in &entity.observations {
                    if start <= point.timestamp_ms
                        && point.timestamp_ms < end
                        && selected.contains(&point.event_id)
                    {
                        entity_ids.insert(entity.entity_id);
                        observed_points.insert(point.observation_id);
                        if point.kind == "detector_observation" {
                            detector_points.insert(point.observation_id);
                        }
                    }
                }
            }
            report.observed_tracker_entities = Some(entity_ids.len());
            report.detector_observations = Some(detector_points.len());
            report.tracker_observation_links = Some(
                graph
                    .links
                    .iter()
                    .filter(|edge| {
                        entity_ids.contains(&edge.entity_id)
                            && observed_points.contains(&edge.from)
                            && observed_points.contains(&edge.to)
                    })
                    .count(),
            );
        }
        reports.push(report);
    }
    Ok(reports)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture() -> MemoryJobRecord {
        let id = Uuid::from_u128(600);
        let camera = crate::long_memory::tests::fixture(id, "a");
        serde_json::from_value(json!({"id":id,"request":{"name":"metadata fixture","cameras":[camera.camera.clone()]},
            "status":"completed","created_at_ms":1,"started_at_ms":1,"completed_at_ms":2,"error":null,
            "result":{"cluster_id":"fixture","cameras_requested":1,"cameras_processed":1,"camera_failures":[],
                "events_indexed":3,"source_duration_ms":22000,"observer_frames_decoded":110,
                "camera_results":[camera],"algorithm_version":"fixture"}})).unwrap()
    }

    #[test]
    fn exact_half_open_coverage_and_tracker_counts_do_not_infer_footfall() {
        let mut job = fixture();
        job.metadata_projection = Some(intent(&job, "research-raw-v1").unwrap());
        let doc = document(&job, "research-raw-v1").unwrap();
        let all = statistics(&doc, &AnalyticsQuery::default()).unwrap();
        assert_eq!(all[0].indexed_events, 3);
        assert_eq!(all[0].indexed_interval_ms, 22000);
        assert_eq!(all[0].observed_tracker_entities, Some(2));
        assert_eq!(all[0].detector_observations, Some(8));
        let middle = statistics(
            &doc,
            &AnalyticsQuery {
                start_ms: Some(4000),
                end_ms: Some(10000),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(middle[0].indexed_events, 1);
        assert_eq!(middle[0].indexed_interval_ms, 6000);
        assert_eq!(middle[0].detector_observations, Some(2));
        assert_eq!(middle[0].tracker_observation_links, Some(0));
        let outside = statistics(
            &doc,
            &AnalyticsQuery {
                start_ms: Some(22000),
                end_ms: Some(23000),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(outside[0].indexed_interval_ms, 0);
        assert_eq!(outside[0].observed_tracker_entities, Some(0));
        let mut no_graph = doc;
        no_graph.cameras.get_mut("a").unwrap().graph = None;
        assert!(
            statistics(&no_graph, &AnalyticsQuery::default()).unwrap()[0]
                .observed_tracker_entities
                .is_none()
        );
        assert!(statistics(
            &no_graph,
            &AnalyticsQuery {
                camera_ids: vec!["missing".into()],
                ..Default::default()
            }
        )
        .is_err());
    }

    #[test]
    fn content_anchor_namespace_and_graph_edges_are_verified() {
        let mut job = fixture();
        job.metadata_projection = Some(intent(&job, "research-raw-v1").unwrap());
        let doc = document(&job, "research-raw-v1").unwrap();
        let bytes = serde_json::to_vec_pretty(&doc).unwrap();
        let pending = job.metadata_projection.as_mut().unwrap();
        pending.state = "completed".into();
        pending.output_sha256 = Some(digest(&bytes));
        assert!(verify(&job, "research-raw-v1", &bytes).is_ok());
        let mut changed = doc;
        changed
            .cameras
            .get_mut("a")
            .unwrap()
            .graph
            .as_mut()
            .unwrap()
            .entities[0]
            .camera_id = "foreign".into();
        let changed = serde_json::to_vec(&changed).unwrap();
        assert!(verify(&job, "research-raw-v1", &changed).is_err());
        job.metadata_projection.as_mut().unwrap().output_sha256 = Some(digest(&changed));
        assert!(verify(&job, "research-raw-v1", &changed).is_err());
        let graph = job.result.as_mut().unwrap().camera_results[0]
            .long_memory
            .as_mut()
            .unwrap();
        graph.links[0].from = Uuid::nil();
        assert!(intent(&job, "research-raw-v1").is_err());
        let mut wrong_session = fixture();
        wrong_session.result.as_mut().unwrap().camera_results[0].events[0].job_id = Uuid::nil();
        assert!(intent(&wrong_session, "research-raw-v1").is_err());
    }

    #[test]
    fn saved_entities_cannot_cross_durable_capture_sessions() {
        let mut job = fixture();
        let camera = &mut job.result.as_mut().unwrap().camera_results[0];
        camera.events[0].privacy = Some(serde_json::from_value(json!({
            "source":{"schema":"visn-sanitized-media-v1","policy":"sanitized-pixels-v1",
                "qualification":"fixture","media_sha256":"fixture-sha","media_bytes":1,
                "frames":8,"width":32,"height":32,"ledger_sha256":"fixture","audio":"removed",
                "capture":{"session_id":"fixture","requested_seconds":22.0,"termination":"end_of_stream",
                    "observed_frames":8,"last_observed_frame_ms":21500,
                    "transport_started_wall_ms":1,"transport_finished_wall_ms":2,
                    "transport_time_is_not_capture_time":true,"continuity_certified":false,
                    "timestamp_jump_threshold_ms":1000,"timestamp_jump_count":1,
                    "timestamp_jump_samples":[[1500,8000]],"gap_interpretation":"uncertain spacing",
                    "association_sessions":[
                        {"session_id":"fixture:0","first_frame_index":0,"last_frame_index":3,
                         "first_observed_ms":0,"last_observed_ms":1500,"start_reason":"initial_capture"},
                        {"session_id":"fixture:1","first_frame_index":4,"last_frame_index":7,
                         "first_observed_ms":8000,"last_observed_ms":21500,"start_reason":"observed_timestamp_spacing"}]}},
            "thumbnail_sha256":"fixture","clip_sha256":"fixture"})).unwrap());
        assert!(intent(&job, "sanitized-v1")
            .unwrap_err()
            .to_string()
            .contains("crosses_capture_sessions"));
        // Removing the new contract is explicitly legacy/unavailable, preserving
        // old projection fingerprints rather than retroactively certifying it.
        job.result.as_mut().unwrap().camera_results[0].events[0]
            .privacy
            .as_mut()
            .unwrap()
            .source
            .capture
            .as_mut()
            .unwrap()
            .association_sessions
            .clear();
        let cameras = camera_payload(&job).unwrap();
        assert!(cameras["a"].capture_timeline.is_none());
        assert!(serde_json::to_value(&cameras["a"])
            .unwrap()
            .get("capture_timeline")
            .is_none());
    }

    #[test]
    fn equal_camera_timestamps_remain_separate_across_source_sessions() {
        let mut job = fixture();
        let original = job.result.as_ref().unwrap().camera_results[0].clone();
        let policy = serde_json::from_value(json!({})).unwrap();
        let mut cameras = Vec::new();
        for session in ["first", "second"] {
            let mut camera = original.clone();
            camera.source_session_id = Some(session.into());
            for event in &mut camera.events {
                event.source_session_id = Some(session.into());
                event.event_id = crate::domain::memory_event_id(
                    job.id,
                    "a",
                    Some(session),
                    event.start_ms,
                    event.end_ms,
                );
                event.privacy = Some(serde_json::from_value(json!({"source":{
                    "schema":"visn-sanitized-media-v1","policy":"sanitized-pixels-v1","qualification":"fixture",
                    "media_sha256":"fixture-sha","media_bytes":1,"frames":3,"width":32,"height":32,
                    "ledger_sha256":"fixture","audio":"removed","capture":{
                        "session_id":session,"requested_seconds":22.0,"termination":"end_of_stream",
                        "observed_frames":3,"last_observed_frame_ms":1000,
                        "transport_started_wall_ms":1,"transport_finished_wall_ms":2,
                        "transport_time_is_not_capture_time":true,"continuity_certified":false,
                        "timestamp_jump_threshold_ms":1000,"timestamp_jump_count":0,
                        "timestamp_jump_samples":[],"gap_interpretation":"unqualified fixture"}},
                    "thumbnail_sha256":"fixture","clip_sha256":"fixture"})).unwrap());
            }
            let mut analyzer = crate::event_engine::StreamingAnalyzer::new(job.id, &policy)
                .with_evidence_intervals(&[(0, 22000)]);
            for time in [0, 500, 1000] {
                analyzer
                    .observe(&crate::domain::Observation {
                        frame_time_ms: time,
                        track_id: "1".into(),
                        class_name: "truck".into(),
                        confidence: 0.9,
                        bbox: [0.1, 0.1, 0.2, 0.2],
                        appearance: None,
                    })
                    .unwrap();
            }
            camera.long_memory = Some(crate::long_memory::build(
                job.id,
                &camera,
                &analyzer.finish(),
                "fixture-detector".into(),
                "fixture-sha".into(),
            ));
            cameras.push(camera);
        }
        assert_ne!(cameras[0].events[0].event_id, cameras[1].events[0].event_id);
        let mut swapped_event = cameras[1].events[0].clone();
        swapped_event.source_session_id = Some("first".into());
        assert!(!swapped_event.source_session_matches_provenance());
        assert_ne!(
            cameras[0].long_memory.as_ref().unwrap().entities[0].entity_id,
            cameras[1].long_memory.as_ref().unwrap().entities[0].entity_id
        );
        job.result.as_mut().unwrap().camera_results = cameras.clone();
        job.metadata_projection = Some(intent(&job, "sanitized-v1").unwrap());
        let doc = document(&job, "sanitized-v1").unwrap();
        assert_eq!(doc.cameras.len(), 2);
        let reports = statistics(
            &doc,
            &AnalyticsQuery {
                camera_ids: vec!["a".into()],
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(reports.len(), 2);
        assert_eq!(
            reports.iter().map(|r| r.indexed_interval_ms).sum::<u64>(),
            44000
        );
        let only_second = statistics(
            &doc,
            &AnalyticsQuery {
                source_session_ids: vec!["second".into()],
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(only_second.len(), 1);
        assert_eq!(only_second[0].source_session_id.as_deref(), Some("second"));
        let query = serde_json::from_value(json!({"query":"recorded evidence","job_id":job.id,"source_session_ids":["second"],"long_memory_enabled":true})).unwrap();
        let candidates = cameras
            .iter()
            .flat_map(|c| c.events.iter().map(move |e| (e, &c.camera)))
            .collect();
        let (matches, _) = crate::memory::retrieve(&query, candidates);
        assert!(!matches.is_empty());
        assert!(matches
            .iter()
            .all(|m| m.event.source_session_id.as_deref() == Some("second")));
        let answer = crate::long_memory::answer(&query, &matches, &cameras);
        assert_eq!(answer.entities.len(), 1);
        assert!(answer
            .entities
            .iter()
            .all(|e| e.source_session_id.as_deref() == Some("second")));
        job.result.as_mut().unwrap().camera_results[1]
            .long_memory
            .as_mut()
            .unwrap()
            .entities[0]
            .source_session_id = Some("first".into());
        assert!(intent(&job, "sanitized-v1").is_err());
        let legacy = crate::domain::memory_event_id(job.id, "a", None, 0, 4000);
        assert_eq!(legacy, Uuid::new_v5(&job.id, b"a:0:4000"));
    }

    #[test]
    fn derived_recipe_is_bound_to_evidence_and_its_stage() {
        let mut job = fixture();
        let camera = &mut job.result.as_mut().unwrap().camera_results[0];
        let source = camera.long_memory.as_ref().unwrap().source_sha256.clone();
        let mut components = BTreeMap::from([
            ("compiled-rust".into(), "a".repeat(64)),
            ("model:tracker".into(), "b".repeat(64)),
            ("detector-runtime".into(), "c".repeat(64)),
            ("settings".into(), "d".repeat(64)),
        ]);
        let detector = digest(&serde_json::to_vec(&components).unwrap());
        components.insert("detector-identity".into(), detector);
        components.insert(
            "evidence".into(),
            crate::execution_profile::tracking_evidence(camera, &source).unwrap(),
        );
        let version = crate::execution_profile::TRACKING_VERSION;
        let fingerprint = digest(&serde_json::to_vec(&(version, &components)).unwrap());
        camera.long_memory.as_mut().unwrap().execution_profile =
            Some(crate::execution_profile::ExecutionProfile {
                version: version.into(),
                fingerprint,
                components,
            });
        let original = intent(&job, "research-raw-v1").unwrap();
        let camera = &mut job.result.as_mut().unwrap().camera_results[0];
        camera.events[0].end_ms += 1;
        assert!(intent(&job, "research-raw-v1").is_err());
        let camera = &mut job.result.as_mut().unwrap().camera_results[0];
        camera.events[0].end_ms -= 1;
        assert_eq!(
            intent(&job, "research-raw-v1").unwrap().input_fingerprint,
            original.input_fingerprint
        );
        let profile = job.result.as_mut().unwrap().camera_results[0]
            .long_memory
            .as_mut()
            .unwrap()
            .execution_profile
            .as_mut()
            .unwrap();
        profile.version = crate::execution_profile::VERSION.into();
        profile.fingerprint =
            digest(&serde_json::to_vec(&(&profile.version, &profile.components)).unwrap());
        assert!(intent(&job, "research-raw-v1").is_err());
    }

    #[test]
    fn execution_recipe_is_part_of_the_projection_contract() {
        let mut job = fixture();
        let legacy = intent(&job, "sanitized-v1").unwrap().input_fingerprint;
        let components = BTreeMap::from([("runtime".into(), "original".into())]);
        let version = crate::execution_profile::VERSION;
        let fingerprint = digest(&serde_json::to_vec(&(version, &components)).unwrap());
        job.execution_profile = Some(crate::execution_profile::ExecutionProfile {
            version: version.into(),
            fingerprint: fingerprint.clone(),
            components,
        });
        let pending = intent(&job, "sanitized-v1").unwrap();
        assert_ne!(pending.input_fingerprint, legacy);
        job.metadata_projection = Some(pending);
        let doc = document(&job, "sanitized-v1").unwrap();
        assert_eq!(
            doc.execution_profile_fingerprint.as_deref(),
            Some(fingerprint.as_str())
        );
        let profile = job.execution_profile.as_mut().unwrap();
        profile
            .components
            .insert("runtime".into(), "changed".into());
        profile.fingerprint = digest(&serde_json::to_vec(&(version, &profile.components)).unwrap());
        assert!(document(&job, "sanitized-v1").is_err());
    }

    #[test]
    fn producer_recipe_changes_invalidate_projection_even_when_pixels_are_identical() {
        let mut job = fixture();
        let camera = &mut job.result.as_mut().unwrap().camera_results[0];
        camera.long_memory = None;
        let privacy: crate::privacy_media::SanitizedEventEvidence = serde_json::from_value(json!({
            "source":{"schema":"visn-sanitized-media-v1","policy":"sanitized-pixels-v1",
                "qualification":"fixture","media_sha256":"unchanged-pixels","media_bytes":1,
                "frames":1,"width":32,"height":32,"ledger_sha256":"fixture","audio":"removed"},
            "thumbnail_sha256":"fixture","clip_sha256":"fixture"
        }))
        .unwrap();
        for event in &mut camera.events {
            event.privacy = Some(privacy.clone());
        }
        let legacy = camera_payload(&job).unwrap();
        assert!(serde_json::to_value(&legacy["a"])
            .unwrap()
            .get("source_provenance_sha256")
            .is_none());
        let before = intent(&job, "sanitized-v1").unwrap().input_fingerprint;
        for event in &mut job.result.as_mut().unwrap().camera_results[0].events {
            event
                .privacy
                .as_mut()
                .unwrap()
                .source
                .producer_metadata
                .insert("producer_revision".into(), json!("first"));
        }
        let current = intent(&job, "sanitized-v1").unwrap();
        assert_ne!(before, current.input_fingerprint);
        job.metadata_projection = Some(current);
        let doc = document(&job, "sanitized-v1").unwrap();
        assert!(doc.cameras["a"].source_provenance_sha256.is_some());
        for event in &mut job.result.as_mut().unwrap().camera_results[0].events {
            event
                .privacy
                .as_mut()
                .unwrap()
                .source
                .producer_metadata
                .insert("producer_revision".into(), json!("second"));
        }
        assert_ne!(
            intent(&job, "sanitized-v1").unwrap().input_fingerprint,
            doc.input_fingerprint
        );
        assert!(document(&job, "sanitized-v1").is_err());
    }

    #[test]
    fn retained_prefix_extent_is_explicit_consistent_and_fingerprinted() {
        let mut job = fixture();
        let before = intent(&job, "research-raw-v1").unwrap().input_fingerprint;
        let camera = &mut job.result.as_mut().unwrap().camera_results[0];
        assert!(serde_json::to_value(&*camera)
            .unwrap()
            .get("capture_extent")
            .is_none());
        camera.capture_extent = Some(crate::domain::CaptureExtent::RetainedPrefix);
        assert!(intent(&job, "research-raw-v1").is_err());
        for event in &mut job.result.as_mut().unwrap().camera_results[0].events {
            event.capture_extent = Some(crate::domain::CaptureExtent::RetainedPrefix);
        }
        let pending = intent(&job, "research-raw-v1").unwrap();
        assert_ne!(before, pending.input_fingerprint);
        job.metadata_projection = Some(pending);
        let doc = document(&job, "research-raw-v1").unwrap();
        let stats = statistics(&doc, &AnalyticsQuery::default()).unwrap();
        assert_eq!(
            stats[0].capture_extent,
            Some(crate::domain::CaptureExtent::RetainedPrefix)
        );
        let mut changed = doc;
        changed.cameras.get_mut("a").unwrap().capture_extent =
            Some(crate::domain::CaptureExtent::Finished);
        let bytes = serde_json::to_vec(&changed).unwrap();
        let projection = job.metadata_projection.as_mut().unwrap();
        projection.state = "completed".into();
        projection.output_sha256 = Some(digest(&bytes));
        assert!(verify(&job, "research-raw-v1", &bytes).is_err());
    }

    #[test]
    fn interval_union_matches_independent_bitmap_reference() {
        let mut seed = 93_u64;
        for _ in 0..100 {
            let mut intervals = Vec::new();
            let mut observed = HashSet::new();
            for _ in 0..20 {
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                let start = (seed >> 32) % 100;
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                let end = start + 1 + (seed >> 32) % 20;
                intervals.push((start, end));
                observed.extend(start..end);
            }
            assert_eq!(interval_union(intervals).unwrap(), observed.len() as u64);
        }
        assert_eq!(interval_union(vec![(u64::MAX - 1, u64::MAX)]).unwrap(), 1);
    }
}
