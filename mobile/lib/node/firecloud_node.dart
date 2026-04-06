import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:path_provider/path_provider.dart';

import '../crypto/encryption.dart';
import '../storage/chunking.dart';
import '../storage/local_storage.dart';
import '../p2p/peer_discovery.dart';
import 'device_identity.dart';
import 'node_role.dart';

/// The main FireCloud P2P node running on this device.
/// This is a fully decentralized node - no central server needed.
class FireCloudNode {
  final DeviceIdentity identity;
  final NodeRoleManager roleManager;
  final String? accountId;
  late final LocalStorage localStorage;
  late final PeerDiscovery peerDiscovery;
  late final ChunkDistributor chunkDistributor;
  
  HttpServer? _httpServer;
  bool _isRunning = false;
  final int _nodePort;

  FireCloudNode({
    required this.identity,
    required this.roleManager,
    this.accountId,
    int port = 4001,
  }) : _nodePort = port;

  bool get isRunning => _isRunning;
  int get port => _nodePort;
  
  /// Initialize the node.
  Future<void> initialize() async {
    localStorage = LocalStorage(roleManager: roleManager);
    await localStorage.initialize();
    await _loadKeys();
    await _enforceConsumerStoragePolicy();
    
    peerDiscovery = PeerDiscovery(
      identity: identity,
      roleManager: roleManager,
      nodePort: _nodePort,
      accountId: accountId,
    );
    
    chunkDistributor = ChunkDistributor(
      localStorage: localStorage,
      peerDiscovery: peerDiscovery,
      identity: identity,
    );
  }

  /// Start the P2P node.
  Future<void> start() async {
    if (_isRunning) return;
    
    // Start peer discovery
    await peerDiscovery.start();
    
    // Start HTTP server for incoming requests
    await _startHttpServer();
    
    _isRunning = true;
  }

  /// Stop the P2P node.
  Future<void> stop() async {
    if (!_isRunning) return;
    
    await peerDiscovery.stop();
    await _httpServer?.close();
    _httpServer = null;
    
    _isRunning = false;
  }

  /// Reconcile storage state after role changes or app restarts.
  Future<void> reconcileStorageState({bool purgeOrphans = false}) async {
    if (roleManager.isConsumer) {
      final deleted = await localStorage.clearAllChunks();
      if (deleted > 0) {
        developer.log(
          'Consumer policy removed local chunks bytes=$deleted',
          name: 'firecloud.node',
        );
      }
      return;
    }

    if (purgeOrphans) {
      final deleted = await localStorage.purgeUnreferencedChunks();
      if (deleted > 0) {
        developer.log('Purged orphan chunks bytes=$deleted', name: 'firecloud.node');
      }
      return;
    }

    await localStorage.refreshUsage();
  }

  /// Start HTTP server to handle peer requests.
  Future<void> _startHttpServer() async {
    _httpServer = await HttpServer.bind(InternetAddress.anyIPv4, _nodePort);
    
    _httpServer!.listen(_handleRequest);
  }

  /// Handle incoming HTTP request.
  void _handleRequest(HttpRequest request) async {
    try {
      final path = request.uri.path;
      
      if (path == '/health') {
        _respondJson(request, {'status': 'ok', 'device_id': identity.deviceId});
      } else if (path == '/info') {
        _respondJson(request, {
          'device_id': identity.deviceId,
          'role': roleManager.isStorageProvider ? 'storage_provider' : 'consumer',
          'available_storage': roleManager.availableStorageBytes,
          'used_storage': roleManager.usedStorageBytes,
        });
      } else if (path.startsWith('/chunks/')) {
        await _handleChunkRequest(request);
      } else if (path == '/files') {
        await _handleFilesRequest(request);
      } else if (path == '/manifests') {
        await _handleManifestsRequest(request);
      } else {
        request.response.statusCode = 404;
        request.response.write('Not found');
        await request.response.close();
      }
    } catch (e) {
      developer.log('Request handling failed: $e', name: 'firecloud.node');
      request.response.statusCode = 500;
      request.response.write('Error: $e');
      await request.response.close();
    }
  }

