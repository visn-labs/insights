//! Experiment 2 observes ordered visual evidence after retrieval; it never changes rankings.
use crate::{
    config::Config,
    domain::{MemoryEvent, SequenceInterpretation},
};
use anyhow::{bail, Context};
use serde::Deserialize;
use serde_json::json;
use std::{collections::HashSet, time::Duration};

pub fn window(event: &MemoryEvent) -> (u64, u64) {
    let start = event.start_ms.saturating_sub(2_000);
    let source_end = event.source_duration_ms.max(event.end_ms);
    let end = event
        .end_ms
        .saturating_add(2_000)
        .min(source_end)
        .min(start.saturating_add(20_000));
    (start, end)
}

pub async fn interpret(
    config: &Config,
    query: &str,
    events: &[MemoryEvent],
) -> anyhow::Result<Vec<SequenceInterpretation>> {
    interpret_with_tracks(config, query, events, &[], None).await
}

pub async fn interpret_with_tracks(
    config: &Config,
    query: &str,
    events: &[MemoryEvent],
    cameras: &[crate::domain::MemoryCameraResult],
    entity_id: Option<uuid::Uuid>,
) -> anyhow::Result<Vec<SequenceInterpretation>> {
    if events.is_empty() {
        return Ok(vec![]);
    }
    let url = config
        .retrieval_url
        .as_ref()
        .context("Sequence interpretation requires the Qwen worker (VISN_RETRIEVAL_URL)")?;
    let selected: Vec<_> = events.iter().take(3).collect();
    let candidates = selected.iter().map(|event| {
        let (start,end) = window(event);
        let grounding = if let Some(id) = entity_id {
            let graph = cameras.iter().filter(|camera| camera.camera.camera_id == event.camera_id
                && camera.events.iter().any(|e| e.event_id == event.event_id && e.job_id == event.job_id))
                .filter_map(|camera| camera.long_memory.as_ref())
                .find(|graph| graph.version == crate::long_memory::VERSION
                    && graph.entities.iter().any(|entity| entity.entity_id == id && entity.job_id == event.job_id && entity.camera_id == event.camera_id))
                .context("Selected entity has no current tracker evidence in this recording")?;
            let entity = graph.entities.iter().find(|entity| entity.entity_id == id).unwrap();
            let observations: Vec<_> = entity.observations.iter().filter(|o| o.bbox.is_some()
                && start <= o.timestamp_ms && o.timestamp_ms < end).collect();
            Some(json!({"source_sha256":graph.source_sha256,"entity_id":entity.entity_id,
                "class_name":entity.class_name,"observations":observations}))
        } else { None };
        Ok(json!({"grounding":grounding,"event_id":event.event_id, "source_path":std::path::absolute(&event.source_evidence_path)?,
            "start_ms":start,"end_ms":end, "metadata":{"camera_id":event.camera_id,
            "original_start_ms":event.start_ms,"original_end_ms":event.end_ms,
            "summary":event.description.summary,"activity_mean":event.activity_mean,
            "activity_peak":event.activity_peak}}))
    }).collect::<anyhow::Result<Vec<_>>>()?;
    #[derive(Deserialize)]
    struct Response {
        interpretations: Vec<SequenceInterpretation>,
    }
    let response = reqwest::Client::new()
        .post(format!("{}/interpret", url.trim_end_matches('/')))
        .timeout(Duration::from_secs(config.retrieval_timeout_secs))
        .json(&json!({"query":query,"events":candidates}))
        .send()
        .await?
        .error_for_status()?
        .json::<Response>()
        .await?;
    if response.interpretations.len() != selected.len() {
        bail!("Incomplete sequence interpretation response");
    }
    let mut seen = HashSet::new();
    for result in &response.interpretations {
        let event = selected
            .iter()
            .find(|event| event.event_id == result.event_id)
            .context("Unknown interpretation event ID")?;
        if !seen.insert(result.event_id) {
            bail!("Duplicate interpretation event ID");
        }
        validate(result, window(event))?;
    }
    Ok(response.interpretations)
}

