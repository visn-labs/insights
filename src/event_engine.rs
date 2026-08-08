use std::collections::{BTreeMap, BTreeSet, HashMap};

use anyhow::bail;
use uuid::Uuid;

use crate::domain::{
    AnalyticsPolicy, Line, Observation, ObservedEvent, Report, TrackSummary, Zone,
};

pub struct Analysis {
    pub tracks: Vec<TrackSummary>,
    pub events: Vec<ObservedEvent>,
    pub report: Report,
    pub duration_ms: u64,
}

/// Incrementally computes the same track summaries, rule events, and report as [`analyze`]
/// without retaining the observation history. Observations for a given track must arrive in
/// non-decreasing timestamp order; the detector's streamed frame protocol provides that order.
pub struct StreamingAnalyzer {
    job_id: Uuid,
    policy: CompiledPolicy,
    tracks: BTreeMap<String, TrackState>,
    events: Vec<ObservedEvent>,
    observation_count: usize,
    duration_ms: u64,
}

struct CompiledPolicy {
    zones: Vec<CompiledZone>,
    lines: Vec<CompiledLine>,
    line_state_count: usize,
    minimum_confirmation_observations: usize,
    dwell_threshold_ms: u64,
}

struct CompiledZone {
    id: String,
    polygons: Vec<Zone>,
    restricted: bool,
}

struct CompiledLine {
    line: Line,
    state_index: usize,
}

struct TrackState {
    class_name: String,
    started_at_ms: u64,
    last_observation: Observation,
    observations: usize,
    maximum_confidence: f32,
    zones_visited: BTreeSet<String>,
    previous_zones: Vec<bool>,
    current_zones: Vec<bool>,
    previous_line_sides: Vec<Option<f32>>,
    appearance: AppearanceAccumulator,
    confirmed: bool,
    pending_events: Vec<ObservedEvent>,
}

enum AppearanceAccumulator {
    Unseen,
    Empty,
    Active {
        aggregate: Vec<f32>,
        total_weight: f32,
    },
}

impl StreamingAnalyzer {
    pub fn new(job_id: Uuid, policy: &AnalyticsPolicy) -> Self {
        Self {
            job_id,
            policy: CompiledPolicy::new(policy),
            tracks: BTreeMap::new(),
            events: Vec::new(),
            observation_count: 0,
            duration_ms: 0,
        }
    }

    pub fn observation_count(&self) -> usize {
        self.observation_count
    }

    pub fn observe(&mut self, observation: &Observation) -> anyhow::Result<()> {
        let track = self
            .tracks
            .entry(observation.track_id.clone())
            .or_insert_with(|| {
                TrackState::new(
                    observation,
                    self.policy.zones.len(),
                    self.policy.line_state_count,
                )
            });
        if observation.frame_time_ms < track.last_observation.frame_time_ms {
            bail!(
                "track {} observation timestamp moved backwards from {} to {}",
                observation.track_id,
                track.last_observation.frame_time_ms,
                observation.frame_time_ms
            );
        }

        self.observation_count += 1;
        self.duration_ms = self.duration_ms.max(observation.frame_time_ms);
        let emitted = track.observe(
            self.job_id,
            observation,
            &self.policy,
            self.policy.minimum_confirmation_observations,
        );
        self.events.extend(emitted);
        Ok(())
    }

    pub fn finish(mut self) -> Analysis {
        let mut tracks = Vec::with_capacity(self.tracks.len());
        for (track_id, track) in self.tracks {
            if !track.confirmed {
                continue;
            }

            let duration_ms = track
                .last_observation
                .frame_time_ms
                .saturating_sub(track.started_at_ms);
            if duration_ms >= self.policy.dwell_threshold_ms {
                self.events.push(event(
                    self.job_id,
                    "dwell_threshold_exceeded",
                    &track.last_observation,
                    track.zones_visited.iter().next().cloned(),
                    None,
                    None,
                    format!(
                        "{} remained visible for {} seconds",
                        track.last_observation.class_name,
                        duration_ms / 1_000
                    ),
                ));
            }

            tracks.push(TrackSummary {
                track_id,
                class_name: track.class_name,
                started_at_ms: track.started_at_ms,
                ended_at_ms: track.last_observation.frame_time_ms,
                duration_ms,
                observations: track.observations,
                maximum_confidence: track.maximum_confidence,
                zones_visited: track.zones_visited.into_iter().collect(),
                appearance_prototype: track.appearance.finish(),
            });
        }

        // Batch analysis historically visited tracks in lexical track-id order before its
        // stable timestamp sort. Include that tie-break explicitly so interleaved streamed
        // frames retain the same deterministic order for simultaneous events.
        self.events.sort_by(|left, right| {
            left.event_time_ms
                .cmp(&right.event_time_ms)
                .then_with(|| left.track_id.cmp(&right.track_id))
        });
        let report = deterministic_report(
            &tracks,
            &self.events,
            self.observation_count,
            self.duration_ms,
        );
        Analysis {
            tracks,
            events: self.events,
            report,
            duration_ms: self.duration_ms,
        }
    }
}

