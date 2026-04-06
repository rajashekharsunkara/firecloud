use crate::config::Settings;
use crate::state::{AppState, AuditAppeal, AuditEvent, FileInfo, NodeInfo, PeerInfo};
use std::sync::Mutex;
use tauri::State;

fn audit_identity(state: &State<'_, Mutex<AppState>>) -> Result<(String, String), String> {
    let guard = state.lock().map_err(|e| e.to_string())?;
    let device_id = guard
        .device_id
        .clone()
        .unwrap_or_else(|| "desktop-device".to_string());
    let public_key = guard
        .public_key
        .clone()
        .unwrap_or_else(|| "desktop-public-key".to_string());
    Ok((device_id, public_key))
}

// ============================================================================
// Health & Connection
// ============================================================================

#[tauri::command]
pub async fn get_health(state: State<'_, Mutex<AppState>>) -> Result<serde_json::Value, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/health", settings.server_url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    
    // Update connection state
    {
        let mut guard = state.lock().map_err(|e| e.to_string())?;
        guard.is_connected = true;
    }
    
    Ok(json)
}

// ============================================================================
// File Operations
// ============================================================================

#[tauri::command]
pub async fn list_files(state: State<'_, Mutex<AppState>>) -> Result<Vec<FileInfo>, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/files", settings.server_url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let files: Vec<FileInfo> = response.json().await.map_err(|e| e.to_string())?;
    Ok(files)
}

#[tauri::command]
pub async fn upload_file(
    state: State<'_, Mutex<AppState>>,
    file_path: String,
    file_name: String,
) -> Result<String, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let file_bytes = std::fs::read(&file_path).map_err(|e| e.to_string())?;
    
    // URL-encode the filename to handle special characters
    let encoded_name: String = file_name
        .chars()
        .map(|c| match c {
            ' ' => "%20".to_string(),
            '!' => "%21".to_string(),
            '#' => "%23".to_string(),
            '$' => "%24".to_string(),
            '%' => "%25".to_string(),
            '&' => "%26".to_string(),
            '\'' => "%27".to_string(),
            '(' => "%28".to_string(),
            ')' => "%29".to_string(),
            '+' => "%2B".to_string(),
            ',' => "%2C".to_string(),
            ';' => "%3B".to_string(),
            '=' => "%3D".to_string(),
            '?' => "%3F".to_string(),
            '@' => "%40".to_string(),
            '[' => "%5B".to_string(),
            ']' => "%5D".to_string(),
            _ => c.to_string(),
        })
        .collect();
    
    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/files/upload?file_name={}", settings.server_url, encoded_name))
        .header("Content-Type", "application/octet-stream")
        .body(file_bytes)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    let file_id = json["file_id"].as_str().unwrap_or("").to_string();
    
    Ok(file_id)
}

#[tauri::command]
pub async fn download_file(
    state: State<'_, Mutex<AppState>>,
    file_id: String,
    save_path: String,
) -> Result<(), String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/files/{}/download", settings.server_url, file_id))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let bytes = response.bytes().await.map_err(|e| e.to_string())?;
    std::fs::write(&save_path, bytes).map_err(|e| e.to_string())?;
    
    Ok(())
}

#[tauri::command]
pub async fn delete_file(state: State<'_, Mutex<AppState>>, file_id: String) -> Result<(), String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    client
        .delete(format!("{}/files/{}", settings.server_url, file_id))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    Ok(())
}

// ============================================================================
// Node Management
// ============================================================================

#[tauri::command]
pub async fn list_nodes(state: State<'_, Mutex<AppState>>) -> Result<Vec<NodeInfo>, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/nodes", settings.server_url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let nodes: Vec<NodeInfo> = response.json().await.map_err(|e| e.to_string())?;
    Ok(nodes)
}

#[tauri::command]
pub async fn get_node_state(state: State<'_, Mutex<AppState>>) -> Result<serde_json::Value, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/node/state", settings.server_url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    Ok(json)
}

#[tauri::command]
pub async fn set_node_role(
    state: State<'_, Mutex<AppState>>,
    role: String,
    storage_gb: Option<u64>,
) -> Result<(), String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let mut payload = serde_json::json!({
        "role": role,
    });
    
    if let Some(gb) = storage_gb {
        payload["storage_bytes"] = serde_json::json!(gb * 1024 * 1024 * 1024);
    }
    
    let client = reqwest::Client::new();
    client
        .post(format!("{}/node/role", settings.server_url))
        .json(&payload)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    // Update local settings
    {
        let mut guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.node_role = role;
        if let Some(gb) = storage_gb {
            guard.settings.storage_quota_gb = gb;
        }
    }
    
    Ok(())
}

