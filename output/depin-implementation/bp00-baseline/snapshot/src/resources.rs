//! A guardian owns shared residency until dedicated model-server cleanup finishes.
//! Closing stdin on scope exit/client death requests cleanup, not immediate unlock.
use std::{process::Stdio, time::Duration};

use anyhow::{bail, Context};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, Command},
    task::JoinHandle,
};

use crate::config::Config;

pub struct ResourceGuard {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stderr: Option<JoinHandle<String>>,
}

impl ResourceGuard {
    pub async fn complete(&mut self) -> anyhow::Result<()> {
        if let Some(mut stdin) = self.stdin.take() {
            stdin
                .write_all(b"completed\n")
                .await
                .context("acknowledge completed external inference")?;
            stdin.flush().await?;
        }
        Ok(())
    }

    pub async fn acquire(config: &Config) -> anyhow::Result<Option<Self>> {
        let Some(directory) = &config.resource_dir else {
            return Ok(None);
        };
        if !config.managed_lmstudio {
            bail!("Coordinated external VLM calls require VISN_MANAGED_LMSTUDIO=true and a dedicated native-v1 server");
        }
        let mut child = Command::new(&config.detector_executable)
            .args(&config.resource_runner_args)
            .env("VISN_RESOURCE_DIR", directory)
            .env(
                "VISN_RESOURCE_WAIT_SECS",
                config.resource_wait_secs.to_string(),
            )
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(false)
            .spawn()
            .context("launch resource guardian")?;
        let mut stdin = child.stdin.take().context("resource guardian stdin")?;
        let mut stderr = child.stderr.take().context("resource guardian stderr")?;
        let errors = tokio::spawn(async move {
            let mut saved = Vec::new();
            let mut chunk = [0_u8; 4096];
            while let Ok(count) = stderr.read(&mut chunk).await {
                if count == 0 {
                    break;
                }
                let keep = count.min(32768_usize.saturating_sub(saved.len()));
                saved.extend_from_slice(&chunk[..keep]);
            }
            String::from_utf8_lossy(&saved).into_owned()
        });
        let payload = serde_json::json!({"endpoint":config.lmstudio_api_base_url,"token":config.gemma_api_key});
        stdin.write_all(format!("{payload}\n").as_bytes()).await?;
        let mut output = BufReader::new(child.stdout.take().context("resource guardian stdout")?);
        let mut line = String::new();
        let admitted = tokio::time::timeout(
            Duration::from_secs(config.resource_wait_secs + 20),
            output.read_line(&mut line),
        )
        .await;
        if !matches!(admitted, Ok(Ok(_))) || line.trim() != "VISN_RESOURCE_READY" {
            drop(stdin);
            let _ = child.kill().await;
            let _ = child.wait().await;
            let detail = errors.await.unwrap_or_default();
            bail!(
                "Resource guardian did not grant residency: {}",
                detail.trim()
            );
        }
        Ok(Some(Self {
            child: Some(child),
            stdin: Some(stdin),
            stderr: Some(errors),
        }))
    }
}

impl Drop for ResourceGuard {
    fn drop(&mut self) {
        self.stdin.take();
        if let (Some(mut child), Some(errors)) = (self.child.take(), self.stderr.take()) {
            if let Ok(runtime) = tokio::runtime::Handle::try_current() {
                runtime.spawn(async move {
                    let status = child.wait().await;
                    let detail = errors.await.unwrap_or_default();
                    if !matches!(status,Ok(status) if status.success()) {
                        tracing::warn!(?status,%detail,"resource cleanup failed; group may require external recovery");
                    }
                });
            }
        }
    }
}
