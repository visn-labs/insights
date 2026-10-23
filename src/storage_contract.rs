//! Local release/storage compatibility, distinct from model-quality approval.
use anyhow::{ensure, Context};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, path::Path};

pub const READER: &str = "visn-storage-reader-v1";
pub const FEATURES: &[&str] = &[
    "committed-media-hash-admission",
    "saved-privacy-policy",
    "durable-revocation",
    "revocation-source-contract",
    "worker-recording-scope-revocation",
    "retrieval-generation-binding",
    "legacy-projection-quarantine",
    "restore-root-fencing",
    "external-revocation-witness",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Contract {
    #[serde(default)]
    pub witness_required: bool,
    pub schema: String,
    pub version: u32,
    pub required_features: BTreeSet<String>,
}

pub fn current() -> Contract {
    Contract {
        witness_required: false,
        schema: "visn-storage-contract".into(),
        version: 1,
        required_features: FEATURES.iter().map(|value| (*value).into()).collect(),
    }
}

pub fn capabilities() -> serde_json::Value {
    serde_json::json!({"reader":READER,"storage_versions":[1],"manifest_versions":[1],
        "features":FEATURES,"code_sha256":env!("VISN_EXECUTION_CODE_SHA256")})
}

pub fn validate(contract: &Contract) -> anyhow::Result<()> {
    ensure!(
        contract.schema == "visn-storage-contract" && contract.version == 1,
        "storage_contract_version_unsupported"
    );
    ensure!(
        contract
            .required_features
            .iter()
            .all(|feature| FEATURES.contains(&feature.as_str())),
        "storage_contract_features_unsupported"
    );
    Ok(())
}

/// Called under DataOwner before recovery, directories or a listener are opened.
pub fn check(directory: &Path) -> anyhow::Result<Option<Contract>> {
    check_inner(directory, false)
}

/// Read-only recovery inspection never authorizes a listener on a fenced root.
pub fn check_recovery(directory: &Path) -> anyhow::Result<Option<Contract>> {
    check_inner(directory, true)
}

fn check_inner(directory: &Path, recovery: bool) -> anyhow::Result<Option<Contract>> {
    if !recovery {
        match std::fs::symlink_metadata(directory.join("recovery-cutover.json")) {
            Ok(_) => anyhow::bail!(
                "storage_root_superseded: resume the recorded cutover; never remove its fence"
            ),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        let path = directory.join("recovery-activation.json");
        match std::fs::symlink_metadata(&path) {
            Ok(metadata) => {
                ensure!(
                    metadata.is_file()
                        && !metadata.file_type().is_symlink()
                        && metadata.len() <= 65536,
                    "storage_activation_invalid"
                );
                let value: serde_json::Value = serde_json::from_slice(&std::fs::read(path)?)?;
                ensure!(
                    value["schema"] == "visn-recovery-activation-v1" && value["state"] == "active",
                    "storage_activation_pending"
                );
                value["transaction"]
                    .as_str()
                    .context("storage_activation_transaction_missing")?
                    .parse::<uuid::Uuid>()?;
                ensure!(
                    value["destination"].as_str().map(std::path::Path::new)
                        == Some(std::fs::canonicalize(directory)?.as_path()),
                    "storage_activation_root_mismatch"
                );
                ensure!(
                    value["source_commit_sha256"]
                        .as_str()
                        .is_some_and(|hash| hash.len() == 64
                            && hash.bytes().all(|byte| byte.is_ascii_hexdigit())),
                    "storage_activation_commit_missing"
                );
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    // Validate the entire set before a compatible earlier job can be recovered
    // or a missing contract marker is published.
    manifests(directory)?;
    crate::revocation_witness::check(directory)?;
    let path = directory.join("storage-contract.json");
    match std::fs::symlink_metadata(&path) {
        Ok(metadata) => {
            ensure!(
                metadata.is_file() && !metadata.file_type().is_symlink() && metadata.len() <= 65536,
                "storage_contract_path_invalid"
            );
            let contract: Contract = serde_json::from_slice(&std::fs::read(&path)?)
                .context("storage_contract_invalid")?;
            validate(&contract)?;
            return Ok(Some(contract));
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(None);
        }
        Err(error) => return Err(error.into()),
    }
}

pub fn admit(directory: &Path) -> anyhow::Result<()> {
    let saved = check(directory)?;
    let mut next = current();
    next.witness_required = saved
        .as_ref()
        .is_some_and(|contract| contract.witness_required);
    if saved.is_none_or(|contract| contract.required_features != next.required_features) {
        crate::durable::atomic_write(
            &directory.join("storage-contract.json"),
            &serde_json::to_vec_pretty(&next)?,
        )?;
    }
    Ok(())
}

pub fn validate_manifest(value: &serde_json::Value) -> anyhow::Result<()> {
    ensure!(
        value
            .get("storage_version")
            .is_none_or(|version| version.as_u64() == Some(1)),
        "memory_manifest_version_unsupported"
    );
    Ok(())
}

pub fn manifests(
    directory: &Path,
) -> anyhow::Result<Vec<(std::path::PathBuf, crate::domain::MemoryJobRecord)>> {
    let entries = match std::fs::read_dir(directory.join("memory/manifests")) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.into()),
    };
    let mut records = Vec::new();
    for entry in entries {
        let path = entry?.path();
        if path.extension().and_then(|value| value.to_str()) != Some("json") {
            continue;
        }
        let metadata = std::fs::symlink_metadata(&path)?;
        ensure!(
            metadata.is_file() && !metadata.file_type().is_symlink(),
            "memory_manifest_path_invalid"
        );
        let value: serde_json::Value = serde_json::from_slice(&std::fs::read(&path)?)
            .context("memory_manifest_invalid_json")?;
        validate_manifest(&value)?;
        let job: crate::domain::MemoryJobRecord =
            serde_json::from_value(value).context("memory_manifest_invalid")?;
        ensure!(
            path.file_stem().and_then(|value| value.to_str()) == Some(job.id.to_string().as_str()),
            "memory_manifest_identity_mismatch"
        );
        records.push((path, job));
    }
    Ok(records)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fences_and_pending_activation_never_authorize_startup() {
        let root = std::env::temp_dir().join(format!("visn-fence-{}", uuid::Uuid::now_v7()));
        std::fs::create_dir_all(&root).unwrap();
        admit(&root).unwrap();
        let fence = root.join("recovery-cutover.json");
        std::fs::write(&fence, b"corrupt or pending fence").unwrap();
        assert!(check(&root).is_err());
        assert!(check_recovery(&root).is_ok());
        std::fs::remove_file(fence).unwrap();
        let proof = root.join("recovery-activation.json");
        std::fs::write(
            &proof,
            br#"{"schema":"visn-recovery-activation-v1","state":"pending"}"#,
        )
        .unwrap();
        assert!(check(&root).is_err());
        assert!(check_recovery(&root).is_ok());
        let mut value = serde_json::json!({"schema":"visn-recovery-activation-v1","state":"active", "transaction":uuid::Uuid::now_v7(),
            "destination":std::fs::canonicalize(&root).unwrap(),"source_commit_sha256":"a".repeat(64)});
        std::fs::write(&proof, serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(check(&root).is_ok());
        value["destination"] = serde_json::json!(root.join("other"));
        std::fs::write(proof, serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(check(&root).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn future_state_is_rejected_without_rewriting_it() {
        let root = std::env::temp_dir().join(format!("visn-contract-{}", uuid::Uuid::now_v7()));
        std::fs::create_dir_all(&root).unwrap();
        admit(&root).unwrap();
        let path = root.join("storage-contract.json");
        let original = std::fs::read(&path).unwrap();
        admit(&root).unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), original);
        for body in [br#"{"schema":"visn-storage-contract","version":2,"required_features":[]}"#.as_slice(),
            br#"{"schema":"visn-storage-contract","version":1,"required_features":["future-feature"]}"#,
            b"{broken"] {
            std::fs::write(&path,body).unwrap();assert!(admit(&root).is_err());
            assert_eq!(std::fs::read(&path).unwrap(),body);
        }
        assert!(validate_manifest(&serde_json::json!({"storage_version":2})).is_err());
        assert!(validate_manifest(&serde_json::json!({"storage_version":"1"})).is_err());
        assert!(validate_manifest(&serde_json::json!({})).is_ok());
        std::fs::remove_dir_all(root).unwrap();
    }
}
