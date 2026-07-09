use std::env;

use serde::Deserialize;

/// Bundled default configuration — compiled into the binary so it's available
/// identically in `cargo run`/`tauri dev` and the packaged app, no resource
/// resolution needed. Env vars below let a developer or packager override it
/// without touching Rust.
const DEFAULT_CONFIG_JSON: &str = include_str!("../config/app.config.json");

#[derive(Debug, Clone, Deserialize)]
pub struct BackendConfig {
    pub host: String,
    pub port: u16,
    #[serde(rename = "startupTimeoutSecs")]
    pub startup_timeout_secs: u64,
    #[serde(rename = "pollIntervalMs")]
    pub poll_interval_ms: u64,
    #[serde(rename = "shutdownGraceSecs")]
    pub shutdown_grace_secs: u64,
    #[serde(rename = "logLevel")]
    pub log_level: String,
    #[serde(rename = "sidecarName")]
    pub sidecar_name: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct AppConfig {
    pub backend: BackendConfig,
}

impl AppConfig {
    pub fn load() -> Self {
        let mut cfg: AppConfig = serde_json::from_str(DEFAULT_CONFIG_JSON)
            .expect("bundled config/app.config.json is invalid JSON");

        if let Ok(v) = env::var("LOREGARDEN_DESKTOP_HOST") {
            cfg.backend.host = v;
        }
        if let Ok(v) = env::var("LOREGARDEN_DESKTOP_PORT") {
            if let Ok(port) = v.parse() {
                cfg.backend.port = port;
            }
        }
        if let Ok(v) = env::var("LOREGARDEN_DESKTOP_LOG_LEVEL") {
            cfg.backend.log_level = v;
        }
        if let Ok(v) = env::var("LOREGARDEN_DESKTOP_STARTUP_TIMEOUT_SECS") {
            if let Ok(n) = v.parse() {
                cfg.backend.startup_timeout_secs = n;
            }
        }
        cfg
    }
}
