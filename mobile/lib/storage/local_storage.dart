import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;
import 'dart:io';
import 'dart:typed_data';

import 'package:path_provider/path_provider.dart';

import '../crypto/encryption.dart';
import '../node/device_identity.dart';
import '../node/node_role.dart';
import '../p2p/peer_discovery.dart';
import 'chunking.dart';

/// Local storage engine for chunks and manifests.
class LocalStorage {
  late Directory _chunksDir;
  late Directory _manifestsDir;
  late Directory _cacheDir;
  late Directory _manifestCacheDir;
  late File _chunkRefsFile;
  final Map<String, Set<String>> _chunkRefs = {};
  
  final NodeRoleManager roleManager;
  int _storedBytes = 0;

  LocalStorage({required this.roleManager});

  /// Initialize storage directories.
  Future<void> initialize() async {
    final appDir = await getApplicationDocumentsDirectory();
    final fireCloudDir = Directory('${appDir.path}/firecloud');
    
    _chunksDir = Directory('${fireCloudDir.path}/chunks');
    _manifestsDir = Directory('${fireCloudDir.path}/manifests');
    _cacheDir = Directory('${fireCloudDir.path}/cache');
    _manifestCacheDir = Directory('${fireCloudDir.path}/manifest_cache');
    _chunkRefsFile = File('${fireCloudDir.path}/chunk_refs.json');
    
    await _chunksDir.create(recursive: true);
    await _manifestsDir.create(recursive: true);
    await _cacheDir.create(recursive: true);
    await _manifestCacheDir.create(recursive: true);
    await _loadChunkReferences();
    
    // Calculate stored bytes
    await _calculateStoredBytes();
  }

  /// Store a chunk locally.
  Future<void> storeChunk(String hash, Uint8List data) async {
    final file = File('${_chunksDir.path}/$hash');
    if (await file.exists()) return; // Deduplication
    
    // Check quota for storage providers
    if (roleManager.isStorageProvider) {
      if (!roleManager.canStore(data.length)) {
        throw StorageQuotaExceededException(
          'Cannot store chunk: quota exceeded',
        );
      }
    }
    
    await file.writeAsBytes(data);
    _storedBytes += data.length;
    await roleManager.updateUsedStorage(_storedBytes);
  }

  /// Retrieve a chunk.
  Future<Uint8List?> getChunk(String hash) async {
    final file = File('${_chunksDir.path}/$hash');
    if (!await file.exists()) return null;
    return await file.readAsBytes();
  }

  /// Check if chunk exists locally.
  Future<bool> hasChunk(String hash) async {
    final file = File('${_chunksDir.path}/$hash');
    return await file.exists();
  }

  /// Delete a chunk.
  Future<void> deleteChunk(String hash) async {
    final file = File('${_chunksDir.path}/$hash');
    if (await file.exists()) {
      final size = await file.length();
      await file.delete();
      final hadRefs = _chunkRefs.remove(hash) != null;
      if (hadRefs) {
        await _persistChunkReferences();
      }
      _storedBytes -= size;
      await roleManager.updateUsedStorage(_storedBytes);
    }
  }

  /// Store a file manifest.
  Future<void> storeManifest(FileManifest manifest) async {
    final file = File('${_manifestsDir.path}/${manifest.fileId}.json');
    await file.writeAsString(jsonEncode(manifest.toJson()));
  }

  /// Get a file manifest.
  Future<FileManifest?> getManifest(String fileId) async {
    final file = File('${_manifestsDir.path}/$fileId.json');
    if (!await file.exists()) return null;
    final json = jsonDecode(await file.readAsString());
    return FileManifest.fromJson(json as Map<String, dynamic>);
  }

  /// List all stored manifests.
  Future<List<FileManifest>> listManifests() async {
    final manifests = <FileManifest>[];
    await for (final entity in _manifestsDir.list()) {
      if (entity is File && entity.path.endsWith('.json')) {
        try {
          final json = jsonDecode(await entity.readAsString());
          manifests.add(FileManifest.fromJson(json as Map<String, dynamic>));
        } catch (_) {
          // Skip corrupted manifests
        }
      }
    }
    return manifests;
  }

