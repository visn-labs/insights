use std::collections::{BTreeMap, BTreeSet, HashMap};

use uuid::Uuid;

use crate::domain::{
    AssociationDecision, AssociationDecisionState, CameraAnalyticsResult, CameraEdgeType,
    CameraProcessingFailure, CameraTopologyEdge, ClusterJobRequest, GlobalTrack,
    GlobalTrackSegment, Report, TrackSummary, ViewDescription,
};

#[derive(Clone)]
struct TrackletNode {
    camera_id: String,
    track: TrackSummary,
    started_at_ms: u64,
    ended_at_ms: u64,
}

#[derive(Clone)]
struct Relation {
    edge_id: Option<String>,
    source_camera_id: String,
    target_camera_id: String,
    edge_type: CameraEdgeType,
    minimum_travel_ms: u64,
    maximum_travel_ms: u64,
    confidence: f32,
}

pub struct AssociationOutput {
    pub decisions: Vec<AssociationDecision>,
    pub global_tracks: Vec<GlobalTrack>,
}

pub fn associate(
    job_id: Uuid,
    request: &ClusterJobRequest,
    cameras: &[CameraAnalyticsResult],
) -> AssociationOutput {
    let nodes = collect_person_tracklets(cameras);
    let mut by_camera: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for (index, node) in nodes.iter().enumerate() {
        by_camera
            .entry(node.camera_id.as_str())
            .or_default()
            .push(index);
    }

    let relations = association_relations(request);
    let mut best_by_pair: HashMap<(usize, usize), AssociationDecision> = HashMap::new();
    for relation in relations {
        let Some(sources) = by_camera.get(relation.source_camera_id.as_str()) else {
            continue;
        };
        let Some(targets) = by_camera.get(relation.target_camera_id.as_str()) else {
            continue;
        };
        if sources.is_empty() || targets.is_empty() {
            continue;
        }

        let mut scores = vec![vec![None; targets.len()]; sources.len()];
        for (source_row, source_index) in sources.iter().enumerate() {
            for (target_column, target_index) in targets.iter().enumerate() {
                scores[source_row][target_column] = score_candidate(
                    &nodes[*source_index],
                    &nodes[*target_index],
                    &relation,
                    request,
                );
            }
        }

        for (source_row, target_column, score) in maximum_weight_assignment(&scores) {
            if score < 0.55 {
                continue;
            }
            let source_index = sources[source_row];
            let target_index = targets[target_column];
            let source = &nodes[source_index];
            let target = &nodes[target_index];
            let appearance_similarity = cosine_similarity(
                source.track.appearance_prototype.as_deref(),
                target.track.appearance_prototype.as_deref(),
            )
            .unwrap_or(0.0);
            let temporal_score = temporal_score(source, target, &relation, request);
            let state = decision_state(score, request);
            let identity = format!(
                "{job_id}:{}:{}:{}:{}",
                source.camera_id, source.track.track_id, target.camera_id, target.track.track_id
            );
            let decision = AssociationDecision {
                association_id: Uuid::new_v5(&Uuid::NAMESPACE_OID, identity.as_bytes()),
                edge_id: relation.edge_id.clone(),
                source_camera_id: source.camera_id.clone(),
                source_track_id: source.track.track_id.clone(),
                target_camera_id: target.camera_id.clone(),
                target_track_id: target.track.track_id.clone(),
                edge_type: relation.edge_type,
                appearance_similarity,
                temporal_score,
                score,
                state,
                explanation: explanation(
                    source,
                    target,
                    &relation,
                    appearance_similarity,
                    temporal_score,
                ),
            };
            let key = if source_index <= target_index {
                (source_index, target_index)
            } else {
                (target_index, source_index)
            };
            if best_by_pair
                .get(&key)
                .map(|existing| existing.score < score)
                .unwrap_or(true)
            {
                best_by_pair.insert(key, decision);
            }
        }
    }

    let mut decisions: Vec<_> = best_by_pair.into_values().collect();
    decisions.sort_by(|left, right| {
        right
            .score
            .total_cmp(&left.score)
            .then_with(|| left.association_id.cmp(&right.association_id))
    });
    let global_tracks = build_global_tracks(job_id, &nodes, &mut decisions);
    AssociationOutput {
        decisions,
        global_tracks,
    }
}

