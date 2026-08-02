use std::collections::{BTreeMap, BTreeSet};

use uuid::Uuid;

use crate::domain::{AnalyticsPolicy, Observation, ObservedEvent, Report, TrackSummary, Zone};

pub struct Analysis {
    pub tracks: Vec<TrackSummary>,
    pub events: Vec<ObservedEvent>,
    pub report: Report,
    pub duration_ms: u64,
}

pub fn analyze(job_id: Uuid, observations: &[Observation], policy: &AnalyticsPolicy) -> Analysis {
    let mut grouped: BTreeMap<&str, Vec<&Observation>> = BTreeMap::new();
    for observation in observations {
        grouped
            .entry(observation.track_id.as_str())
            .or_default()
            .push(observation);
    }

    let mut tracks = Vec::new();
    let mut events = Vec::new();

    for (track_id, mut samples) in grouped {
        samples.sort_by_key(|sample| sample.frame_time_ms);
        if samples.len() < policy.minimum_confirmation_observations.max(1) {
            continue;
        }

        let first = samples[0];
        let last = samples[samples.len() - 1];
        let mut zones_visited = BTreeSet::new();
        let mut previous_zones = BTreeSet::new();
        let mut previous_line_sides: BTreeMap<&str, f32> = BTreeMap::new();

        for sample in &samples {
            let center = sample.center();
            let current_zones: BTreeSet<&str> = policy
                .zones
                .iter()
                .filter(|zone| point_in_polygon(center, zone))
                .map(|zone| zone.id.as_str())
                .collect();

            for zone_id in current_zones.difference(&previous_zones) {
                let zone = policy
                    .zones
                    .iter()
                    .find(|candidate| candidate.id == **zone_id)
                    .expect("zone id came from policy");
                zones_visited.insert((*zone_id).to_owned());
                let event_type = if zone.restricted {
                    "restricted_zone_occupied"
                } else {
                    "object_entered_zone"
                };
                events.push(event(
                    job_id,
                    event_type,
                    sample,
                    Some((*zone_id).to_owned()),
                    None,
                    None,
                    format!("{} entered zone {}", sample.class_name, zone_id),
                ));
            }

            for zone_id in previous_zones.difference(&current_zones) {
                events.push(event(
                    job_id,
                    "object_exited_zone",
                    sample,
                    Some((*zone_id).to_owned()),
                    None,
                    None,
                    format!("{} exited zone {}", sample.class_name, zone_id),
                ));
            }

            for line in &policy.lines {
                let side = signed_side(center, line.start, line.end);
                if let Some(previous) = previous_line_sides.insert(line.id.as_str(), side) {
                    let crossed = previous.abs() > 0.0001
                        && side.abs() > 0.0001
                        && previous.signum() != side.signum();
                    if crossed {
                        let direction = if previous > 0.0 {
                            line.positive_to_negative_label.clone()
                        } else {
                            line.negative_to_positive_label.clone()
                        };
                        events.push(event(
                            job_id,
                            "line_crossed",
                            sample,
                            None,
                            Some(line.id.clone()),
                            Some(direction.clone()),
                            format!(
                                "{} crossed line {} in the {} direction",
                                sample.class_name, line.id, direction
                            ),
                        ));
                    }
                }
            }
            previous_zones = current_zones;
        }

        let duration_ms = last.frame_time_ms.saturating_sub(first.frame_time_ms);
        if duration_ms >= policy.dwell_threshold_ms {
            events.push(event(
                job_id,
                "dwell_threshold_exceeded",
                last,
                zones_visited.iter().next().cloned(),
                None,
                None,
                format!(
                    "{} remained visible for {} seconds",
                    last.class_name,
                    duration_ms / 1_000
                ),
            ));
        }

        tracks.push(TrackSummary {
            track_id: track_id.to_owned(),
            class_name: first.class_name.clone(),
            started_at_ms: first.frame_time_ms,
            ended_at_ms: last.frame_time_ms,
            duration_ms,
            observations: samples.len(),
            maximum_confidence: samples
                .iter()
                .map(|sample| sample.confidence)
                .fold(0.0, f32::max),
            zones_visited: zones_visited.into_iter().collect(),
        });
    }

    events.sort_by_key(|event| event.event_time_ms);
    let duration_ms = observations
        .iter()
        .map(|observation| observation.frame_time_ms)
        .max()
        .unwrap_or(0);
    let report = deterministic_report(&tracks, &events, observations.len(), duration_ms);

    Analysis {
        tracks,
        events,
        report,
        duration_ms,
    }
}

