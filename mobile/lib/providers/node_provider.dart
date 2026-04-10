import 'dart:async';
import 'dart:developer' as developer;
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../node/device_identity.dart';
import '../node/firecloud_node.dart';
import '../node/node_role.dart';
import '../p2p/peer_discovery.dart';
import '../p2p/signaling_client.dart';
import '../providers/auth_provider.dart' show authProvider;
import '../providers/audit_ledger_provider.dart';
import '../services/background_node_service.dart';
import '../services/audit_ledger_service.dart';
import '../services/manifest_sync_service.dart';
import '../storage/chunking.dart';

const _keyBackgroundModeEnabled = 'background_mode_enabled';
const _keySignalingServerUrl = 'signaling_server_url';
const _keyRelayBaseUrl = 'relay_base_url';

Future<void> _appendAuditLog(
  Ref ref, {
  required String action,
  required AuditLogStatus status,
  required String message,
  Map<String, Object?> details = const {},
}) async {
  try {
    final service = await ref.read(auditLedgerServiceProvider.future);
    await service.appendAuditLog(
      action: action,
      status: status,
      message: message,
      details: details,
    );
    ref.invalidate(auditLogsProvider);
  } catch (e) {
    developer.log('Failed to persist audit log: $e', name: 'firecloud.audit');
  }
}

String _normalizeUrl(String value, {required bool allowEmpty}) {
  var normalized = value.trim();
  if (normalized.isEmpty) {
    if (allowEmpty) return '';
    throw ArgumentError('URL cannot be empty');
  }
  if (!normalized.startsWith('http://') && !normalized.startsWith('https://')) {
    normalized = 'https://$normalized';
  }
  final parsed = Uri.tryParse(normalized);
  if (parsed == null ||
      parsed.host.isEmpty ||
      (parsed.scheme != 'http' && parsed.scheme != 'https')) {
    throw ArgumentError('Invalid URL: $value');
  }
  if (normalized.endsWith('/')) {
    normalized = normalized.substring(0, normalized.length - 1);
  }
  return normalized;
}

/// Provider for device identity (singleton).
final deviceIdentityProvider = FutureProvider<DeviceIdentity>((ref) async {
  final identity = DeviceIdentity();
  await identity.initialize();
  return identity;
});

/// Provider for node role manager.
final nodeRoleProvider = FutureProvider<NodeRoleManager>((ref) async {
  final roleManager = NodeRoleManager();
  await roleManager.load();
  return roleManager;
});

/// Provider for the main FireCloud P2P node.
final fireCloudNodeProvider = FutureProvider<FireCloudNode>((ref) async {
  final identity = await ref.watch(deviceIdentityProvider.future);
  final roleManager = await ref.watch(nodeRoleProvider.future);
  final authState = ref.watch(authProvider);
  final authNotifier = ref.read(authProvider.notifier);
  final prefs = await SharedPreferences.getInstance();
  final signalingServerUrl =
      prefs.getString(_keySignalingServerUrl) ??
      SignalingClient.defaultServerUrl;
  final relayBaseUrl =
      prefs.getString(_keyRelayBaseUrl) ?? SignalingClient.defaultRelayBaseUrl;

  final node = FireCloudNode(
    identity: identity,
    roleManager: roleManager,
    accountId: authState.isAuthenticated ? authState.userId : null,
    signalingServerUrl: signalingServerUrl,
    relayBaseUrl: relayBaseUrl,
    authTokenProvider: () => authNotifier.getIdToken(),
  );

  await node.initialize();
  await node.start();

  final backgroundModeEnabled =
      prefs.getBool(_keyBackgroundModeEnabled) ?? true;
  await BackgroundNodeService.syncWithRole(
    role: roleManager.role,
    isStorageLocked: roleManager.storageQuotaBytes > 0 && backgroundModeEnabled,
  );

  ref.onDispose(() {
    node.stop();
  });

  return node;
});