  /// Refresh usage counters from disk state.
  Future<void> refreshUsage() async {
    await _calculateStoredBytes();
  }

  /// Remove chunks not referenced by any local manifest.
  /// Returns total bytes deleted.
  Future<int> purgeUnreferencedChunks() async {
    final manifests = await listManifests();
    final referenced = <String>{};
    for (final manifest in manifests) {
      for (final chunk in manifest.chunks) {
        referenced.add(chunk.hash);
      }
    }
    // Keep chunks that are still referenced by provider-side file refs.
    referenced.addAll(_chunkRefs.keys);

    var deletedBytes = 0;
    var refsChanged = false;
    await for (final entity in _chunksDir.list()) {
      if (entity is! File) continue;
      final hash = entity.uri.pathSegments.last;
      if (referenced.contains(hash)) continue;
      final size = await entity.length();
      await entity.delete();
      if (_chunkRefs.remove(hash) != null) {
        refsChanged = true;
      }
      deletedBytes += size;
    }

    if (refsChanged) {
      await _persistChunkReferences();
    }

    await _calculateStoredBytes();
    return deletedBytes;
  }

  /// Remove all locally stored chunks and reset chunk reference metadata.
  /// Used to enforce consumer policy (no local chunk persistence).
  Future<int> clearAllChunks() async {
    var deletedBytes = 0;
    await for (final entity in _chunksDir.list()) {
      if (entity is! File) continue;
      final size = await entity.length();
      await entity.delete();
      deletedBytes += size;
    }
    if (_chunkRefs.isNotEmpty) {
      _chunkRefs.clear();
      await _persistChunkReferences();
    }
    await _calculateStoredBytes();
    return deletedBytes;
  }

  /// Delete a manifest.
  Future<void> deleteManifest(String fileId) async {
    final file = File('${_manifestsDir.path}/$fileId.json');
    if (await file.exists()) {
      await file.delete();
    }
  }

  /// Cache encrypted remote manifest payload for offline use.
  Future<void> cacheRemoteManifestEnvelope({
    required String fileId,
    required Map<String, dynamic> envelope,
  }) async {
    final file = File('${_manifestCacheDir.path}/$fileId.json');
    await file.writeAsString(jsonEncode(envelope));
  }

  /// Read all cached encrypted remote manifest envelopes.
  Future<List<Map<String, dynamic>>> listCachedRemoteManifestEnvelopes() async {
    final cached = <Map<String, dynamic>>[];
    await for (final entity in _manifestCacheDir.list()) {
      if (entity is! File || !entity.path.endsWith('.json')) continue;
      try {
        final parsed = jsonDecode(await entity.readAsString()) as Map<String, dynamic>;
        cached.add(parsed);
      } catch (_) {
        // Skip corrupted cache entries.
      }
    }
    return cached;
  }

  /// Get list of all stored chunk hashes.
  Future<List<String>> listChunks() async {
    final chunks = <String>[];
    await for (final entity in _chunksDir.list()) {
      if (entity is File) {
        chunks.add(entity.uri.pathSegments.last);
      }
    }
    return chunks;
  }

  /// Calculate total stored bytes.
  Future<void> _calculateStoredBytes() async {
    _storedBytes = 0;
    await for (final entity in _chunksDir.list()) {
      if (entity is File) {
        _storedBytes += await entity.length();
      }
    }
    await roleManager.updateUsedStorage(_storedBytes);
  }

