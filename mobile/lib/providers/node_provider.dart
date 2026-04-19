import 'dart:async';
import 'dart:typed_data';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../node/device_identity.dart';
import '../node/firecloud_node.dart';
import '../node/node_role.dart';
import '../p2p/peer_discovery.dart';
import '../providers/auth_provider.dart' show authProvider;
import '../services/background_node_service.dart';
import '../services/manifest_sync_service.dart';
import '../storage/chunking.dart';
import '../storage/local_storage.dart' show P2PStorageUnavailableError;

const _backgroundModeEnabledKey = 'background_mode_enabled';

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

  final node = FireCloudNode(
    identity: identity,
    roleManager: roleManager,
    accountId: authState.isAuthenticated ? authState.userId : null,
    authTokenProvider: () async =>
        FirebaseAuth.instance.currentUser?.getIdToken(),
  );

  await node.initialize();
  await node.start();

  final prefs = await SharedPreferences.getInstance();
  final backgroundModeEnabled = prefs.getBool(_backgroundModeEnabledKey) ?? true;
  await BackgroundNodeService.syncWithRole(
    role: roleManager.role,
    isStorageLocked:
        roleManager.storageQuotaBytes > 0 && backgroundModeEnabled,
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

/// Trigger an immediate peer refresh across LAN + signaling relay.
final peerRefreshProvider = Provider<Future<void> Function()>((ref) {
  return () async {
    final node = await ref.read(fireCloudNodeProvider.future);
    for (var attempt = 0; attempt < 3; attempt++) {
      await node.refreshPeers();
      if (attempt < 2) {
        await Future<void>.delayed(const Duration(milliseconds: 350));
      }
    }
    ref.invalidate(peersProvider);
    ref.invalidate(networkCapacityProvider);
  };
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

  return ManifestSyncService(
    localStorage: node.localStorage,
    peerDiscovery: node.peerDiscovery,
    currentOwnerId: authState.isAuthenticated ? authState.userId : null,
    relayApiBaseUrl: node.signalingServerUrl,
    authTokenProvider: () async => FirebaseAuth.instance.currentUser?.getIdToken(),
  );
});

/// Provider for file list (includes local + synced from other devices).
final filesProvider = FutureProvider<List<FileManifest>>((ref) async {
  // Watch fireCloudNodeProvider to ensure node is initialized
  await ref.watch(fireCloudNodeProvider.future);
  final syncService = await ref.watch(manifestSyncProvider.future);

  // Restore offline cache first, then refresh from network.
  await syncService.restoreFromLocalCache();
  // Trigger background sync from relay + peers.
  await syncService.syncFromCloudAndPeers();

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
  final String deviceId;
  final int peerCount;
  final int usedStorageMB;

  NodeConfigState({
    required this.role,
    required this.storageQuotaGB,
    required this.isRunning,
    required this.isBackgroundServiceRunning,
    required this.backgroundModeEnabled,
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
      deviceId: deviceId ?? this.deviceId,
      peerCount: peerCount ?? this.peerCount,
      usedStorageMB: usedStorageMB ?? this.usedStorageMB,
    );
  }
}

/// Notifier for node configuration.
class NodeConfigNotifier extends AsyncNotifier<NodeConfigState> {
  static const _keyBackgroundModeEnabled = _backgroundModeEnabledKey;

  @override
  Future<NodeConfigState> build() async {
    final node = await ref.watch(fireCloudNodeProvider.future);
    final roleManager = await ref.watch(nodeRoleProvider.future);
    final isBackgroundServiceRunning = await BackgroundNodeService.isRunning();
    final prefs = await SharedPreferences.getInstance();
    final backgroundModeEnabled =
        prefs.getBool(_keyBackgroundModeEnabled) ?? true;

    return NodeConfigState(
      role: roleManager.role,
      storageQuotaGB: roleManager.storageQuotaBytes ~/ (1024 * 1024 * 1024),
      isRunning: node.isRunning,
      isBackgroundServiceRunning: isBackgroundServiceRunning,
      backgroundModeEnabled: backgroundModeEnabled,
      deviceId: node.identity.deviceId,
      peerCount: node.peers.length,
      usedStorageMB: roleManager.usedStorageBytes ~/ (1024 * 1024),
    );
  }

  /// Change node role.
  Future<void> setRole(NodeRole role) async {
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
  }

  /// Set storage quota (in GB).
  Future<void> setStorageQuota(int quotaGB) async {
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
  }

  /// Toggle background mode on/off.
  Future<void> setBackgroundModeEnabled(bool enabled) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_keyBackgroundModeEnabled, enabled);

    final roleManager = await ref.read(nodeRoleProvider.future);
    await BackgroundNodeService.syncWithRole(
      role: roleManager.role,
      isStorageLocked: roleManager.storageQuotaBytes > 0 && enabled,
    );

    ref.invalidateSelf();
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
    final capacity = await ref.read(networkCapacityProvider.future);
    if (capacity.providerCount == 0 || capacity.totalAvailableBytes <= 0) {
      throw P2PStorageUnavailableError(
        'No storage providers with available capacity are currently online',
      );
    }
    final requiredProviderBytes = FastCDC.chunk(
      data,
    ).fold<int>(0, (total, chunk) => total + chunk.size + 24);
    if (capacity.totalAvailableBytes < requiredProviderBytes) {
      throw P2PStorageUnavailableError(
        'Not enough provider capacity on network '
        '(available=${capacity.totalAvailableBytes} bytes, required=$requiredProviderBytes bytes)',
      );
    }
    // Get owner ID from authenticated user for cross-device visibility
    final authState = ref.read(authProvider);
    final ownerId = authState.isAuthenticated ? authState.userId : null;

    final node = await ref.read(fireCloudNodeProvider.future);
    final manifest = await node.uploadFile(fileName, data, ownerId: ownerId);
    final syncService = await ref.read(manifestSyncProvider.future);
    try {
      await syncService.publishManifestEnvelope(manifest, node.identity.deviceId);
    } catch (e) {
      await node.deleteFile(manifest.fileId);
      throw Exception(
        'Upload rollback: relay manifest publish failed. '
        'Check signaling service and retry. ($e)',
      );
    }
    ref.invalidate(filesProvider);
    return manifest;
  }

  /// Download a file.
  Future<Uint8List> downloadFile(String fileId) async {
    final node = await ref.read(fireCloudNodeProvider.future);
    final localManifest = await node.localStorage.getManifest(fileId);
    if (localManifest != null) {
      return await node.downloadFile(fileId);
    }

    final syncService = await ref.read(manifestSyncProvider.future);
    var remoteManifest = syncService.getRemoteManifest(fileId);
    if (remoteManifest == null) {
      await syncService.syncFromCloudAndPeers();
      remoteManifest = syncService.getRemoteManifest(fileId);
    }
    if (remoteManifest == null) {
      throw Exception('File not found in local or remote manifests: $fileId');
    }
    return await node.downloadManifest(remoteManifest);
  }

  /// Delete a file.
  Future<void> deleteFile(String fileId) async {
    final node = await ref.read(fireCloudNodeProvider.future);
    final localManifest = await node.localStorage.getManifest(fileId);
    if (localManifest != null) {
      await node.deleteFile(fileId);
    } else {
      final syncService = await ref.read(manifestSyncProvider.future);
      await syncService.deleteManifestFromRelay(fileId);
      syncService.removeRemoteManifest(fileId);
    }
    await node.reconcileStorageState();
    ref.invalidate(nodeConfigProvider);
    ref.invalidate(networkCapacityProvider);
    ref.invalidate(filesProvider);
  }
}

final fileActionsProvider = AsyncNotifierProvider<FileActionsNotifier, void>(
  FileActionsNotifier.new,
);
