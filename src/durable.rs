//! Single-writer ownership and fsync/rename publication for local job manifests.
use anyhow::{ensure, Context};
use std::{
    fs::{File, OpenOptions},
    io::Write,
    path::Path,
    sync::{Arc, Mutex, OnceLock, Weak},
};

pub struct DataOwner {
    _file: Arc<File>,
}

static WORKER_OWNER: OnceLock<Mutex<Weak<File>>> = OnceLock::new();

pub struct WorkerLifeline {
    #[cfg(unix)]
    _parent: std::os::unix::net::UnixStream,
    #[cfg(unix)]
    child: Option<std::os::unix::net::UnixStream>,
    _owner: Option<Arc<File>>,
}

/// Isolated process group plus an exec-inherited parent-death channel/owner lease.
pub struct WorkerGroup {
    pid: Option<u32>,
    _lifeline: WorkerLifeline,
}

pub fn isolate_worker(command: &mut tokio::process::Command) -> anyhow::Result<WorkerLifeline> {
    #[cfg(unix)]
    {
        use std::os::{fd::AsRawFd, unix::process::CommandExt};
        let (parent, child) = std::os::unix::net::UnixStream::pair()?;
        let read_fd = child.as_raw_fd();
        let owner = WORKER_OWNER
            .get_or_init(|| Mutex::new(Weak::new()))
            .lock()
            .map_err(|_| anyhow::anyhow!("worker_owner_registry_unavailable"))?
            .upgrade();
        let owner_fd = owner.as_ref().map(|file| file.as_raw_fd());
        command.env("VISN_WORKER_LIFELINE_FD", read_fd.to_string());
        command.as_std_mut().process_group(0);
        // SAFETY: after fork this callback performs only async-signal-safe fcntl.
        // Both descriptors remain owned/alive through spawn; only this child clears CLOEXEC.
        unsafe {
            command.as_std_mut().pre_exec(move || {
                for fd in [Some(read_fd), owner_fd].into_iter().flatten() {
                    if libc::fcntl(fd, libc::F_SETFD, 0) < 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                }
                Ok(())
            });
        }
        Ok(WorkerLifeline {
            _parent: parent,
            child: Some(child),
            _owner: owner,
        })
    }
    #[cfg(not(unix))]
    anyhow::bail!("worker_lifeline_requires_unix_runtime");
}

impl WorkerGroup {
    pub fn new(pid: Option<u32>, mut lifeline: WorkerLifeline) -> Self {
        #[cfg(unix)]
        {
            lifeline.child.take();
        }
        Self {
            pid,
            _lifeline: lifeline,
        }
    }
    pub fn finish(&mut self) {
        #[cfg(unix)]
        if let Some(pid) = self.pid.take() {
            if pid > 0 && pid <= i32::MAX as u32 {
                // SAFETY: this group was created for our own child with process_group(0).
                unsafe {
                    libc::kill(-(pid as i32), libc::SIGKILL);
                }
            }
        }
    }
}
impl Drop for WorkerGroup {
    fn drop(&mut self) {
        self.finish();
    }
}

impl DataOwner {
    pub fn register_workers(&self) -> anyhow::Result<()> {
        *WORKER_OWNER
            .get_or_init(|| Mutex::new(Weak::new()))
            .lock()
            .map_err(|_| anyhow::anyhow!("worker_owner_registry_unavailable"))? =
            Arc::downgrade(&self._file);
        Ok(())
    }
    pub fn acquire(directory: &Path) -> anyhow::Result<Self> {
        std::fs::create_dir_all(directory)?;
        #[cfg(unix)]
        {
            use std::os::{fd::AsRawFd, unix::fs::OpenOptionsExt};
            let mut file = OpenOptions::new()
                .create(true)
                .truncate(false)
                .read(true)
                .write(true)
                .mode(0o600)
                .custom_flags(libc::O_NOFOLLOW)
                .open(directory.join(".writer.lock"))?;
            // SAFETY: the descriptor belongs to this live File; flock does not retain a pointer.
            let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
            ensure!(
                result == 0,
                "data_directory_already_owned: {}",
                std::io::Error::last_os_error()
            );
            file.set_len(0)?;
            writeln!(file, "{}", std::process::id())?;
            file.sync_all()?;
            Ok(Self {
                _file: Arc::new(file),
            })
        }
        #[cfg(not(unix))]
        anyhow::bail!("local durable ownership requires the qualified Unix runtime");
    }
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> anyhow::Result<()> {
    let directory = path.parent().context("manifest_directory_missing")?;
    std::fs::create_dir_all(directory)?;
    let temporary = directory.join(format!(
        ".{}.{}.pending",
        path.file_name()
            .context("manifest_filename")?
            .to_string_lossy(),
        uuid::Uuid::now_v7()
    ));
    let operation = || -> anyhow::Result<()> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        std::fs::rename(&temporary, path)?;
        File::open(directory)?.sync_all()?;
        Ok(())
    };
    let result = operation();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

pub async fn publish(path: std::path::PathBuf, bytes: Vec<u8>) -> anyhow::Result<()> {
    tokio::task::spawn_blocking(move || atomic_write(&path, &bytes)).await?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ownership_is_exclusive_and_released_without_stale_pid_recovery() {
        let directory = std::env::temp_dir().join(format!("visn-owner-{}", uuid::Uuid::now_v7()));
        let owner = DataOwner::acquire(&directory).unwrap();
        assert!(DataOwner::acquire(&directory).is_err());
        drop(owner);
        let owner = DataOwner::acquire(&directory).unwrap();
        atomic_write(&directory.join("record.json"), b"one").unwrap();
        atomic_write(&directory.join("record.json"), b"two").unwrap();
        assert_eq!(
            std::fs::read(directory.join("record.json")).unwrap(),
            b"two"
        );
        drop(owner);
        std::fs::remove_dir_all(directory).unwrap();
    }
}
