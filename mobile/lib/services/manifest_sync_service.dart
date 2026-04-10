import 'dart:convert';
import 'dart:developer' as developer;

import 'package:dio/dio.dart';

import '../crypto/encryption.dart';
import '../p2p/peer_discovery.dart';
import '../p2p/signaling_client.dart';
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
  final String signalingServerUrl;
  final AuthTokenProvider? authTokenProvider;
  final Dio _dio;
  final Dio _signalingDio;

  /// Manifests from remote peers (not stored locally yet)
  final Map<String, FileManifest> _remoteManifests = {};

  ManifestSyncService({
    required this.localStorage,
    required this.peerDiscovery,
    this.currentOwnerId,
    this.signalingServerUrl = SignalingClient.defaultServerUrl,
    this.authTokenProvider,
  }) : _dio = Dio(
         BaseOptions(
           connectTimeout: const Duration(seconds: 5),
           receiveTimeout: const Duration(seconds: 10),
         ),
       ),
       _signalingDio = Dio(
         BaseOptions(
           baseUrl: signalingServerUrl,
           connectTimeout: const Duration(seconds: 4),
           receiveTimeout: const Duration(seconds: 8),
           headers: const {'Content-Type': 'application/json'},
         ),
       ) {
    _signalingDio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) async {
          final ownerId = currentOwnerId?.trim();
          if (ownerId != null && ownerId.isNotEmpty) {
            options.headers['X-Account-ID'] = ownerId;
          }
          if (authTokenProvider != null) {
            try {
              final token = await authTokenProvider!.call();
              if (token != null && token.isNotEmpty) {
                options.headers['Authorization'] = 'Bearer $token';
              }
            } catch (e) {
              developer.log(
                'Unable to fetch Firebase auth token for signaling request: $e',
                name: 'firecloud.sync',
              );
            }
          }
          handler.next(options);
        },
      ),
    );
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

  /// Sync manifests from all online peers with the same account.
  Future<int> syncFromPeers() async {
    final ownerId = currentOwnerId;
    if (ownerId == null) {
      developer.log(
        'No owner ID - skipping manifest sync',
        name: 'firecloud.sync',
      );
      return 0;
    }

    final peers = peerDiscovery.peers.where((p) => p.isOnline).toList();
    int synced = 0;

    if (_supportsSignalingManifestSync) {
      synced += await _syncFromSignaling(ownerId);
    }

    for (final peer in peers) {
      try {
        final manifests = await _fetchAndDecryptManifestsFromPeer(
          peer,
          ownerId,
        );
        for (final manifest in manifests) {
          if (!_remoteManifests.containsKey(manifest.fileId)) {
            _remoteManifests[manifest.fileId] = manifest;
            synced++;
          }
        }
      } catch (e) {
        developer.log(
          'Failed to sync from peer ${peer.deviceId}: $e',
          name: 'firecloud.sync',
        );
      }
    }

    developer.log(
      'Synced $synced manifests (${peers.length} peer sources)',
      name: 'firecloud.sync',
    );

    return synced;
  }

  Future<void> publishManifest(FileManifest manifest) async {
    final ownerId = manifest.ownerId ?? currentOwnerId;
    if (ownerId == null || !_supportsSignalingManifestSync) return;

    try {
      final encryptedPayload = ManifestEncryption.encryptManifestJson(
        manifestJson: jsonEncode(manifest.toJson()),
        ownerId: ownerId,
      );
      await _signalingDio.post<void>(
        '/api/v1/manifests/upsert',
        data: {
          'owner_id': ownerId,
          'file_id': manifest.fileId,
          'encrypted_payload': encryptedPayload,
          'device_id': manifest.uploaderDeviceId ?? 'unknown-device',
          'created_at': manifest.createdAt.toIso8601String(),
        },
      );
    } catch (e) {
      developer.log(
        'Failed to publish manifest ${manifest.fileId} to signaling: $e',
        name: 'firecloud.sync',
      );
    }
  }

  Future<void> deletePublishedManifest(String fileId) async {
    final ownerId = currentOwnerId;
    if (ownerId == null || !_supportsSignalingManifestSync) return;
    try {
      await _signalingDio.delete<void>(
        '/api/v1/manifests/$fileId',
        queryParameters: {'owner_id': ownerId},
      );
    } catch (e) {
      developer.log(
        'Failed to delete published manifest $fileId from signaling: $e',
        name: 'firecloud.sync',
      );
    }
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
    return _decodeManifestEnvelopes(data, ownerId);
  }

  Future<int> _syncFromSignaling(String ownerId) async {
    try {
      final response = await _signalingDio.get<Map<String, dynamic>>(
        '/api/v1/manifests',
        queryParameters: {'owner_id': ownerId},
      );
      final envelopes =
          response.data?['manifests'] as List<dynamic>? ?? const [];
      final manifests = await _decodeManifestEnvelopes(envelopes, ownerId);
      var synced = 0;
      for (final manifest in manifests) {
        if (!_remoteManifests.containsKey(manifest.fileId)) {
          _remoteManifests[manifest.fileId] = manifest;
          synced++;
        }
      }
      return synced;
    } catch (e) {
      developer.log(
        'Signaling manifest sync failed: $e',
        name: 'firecloud.sync',
      );
      return 0;
    }
  }

  Future<List<FileManifest>> _decodeManifestEnvelopes(
    List<dynamic> envelopes,
    String ownerId,
  ) async {
    final manifests = <FileManifest>[];
    for (final item in envelopes) {
      if (item is! Map) continue;
      final envelope = Map<String, dynamic>.from(item);
      final encryptedPayload = envelope['encrypted_payload'] as String?;
      final fileId = envelope['file_id'] as String?;
      if (encryptedPayload == null || fileId == null) continue;
      try {
        final jsonText = ManifestEncryption.decryptManifestJson(
          encryptedBase64: encryptedPayload,
          ownerId: ownerId,
        );
        final decoded = FileManifest.fromJson(
          (jsonDecode(jsonText) as Map<String, dynamic>),
        );
        manifests.add(decoded);
        await localStorage.cacheRemoteManifestEnvelope(
          fileId: fileId,
          envelope: envelope,
        );
      } catch (_) {
        // Skip invalid envelope and continue.
      }
    }
    return manifests;
  }

  bool get _supportsSignalingManifestSync {
    final uri = Uri.tryParse(signalingServerUrl.trim());
    final host = uri?.host.trim().toLowerCase() ?? '';
    return host.isNotEmpty && host != 'signal.firecloud.app';
  }

  /// Check if a manifest is available (local or remote).
  bool hasManifest(String fileId) {
    return _remoteManifests.containsKey(fileId);
  }

  /// Get a remote manifest by file ID.
  FileManifest? getRemoteManifest(String fileId) {
    return _remoteManifests[fileId];
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
