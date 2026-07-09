use std::fs;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

use crate::config::BackendConfig;

/// The two ways the FastAPI control plane gets started. Dev runs the real
/// source tree through `uv` (fast iteration, `--reload`); prod runs the
/// PyInstaller-built sidecar so the packaged app needs neither Python nor uv.
pub enum ManagedChild {
    Dev(Child),
    Sidecar(CommandChild, Arc<AtomicBool>),
}

impl ManagedChild {
    fn pid(&self) -> Option<u32> {
        match self {
            ManagedChild::Dev(child) => Some(child.id()),
            ManagedChild::Sidecar(child, _) => Some(child.pid()),
        }
    }

    fn has_exited(&mut self) -> bool {
        match self {
            ManagedChild::Dev(child) => matches!(child.try_wait(), Ok(Some(_))),
            ManagedChild::Sidecar(_, exited) => exited.load(Ordering::SeqCst),
        }
    }
}

/// Ask the backend to shut down: SIGTERM + wait on Unix (uvicorn handles
/// SIGTERM gracefully on its own), hard kill on Windows since there's no
/// portable SIGTERM-equivalent for an arbitrary child process there.
pub fn terminate(mut child: ManagedChild, grace: Duration) {
    let pid = child.pid();

    #[cfg(unix)]
    {
        if let Some(pid) = pid {
            // SAFETY: kill(2) with a pid we own and a standard termination
            // signal; failure (e.g. it already exited) is not fatal here.
            unsafe {
                libc::kill(pid as libc::pid_t, libc::SIGTERM);
            }
        }
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            if child.has_exited() {
                log::info!("backend (pid {pid:?}) shut down gracefully");
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        log::warn!("backend (pid {pid:?}) did not exit within {grace:?}, force killing");
    }

    kill_hard(child);
}

fn kill_hard(child: ManagedChild) {
    match child {
        ManagedChild::Dev(mut c) => {
            let _ = c.kill();
        }
        ManagedChild::Sidecar(c, _) => {
            let _ = c.kill();
        }
    }
}

/// Poll until the backend is accepting TCP connections, or give up after `timeout`.
pub fn wait_until_ready(host: &str, port: u16, timeout: Duration, poll_interval: Duration) -> bool {
    let addr = format!("{host}:{port}");
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect(&addr).is_ok() {
            return true;
        }
        std::thread::sleep(poll_interval);
    }
    false
}

/// `cargo run`/`tauri dev` always runs from source, so the monorepo root is
/// two levels up from this crate (`src-tauri/../`).
pub fn dev_repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri has a parent directory")
        .to_path_buf()
}

pub fn spawn_dev(cfg: &BackendConfig, repo_root: &Path) -> std::io::Result<ManagedChild> {
    log::info!(
        "starting backend (dev): uv run python -m loregarden --reload --host {} --port {}",
        cfg.host,
        cfg.port
    );
    let child = Command::new("uv")
        .args([
            "run",
            "python",
            "-m",
            "loregarden",
            "--reload",
            "--host",
            &cfg.host,
            "--port",
            &cfg.port.to_string(),
            "--log-level",
            &cfg.log_level,
        ])
        .current_dir(repo_root.join("server"))
        .env("LOREGARDEN_REPO_ROOT", repo_root)
        .env("LOREGARDEN_PARENT_PID", std::process::id().to_string())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()?;
    Ok(ManagedChild::Dev(child))
}

pub fn spawn_prod(
    app: &AppHandle,
    cfg: &BackendConfig,
    app_data_dir: &Path,
) -> Result<ManagedChild, String> {
    log::info!(
        "starting backend (prod sidecar {}) on {}:{}",
        cfg.sidecar_name,
        cfg.host,
        cfg.port
    );
    let (mut rx, child) = app
        .shell()
        .sidecar(&cfg.sidecar_name)
        .map_err(|e| format!("failed to resolve sidecar '{}': {e}", cfg.sidecar_name))?
        .args(["--host", &cfg.host, "--port", &cfg.port.to_string(), "--log-level", &cfg.log_level])
        .env("LOREGARDEN_REPO_ROOT", app_data_dir.to_string_lossy().to_string())
        .env("LOREGARDEN_PARENT_PID", std::process::id().to_string())
        .spawn()
        .map_err(|e| format!("failed to spawn backend sidecar: {e}"))?;

    let exited = Arc::new(AtomicBool::new(false));
    let exited_writer = exited.clone();
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    log::info!("[backend] {}", String::from_utf8_lossy(&bytes).trim_end());
                }
                CommandEvent::Stderr(bytes) => {
                    log::info!("[backend] {}", String::from_utf8_lossy(&bytes).trim_end());
                }
                CommandEvent::Error(err) => {
                    log::error!("[backend] process error: {err}");
                }
                CommandEvent::Terminated(payload) => {
                    log::warn!("[backend] exited with {:?}", payload.code);
                    exited_writer.store(true, Ordering::SeqCst);
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(ManagedChild::Sidecar(child, exited))
}

/// First-run bootstrap for packaged builds: copy the bundled `agent_context/`
/// resource into the OS per-user app-data directory (idempotent — skipped if
/// already present) and make sure `data/` + `project_board/` exist there.
/// Dev mode never calls this; it runs straight out of the real checkout.
pub fn ensure_app_data_seeded(app: &AppHandle, app_data_dir: &Path) -> std::io::Result<()> {
    fs::create_dir_all(app_data_dir)?;
    fs::create_dir_all(app_data_dir.join("data"))?;
    fs::create_dir_all(app_data_dir.join("project_board"))?;

    let dest = app_data_dir.join("agent_context");
    if dest.exists() {
        return Ok(());
    }

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| std::io::Error::other(format!("resolving resource dir: {e}")))?;
    let source = resource_dir.join("agent_context");
    log::info!("seeding agent_context into {}", dest.display());
    copy_dir_recursive(&source, &dest)
}

fn copy_dir_recursive(source: &Path, dest: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dest)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let dest_path = dest.join(entry.file_name());
        if file_type.is_dir() {
            copy_dir_recursive(&entry.path(), &dest_path)?;
        } else {
            fs::copy(entry.path(), &dest_path)?;
        }
    }
    Ok(())
}
