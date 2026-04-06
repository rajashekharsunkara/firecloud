import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:firecloud_mobile/crypto/encryption.dart';
import 'package:firecloud_mobile/node/device_identity.dart';
import 'package:firecloud_mobile/node/node_role.dart';
import 'package:firecloud_mobile/p2p/peer_discovery.dart';
import 'package:firecloud_mobile/services/manifest_sync_service.dart';
import 'package:firecloud_mobile/storage/chunking.dart';
import 'package:firecloud_mobile/storage/local_storage.dart';

void main() {
  group('ManifestSyncService', () {
    test('syncFromPeers decrypts envelopes and caches them', () async {
      const ownerId = 'owner-1';
      final remoteManifest = _makeManifest(
        fileId: 'f1',
        fileName: 'remote.txt',
        ownerId: ownerId,
      );
      final encryptedPayload = ManifestEncryption.encryptManifestJson(
        manifestJson: jsonEncode(remoteManifest.toJson()),
        ownerId: ownerId,
      );

      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        if (request.uri.path == '/manifests' && request.method == 'GET') {
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode([
            {
              'file_id': remoteManifest.fileId,
              'owner_id': ownerId,
              'encrypted_payload': encryptedPayload,
              'created_at': remoteManifest.createdAt.toIso8601String(),
            },
          ]));
          await request.response.close();
          return;
        }
        request.response.statusCode = 404;
        await request.response.close();
      });

      final localStorage = _MemoryLocalStorage(localManifests: []);
      final peerDiscovery = _FakePeerDiscovery();
      peerDiscovery.peersList = [
        PeerInfo(
          deviceId: 'peer-1',
          publicKey: 'pk',
          ipAddress: server.address.address,
          port: server.port,
          role: NodeRole.storageProvider,
          availableStorageBytes: 1024,
          lastSeen: DateTime.now(),
        ),
      ];

      final service = ManifestSyncService(
        localStorage: localStorage,
        peerDiscovery: peerDiscovery,
        currentOwnerId: ownerId,
      );

      final synced = await service.syncFromPeers();
      final all = await service.getAllManifests();

      expect(synced, equals(1));
      expect(all.length, equals(1));
      expect(all.single.fileId, equals('f1'));
      expect(localStorage.cachedEnvelopes.containsKey('f1'), isTrue);

      await server.close(force: true);
    });

    test('getAllManifests prefers local manifest when fileId conflicts', () async {
      const ownerId = 'owner-2';
      final localManifest = _makeManifest(
        fileId: 'same-id',
        fileName: 'local-name.txt',
        ownerId: ownerId,
      );
      final remoteManifest = _makeManifest(
        fileId: 'same-id',
        fileName: 'remote-name.txt',
        ownerId: ownerId,
      );

      final encryptedPayload = ManifestEncryption.encryptManifestJson(
        manifestJson: jsonEncode(remoteManifest.toJson()),
        ownerId: ownerId,
      );

      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        if (request.uri.path == '/manifests' && request.method == 'GET') {
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode([
            {
              'file_id': remoteManifest.fileId,
              'owner_id': ownerId,
              'encrypted_payload': encryptedPayload,
              'created_at': remoteManifest.createdAt.toIso8601String(),
            },
          ]));
          await request.response.close();
          return;
        }
        request.response.statusCode = 404;
        await request.response.close();
      });

      final localStorage = _MemoryLocalStorage(localManifests: [localManifest]);
      final peerDiscovery = _FakePeerDiscovery();
      peerDiscovery.peersList = [
        PeerInfo(
          deviceId: 'peer-2',
          publicKey: 'pk',
          ipAddress: server.address.address,
          port: server.port,
          role: NodeRole.storageProvider,
          availableStorageBytes: 1024,
          lastSeen: DateTime.now(),
        ),
      ];

      final service = ManifestSyncService(
        localStorage: localStorage,
        peerDiscovery: peerDiscovery,
        currentOwnerId: ownerId,
      );

      await service.syncFromPeers();
      final all = await service.getAllManifests();

      expect(all.length, equals(1));
      expect(all.single.fileName, equals('local-name.txt'));

      await server.close(force: true);
    });

    test('restoreFromLocalCache restores encrypted manifest without peers', () async {
      const ownerId = 'owner-3';
      final manifest = _makeManifest(
        fileId: 'cached-file',
        fileName: 'cached.txt',
        ownerId: ownerId,
      );
      final envelope = <String, dynamic>{
        'file_id': manifest.fileId,
        'owner_id': ownerId,
        'encrypted_payload': ManifestEncryption.encryptManifestJson(
          manifestJson: jsonEncode(manifest.toJson()),
          ownerId: ownerId,
        ),
        'created_at': manifest.createdAt.toIso8601String(),
      };

      final localStorage = _MemoryLocalStorage(localManifests: []);
      localStorage.seedCachedEnvelope(envelope);

      final service = ManifestSyncService(
        localStorage: localStorage,
        peerDiscovery: _FakePeerDiscovery(),
        currentOwnerId: ownerId,
      );

      final restored = await service.restoreFromLocalCache();
      final all = await service.getAllManifests();

      expect(restored, equals(1));
      expect(all.length, equals(1));
      expect(all.single.fileId, equals('cached-file'));
    });
  });
}

FileManifest _makeManifest({
  required String fileId,
  required String fileName,
  required String ownerId,
}) {
  return FileManifest(
    fileId: fileId,
    fileName: fileName,
    fileSize: 10,
    fileHash: '$fileId-hash',
    chunks: [
      ChunkRef(hash: 'h1', offset: 0, size: 10, nodeIds: ['n1']),
    ],
    createdAt: DateTime.parse('2026-04-01T00:00:00Z'),
    ownerId: ownerId,
    uploaderDeviceId: 'dev-x',
  );
}

class _FakeIdentity extends DeviceIdentity {
  @override
  String get deviceId => 'local-dev';

  @override
  String get publicKeyHex => 'pk';
}

class _FakeRoleManager extends NodeRoleManager {
  @override
  bool get isStorageProvider => true;

  @override
  int get availableStorageBytes => 1024;

  @override
  NodeRole get role => NodeRole.storageProvider;
}

class _FakePeerDiscovery extends PeerDiscovery {
  _FakePeerDiscovery()
      : super(
          identity: _FakeIdentity(),
          roleManager: _FakeRoleManager(),
          nodePort: 4001,
        );

  List<PeerInfo> peersList = [];

  @override
  List<PeerInfo> get peers => peersList;
}

class _MemoryLocalStorage extends LocalStorage {
  _MemoryLocalStorage({required this.localManifests})
      : super(roleManager: _FakeRoleManager());

  final List<FileManifest> localManifests;
  final Map<String, Map<String, dynamic>> cachedEnvelopes = {};

  void seedCachedEnvelope(Map<String, dynamic> envelope) {
    cachedEnvelopes[envelope['file_id'] as String] = Map<String, dynamic>.from(envelope);
  }

  @override
  Future<List<FileManifest>> listManifests() async {
    return List<FileManifest>.from(localManifests);
  }

  @override
  Future<void> cacheRemoteManifestEnvelope({
    required String fileId,
    required Map<String, dynamic> envelope,
  }) async {
    cachedEnvelopes[fileId] = Map<String, dynamic>.from(envelope);
  }

  @override
  Future<List<Map<String, dynamic>>> listCachedRemoteManifestEnvelopes() async {
    return cachedEnvelopes.values
        .map((e) => Map<String, dynamic>.from(e))
        .toList();
  }
}
