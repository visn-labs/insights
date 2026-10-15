//! Hybrid clip retrieval. Filter before sending evidence to the local model worker.
use crate::{
    config::Config,
    domain::{CameraProfile, MemoryEvent, MemoryQueryMatch, MemoryQueryRequest},
    memory,
};
use anyhow::{bail, Context};
use serde::Deserialize;
use serde_json::json;
use std::{
    collections::{HashMap, HashSet},
    time::Duration,
};
use uuid::Uuid;

#[derive(Deserialize)]
struct RankedItem {
    event_id: Uuid,
    score: f32,
}
#[derive(Deserialize)]
struct SearchResult {
    items: Vec<RankedItem>,
    mode: String,
    notes: Vec<String>,
    #[serde(default)]
    provenance: serde_json::Value,
}

pub struct RetrievalResult {
    pub matches: Vec<MemoryQueryMatch>,
    pub considered: usize,
    pub mode: String,
    pub notes: Vec<String>,
    pub provenance: serde_json::Value,
}

pub fn eligible(request: &MemoryQueryRequest, event: &MemoryEvent) -> bool {
    request.job_id.is_none_or(|id| event.job_id == id)
        && request
            .cluster_id
            .as_ref()
            .is_none_or(|id| event.cluster_id.as_ref() == Some(id))
        && (request.camera_ids.is_empty() || request.camera_ids.contains(&event.camera_id))
        && request.start_ms.is_none_or(|start| event.end_ms >= start)
        && request.end_ms.is_none_or(|end| event.start_ms <= end)
}

pub async fn search(
    config: &Config,
    request: &MemoryQueryRequest,
    candidates: Vec<(MemoryEvent, CameraProfile)>,
) -> RetrievalResult {
    let indexed_count = candidates.len();
    let filtered: Vec<_> = candidates
        .into_iter()
        .filter(|(event, _)| eligible(request, event))
        .collect();
    let mut lexical_request = request.clone();
    lexical_request.limit = 50;
    let (lexical, _) = memory::retrieve(
        &lexical_request,
        filtered.iter().map(|(e, c)| (e, c)).collect(),
    );
    let mut result = RetrievalResult {
        matches: lexical.clone(),
        considered: filtered.len(),
        mode: "local_lexical_metadata".into(),
        notes: Vec::new(),
        provenance: serde_json::Value::Null,
    };
    if filtered.is_empty() {
        result.mode = if indexed_count == 0 {
            "not_run_empty_index"
        } else {
            "not_run_filtered_empty"
        }
        .into();
        result.notes.push(if indexed_count == 0 {
            "No indexed clips are available in this server's data directory. Complete a recording before searching.".into()
        } else {
            format!("{indexed_count} indexed clips exist, but none match the recording, cluster, camera or time filters. Select a completed recording or use Search all recordings. No retrieval model was called.")
        });
    }
    if request.semantic_enabled && !filtered.is_empty() {
        if let Some(url) = &config.retrieval_url {
            match remote_search(
                url,
                config.retrieval_timeout_secs,
                request,
                &filtered,
                &lexical,
            )
            .await
            {
                Ok((matches, mode, notes, provenance)) => {
                    result.matches = matches;
                    result.mode = mode;
                    result.notes = notes;
                    result.provenance = provenance;
                }
                Err(error) => result.notes.push(format!(
                    "Semantic retrieval unavailable; using keyword results: {error}"
                )),
            }
        } else {
            result.notes.push("Semantic retrieval is not configured; using keyword results. Set VISN_RETRIEVAL_URL to the local Qwen retrieval service.".into());
        }
    }
    result.matches.truncate(request.limit.clamp(1, 50));
    for (index, item) in result.matches.iter_mut().enumerate() {
        item.rank = index + 1;
    }
    result
}

async fn remote_search(
    url: &str,
    timeout: u64,
    request: &MemoryQueryRequest,
    candidates: &[(MemoryEvent, CameraProfile)],
    lexical: &[MemoryQueryMatch],
) -> anyhow::Result<(
    Vec<MemoryQueryMatch>,
    String,
    Vec<String>,
    serde_json::Value,
)> {
    let events: Vec<_>=candidates.iter().map(|(event,_)| {
        Ok(json!({"event_id":event.event_id,
            // Source intervals avoid copy-mode keyframe padding and reference-mode full clips.
            "source_path":std::path::absolute(&event.source_evidence_path).context("invalid source evidence path")?,
            "start_ms":event.start_ms,"end_ms":event.end_ms}))
    }).collect::<anyhow::Result<_>>()?;
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(timeout))
        .build()?;
    let response=client.post(format!("{}/search",url.trim_end_matches('/'))).json(&json!({
        "query":request.query,"events":events,"limit":request.limit.clamp(1,50),"rerank":request.rerank_enabled,
        "lexical_ids":lexical.iter().map(|item|item.event.event_id).collect::<Vec<_>>()
    })).send().await.context("Qwen retrieval service request failed")?.error_for_status().context("Qwen retrieval service returned an error")?;
    let body: SearchResult = response
        .json()
        .await
        .context("invalid retrieval response")?;
    validate(
        &body,
        candidates.iter().map(|(e, _)| e.event_id).collect(),
        request.limit.clamp(1, 50),
    )?;
    let by_id: HashMap<_, _> = candidates.iter().map(|(e, _)| (e.event_id, e)).collect();
    let terms: HashMap<_, _> = lexical
        .iter()
        .map(|m| (m.event.event_id, m.matched_terms.clone()))
        .collect();
    let matches = body
        .items
        .into_iter()
        .enumerate()
        .map(|(index, item)| MemoryQueryMatch {
            rank: index + 1,
            score: item.score,
            matched_terms: terms.get(&item.event_id).cloned().unwrap_or_default(),
            event: by_id[&item.event_id].clone(),
        })
        .collect();
    Ok((matches, body.mode, body.notes, body.provenance))
}

