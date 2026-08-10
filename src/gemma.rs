use std::{
    collections::BTreeSet,
    sync::Arc,
    time::{Duration, Instant},
};

use anyhow::{anyhow, bail, Context};
use regex::Regex;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use tokio::sync::{Mutex, Semaphore, SemaphorePermit};
use tracing::warn;
use uuid::Uuid;

use crate::{
    config::Config,
    detector_worker::DetectorWorker,
    domain::{
        CameraProfile, MemoryEventDescription, MemoryQueryMatch, Report, RepresentativeFrame,
    },
};

#[derive(Clone)]
pub struct GemmaClient {
    config: Arc<Config>,
    client: Client,
    inference_gate: Arc<Semaphore>,
    media_worker_gate: Arc<Semaphore>,
    detector_worker: DetectorWorker,
    loaded_model: Arc<Mutex<Option<LoadedModel>>>,
}

#[derive(Clone, Debug)]
struct LoadedModel {
    key: String,
    instance_id: String,
    verified_at: Instant,
}

const LOADED_MODEL_CACHE_TTL: Duration = Duration::from_secs(15);

#[derive(Debug, Deserialize, Serialize)]
pub struct ModelInfo {
    pub id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ModelsResponse {
    data: Vec<ModelInfo>,
}

#[derive(Debug, Deserialize)]
struct NativeModelsResponse {
    #[serde(default)]
    models: Vec<NativeModelInfo>,
}

#[derive(Debug, Deserialize)]
struct NativeModelInfo {
    key: String,
    #[serde(default)]
    display_name: Option<String>,
    #[serde(default, rename = "type")]
    model_type: Option<String>,
    #[serde(default)]
    state: Option<String>,
    #[serde(default)]
    loaded_instances: Vec<NativeLoadedInstance>,
}

#[derive(Debug, Deserialize)]
struct NativeLoadedInstance {
    id: String,
}

#[derive(Debug, Deserialize)]
struct LoadModelResponse {
    #[serde(default)]
    instance_id: Option<String>,
    #[serde(default)]
    status: String,
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

#[derive(Debug, Deserialize)]
pub struct ViewDescriptionCandidate {
    pub description: String,
    pub scene_type: String,
    #[serde(default)]
    pub visible_areas: Vec<String>,
    #[serde(default)]
    pub notable_static_elements: Vec<String>,
    pub visibility_conditions: String,
    pub confidence: f32,
}

#[derive(Debug, Deserialize)]
pub struct MemoryQuerySynthesisCandidate {
    pub summary: String,
    #[serde(default)]
    pub relevant_event_ids: Vec<Uuid>,
    pub confidence: f32,
}

impl GemmaClient {
    pub fn new(
        config: Arc<Config>,
        media_worker_gate: Arc<Semaphore>,
        detector_worker: DetectorWorker,
    ) -> anyhow::Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.gemma_timeout_secs))
            .build()
            .context("build Gemma HTTP client")?;
        Ok(Self {
            config,
            client,
            inference_gate: Arc::new(Semaphore::new(1)),
            media_worker_gate,
            detector_worker,
            loaded_model: Arc::new(Mutex::new(None)),
        })
    }

    pub async fn models(&self) -> anyhow::Result<Vec<ModelInfo>> {
        if let Ok(models) = self.native_models().await {
            if !models.is_empty() {
                return Ok(models);
            }
        }

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

    async fn native_models(&self) -> anyhow::Result<Vec<ModelInfo>> {
        let response = self
            .client
            .get(format!("{}/models", self.config.lmstudio_api_base_url))
            .bearer_auth(&self.config.gemma_api_key)
            .send()
            .await
            .context("connect to LM Studio native model API")?
            .error_for_status()
            .context("LM Studio native model-list response")?
            .json::<NativeModelsResponse>()
            .await
            .context("decode LM Studio native model list")?;
        Ok(response
            .models
            .into_iter()
            .map(|model| ModelInfo {
                id: model.key,
                display_name: model.display_name,
                model_type: model.model_type,
                state: model.state,
                source: Some("lmstudio_native".to_owned()),
            })
            .collect())
    }

    async fn selected_model_inner(&self, requested_model: Option<&str>) -> anyhow::Result<String> {
        tracing::info!(
            requested_model = ?requested_model,
            "selected_model called"
        );

        if let Some(model) = normalize_requested_model(requested_model) {
            tracing::info!(
                model,
                "selected_model: user-requested model accepted by normalize_requested_model"
            );
            return self.ensure_model_loaded(model).await;
        }

        tracing::info!(
            requested_model = ?requested_model,
            known_vlms = ?known_vlm_models(),
            "selected_model: normalize_requested_model returned None; \
             model not in known_vlm_models or was empty"
        );

        if let Some(model) = &self.config.gemma_model {
            tracing::info!(
                model = model.as_str(),
                "selected_model: falling back to VISN_GEMMA_MODEL config"
            );
            return self.ensure_model_loaded(model).await;
        }

        if let Ok(models) = self.native_models().await {
            if let Some(model) = models
                .iter()
                .find(|model| known_vlm_models().contains(&model.id.as_str()))
                .or_else(|| {
                    models.iter().find(|model| {
                        model
                            .model_type
                            .as_deref()
                            .is_some_and(|value| value.eq_ignore_ascii_case("vlm"))
                    })
                })
            {
                tracing::info!(
                    model = model.id.as_str(),
                    "selected_model: auto-detected model from native model list"
                );
                return self.ensure_model_loaded(&model.id).await;
            }
        }

        let models = self.models().await?;
        let fallback = models
            .iter()
            .find(|model| {
                let id = model.id.to_ascii_lowercase();
                id.contains("gemma-4") && id.contains("26b") && id.contains("a4b")
            })
            .or_else(|| models.first())
            .map(|model| model.id.clone())
            .ok_or_else(|| anyhow!("LM Studio has no loaded model"))?;
        tracing::info!(
            model = fallback.as_str(),
            "selected_model: using last-resort fallback from OpenAI model list"
        );
        Ok(fallback)
    }

    pub async fn generate_report(
        &self,
        deterministic: &Report,
        requested_model: Option<&str>,
    ) -> anyhow::Result<(Report, String)> {
        let _permit = self
            .inference_gate
            .acquire()
            .await
            .context("acquire global VLM inference gate")?;
        let _media_permit = self.acquire_exclusive_media().await?;
        let model = self.selected_model_inner(requested_model).await?;
        tracing::info!(model = model.as_str(), "generate_report: using model");
        let facts = serde_json::to_string(deterministic)?;
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
            serde_json::to_string(&schema)?
        );
        let body = json!({
            "model": model,
            "max_tokens": self.config.vlm_max_output_tokens,
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

    pub async fn describe_view(
        &self,
        frame: &RepresentativeFrame,
        detected_classes: &[String],
        requested_model: Option<&str>,
    ) -> anyhow::Result<(ViewDescriptionCandidate, String)> {
        validate_representative_frame(frame)?;
        let _permit = self
            .inference_gate
            .acquire()
            .await
            .context("acquire global VLM inference gate")?;
        let _media_permit = self.acquire_exclusive_media().await?;
        let model = self.selected_model_inner(requested_model).await?;
        tracing::info!(model = model.as_str(), "describe_view: using model");
        let schema = json!({
            "description": "two to four sentences describing the physical scene, layout and camera viewpoint",
            "scene_type": "short category such as indoor lobby, roadway, entrance or unknown",
            "visible_areas": ["short visible region or functional area"],
            "notable_static_elements": ["short static structural element"],
            "visibility_conditions": "lighting, weather, occlusion or image-quality conditions",
            "confidence": "number from 0 to 1"
        });
        let class_context = if detected_classes.is_empty() {
            "No detector class context is available.".to_owned()
        } else {
            format!(
                "The detector observed these class types during the monitoring window: {}. Do not infer counts from this list.",
                detected_classes.join(", ")
            )
        };
        let prompt = format!(
            "This representative frame was sampled {} milliseconds into the monitoring window. Describe this camera's general field of view. Focus on the physical setting, spatial layout, entrances/exits, paths, counters, roads, walls, barriers and other persistent elements that are actually visible. Describe visibility conditions. Do not count people or objects, identify individuals, infer identity, claim an event, infer intent, or invent areas outside the image. If uncertain, say so. {class_context}\n\nReturn exactly one JSON object matching this schema and no markdown:\n{}",
            frame.frame_time_ms,
            serde_json::to_string(&schema)?
        );
        let image_url = format!("data:{};base64,{}", frame.media_type, frame.data_base64);
        let body = json!({
            "model": model,
            "max_tokens": self.config.vlm_max_output_tokens,
            "temperature": 0.1,
            "stream": false,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }]
        });
        let response = self
            .client
            .post(format!("{}/chat/completions", self.config.gemma_base_url))
            .bearer_auth(&self.config.gemma_api_key)
            .json(&body)
            .send()
            .await
            .context("call LM Studio vision chat completion")?
            .error_for_status()
            .context("LM Studio vision response")?
            .json::<ChatResponse>()
            .await
            .context("decode LM Studio vision response")?;
        let content = response
            .choices
            .first()
            .ok_or_else(|| anyhow!("LM Studio returned no vision choices"))?
            .message
            .content
            .trim();
        let content = strip_json_fence(content);
        let candidate: ViewDescriptionCandidate =
            serde_json::from_str(content).with_context(|| {
                format!("Gemma did not return valid view-description JSON: {content}")
            })?;
        validate_view_description(&candidate)?;
        Ok((candidate, model))
    }

    pub async fn describe_memory_event(
        &self,
        frame: &RepresentativeFrame,
        camera: &CameraProfile,
        activity_mean: f32,
        activity_peak: f32,
        requested_model: Option<&str>,
    ) -> anyhow::Result<(MemoryEventDescription, String)> {
        validate_representative_frame(frame)?;
        let _permit = self
            .inference_gate
            .acquire()
            .await
            .context("acquire global VLM inference gate")?;
        let _media_permit = self.acquire_exclusive_media().await?;
        let model = self.selected_model_inner(requested_model).await?;
        let schema = json!({
            "summary": "two or three factual sentences about the visible scene and moment",
            "scene_type": "short physical-scene category",
            "visible_objects": ["visible object or structure; no invented counts"],
            "visible_people": ["visible, non-identifying person attribute when present"],
            "apparent_actions": ["directly visible action; empty when a still frame is insufficient"],
            "visible_text": ["legible text only"],
            "conditions": "lighting, weather, occlusion and image quality",
            "confidence": "number from 0 to 1"
        });
        let context = json!({
            "country": camera.country,
            "region": camera.region,
            "city": camera.city,
            "manufacturer": camera.manufacturer,
            "backend_description": camera.description,
            "frame_time_ms": frame.frame_time_ms,
            "activity_mean": activity_mean,
            "activity_peak": activity_peak
        });
        let prompt = format!(
            "Describe this representative frame as searchable camera evidence. Use only what is visibly supported. The supplied camera metadata is context, not visual proof; mention it only when it helps locate the camera. Do not identify people, infer intent, assert a count you cannot verify, or claim temporal motion/action from one still image. If the metadata description conflicts with the image, trust the image.\n\nCAMERA AND SAMPLING CONTEXT:\n{}\n\nReturn exactly one JSON object matching this schema and no markdown:\n{}",
            serde_json::to_string(&context)?,
            serde_json::to_string(&schema)?
        );
        let image_url = format!("data:{};base64,{}", frame.media_type, frame.data_base64);
        let body = json!({
            "model": model,
            "max_tokens": self.config.vlm_max_output_tokens,
            "temperature": 0.1,
            "stream": false,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }]
        });
        let response = self
            .client
            .post(format!("{}/chat/completions", self.config.gemma_base_url))
            .bearer_auth(&self.config.gemma_api_key)
            .json(&body)
            .send()
            .await
            .context("call LM Studio event-description completion")?
            .error_for_status()
            .context("LM Studio event-description response")?
            .json::<ChatResponse>()
            .await
            .context("decode LM Studio event-description response")?;
        let content = strip_json_fence(
            response
                .choices
                .first()
                .ok_or_else(|| anyhow!("LM Studio returned no event-description choices"))?
                .message
                .content
                .trim(),
        );
        let mut candidate: MemoryEventDescription =
            serde_json::from_str(content).with_context(|| {
                format!("VLM did not return valid event-description JSON: {content}")
            })?;
        validate_memory_event_description(&candidate)?;
        candidate.generated_by_model = true;
        candidate.model = Some(model.clone());
        candidate.fallback_reason = None;
        Ok((candidate, model))
    }

    pub async fn synthesize_memory_query(
        &self,
        query: &str,
        matches: &[MemoryQueryMatch],
        requested_model: Option<&str>,
    ) -> anyhow::Result<(MemoryQuerySynthesisCandidate, String)> {
        let _permit = self
            .inference_gate
            .acquire()
            .await
            .context("acquire global VLM inference gate")?;
        let _media_permit = self.acquire_exclusive_media().await?;
        let model = self.selected_model_inner(requested_model).await?;
        let evidence: Vec<_> = matches
            .iter()
            .take(12)
            .map(|candidate| {
                json!({
                    "event_id": candidate.event.event_id,
                    "camera_id": candidate.event.camera_id,
                    "start_ms": candidate.event.start_ms,
                    "end_ms": candidate.event.end_ms,
                    "retrieval_score": candidate.score,
                    "summary": candidate.event.description.summary,
                    "scene_type": candidate.event.description.scene_type,
                    "objects": candidate.event.description.visible_objects,
                    "people": candidate.event.description.visible_people,
                    "actions": candidate.event.description.apparent_actions,
                    "visible_text": candidate.event.description.visible_text
                })
            })
            .collect();
        let body = json!({
            "model": model,
            "max_tokens": self.config.vlm_max_output_tokens,
            "temperature": 0.1,
            "stream": false,
            "messages": [
                {"role": "system", "content": "Answer a camera-memory query using only the supplied candidate records. Do not invent objects, people, actions, counts, identities, locations, or timestamps. Refer only to supplied event IDs. If evidence is weak, say so. Return one JSON object and no markdown."},
                {"role": "user", "content": format!(
                    "QUERY:\n{}\n\nCANDIDATE RECORDS:\n{}\n\nReturn {{\"summary\":\"concise evidence-grounded answer\",\"relevant_event_ids\":[\"UUIDs from candidates only\"],\"confidence\":0.0}}.",
                    query,
                    serde_json::to_string(&evidence)?
                )}
            ]
        });
        let response = self
            .client
            .post(format!("{}/chat/completions", self.config.gemma_base_url))
            .bearer_auth(&self.config.gemma_api_key)
            .json(&body)
            .send()
            .await
            .context("call LM Studio memory-query synthesis")?
            .error_for_status()
            .context("LM Studio memory-query response")?
            .json::<ChatResponse>()
            .await
            .context("decode LM Studio memory-query response")?;
        let content = strip_json_fence(
            response
                .choices
                .first()
                .ok_or_else(|| anyhow!("LM Studio returned no memory-query choices"))?
                .message
                .content
                .trim(),
        );
        let candidate: MemoryQuerySynthesisCandidate = serde_json::from_str(content)
            .with_context(|| format!("VLM did not return valid memory-query JSON: {content}"))?;
        if candidate.summary.trim().is_empty()
            || candidate.summary.len() > 2_000
            || !(0.0..=1.0).contains(&candidate.confidence)
        {
            bail!("VLM memory-query response failed bounds validation");
        }
        let allowed: BTreeSet<_> = matches.iter().map(|item| item.event.event_id).collect();
        if candidate
            .relevant_event_ids
            .iter()
            .any(|event_id| !allowed.contains(event_id))
        {
            bail!("VLM memory-query response referenced an unknown event");
        }
        Ok((candidate, model))
    }

    async fn acquire_exclusive_media(&self) -> anyhow::Result<Option<SemaphorePermit<'_>>> {
        if !self.config.vlm_exclusive_media {
            return Ok(None);
        }
        let permits = u32::try_from(self.config.max_concurrent_cameras)
            .context("media-worker limit exceeds semaphore permit range")?;
        let permit = self
            .media_worker_gate
            .acquire_many(permits)
            .await
            .context("acquire exclusive media budget for VLM call")?;
        if let Err(error) = self.detector_worker.shutdown_when_idle().await {
            warn!(
                %error,
                "could not explicitly drain the persistent detector before VLM execution"
            );
        }
        Ok(Some(permit))
    }

    /// Ensures that the requested model is actively loaded in LM Studio.
    /// If another model is currently loaded, it is unloaded first to free resources.
    /// Returns the model ID to use in subsequent chat/vision API calls.
    async fn ensure_model_loaded(&self, model: &str) -> anyhow::Result<String> {
        tracing::info!(model, "ensure_model_loaded: starting");

        if let Some(cached) = self.loaded_model.lock().await.clone() {
            if cached.key.eq_ignore_ascii_case(model)
                && cached.verified_at.elapsed() <= LOADED_MODEL_CACHE_TTL
            {
                tracing::debug!(
                    model,
                    instance_id = %cached.instance_id,
                    "using the process-local LM Studio model cache"
                );
                return Ok(cached.instance_id);
            }
        }
        // A model switch is now in progress. Do not retain a stale instance in
        // the cache if load or unload subsequently fails.
        *self.loaded_model.lock().await = None;

        // 1. Check if the model already has a running instance via the native API.
        match self.find_loaded_instance(model).await {
            Ok(Some(instance_id)) => {
                tracing::info!(
                    model,
                    instance_id = %instance_id,
                    "ensure_model_loaded: model already has a loaded instance — skipping load"
                );
                self.remember_loaded_model(model, &instance_id).await;
                return Ok(instance_id);
            }
            Ok(None) => {
                tracing::info!(
                    model,
                    "ensure_model_loaded: model has no loaded instance — will load"
                );
            }
            Err(err) => {
                tracing::info!(
                    model,
                    error = %err,
                    "ensure_model_loaded: could not check native model list — will attempt load anyway"
                );
            }
        }

        // 2. Unload any currently loaded models to free resources.
        match self.fetch_native_models().await {
            Ok(native_models) => {
                for other in &native_models {
                    if !other.loaded_instances.is_empty() && !other.key.eq_ignore_ascii_case(model)
                    {
                        for instance in &other.loaded_instances {
                            tracing::info!(
                                unloading_model = %other.key,
                                unloading_instance = %instance.id,
                                to_load = model,
                                "ensure_model_loaded: unloading model instance to free resources"
                            );
                            match self.unload_model(&instance.id).await {
                                Ok(()) => tracing::info!(
                                    instance_id = %instance.id,
                                    "ensure_model_loaded: unload succeeded"
                                ),
                                Err(err) => warn!(
                                    instance_id = %instance.id,
                                    error = %err,
                                    "ensure_model_loaded: unload failed (will attempt load anyway)"
                                ),
                            }
                        }
                    }
                }
            }
            Err(err) => {
                warn!(
                    model,
                    error = %err,
                    "ensure_model_loaded: could not fetch native model list for unload phase"
                );
            }
        }

        // 3. Load the requested model.
        tracing::info!(model, "ensure_model_loaded: sending load request");
        let body = json!({
            "model": model,
            "context_length": self.config.vlm_context_length,
            "eval_batch_size": self.config.vlm_eval_batch_size,
            "flash_attention": self.config.vlm_flash_attention,
            "offload_kv_cache_to_gpu": self.config.vlm_offload_kv_cache_to_gpu,
            "echo_load_config": true
        });
        let mut response = self
            .client
            .post(format!("{}/models/load", self.config.lmstudio_api_base_url))
            .bearer_auth(&self.config.gemma_api_key)
            .json(&body)
            .send()
            .await
            .context("connect to LM Studio model-load API")?;

        let mut status = response.status();
        tracing::info!(model, http_status = %status, "ensure_model_loaded: load response received");

        if !status.is_success() {
            let configured_error = response.text().await.unwrap_or_default();
            warn!(
                model,
                http_status = %status,
                body = %configured_error,
                "LM Studio rejected optimized load settings; retrying a compatibility load"
            );
            response = self
                .client
                .post(format!("{}/models/load", self.config.lmstudio_api_base_url))
                .bearer_auth(&self.config.gemma_api_key)
                .json(&json!({ "model": model }))
                .send()
                .await
                .context("connect to LM Studio compatibility model-load API")?;
            status = response.status();
            if !status.is_success() {
                let fallback_error = response.text().await.unwrap_or_default();
                bail!(
                    "LM Studio model-load failed for {model}; configured load: {configured_error}; compatibility load HTTP {status}: {fallback_error}"
                );
            }
        }

        // Try to extract the instance_id from the load response.
        let response_text = response.text().await.unwrap_or_default();
        tracing::info!(
            model,
            response = %response_text,
            "ensure_model_loaded: load response body"
        );

        if let Ok(load_info) = serde_json::from_str::<LoadModelResponse>(&response_text) {
            if load_info.status == "loaded" {
                let instance_id = load_info.instance_id.unwrap_or_else(|| model.to_owned());
                tracing::info!(
                    model,
                    instance_id = %instance_id,
                    "ensure_model_loaded: model loaded successfully"
                );
                self.remember_loaded_model(model, &instance_id).await;
                return Ok(instance_id);
            }
        }

        // 4. Fallback: re-check native API for the loaded instance ID.
        if let Ok(Some(instance_id)) = self.find_loaded_instance(model).await {
            tracing::info!(
                model,
                instance_id = %instance_id,
                "ensure_model_loaded: model confirmed loaded via native API re-check"
            );
            self.remember_loaded_model(model, &instance_id).await;
            return Ok(instance_id);
        }

        // 5. Last resort: return the user-provided model string.
        warn!(
            model,
            "ensure_model_loaded: load response parsed but instance not confirmed; \
             proceeding with provided model ID"
        );
        self.remember_loaded_model(model, model).await;
        Ok(model.to_owned())
    }

    async fn remember_loaded_model(&self, key: &str, instance_id: &str) {
        *self.loaded_model.lock().await = Some(LoadedModel {
            key: key.to_owned(),
            instance_id: instance_id.to_owned(),
            verified_at: Instant::now(),
        });
    }

    /// Fetches the full native model list from LM Studio.
    async fn fetch_native_models(&self) -> anyhow::Result<Vec<NativeModelInfo>> {
        Ok(self
            .client
            .get(format!("{}/models", self.config.lmstudio_api_base_url))
            .bearer_auth(&self.config.gemma_api_key)
            .send()
            .await
            .context("connect to LM Studio native model API")?
            .error_for_status()
            .context("LM Studio native model-list response")?
            .json::<NativeModelsResponse>()
            .await
            .context("decode LM Studio native model list")?
            .models)
    }

    /// Checks the native model list for a model matching `model` that has at least one
    /// loaded instance. Returns the `instance_id` of the first loaded instance.
    async fn find_loaded_instance(&self, model: &str) -> anyhow::Result<Option<String>> {
        let models = self.fetch_native_models().await?;
        let found = models.iter().find(|m| m.key.eq_ignore_ascii_case(model));
        if let Some(m) = found {
            tracing::info!(
                model,
                key = %m.key,
                loaded_count = m.loaded_instances.len(),
                "find_loaded_instance: found model in native list"
            );
        } else {
            tracing::info!(
                model,
                available_keys = ?models.iter().map(|m| m.key.as_str()).collect::<Vec<_>>(),
                "find_loaded_instance: model not found in native list"
            );
        }
        Ok(models
            .into_iter()
            .find(|m| m.key.eq_ignore_ascii_case(model))
            .and_then(|m| m.loaded_instances.into_iter().next())
            .map(|inst| inst.id))
    }

    /// Unloads a model instance from LM Studio.
    async fn unload_model(&self, instance_id: &str) -> anyhow::Result<()> {
        // Try with "identifier" field first (newer LM Studio API).
        let body = json!({ "identifier": instance_id });
        let result = self
            .client
            .post(format!(
                "{}/models/unload",
                self.config.lmstudio_api_base_url
            ))
            .bearer_auth(&self.config.gemma_api_key)
            .json(&body)
            .send()
            .await;

        match result {
            Ok(resp) if resp.status().is_success() => {
                tracing::info!(
                    instance_id,
                    "unload_model: unloaded with 'identifier' field"
                );
                return Ok(());
            }
            Ok(resp) => {
                let status = resp.status();
                let body_text = resp.text().await.unwrap_or_default();
                tracing::info!(
                    instance_id,
                    http_status = %status,
                    body = %body_text,
                    "unload_model: 'identifier' field failed; trying 'model' field"
                );
            }
            Err(err) => {
                tracing::info!(
                    instance_id,
                    error = %err,
                    "unload_model: 'identifier' field request failed; trying 'model' field"
                );
            }
        }

        // Retry with "model" field (older LM Studio API convention).
        let body = json!({ "model": instance_id });
        self.client
            .post(format!(
                "{}/models/unload",
                self.config.lmstudio_api_base_url
            ))
            .bearer_auth(&self.config.gemma_api_key)
            .json(&body)
            .send()
            .await
            .context("connect to LM Studio model-unload API")?
            .error_for_status()
            .context("LM Studio model-unload response")?;
        tracing::info!(instance_id, "unload_model: unloaded with 'model' field");
        Ok(())
    }
}