/// Provider for peer list (updated every 5 seconds).
final peersProvider = StreamProvider<List<PeerInfo>>((ref) async* {
  final node = await ref.watch(fireCloudNodeProvider.future);

  // Emit initial peers
  yield node.peers;

  // Subscribe to peer updates
  yield* node.peerDiscovery.peerStream;
});

/// Snapshot of network storage capacity from currently online providers.
class NetworkCapacityState {
  final int providerCount;
  final int totalAvailableBytes;

  const NetworkCapacityState({
    required this.providerCount,
    required this.totalAvailableBytes,
  });

  bool get hasProviders => providerCount > 0;
}

final networkCapacityProvider = StreamProvider<NetworkCapacityState>((
  ref,
) async* {
  final node = await ref.watch(fireCloudNodeProvider.future);

  NetworkCapacityState snapshot(List<PeerInfo> peers) {
    final remoteProviders = peers
        .where(
          (peer) =>
              peer.isStorageProvider &&
              peer.isOnline &&
              peer.availableStorageBytes > 0,
        )
        .toList();
    final remoteAvailableBytes = remoteProviders.fold<int>(
      0,
      (total, peer) => total + peer.availableStorageBytes,
    );
    final localAvailableBytes = node.roleManager.isStorageProvider
        ? node.roleManager.availableStorageBytes
        : 0;
    final localProviderCount = localAvailableBytes > 0 ? 1 : 0;
    return NetworkCapacityState(
      providerCount: remoteProviders.length + localProviderCount,
      totalAvailableBytes: remoteAvailableBytes + localAvailableBytes,
    );
  }

  yield snapshot(node.peers);
  yield* node.peerDiscovery.peerStream.map(snapshot);
});

/// Provider for manifest sync service.
final manifestSyncProvider = FutureProvider<ManifestSyncService>((ref) async {
  final node = await ref.watch(fireCloudNodeProvider.future);
  final authState = ref.watch(authProvider);
  final authNotifier = ref.read(authProvider.notifier);

  return ManifestSyncService(
    localStorage: node.localStorage,
    peerDiscovery: node.peerDiscovery,
    currentOwnerId: authState.isAuthenticated ? authState.userId : null,
    signalingServerUrl: node.signalingServerUrl,
    authTokenProvider: () => authNotifier.getIdToken(),
  );
});

/// Provider for file list (includes local + synced from other devices).
final filesProvider = FutureProvider<List<FileManifest>>((ref) async {
  // Watch fireCloudNodeProvider to ensure node is initialized
  await ref.watch(fireCloudNodeProvider.future);
  final syncService = await ref.watch(manifestSyncProvider.future);

  // Restore offline cache first, then refresh from network.
  await syncService.restoreFromLocalCache();
  // Trigger background sync from peers
  await syncService.syncFromPeers();

  // Return merged list (local + remote)
  return await syncService.getAllManifests();
});

/// State for node configuration.
class NodeConfigState {
  final NodeRole role;
  final int storageQuotaGB;
  final bool isRunning;
  final bool isBackgroundServiceRunning;
  final bool backgroundModeEnabled;
  final String signalingServerUrl;
  final String relayBaseUrl;
  final String deviceId;
  final int peerCount;
  final int usedStorageMB;

  NodeConfigState({
    required this.role,
    required this.storageQuotaGB,
    required this.isRunning,
    required this.isBackgroundServiceRunning,
    required this.backgroundModeEnabled,
    required this.signalingServerUrl,
    required this.relayBaseUrl,
    required this.deviceId,
    required this.peerCount,
    required this.usedStorageMB,
  });

