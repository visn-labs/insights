//! Experiment 3: persistent evidence-linked tracker memory, never appearance-based identity.
use crate::{
    domain::{MemoryCameraResult, MemoryEvent, MemoryQueryMatch, MemoryQueryRequest},
    event_engine::Analysis,
};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use uuid::Uuid;

pub const VERSION: &str = "entity-memory-v2-pts";
const UNCERTAINTY: &str = "Tracker association, not verified physical identity. ID switches, occlusion and missed detections remain possible. First/last observed do not mean arrival/departure. No cross-camera or cross-recording identity is inferred.";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EntityMemory {
    pub version: String,
    pub source_sha256: String,
    pub detector_model: String,
    pub detector_fps: f32,
    pub status: String,
    pub entities: Vec<Entity>,
    pub links: Vec<EventLink>,
    pub notes: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Entity {
    pub entity_id: Uuid,
    pub job_id: Uuid,
    pub camera_id: String,
    pub track_id: String,
    pub class_name: String,
    pub observations: Vec<EvidenceObservation>,
    pub uncertainty: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EvidenceObservation {
    #[serde(default)]
    pub bbox: Option<crate::domain::BoundingBox>,
    #[serde(default)]
    pub detector_confidence: Option<f32>,
    pub observation_id: Uuid,
    pub event_id: Uuid,
    pub timestamp_ms: u64,
    pub kind: String,
    pub description: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EventLink {
    pub from: Uuid,
    pub to: Uuid,
    pub entity_id: Uuid,
    pub relation: String,
    pub gap_ms: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct JourneyAnswer {
    pub status: String,
    pub summary: String,
    pub entities: Vec<Entity>,
    pub links: Vec<EventLink>,
    pub supporting_events: Vec<MemoryEvent>,
    pub notes: Vec<String>,
}

pub fn build(
    job_id: Uuid,
    camera: &MemoryCameraResult,
    analysis: &Analysis,
    model: String,
    digest: String,
) -> EntityMemory {
    let mut entities = Vec::new();
    let mut links = Vec::new();
    let mut omitted = 0;
    for track in &analysis.tracks {
        // Namespace numeric tracker IDs by recording AND camera. Never merge by class or caption.
        let entity_id = Uuid::new_v5(
            &job_id,
            format!("{VERSION}:{}:{}", camera.camera.camera_id, track.track_id).as_bytes(),
        );
        let mut points = vec![
            (
                track.started_at_ms,
                "first_observed".to_owned(),
                format!(
                    "{} first observed under tracker ID {}",
                    track.class_name, track.track_id
                ),
            ),
            (
                track.ended_at_ms,
                "last_observed".to_owned(),
                format!(
                    "{} last observed under tracker ID {}",
                    track.class_name, track.track_id
                ),
            ),
        ];
        points.extend(
            analysis
                .events
                .iter()
                .filter(|e| e.track_id == track.track_id)
                .map(|e| {
                    (
                        e.event_time_ms,
                        format!("rule:{}", e.event_type),
                        format!(
                            "Rule {} emitted for this track{}{}",
                            e.event_type,
                            e.zone_id
                                .as_ref()
                                .map(|s| format!(" in zone {s}"))
                                .unwrap_or_default(),
                            e.direction
                                .as_ref()
                                .map(|s| format!(" ({s})"))
                                .unwrap_or_default()
                        ),
                    )
                }),
        );
        points.extend(analysis.evidence_observations.iter().filter(|o|o.track_id==track.track_id).map(|o|(o.frame_time_ms,"detector_observation".into(),format!("{} detection at normalized box [{:.3}, {:.3}, {:.3}, {:.3}]; detector score {:.3} (not identity confidence)",o.class_name,o.bbox[0],o.bbox[1],o.bbox[2],o.bbox[3],o.confidence))));
        points.sort();
        points.dedup();
        let observations: Vec<_> = points
            .into_iter()
            .filter_map(|(time, kind, description)| {
                // Half-open source intervals: a boundary citation belongs to the following clip.
                let event = camera
                    .events
                    .iter()
                    .filter(|e| e.start_ms <= time && time < e.end_ms)
                    .min_by_key(|e| (e.start_ms, e.event_id));
                match event {
                    Some(event) => Some(EvidenceObservation {
                        bbox: analysis
                            .evidence_observations
                            .iter()
                            .find(|o| o.track_id == track.track_id && o.frame_time_ms == time)
                            .map(|o| o.bbox),
                        detector_confidence: analysis
                            .evidence_observations
                            .iter()
                            .find(|o| o.track_id == track.track_id && o.frame_time_ms == time)
                            .map(|o| o.confidence),
                        observation_id: Uuid::new_v5(
                            &entity_id,
                            format!("{time}:{kind}").as_bytes(),
                        ),
                        event_id: event.event_id,
                        timestamp_ms: time,
                        kind,
                        description,
                    }),
                    None => {
                        omitted += 1;
                        None
                    }
                }
            })
            .collect();
        if observations.is_empty() {
            continue;
        }
        for pair in observations.windows(2) {
            links.push(EventLink {
                from: pair[0].observation_id,
                to: pair[1].observation_id,
                entity_id,
                relation: "same_tracker_order".into(),
                gap_ms: pair[1].timestamp_ms - pair[0].timestamp_ms,
            });
        }
        entities.push(Entity {
            entity_id,
            job_id,
            camera_id: camera.camera.camera_id.clone(),
            track_id: track.track_id.clone(),
            class_name: track.class_name.clone(),
            observations,
            uncertainty: UNCERTAINTY.into(),
        });
    }
    entities.sort_by_key(|e| e.entity_id);
    EntityMemory {version:VERSION.into(),source_sha256:digest,detector_model:model,detector_fps:5.0,status:"ready".into(),entities,links,
        notes:vec![UNCERTAINTY.into(),format!("{omitted} observations without an indexed evidence interval were omitted. Times are source-relative, not synchronized wall-clock times. Detector cadence uses source presentation timestamps (receipt times for live network analysis); nominal FPS is only a fallback clock. Rule events are algorithm outputs, not VLM action claims.")]}
}

pub fn answer(
    request: &MemoryQueryRequest,
    matches: &[MemoryQueryMatch],
    cameras: &[MemoryCameraResult],
) -> JourneyAnswer {
    let seeds: HashSet<_> = matches.iter().map(|m| m.event.event_id).collect();
    let events: HashMap<_, _> = cameras
        .iter()
        .flat_map(|c| &c.events)
        .filter(|e| crate::retrieval::eligible(request, e))
        .map(|e| (e.event_id, e))
        .collect();
    let terms: HashSet<_> = request
        .query
        .to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();
    let classes = [
        "person",
        "car",
        "truck",
        "bus",
        "motorcycle",
        "bicycle",
        "dog",
        "cat",
    ];
    let requested_classes: Vec<_> = classes
        .into_iter()
        .filter(|class| {
            terms.contains(*class)
                || terms.contains(&format!("{class}s"))
                || terms.contains(&format!("{class}es"))
                || (*class == "person" && terms.contains("people"))
                || ((terms.contains("vehicle") || terms.contains("vehicles"))
                    && ["car", "truck", "bus", "motorcycle"].contains(class))
        })
        .collect();
    let mut entities = Vec::new();
    let mut all_links = Vec::new();
    let mut notes = vec![UNCERTAINTY.into(), "Only source-relative observations are ordered. No ordering across recordings, causality, identity after track loss, or unsupported actions is asserted. Class lookup uses detector labels; color and action predicates are not verified.".into()];
    let mut available = 0;
    let mut missing = 0;
    for camera in cameras {
        if !camera
            .events
            .iter()
            .any(|e| events.contains_key(&e.event_id))
        {
            continue;
        }
        let Some(graph) = &camera.long_memory else {
            missing += 1;
            continue;
        };
        if graph.version != VERSION || graph.status != "ready" {
            missing += 1;
            continue;
        }
        available += 1;
        for entity in &graph.entities {
            if request.entity_id.is_some_and(|id| id != entity.entity_id) {
                continue;
            }
            if request.entity_id.is_none()
                && !requested_classes.is_empty()
                && !requested_classes.contains(&entity.class_name.as_str())
            {
                continue;
            }
            let observations: Vec<_> = entity
                .observations
                .iter()
                .filter(|o| {
                    events.get(&o.event_id).is_some_and(|event| {
                        event.job_id == entity.job_id
                            && event.camera_id == entity.camera_id
                            && event.start_ms <= o.timestamp_ms
                            && o.timestamp_ms < event.end_ms
                    }) && request.start_ms.is_none_or(|t| o.timestamp_ms >= t)
                        && request.end_ms.is_none_or(|t| o.timestamp_ms <= t)
                })
                .cloned()
                .collect();
            if observations.is_empty() {
                continue;
            }
            // Explicit entity or class queries can start directly from structured memory.
            if request.entity_id.is_none()
                && requested_classes.is_empty()
                && !observations.iter().any(|o| seeds.contains(&o.event_id))
            {
                continue;
            }
            let mut entity = entity.clone();
            entity.observations = observations;
            entities.push(entity);
        }
        all_links.extend(graph.links.clone());
    }
    entities.sort_by_key(|e| {
        (
            e.job_id,
            e.camera_id.clone(),
            e.observations[0].timestamp_ms,
            e.entity_id,
        )
    });
    let total = entities.len();
    entities.truncate(12);
    if total > 12 {
        notes.push(format!("Showing 12 of {total} candidate tracks. Select an entity or narrow filters; this is not a complete count."));
    }
    if missing > 0 {
        notes.push(format!("{missing} eligible camera recordings have no ready entity memory. Build experiment 3 memory for complete coverage."));
    }
    notes.push(format!("{available} eligible camera memories available. Detector errors and fragmentation were not independently corrected."));
    let ids: HashSet<_> = entities.iter().map(|e| e.entity_id).collect();
    let observations: HashMap<_, _> = entities
        .iter()
        .flat_map(|e| {
            e.observations
                .iter()
                .map(|o| (o.observation_id, (e.entity_id, o.timestamp_ms)))
        })
        .collect();
    let links = all_links
        .into_iter()
        .filter(
            |link| match (observations.get(&link.from), observations.get(&link.to)) {
                (Some((a, start)), Some((b, end))) => {
                    ids.contains(&link.entity_id)
                        && *a == link.entity_id
                        && *b == link.entity_id
                        && link.relation == "same_tracker_order"
                        && end.checked_sub(*start) == Some(link.gap_ms)
                }
                _ => false,
            },
        )
        .collect();
    let evidence_ids: HashSet<_> = entities
        .iter()
        .flat_map(|e| &e.observations)
        .map(|o| o.event_id)
        .collect();
    let mut supporting_events: Vec<_> = evidence_ids
        .into_iter()
        .filter_map(|id| events.get(&id).map(|e| (*e).clone()))
        .collect();
    supporting_events.sort_by_key(|e| (e.job_id, e.camera_id.clone(), e.start_ms, e.event_id));
    let multi_observation = entities
        .iter()
        .any(|e| e.observations.last().unwrap().timestamp_ms > e.observations[0].timestamp_ms);
    let status = if entities.is_empty() || !multi_observation {
        "insufficient_evidence"
    } else if total > 1 && request.entity_id.is_none() {
        "ambiguous_entities"
    } else {
        "tracker_linked"
    };
    let summary = if entities.is_empty() {
        "No evidence-linked entity matched this scope. Build entity memory or select a matching track; identity and unseen actions cannot be inferred.".into()
    } else {
        format!("{} candidate tracker histories ({}). {} These are tracker observations, not proof of physical identity or inferred actions.",entities.len(),status,
            entities.iter().take(3).map(|e|format!("{} track {} in camera {} (recording {}): observed at {:.2}s and {:.2}s, with {} cited observations.",e.class_name,e.track_id,e.camera_id,e.job_id,e.observations[0].timestamp_ms as f64/1000.0,e.observations.last().unwrap().timestamp_ms as f64/1000.0,e.observations.len())).collect::<Vec<_>>().join(" "))
    };
    JourneyAnswer {
        status: status.into(),
        summary,
        entities,
        links,
        supporting_events,
        notes,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        domain::{AnalyticsPolicy, Observation},
        event_engine,
    };
    use serde_json::json;

    fn fixture(job: Uuid, camera_id: &str) -> MemoryCameraResult {
        let camera = json!({"camera_id":camera_id,"live_url":"http://fixture/"});
        let events:Vec<_>=[(0,4000),(4000,10000),(10000,22000)].into_iter().enumerate().map(|(index,(start,end))|json!({
            "event_id":Uuid::new_v5(&job,format!("{camera_id}:{index}").as_bytes()),"job_id":job,"camera_id":camera_id,"cluster_id":"fixture",
            "start_ms":start,"end_ms":end,"duration_ms":end-start,"source_duration_ms":22000,
            "activity_mean":0.1,"activity_peak":0.2,"quality":1.0,"boundary_reason":"fixture","thumbnail_url":"/fixture.jpg","evidence_url":"/fixture.mp4",
            "description":{"summary":"recorded evidence","scene_type":"road","confidence":0.0,"generated_by_model":false,"conditions":"fixture"}
        })).collect();
        let mut camera: MemoryCameraResult=serde_json::from_value(json!({"camera":camera,"duration_ms":22000,"frames_decoded":110,"evidence_url":"/source","events":events,"data_quality_notes":[]})).unwrap();
        let mut observations = Vec::new();
        for (track, offset) in [("1", 0), ("2", 500)] {
            for time in [0, 1000, 8000, 20000] {
                observations.push(Observation {
                    frame_time_ms: time + offset,
                    track_id: track.into(),
                    class_name: "truck".into(),
                    confidence: 0.9,
                    bbox: [0.1, 0.1, 0.2, 0.2],
                    appearance: None,
                });
            }
        }
        let policy: AnalyticsPolicy = serde_json::from_value(json!({})).unwrap();
        let intervals: Vec<_> = camera
            .events
            .iter()
            .map(|e| (e.start_ms, e.end_ms))
            .collect();
        let mut analyzer =
            event_engine::StreamingAnalyzer::new(job, &policy).with_evidence_intervals(&intervals);
        for observation in &observations {
            analyzer.observe(observation).unwrap();
        }
        let analysis = analyzer.finish();
        camera.long_memory = Some(build(
            job,
            &camera,
            &analysis,
            "fixture-detector".into(),
            "fixture-sha".into(),
        ));
        camera
    }
    fn query() -> MemoryQueryRequest {
        serde_json::from_value(
            json!({"query":"What happened to the trucks?","semantic_enabled":false}),
        )
        .unwrap()
    }

    #[test]
    fn separates_similar_objects_and_namespaces_reused_track_ids() {
        let job = Uuid::from_u128(1);
        let a = fixture(job, "a");
        let b = fixture(job, "b");
        let c = fixture(Uuid::from_u128(2), "a");
        let answer = answer(&query(), &[], &[a, b, c]);
        assert_eq!(answer.entities.len(), 6);
        assert_eq!(answer.status, "ambiguous_entities");
        assert_eq!(
            answer
                .entities
                .iter()
                .map(|e| e.entity_id)
                .collect::<HashSet<_>>()
                .len(),
            6
        );
        for link in answer.links {
            let entity = answer
                .entities
                .iter()
                .find(|e| e.entity_id == link.entity_id)
                .unwrap();
            assert!(entity
                .observations
                .iter()
                .any(|o| o.observation_id == link.from));
            assert!(entity
                .observations
                .iter()
                .any(|o| o.observation_id == link.to));
        }
    }
    #[test]
    fn follows_one_entity_beyond_retrieval_shortlist_and_preserves_evidence() {
        let camera = fixture(Uuid::nil(), "a");
        let entity = &camera.long_memory.as_ref().unwrap().entities[0];
        let mut request = query();
        request.entity_id = Some(entity.entity_id);
        let seed = MemoryQueryMatch {
            rank: 1,
            score: 0.5,
            matched_terms: vec![],
            event: camera.events[0].clone(),
        };
        let result = answer(&request, &[seed], std::slice::from_ref(&camera));
        assert_eq!(result.status, "tracker_linked");
        assert_eq!(result.entities.len(), 1);
        assert_eq!(result.supporting_events.len(), 3);
        assert!(result.entities[0]
            .observations
            .windows(2)
            .all(|p| p[0].timestamp_ms <= p[1].timestamp_ms));
        for observation in &result.entities[0].observations {
            let e = result
                .supporting_events
                .iter()
                .find(|e| e.event_id == observation.event_id)
                .unwrap();
            assert!(e.start_ms <= observation.timestamp_ms && observation.timestamp_ms < e.end_ms);
        }
        // Persistence round trip preserves identifiers and all evidence links.
        let restored: MemoryCameraResult =
            serde_json::from_str(&serde_json::to_string(&camera).unwrap()).unwrap();
        assert_eq!(
            serde_json::to_value(answer(&request, &[], &[restored])).unwrap(),
            serde_json::to_value(result).unwrap()
        );
    }
    #[test]
    fn applies_all_filters_to_expansion_and_abstains_outside_scope() {
        let camera = fixture(Uuid::nil(), "a");
        let mut request = query();
        request.entity_id = Some(camera.long_memory.as_ref().unwrap().entities[0].entity_id);
        request.start_ms = Some(10000);
        request.end_ms = Some(21000);
        let result = answer(&request, &[], std::slice::from_ref(&camera));
        assert!(result
            .entities
            .iter()
            .flat_map(|e| &e.observations)
            .all(|o| o.timestamp_ms >= 10000));
        assert_eq!(result.supporting_events.len(), 1);
        request.camera_ids = vec!["other".into()];
        assert!(answer(&request, &[], std::slice::from_ref(&camera))
            .entities
            .is_empty());
        request.camera_ids.clear();
        request.job_id = Some(Uuid::from_u128(9));
        assert!(answer(&request, &[], std::slice::from_ref(&camera))
            .entities
            .is_empty());
        request.job_id = None;
        request.cluster_id = Some("other".into());
        assert!(answer(&request, &[], &[camera]).entities.is_empty());
    }
    #[test]
    fn rejects_unrelated_links_missing_evidence_and_unknown_entities() {
        let mut camera = fixture(Uuid::nil(), "a");
        let g = camera.long_memory.as_mut().unwrap();
        let mut forged = g.links[0].clone();
        forged.to = g
            .entities
            .iter()
            .find(|e| e.entity_id != forged.entity_id)
            .unwrap()
            .observations[0]
            .observation_id;
        g.links.push(forged);
        let result = answer(&query(), &[], std::slice::from_ref(&camera));
        assert!(result.links.len() < g_len(&camera));
        camera.events.clear();
        assert!(answer(&query(), &[], std::slice::from_ref(&camera))
            .entities
            .is_empty());
        let camera = fixture(Uuid::nil(), "a");
        let mut request = query();
        request.entity_id = Some(Uuid::from_u128(999));
        assert_eq!(
            answer(&request, &[], &[camera]).status,
            "insufficient_evidence"
        );
    }
    fn g_len(c: &MemoryCameraResult) -> usize {
        c.long_memory.as_ref().unwrap().links.len()
    }
    #[test]
    fn old_manifests_have_explicit_missing_coverage() {
        let mut camera = fixture(Uuid::nil(), "a");
        camera.long_memory = None;
        let result = answer(&query(), &[], &[camera]);
        assert!(result.entities.is_empty());
        assert!(result
            .notes
            .iter()
            .any(|n| n.contains("no ready entity memory")));
    }
    #[test]
    fn stable_ids_and_no_invented_observations_across_uncovered_intervals() {
        let a = fixture(Uuid::nil(), "a");
        let b = fixture(Uuid::nil(), "a");
        assert_eq!(
            serde_json::to_value(&a.long_memory).unwrap(),
            serde_json::to_value(&b.long_memory).unwrap()
        );
        let mut camera = a;
        camera.events.remove(1);
        let result = answer(&query(), &[], &[camera]);
        assert!(result
            .entities
            .iter()
            .flat_map(|e| &e.observations)
            .all(|o| o.timestamp_ms < 4000 || o.timestamp_ms >= 10000));
    }
    #[test]
    fn bounded_anchors_keep_actual_interval_endpoints_without_full_history() {
        let policy: AnalyticsPolicy = serde_json::from_value(json!({})).unwrap();
        let mut analyzer = event_engine::StreamingAnalyzer::new(Uuid::nil(), &policy)
            .with_evidence_intervals(&[(0, 5000), (5000, 10000)]);
        let mut baseline = event_engine::StreamingAnalyzer::new(Uuid::nil(), &policy);
        for i in 0..1000 {
            let o = Observation {
                frame_time_ms: i * 10,
                track_id: "one".into(),
                class_name: "car".into(),
                confidence: 0.8,
                bbox: [0.0, 0.0, 0.1, 0.1],
                appearance: None,
            };
            analyzer.observe(&o).unwrap();
            baseline.observe(&o).unwrap();
        }
        let enhanced = analyzer.finish();
        let baseline = baseline.finish();
        assert_eq!(
            enhanced
                .evidence_observations
                .iter()
                .map(|o| o.frame_time_ms)
                .collect::<Vec<_>>(),
            vec![0, 4990, 5000, 9990]
        );
        assert!(baseline.evidence_observations.is_empty());
        assert_eq!(
            serde_json::to_value(enhanced.tracks).unwrap(),
            serde_json::to_value(baseline.tracks).unwrap()
        );
        assert_eq!(
            serde_json::to_value(enhanced.events).unwrap(),
            serde_json::to_value(baseline.events).unwrap()
        );
    }

    #[test]
    fn class_lookup_handles_plural_buses_and_old_graph_versions_abstain() {
        let mut camera = fixture(Uuid::nil(), "a");
        camera.long_memory.as_mut().unwrap().entities[0].class_name = "bus".into();
        for word in ["bus", "buses"] {
            let mut request = query();
            request.query = word.into();
            let result = answer(&request, &[], std::slice::from_ref(&camera));
            assert_eq!(result.entities.len(), 1);
            assert_eq!(result.entities[0].class_name, "bus");
        }
        camera.long_memory.as_mut().unwrap().version = "unknown-version".into();
        assert_eq!(
            answer(&query(), &[], &[camera]).status,
            "insufficient_evidence"
        );
    }
}
