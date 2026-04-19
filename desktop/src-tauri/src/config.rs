use directories::ProjectDirs;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    pub server_url: String,
    pub node_role: String, // "storage" or "consumer"
    pub storage_quota_gb: u64,
    pub dark_mode: bool,
    pub start_minimized: bool,
    pub auto_start: bool,
    pub sync_folder: Option<String>,
    pub account_id: Option<String>,
    pub auth_bearer_token: Option<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            server_url: "https://signal.firecloud.app".to_string(),
            node_role: "consumer".to_string(),
            storage_quota_gb: 10,
            dark_mode: true,
            start_minimized: false,
            auto_start: false,
            sync_folder: None,
            account_id: None,
            auth_bearer_token: None,
        }
    }
}

const SETTINGS_FILE: &str = "settings.json";

fn settings_path() -> Result<PathBuf, String> {
    let project_dirs = ProjectDirs::from("app", "firecloud", "desktop")
        .ok_or_else(|| "unable to resolve desktop config directory".to_string())?;
    let config_dir = project_dirs.config_dir();
    fs::create_dir_all(config_dir).map_err(|e| e.to_string())?;
    Ok(config_dir.join(SETTINGS_FILE))
}

pub fn load_settings() -> Settings {
    let path = match settings_path() {
        Ok(path) => path,
        Err(_) => return Settings::default(),
    };
    let raw = match fs::read_to_string(path) {
        Ok(raw) => raw,
        Err(_) => return Settings::default(),
    };
    serde_json::from_str::<Settings>(&raw).unwrap_or_default()
}

pub fn save_settings(settings: &Settings) -> Result<(), String> {
    let path = settings_path()?;
    let encoded = serde_json::to_string_pretty(settings).map_err(|e| e.to_string())?;
    fs::write(path, encoded).map_err(|e| e.to_string())
}