fn collect_person_tracklets(cameras: &[CameraAnalyticsResult]) -> Vec<TrackletNode> {
    let mut output = Vec::new();
    for camera in cameras {
        for track in &camera.pipeline.tracks {
            if track.class_name != "person" {
                continue;
            }
            output.push(TrackletNode {
                camera_id: camera.camera_id.clone(),
                started_at_ms: apply_clock_offset(
                    track
                        .started_at_ms
                        .saturating_add(camera.processing_start_offset_ms),
                    camera.clock_offset_ms,
                ),
                ended_at_ms: apply_clock_offset(
                    track
                        .ended_at_ms
                        .saturating_add(camera.processing_start_offset_ms),
                    camera.clock_offset_ms,
                ),
                track: track.clone(),
            });
        }
    }
    output.sort_by(|left, right| {
        left.camera_id
            .cmp(&right.camera_id)
            .then_with(|| left.started_at_ms.cmp(&right.started_at_ms))
            .then_with(|| left.track.track_id.cmp(&right.track.track_id))
    });
    output
}

fn apply_clock_offset(value: u64, offset: i64) -> u64 {
    if offset >= 0 {
        value.saturating_add(offset as u64)
    } else {
        value.saturating_sub(offset.unsigned_abs())
    }
}

fn association_relations(request: &ClusterJobRequest) -> Vec<Relation> {
    let mut output = Vec::new();
    for edge in &request.topology {
        output.push(relation_from_edge(edge));
    }

    let mut overlap_groups: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for camera in &request.cameras {
        if let Some(group) = camera
            .overlap_group
            .as_deref()
            .filter(|group| !group.is_empty())
        {
            overlap_groups
                .entry(group)
                .or_default()
                .push(camera.camera_id.as_str());
        }
    }
    for (group, mut camera_ids) in overlap_groups {
        camera_ids.sort_unstable();
        camera_ids.dedup();
        for left in 0..camera_ids.len() {
            for right in (left + 1)..camera_ids.len() {
                output.push(Relation {
                    edge_id: Some(format!("overlap-group:{group}")),
                    source_camera_id: camera_ids[left].to_owned(),
                    target_camera_id: camera_ids[right].to_owned(),
                    edge_type: CameraEdgeType::Overlap,
                    minimum_travel_ms: 0,
                    maximum_travel_ms: request.association.overlap_tolerance_ms,
                    confidence: 1.0,
                });
            }
        }
    }
    output
}

fn relation_from_edge(edge: &CameraTopologyEdge) -> Relation {
    Relation {
        edge_id: Some(edge.edge_id.clone()),
        source_camera_id: edge.source_camera_id.clone(),
        target_camera_id: edge.target_camera_id.clone(),
        edge_type: edge.edge_type,
        minimum_travel_ms: edge.minimum_travel_ms,
        maximum_travel_ms: edge.maximum_travel_ms,
        confidence: edge.confidence,
    }
}

fn score_candidate(
    source: &TrackletNode,
    target: &TrackletNode,
    relation: &Relation,
    request: &ClusterJobRequest,
) -> Option<f32> {
    if source.track.class_name != target.track.class_name {
        return None;
    }
    let appearance = cosine_similarity(
        source.track.appearance_prototype.as_deref(),
        target.track.appearance_prototype.as_deref(),
    )?;
    if appearance < request.association.minimum_appearance_similarity {
        return None;
    }
    let temporal = temporal_score(source, target, relation, request);
    if temporal <= 0.0 {
        return None;
    }
    let quality = (source.track.maximum_confidence * target.track.maximum_confidence).sqrt();
    let score = match relation.edge_type {
        CameraEdgeType::Overlap => 0.70 * appearance + 0.20 * temporal + 0.10 * quality,
        CameraEdgeType::Transition => {
            0.65 * appearance + 0.20 * temporal + 0.10 * quality + 0.05 * relation.confidence
        }
    };
    Some(score.clamp(0.0, 1.0))
}

