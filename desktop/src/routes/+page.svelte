<script lang="ts">
  import { onMount } from 'svelte';
  import { invoke } from '@tauri-apps/api/core';
  import { open, save } from '@tauri-apps/plugin-dialog';
  
  interface FileInfo {
    file_id: string;
    file_name: string;
    file_size: number;
    created_at: string;
  }
  
  let files: FileInfo[] = [];
  let loading = false;
  let uploading = false;
  let error = '';
  
  onMount(async () => {
    await loadFiles();
  });
  
  async function loadFiles() {
    loading = true;
    error = '';
    try {
      files = await invoke('list_files');
    } catch (e) {
      error = `Failed to load files: ${e}`;
    } finally {
      loading = false;
    }
  }
  
  async function uploadFile() {
    try {
      const selected = await open({
        multiple: false,
        directory: false,
      });
      
      if (selected) {
        uploading = true;
        const fileName = selected.split(/[/\\]/).pop() || 'file';
        await invoke('upload_file', {
          filePath: selected,
          fileName: fileName,
        });
        await loadFiles();
      }
    } catch (e) {
      error = `Upload failed: ${e}`;
    } finally {
      uploading = false;
    }
  }
  
  async function downloadFile(fileId: string, fileName: string) {
    try {
      const savePath = await save({
        defaultPath: fileName,
      });
      
      if (savePath) {
        await invoke('download_file', {
          fileId: fileId,
          savePath: savePath,
        });
      }
    } catch (e) {
      error = `Download failed: ${e}`;
    }
  }
  
  async function deleteFile(fileId: string) {
    if (!confirm('Delete this file?')) return;
    
    try {
      await invoke('delete_file', { fileId });
      await loadFiles();
    } catch (e) {
      error = `Delete failed: ${e}`;
    }
  }
  
  function formatSize(bytes: number): string {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = bytes;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit++;
    }
    return `${size.toFixed(1)} ${units[unit]}`;
  }
  
  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString();
  }
</script>

<div class="page">
  <header class="page-header">
    <h1>Files</h1>
    <button class="btn btn-primary" on:click={uploadFile} disabled={uploading}>
      {uploading ? 'Uploading...' : '+ Upload File'}
    </button>
  </header>
  
  {#if error}
    <div class="error-banner">{error}</div>
  {/if}
  
  {#if loading}
    <div class="loading">Loading files...</div>
  {:else if files.length === 0}
    <div class="empty-state">
      <div class="empty-icon">📁</div>
      <h2>No files yet</h2>
      <p>Upload your first file to get started</p>
    </div>
  {:else}
    <div class="file-list">
      {#each files as file}
        <div class="file-item card">
          <div class="file-icon">📄</div>
          <div class="file-info">
            <div class="file-name">{file.file_name}</div>
            <div class="file-meta">
              {formatSize(file.file_size)} • {formatDate(file.created_at)}
            </div>
          </div>
          <div class="file-actions">
            <button
              class="btn btn-secondary"
              on:click={() => downloadFile(file.file_id, file.file_name)}
            >
              ⬇ Download
            </button>
            <button
              class="btn btn-danger"
              on:click={() => deleteFile(file.file_id)}
            >
              🗑
            </button>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .page {
    max-width: 1200px;
    margin: 0 auto;
  }
  
  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2rem;
  }
  
  h1 {
    font-size: 1.5rem;
    font-weight: 600;
  }
  
  .error-banner {
    background: color-mix(in srgb, var(--error) 12%, transparent);
    color: var(--error);
    padding: 0.75rem 1rem;
    border-radius: var(--radius);
    margin-bottom: 1rem;
  }
  
  .loading {
    text-align: center;
    color: var(--text-secondary);
    padding: 3rem;
  }
  
  .empty-state {
    text-align: center;
    padding: 4rem 2rem;
  }
  
  .empty-icon {
    font-size: 4rem;
    margin-bottom: 1rem;
  }
  
  .empty-state h2 {
    font-size: 1.25rem;
    margin-bottom: 0.5rem;
  }
  
  .empty-state p {
    color: var(--text-secondary);
  }
  
  .file-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  
  .file-item {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 1rem 1.5rem;
  }
  
  .file-icon {
    font-size: 2rem;
  }
  
  .file-info {
    flex: 1;
  }
  
  .file-name {
    font-weight: 500;
    margin-bottom: 0.25rem;
  }
  
  .file-meta {
    font-size: 0.875rem;
    color: var(--text-secondary);
  }
  
  .file-actions {
    display: flex;
    gap: 0.5rem;
  }
</style>
