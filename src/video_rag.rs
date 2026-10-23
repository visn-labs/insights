//! Spatio-Temporal Graph and Event-Chain Video RAG (VideoStir ACL 2026 & EC-RAG).
#![allow(dead_code)]
//!
//! Four-phase structured Video-RAG:
//! - Phase 1: Query Decoupling (R_asr, R_ocr, R_det, R_type) and Hybrid Routing (OLAP fast path + H3 filtering).
//! - Phase 2: Spatio-temporal Graph G=(V,E) topology traversal + EC-RAG temporal event chains.
//! - Phase 3: Intent-Aware Reranking filtering candidates to ~14 intent-aligned keyframes.
//! - Phase 4: KV-cached bounded-token synthesis.

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use uuid::Uuid;

/// Decoupled structured retrieval request from natural-language enterprise query.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DecoupledQuery {
    pub raw_query: String,
    /// Physical concrete entities to detect (e.g. "delivery van", "pedestrian")
    pub detection_entities: Vec<String>,
    /// Text / license plates to search via OCR
    pub ocr_targets: Vec<String>,
    /// Acoustic cues / audio transcripts
    pub asr_keywords: Vec<String>,
    /// Target semantic query type ("causal_investigation", "footfall_aggregation", "temporal_progression")
    pub query_type: String,
    /// H3 spatial cell constraint for domain pre-filtering
    pub h3_filter: Option<u64>,
    /// Time window constraint in milliseconds [start_ms, end_ms]
    pub time_range_ms: Option<[u64; 2]>,
}

/// A node in the VideoStir spatio-temporal graph representing a coherent clip.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ClipNode {
    pub clip_id: Uuid,
    pub camera_id: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub h3_index: u64,
    pub detected_entities: Vec<String>,
    pub summary_text: String,
    pub visual_embedding: Vec<f32>,
}

/// Edge type connecting clip nodes in the spatio-temporal graph.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphEdgeKind {
    /// Chronological adjacency along timeline
    TemporalPrecedence,
    /// Cross-time or cross-camera visual / behavioral similarity
    SpatialSemanticBridge,
}

/// Weighted edge in the spatio-temporal graph.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GraphEdge {
    pub source_id: Uuid,
    pub target_id: Uuid,
    pub edge_kind: GraphEdgeKind,
    pub weight: f32,
}

/// Spatio-temporal topology graph G = (V, E).
pub struct SpatioTemporalGraph {
    pub nodes: HashMap<Uuid, ClipNode>,
    pub adjacency: HashMap<Uuid, Vec<GraphEdge>>,
}

impl SpatioTemporalGraph {
    pub fn new() -> Self {
        Self {
            nodes: HashMap::new(),
            adjacency: HashMap::new(),
        }
    }

    pub fn insert_clip(&mut self, clip: ClipNode) {
        let id = clip.clip_id;
        self.nodes.insert(id, clip);
        self.adjacency.entry(id).or_default();
    }

    pub fn add_edge(&mut self, edge: GraphEdge) {
        self.adjacency
            .entry(edge.source_id)
            .or_default()
            .push(edge.clone());

        // Undirected semantic bridges, directed temporal edges
        if edge.edge_kind == GraphEdgeKind::SpatialSemanticBridge {
            self.adjacency
                .entry(edge.target_id)
                .or_default()
                .push(GraphEdge {
                    source_id: edge.target_id,
                    target_id: edge.source_id,
                    edge_kind: GraphEdgeKind::SpatialSemanticBridge,
                    weight: edge.weight,
                });
        }
    }