fn validate(result: &SequenceInterpretation, bounds: (u64, u64)) -> anyhow::Result<()> {
    let times = &result.sampled_timestamps_ms;
    if (result.start_ms, result.end_ms) != bounds
        || !matches!(
            result.status.as_str(),
            "supported" | "not_observed" | "uncertain"
        )
        || result.answer.trim().is_empty()
        || result.answer.len() > 6400
        || !(2..=16).contains(&times.len())
        || times.windows(2).any(|pair| pair[0] >= pair[1])
        || times
            .iter()
            .any(|time| *time < bounds.0 || *time >= bounds.1)
        || !result.elapsed_secs.is_finite()
        || result.elapsed_secs < 0.0
    {
        bail!("Invalid sequence interpretation or timestamps");
    }
    if result.frames.len() != times.len()
        || result.frames.iter().zip(times).any(|(frame, time)| {
            frame.timestamp_ms != *time
                || frame.image.len() > 1_000_000
                || frame
                    .image
                    .strip_prefix("data:image/jpeg;base64,")
                    .is_none_or(|encoded| {
                        encoded.is_empty()
                            || !encoded
                                .bytes()
                                .all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b))
                    })
        })
    {
        bail!("Invalid visual evidence frames");
    }
    for frame in &result.frames {
        if frame
            .decoded_timestamp_ms
            .is_some_and(|t| t < bounds.0 || t >= bounds.1)
            || frame.target_crop.as_ref().is_some_and(|crop| {
                crop.len() > 1_000_000
                    || crop
                        .strip_prefix("data:image/jpeg;base64,")
                        .is_none_or(|encoded| {
                            encoded.is_empty()
                                || !encoded
                                    .bytes()
                                    .all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b))
                        })
            })
        {
            bail!("Invalid decoded frame or target crop");
        }
    }
    let mut seen = HashSet::new();
    for observation in &result.observations {
        if times.get(observation.frame_index) != Some(&observation.timestamp_ms)
            || !seen.insert(observation.frame_index)
            || !matches!(
                observation.target_visibility.as_str(),
                "visible" | "absent" | "unclear"
            )
            || observation.description.trim().is_empty()
            || observation.description.len() > 2400
        {
            bail!("Invalid frame citation");
        }
    }
    if result.status == "supported"
        && result
            .observations
            .iter()
            .filter(|o| o.target_visibility == "visible")
            .count()
            < 2
    {
        bail!("Action claim needs two frames");
    }
    if result.status == "supported" {
        if matches!(
            result.provenance["prompt_version"].as_str(),
            Some("blind-state-consistency-v9" | "track-visibility-quality-v10")
        ) {
            let gate = &result.provenance["state_verification"];
            let claim = result
                .claimed_transition
                .as_deref()
                .context("Missing checked transition")?;
            if gate["version"] != "blind-visual-states-v1"
                || gate["status"] != "passed"
                || gate["identity"] != "same"
                || gate["reference_visible"] != true
                || gate["transition"].as_str() != Some(claim)
                || !matches!(
                    claim,
                    "approaching"
                        | "receding"
                        | "opening"
                        | "closing"
                        | "placing"
                        | "removing"
                        | "arriving"
                        | "leaving"
                )
                || gate["frame_indices"]
                    != json!(result
                        .observations
                        .iter()
                        .map(|o| o.frame_index)
                        .collect::<Vec<_>>())
            {
                bail!("Action claim did not pass the visual-state gate");
            }
        }
        let images: HashSet<_> = result
            .observations
            .iter()
            .map(|o| result.frames[o.frame_index].image.as_str())
            .collect();
        if images.len() < 2 {
            bail!("Action claim cites repeated images");
        }
    }
    if result.status == "not_observed"
        && result.provenance["prompt_version"] == "track-visibility-quality-v10"
        && result.provenance["track_grounding"]["status"] == "explicit_entity"
    {
        bail!("Selected-track uncertainty cannot establish scene-wide absence");
    }
    if result.status == "not_observed"
        && (seen.len() < 2
            || !seen.contains(&0)
            || !seen.contains(&(times.len() - 1))
            || result
                .observations
                .iter()
                .any(|o| o.target_visibility != "absent"))
    {
        bail!("Not-observed answer needs absent boundary observations");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_invented_timestamp_and_single_frame_action() {
        let mut value: SequenceInterpretation = serde_json::from_value(json!({"event_id":uuid::Uuid::nil(),
            "status":"supported","answer":"An object leaves", "start_ms":0,"end_ms":1000,
            "sampled_timestamps_ms":[250,750],"frames":[{"timestamp_ms":250,"image":"data:image/jpeg;base64,YQ=="},{"timestamp_ms":750,"image":"data:image/jpeg;base64,Yg=="}],"observations":[{"frame_index":0,"timestamp_ms":250,"target_visibility":"visible","description":"present"},
                {"frame_index":1,"timestamp_ms":750,"target_visibility":"visible","description":"closer"}],"limitations":[],"cached":false,
            "elapsed_secs":1.0,"provenance":{}})).unwrap();
        assert!(validate(&value, (0, 1000)).is_ok());
        value.provenance = json!({"prompt_version":"blind-state-consistency-v9"});
        assert!(validate(&value, (0, 1000)).is_err());
        value.claimed_transition = Some("opening".into());
        value.provenance["state_verification"] = json!({"version":"blind-visual-states-v1","status":"passed","identity":"same","reference_visible":true,"transition":"closing","frame_indices":[0,1]});
        assert!(validate(&value, (0, 1000)).is_err());
        value.provenance["state_verification"]["transition"] = json!("opening");
        assert!(validate(&value, (0, 1000)).is_ok());
        value.provenance = json!({});
        value.frames[0].decoded_timestamp_ms = Some(1000);
        assert!(validate(&value, (0, 1000)).is_err());
        value.frames[0].decoded_timestamp_ms = Some(300);
        value.frames[0].target_crop = Some("data:text/html;base64,YQ==".into());
        assert!(validate(&value, (0, 1000)).is_err());
        value.frames[0].target_crop = Some("data:image/jpeg;base64,YQ==".into());
        assert!(validate(&value, (0, 1000)).is_ok());
        let distinct = value.frames[1].image.clone();
        value.frames[1].image = value.frames[0].image.clone();
        assert!(validate(&value, (0, 1000)).is_err());
        value.frames[1].image = distinct;
        value.observations[1].target_visibility = "absent".into();
        assert!(validate(&value, (0, 1000)).is_err());
        value.status = "not_observed".into();
        assert!(validate(&value, (0, 1000)).is_err());
        value.observations[0].target_visibility = "absent".into();
        assert!(validate(&value, (0, 1000)).is_ok());
        value.provenance = json!({"prompt_version":"track-visibility-quality-v10","track_grounding":{"status":"explicit_entity"}});
        assert!(validate(&value, (0, 1000)).is_err());
        value.provenance = json!({});
        value.observations[0].target_visibility = "visible".into();
        value.observations[1].target_visibility = "visible".into();
        value.status = "supported".into();
        value.observations[1].timestamp_ms = 900;
        assert!(validate(&value, (0, 1000)).is_err());
        value.observations.pop();
        assert!(validate(&value, (0, 1000)).is_err());
        value.status = "uncertain".into();
        assert!(validate(&value, (0, 1000)).is_ok());
    }
}
