// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod config;
mod state;

use std::sync::Mutex;
use state::AppState;

fn main() {
    tauri::Builder::default()
        .manage(Mutex::new(AppState::new()))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            commands::get_health,
            commands::list_files,
            commands::upload_file,
            commands::download_file,
            commands::delete_file,
            commands::list_nodes,
            commands::get_node_state,
            commands::set_node_role,
            commands::set_storage_quota,
            commands::get_discovered_peers,
            commands::get_network_stats,
            commands::submit_audit_appeal,
            commands::vote_on_appeal,
            commands::get_pending_appeals,
            commands::get_audit_events,
            commands::verify_audit_chain,
            commands::get_downloads_directory,
            commands::open_file_path,
            commands::get_settings,
            commands::save_settings,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