pub fn analyze(job_id: Uuid, observations: &[Observation], policy: &AnalyticsPolicy) -> Analysis {
    let mut grouped: BTreeMap<&str, Vec<&Observation>> = BTreeMap::new();
    for observation in observations {
        grouped
            .entry(observation.track_id.as_str())
            .or_default()
            .push(observation);
    }

    let mut analyzer = StreamingAnalyzer::new(job_id, policy);
    for (_, mut samples) in grouped {
        samples.sort_by_key(|sample| sample.frame_time_ms);
        for sample in samples {
            analyzer
                .observe(sample)
                .expect("per-track observations were sorted by timestamp");
        }
    }
    analyzer.finish()
}

impl CompiledPolicy {
    fn new(policy: &AnalyticsPolicy) -> Self {
        let mut zones: BTreeMap<String, CompiledZone> = BTreeMap::new();
        for zone in &policy.zones {
            zones
                .entry(zone.id.clone())
                .and_modify(|compiled| compiled.polygons.push(zone.clone()))
                .or_insert_with(|| CompiledZone {
                    id: zone.id.clone(),
                    polygons: vec![zone.clone()],
                    restricted: zone.restricted,
                });
        }

        let mut line_state_indices = HashMap::new();
        let mut lines = Vec::with_capacity(policy.lines.len());
        for line in &policy.lines {
            let next_index = line_state_indices.len();
            let state_index = *line_state_indices
                .entry(line.id.clone())
                .or_insert(next_index);
            lines.push(CompiledLine {
                line: line.clone(),
                state_index,
            });
        }

        Self {
            zones: zones.into_values().collect(),
            lines,
            line_state_count: line_state_indices.len(),
            minimum_confirmation_observations: policy.minimum_confirmation_observations.max(1),
            dwell_threshold_ms: policy.dwell_threshold_ms,
        }
    }
}

impl TrackState {
    fn new(observation: &Observation, zone_count: usize, line_state_count: usize) -> Self {
        Self {
            class_name: observation.class_name.clone(),
            started_at_ms: observation.frame_time_ms,
            last_observation: observation_without_appearance(observation),
            observations: 0,
            maximum_confidence: 0.0,
            zones_visited: BTreeSet::new(),
            previous_zones: vec![false; zone_count],
            current_zones: vec![false; zone_count],
            previous_line_sides: vec![None; line_state_count],
            appearance: AppearanceAccumulator::Unseen,
            confirmed: false,
            pending_events: Vec::new(),
        }
    }

    fn observe(
        &mut self,
        job_id: Uuid,
        observation: &Observation,
        policy: &CompiledPolicy,
        confirmation_threshold: usize,
    ) -> Vec<ObservedEvent> {
        self.observations += 1;
        self.maximum_confidence = self.maximum_confidence.max(observation.confidence);
        self.last_observation = observation_without_appearance(observation);
        self.appearance.observe(observation);

        let center = observation.center();
        for (index, zone) in policy.zones.iter().enumerate() {
            self.current_zones[index] = zone
                .polygons
                .iter()
                .any(|polygon| point_in_polygon(center, polygon));
        }

        let mut generated = Vec::new();
        for (index, zone) in policy.zones.iter().enumerate() {
            if self.current_zones[index] && !self.previous_zones[index] {
                self.zones_visited.insert(zone.id.clone());
                let event_type = if zone.restricted {
                    "restricted_zone_occupied"
                } else {
                    "object_entered_zone"
                };
                generated.push(event(
                    job_id,
                    event_type,
                    observation,
                    Some(zone.id.clone()),
                    None,
                    None,
                    format!("{} entered zone {}", observation.class_name, zone.id),
                ));
            }
        }
        for (index, zone) in policy.zones.iter().enumerate() {
            if self.previous_zones[index] && !self.current_zones[index] {
                generated.push(event(
                    job_id,
                    "object_exited_zone",
                    observation,
                    Some(zone.id.clone()),
                    None,
                    None,
                    format!("{} exited zone {}", observation.class_name, zone.id),
                ));
            }
        }
        std::mem::swap(&mut self.previous_zones, &mut self.current_zones);

        for compiled in &policy.lines {
            let line = &compiled.line;
            let side = signed_side(center, line.start, line.end);
            let previous = self.previous_line_sides[compiled.state_index].replace(side);
            if let Some(previous) = previous {
                let crossed = previous.abs() > 0.0001
                    && side.abs() > 0.0001
                    && previous.signum() != side.signum();
                if crossed {
                    let direction = if previous > 0.0 {
                        line.positive_to_negative_label.clone()
                    } else {
                        line.negative_to_positive_label.clone()
                    };
                    generated.push(event(
                        job_id,
                        "line_crossed",
                        observation,
                        None,
                        Some(line.id.clone()),
                        Some(direction.clone()),
                        format!(
                            "{} crossed line {} in the {} direction",
                            observation.class_name, line.id, direction
                        ),
                    ));
                }
            }
        }

        if self.confirmed {
            generated
        } else {
            self.pending_events.extend(generated);
            if self.observations >= confirmation_threshold {
                self.confirmed = true;
                std::mem::take(&mut self.pending_events)
            } else {
                Vec::new()
            }
        }
    }
}

