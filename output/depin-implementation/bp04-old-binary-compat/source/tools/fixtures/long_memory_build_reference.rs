// Frozen pre-package-2 implementation, used only for equivalence tests.
fn build_reference(
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