pub fn known_vlm_models() -> &'static [&'static str] {
    &[
        "prism-ml/bonsai-27b",
        "moondream2",
        "qwen/qwen3.6-35b-a3b",
        "google/gemma-4-26b-a4b-qat",
        "zai-org/glm-4.6v-flash",
    ]
}

fn normalize_requested_model(requested_model: Option<&str>) -> Option<&str> {
    requested_model
        .map(str::trim)
        .filter(|model| !model.is_empty() && known_vlm_models().contains(model))
}

fn validate_representative_frame(frame: &RepresentativeFrame) -> anyhow::Result<()> {
    if !matches!(frame.media_type.as_str(), "image/jpeg" | "image/png") {
        bail!("unsupported representative-frame media type");
    }
    if frame.data_base64.is_empty() || frame.data_base64.len() > 4_000_000 {
        bail!("representative frame is empty or exceeds the four-megabyte limit");
    }
    if frame.width == 0 || frame.height == 0 {
        bail!("representative frame has invalid dimensions");
    }
    Ok(())
}

fn validate_memory_event_description(candidate: &MemoryEventDescription) -> anyhow::Result<()> {
    if candidate.summary.trim().is_empty()
        || candidate.summary.len() > 1_500
        || candidate.scene_type.trim().is_empty()
        || candidate.scene_type.len() > 120
        || candidate.conditions.len() > 500
        || !(0.0..=1.0).contains(&candidate.confidence)
    {
        bail!("VLM event description failed scalar bounds validation");
    }
    validate_short_list("visible_objects", &candidate.visible_objects)?;
    validate_short_list("visible_people", &candidate.visible_people)?;
    validate_short_list("apparent_actions", &candidate.apparent_actions)?;
    validate_short_list("visible_text", &candidate.visible_text)?;
    Ok(())
}

fn validate_view_description(candidate: &ViewDescriptionCandidate) -> anyhow::Result<()> {
    if candidate.description.trim().is_empty() || candidate.description.len() > 2_000 {
        bail!("view description must contain 1 to 2000 characters");
    }
    if candidate.scene_type.trim().is_empty() || candidate.scene_type.len() > 160 {
        bail!("view scene_type must contain 1 to 160 characters");
    }
    if candidate.visibility_conditions.trim().is_empty()
        || candidate.visibility_conditions.len() > 500
    {
        bail!("view visibility_conditions must contain 1 to 500 characters");
    }
    if !(0.0..=1.0).contains(&candidate.confidence) {
        bail!("view confidence must be between zero and one");
    }
    validate_short_list("visible_areas", &candidate.visible_areas)?;
    validate_short_list(
        "notable_static_elements",
        &candidate.notable_static_elements,
    )?;
    Ok(())
}

fn validate_short_list(name: &str, values: &[String]) -> anyhow::Result<()> {
    if values.len() > 12
        || values
            .iter()
            .any(|value| value.trim().is_empty() || value.len() > 200)
    {
        bail!("{name} must contain at most 12 non-empty values of at most 200 characters");
    }
    Ok(())
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
