use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    pub server_url: String,
    pub node_role: String, // "storage" or "consumer"
    pub storage_quota_gb: u64,
    pub dark_mode: bool,
    pub start_minimized: bool,
    pub auto_start: bool,
    pub sync_folder: Option<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            server_url: "http://localhost:8080".to_string(),
            node_role: "consumer".to_string(),
            storage_quota_gb: 10,
            dark_mode: true,
            start_minimized: false,
            auto_start: false,
            sync_folder: None,
        }
    }
}
