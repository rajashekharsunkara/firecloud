use crate::config::{save_settings as persist_settings, Settings};
use crate::state::{AppState, AuditAppeal, AuditEvent, FileInfo, NodeInfo, PeerInfo};
use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use reqwest::{Client, RequestBuilder};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use tauri::State;

const MANIFEST_SALT: &str = "firecloud-manifest-salt-v1";

#[derive(Debug, Clone, Deserialize)]
struct RelayManifestListResponse {
    manifests: Vec<RelayManifestEnvelope>,
}

#[derive(Debug, Clone, Deserialize)]
struct RelayManifestEnvelope {
    #[serde(default)]
    file_id: String,
    #[serde(default)]
    encrypted_payload: String,
    #[serde(default)]
    created_at: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct RelayPeerListResponse {
    peers: Vec<RelayPeer>,
}

#[derive(Debug, Clone, Deserialize)]
struct RelayPeer {
    #[serde(default)]
    device_id: String,
    #[serde(default)]
    public_ip: Option<String>,
    #[serde(default)]
    public_port: Option<u16>,
    #[serde(default)]
    role: String,
    #[serde(default)]
    available_storage: u64,
}

#[derive(Debug, Clone, Deserialize)]
struct ManifestPayload {
    #[serde(default)]
    file_id: String,
    #[serde(default)]
    file_name: String,
    #[serde(default)]
    file_size: u64,
    #[serde(default)]
    created_at: String,
    #[serde(default)]
    chunks: Vec<ChunkRefPayload>,
    #[serde(default)]
    encryption_key_b64: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct ChunkRefPayload {
    hash: String,
    offset: usize,
    size: usize,
    #[serde(default)]
    node_ids: Vec<String>,
}

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

fn api_url(base: &str, path: &str) -> String {
    format!(
        "{}/{}",
        base.trim_end_matches('/'),
        path.trim_start_matches('/')
    )
}

fn with_auth_headers(builder: RequestBuilder, settings: &Settings) -> RequestBuilder {
    let mut request = builder;
    if let Some(token) = settings
        .auth_bearer_token
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
    {
        request = request.bearer_auth(token);
    }
    if let Some(account_id) = settings
        .account_id
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
    {
        request = request
            .header("X-FireCloud-Account-Id", account_id)
            .header("X-Account-Id", account_id);
    }
    request
}

fn required_account_owner(settings: &Settings) -> Result<String, String> {
    let owner_id = settings
        .account_id
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| "account_id is required for account-scoped mode".to_string())?;
    let has_token = settings
        .auth_bearer_token
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .is_some();
    if !has_token {
        return Err("auth_bearer_token is required when account_id is set".to_string());
    }
    Ok(owner_id.to_string())
}

fn is_account_mode(settings: &Settings) -> bool {
    settings
        .account_id
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .is_some()
}

fn derive_account_key(owner_id: &str) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(owner_id.as_bytes());
    hasher.update(MANIFEST_SALT.as_bytes());
    let digest = hasher.finalize();
    let mut key = [0u8; 32];
    key.copy_from_slice(&digest);
    key
}

fn xor_stream_decrypt(ciphertext: &[u8], key: &[u8; 32]) -> Result<Vec<u8>, String> {
    if ciphertext.len() < 24 {
        return Err("ciphertext too short (missing nonce)".to_string());
    }

    let nonce = &ciphertext[..24];
    let encrypted = &ciphertext[24..];
    let mut plaintext = vec![0u8; encrypted.len()];
    let mut offset = 0usize;
    let mut counter = 0u32;

    while offset < encrypted.len() {
        let mut hasher = Sha256::new();
        hasher.update(key);
        hasher.update(nonce);
        hasher.update(counter.to_le_bytes());
        let block = hasher.finalize();
        for byte in block {
            if offset >= encrypted.len() {
                break;
            }
            plaintext[offset] = encrypted[offset] ^ byte;
            offset += 1;
        }
        counter = counter.wrapping_add(1);
    }

    Ok(plaintext)
}

