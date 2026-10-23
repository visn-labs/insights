//! Independent local tombstone storage; not remote/WORM hardware attestation.
use crate::{domain::MemoryJobRecord, retrieval_deployment::Registry};
use anyhow::{ensure, Context};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};
use uuid::Uuid;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Instance {
    schema: String,
    id: Uuid,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Pin {
    schema: String,
    directory: PathBuf,
    instance: Uuid,
    acknowledged: BTreeMap<Uuid, String>,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Record {
    schema: String,
    instance: Uuid,
    job: MemoryJobRecord,
    registry: Option<Registry>,
}

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn read<T: serde::de::DeserializeOwned>(path: &Path) -> anyhow::Result<T> {
    let metadata = std::fs::symlink_metadata(path)?;
    ensure!(
        metadata.is_file()
            && !metadata.file_type().is_symlink()
            && metadata.len() <= 32 * 1024 * 1024,
        "witness_file_invalid"
    );
    Ok(serde_json::from_slice(&std::fs::read(path)?)?)
}
fn required(data: &Path) -> anyhow::Result<bool> {
    let path = data.join("storage-contract.json");
    if !path.exists() {
        return Ok(false);
    }
    let value: serde_json::Value = read(&path)?;
    Ok(value["witness_required"] == true)
}
fn location(data: &Path) -> anyhow::Result<Option<Pin>> {
    let path = data.join("revocation-witness.json");
    let pin: Option<Pin> = if path.exists() || path.is_symlink() {
        Some(read(&path)?)
    } else {
        None
    };
    let configured = std::env::var_os("VISN_REVOCATION_WITNESS_DIR").map(PathBuf::from);
    let expected = std::env::var("VISN_REVOCATION_WITNESS_INSTANCE")
        .ok()
        .map(|value| value.parse::<Uuid>())
        .transpose()?;
    if pin.is_none() && configured.is_none() {
        ensure!(expected.is_none(), "witness_directory_missing");
        ensure!(!required(data)?, "witness_required_pin_missing");
        return Ok(None);
    }
    let directory = std::fs::canonicalize(
        configured
            .as_ref()
            .or_else(|| pin.as_ref().map(|pin| &pin.directory))
            .context("witness_directory_missing")?,
    )?;
    let data = std::fs::canonicalize(data)?;
    ensure!(
        !directory.starts_with(&data) && !data.starts_with(&directory),
        "witness_must_be_outside_data_root"
    );
    let instance: Instance = read(&directory.join("instance.json"))?;
    ensure!(
        instance.schema == "visn-revocation-witness-v1",
        "witness_instance_schema_invalid"
    );
    if let Some(pin) = &pin {
        ensure!(
            pin.schema == "visn-revocation-pin-v1"
                && pin.directory == directory
                && pin.instance == instance.id,
            "witness_binding_mismatch"
        );
    } else {
        ensure!(
            expected == Some(instance.id),
            "witness_explicit_instance_required"
        )
    }
    ensure!(
        expected.is_none_or(|value| value == instance.id),
        "witness_expected_instance_mismatch"
    );
    Ok(Some(pin.unwrap_or(Pin {
        schema: "visn-revocation-pin-v1".into(),
        directory,
        instance: instance.id,
        acknowledged: BTreeMap::new(),
    })))
}

fn records(pin: &Pin) -> anyhow::Result<BTreeMap<Uuid, (Record, String)>> {
    let mut result = BTreeMap::new();
    let metadata = std::fs::symlink_metadata(pin.directory.join("records"))?;
    ensure!(
        metadata.is_dir() && !metadata.file_type().is_symlink(),
        "witness_records_directory_invalid"
    );
    for entry in std::fs::read_dir(pin.directory.join("records"))? {
        let path = entry?.path();
        if path.extension().and_then(|value| value.to_str()) != Some("json") {
            continue;
        }
        let record: Record = read(&path)?;
        ensure!(
            record.schema == "visn-revocation-record-v1"
                && record.instance == pin.instance
                && record.job.revocation.is_some()
                && record.job.result.is_none()
                && record.job.camera_checkpoints.is_empty()
                && record.job.storage_version == 1
                && path.file_stem().and_then(|value| value.to_str())
                    == Some(record.job.id.to_string().as_str()),
            "witness_record_invalid"
        );
        if let Some(registry) = &record.registry {
            registry.validate()?;
        }
        result.insert(record.job.id, (record, hash(&std::fs::read(&path)?)));
    }
    for (id, expected) in &pin.acknowledged {
        ensure!(
            result.get(id).is_some_and(|(_, actual)| actual == expected),
            "witness_acknowledged_record_missing_or_changed"
        );
    }
    Ok(result)
}

pub fn check(data: &Path) -> anyhow::Result<()> {
    if let Some(pin) = location(data)? {
        let _owner = crate::durable::DataOwner::acquire(&pin.directory)?;
        records(&pin)?;
    }
    Ok(())
}

pub fn initialize(directory: &Path) -> anyhow::Result<Uuid> {
    ensure!(
        !directory.exists(),
        "witness_initialization_requires_new_directory"
    );
    use std::os::unix::fs::DirBuilderExt;
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(directory)?;
    let _owner = crate::durable::DataOwner::acquire(directory)?;
    std::fs::DirBuilder::new()
        .mode(0o700)
        .create(directory.join("records"))?;
    let id = Uuid::now_v7();
    crate::durable::atomic_write(
        &directory.join("instance.json"),
        &serde_json::to_vec(&Instance {
            schema: "visn-revocation-witness-v1".into(),
            id,
        })?,
    )?;
    std::fs::File::open(directory.join("records"))?.sync_all()?;
    std::fs::File::open(directory.parent().context("witness_parent_missing")?)?.sync_all()?;
    Ok(id)
}

fn registry(data: &Path) -> anyhow::Result<Option<Registry>> {
    let path = data.join("retrieval/deployments.json");
    if !path.exists() {
        return Ok(None);
    }
    let registry: Registry = read(&path)?;
    registry.validate()?;
    Ok(Some(registry))
}
fn append(data: &Path, pin: &Pin, job: &MemoryJobRecord) -> anyhow::Result<()> {
    let path = pin
        .directory
        .join("records")
        .join(format!("{}.json", job.id));
    if path.exists() {
        let existing: Record = read(&path)?;
        ensure!(
            existing
                .job
                .revocation
                .as_ref()
                .map(|value| (&value.revision, &value.source_paths))
                == job
                    .revocation
                    .as_ref()
                    .map(|value| (&value.revision, &value.source_paths)),
            "witness_revocation_conflict"
        );
    } else {
        let record = Record {
            schema: "visn-revocation-record-v1".into(),
            instance: pin.instance,
            job: job.clone(),
            registry: registry(data)?,
        };
        let bytes = serde_json::to_vec_pretty(&record)?;
        ensure!(bytes.len() <= 32 * 1024 * 1024, "witness_record_capacity");
        crate::durable::atomic_write(&path, &bytes)?;
    }
    Ok(())
}
fn save_pin(
    data: &Path,
    pin: &mut Pin,
    all: &BTreeMap<Uuid, (Record, String)>,
) -> anyhow::Result<()> {
    pin.acknowledged = all
        .iter()
        .map(|(id, (_, digest))| (*id, digest.clone()))
        .collect();
    let bytes = serde_json::to_vec_pretty(pin)?;
    ensure!(bytes.len() <= 32 * 1024 * 1024, "witness_pin_capacity");
    crate::durable::atomic_write(&data.join("revocation-witness.json"), &bytes)?;
    let path = data.join("storage-contract.json");
    let mut contract: crate::storage_contract::Contract = read(&path)?;
    contract.witness_required = true;
    crate::durable::atomic_write(&path, &serde_json::to_vec_pretty(&contract)?)?;
    Ok(())
}

pub fn record(data: &Path, job: &MemoryJobRecord) -> anyhow::Result<()> {
    let Some(mut pin) = location(data)? else {
        return Ok(());
    };
    let _owner = crate::durable::DataOwner::acquire(&pin.directory)?;
    records(&pin)?;
    append(data, &pin, job)?;
    let all = records(&pin)?;
    save_pin(data, &mut pin, &all)
}

/// Recover missing/stale canonical tombstones before AppState or the API exists.
pub fn reconcile(data: &Path) -> anyhow::Result<()> {
    let Some(mut pin) = location(data)? else {
        return Ok(());
    };
    let _owner = crate::durable::DataOwner::acquire(&pin.directory)?;
    use std::os::{fd::AsRawFd, unix::fs::OpenOptionsExt};
    std::fs::create_dir_all(data.join("retrieval"))?;
    let registry_owner = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(data.join("retrieval/operator.lock"))?;
    ensure!(
        unsafe { libc::flock(registry_owner.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
        "witness_registry_busy"
    );
    let local = crate::storage_contract::manifests(data)?;
    records(&pin)?;
    for (_, job) in &local {
        if job.revocation.is_some() {
            append(data, &pin, job)?;
        }
    }
    let all = records(&pin)?;
    let mut catalog = registry(data)?;
    for (id, (record, _)) in &all {
        let existing = local
            .iter()
            .find(|(_, job)| job.id == *id)
            .map(|(_, job)| job);
        if existing.is_some_and(|job| job.revocation.is_some()) {
            ensure!(
                existing.unwrap().revocation.as_ref().map(|r| r.revision)
                    == record.job.revocation.as_ref().map(|r| r.revision),
                "witness_canonical_revision_conflict"
            );
        } else {
            crate::durable::atomic_write(
                &data.join("memory/manifests").join(format!("{id}.json")),
                &serde_json::to_vec_pretty(&record.job)?,
            )?;
        }
        if let Some(saved) = &record.registry {
            let target = catalog.get_or_insert_with(|| {
                let mut value = saved.clone();
                value.active = None;
                value.revision = Uuid::now_v7();
                value
            });
            for (id, value) in &saved.targets {
                if let Some(existing) = target.targets.get(id) {
                    ensure!(
                        existing.url == value.url
                            && existing.worker_profile == value.worker_profile,
                        "witness_registry_conflict"
                    );
                } else {
                    target.targets.insert(*id, value.clone());
                    target.revision = Uuid::now_v7();
                }
            }
            target.validate()?;
        }
    }
    if let Some(catalog) = catalog {
        crate::durable::atomic_write(
            &data.join("retrieval/deployments.json"),
            &serde_json::to_vec_pretty(&catalog)?,
        )?;
    }
    save_pin(data, &mut pin, &all)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn immutable_records_detect_loss_corruption_and_revision_conflict() {
        let base = std::env::temp_dir().join(format!("visn-witness-{}", Uuid::now_v7()));
        let directory = base.join("independent");
        let instance = initialize(&directory).unwrap();
        assert!(initialize(&directory).is_err());
        let data = base.join("data");
        std::fs::create_dir_all(&data).unwrap();
        let id = Uuid::now_v7();
        let revision = Uuid::now_v7();
        let mut job:MemoryJobRecord=serde_json::from_value(serde_json::json!({"id":id,"request":{"name":"witness","cameras":[]},
            "status":"cancelled","created_at_ms":1,"started_at_ms":null,"completed_at_ms":2,"result":null,"error":"recording_revoked",
            "revocation":{"revision":revision,"revoked_at_ms":2,"cleanup_state":"pending","attempts":0,"next_attempt_at_ms":0,
                "worker_fingerprint":null,"last_error":null,"receipt":null}})).unwrap();
        let mut pin = Pin {
            schema: "visn-revocation-pin-v1".into(),
            directory: std::fs::canonicalize(&directory).unwrap(),
            instance,
            acknowledged: BTreeMap::new(),
        };
        append(&data, &pin, &job).unwrap();
        let all = records(&pin).unwrap();
        pin.acknowledged.insert(id, all[&id].1.clone());
        let path = directory.join("records").join(format!("{id}.json"));
        let original = std::fs::read(&path).unwrap();
        job.revocation.as_mut().unwrap().attempts = 8;
        append(&data, &pin, &job).unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), original);
        job.revocation.as_mut().unwrap().revision = Uuid::now_v7();
        assert!(append(&data, &pin, &job).is_err());
        std::fs::remove_file(&path).unwrap();
        assert!(records(&pin).is_err());
        std::fs::write(&path, b"{corrupt").unwrap();
        assert!(records(&pin).is_err());
        std::fs::write(&path, original).unwrap();
        assert!(records(&pin).is_ok());
        std::fs::remove_dir_all(base).unwrap();
    }
}
