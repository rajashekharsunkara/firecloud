use crate::config::Settings;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileInfo {
    pub file_id: String,
    pub file_name: String,
    pub file_size: u64,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeInfo {
    pub node_id: String,
    pub endpoint: String,
    pub kind: String,
    pub online: bool,
    pub symbol_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerInfo {
    pub device_id: String,
    pub hostname: String,
    pub ip_address: String,
    pub port: u16,
    pub node_type: String,
    pub available_storage: u64,
    pub is_online: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEvent {
    pub sequence: u64,
    pub event_time: String,
    pub event_type: String,
    pub payload: serde_json::Value,
    pub prev_hash: String,
    pub event_hash: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditAppeal {
    pub appeal_id: String,
    pub requester_device_id: String,
    pub reason: String,
    pub justification: String,
    pub status: String,
    pub created_at: String,
    pub expires_at: String,
    pub vote_count: u32,
    pub votes_needed: u32,
}

pub struct AppState {
    pub settings: Settings,
    pub device_id: Option<String>,
    pub public_key: Option<String>,
    pub is_connected: bool,
    pub discovered_peers: Vec<PeerInfo>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            settings: Settings::default(),
            device_id: None,
            public_key: None,
            is_connected: false,
            discovered_peers: Vec::new(),
        }
    }
}