  /// Handle chunk storage/retrieval requests.
  Future<void> _handleChunkRequest(HttpRequest request) async {
    final hash = request.uri.pathSegments.last;
    
    if (request.method == 'GET') {
      final chunk = await localStorage.getChunk(hash);
      if (chunk != null) {
        request.response.headers.contentType = ContentType.binary;
        request.response.add(chunk);
        await request.response.close();
      } else {
        request.response.statusCode = 404;
        request.response.write('Chunk not found');
        await request.response.close();
      }
    } else if (request.method == 'POST' || request.method == 'PUT') {
      if (!roleManager.isStorageProvider) {
        request.response.statusCode = 403;
        request.response.write('This node is not a storage provider');
        await request.response.close();
        return;
      }
      
      final bytes = await request.fold<List<int>>([], (p, c) => p..addAll(c));
      try {
        await localStorage.storeChunk(hash, Uint8List.fromList(bytes));
        final fileId = request.headers.value('x-file-id');
        if (fileId != null && fileId.isNotEmpty) {
          await localStorage.addChunkReference(hash, fileId);
        }
        request.response.statusCode = 201;
        _respondJson(request, {'status': 'stored', 'hash': hash});
      } catch (e) {
        request.response.statusCode = 507;
        request.response.write('Storage quota exceeded');
        await request.response.close();
      }
    } else if (request.method == 'DELETE') {
      if (!roleManager.isStorageProvider) {
        request.response.statusCode = 403;
        request.response.write('This node is not a storage provider');
        await request.response.close();
        return;
      }

      final fileId = request.headers.value('x-file-id');
      if (fileId == null || fileId.isEmpty) {
        request.response.statusCode = 400;
        request.response.write('Missing file reference');
        await request.response.close();
        return;
      }

      final remainingRefs = await localStorage.removeChunkReference(hash, fileId);
      if (remainingRefs == 0) {
        await localStorage.deleteChunk(hash);
      }
      request.response.statusCode = 200;
      _respondJson(request, {
        'status': 'deleted',
        'hash': hash,
        'remaining_refs': remainingRefs,
      });
    }
  }

  /// Handle file listing request.
  Future<void> _handleFilesRequest(HttpRequest request) async {
    final manifests = await localStorage.listManifests();
    final fileList = manifests.map((m) {
      return <String, dynamic>{
        'file_id': m.fileId,
        'file_name': m.fileName,
        'file_size': m.fileSize,
        'created_at': m.createdAt.toIso8601String(),
      };
    }).toList();
    _respondJson(request, fileList);
  }
  
  /// Handle manifest sync requests for cross-device file visibility.
  /// GET /manifests?owner_id=ID - returns manifests belonging to specified owner
  Future<void> _handleManifestsRequest(HttpRequest request) async {
    if (request.method != 'GET') {
      request.response.statusCode = 405;
      request.response.write('Method not allowed');
      await request.response.close();
      return;
    }
    
    final ownerId = request.uri.queryParameters['owner_id'];
    final encrypted = request.uri.queryParameters['encrypted'] == '1';
    final manifests = await localStorage.listManifests();
    
    // Filter by owner if specified
    final filtered = ownerId != null
        ? manifests.where((m) => m.ownerId == ownerId).toList()
        : manifests;
    
    if (!encrypted || ownerId == null || ownerId.isEmpty) {
      // Legacy/plain response
      final manifestList = filtered.map((m) => m.toJson()).toList();
      _respondJson(request, manifestList);
      return;
    }

    // Encrypted envelope response for account-scoped sync and provider caching.
    final envelopes = filtered.map((m) {
      final jsonText = jsonEncode(m.toJson());
      final payload = ManifestEncryption.encryptManifestJson(
        manifestJson: jsonText,
        ownerId: ownerId,
      );
      return <String, dynamic>{
        'file_id': m.fileId,
        'owner_id': ownerId,
        'encrypted_payload': payload,
        'created_at': m.createdAt.toIso8601String(),
      };
    }).toList();
    _respondJson(request, envelopes);
  }