  NodeConfigState copyWith({
    NodeRole? role,
    int? storageQuotaGB,
    bool? isRunning,
    bool? isBackgroundServiceRunning,
    bool? backgroundModeEnabled,
    String? signalingServerUrl,
    String? relayBaseUrl,
    String? deviceId,
    int? peerCount,
    int? usedStorageMB,
  }) {
    return NodeConfigState(
      role: role ?? this.role,
      storageQuotaGB: storageQuotaGB ?? this.storageQuotaGB,
      isRunning: isRunning ?? this.isRunning,
      isBackgroundServiceRunning:
          isBackgroundServiceRunning ?? this.isBackgroundServiceRunning,
      backgroundModeEnabled:
          backgroundModeEnabled ?? this.backgroundModeEnabled,
      signalingServerUrl: signalingServerUrl ?? this.signalingServerUrl,
      relayBaseUrl: relayBaseUrl ?? this.relayBaseUrl,
      deviceId: deviceId ?? this.deviceId,
      peerCount: peerCount ?? this.peerCount,
      usedStorageMB: usedStorageMB ?? this.usedStorageMB,
    );
  }
}

/// Notifier for node configuration.
class NodeConfigNotifier extends AsyncNotifier<NodeConfigState> {
  @override
  Future<NodeConfigState> build() async {
    final node = await ref.watch(fireCloudNodeProvider.future);
    final roleManager = await ref.watch(nodeRoleProvider.future);
    final prefs = await SharedPreferences.getInstance();
    final backgroundModeEnabled =
        prefs.getBool(_keyBackgroundModeEnabled) ?? true;
    final signalingServerUrl =
        prefs.getString(_keySignalingServerUrl) ??
        SignalingClient.defaultServerUrl;
    final relayBaseUrl =
        prefs.getString(_keyRelayBaseUrl) ??
        SignalingClient.defaultRelayBaseUrl;
    await BackgroundNodeService.syncWithRole(
      role: roleManager.role,
      isStorageLocked:
          roleManager.storageQuotaBytes > 0 && backgroundModeEnabled,
    );
    final isBackgroundServiceRunning = await BackgroundNodeService.isRunning();

    return NodeConfigState(
      role: roleManager.role,
      storageQuotaGB: roleManager.storageQuotaBytes ~/ (1024 * 1024 * 1024),
      isRunning: node.isRunning,
      isBackgroundServiceRunning: isBackgroundServiceRunning,
      backgroundModeEnabled: backgroundModeEnabled,
      signalingServerUrl: signalingServerUrl,
      relayBaseUrl: relayBaseUrl,
      deviceId: node.identity.deviceId,
      peerCount: node.peers.length,
      usedStorageMB: roleManager.usedStorageBytes ~/ (1024 * 1024),
    );
  }

  /// Change node role.
  Future<void> setRole(NodeRole role) async {
    try {
      final node = await ref.read(fireCloudNodeProvider.future);
      if (role == NodeRole.consumer) {
        await node.reconcileStorageState(purgeOrphans: true);
      }
      final roleManager = await ref.read(nodeRoleProvider.future);
      await roleManager.setRole(role);
      if (role == NodeRole.consumer) {
        await roleManager.setStorageQuota(0);
      }

      final prefs = await SharedPreferences.getInstance();
      final backgroundModeEnabled =
          prefs.getBool(_keyBackgroundModeEnabled) ?? true;

      await BackgroundNodeService.syncWithRole(
        role: roleManager.role,
        isStorageLocked:
            roleManager.storageQuotaBytes > 0 && backgroundModeEnabled,
      );
      await node.announcePresence();
      ref.invalidateSelf();
      ref.invalidate(peersProvider);
      ref.invalidate(networkCapacityProvider);
      await _appendAuditLog(
        ref,
        action: 'role_update',
        status: AuditLogStatus.success,
        message: 'Role updated to ${role.name}',
        details: {'role': role.name},
      );
    } catch (e) {
      await _appendAuditLog(
        ref,
        action: 'role_update',
        status: AuditLogStatus.failure,
        message: 'Failed to update role: $e',
        details: {'target_role': role.name},
      );
      rethrow;
    }
  }