fn temporal_score(
    source: &TrackletNode,
    target: &TrackletNode,
    relation: &Relation,
    request: &ClusterJobRequest,
) -> f32 {
    match relation.edge_type {
        CameraEdgeType::Overlap => {
            let gap = if source.ended_at_ms < target.started_at_ms {
                target.started_at_ms - source.ended_at_ms
            } else if target.ended_at_ms < source.started_at_ms {
                source.started_at_ms - target.ended_at_ms
            } else {
                0
            };
            let tolerance = request.association.overlap_tolerance_ms.max(1);
            if gap > tolerance {
                0.0
            } else {
                1.0 - gap as f32 / tolerance as f32
            }
        }
        CameraEdgeType::Transition => {
            if target.started_at_ms < source.ended_at_ms {
                return 0.0;
            }
            let travel = target.started_at_ms - source.ended_at_ms;
            if travel < relation.minimum_travel_ms || travel > relation.maximum_travel_ms {
                return 0.0;
            }
            let midpoint = (relation.minimum_travel_ms + relation.maximum_travel_ms) as f32 / 2.0;
            let half_width =
                (relation.maximum_travel_ms - relation.minimum_travel_ms).max(1) as f32 / 2.0;
            (1.0 - (travel as f32 - midpoint).abs() / half_width).clamp(0.05, 1.0)
        }
    }
}

fn cosine_similarity(left: Option<&[f32]>, right: Option<&[f32]>) -> Option<f32> {
    let (left, right) = (left?, right?);
    if left.is_empty() || left.len() != right.len() {
        return None;
    }
    let mut dot = 0.0_f32;
    let mut left_norm = 0.0_f32;
    let mut right_norm = 0.0_f32;
    for (left, right) in left.iter().zip(right) {
        dot += left * right;
        left_norm += left * left;
        right_norm += right * right;
    }
    let denominator = (left_norm * right_norm).sqrt();
    if denominator <= f32::EPSILON {
        None
    } else {
        Some((dot / denominator).clamp(0.0, 1.0))
    }
}

fn decision_state(score: f32, request: &ClusterJobRequest) -> AssociationDecisionState {
    if score >= request.association.final_threshold {
        AssociationDecisionState::FinalMatch
    } else if score >= request.association.provisional_threshold {
        AssociationDecisionState::Provisional
    } else {
        AssociationDecisionState::Ambiguous
    }
}

fn explanation(
    source: &TrackletNode,
    target: &TrackletNode,
    relation: &Relation,
    appearance: f32,
    temporal: f32,
) -> String {
    format!(
        "{}:{} → {}:{} through {} evidence; appearance {:.3}, temporal {:.3}",
        source.camera_id,
        source.track.track_id,
        target.camera_id,
        target.track.track_id,
        match relation.edge_type {
            CameraEdgeType::Overlap => "overlap",
            CameraEdgeType::Transition => "directed transition",
        },
        appearance,
        temporal
    )
}

fn maximum_weight_assignment(scores: &[Vec<Option<f32>>]) -> Vec<(usize, usize, f32)> {
    if scores.is_empty() || scores[0].is_empty() {
        return Vec::new();
    }
    let rows = scores.len();
    let columns = scores[0].len();
    let size = rows.max(columns);
    let mut cost = vec![vec![1.0_f64; size + 1]; size + 1];
    for row in 0..rows {
        for column in 0..columns {
            cost[row + 1][column + 1] = scores[row][column]
                .map(|score| 1.0 - score as f64)
                .unwrap_or(2.0);
        }
    }

    let mut u = vec![0.0_f64; size + 1];
    let mut v = vec![0.0_f64; size + 1];
    let mut p = vec![0_usize; size + 1];
    let mut way = vec![0_usize; size + 1];
    for row in 1..=size {
        p[0] = row;
        let mut column0 = 0;
        let mut min_value = vec![f64::INFINITY; size + 1];
        let mut used = vec![false; size + 1];
        loop {
            used[column0] = true;
            let row0 = p[column0];
            let mut delta = f64::INFINITY;
            let mut column1 = 0;
            for column in 1..=size {
                if used[column] {
                    continue;
                }
                let current = cost[row0][column] - u[row0] - v[column];
                if current < min_value[column] {
                    min_value[column] = current;
                    way[column] = column0;
                }
                if min_value[column] < delta {
                    delta = min_value[column];
                    column1 = column;
                }
            }
            for column in 0..=size {
                if used[column] {
                    u[p[column]] += delta;
                    v[column] -= delta;
                } else {
                    min_value[column] -= delta;
                }
            }
            column0 = column1;
            if p[column0] == 0 {
                break;
            }
        }
        loop {
            let column1 = way[column0];
            p[column0] = p[column1];
            column0 = column1;
            if column0 == 0 {
                break;
            }
        }
    }

    let mut output = Vec::new();
    for column in 1..=size {
        let row = p[column];
        if row == 0 || row > rows || column > columns {
            continue;
        }
        if let Some(score) = scores[row - 1][column - 1] {
            output.push((row - 1, column - 1, score));
        }
    }
    output
}