  Future<void> _loadChunkReferences() async {
    _chunkRefs.clear();
    if (!await _chunkRefsFile.exists()) return;
    try {
      final raw = await _chunkRefsFile.readAsString();
      if (raw.trim().isEmpty) return;
      final parsed = jsonDecode(raw) as Map<String, dynamic>;
      for (final entry in parsed.entries) {
        final refs = (entry.value as List).cast<String>().toSet();
        if (refs.isNotEmpty) {
          _chunkRefs[entry.key] = refs;
        }
      }
    } catch (e) {
      developer.log(
        'Failed loading chunk refs: $e',
        name: 'firecloud.local_storage',
      );
    }
  }

  Future<void> _persistChunkReferences() async {
    final json = <String, List<String>>{};
    for (final entry in _chunkRefs.entries) {
      json[entry.key] = entry.value.toList()..sort();
    }
    await _chunkRefsFile.writeAsString(jsonEncode(json));
  }

  Future<void> addChunkReference(String hash, String fileId) async {
    if (fileId.isEmpty) return;
    final refs = _chunkRefs.putIfAbsent(hash, () => <String>{});
    if (refs.add(fileId)) {
      await _persistChunkReferences();
    }
  }

  Future<int> removeChunkReference(String hash, String fileId) async {
    final refs = _chunkRefs[hash];
    if (refs == null || fileId.isEmpty) return refs?.length ?? 0;
    refs.remove(fileId);
    if (refs.isEmpty) {
      _chunkRefs.remove(hash);
      await _persistChunkReferences();
      return 0;
    }
    await _persistChunkReferences();
    return refs.length;
  }

  /// Get storage statistics.
  StorageStats get stats => StorageStats(
    storedBytes: _storedBytes,
    quotaBytes: roleManager.storageQuotaBytes,
    availableBytes: roleManager.availableStorageBytes,
  );
}

/// Storage quota exceeded exception.
class StorageQuotaExceededException implements Exception {
  final String message;
  StorageQuotaExceededException(this.message);
  @override
  String toString() => message;
}

/// Storage statistics.
class StorageStats {
  final int storedBytes;
  final int quotaBytes;
  final int availableBytes;

  StorageStats({
    required this.storedBytes,
    required this.quotaBytes,
    required this.availableBytes,
  });
}

/// P2P chunk distribution across the network.
class ChunkDistributor {
  final LocalStorage localStorage;
  final PeerDiscovery peerDiscovery;
  final DeviceIdentity identity;
  final String relayBaseUrl;
  final String? accountId;
  final Future<String?> Function()? authTokenProvider;

  ChunkDistributor({
    required this.localStorage,
    required this.peerDiscovery,
    required this.identity,
    required this.relayBaseUrl,
    this.accountId,
    this.authTokenProvider,
  });

  String? get _normalizedRelayBaseUrl {
    final trimmed = relayBaseUrl.trim();
    if (trimmed.isEmpty) return null;
    return trimmed.endsWith('/')
        ? trimmed.substring(0, trimmed.length - 1)
        : trimmed;
  }

  bool get _requireRelayReplica {
    final owner = accountId?.trim();
    return owner != null && owner.isNotEmpty && _normalizedRelayBaseUrl != null;
  }

  Uri? _relayChunkUri({required String nodeId, required String hash}) {
    final base = _normalizedRelayBaseUrl;
    if (base == null) return null;
    return Uri.parse('$base/p2p/$nodeId/chunks/$hash');
  }

  Future<void> _applyRequestHeaders(
    HttpClientRequest request, {
    String? fileId,
    bool binaryPayload = false,
  }) async {
    request.headers.set('X-Device-ID', identity.deviceId);
    if (fileId != null && fileId.isNotEmpty) {
      request.headers.set('X-File-ID', fileId);
    }
    final owner = accountId?.trim();
    if (owner != null && owner.isNotEmpty) {
      request.headers.set('X-FireCloud-Account-Id', owner);
      request.headers.set('X-Account-Id', owner);
    }
    if (authTokenProvider != null) {
      final token = await authTokenProvider!();
      if (token != null && token.isNotEmpty) {
        request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      }
    }
    if (binaryPayload) {
      request.headers.set('Content-Type', 'application/octet-stream');
    }
  }