impl AppearanceAccumulator {
    fn observe(&mut self, observation: &Observation) {
        let Some(embedding) = &observation.appearance else {
            return;
        };
        if matches!(self, Self::Unseen) {
            *self = if embedding.is_empty() {
                Self::Empty
            } else {
                Self::Active {
                    aggregate: vec![0.0; embedding.len()],
                    total_weight: 0.0,
                }
            };
        }
        let Self::Active {
            aggregate,
            total_weight,
        } = self
        else {
            return;
        };
        if embedding.len() != aggregate.len() || embedding.iter().any(|value| !value.is_finite()) {
            return;
        }
        let weight = observation.confidence.max(0.05);
        for (output, value) in aggregate.iter_mut().zip(embedding) {
            *output += value * weight;
        }
        *total_weight += weight;
    }

    fn finish(self) -> Option<Vec<f32>> {
        let Self::Active {
            mut aggregate,
            total_weight,
        } = self
        else {
            return None;
        };
        if total_weight <= f32::EPSILON {
            return None;
        }
        for value in &mut aggregate {
            *value /= total_weight;
        }
        let norm = aggregate
            .iter()
            .map(|value| value * value)
            .sum::<f32>()
            .sqrt();
        if norm <= f32::EPSILON {
            return None;
        }
        for value in &mut aggregate {
            *value /= norm;
        }
        Some(aggregate)
    }
}

fn observation_without_appearance(observation: &Observation) -> Observation {
    Observation {
        frame_time_ms: observation.frame_time_ms,
        track_id: observation.track_id.clone(),
        class_name: observation.class_name.clone(),
        confidence: observation.confidence,
        bbox: observation.bbox,
        appearance: None,
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

    #[test]
    fn streamed_and_batch_analysis_match_for_ordered_frames() {
        let job_id = Uuid::nil();
        let observations = sample_observations();
        let policy = sample_policy();
        let batch = analyze(job_id, &observations, &policy);
        let mut streaming = StreamingAnalyzer::new(job_id, &policy);
        for observation in &observations {
            streaming.observe(observation).unwrap();
        }
        let streaming = streaming.finish();

        assert_eq!(streaming.duration_ms, batch.duration_ms);
        assert_eq!(streaming.report.summary, batch.report.summary);
        assert_eq!(
            streaming
                .tracks
                .iter()
                .map(|track| (&track.track_id, track.observations, track.duration_ms))
                .collect::<Vec<_>>(),
            batch
                .tracks
                .iter()
                .map(|track| (&track.track_id, track.observations, track.duration_ms))
                .collect::<Vec<_>>()
        );
        assert_eq!(
            streaming
                .events
                .iter()
                .map(|event| event.event_id)
                .collect::<Vec<_>>(),
            batch
                .events
                .iter()
                .map(|event| event.event_id)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn streaming_rejects_backwards_track_timestamps() {
        let policy = AnalyticsPolicy {
            minimum_confirmation_observations: 1,
            ..AnalyticsPolicy::default()
        };
        let mut analyzer = StreamingAnalyzer::new(Uuid::nil(), &policy);
        let mut observation = sample_observations().remove(0);
        observation.frame_time_ms = 1_000;
        analyzer.observe(&observation).unwrap();
        observation.frame_time_ms = 999;
        assert!(analyzer.observe(&observation).is_err());
    }
}