fn build_global_tracks(
    job_id: Uuid,
    nodes: &[TrackletNode],
    decisions: &mut [AssociationDecision],
) -> Vec<GlobalTrack> {
    let mut disjoint = DisjointSet::new(nodes.len());
    let lookup: HashMap<(&str, &str), usize> = nodes
        .iter()
        .enumerate()
        .map(|(index, node)| {
            (
                (node.camera_id.as_str(), node.track.track_id.as_str()),
                index,
            )
        })
        .collect();

    for decision in decisions.iter_mut() {
        if decision.state == AssociationDecisionState::Ambiguous {
            continue;
        }
        let Some(&source) = lookup.get(&(
            decision.source_camera_id.as_str(),
            decision.source_track_id.as_str(),
        )) else {
            continue;
        };
        let Some(&target) = lookup.get(&(
            decision.target_camera_id.as_str(),
            decision.target_track_id.as_str(),
        )) else {
            continue;
        };
        if components_are_compatible(&mut disjoint, nodes, source, target) {
            disjoint.union(source, target);
        } else {
            decision.state = AssociationDecisionState::Ambiguous;
            decision.explanation.push_str(
                "; not merged because it would create overlapping identities in one camera",
            );
        }
    }

    let mut components: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for index in 0..nodes.len() {
        let root = disjoint.find(index);
        components.entry(root).or_default().push(index);
    }

    let mut output = Vec::new();
    for mut members in components.into_values() {
        members.sort_by_key(|index| nodes[*index].started_at_ms);
        let member_keys: BTreeSet<(&str, &str)> = members
            .iter()
            .map(|index| {
                (
                    nodes[*index].camera_id.as_str(),
                    nodes[*index].track.track_id.as_str(),
                )
            })
            .collect();
        let association_ids: Vec<_> = decisions
            .iter()
            .filter(|decision| decision.state != AssociationDecisionState::Ambiguous)
            .filter(|decision| {
                member_keys.contains(&(
                    decision.source_camera_id.as_str(),
                    decision.source_track_id.as_str(),
                )) && member_keys.contains(&(
                    decision.target_camera_id.as_str(),
                    decision.target_track_id.as_str(),
                ))
            })
            .map(|decision| decision.association_id)
            .collect();
        let mut identity_parts: Vec<String> = member_keys
            .iter()
            .map(|(camera, track)| format!("{camera}:{track}"))
            .collect();
        identity_parts.sort();
        let identity = format!("{job_id}:{}", identity_parts.join("|"));
        let camera_ids: BTreeSet<_> = members
            .iter()
            .map(|index| nodes[*index].camera_id.clone())
            .collect();
        let association_confidence = decisions
            .iter()
            .filter(|decision| association_ids.contains(&decision.association_id))
            .map(|decision| decision.score)
            .reduce(f32::min);
        let track_confidence = members
            .iter()
            .map(|index| nodes[*index].track.maximum_confidence)
            .reduce(f32::min)
            .unwrap_or(0.0);
        let has_provisional = decisions.iter().any(|decision| {
            association_ids.contains(&decision.association_id)
                && decision.state == AssociationDecisionState::Provisional
        });
        output.push(GlobalTrack {
            global_id: Uuid::new_v5(&Uuid::NAMESPACE_OID, identity.as_bytes()),
            state: if camera_ids.len() == 1 {
                "observed_single_camera"
            } else if has_provisional {
                "provisional_multi_camera"
            } else {
                "observed_multi_camera"
            }
            .to_owned(),
            identity_confidence: association_confidence.unwrap_or(track_confidence),
            camera_ids: camera_ids.into_iter().collect(),
            segments: members
                .iter()
                .map(|index| GlobalTrackSegment {
                    camera_id: nodes[*index].camera_id.clone(),
                    local_track_id: nodes[*index].track.track_id.clone(),
                    class_name: nodes[*index].track.class_name.clone(),
                    started_at_ms: nodes[*index].started_at_ms,
                    ended_at_ms: nodes[*index].ended_at_ms,
                    observations: nodes[*index].track.observations,
                    track_confidence: nodes[*index].track.maximum_confidence,
                })
                .collect(),
            association_ids,
        });
    }
    output.sort_by_key(|track| track.global_id);
    output
}