  /// Distribute chunks to storage providers on the network.
  /// Uses 3-of-5 erasure coding for fault tolerance.
  Future<List<ChunkRef>> distributeChunks(
    List<Chunk> chunks,
    Uint8List encryptionKey,
    String fileId,
  ) async {
    final refs = <ChunkRef>[];
    final providers = peerDiscovery.getBestStorageProviders(count: 5);
    final isProviderNode = localStorage.roleManager.isStorageProvider;
    final newlyStoredLocalChunkHashes = <String>{};

    try {
      if (providers.isEmpty) {
        if (isProviderNode) {
          for (final chunk in chunks) {
            final encrypted = ChunkEncryption.encrypt(chunk.data, encryptionKey);
            final existed = await localStorage.hasChunk(chunk.hash);
            await localStorage.storeChunk(chunk.hash, encrypted);
            await localStorage.addChunkReference(chunk.hash, fileId);
            if (!existed) newlyStoredLocalChunkHashes.add(chunk.hash);
            refs.add(ChunkRef(
              hash: chunk.hash,
              offset: chunk.offset,
              size: chunk.size,
              nodeIds: [identity.deviceId],
            ));
          }
          return refs;
        }
        throw P2PStorageUnavailableError(
          'No storage providers with available capacity are currently online',
        );
      }

      final requiredProviderBytes = chunks.fold<int>(
        0,
        (total, chunk) => total + chunk.size + 24,
      );
      final totalAvailableProviderBytes = providers.fold<int>(
        0,
        (total, provider) => total + provider.availableStorageBytes,
      );
      if (!isProviderNode && totalAvailableProviderBytes < requiredProviderBytes) {
        throw P2PStorageUnavailableError(
          'Not enough provider capacity on network '
          '(available=$totalAvailableProviderBytes bytes, required=$requiredProviderBytes bytes)',
        );
      }

      // Distribute to multiple providers
      for (final chunk in chunks) {
        final encrypted = ChunkEncryption.encrypt(chunk.data, encryptionKey);
        final nodeIds = <String>[];

        // Storage providers keep a local replica; consumer nodes do not.
        if (isProviderNode) {
          final existed = await localStorage.hasChunk(chunk.hash);
          await localStorage.storeChunk(chunk.hash, encrypted);
          await localStorage.addChunkReference(chunk.hash, fileId);
          if (!existed) newlyStoredLocalChunkHashes.add(chunk.hash);
          nodeIds.add(identity.deviceId);
        }

        final remoteTargetCount = isProviderNode ? 4 : 5;
        final uploadTargets = providers.take(remoteTargetCount).toList();
        final uploadResults = await Future.wait(
          uploadTargets.map((provider) async {
            try {
              await _sendChunkToPeer(provider, chunk.hash, encrypted, fileId);
              return provider.deviceId;
            } catch (e) {
              developer.log(
                'Failed to send chunk to ${provider.deviceId}: $e',
                name: 'firecloud.local_storage',
              );
              return null;
            }
          }),
        );
        final remoteNodeIds = uploadResults.whereType<String>().toList();
        nodeIds.addAll(remoteNodeIds);

        if (_normalizedRelayBaseUrl != null) {
          try {
            await _storeChunkInRelay(
              hash: chunk.hash,
              data: encrypted,
              fileId: fileId,
            );
            if (!nodeIds.contains(identity.deviceId)) {
              nodeIds.add(identity.deviceId);
            }
          } catch (e) {
            if (_requireRelayReplica) {
              throw UploadReplicationFailedError(
                'Upload could not mirror chunk ${chunk.hash} to relay cache: $e',
              );
            }
            developer.log(
              'Relay chunk mirror failed for ${chunk.hash}: $e',
              name: 'firecloud.local_storage',
            );
          }
        }

      if (!isProviderNode && remoteNodeIds.isEmpty) {
        throw UploadReplicationFailedError(
          'Upload could not reach any storage provider for chunk ${chunk.hash}',
        );
      }

      if (isProviderNode && remoteNodeIds.isEmpty && providers.isNotEmpty) {
        throw UploadReplicationFailedError(
          'Upload could not replicate to any remote storage provider for chunk ${chunk.hash}',
        );
      }

      refs.add(ChunkRef(
          hash: chunk.hash,
          offset: chunk.offset,
          size: chunk.size,
          nodeIds: nodeIds,
        ));
      }
      return refs;
    } catch (e) {
      for (final hash in newlyStoredLocalChunkHashes) {
        await localStorage.deleteChunk(hash);
      }
      rethrow;
    }
  }