  /// Set storage quota (in GB).
  Future<void> setStorageQuota(int quotaGB) async {
    try {
      final roleManager = await ref.read(nodeRoleProvider.future);
      await roleManager.setStorageQuota(quotaGB * 1024 * 1024 * 1024);

      final prefs = await SharedPreferences.getInstance();
      final backgroundModeEnabled =
          prefs.getBool(_keyBackgroundModeEnabled) ?? true;

      await BackgroundNodeService.syncWithRole(
        role: roleManager.role,
        isStorageLocked:
            roleManager.storageQuotaBytes > 0 && backgroundModeEnabled,
      );
      final node = await ref.read(fireCloudNodeProvider.future);
      await node.announcePresence();
      ref.invalidateSelf();
      ref.invalidate(peersProvider);
      ref.invalidate(networkCapacityProvider);
      await _appendAuditLog(
        ref,
        action: 'storage_quota_update',
        status: AuditLogStatus.success,
        message: 'Storage quota set to ${quotaGB}GB',
        details: {'quota_gb': quotaGB},
      );
    } catch (e) {
      await _appendAuditLog(
        ref,
        action: 'storage_quota_update',
        status: AuditLogStatus.failure,
        message: 'Failed to set storage quota: $e',
        details: {'quota_gb': quotaGB},
      );
      rethrow;
    }
  }

  /// Toggle background mode on/off.
  Future<void> setBackgroundModeEnabled(bool enabled) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(_keyBackgroundModeEnabled, enabled);

      final roleManager = await ref.read(nodeRoleProvider.future);
      await BackgroundNodeService.syncWithRole(
        role: roleManager.role,
        isStorageLocked: roleManager.storageQuotaBytes > 0 && enabled,
      );

      ref.invalidateSelf();
      await _appendAuditLog(
        ref,
        action: 'background_mode_toggle',
        status: AuditLogStatus.success,
        message: enabled
            ? 'Background mode enabled'
            : 'Background mode disabled',
        details: {'enabled': enabled},
      );
    } catch (e) {
      await _appendAuditLog(
        ref,
        action: 'background_mode_toggle',
        status: AuditLogStatus.failure,
        message: 'Failed to update background mode: $e',
        details: {'enabled': enabled},
      );
      rethrow;
    }
  }

  Future<void> setSignalingServerUrl(String value) async {
    final normalized = _normalizeUrl(value, allowEmpty: false);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_keySignalingServerUrl, normalized);
      ref.invalidate(fireCloudNodeProvider);
      ref.invalidate(peersProvider);
      ref.invalidate(networkCapacityProvider);
      ref.invalidate(filesProvider);
      ref.invalidateSelf();
      await _appendAuditLog(
        ref,
        action: 'signaling_server_update',
        status: AuditLogStatus.success,
        message: 'Signaling server updated',
        details: {'signaling_server_url': normalized},
      );
    } catch (e) {
      await _appendAuditLog(
        ref,
        action: 'signaling_server_update',
        status: AuditLogStatus.failure,
        message: 'Failed to update signaling server: $e',
        details: {'signaling_server_url': normalized},
      );
      rethrow;
    }
  }

  Future<void> setRelayBaseUrl(String value) async {
    final normalized = _normalizeUrl(value, allowEmpty: true);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_keyRelayBaseUrl, normalized);
      ref.invalidate(fireCloudNodeProvider);
      ref.invalidate(peersProvider);
      ref.invalidate(networkCapacityProvider);
      ref.invalidate(filesProvider);
      ref.invalidateSelf();
      await _appendAuditLog(
        ref,
        action: 'relay_base_update',
        status: AuditLogStatus.success,
        message: normalized.isEmpty
            ? 'Relay base URL cleared'
            : 'Relay base URL updated',
        details: {'relay_base_url': normalized},
      );
    } catch (e) {
      await _appendAuditLog(
        ref,
        action: 'relay_base_update',
        status: AuditLogStatus.failure,
        message: 'Failed to update relay base URL: $e',
        details: {'relay_base_url': normalized},
      );
      rethrow;
    }
  }

  Future<void> resetInternetDiscoveryDefaults() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _keySignalingServerUrl,
      SignalingClient.defaultServerUrl,
    );
    await prefs.setString(
      _keyRelayBaseUrl,
      SignalingClient.defaultRelayBaseUrl,
    );
    ref.invalidate(fireCloudNodeProvider);
    ref.invalidate(peersProvider);
    ref.invalidate(networkCapacityProvider);
    ref.invalidate(filesProvider);
    ref.invalidateSelf();
    await _appendAuditLog(
      ref,
      action: 'internet_discovery_reset',
      status: AuditLogStatus.success,
      message: 'Internet discovery endpoints reset to defaults',
      details: {
        'signaling_server_url': SignalingClient.defaultServerUrl,
        'relay_base_url': SignalingClient.defaultRelayBaseUrl,
      },
    );
  }
}