fn components_are_compatible(
    disjoint: &mut DisjointSet,
    nodes: &[TrackletNode],
    left: usize,
    right: usize,
) -> bool {
    let left_root = disjoint.find(left);
    let right_root = disjoint.find(right);
    if left_root == right_root {
        return true;
    }
    for left_index in 0..nodes.len() {
        if disjoint.find(left_index) != left_root {
            continue;
        }
        for right_index in 0..nodes.len() {
            if disjoint.find(right_index) != right_root {
                continue;
            }
            if nodes[left_index].camera_id == nodes[right_index].camera_id
                && intervals_overlap(&nodes[left_index], &nodes[right_index])
            {
                return false;
            }
        }
    }
    true
}

fn intervals_overlap(left: &TrackletNode, right: &TrackletNode) -> bool {
    left.started_at_ms <= right.ended_at_ms && right.started_at_ms <= left.ended_at_ms
}

struct DisjointSet {
    parent: Vec<usize>,
    rank: Vec<u8>,
}

impl DisjointSet {
    fn new(size: usize) -> Self {
        Self {
            parent: (0..size).collect(),
            rank: vec![0; size],
        }
    }

    fn find(&mut self, value: usize) -> usize {
        if self.parent[value] != value {
            self.parent[value] = self.find(self.parent[value]);
        }
        self.parent[value]
    }

    fn union(&mut self, left: usize, right: usize) {
        let mut left_root = self.find(left);
        let mut right_root = self.find(right);
        if left_root == right_root {
            return;
        }
        if self.rank[left_root] < self.rank[right_root] {
            std::mem::swap(&mut left_root, &mut right_root);
        }
        self.parent[right_root] = left_root;
        if self.rank[left_root] == self.rank[right_root] {
            self.rank[left_root] += 1;
        }
    }
}

pub fn cluster_report(
    request: &ClusterJobRequest,
    cameras: &[CameraAnalyticsResult],
    failures: &[CameraProcessingFailure],
    decisions: &[AssociationDecision],
    global_tracks: &[GlobalTrack],
) -> Report {
    let observations = cameras
        .iter()
        .map(|camera| {
            format!(
                "{}: {} local tracks and {} events. View: {}",
                camera.label,
                camera.pipeline.tracks.len(),
                camera.pipeline.events.len(),
                camera.pipeline.view_description.description
            )
        })
        .chain(
            decisions
                .iter()
                .take(6)
                .map(|decision| decision.explanation.clone()),
        )
        .collect();
    let notable_event_ids = cameras
        .iter()
        .flat_map(|camera| camera.pipeline.events.iter())
        .take(8)
        .map(|event| event.event_id)
        .collect();
    let local_tracks: usize = cameras
        .iter()
        .map(|camera| camera.pipeline.tracks.len())
        .sum();
    let events: usize = cameras
        .iter()
        .map(|camera| camera.pipeline.events.len())
        .sum();
    let detector_observations: usize = cameras
        .iter()
        .map(|camera| camera.pipeline.observations_processed)
        .sum();
    let finalized = decisions
        .iter()
        .filter(|decision| decision.state == AssociationDecisionState::FinalMatch)
        .count();
    let provisional = decisions
        .iter()
        .filter(|decision| decision.state == AssociationDecisionState::Provisional)
        .count();
    let ambiguous = decisions
        .iter()
        .filter(|decision| decision.state == AssociationDecisionState::Ambiguous)
        .count();
    let mut notes = vec![
        "Cross-camera association is deterministic; Gemma cannot assign identities.".to_owned(),
        "Phase 0 appearance descriptors are color/texture prototypes; replace them with a calibrated person ReID model before production.".to_owned(),
    ];
    if request.topology.is_empty()
        && request
            .cameras
            .iter()
            .all(|camera| camera.overlap_group.is_none())
    {
        notes.push(
            "No overlap group or directed topology was supplied, so camera tracks remain separate global identities."
                .to_owned(),
        );
    }
    if ambiguous > 0 {
        notes.push(format!(
            "{ambiguous} ambiguous associations were retained without merging identities."
        ));
    }
    for failure in failures {
        notes.push(format!(
            "Camera {} failed and was excluded: {}",
            failure.camera_id, failure.error
        ));
    }
    let confidence = if detector_observations == 0 {
        0.0
    } else if decisions.is_empty() {
        1.0
    } else {
        decisions.iter().map(|decision| decision.score).sum::<f32>() / decisions.len() as f32
    };

    Report {
        headline: format!(
            "Cluster {}: {} identity records across {} successful cameras",
            request.cluster_id,
            global_tracks.len(),
            cameras.len()
        ),
        summary: format!(
            "Processed {local_tracks} confirmed local person tracklets and {events} camera events. Produced {} cross-camera decisions: {finalized} final, {provisional} provisional and {ambiguous} ambiguous. Identity records are not a unique-person count when cameras are unrelated.",
            decisions.len()
        ),
        notable_event_ids,
        observations,
        data_quality_notes: notes,
        confidence,
    }
}