fn decode_manifest_payload(
    envelope: &RelayManifestEnvelope,
    owner_id: &str,
) -> Result<ManifestPayload, String> {
    let encrypted = STANDARD
        .decode(envelope.encrypted_payload.as_bytes())
        .map_err(|e| format!("invalid manifest payload encoding: {e}"))?;
    let account_key = derive_account_key(owner_id);
    let plaintext = xor_stream_decrypt(&encrypted, &account_key)?;
    let decoded_json =
        String::from_utf8(plaintext).map_err(|e| format!("manifest payload is not UTF-8: {e}"))?;
    let mut manifest: ManifestPayload =
        serde_json::from_str(&decoded_json).map_err(|e| format!("invalid manifest JSON: {e}"))?;
    if manifest.file_id.is_empty() {
        manifest.file_id = envelope.file_id.clone();
    }
    if manifest.file_name.trim().is_empty() {
        manifest.file_name = manifest.file_id.clone();
    }
    if manifest.created_at.trim().is_empty() {
        manifest.created_at = envelope
            .created_at
            .clone()
            .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    }
    Ok(manifest)
}

async fn fetch_account_manifests(
    client: &Client,
    settings: &Settings,
    owner_id: &str,
) -> Result<Vec<ManifestPayload>, String> {
    let response = with_auth_headers(
        client
            .get(api_url(&settings.server_url, "/api/v1/manifests"))
            .query(&[("owner_id", owner_id)]),
        settings,
    )
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
    .map_err(|e| e.to_string())?;

    let body: RelayManifestListResponse = response.json().await.map_err(|e| e.to_string())?;
    let mut manifests = Vec::new();
    for envelope in body.manifests {
        if envelope.encrypted_payload.trim().is_empty() {
            continue;
        }
        if let Ok(manifest) = decode_manifest_payload(&envelope, owner_id) {
            manifests.push(manifest);
        }
    }
    manifests.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    Ok(manifests)
}

async fn fetch_chunk_from_relay(
    client: &Client,
    settings: &Settings,
    chunk: &ChunkRefPayload,
) -> Result<Vec<u8>, String> {
    if chunk.node_ids.is_empty() {
        return Err(format!(
            "chunk {} has no known replica nodes in manifest",
            chunk.hash
        ));
    }
    for node_id in &chunk.node_ids {
        let path = format!("/relay/p2p/{node_id}/chunks/{}", chunk.hash);
        let response = with_auth_headers(client.get(api_url(&settings.server_url, &path)), settings)
            .send()
            .await
            .map_err(|e| e.to_string())?;
        if !response.status().is_success() {
            continue;
        }
        let bytes = response.bytes().await.map_err(|e| e.to_string())?;
        return Ok(bytes.to_vec());
    }
    Err(format!(
        "unable to fetch chunk {} from any replica",
        chunk.hash
    ))
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

    let client = Client::new();
    let response = with_auth_headers(client.get(api_url(&settings.server_url, "/health")), &settings)
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?;

    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
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
    let client = Client::new();

    if is_account_mode(&settings) {
        let owner_id = required_account_owner(&settings)?;
        let manifests = fetch_account_manifests(&client, &settings, &owner_id).await?;
        let files = manifests
            .into_iter()
            .map(|manifest| FileInfo {
                file_id: manifest.file_id.clone(),
                file_name: manifest.file_name,
                file_size: manifest.file_size,
                created_at: manifest.created_at,
            })
            .collect();
        return Ok(files);
    }

    let response = with_auth_headers(client.get(api_url(&settings.server_url, "/files")), &settings)
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
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
    let client = Client::new();
    let response = with_auth_headers(
        client
            .post(api_url(&settings.server_url, "/files/upload"))
            .query(&[("file_name", file_name)]),
        &settings,
    )
    .header("Content-Type", "application/octet-stream")
    .body(file_bytes)
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
    .map_err(|e| e.to_string())?;

    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    let file_id = json["file_id"]
        .as_str()
        .ok_or_else(|| "upload response did not include file_id".to_string())?
        .to_string();
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

    let client = Client::new();

    if is_account_mode(&settings) {
        let owner_id = required_account_owner(&settings)?;
        let manifests = fetch_account_manifests(&client, &settings, &owner_id).await?;
        let manifest = manifests
            .into_iter()
            .find(|entry| entry.file_id == file_id)
            .ok_or_else(|| format!("file {file_id} not found in account manifests"))?;

        let encoded_key = manifest
            .encryption_key_b64
            .ok_or_else(|| "manifest is missing encryption key metadata".to_string())?;
        let key_bytes = STANDARD
            .decode(encoded_key.as_bytes())
            .map_err(|e| format!("invalid file key encoding: {e}"))?;
        if key_bytes.len() != 32 {
            return Err("file key must be 32 bytes".to_string());
        }
        let mut file_key = [0u8; 32];
        file_key.copy_from_slice(&key_bytes);

        let target_size = usize::try_from(manifest.file_size)
            .map_err(|_| "manifest file size exceeds platform limits".to_string())?;
        let mut buffer = vec![0u8; target_size];
        let mut chunks = manifest.chunks.clone();
        chunks.sort_by_key(|chunk| chunk.offset);

        for chunk in chunks {
            let end = chunk
                .offset
                .checked_add(chunk.size)
                .ok_or_else(|| "manifest chunk range overflowed".to_string())?;
            if end > buffer.len() {
                return Err("manifest chunk range exceeds declared file size".to_string());
            }

            let encrypted = fetch_chunk_from_relay(&client, &settings, &chunk).await?;
            let decrypted = xor_stream_decrypt(&encrypted, &file_key)?;
            if decrypted.len() < chunk.size {
                return Err(format!(
                    "decrypted chunk {} is smaller than expected",
                    chunk.hash
                ));
            }
            buffer[chunk.offset..end].copy_from_slice(&decrypted[..chunk.size]);
        }

        std::fs::write(&save_path, &buffer).map_err(|e| e.to_string())?;
        return Ok(());
    }

    let response = with_auth_headers(
        client.get(api_url(
            &settings.server_url,
            &format!("/files/{file_id}/download"),
        )),
        &settings,
    )
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
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

    let client = Client::new();
    if is_account_mode(&settings) {
        let owner_id = required_account_owner(&settings)?;
        with_auth_headers(
            client
                .delete(api_url(
                    &settings.server_url,
                    &format!("/api/v1/manifests/{file_id}"),
                ))
                .query(&[("owner_id", owner_id)]),
            &settings,
        )
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?;
        return Ok(());
    }

    with_auth_headers(
        client.delete(api_url(
            &settings.server_url,
            &format!("/files/{file_id}"),
        )),
        &settings,
    )
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
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

    let client = Client::new();
    let response = with_auth_headers(client.get(api_url(&settings.server_url, "/nodes")), &settings)
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
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

    let client = Client::new();
    let response = with_auth_headers(client.get(api_url(&settings.server_url, "/node/state")), &settings)
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
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
    let mut settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };

    let mut payload = serde_json::json!({
        "role": role,
    });
    if let Some(gb) = storage_gb {
        payload["storage_bytes"] = serde_json::json!(gb * 1024 * 1024 * 1024);
    }

    let client = Client::new();
    with_auth_headers(client.post(api_url(&settings.server_url, "/node/role")).json(&payload), &settings)
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?;

    settings.node_role = role;
    if let Some(gb) = storage_gb {
        settings.storage_quota_gb = gb;
    }
    persist_settings(&settings)?;
    {
        let mut guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings = settings;
    }
    Ok(())
}