final nodeConfigProvider =
    AsyncNotifierProvider<NodeConfigNotifier, NodeConfigState>(
      NodeConfigNotifier.new,
    );

/// File upload/download actions.
class FileActionsNotifier extends AsyncNotifier<void> {
  @override
  Future<void> build() async {}

  /// Upload a file.
  Future<FileManifest> uploadFile(String fileName, Uint8List data) async {
    try {
      final authState = ref.read(authProvider);
      final ownerId = authState.isAuthenticated ? authState.userId : null;

      final node = await ref.read(fireCloudNodeProvider.future);
      final manifest = await node.uploadFile(fileName, data, ownerId: ownerId);
      final syncService = await ref.read(manifestSyncProvider.future);
      await syncService.publishManifest(manifest);
      await node.reconcileStorageState();
      await node.announcePresence();
      ref.invalidate(filesProvider);
      ref.invalidate(nodeConfigProvider);
      ref.invalidate(networkCapacityProvider);
      ref.invalidate(peersProvider);
      await _appendAuditLog(
        ref,
        action: 'file_upload',
        status: AuditLogStatus.success,
        message: 'Uploaded file "$fileName"',
        details: {'file_id': manifest.fileId, 'file_size': data.length},
      );
      return manifest;
    } catch (e) {
      await _appendAuditLog(
        ref,
        action: 'file_upload',
        status: AuditLogStatus.failure,
        message: 'Failed to upload "$fileName": $e',
        details: {'file_size': data.length},
      );
      rethrow;
    }
  }

  /// Download a file.
  Future<Uint8List> downloadFile(String fileId) async {
    try {
      final node = await ref.read(fireCloudNodeProvider.future);
      final bytes = await node.downloadFile(fileId);
      await _appendAuditLog(
        ref,
        action: 'file_download',
        status: AuditLogStatus.success,
        message: 'Downloaded file "$fileId"',
        details: {'file_id': fileId, 'file_size': bytes.length},
      );
      return bytes;
    } catch (e) {
      await _appendAuditLog(
        ref,
        action: 'file_download',
        status: AuditLogStatus.failure,
        message: 'Failed to download "$fileId": $e',
        details: {'file_id': fileId},
      );
      rethrow;
    }
  }

  /// Delete a file.
  Future<void> deleteFile(String fileId) async {
    try {
      final node = await ref.read(fireCloudNodeProvider.future);
      await node.deleteFile(fileId);
      final syncService = await ref.read(manifestSyncProvider.future);
      await syncService.deletePublishedManifest(fileId);
      await node.reconcileStorageState();
      ref.invalidate(nodeConfigProvider);
      ref.invalidate(networkCapacityProvider);
      ref.invalidate(filesProvider);
      await _appendAuditLog(
        ref,
        action: 'file_delete',
        status: AuditLogStatus.success,
        message: 'Deleted file "$fileId"',
        details: {'file_id': fileId},
      );
    } catch (e) {
      await _appendAuditLog(
        ref,
        action: 'file_delete',
        status: AuditLogStatus.failure,
        message: 'Failed to delete "$fileId": $e',
        details: {'file_id': fileId},
      );
      rethrow;
    }
  }
}

final fileActionsProvider = AsyncNotifierProvider<FileActionsNotifier, void>(
  FileActionsNotifier.new,
);
