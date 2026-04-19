import 'dart:convert';
import 'dart:developer' as developer;

import 'package:dio/dio.dart';

import '../crypto/encryption.dart';
import '../p2p/peer_discovery.dart';
import '../storage/chunking.dart';
import '../storage/local_storage.dart';

/// Service for syncing file manifests across devices with the same account.
/// 
/// When a user has multiple devices with the same Google account:
/// - Device A uploads a file → manifest stored locally with ownerId
/// - Device B comes online → queries peers for manifests with matching ownerId
/// - Device B can now see and download files uploaded from Device A
class ManifestSyncService {
  final LocalStorage localStorage;
  final PeerDiscovery peerDiscovery;
  final String? currentOwnerId;
  final String? relayApiBaseUrl;
  final Future<String?> Function()? authTokenProvider;
  final Dio _dio;
  
  /// Manifests from remote peers (not stored locally yet)
  final Map<String, FileManifest> _remoteManifests = {};
  
  ManifestSyncService({
    required this.localStorage,
    required this.peerDiscovery,
    this.currentOwnerId,
    this.relayApiBaseUrl,
    this.authTokenProvider,
  }) : _dio = Dio(BaseOptions(
    connectTimeout: const Duration(seconds: 5),
    receiveTimeout: const Duration(seconds: 10),
  ));

  String? get _normalizedRelayApiBase {
    final raw = relayApiBaseUrl?.trim();
    if (raw == null || raw.isEmpty) return null;
    return raw.endsWith('/') ? raw.substring(0, raw.length - 1) : raw;
  }

  Future<Map<String, String>> _buildAuthHeaders() async {
    final headers = <String, String>{};
    final ownerId = currentOwnerId;
    if (ownerId != null && ownerId.isNotEmpty) {
      headers['X-FireCloud-Account-Id'] = ownerId;
      headers['X-Account-Id'] = ownerId;
    }
    if (authTokenProvider != null) {
      final token = await authTokenProvider!();
      if (token != null && token.isNotEmpty) {
        headers['Authorization'] = 'Bearer $token';
      }
    }
    return headers;
  }

  int _mergeRemoteManifest(FileManifest manifest) {
    final existing = _remoteManifests[manifest.fileId];
    if (existing == null) {
      _remoteManifests[manifest.fileId] = manifest;
      return 1;
    }
    if (manifest.createdAt.isAfter(existing.createdAt)) {
      _remoteManifests[manifest.fileId] = manifest;
    }
    return 0;
  }

  Future<FileManifest?> _decodeEnvelope(
    Map<String, dynamic> envelope,
    String ownerId,
  ) async {
    final encryptedPayload = envelope['encrypted_payload'] as String?;
    final fileId = envelope['file_id'] as String?;
    if (encryptedPayload == null || fileId == null) return null;

    final jsonText = ManifestEncryption.decryptManifestJson(
      encryptedBase64: encryptedPayload,
      ownerId: ownerId,
    );
    final decoded = FileManifest.fromJson(
      (jsonDecode(jsonText) as Map<String, dynamic>),
    );
    await localStorage.cacheRemoteManifestEnvelope(
      fileId: fileId,
      envelope: envelope,
    );
    return decoded;
  }
  
  /// Get all manifests (local + remote synced).
  Future<List<FileManifest>> getAllManifests() async {
    final local = await localStorage.listManifests();
    final remote = _remoteManifests.values.toList();
    
    // Merge, preferring local versions (they have encryption keys)
    final merged = <String, FileManifest>{};
    for (final m in local) {
      merged[m.fileId] = m;
    }
    for (final m in remote) {
      if (!merged.containsKey(m.fileId)) {
        merged[m.fileId] = m;
      }
    }
    
    return merged.values.toList()
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
  }

  /// Sync manifests from relay (Cloud Run signaling service).
  Future<int> syncFromRelay() async {
    final ownerId = currentOwnerId;
    final baseUrl = _normalizedRelayApiBase;
    if (ownerId == null || baseUrl == null) {
      return 0;
    }

    try {
      final headers = await _buildAuthHeaders();
      final response = await _dio.get<Map<String, dynamic>>(
        '$baseUrl/api/v1/manifests',
        options: Options(headers: headers),
        queryParameters: {'owner_id': ownerId},
      );
      final items = (response.data?['manifests'] as List?) ?? const [];
      var synced = 0;
      for (final item in items) {
        if (item is! Map<String, dynamic>) continue;
        try {
          final manifest = await _decodeEnvelope(item, ownerId);
          if (manifest == null) continue;
          synced += _mergeRemoteManifest(manifest);
        } catch (_) {
          // Skip malformed envelope and continue.
        }
      }
      return synced;
    } catch (e) {
      developer.log(
        'Relay manifest sync failed: $e',
        name: 'firecloud.sync',
      );
      return 0;
    }
  }