  /// Retrieve chunks from the network.
  Future<Uint8List> retrieveChunks(
    FileManifest manifest,
    Uint8List encryptionKey,
  ) async {
    final buffer = Uint8List(manifest.fileSize);
    final allowLocalRead = localStorage.roleManager.isStorageProvider;
    
    for (final chunkRef in manifest.chunks) {
      Uint8List? encrypted;

      // Try local first
      if (allowLocalRead) {
        encrypted = await localStorage.getChunk(chunkRef.hash);
      }

      // Try remote nodes
      if (encrypted == null) {
        for (final nodeId in chunkRef.nodeIds) {
          if (nodeId == identity.deviceId) {
            try {
              encrypted = await _getChunkFromRelay(nodeId: nodeId, hash: chunkRef.hash);
              if (encrypted != null) break;
            } catch (_) {
              // Try other replicas.
            }
            continue;
          }
          
          final peer = peerDiscovery.getPeer(nodeId);
          if (peer != null && peer.isOnline) {
            try {
              encrypted = await _getChunkFromPeer(peer, chunkRef.hash);
              if (encrypted != null) break;
            } catch (_) {
              // Try relay fallback for this node id.
            }
          }
          try {
            encrypted = await _getChunkFromRelay(nodeId: nodeId, hash: chunkRef.hash);
            if (encrypted != null) break;
          } catch (_) {
            // Try next node id.
          }
        }
      }

      if (encrypted == null) {
        throw ChunkNotFoundError(
          'Chunk ${chunkRef.hash} not found on any node',
        );
      }

      // Decrypt and copy to buffer
      final decrypted = ChunkEncryption.decrypt(encrypted, encryptionKey);
      if (decrypted.length < chunkRef.size) {
        throw ChunkNotFoundError(
          'Chunk ${chunkRef.hash} payload is smaller than expected',
        );
      }
      buffer.setRange(chunkRef.offset, chunkRef.offset + chunkRef.size, decrypted);
    }

    return buffer;
  }

  /// Delete a chunk copy from peer if it exists.
  Future<void> deleteChunkFromPeer(PeerInfo peer, String hash, String fileId) async {
    final endpoints = peer.endpointCandidates('/chunks/$hash', preferRelay: true);
    Object? lastError;
    for (final endpoint in endpoints) {
      final client = HttpClient();
      try {
        final request = await client.deleteUrl(endpoint);
        await _applyRequestHeaders(request, fileId: fileId);
        final response = await request.close();
        if (response.statusCode == 200 || response.statusCode == 204 || response.statusCode == 404) {
          return;
        }
        lastError = Exception('Failed to delete chunk: ${response.statusCode}');
      } catch (e) {
        lastError = e;
      } finally {
        client.close();
      }
    }
    throw lastError ?? Exception('Failed to delete chunk from any endpoint');
  }

  /// Delete relay-cached chunk when no live peer endpoint is available.
  Future<void> deleteChunkFromRelay({
    required String nodeId,
    required String hash,
    required String fileId,
  }) async {
    final endpoint = _relayChunkUri(nodeId: nodeId, hash: hash);
    if (endpoint == null) {
      throw Exception('Relay base URL is not configured');
    }

    final client = HttpClient();
    try {
      final request = await client.deleteUrl(endpoint);
      await _applyRequestHeaders(request, fileId: fileId);
      final response = await request.close();
      if (response.statusCode == 200 ||
          response.statusCode == 204 ||
          response.statusCode == 404) {
        return;
      }
      throw Exception('Relay delete failed: ${response.statusCode}');
    } finally {
      client.close();
    }
  }

