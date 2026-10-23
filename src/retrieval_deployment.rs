//! Durable local operator registry. Each request holds one immutable selection.
use crate::config::Config;
use anyhow::{ensure, Context};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use uuid::Uuid;

pub const SCHEMA: &str = "visn-retrieval-deployments-v1";

fn ready() -> String {
    "ready".into()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Target {
    #[serde(default = "ready")]
    pub state: String,
    pub url: String,
    pub worker_profile: serde_json::Value,
    pub prepared: Vec<serde_json::Value>,
    pub verification_requests: Vec<serde_json::Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Registry {
    pub schema: String,
    pub revision: Uuid,
    pub active: Option<Uuid>,
    pub targets: BTreeMap<Uuid, Target>,
}

impl Registry {
    pub fn validate(&self) -> anyhow::Result<()> {
        ensure!(
            self.schema == SCHEMA
                && !self.targets.is_empty()
                && self.targets.len() <= 256
                && self
                    .active
                    .is_none_or(|active| self.targets.contains_key(&active)),
            "retrieval_registry_invalid"
        );
        ensure!(
            self.active
                .is_none_or(|id| self.targets[&id].state == "ready"),
            "retrieval_registry_active_not_ready"
        );
        for target in self.targets.values() {
            ensure!(
                matches!(target.state.as_str(), "staging" | "ready" | "failed"),
                "retrieval_registry_state_invalid"
            );
            let url = reqwest::Url::parse(&target.url)?;
            ensure!(
                matches!(url.scheme(), "http" | "https")
                    && url.username().is_empty()
                    && url.password().is_none()
                    && url.query().is_none()
                    && url.fragment().is_none(),
                "retrieval_registry_url_invalid"
            );
            let fingerprint = target.worker_profile["fingerprint"]
                .as_str()
                .context("retrieval_registry_profile_missing")?;
            let storage = target.worker_profile["manifest"]["index_storage"]
                .as_str()
                .context("retrieval_registry_storage_missing")?;
            ensure!(
                storage.len() == 64 && storage.bytes().all(|byte| byte.is_ascii_hexdigit()),
                "retrieval_registry_storage_invalid"
            );
            ensure!(
                self.targets
                    .values()
                    .filter(|other| other.url == target.url)
                    .all(|other| other.worker_profile["manifest"]["index_storage"] == storage),
                "retrieval_registry_url_storage_reused"
            );
            ensure!(
                self.targets
                    .values()
                    .filter(|other| other.url == target.url)
                    .all(|other| other.worker_profile["fingerprint"] == fingerprint),
                "retrieval_registry_url_profile_reused"
            );
            ensure!(
                fingerprint.len() == 64 && fingerprint.bytes().all(|byte| byte.is_ascii_hexdigit()),
                "retrieval_registry_profile_invalid"
            );
            ensure!(
                !target.verification_requests.is_empty()
                    && target.verification_requests.len() <= 128,
                "retrieval_registry_scope_missing"
            );
            if target.state == "ready" {
                ensure!(
                    !target.prepared.is_empty()
                        && target.prepared.len() == target.verification_requests.len(),
                    "retrieval_registry_readiness_missing"
                );
            }
            for receipt in &target.prepared {
                ensure!(
                    receipt["status"] == "completed"
                        && receipt["profile_current"] == true
                        && receipt["total"].as_u64().is_some_and(|count| count > 0)
                        && receipt["total"] == receipt["completed"]
                        && receipt["failed"] == 0
                        && receipt["worker_profile"]["fingerprint"] == fingerprint,
                    "retrieval_registry_readiness_invalid"
                );
            }
        }
        Ok(())
    }
}

pub fn path(config: &Config) -> std::path::PathBuf {
    config.data_dir.join("retrieval/deployments.json")
}

pub async fn read(config: &Config) -> anyhow::Result<Option<Registry>> {
    let path = path(config);
    let metadata = match tokio::fs::symlink_metadata(&path).await {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    ensure!(
        metadata.is_file()
            && !metadata.file_type().is_symlink()
            && metadata.len() <= 4 * 1024 * 1024,
        "retrieval_registry_capacity_or_symlink"
    );
    let bytes = tokio::fs::read(path).await?;
    ensure!(
        bytes.len() <= 4 * 1024 * 1024,
        "retrieval_registry_capacity"
    );
    let registry: Registry =
        serde_json::from_slice(&bytes).context("retrieval_registry_invalid_json")?;
    registry.validate()?;
    Ok(Some(registry))
}

pub async fn select(config: &Config, generation: Option<Uuid>) -> anyhow::Result<Config> {
    let registry = read(config).await?;
    let selected = generation.or_else(|| registry.as_ref().and_then(|registry| registry.active));
    let mut result = config.clone();
    if let Some(generation) = selected {
        let target = registry
            .as_ref()
            .and_then(|registry| registry.targets.get(&generation))
            .context("retrieval_deployment_missing")?;
        result.retrieval_url = Some(target.url.clone());
        result.retrieval_expected_profile = Some(
            target.worker_profile["fingerprint"]
                .as_str()
                .unwrap()
                .into(),
        );
        result.retrieval_deployment_generation = Some(generation);
    }
    Ok(result)
}

pub async fn cleanup_targets(
    config: &Config,
) -> anyhow::Result<(Option<Uuid>, BTreeMap<String, (String, Option<String>)>)> {
    use sha2::{Digest, Sha256};
    let registry = read(config).await?;
    let mut targets = BTreeMap::new();
    for url in config.retrieval_url.iter().chain(
        registry
            .iter()
            .flat_map(|registry| registry.targets.values().map(|target| &target.url)),
    ) {
        let storage = registry
            .as_ref()
            .and_then(|registry| registry.targets.values().find(|target| &target.url == url))
            .and_then(|target| target.worker_profile["manifest"]["index_storage"].as_str())
            .map(str::to_owned);
        targets.insert(
            format!("{:x}", Sha256::digest(url.as_bytes())),
            (url.clone(), storage),
        );
    }
    Ok((registry.map(|registry| registry.revision), targets))
}

pub async fn incompatible(config: &Config, generation: Option<Uuid>) -> anyhow::Result<bool> {
    let Some(generation) = generation else {
        return Ok(false);
    };
    let Some(registry) = read(config).await? else {
        return Ok(true);
    };
    if registry.active == Some(generation) {
        return Ok(false);
    };
    let old = registry
        .targets
        .get(&generation)
        .context("retrieval_deployment_missing")?;
    let Some(active) = registry.active.and_then(|id| registry.targets.get(&id)) else {
        return Ok(true);
    };
    Ok(old.worker_profile["manifest"]["embedding"]
        != active.worker_profile["manifest"]["embedding"]
        || old.worker_profile["manifest"]["vector_policy"]
            != active.worker_profile["manifest"]["vector_policy"])
}

#[cfg(test)]
mod tests {
    use super::*;
    fn target(port: u16, model: &str) -> Target {
        serde_json::from_value(serde_json::json!({"url":format!("http://127.0.0.1:{port}"),
            "worker_profile":{"fingerprint":"a".repeat(64),"manifest":{"index_storage":"b".repeat(64),"embedding":{"model":model},"vector_policy":"legacy-renormalize-v1"}},
            "prepared":[{"status":"completed","profile_current":true,"total":1,"completed":1,"failed":0,"worker_profile":{"fingerprint":"a".repeat(64)}}],
            "verification_requests":[{"scope_id":"fixture","events":[]}]})).unwrap()
    }
    #[tokio::test]
    async fn selection_is_immutable_and_bad_registry_never_falls_back() {
        let mut config = Config::from_env().unwrap();
        config.data_dir = std::env::temp_dir().join(format!("visn-registry-{}", Uuid::now_v7()));
        config.retrieval_url = Some("http://127.0.0.1:9999".into());
        assert_eq!(
            select(&config, None).await.unwrap().retrieval_url,
            config.retrieval_url
        );
        let a = Uuid::now_v7();
        let b = Uuid::now_v7();
        let mut registry = Registry {
            schema: SCHEMA.into(),
            revision: Uuid::now_v7(),
            active: Some(a),
            targets: BTreeMap::from([(a, target(8801, "a")), (b, target(8802, "b"))]),
        };
        crate::durable::publish(path(&config), serde_json::to_vec(&registry).unwrap())
            .await
            .unwrap();
        let bound = select(&config, None).await.unwrap();
        registry.active = Some(b);
        registry.revision = Uuid::now_v7();
        crate::durable::publish(path(&config), serde_json::to_vec(&registry).unwrap())
            .await
            .unwrap();
        assert_eq!(bound.retrieval_deployment_generation, Some(a));
        assert_eq!(
            select(&config, None)
                .await
                .unwrap()
                .retrieval_deployment_generation,
            Some(b)
        );
        assert_eq!(
            select(&config, Some(a)).await.unwrap().retrieval_url,
            bound.retrieval_url
        );
        assert!(incompatible(&config, Some(a)).await.unwrap());
        assert_eq!(cleanup_targets(&config).await.unwrap().1.len(), 3);
        registry.targets.get_mut(&b).unwrap().prepared[0]["completed"] = serde_json::json!(0);
        assert!(registry.validate().is_err());
        crate::durable::publish(path(&config), b"{}".to_vec())
            .await
            .unwrap();
        assert!(select(&config, None).await.is_err());
        tokio::fs::remove_dir_all(config.data_dir).await.unwrap();
    }
}