    /// Multi-hop traversal: V_hop = { v_j | d(v_j, V_anc) <= L, w_ij >= eta }
    pub fn traverse_subgraph(
        &self,
        anchors: &[Uuid],
        max_hops: usize,
        min_weight: f32,
    ) -> Vec<Uuid> {
        let mut visited = HashSet::new();
        let mut queue = VecDeque::new();

        for &a in anchors {
            visited.insert(a);
            queue.push_back((a, 0usize));
        }

        let mut result = Vec::new();

        while let Some((curr, depth)) = queue.pop_front() {
            result.push(curr);
            if depth >= max_hops {
                continue;
            }

            if let Some(edges) = self.adjacency.get(&curr) {
                for edge in edges {
                    if edge.weight >= min_weight && !visited.contains(&edge.target_id) {
                        visited.insert(edge.target_id);
                        queue.push_back((edge.target_id, depth + 1));
                    }
                }
            }
        }

        result
    }
}

/// Intent-aligned retrieved keyframe candidate with VideoStir relevance score.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IntentAlignedKeyframe {
    pub clip_id: Uuid,
    pub camera_id: String,
    pub timestamp_ms: u64,
    pub intent_relevance_score: f32,
    pub summary: String,
}

/// Response returned by the Spatio-Temporal Video-RAG Engine.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VideoRagResponse {
    pub decoupled_query: DecoupledQuery,
    pub candidate_clips_considered: usize,
    pub hops_traversed: usize,
    pub intent_keyframes: Vec<IntentAlignedKeyframe>,
    pub context_tokens_estimated: usize,
    pub synthesis_summary: String,
    pub latency_ms: f32,
}

/// Decouples a natural language enterprise query into structured components.
pub fn decouple_enterprise_query(query: &str) -> DecoupledQuery {
    let lower = query.to_lowercase();
    let mut detection_entities = Vec::new();
    let mut query_type = "visual_semantic_search".to_string();

    if lower.contains("car")
        || lower.contains("vehicle")
        || lower.contains("truck")
        || lower.contains("van")
    {
        detection_entities.push("vehicle".into());
    }
    if lower.contains("person") || lower.contains("pedestrian") || lower.contains("crowd") {
        detection_entities.push("pedestrian".into());
    }
    if lower.contains("barrier") || lower.contains("box") || lower.contains("package") {
        detection_entities.push("barrier".into());
    }

    if lower.contains("before")
        || lower.contains("after")
        || lower.contains("why")
        || lower.contains("preceded")
    {
        query_type = "causal_investigation".to_string();
    } else if lower.contains("count") || lower.contains("how many") || lower.contains("footfall") {
        query_type = "footfall_aggregation".to_string();
    }

    DecoupledQuery {
        raw_query: query.to_string(),
        detection_entities,
        ocr_targets: Vec::new(),
        asr_keywords: Vec::new(),
        query_type,
        h3_filter: None,
        time_range_ms: None,
    }
}