pub fn aggregate_view_descriptions(cameras: &[CameraAnalyticsResult]) -> ViewDescription {
    if cameras.is_empty() {
        return ViewDescription {
            description: "No camera view was processed successfully.".to_owned(),
            scene_type: "multi-camera cluster".to_owned(),
            visible_areas: Vec::new(),
            notable_static_elements: Vec::new(),
            visibility_conditions: "Not assessed".to_owned(),
            confidence: 0.0,
            generated_by_model: false,
            model: None,
            fallback_reason: Some("all camera pipelines failed".to_owned()),
        };
    }

    let descriptions = cameras
        .iter()
        .map(|camera| {
            format!(
                "{} ({}): {}",
                camera.label,
                camera.pipeline.view_description.scene_type,
                truncate_chars(&camera.pipeline.view_description.description, 420)
            )
        })
        .collect::<Vec<_>>()
        .join(" ");
    let generated: Vec<_> = cameras
        .iter()
        .filter(|camera| camera.pipeline.view_description.generated_by_model)
        .collect();
    let confidence = if generated.is_empty() {
        0.0
    } else {
        generated
            .iter()
            .map(|camera| camera.pipeline.view_description.confidence)
            .sum::<f32>()
            / generated.len() as f32
    };
    let models: BTreeSet<_> = generated
        .iter()
        .filter_map(|camera| camera.pipeline.view_description.model.clone())
        .collect();
    let fallback_reasons: Vec<_> = cameras
        .iter()
        .filter_map(|camera| {
            camera
                .pipeline
                .view_description
                .fallback_reason
                .as_ref()
                .map(|reason| format!("{}: {reason}", camera.label))
        })
        .collect();

    ViewDescription {
        description: descriptions,
        scene_type: "multi-camera cluster".to_owned(),
        visible_areas: cameras
            .iter()
            .flat_map(|camera| {
                camera
                    .pipeline
                    .view_description
                    .visible_areas
                    .iter()
                    .map(|area| format!("{}: {area}", camera.label))
            })
            .take(48)
            .collect(),
        notable_static_elements: cameras
            .iter()
            .flat_map(|camera| {
                camera
                    .pipeline
                    .view_description
                    .notable_static_elements
                    .iter()
                    .map(|element| format!("{}: {element}", camera.label))
            })
            .take(48)
            .collect(),
        visibility_conditions: cameras
            .iter()
            .map(|camera| {
                format!(
                    "{}: {}",
                    camera.label, camera.pipeline.view_description.visibility_conditions
                )
            })
            .collect::<Vec<_>>()
            .join("; "),
        confidence,
        generated_by_model: !generated.is_empty(),
        model: if models.is_empty() {
            None
        } else {
            Some(models.into_iter().collect::<Vec<_>>().join(", "))
        },
        fallback_reason: if fallback_reasons.is_empty() {
            None
        } else {
            Some(fallback_reasons.join("; "))
        },
    }
}