  /// Send chunk to peer via HTTP.
  Future<void> _sendChunkToPeer(
    PeerInfo peer,
    String hash,
    Uint8List data,
    String fileId,
  ) async {
    final endpoints = peer.endpointCandidates('/chunks/$hash', preferRelay: true);
    Object? lastError;
    for (final endpoint in endpoints) {
      final client = HttpClient();
      try {
        final request = await client.postUrl(endpoint);
        await _applyRequestHeaders(
          request,
          fileId: fileId,
          binaryPayload: true,
        );
        request.add(data);
        final response = await request.close();
        if (response.statusCode == 200 || response.statusCode == 201) {
          return;
        }
        if (response.statusCode == 507) {
          throw StorageQuotaExceededException(
            'Storage provider ${peer.deviceId} is full',
          );
        }
        lastError = Exception('Failed to store chunk: ${response.statusCode}');
      } catch (e) {
        lastError = e;
      } finally {
        client.close();
      }
    }
    if (lastError != null) throw lastError;
    throw Exception('Failed to store chunk on any endpoint');
  }

  Future<void> _storeChunkInRelay({
    required String hash,
    required Uint8List data,
    required String fileId,
  }) async {
    final endpoint = _relayChunkUri(nodeId: identity.deviceId, hash: hash);
    if (endpoint == null) {
      throw Exception('Relay base URL is not configured');
    }

    final client = HttpClient();
    try {
      final request = await client.postUrl(endpoint);
      await _applyRequestHeaders(
        request,
        fileId: fileId,
        binaryPayload: true,
      );
      request.add(data);
      final response = await request.close();
      if (response.statusCode == 200 || response.statusCode == 201) {
        return;
      }
      throw Exception('Relay store failed: ${response.statusCode}');
    } finally {
      client.close();
    }
  }

  /// Get chunk from peer via HTTP.
  Future<Uint8List?> _getChunkFromPeer(PeerInfo peer, String hash) async {
    final endpoints = peer.endpointCandidates('/chunks/$hash', preferRelay: true);
    for (final endpoint in endpoints) {
      final client = HttpClient();
      try {
        final request = await client.getUrl(endpoint);
        await _applyRequestHeaders(request);
        final response = await request.close();
        if (response.statusCode == 200) {
          final bytes = await response.fold<List<int>>(
            [],
            (prev, chunk) => prev..addAll(chunk),
          );
          return Uint8List.fromList(bytes);
        }
      } catch (_) {
        // Try next endpoint candidate.
      } finally {
        client.close();
      }
    }
    return null;
  }

  Future<Uint8List?> _getChunkFromRelay({
    required String nodeId,
    required String hash,
  }) async {
    final endpoint = _relayChunkUri(nodeId: nodeId, hash: hash);
    if (endpoint == null) return null;

    final client = HttpClient();
    try {
      final request = await client.getUrl(endpoint);
      await _applyRequestHeaders(request);
      final response = await request.close();
      if (response.statusCode != 200) {
        return null;
      }
      final bytes = await response.fold<List<int>>(
        [],
        (prev, chunk) => prev..addAll(chunk),
      );
      return Uint8List.fromList(bytes);
    } finally {
      client.close();
    }
  }
}

/// Chunk not found error.
class P2PStorageUnavailableError implements Exception {
  final String message;
  P2PStorageUnavailableError(this.message);
  @override
  String toString() => message;
}

class UploadReplicationFailedError implements Exception {
  final String message;
  UploadReplicationFailedError(this.message);
  @override
  String toString() => message;
}

/// Chunk not found error.
class ChunkNotFoundError implements Exception {
  final String message;
  ChunkNotFoundError(this.message);
  @override
  String toString() => message;
}
