<script lang="ts">
  import { onMount } from 'svelte';
  import { invoke } from '@tauri-apps/api/core';
  import { open, save } from '@tauri-apps/plugin-dialog';
  import { onAuthStateChanged, signInWithPopup, signOut } from 'firebase/auth';
  import { firebaseAuth, googleProvider } from '$lib/firebase';
  import Cloud from 'lucide-svelte/icons/cloud';
  import Download from 'lucide-svelte/icons/download';
  import ExternalLink from 'lucide-svelte/icons/external-link';
  import FileText from 'lucide-svelte/icons/file-text';
  import FolderOpen from 'lucide-svelte/icons/folder-open';
  import Globe from 'lucide-svelte/icons/globe';
  import HardDrive from 'lucide-svelte/icons/hard-drive';
  import Network from 'lucide-svelte/icons/network';
  import RefreshCw from 'lucide-svelte/icons/refresh-cw';
  import Settings2 from 'lucide-svelte/icons/settings-2';
  import Smartphone from 'lucide-svelte/icons/smartphone';
  import Trash2 from 'lucide-svelte/icons/trash-2';
  import Upload from 'lucide-svelte/icons/upload';
  import Wrench from 'lucide-svelte/icons/wrench';

  interface FileInfo {
    file_id: string;
    file_name: string;
    file_size: number;
    created_at: string;
  }

  interface PeerInfo {
    device_id: string;
    hostname: string;
    ip_address: string;
    port: number;
    node_type: string;
    available_storage: number;
    is_online: boolean;
  }

  interface DesktopSettings {
    server_url: string;
    node_role: string;
    storage_quota_gb: number;
    dark_mode: boolean;
    start_minimized: boolean;
    auto_start: boolean;
    sync_folder: string | null;
    account_id: string | null;
    auth_bearer_token: string | null;
  }

  type Page = 'files' | 'network' | 'settings';

  const GOOGLE_WEB_CLIENT_ID = '802718577477-cqm8n897sp24tqgtovdm14kg4c3bualh.apps.googleusercontent.com';
  const FIREBASE_WEB_API_KEY = 'AIzaSyBbDFpcOr_dz52rLv9SL8arRUlTgm5SF5Y';

  type GoogleApi = {
    accounts?: {
      id?: {
        initialize: (config: {
          client_id: string;
          callback: (response: { credential?: string }) => void;
          auto_select?: boolean;
          cancel_on_tap_outside?: boolean;
        }) => void;
        prompt: (
          callback?: (notification: {
            isNotDisplayed?: () => boolean;
            isSkippedMoment?: () => boolean;
            isDismissedMoment?: () => boolean;
          }) => void,
        ) => void;
      };
    };
  };

  let googleScriptPromise: Promise<void> | null = null;

  let currentPage: Page = 'files';
  let files: FileInfo[] = [];
  let peers: PeerInfo[] = [];
  let networkStats: Record<string, unknown> = {};
  let settings: DesktopSettings | null = null;

  let loadingFiles = false;
  let loadingPeers = false;
  let uploading = false;
  let savingSettings = false;
  let applyingRole = false;
  let authInProgress = false;
  let downloadInProgress = false;
  let isConnected = false;
  let healthChecked = false;
  let error = '';
  let success = '';
  let downloadedPaths: Record<string, string> = {};

  let serverUrl = '';
  let nodeRole = 'consumer';
  let storageQuotaGb = 10;
  let darkMode = true;
  let accountId = '';
  let authToken = '';
  let setupIssue = '';

  const pages = [
    { id: 'files' as const, label: 'Files' },
    { id: 'network' as const, label: 'Network' },
    { id: 'settings' as const, label: 'Settings' },
  ];

  $: accountModeEnabled = accountId.trim().length > 0;
  $: setupIssue = getSetupIssue();

  onMount(() => {
    const unsubscribe = onAuthStateChanged(firebaseAuth, async (user) => {
      if (!user) return;
      accountId = user.uid;
      authToken = await user.getIdToken();
    });

    void (async () => {
      await loadSettings();
      await refreshHealth();
      if (!getSetupIssue() && isConnected) {
        await Promise.all([loadFiles(), loadPeers()]);
      }
    })();

    return () => {
      unsubscribe();
    };
  });

  function clearMessages() {
    error = '';
    success = '';
  }

  function formatError(err: unknown): string {
    return typeof err === 'string' ? err : String(err);
  }

  function normalizeServerUrl(input: string): string {
    return input.trim();
  }

  function isLocalServer(url: string): boolean {
    try {
      const parsed = new URL(url);
      return parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1';
    } catch {
      return false;
    }
  }

  function requiresAccountMode(url: string): boolean {
    if (!url) return false;
    return !isLocalServer(url);
  }

  function hasAccountCredentials(): boolean {
    return accountId.trim().length > 0 && authToken.trim().length > 0;
  }

  function backendUnavailableHint(url: string): string {
    return isLocalServer(url)
      ? `Local backend is not running at ${url}. Start API service or set remote server URL in Settings.`
      : `Unable to reach ${url}. Check server URL and network, then retry.`;
  }

  function getSetupIssue(): string {
    const url = normalizeServerUrl(serverUrl);
    if (!url) {
      return 'Set a Server URL in Settings before loading files and peers.';
    }
    if (healthChecked && isLocalServer(url) && !isConnected) {
      return backendUnavailableHint(url);
    }
    if (requiresAccountMode(url) && !hasAccountCredentials()) {
      return 'Remote mode requires Google sign-in (UID + Firebase ID token). Sign in, then save settings.';
    }
    return '';
  }

  function isConnectionRefused(raw: string): boolean {
    return /connection refused|tcp connect error|os error 111/i.test(raw);
  }

  function firebaseErrorCode(err: unknown): string {
    if (typeof err === 'object' && err !== null && 'code' in err) {
      const code = (err as { code?: unknown }).code;
      if (typeof code === 'string') {
        return code;
      }
    }
    return '';
  }

  function sanitizeFileName(fileName: string): string {
    return fileName.replace(/[\\/:*?"<>|]/g, '_');
  }

  function joinPath(base: string, leaf: string): string {
    const normalizedBase = base.replace(/[\\/]+$/, '');
    const separator = normalizedBase.includes('\\') ? '\\' : '/';
    return `${normalizedBase}${separator}${leaf}`;
  }

  function decodeBase64Url(input: string): string {
    const normalized = input.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  }

  async function loadGoogleScript() {
    if ((window as unknown as { google?: GoogleApi }).google?.accounts?.id) {
      return;
    }
    if (googleScriptPromise) {
      await googleScriptPromise;
      return;
    }

    googleScriptPromise = new Promise<void>((resolve, reject) => {
      const existingScript = document.querySelector(
        'script[data-google-gsi="1"]',
      ) as HTMLScriptElement | null;
      if (existingScript) {
        existingScript.addEventListener('load', () => resolve(), { once: true });
        existingScript.addEventListener('error', () => reject(new Error('Google script failed to load')), {
          once: true,
        });
        return;
      }

      const script = document.createElement('script');
      script.src = 'https://accounts.google.com/gsi/client';
      script.async = true;
      script.defer = true;
      script.dataset.googleGsi = '1';
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('Google script failed to load'));
      document.head.appendChild(script);
    });

    await googleScriptPromise;
  }

  async function requestGoogleCredential(): Promise<string> {
    await loadGoogleScript();
    const googleApi = (window as unknown as { google?: GoogleApi }).google;
    if (!googleApi?.accounts?.id) {
      throw new Error('Google Identity Services is unavailable');
    }

    return new Promise((resolve, reject) => {
      let settled = false;
      googleApi.accounts?.id?.initialize({
        client_id: GOOGLE_WEB_CLIENT_ID,
        auto_select: false,
        cancel_on_tap_outside: true,
        callback: (response) => {
          if (settled) return;
          settled = true;
          if (!response.credential) {
            reject(new Error('Google sign-in did not return a credential'));
            return;
          }
          resolve(response.credential);
        },
      });

      googleApi.accounts?.id?.prompt((notification) => {
        if (settled) return;
        if (notification?.isNotDisplayed?.() || notification?.isSkippedMoment?.()) {
          settled = true;
          reject(new Error('Google prompt unavailable. Ensure network and client ID are valid.'));
        }
      });
    });
  }

  async function exchangeGoogleCredentialForFirebase(googleIdToken: string) {
    const response = await fetch(
      `https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key=${FIREBASE_WEB_API_KEY}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          postBody: `id_token=${encodeURIComponent(googleIdToken)}&providerId=google.com`,
          requestUri: 'http://localhost',
          returnSecureToken: true,
          returnIdpCredential: true,
        }),
      },
    );

    const payload = (await response.json()) as Record<string, unknown>;
    if (!response.ok) {
      throw new Error(String(payload.error ?? 'Firebase token exchange failed'));
    }

    const idToken = typeof payload.idToken === 'string' ? payload.idToken : '';
    const localId = typeof payload.localId === 'string' ? payload.localId : '';
    if (!idToken || !localId) {
      throw new Error('Firebase response did not include idToken/localId');
    }
    return { idToken, localId };
  }

  async function signInWithGoogleDesktop() {
    if (authInProgress) {
      error = 'Google sign-in is already in progress. Please complete the existing popup first.';
      return;
    }
    clearMessages();
    authInProgress = true;
    try {
      const popupResult = await signInWithPopup(firebaseAuth, googleProvider);
      authToken = await popupResult.user.getIdToken(true);
      accountId = popupResult.user.uid;
      await saveDesktopSettings();
      currentPage = 'files';
      success = 'Google sign-in complete. Account mode is active.';
    } catch (popupError) {
      const popupCode = firebaseErrorCode(popupError);
      if (popupCode === 'auth/cancelled-popup-request') {
        error = 'Google sign-in was triggered more than once. Please retry once and wait for the popup to finish.';
        return;
      }
      if (popupCode === 'auth/popup-closed-by-user') {
        error = 'Google sign-in was canceled before completion.';
        return;
      }
      try {
        const googleCredential = await requestGoogleCredential();
        const firebaseSession = await exchangeGoogleCredentialForFirebase(googleCredential);
        authToken = firebaseSession.idToken;
        accountId = firebaseSession.localId;
        await saveDesktopSettings();
        currentPage = 'files';
        success = 'Google sign-in complete. Account mode is active.';
      } catch (fallbackError) {
        error =
          'Google sign-in is unavailable in this desktop session. Use a valid Firebase ID token in Settings and save, or retry popup sign-in once.';
      }
    } finally {
      authInProgress = false;
    }
  }

  async function signOutDesktop() {
    clearMessages();
    try {
      await signOut(firebaseAuth);
      accountId = '';
      authToken = '';
      success = 'Signed out from Google account';
    } catch (err) {
      error = `Sign out failed: ${formatError(err)}`;
    }
  }

  function syncUiFromSettings(next: DesktopSettings) {
    serverUrl = next.server_url;
    nodeRole = next.node_role;
    storageQuotaGb = next.storage_quota_gb;
    darkMode = next.dark_mode;
    accountId = next.account_id ?? '';
    authToken = next.auth_bearer_token ?? '';
  }

  async function loadSettings() {
    try {
      const fetched = await invoke<DesktopSettings>('get_settings');
      settings = fetched;
      syncUiFromSettings(fetched);
    } catch (err) {
      error = `Failed to load settings: ${formatError(err)}`;
    }
  }

  async function refreshHealth() {
    const url = normalizeServerUrl(serverUrl);
    if (!url) {
      isConnected = false;
      healthChecked = true;
      return;
    }
    try {
      await invoke('get_health');
      isConnected = true;
      healthChecked = true;
    } catch (err) {
      const raw = formatError(err);
      isConnected = false;
      healthChecked = true;
      if (isConnectionRefused(raw)) {
        error = backendUnavailableHint(url);
        return;
      }
      error = `Backend is offline: ${raw}`;
    }
  }

  async function loadFiles() {
    if (setupIssue) {
      files = [];
      return;
    }
    if (!isConnected) {
      files = [];
      error = backendUnavailableHint(normalizeServerUrl(serverUrl));
      return;
    }
    loadingFiles = true;
    try {
      files = await invoke<FileInfo[]>('list_files');
    } catch (err) {
      const raw = formatError(err);
      if (isConnectionRefused(raw)) {
        error = backendUnavailableHint(normalizeServerUrl(serverUrl));
      } else if (/404|not found/i.test(raw) && requiresAccountMode(normalizeServerUrl(serverUrl))) {
        error = 'Failed to load files: remote mode requires account sign-in and saved credentials in Settings.';
      } else {
        error = `Failed to load files: ${raw}`;
      }
    } finally {
      loadingFiles = false;
    }
  }

  async function loadPeers() {
    if (setupIssue) {
      peers = [];
      return;
    }
    if (!isConnected) {
      peers = [];
      error = backendUnavailableHint(normalizeServerUrl(serverUrl));
      return;
    }
    loadingPeers = true;
    try {
      const [peerList, stats] = await Promise.all([
        invoke<PeerInfo[]>('get_discovered_peers'),
        invoke<Record<string, unknown>>('get_network_stats'),
      ]);
      peers = peerList;
      networkStats = stats;
    } catch (err) {
      const raw = formatError(err);
      if (isConnectionRefused(raw)) {
        error = backendUnavailableHint(normalizeServerUrl(serverUrl));
      } else {
        error = `Failed to load peers: ${raw}`;
      }
    } finally {
      loadingPeers = false;
    }
  }

  async function refreshCurrentPage() {
    clearMessages();
    if (setupIssue) {
      currentPage = 'settings';
      error = setupIssue;
      return;
    }
    await refreshHealth();
    if (!isConnected) {
      return;
    }
    if (currentPage === 'files') {
      await loadFiles();
      return;
    }
    if (currentPage === 'network') {
      await loadPeers();
      return;
    }
  }

  async function uploadFile() {
    clearMessages();
    try {
      if (!isConnected) {
        await refreshHealth();
        if (!isConnected) {
          error = backendUnavailableHint(normalizeServerUrl(serverUrl));
          return;
        }
      }
      const selected = await open({
        multiple: false,
        directory: false,
      });
      if (!selected) return;

      uploading = true;
      const fileName = selected.split(/[/\\]/).pop() || 'file';
      await invoke<string>('upload_file', {
        filePath: selected,
        fileName,
      });
      success = 'Upload complete';
      await loadFiles();
    } catch (err) {
      error = `Upload failed: ${formatError(err)}`;
    } finally {
      uploading = false;
    }
  }

  async function resolveDefaultDownloadPath(fileName: string): Promise<string> {
    const downloadsDir = await invoke<string>('get_downloads_directory');
    return joinPath(downloadsDir, sanitizeFileName(fileName));
  }

  async function openFilePath(path: string) {
    await invoke('open_file_path', { path });
  }

  async function extractUidFromToken() {
    clearMessages();
    try {
      const token = authToken.trim();
      if (!token) {
        throw new Error('Token is empty');
      }
      const segments = token.split('.');
      if (segments.length < 2) {
        throw new Error('Token format is invalid');
      }
      const payloadJson = decodeBase64Url(segments[1]);
      const payload = JSON.parse(payloadJson) as Record<string, unknown>;
      const uid =
        typeof payload.user_id === 'string'
          ? payload.user_id
          : typeof payload.sub === 'string'
            ? payload.sub
            : '';
      if (!uid) {
        throw new Error('Token payload does not contain user_id/sub');
      }
      accountId = uid;
      success = 'UID extracted from token';
    } catch (err) {
      error = `Failed to decode token: ${formatError(err)}`;
    }
  }

  async function downloadFile(fileId: string, fileName: string, mode: 'saveAs' | 'downloads' = 'saveAs') {
    clearMessages();
    try {
      if (!isConnected) {
        await refreshHealth();
        if (!isConnected) {
          error = backendUnavailableHint(normalizeServerUrl(serverUrl));
          return;
        }
      }
      downloadInProgress = true;
      const savePath =
        mode === 'saveAs'
          ? await save({ defaultPath: fileName })
          : await resolveDefaultDownloadPath(fileName);
      if (!savePath) {
        downloadInProgress = false;
        return;
      }
      await invoke('download_file', {
        fileId,
        savePath,
      });
      downloadedPaths = {
        ...downloadedPaths,
        [fileId]: savePath,
      };
      success = `Downloaded ${fileName} to ${savePath}`;
    } catch (err) {
      error = `Download failed: ${formatError(err)}`;
    } finally {
      downloadInProgress = false;
    }
  }

  async function openDownloadedFile(fileId: string, fileName: string) {
    clearMessages();
    try {
      const existingPath = downloadedPaths[fileId];
      if (existingPath) {
        await openFilePath(existingPath);
        success = `Opened ${fileName}`;
        return;
      }

      const defaultPath = await resolveDefaultDownloadPath(fileName);
      await openFilePath(defaultPath);
      downloadedPaths = {
        ...downloadedPaths,
        [fileId]: defaultPath,
      };
      success = `Opened ${fileName}`;
    } catch (err) {
      error = `Unable to open file locally. Download it first. (${formatError(err)})`;
    }
  }

  async function deleteFile(fileId: string) {
    clearMessages();
    if (!confirm('Delete this file?')) return;
    try {
      if (!isConnected) {
        await refreshHealth();
        if (!isConnected) {
          error = backendUnavailableHint(normalizeServerUrl(serverUrl));
          return;
        }
      }
      await invoke('delete_file', { fileId });
      success = 'File deleted';
      await loadFiles();
    } catch (err) {
      error = `Delete failed: ${formatError(err)}`;
    }
  }

  async function saveDesktopSettings() {
    if (!settings) return;
    clearMessages();
    savingSettings = true;
    const nextSettings: DesktopSettings = {
      ...settings,
      server_url: serverUrl.trim(),
      node_role: nodeRole,
      storage_quota_gb: storageQuotaGb,
      dark_mode: darkMode,
      account_id: accountId.trim() || null,
      auth_bearer_token: authToken.trim() || null,
    };
    try {
      await invoke('save_settings', { settings: nextSettings });
      settings = nextSettings;
      success = 'Settings saved';
      await refreshHealth();
      if (!getSetupIssue() && isConnected) {
        await Promise.all([loadFiles(), loadPeers()]);
      } else {
        currentPage = 'settings';
      }
    } catch (err) {
      error = `Failed to save settings: ${formatError(err)}`;
    } finally {
      savingSettings = false;
    }
  }

  async function applyNodeRole() {
    clearMessages();
    applyingRole = true;
    try {
      if (!isConnected) {
        await refreshHealth();
        if (!isConnected) {
          error = backendUnavailableHint(normalizeServerUrl(serverUrl));
          return;
        }
      }
      await invoke('set_node_role', {
        role: nodeRole,
        storageGb: nodeRole === 'storage' ? storageQuotaGb : null,
      });
      if (nodeRole === 'storage') {
        await invoke('set_storage_quota', { quotaGb: storageQuotaGb });
      }
      success = 'Node role applied';
      await loadPeers();
    } catch (err) {
      error = `Failed to apply role: ${formatError(err)}`;
    } finally {
      applyingRole = false;
    }
  }

  function formatSize(bytes: number): string {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = bytes;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit += 1;
    }
    return `${size.toFixed(1)} ${units[unit]}`;
  }

  function formatDate(iso: string): string {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleString();
  }
</script>

<div class="app" data-theme={darkMode ? 'dark' : 'light'}>
  <aside class="sidebar">
    <div class="logo">
      <span class="logo-icon" aria-hidden="true"><Cloud size={20} strokeWidth={2} /></span>
      <span class="logo-text">FireCloud</span>
    </div>

    <nav class="nav">
      {#each pages as page}
        <button class="nav-item" class:active={currentPage === page.id} on:click={() => (currentPage = page.id)}>
          <span class="nav-icon" aria-hidden="true">
            {#if page.id === 'files'}
              <FolderOpen size={18} strokeWidth={2} />
            {:else if page.id === 'network'}
              <Network size={18} strokeWidth={2} />
            {:else}
              <Settings2 size={18} strokeWidth={2} />
            {/if}
          </span>
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
        {#if nodeRole === 'storage'}
          <HardDrive size={13} strokeWidth={2} /> Provider
        {:else}
          <Smartphone size={13} strokeWidth={2} /> Consumer
        {/if}
      </div>
      {#if accountModeEnabled}
        <div class="badge badge-info">Account mode</div>
      {/if}
    </div>
  </aside>

  <main class="main">
    <header class="page-header">
      <h1>{currentPage === 'files' ? 'Files' : currentPage === 'network' ? 'Network' : 'Settings'}</h1>
      <button class="btn btn-secondary" on:click={refreshCurrentPage}>
        <RefreshCw size={14} strokeWidth={2} /> Refresh
      </button>
    </header>

    {#if error}
      <div class="error-banner">{error}</div>
    {/if}
    {#if success}
      <div class="success-banner">{success}</div>
    {/if}

    {#if setupIssue}
      <section class="card setup-card">
        <div class="section-header">Setup Required</div>
        <p class="hint">{setupIssue}</p>
        <div class="file-actions">
          <button class="btn btn-secondary" on:click={() => (currentPage = 'settings')}>Open Settings</button>
          {#if !accountModeEnabled}
            <button class="btn btn-primary" on:click={signInWithGoogleDesktop} disabled={authInProgress}>
              {authInProgress ? 'Signing in...' : 'Sign In with Google'}
            </button>
          {/if}
        </div>
      </section>
    {/if}

    {#if currentPage === 'files'}
      <div class="panel-header">
        <div class="panel-subtitle">
          {#if accountModeEnabled}
            Account-scoped files (same Google account across nodes)
          {:else}
            Local backend files
          {/if}
        </div>
        <button class="btn btn-primary" on:click={uploadFile} disabled={uploading}>
          <Upload size={14} strokeWidth={2} /> {uploading ? 'Uploading...' : 'Upload File'}
        </button>
      </div>

      {#if setupIssue}
        <div class="empty-state">
          <div class="empty-icon" aria-hidden="true"><Wrench size={48} strokeWidth={1.9} /></div>
          <h2>Finish setup to load files</h2>
          <p>{setupIssue}</p>
        </div>
      {:else if loadingFiles}

        <div class="loading">Loading files...</div>
      {:else if files.length === 0}
        <div class="empty-state">
          <div class="empty-icon" aria-hidden="true"><FolderOpen size={48} strokeWidth={1.9} /></div>
          <h2>No files yet</h2>
          <p>Upload files from any node on the same account to see them here.</p>
        </div>
      {:else}
        <div class="file-list">
          {#each files as file}
            <div class="file-item card">
              <div class="file-icon" aria-hidden="true"><FileText size={20} strokeWidth={2} /></div>
              <div class="file-info">
                <div class="file-name">{file.file_name}</div>
                <div class="file-meta">{formatSize(file.file_size)} • {formatDate(file.created_at)}</div>
              </div>
              <div class="file-actions">
                <button class="btn btn-secondary" on:click={() => downloadFile(file.file_id, file.file_name)}>
                  Save As
                </button>
                <button
                  class="btn btn-secondary"
                  on:click={() => downloadFile(file.file_id, file.file_name, 'downloads')}
                  disabled={downloadInProgress}
                >
                  <Download size={14} strokeWidth={2} /> {downloadInProgress ? 'Downloading...' : 'Downloads'}
                </button>
                <button class="btn btn-secondary" on:click={() => openDownloadedFile(file.file_id, file.file_name)}>
                  <ExternalLink size={14} strokeWidth={2} /> Open
                </button>
                <button class="btn btn-danger" on:click={() => deleteFile(file.file_id)} aria-label="Delete file">
                  <Trash2 size={14} strokeWidth={2} />
                </button>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    {:else if currentPage === 'network'}
      <div class="stats-grid">
        <div class="card">
          <div class="section-header">Peers</div>
          <div class="stat-value">{peers.length}</div>
        </div>
        <div class="card">
          <div class="section-header">Providers</div>
          <div class="stat-value">{peers.filter((p) => p.node_type === 'storage_provider').length}</div>
        </div>
        <div class="card">
          <div class="section-header">Available</div>
          <div class="stat-value">{formatSize(Number(networkStats.total_available_storage ?? 0))}</div>
        </div>
      </div>

        {#if setupIssue}
          <div class="empty-state">
            <div class="empty-icon" aria-hidden="true"><Wrench size={48} strokeWidth={1.9} /></div>
            <h2>Finish setup to view network peers</h2>
            <p>{setupIssue}</p>
          </div>
        {:else if loadingPeers}
        <div class="loading">Loading peers...</div>
      {:else if peers.length === 0}
        <div class="empty-state">
          <div class="empty-icon" aria-hidden="true"><Globe size={48} strokeWidth={1.9} /></div>
          <h2>No peers discovered</h2>
          <p>Ensure peers are online and signed into the same account.</p>
        </div>
      {:else}
        <div class="file-list">
          {#each peers as peer}
            <div class="file-item card">
              <div class="file-icon" aria-hidden="true">
                {#if peer.node_type === 'storage_provider'}
                  <HardDrive size={20} strokeWidth={2} />
                {:else}
                  <Smartphone size={20} strokeWidth={2} />
                {/if}
              </div>
              <div class="file-info">
                <div class="file-name">{peer.device_id}</div>
                <div class="file-meta">
                  {peer.ip_address}:{peer.port} •
                  {peer.node_type === 'storage_provider' ? ` ${formatSize(peer.available_storage)} free` : ' Consumer'}
                </div>
              </div>
              <div>
                <span class="badge {peer.is_online ? 'badge-success' : 'badge-error'}">
                  {peer.is_online ? 'Online' : 'Offline'}
                </span>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    {:else}
      <div class="settings-grid">
        <section class="card">
          <div class="section-header">Connection</div>
          <label class="label" for="server-url">Server URL</label>
          <input id="server-url" class="input" bind:value={serverUrl} placeholder="https://signal.firecloud.app" />
          <p class="hint">Use relay/signaling base URL for account-scoped cross-node downloads.</p>
        </section>

        <section class="card">
          <div class="section-header">Account</div>
          <div class="file-actions">
            <button class="btn btn-primary" on:click={signInWithGoogleDesktop} disabled={authInProgress}>
              {authInProgress ? 'Signing in...' : 'Sign In with Google'}
            </button>
            <button class="btn btn-secondary" on:click={signOutDesktop} disabled={!accountModeEnabled}>Sign Out</button>
          </div>
          <label class="label" for="account-id">Google Account UID</label>
          <input id="account-id" class="input" bind:value={accountId} placeholder="firebase uid" />
          <label class="label" for="auth-token">Firebase ID Token</label>
          <input id="auth-token" class="input" bind:value={authToken} placeholder="Bearer token" />
          <button class="btn btn-secondary" on:click={extractUidFromToken}>Use UID from Token</button>
          <p class="hint">When account mode is enabled, files are loaded by account so another node with the same account can be downloaded here.</p>
          <p class="hint">Desktop uses Firebase ID token + UID for account-scoped mode. Use the same Google account as mobile so files are shared across devices.</p>
        </section>

        <section class="card">
          <div class="section-header">Node Role</div>
          <label class="label" for="node-role">Role</label>
          <select id="node-role" class="input" bind:value={nodeRole}>
            <option value="consumer">Consumer</option>
            <option value="storage">Storage Provider</option>
          </select>
          <label class="label" for="quota">Storage quota (GB)</label>
          <input id="quota" class="input" type="number" min="1" bind:value={storageQuotaGb} />
          <button class="btn btn-secondary" on:click={applyNodeRole} disabled={applyingRole}>
            {applyingRole ? 'Applying...' : 'Apply Node Role'}
          </button>
        </section>

        <section class="card">
          <div class="section-header">Appearance</div>
          <label class="toggle">
            <input type="checkbox" bind:checked={darkMode} />
            <span>Dark mode</span>
          </label>
        </section>
      </div>

      <div class="settings-actions">
        <button class="btn btn-primary" on:click={saveDesktopSettings} disabled={savingSettings}>
          {savingSettings ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    {/if}
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
    display: inline-flex;
    color: var(--primary);
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
    display: inline-flex;
    align-items: center;
    justify-content: center;
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
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.75rem;
    color: var(--text-secondary);
  }

  .main {
    flex: 1;
    overflow-y: auto;
    padding: 2rem;
  }

  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
  }

  h1 {
    font-size: 1.5rem;
    font-weight: 600;
  }

  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
  }

  .panel-subtitle {
    color: var(--text-secondary);
    font-size: 0.875rem;
  }

  .error-banner {
    background: color-mix(in srgb, var(--error) 12%, transparent);
    color: var(--error);
    padding: 0.75rem 1rem;
    border-radius: var(--radius);
    margin-bottom: 1rem;
  }

  .success-banner {
    background: color-mix(in srgb, var(--success) 14%, transparent);
    color: var(--success);
    padding: 0.75rem 1rem;
    border-radius: var(--radius);
    margin-bottom: 1rem;
  }

  .setup-card {
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
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 1rem;
    color: var(--text-secondary);
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
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
    min-width: 2rem;
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

  .stats-grid,
  .settings-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 1rem;
  }

  .stat-value {
    font-size: 1.5rem;
    font-weight: 700;
  }

  .hint {
    margin-top: 0.5rem;
    color: var(--text-secondary);
    font-size: 0.8rem;
    line-height: 1.4;
  }

  .toggle {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    color: var(--text);
  }

  .settings-actions {
    margin-top: 1rem;
    display: flex;
    justify-content: flex-end;
  }
</style>