fn truncate_chars(value: &str, maximum: usize) -> String {
    let mut characters = value.chars();
    let truncated: String = characters.by_ref().take(maximum).collect();
    if characters.next().is_some() {
        format!("{truncated}…")
    } else {
        truncated
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{
        AnalyticsPolicy, BackendKind, CameraAnalyticsResult, ClusterCameraInput, GemmaRun,
        PipelineResult,
    };

    fn report() -> Report {
        Report {
            headline: "No events".to_owned(),
            summary: "No events".to_owned(),
            notable_event_ids: Vec::new(),
            observations: Vec::new(),
            data_quality_notes: Vec::new(),
            confidence: 1.0,
        }
    }

    fn camera(
        camera_id: &str,
        overlap_group: Option<&str>,
        embedding: Vec<f32>,
    ) -> CameraAnalyticsResult {
        CameraAnalyticsResult {
            camera_id: camera_id.to_owned(),
            label: camera_id.to_owned(),
            overlap_group: overlap_group.map(ToOwned::to_owned),
            clock_offset_ms: 0,
            processing_start_offset_ms: 0,
            pipeline: PipelineResult {
                backend: BackendKind::Yolo26Command,
                model: "test".to_owned(),
                detector_fps: 5.0,
                observations_processed: 4,
                duration_ms: 1_000,
                tracks: vec![TrackSummary {
                    track_id: "local-1".to_owned(),
                    class_name: "person".to_owned(),
                    started_at_ms: 0,
                    ended_at_ms: 1_000,
                    duration_ms: 1_000,
                    observations: 4,
                    maximum_confidence: 0.95,
                    zones_visited: Vec::new(),
                    appearance_prototype: Some(embedding),
                }],
                events: Vec::new(),
                view_description: ViewDescription {
                    description: "An entrance view".to_owned(),
                    scene_type: "entrance".to_owned(),
                    visible_areas: vec!["doorway".to_owned()],
                    notable_static_elements: vec!["door".to_owned()],
                    visibility_conditions: "clear".to_owned(),
                    confidence: 0.9,
                    generated_by_model: true,
                    model: Some("test-vision".to_owned()),
                    fallback_reason: None,
                },
                deterministic_report: report(),
                report: report(),
                gemma: GemmaRun {
                    requested: false,
                    used: false,
                    model: None,
                    fallback_reason: None,
                },
            },
        }
    }

    fn request(overlap_group: Option<&str>) -> ClusterJobRequest {
        ClusterJobRequest {
            name: "test cluster".to_owned(),
            cluster_id: "test".to_owned(),
            cameras: ["a", "b"]
                .into_iter()
                .map(|camera_id| ClusterCameraInput {
                    camera_id: camera_id.to_owned(),
                    label: camera_id.to_owned(),
                    uri: format!("http://{camera_id}/stream"),
                    overlap_group: overlap_group.map(ToOwned::to_owned),
                    clock_offset_ms: 0,
                    policy: AnalyticsPolicy::default(),
                })
                .collect(),
            topology: Vec::new(),
            association: Default::default(),
            detector_fps: 5.0,
            monitor_duration_secs: 10,
            gemma_enabled: false,
            vlm_model: None,
        }
    }

    #[test]
    fn explicit_overlap_group_merges_matching_people() {
        let request = request(Some("shared"));
        let cameras = vec![
            camera("a", Some("shared"), vec![1.0, 0.0]),
            camera("b", Some("shared"), vec![1.0, 0.0]),
        ];
        let output = associate(Uuid::nil(), &request, &cameras);
        assert_eq!(output.decisions.len(), 1);
        assert_eq!(
            output.decisions[0].state,
            AssociationDecisionState::FinalMatch
        );
        assert_eq!(output.global_tracks.len(), 1);
        assert_eq!(output.global_tracks[0].segments.len(), 2);
    }

    #[test]
    fn no_relationship_does_not_create_all_to_all_associations() {
        let request = request(None);
        let cameras = vec![
            camera("a", None, vec![1.0, 0.0]),
            camera("b", None, vec![1.0, 0.0]),
        ];
        let output = associate(Uuid::nil(), &request, &cameras);
        assert!(output.decisions.is_empty());
        assert_eq!(output.global_tracks.len(), 2);
    }

    #[test]
    fn queued_processing_offset_prevents_false_simultaneous_match() {
        let request = request(Some("shared"));
        let first = camera("a", Some("shared"), vec![1.0, 0.0]);
        let mut delayed = camera("b", Some("shared"), vec![1.0, 0.0]);
        delayed.processing_start_offset_ms = 10_000;

        let output = associate(Uuid::nil(), &request, &[first, delayed]);
        assert!(output.decisions.is_empty());
        assert_eq!(output.global_tracks.len(), 2);
    }
}