  /// JSON response helper.
  void _respondJson(HttpRequest request, dynamic data) {
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(data));
    request.response.close();
  }

  /// Upload a file to the network.
  /// [ownerId] is the Google account UID for cross-device visibility.
  Future<FileManifest> uploadFile(
    String fileName,
    Uint8List data, {
    String? ownerId,
  }) async {
    // Chunk first so capacity validation matches real encrypted payload shape.
    final chunks = FastCDC.chunk(data);
    final providers = storageProviders;
    final localProviderCapacity = roleManager.isStorageProvider
        ? roleManager.availableStorageBytes
        : 0;
    final providerCount = providers.length + (localProviderCapacity > 0 ? 1 : 0);
    if (providerCount == 0) {
      throw P2PStorageUnavailableError(
        'No storage providers with available capacity are currently online',
      );
    }

    final requiredProviderBytes =
        chunks.fold<int>(0, (total, chunk) => total + chunk.size + 24);
    final totalAvailable =
        peerDiscovery.totalAvailableProviderStorageBytes + localProviderCapacity;
    if (totalAvailable < requiredProviderBytes) {
      throw P2PStorageUnavailableError(
        'Not enough provider capacity on network '
        '(available=$totalAvailable bytes, required=$requiredProviderBytes bytes)',
      );
    }

    // Generate encryption key for this file
    final encryptionKey = ChunkEncryption.generateKey();
    final uploadStartedAt = DateTime.now();
    
    final fileHash = sha256.convert(data).toString();
    final fileId = '${fileHash.substring(0, 16)}_${DateTime.now().millisecondsSinceEpoch}';

    // Distribute chunks to network
    final chunkRefs = await chunkDistributor.distributeChunks(
      chunks,
      encryptionKey,
      fileId,
    );
    
    final manifest = FileManifest(
      fileId: fileId,
      fileName: fileName,
      fileSize: data.length,
      fileHash: fileHash,
      chunks: chunkRefs,
      createdAt: DateTime.now(),
      ownerId: ownerId,
      uploaderDeviceId: identity.deviceId,
    );
    
    // Store manifest locally
    await localStorage.storeManifest(manifest);
    
    // Store encryption key (in real app, this would be encrypted with user key)
    await _storeFileKey(fileId, encryptionKey);

    final durationMs = DateTime.now().difference(uploadStartedAt).inMilliseconds;
    developer.log(
      'Upload completed file=$fileName size=${data.length} chunks=${chunks.length} duration_ms=$durationMs',
      name: 'firecloud.node',
    );
    
    return manifest;
  }

  /// Download a file from the network.
  Future<Uint8List> downloadFile(String fileId) async {
    final manifest = await localStorage.getManifest(fileId);
    if (manifest == null) {
      throw FileNotFoundException('File not found: $fileId');
    }
    
    final encryptionKey = await _getFileKey(fileId);
    if (encryptionKey == null) {
      throw Exception('Encryption key not found for file');
    }
    
    return await chunkDistributor.retrieveChunks(manifest, encryptionKey);
  }

  /// Delete a file.
  Future<void> deleteFile(String fileId) async {
    final manifest = await localStorage.getManifest(fileId);
    if (manifest == null) return;

    final visitedRemotes = <String>{};
    for (final chunk in manifest.chunks) {
      final localOwned = chunk.nodeIds.contains(identity.deviceId);
      if (localOwned) {
        final remainingRefs = await localStorage.removeChunkReference(chunk.hash, fileId);
        if (remainingRefs == 0) {
          await localStorage.deleteChunk(chunk.hash);
        }
      }

      for (final nodeId in chunk.nodeIds) {
        if (nodeId == identity.deviceId) continue;
        final requestKey = '$nodeId:${chunk.hash}';
        if (!visitedRemotes.add(requestKey)) continue;

        final peer = peerDiscovery.getPeer(nodeId);
        if (peer == null || !peer.isOnline) continue;
        try {
          await chunkDistributor.deleteChunkFromPeer(peer, chunk.hash, fileId);
        } catch (e) {
          developer.log(
            'Remote chunk delete failed node=$nodeId hash=${chunk.hash}: $e',
            name: 'firecloud.node',
          );
        }
      }
    }
    
    // Delete manifest and key
    await localStorage.deleteManifest(fileId);
    await _deleteFileKey(fileId);
    await localStorage.purgeUnreferencedChunks();
    await announcePresence();
  }

  /// List all files.
  Future<List<FileManifest>> listFiles() async {
    return await localStorage.listManifests();
  }

  /// Get network peers.
  List<PeerInfo> get peers => peerDiscovery.peers;

  /// Get storage providers.
  List<PeerInfo> get storageProviders => peerDiscovery.storageProviders;

  /// Broadcast current node role/quota to peers immediately.
  Future<void> announcePresence() async {
    await peerDiscovery.announceNow();
  }

  // Key storage helpers (simplified - in production use secure storage)
  final Map<String, Uint8List> _fileKeys = {};
  late final File _keysFile;

  Future<void> _loadKeys() async {
    final appDir = await getApplicationDocumentsDirectory();
    final keyDir = Directory('${appDir.path}/firecloud');
    await keyDir.create(recursive: true);
    _keysFile = File('${keyDir.path}/file_keys.json');
    if (!await _keysFile.exists()) return;
    try {
      final raw = await _keysFile.readAsString();
      final json = jsonDecode(raw) as Map<String, dynamic>;
      for (final entry in json.entries) {
        final encoded = entry.value as String;
        _fileKeys[entry.key] = Uint8List.fromList(base64Decode(encoded));
      }
    } catch (e) {
      developer.log('Failed loading key store: $e', name: 'firecloud.node');
    }
  }

  Future<void> _persistKeys() async {
    final json = <String, String>{};
    for (final entry in _fileKeys.entries) {
      json[entry.key] = base64Encode(entry.value);
    }
    await _keysFile.writeAsString(jsonEncode(json));
  }
  
  Future<void> _storeFileKey(String fileId, Uint8List key) async {
    _fileKeys[fileId] = key;
    await _persistKeys();
  }
  
  Future<Uint8List?> _getFileKey(String fileId) async {
    return _fileKeys[fileId];
  }
  
  Future<void> _deleteFileKey(String fileId) async {
    _fileKeys.remove(fileId);
    await _persistKeys();
  }

  Future<void> _enforceConsumerStoragePolicy() async {
    if (!roleManager.isConsumer) return;
    final deleted = await localStorage.clearAllChunks();
    if (deleted > 0) {
      developer.log(
        'Consumer startup policy removed local chunks bytes=$deleted',
        name: 'firecloud.node',
      );
    }
  }
}

/// File not found error.
class FileNotFoundException implements Exception {
  final String message;
  FileNotFoundException(this.message);
  @override
  String toString() => message;
}