fn validate(body: &SearchResult, allowed: HashSet<Uuid>, limit: usize) -> anyhow::Result<()> {
    if !matches!(body.mode.as_str(), "hybrid_rrf" | "hybrid_qwen_reranker")
        || body.items.len() > limit
    {
        bail!("invalid retrieval mode or result count");
    }
    let mut seen = HashSet::new();
    for item in &body.items {
        if !allowed.contains(&item.event_id)
            || !seen.insert(item.event_id)
            || !item.score.is_finite()
            || !(0.0..=1.0).contains(&item.score)
        {
            bail!("invalid, duplicate or unfiltered retrieval result");
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn empty_index_is_not_reported_as_keyword_search() {
        let config = Config::from_env().unwrap();
        let request = serde_json::from_value(json!({"query":"truck"})).unwrap();
        let result = search(&config, &request, vec![]).await;
        assert_eq!(result.mode, "not_run_empty_index");
        assert!(result.notes[0].contains("No indexed clips"));
    }
    #[tokio::test]
    async fn filters_candidates_and_keeps_keywords_when_worker_is_unavailable() {
        let event: MemoryEvent = serde_json::from_value(json!({
            "event_id": Uuid::now_v7(), "job_id": Uuid::now_v7(),
            "camera_id":"front", "cluster_id":"street", "start_ms":100,
            "end_ms":200, "duration_ms":100, "activity_mean":0.1,
            "activity_peak":0.2, "quality":0.8, "boundary_reason":"test",
            "thumbnail_url":"", "evidence_url":"",
            "description": {"summary":"red car", "scene_type":"street",
                "conditions":"day", "confidence":0.5, "generated_by_model":false}
        }))
        .unwrap();
        let camera: CameraProfile = serde_json::from_value(
            json!({"live_url":"http://localhost/test", "camera_id":"front"}),
        )
        .unwrap();
        let request: MemoryQueryRequest = serde_json::from_value(json!({
            "query":"red car", "camera_ids":["front"], "cluster_id":"street",
            "start_ms":100, "end_ms":200
        }))
        .unwrap();
        assert!(eligible(&request, &event));
        let mut scoped = request.clone();
        scoped.job_id = Some(Uuid::nil());
        assert!(!eligible(&scoped, &event));
        let excluded_result = search(
            &Config::from_env().unwrap(),
            &scoped,
            vec![(event.clone(), camera.clone())],
        )
        .await;
        assert_eq!(excluded_result.mode, "not_run_filtered_empty");
        assert!(excluded_result.notes[0].contains("1 indexed clips exist"));
        let mut excluded = event.clone();
        excluded.camera_id = "back".into();
        assert!(!eligible(&request, &excluded));
        excluded = event.clone();
        excluded.cluster_id = Some("elsewhere".into());
        assert!(!eligible(&request, &excluded));
        excluded = event.clone();
        excluded.start_ms = 201;
        assert!(!eligible(&request, &excluded));
        let mut config = Config::from_env().unwrap();
        config.retrieval_url = Some("http://127.0.0.1:0".into());
        config.retrieval_timeout_secs = 1;
        let result = search(
            &config,
            &request,
            vec![(event.clone(), camera.clone()), (excluded, camera)],
        )
        .await;
        assert_eq!(result.considered, 1);
        assert_eq!(result.mode, "local_lexical_metadata");
        assert_eq!(result.matches[0].event.event_id, event.event_id);
        assert!(result.notes[0].contains("using keyword results"));
    }

    #[test]
    fn rejects_unfiltered_duplicate_and_invalid_scores() {
        let a = Uuid::now_v7();
        let b = Uuid::now_v7();
        for items in [
            vec![RankedItem {
                event_id: b,
                score: 0.5,
            }],
            vec![RankedItem {
                event_id: a,
                score: f32::NAN,
            }],
            vec![
                RankedItem {
                    event_id: a,
                    score: 0.4,
                },
                RankedItem {
                    event_id: a,
                    score: 0.5,
                },
            ],
        ] {
            assert!(validate(
                &SearchResult {
                    items,
                    mode: "hybrid_rrf".into(),
                    provenance: serde_json::Value::Null,
                    notes: vec![]
                },
                HashSet::from([a]),
                10
            )
            .is_err());
        }
        assert!(validate(
            &SearchResult {
                items: vec![RankedItem {
                    event_id: a,
                    score: 0.8
                }],
                mode: "hybrid_qwen_reranker".into(),
                provenance: serde_json::Value::Null,
                notes: vec![]
            },
            HashSet::from([a]),
            10
        )
        .is_ok());
    }
}