fn event(
    job_id: Uuid,
    event_type: &str,
    observation: &Observation,
    zone_id: Option<String>,
    line_id: Option<String>,
    direction: Option<String>,
    description: String,
) -> ObservedEvent {
    let identity = format!(
        "{job_id}:{event_type}:{}:{}:{}:{}",
        observation.track_id,
        observation.frame_time_ms,
        zone_id.as_deref().unwrap_or(""),
        line_id.as_deref().unwrap_or("")
    );
    ObservedEvent {
        event_id: Uuid::new_v5(&Uuid::NAMESPACE_OID, identity.as_bytes()),
        event_type: event_type.to_owned(),
        event_time_ms: observation.frame_time_ms,
        track_id: observation.track_id.clone(),
        class_name: observation.class_name.clone(),
        confidence: observation.confidence,
        zone_id,
        line_id,
        direction,
        description,
    }
}

fn point_in_polygon(point: [f32; 2], zone: &Zone) -> bool {
    if zone.points.len() < 3 {
        return false;
    }
    let mut inside = false;
    let mut previous = zone.points[zone.points.len() - 1];
    for current in &zone.points {
        let vertical_delta = previous[1] - current[1];
        let intersects = ((current[1] > point[1]) != (previous[1] > point[1]))
            && vertical_delta.abs() > f32::EPSILON
            && (point[0]
                < (previous[0] - current[0]) * (point[1] - current[1]) / vertical_delta
                    + current[0]);
        if intersects {
            inside = !inside;
        }
        previous = *current;
    }
    inside
}

fn signed_side(point: [f32; 2], start: [f32; 2], end: [f32; 2]) -> f32 {
    (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])
}

fn deterministic_report(
    tracks: &[TrackSummary],
    events: &[ObservedEvent],
    observation_count: usize,
    duration_ms: u64,
) -> Report {
    let notable_event_ids = events.iter().take(8).map(|event| event.event_id).collect();
    let mut by_class: BTreeMap<&str, usize> = BTreeMap::new();
    for track in tracks {
        *by_class.entry(track.class_name.as_str()).or_default() += 1;
    }
    let class_summary = if by_class.is_empty() {
        "no confirmed tracks".to_owned()
    } else {
        by_class
            .iter()
            .map(|(class_name, count)| format!("{count} {class_name}"))
            .collect::<Vec<_>>()
            .join(", ")
    };
    let mut notes = Vec::new();
    if observation_count == 0 {
        notes.push("No detector observations were available.".to_owned());
    }

    Report {
        headline: if events.is_empty() {
            "No rule events observed".to_owned()
        } else {
            format!("{} deterministic events observed", events.len())
        },
        summary: format!(
            "Processed {observation_count} observations across {} confirmed tracks ({class_summary}) over {:.1} seconds and produced {} events.",
            tracks.len(),
            duration_ms as f64 / 1_000.0,
            events.len()
        ),
        notable_event_ids,
        observations: events
            .iter()
            .take(6)
            .map(|event| event.description.clone())
            .collect(),
        data_quality_notes: notes,
        confidence: if observation_count == 0 { 0.0 } else { 1.0 },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pipeline::{sample_observations, sample_policy};

    #[test]
    fn sample_produces_confirmed_track_zone_and_line_events() {
        let analysis = analyze(Uuid::nil(), &sample_observations(), &sample_policy());
        assert_eq!(analysis.tracks.len(), 2);
        assert!(analysis
            .events
            .iter()
            .any(|event| event.event_type == "line_crossed"));
        assert!(analysis
            .events
            .iter()
            .any(|event| event.event_type == "restricted_zone_occupied"));
    }

    #[test]
    fn event_ids_are_stable_on_replay() {
        let job_id = Uuid::nil();
        let first = analyze(job_id, &sample_observations(), &sample_policy());
        let second = analyze(job_id, &sample_observations(), &sample_policy());
        let first_ids: Vec<_> = first.events.iter().map(|event| event.event_id).collect();
        let second_ids: Vec<_> = second.events.iter().map(|event| event.event_id).collect();
        assert_eq!(first_ids, second_ids);
    }
}