  /// Publish freshly uploaded manifest envelope to relay for account-wide visibility.
  Future<void> publishManifestEnvelope(
    FileManifest manifest,
    String uploaderDeviceId,
  ) async {
    final ownerId = manifest.ownerId ?? currentOwnerId;
    final baseUrl = _normalizedRelayApiBase;
    if (ownerId == null || ownerId.isEmpty || baseUrl == null) {
      return;
    }

    final encodedEnvelope = ManifestEncryption.encryptManifestJson(
      manifestJson: jsonEncode(manifest.toJson()),
      ownerId: ownerId,
    );
    final payload = <String, dynamic>{
      'owner_id': ownerId,
      'file_id': manifest.fileId,
      'encrypted_payload': encodedEnvelope,
      'device_id': uploaderDeviceId,
      'created_at': manifest.createdAt.toIso8601String(),
    };

    final headers = await _buildAuthHeaders();
    await _dio.post<void>(
      '$baseUrl/api/v1/manifests/upsert',
      options: Options(headers: headers),
      data: jsonEncode(payload),
    );
    await localStorage.cacheRemoteManifestEnvelope(
      fileId: manifest.fileId,
      envelope: payload,
    );
    _mergeRemoteManifest(manifest);
  }

  /// Delete manifest envelope from relay and local remote cache.
  Future<void> deleteManifestFromRelay(String fileId) async {
    final ownerId = currentOwnerId;
    final baseUrl = _normalizedRelayApiBase;
    if (ownerId == null || ownerId.isEmpty || baseUrl == null) {
      return;
    }

    final headers = await _buildAuthHeaders();
    await _dio.delete<void>(
      '$baseUrl/api/v1/manifests/$fileId',
      options: Options(headers: headers),
      queryParameters: {'owner_id': ownerId},
    );
    _remoteManifests.remove(fileId);
  }

  /// Sync from relay first, then LAN/WAN peers.
  Future<int> syncFromCloudAndPeers() async {
    final relaySynced = await syncFromRelay();
    final peerSynced = await syncFromPeers();
    return relaySynced + peerSynced;
  }
  
  /// Sync manifests from all online peers with the same account.
  Future<int> syncFromPeers() async {
    if (currentOwnerId == null) {
      developer.log('No owner ID - skipping manifest sync', name: 'firecloud.sync');
      return 0;
    }
    
    final peers = peerDiscovery.peers.where((p) => p.isOnline).toList();
    int synced = 0;
    
    for (final peer in peers) {
      try {
        final manifests = await _fetchAndDecryptManifestsFromPeer(peer, currentOwnerId!);
        for (final manifest in manifests) {
          synced += _mergeRemoteManifest(manifest);
        }
      } catch (e) {
        developer.log(
          'Failed to sync from peer ${peer.deviceId}: $e',
          name: 'firecloud.sync',
        );
      }
    }
    
    developer.log(
      'Synced $synced manifests from ${peers.length} peers',
      name: 'firecloud.sync',
    );
    
    return synced;
  }
  
  /// Fetch encrypted manifest envelopes, decrypt and cache them.
  Future<List<FileManifest>> _fetchAndDecryptManifestsFromPeer(
    PeerInfo peer,
    String ownerId,
  ) async {
    final endpointPath = '/manifests?owner_id=$ownerId&encrypted=1';
    List<dynamic>? data;
    for (final endpoint in peer.endpointCandidates(endpointPath)) {
      try {
        final response = await _dio.get<List<dynamic>>(endpoint.toString());
        data = response.data;
        if (data != null) {
          break;
        }
      } catch (_) {
        // Try next endpoint candidate.
      }
    }
    if (data == null) return [];
    
    final manifests = <FileManifest>[];
    for (final item in data) {
      final envelope = item as Map<String, dynamic>;
      try {
        final decoded = await _decodeEnvelope(envelope, ownerId);
        if (decoded != null) {
          manifests.add(decoded);
        }
      } catch (_) {
        // Skip invalid envelope and continue.
      }
    }
    return manifests;
  }
  
  /// Check if a manifest is available (local or remote).
  bool hasManifest(String fileId) {
    return _remoteManifests.containsKey(fileId);
  }
  
  /// Get a remote manifest by file ID.
  FileManifest? getRemoteManifest(String fileId) {
    return _remoteManifests[fileId];
  }

  /// Remove remote manifest entry from in-memory cache.
  void removeRemoteManifest(String fileId) {
    _remoteManifests.remove(fileId);
  }
  
  /// Clear remote manifests cache.
  void clearCache() {
    _remoteManifests.clear();
  }

  /// Restore manifests from encrypted local cache when peers are offline.
  Future<int> restoreFromLocalCache() async {
    final ownerId = currentOwnerId;
    if (ownerId == null) return 0;
    final cached = await localStorage.listCachedRemoteManifestEnvelopes();
    var restored = 0;
    for (final envelope in cached) {
      final encryptedPayload = envelope['encrypted_payload'] as String?;
      final fileId = envelope['file_id'] as String?;
      if (encryptedPayload == null || fileId == null) continue;
      if (_remoteManifests.containsKey(fileId)) continue;
      try {
        final jsonText = ManifestEncryption.decryptManifestJson(
          encryptedBase64: encryptedPayload,
          ownerId: ownerId,
        );
        final manifest = FileManifest.fromJson(
          (jsonDecode(jsonText) as Map<String, dynamic>),
        );
        _remoteManifests[fileId] = manifest;
        restored++;
      } catch (_) {
        // Ignore stale cache entries.
      }
    }
    return restored;
  }
}