#[tauri::command]
pub async fn set_storage_quota(
    state: State<'_, Mutex<AppState>>,
    quota_gb: u64,
) -> Result<(), String> {
    let mut settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };

    let payload = serde_json::json!({
        "total_bytes": quota_gb * 1024 * 1024 * 1024,
    });

    let client = Client::new();
    with_auth_headers(
        client
            .post(api_url(&settings.server_url, "/node/quota"))
            .json(&payload),
        &settings,
    )
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
    .map_err(|e| e.to_string())?;

    settings.storage_quota_gb = quota_gb;
    persist_settings(&settings)?;
    {
        let mut guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings = settings;
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

    let client = Client::new();
    let peers = if is_account_mode(&settings) {
        let account_id = required_account_owner(&settings)?;
        let response = with_auth_headers(
            client
                .get(api_url(&settings.server_url, "/api/v1/peers"))
                .query(&[("account_id", account_id)]),
            &settings,
        )
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?;
        let body: RelayPeerListResponse = response.json().await.map_err(|e| e.to_string())?;
        body.peers
            .into_iter()
            .map(|peer| PeerInfo {
                hostname: peer.device_id.clone(),
                device_id: peer.device_id,
                ip_address: peer.public_ip.unwrap_or_else(|| "relay".to_string()),
                port: peer.public_port.unwrap_or_default(),
                node_type: if peer.role == "storage_provider" {
                    "storage_provider".to_string()
                } else {
                    "consumer".to_string()
                },
                available_storage: peer.available_storage,
                is_online: true,
            })
            .collect::<Vec<_>>()
    } else {
        let response = with_auth_headers(client.get(api_url(&settings.server_url, "/network/peers")), &settings)
            .send()
            .await
            .map_err(|e| e.to_string())?
            .error_for_status()
            .map_err(|e| e.to_string())?;
        response
            .json::<Vec<PeerInfo>>()
            .await
            .map_err(|e| e.to_string())?
    };

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

    let client = Client::new();
    if is_account_mode(&settings) {
        let account_id = required_account_owner(&settings)?;
        let response = with_auth_headers(
            client
                .get(api_url(&settings.server_url, "/api/v1/peers"))
                .query(&[("account_id", account_id)]),
            &settings,
        )
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?;
        let body: RelayPeerListResponse = response.json().await.map_err(|e| e.to_string())?;
        let provider_count = body
            .peers
            .iter()
            .filter(|peer| peer.role == "storage_provider")
            .count();
        let total_available_storage: u64 = body.peers.iter().map(|peer| peer.available_storage).sum();
        return Ok(serde_json::json!({
            "total_peers": body.peers.len(),
            "storage_providers": provider_count,
            "total_available_storage": total_available_storage,
        }));
    }

    let response = with_auth_headers(client.get(api_url(&settings.server_url, "/network/stats")), &settings)
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
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

    let client = Client::new();
    let response = with_auth_headers(
        client
            .post(api_url(&settings.server_url, "/audit/appeals"))
            .json(&payload),
        &settings,
    )
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
    .map_err(|e| e.to_string())?;

    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    let appeal_id = json["appeal_id"]
        .as_str()
        .ok_or_else(|| "appeal response missing appeal_id".to_string())?
        .to_string();
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

    let client = Client::new();
    with_auth_headers(
        client
            .post(api_url(
                &settings.server_url,
                &format!("/audit/appeals/{appeal_id}/vote"),
            ))
            .json(&payload),
        &settings,
    )
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub async fn get_pending_appeals(state: State<'_, Mutex<AppState>>) -> Result<Vec<AuditAppeal>, String> {
    let settings = {
        let guard = state.lock().map_err(|e| e.to_string())?;
        guard.settings.clone()
    };

    let client = Client::new();
    let response = with_auth_headers(
        client.get(api_url(&settings.server_url, "/audit/appeals/pending")),
        &settings,
    )
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
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

    let client = Client::new();
    let mut request = with_auth_headers(
        client
            .get(api_url(&settings.server_url, "/audit/events"))
            .query(&[
                ("requester_device_id", requester_device_id.as_str()),
                ("requester_public_key", requester_public_key.as_str()),
            ]),
        &settings,
    );
    if let Some(grant) = grant_id {
        request = request.query(&[("grant_id", grant.as_str())]);
    }
    if let Some(lim) = limit {
        request = request.query(&[("limit", lim.to_string())]);
    }

    let response = request
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
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

    let client = Client::new();
    let response = with_auth_headers(
        client
            .get(api_url(&settings.server_url, "/audit/verify"))
            .query(&[
                ("requester_device_id", requester_device_id.as_str()),
                ("requester_public_key", requester_public_key.as_str()),
            ]),
        &settings,
    )
    .send()
    .await
    .map_err(|e| e.to_string())?
    .error_for_status()
    .map_err(|e| e.to_string())?;

    let json: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    Ok(json)
}

// ============================================================================
// Settings
// ============================================================================

#[tauri::command]
pub fn get_downloads_directory() -> Result<String, String> {
    let user_dirs = directories::UserDirs::new()
        .ok_or_else(|| "unable to resolve user directories".to_string())?;
    let downloads_dir = user_dirs
        .download_dir()
        .ok_or_else(|| "downloads directory is not available".to_string())?;
    Ok(downloads_dir.to_string_lossy().to_string())
}

#[tauri::command]
pub fn open_file_path(path: String) -> Result<(), String> {
    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("path does not exist: {path}"));
    }

    #[cfg(target_os = "windows")]
    {
        Command::new("cmd")
            .args(["/C", "start", ""])
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub fn get_settings(state: State<'_, Mutex<AppState>>) -> Result<Settings, String> {
    let guard = state.lock().map_err(|e| e.to_string())?;
    Ok(guard.settings.clone())
}

#[tauri::command]
pub fn save_settings(
    state: State<'_, Mutex<AppState>>,
    settings: Settings,
) -> Result<(), String> {
    persist_settings(&settings)?;
    let mut guard = state.lock().map_err(|e| e.to_string())?;
    guard.settings = settings;
    Ok(())
}
