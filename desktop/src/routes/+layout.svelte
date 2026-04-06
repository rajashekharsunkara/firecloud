<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { invoke } from '@tauri-apps/api/core';
  
  type DesktopSettings = {
    dark_mode: boolean;
    node_role: string;
  };
  
  let currentPage = 'files';
  let isConnected = false;
  let darkMode = false;
  let nodeRole = 'consumer';
  
  const pages = [
    { id: 'files', label: 'Files', icon: '📁' },
    { id: 'audit', label: 'Audit', icon: '📋' },
    { id: 'settings', label: 'Settings', icon: '⚙️' },
  ];
  
  onMount(async () => {
    try {
      const settings = await invoke<DesktopSettings>('get_settings');
      darkMode = settings.dark_mode;
      nodeRole = settings.node_role;
      
      await invoke('get_health');
      isConnected = true;
    } catch (e) {
      console.error('Failed to connect:', e);
    }
  });
</script>

<div class="app" data-theme={darkMode ? 'dark' : 'light'}>
  <aside class="sidebar">
    <div class="logo">
      <span class="logo-icon">🔥</span>
      <span class="logo-text">FireCloud</span>
    </div>
    
    <nav class="nav">
      {#each pages as page}
        <button
          class="nav-item"
          class:active={currentPage === page.id}
          on:click={() => currentPage = page.id}
        >
          <span class="nav-icon">{page.icon}</span>
          <span class="nav-label">{page.label}</span>
        </button>
      {/each}
    </nav>
    
    <div class="sidebar-footer">
      <div class="connection-status" class:connected={isConnected}>
        <span class="status-dot"></span>
        <span>{isConnected ? 'Connected' : 'Offline'}</span>
      </div>
      <div class="node-role">
        {nodeRole === 'storage' ? '💾 Provider' : '📱 Consumer'}
      </div>
    </div>
  </aside>
  
  <main class="main">
    <slot />
  </main>
</div>

<style>
  .app {
    display: flex;
    height: 100vh;
    background: var(--background);
  }
  
  .sidebar {
    width: 240px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: 1rem;
  }
  
  .logo {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem;
    margin-bottom: 1.5rem;
  }
  
  .logo-icon {
    font-size: 1.5rem;
  }
  
  .logo-text {
    font-size: 1.25rem;
    font-weight: 700;
    color: var(--text);
  }
  
  .nav {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  
  .nav-item {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.75rem 1rem;
    border: none;
    background: transparent;
    border-radius: var(--radius);
    cursor: pointer;
    color: var(--text-secondary);
    transition: all 0.2s;
    text-align: left;
    width: 100%;
  }
  
  .nav-item:hover {
    background: var(--border);
    color: var(--text);
  }
  
  .nav-item.active {
    background: var(--primary);
    color: var(--on-primary);
  }
  
  .nav-icon {
    font-size: 1.25rem;
  }
  
  .nav-label {
    font-size: 0.875rem;
    font-weight: 500;
  }
  
  .sidebar-footer {
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  
  .connection-status {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.75rem;
    color: var(--text-secondary);
  }
  
  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--error);
  }
  
  .connection-status.connected .status-dot {
    background: var(--success);
  }
  
  .node-role {
    font-size: 0.75rem;
    color: var(--text-secondary);
  }
  
  .main {
    flex: 1;
    overflow-y: auto;
    padding: 2rem;
  }
</style>