#[tauri::command]
pub async fn set_storage_quota(
    state: State<'_, Mutex<AppState>>,
    quota_gb: u64,
) -> Result<(), String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let payload = serde_json::json!({
        "total_bytes": quota_gb * 1024 * 1024 * 1024,
    });
    
    let client = reqwest::Client::new();
    client
        .post(format!("{}/node/quota", settings.server_url))
        .json(&payload)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    // Update local settings
    {
        let mut guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.storage_quota_gb = quota_gb;
    }
    
    Ok(())
}

// ============================================================================
// Network Discovery
// ============================================================================

#[tauri::command]
pub async fn get_discovered_peers(state: State<'_, Mutex<AppState>>) -> Result<Vec<PeerInfo>, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/network/peers", settings.server_url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let peers: Vec<PeerInfo> = response.json().await.map_err(|e| e.to_string())?;
    
    // Update state
    {
        let mut guard = state.lock().map_err(|e| e.to_string())?;
        guard.discovered_peers = peers.clone();
    }
    
    Ok(peers)
}

#[tauri::command]
pub async fn get_network_stats(state: State<'_, Mutex<AppState>>) -> Result<serde_json::Value, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/network/stats", settings.server_url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    Ok(json)
}

// ============================================================================
// Audit & Consensus
// ============================================================================

#[tauri::command]
pub async fn submit_audit_appeal(
    state: State<'_, Mutex<AppState>>,
    reason: String,
    justification: String,
    scope_start: Option<String>,
    scope_end: Option<String>,
) -> Result<String, String> {
    let (requester_device_id, requester_public_key) = audit_identity(&state)?;
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let payload = serde_json::json!({
        "requester_device_id": requester_device_id,
        "requester_public_key": requester_public_key,
        "reason": reason,
        "justification": justification,
        "scope_start": scope_start,
        "scope_end": scope_end,
    });
    
    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/audit/appeals", settings.server_url))
        .json(&payload)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    let appeal_id = json["appeal_id"].as_str().unwrap_or("").to_string();
    
    Ok(appeal_id)
}

#[tauri::command]
pub async fn vote_on_appeal(
    state: State<'_, Mutex<AppState>>,
    appeal_id: String,
    approve: bool,
    reason: String,
) -> Result<(), String> {
    let (voter_device_id, voter_public_key) = audit_identity(&state)?;
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let payload = serde_json::json!({
        "voter_device_id": voter_device_id,
        "voter_public_key": voter_public_key,
        "vote": approve,
        "reason": reason,
    });
    
    let client = reqwest::Client::new();
    client
        .post(format!("{}/audit/appeals/{}/vote", settings.server_url, appeal_id))
        .json(&payload)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    Ok(())
}

#[tauri::command]
pub async fn get_pending_appeals(state: State<'_, Mutex<AppState>>) -> Result<Vec<AuditAppeal>, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/audit/appeals/pending", settings.server_url))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let appeals: Vec<AuditAppeal> = response.json().await.map_err(|e| e.to_string())?;
    Ok(appeals)
}

#[tauri::command]
pub async fn get_audit_events(
    state: State<'_, Mutex<AppState>>,
    grant_id: Option<String>,
    limit: Option<u32>,
) -> Result<Vec<AuditEvent>, String> {
    let (requester_device_id, requester_public_key) = audit_identity(&state)?;
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };

    let client = reqwest::Client::new();
    let mut request = client
        .get(format!("{}/audit/events", settings.server_url))
        .query(&[
            ("requester_device_id", requester_device_id.as_str()),
            ("requester_public_key", requester_public_key.as_str()),
        ]);
    if let Some(grant) = grant_id {
        request = request.query(&[("grant_id", grant.as_str())]);
    }
    if let Some(lim) = limit {
        request = request.query(&[("limit", lim)]);
    }
    let response = request
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let events: Vec<AuditEvent> = response.json().await.map_err(|e| e.to_string())?;
    Ok(events)
}

#[tauri::command]
pub async fn verify_audit_chain(state: State<'_, Mutex<AppState>>) -> Result<serde_json::Value, String> {
    let (requester_device_id, requester_public_key) = audit_identity(&state)?;
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };
    
    let client = reqwest::Client::new();
    let response = client
        .get(format!("{}/audit/verify", settings.server_url))
        .query(&[
            ("requester_device_id", requester_device_id.as_str()),
            ("requester_public_key", requester_public_key.as_str()),
        ])
        .send()
        .await
        .map_err(|e| e.to_string())?;
    
    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    Ok(json)
}

// ============================================================================
// Settings
// ============================================================================

#[tauri::command]
pub fn get_settings(state: State<'_, Mutex<AppState>>) -> Result<Settings, String> {
    let guard = state.lock().map_err(|e| e.to_string())?;
    Ok(guard.settings.clone())
}

#[tauri::command]
pub fn save_settings(state: State<'_, Mutex<AppState>>, settings: Settings) -> Result<(), String> {
    let mut guard = state.lock().map_err(|e| e.to_string())?;
    guard.settings = settings;
    Ok(())
}