/// Executes the full 4-Phase Spatio-Temporal Video RAG pipeline.
pub fn execute_video_rag(
    graph: &SpatioTemporalGraph,
    query: &str,
    max_hops: usize,
    min_edge_weight: f32,
    target_keyframes: usize,
) -> VideoRagResponse {
    let start = std::time::Instant::now();
    let decoupled = decouple_enterprise_query(query);

    // Phase 1: Locate top anchor nodes matching query entities
    let mut anchor_nodes = Vec::new();
    for (id, node) in &graph.nodes {
        let mut matches = false;
        for target in &decoupled.detection_entities {
            if node.detected_entities.iter().any(|e| e.contains(target)) {
                matches = true;
                break;
            }
        }
        if matches || decoupled.detection_entities.is_empty() {
            anchor_nodes.push(*id);
        }
    }

    // Phase 2: Spatio-Temporal Graph Traversal & EC-RAG Event Chain Extraction
    let traversed_subgraph = graph.traverse_subgraph(&anchor_nodes, max_hops, min_edge_weight);

    // Phase 3: Intent-Aware Reranking (VideoStir scorer simulation)
    let mut scored_keyframes = Vec::new();
    for clip_id in &traversed_subgraph {
        if let Some(clip) = graph.nodes.get(clip_id) {
            // Compute simulated intent alignment score
            let mut score = 0.5f32;
            for ent in &decoupled.detection_entities {
                if clip.detected_entities.contains(ent) {
                    score += 0.35;
                }
            }
            if decoupled.query_type == "causal_investigation" {
                score += 0.10;
            }

            scored_keyframes.push(IntentAlignedKeyframe {
                clip_id: *clip_id,
                camera_id: clip.camera_id.clone(),
                timestamp_ms: clip.start_ms,
                intent_relevance_score: score.clamp(0.0, 1.0),
                summary: clip.summary_text.clone(),
            });
        }
    }

    scored_keyframes.sort_by(|a, b| {
        b.intent_relevance_score
            .partial_cmp(&a.intent_relevance_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let top_keyframes: Vec<_> = scored_keyframes
        .into_iter()
        .take(target_keyframes.min(14))
        .collect();

    // Phase 4: Token estimation (~140 tokens per keyframe + auxiliary text = ~2000 tokens)
    let estimated_tokens = top_keyframes.len() * 140;

    let synthesis = format!(
        "Retrieved {} candidate keyframes for query '{}' from {} clips. No temporal or causal claim has been verified.",
        top_keyframes.len(),
        query,
        traversed_subgraph.len()
    );

    let elapsed = start.elapsed().as_secs_f32() * 1000.0;

    VideoRagResponse {
        decoupled_query: decoupled,
        candidate_clips_considered: graph.nodes.len(),
        hops_traversed: max_hops,
        intent_keyframes: top_keyframes,
        context_tokens_estimated: estimated_tokens,
        synthesis_summary: synthesis,
        latency_ms: elapsed,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn query_decoupling_identifies_entities_and_causal_type() {
        let q = "What vehicles entered before the pedestrian altercation?";
        let decoupled = decouple_enterprise_query(q);

        assert_eq!(decoupled.query_type, "causal_investigation");
        assert!(decoupled
            .detection_entities
            .contains(&"vehicle".to_string()));
        assert!(decoupled
            .detection_entities
            .contains(&"pedestrian".to_string()));
    }

    #[test]
    fn spatio_temporal_graph_traversal_gathers_subgraph() {
        let mut graph = SpatioTemporalGraph::new();
        let c1 = Uuid::now_v7();
        let c2 = Uuid::now_v7();
        let c3 = Uuid::now_v7();

        graph.insert_clip(ClipNode {
            clip_id: c1,
            camera_id: "cam_1".into(),
            start_ms: 1000,
            end_ms: 4000,
            h3_index: 100,
            detected_entities: vec!["vehicle".into()],
            summary_text: "Van arriving".into(),
            visual_embedding: vec![0.1; 64],
        });

        graph.insert_clip(ClipNode {
            clip_id: c2,
            camera_id: "cam_1".into(),
            start_ms: 4001,
            end_ms: 8000,
            h3_index: 100,
            detected_entities: vec!["vehicle".into(), "pedestrian".into()],
            summary_text: "Driver exiting".into(),
            visual_embedding: vec![0.1; 64],
        });

        graph.insert_clip(ClipNode {
            clip_id: c3,
            camera_id: "cam_2".into(),
            start_ms: 8001,
            end_ms: 12000,
            h3_index: 101,
            detected_entities: vec!["pedestrian".into()],
            summary_text: "Pedestrian walking".into(),
            visual_embedding: vec![0.1; 64],
        });

        // Add temporal edge c1 -> c2 and semantic bridge c2 <-> c3
        graph.add_edge(GraphEdge {
            source_id: c1,
            target_id: c2,
            edge_kind: GraphEdgeKind::TemporalPrecedence,
            weight: 0.95,
        });
        graph.add_edge(GraphEdge {
            source_id: c2,
            target_id: c3,
            edge_kind: GraphEdgeKind::SpatialSemanticBridge,
            weight: 0.85,
        });

        let response = execute_video_rag(&graph, "van and pedestrian", 2, 0.70, 14);
        assert_eq!(response.candidate_clips_considered, 3);
        assert!(!response.intent_keyframes.is_empty());
        assert!(response.context_tokens_estimated > 0);
    }
}
