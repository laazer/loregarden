// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod config;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{Manager, RunEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

use crate::backend::ManagedChild;
use crate::config::AppConfig;

type SharedChild = Arc<Mutex<Option<ManagedChild>>>;

fn main() {
    let cfg = AppConfig::load();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .manage(cfg.clone())
        .manage(SharedChild::default())
        .setup(|app| {
            let app_handle = app.handle().clone();
            let cfg = app.state::<AppConfig>().inner().clone();
            let shared = app.state::<SharedChild>().inner().clone();

            tauri::async_runtime::spawn(async move {
                match start_backend(&app_handle, &cfg).await {
                    Ok(child) => {
                        *shared.lock().unwrap() = Some(child);
                        if let Some(window) = app_handle.get_webview_window("main") {
                            let _ = window.show();
                        }
                        log::info!(
                            "backend ready — window shown ({}:{})",
                            cfg.backend.host,
                            cfg.backend.port
                        );
                    }
                    Err(err) => {
                        log::error!("backend startup failed: {err}");
                        app_handle
                            .dialog()
                            .message(format!(
                                "Loregarden couldn't start its backend service and cannot continue.\n\n{err}"
                            ))
                            .title("Loregarden failed to start")
                            .kind(MessageDialogKind::Error)
                            .blocking_show();
                        app_handle.exit(1);
                    }
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Loregarden desktop app");

    // `RunEvent::ExitRequested` only fires for cooperative shutdown paths
    // (window close, Cmd+Q, `app.exit()`). A signal sent straight to the
    // process — `kill`, a session manager, CI teardown — bypasses tao's
    // event loop entirely and would otherwise orphan the backend. Handle
    // that path explicitly so the guarantee holds either way.
    #[cfg(unix)]
    {
        let shared_for_signal = app.state::<SharedChild>().inner().clone();
        let cfg_for_signal = app.state::<AppConfig>().inner().clone();
        std::thread::spawn(move || {
            use signal_hook::consts::{SIGINT, SIGTERM};
            use signal_hook::iterator::Signals;

            let mut signals = match Signals::new([SIGTERM, SIGINT]) {
                Ok(signals) => signals,
                Err(err) => {
                    log::error!("failed to install signal handler: {err}");
                    return;
                }
            };
            if signals.forever().next().is_some() {
                log::info!("received termination signal — shutting down backend");
                if let Some(child) = shared_for_signal.lock().unwrap().take() {
                    backend::terminate(
                        child,
                        Duration::from_secs(cfg_for_signal.backend.shutdown_grace_secs),
                    );
                }
                std::process::exit(0);
            }
        });
    }

    app.run(move |app_handle, event| {
        // RunEvent::ExitRequested is documented as "the app is about to
        // exit," but on macOS it does NOT fire for Cmd+Q, the Dock, or the
        // app menu's Quit — only for closing the window via its close
        // button or an explicit `app_handle.exit()` call (confirmed, still
        // open upstream: https://github.com/tauri-apps/tauri/issues/9198).
        // RunEvent::Exit is the one that reliably fires for every
        // cooperative shutdown path, so cleanup lives here instead. Kept
        // idempotent (`.take()`) since ExitRequested can still fire first
        // on the paths where it does work.
        let is_exit = matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit);
        if is_exit {
            let shared = app_handle.state::<SharedChild>().inner().clone();
            let cfg = app_handle.state::<AppConfig>().inner().clone();
            let taken = shared.lock().unwrap().take();
            if let Some(child) = taken {
                log::info!("app exiting — shutting down backend");
                backend::terminate(child, Duration::from_secs(cfg.backend.shutdown_grace_secs));
            }
        }
    });
}

/// Spawn the backend (dev source tree vs. prod sidecar, chosen by build
/// profile) and block until it's accepting connections or the startup
/// timeout elapses.
async fn start_backend(app: &tauri::AppHandle, cfg: &AppConfig) -> Result<ManagedChild, String> {
    let child = if cfg!(debug_assertions) {
        let repo_root = backend::dev_repo_root();
        backend::spawn_dev(&cfg.backend, &repo_root)
            .map_err(|e| format!("spawning dev backend: {e}"))?
    } else {
        let app_data_dir = app
            .path()
            .app_data_dir()
            .map_err(|e| format!("resolving app data dir: {e}"))?;
        backend::ensure_app_data_seeded(app, &app_data_dir)
            .map_err(|e| format!("seeding app data dir: {e}"))?;
        backend::spawn_prod(app, &cfg.backend, &app_data_dir)?
    };

    let ready = {
        let host = cfg.backend.host.clone();
        let port = cfg.backend.port;
        let timeout = Duration::from_secs(cfg.backend.startup_timeout_secs);
        let poll = Duration::from_millis(cfg.backend.poll_interval_ms);
        tauri::async_runtime::spawn_blocking(move || {
            backend::wait_until_ready(&host, port, timeout, poll)
        })
        .await
        .map_err(|e| format!("readiness check task panicked: {e}"))?
    };

    if !ready {
        let timeout_secs = cfg.backend.startup_timeout_secs;
        backend::terminate(child, Duration::from_secs(cfg.backend.shutdown_grace_secs));
        return Err(format!(
            "backend did not start accepting connections on {}:{} within {}s",
            cfg.backend.host, cfg.backend.port, timeout_secs
        ));
    }

    Ok(child)
}
