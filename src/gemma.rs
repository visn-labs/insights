use std::{collections::BTreeSet, sync::Arc, time::Duration};

use anyhow::{anyhow, bail, Context};
use regex::Regex;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::{config::Config, domain::Report};

#[derive(Clone)]
pub struct GemmaClient {
    config: Arc<Config>,
    client: Client,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ModelInfo {
    pub id: String,
}

#[derive(Debug, Deserialize)]
struct ModelsResponse {
    data: Vec<ModelInfo>,
}

#[derive(Debug, Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
}

#[derive(Debug, Deserialize)]
struct Choice {
    message: ChatMessage,
}

#[derive(Debug, Deserialize)]
struct ChatMessage {
    content: String,
}

impl GemmaClient {
    pub fn new(config: Arc<Config>) -> anyhow::Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.gemma_timeout_secs))
            .build()
            .context("build Gemma HTTP client")?;
        Ok(Self { config, client })
    }

    pub async fn models(&self) -> anyhow::Result<Vec<ModelInfo>> {
        let response = self
            .client
            .get(format!("{}/models", self.config.gemma_base_url))
            .bearer_auth(&self.config.gemma_api_key)
            .send()
            .await
            .context("connect to LM Studio")?
            .error_for_status()
            .context("LM Studio model-list response")?
            .json::<ModelsResponse>()
            .await
            .context("decode LM Studio model list")?;
        Ok(response.data)
    }

    pub async fn selected_model(&self) -> anyhow::Result<String> {
        if let Some(model) = &self.config.gemma_model {
            return Ok(model.clone());
        }
        let models = self.models().await?;
        models
            .iter()
            .find(|model| {
                let id = model.id.to_ascii_lowercase();
                id.contains("gemma-4") && id.contains("26b") && id.contains("a4b")
            })
            .or_else(|| models.first())
            .map(|model| model.id.clone())
            .ok_or_else(|| anyhow!("LM Studio has no loaded model"))
    }

    pub async fn generate_report(
        &self,
        deterministic: &Report,
    ) -> anyhow::Result<(Report, String)> {
        let model = self.selected_model().await?;
        let facts = serde_json::to_string_pretty(deterministic)?;
        let schema = json!({
            "headline": "string",
            "summary": "string",
            "notable_event_ids": ["UUID from facts only"],
            "observations": ["string"],
            "data_quality_notes": ["string"],
            "confidence": "number from 0 to 1"
        });
        let system = "You produce a concise camera analytics report using only supplied deterministic facts. Do not invent counts, events, identities, motives, times, or evidence. Numeric values are authoritative. Only use event IDs already supplied. Return one JSON object and no markdown.";
        let user = format!(
            "FACT DOCUMENT:\n{facts}\n\nOUTPUT SCHEMA:\n{}\nRewrite the headline and narrative for clarity while preserving every fact.",
            serde_json::to_string_pretty(&schema)?
        );
        let body = json!({
            "model": model,
            "temperature": 0.1,
            "stream": false,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ]
        });
        let response = self
            .client
            .post(format!("{}/chat/completions", self.config.gemma_base_url))
            .bearer_auth(&self.config.gemma_api_key)
            .json(&body)
            .send()
            .await
            .context("call LM Studio chat completions")?
            .error_for_status()
            .context("LM Studio chat response")?
            .json::<ChatResponse>()
            .await
            .context("decode LM Studio chat response")?;
        let content = response
            .choices
            .first()
            .ok_or_else(|| anyhow!("LM Studio returned no choices"))?
            .message
            .content
            .trim();
        let content = strip_json_fence(content);
        let candidate: Report = serde_json::from_str(content)
            .with_context(|| format!("Gemma did not return valid report JSON: {content}"))?;
        validate_report(deterministic, &candidate)?;
        Ok((candidate, model))
    }
}

fn strip_json_fence(content: &str) -> &str {
    content
        .strip_prefix("```json")
        .or_else(|| content.strip_prefix("```"))
        .and_then(|inner| inner.strip_suffix("```"))
        .map(str::trim)
        .unwrap_or(content)
}

fn validate_report(facts: &Report, candidate: &Report) -> anyhow::Result<()> {
    if !(0.0..=1.0).contains(&candidate.confidence) {
        bail!("Gemma confidence must be between zero and one");
    }
    let allowed_ids: BTreeSet<Uuid> = facts.notable_event_ids.iter().copied().collect();
    if candidate
        .notable_event_ids
        .iter()
        .any(|id| !allowed_ids.contains(id))
    {
        bail!("Gemma referenced an event ID outside the fact document");
    }

    let number = Regex::new(r"\b\d+(?:\.\d+)?\b").expect("valid numeric regex");
    let authoritative = format!("{} {}", facts.headline, facts.summary);
    let allowed_numbers: BTreeSet<&str> = number
        .find_iter(&authoritative)
        .map(|value| value.as_str())
        .collect();
    let narrative = format!("{} {}", candidate.headline, candidate.summary);
    if let Some(unsupported) = number
        .find_iter(&narrative)
        .map(|value| value.as_str())
        .find(|value| !allowed_numbers.contains(value))
    {
        bail!("Gemma introduced unsupported numeric value {unsupported}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn report() -> Report {
        Report {
            headline: "2 events observed".into(),
            summary: "Processed 10 observations across 1 track over 3.0 seconds.".into(),
            notable_event_ids: vec![Uuid::nil()],
            observations: vec![],
            data_quality_notes: vec![],
            confidence: 1.0,
        }
    }

    #[test]
    fn rejects_new_numeric_claim() {
        let facts = report();
        let mut candidate = facts.clone();
        candidate.summary = "Processed 11 observations.".into();
        assert!(validate_report(&facts, &candidate).is_err());
    }

    #[test]
    fn accepts_fenced_json() {
        assert_eq!(strip_json_fence("```json\n{}\n```"), "{}");
    }
}
