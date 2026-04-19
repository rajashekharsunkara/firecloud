import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:firecloud_mobile/node/device_identity.dart';
import 'package:firecloud_mobile/node/node_role.dart';
import 'package:firecloud_mobile/p2p/signaling_client.dart';

void main() {
  group('SignalingClient', () {
    test('start registers, fetches peers, and stop unregisters', () async {
      var registerCount = 0;
      var peersCount = 0;
      var deleteCount = 0;

      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        if (request.uri.path == '/api/v1/peers/register' &&
            request.method == 'POST') {
          registerCount++;
          await request.drain<void>();
          request.response.statusCode = 200;
          request.response.write('{}');
          await request.response.close();
          return;
        }

        if (request.uri.path == '/api/v1/peers' && request.method == 'GET') {
          peersCount++;
          request.response.statusCode = 200;
          request.response.headers.contentType = ContentType.json;
          request.response.write(
            jsonEncode({
              'peers': [
                {
                  'device_id': 'peer-1',
                  'public_key': 'pk-1',
                  'public_ip': '203.0.113.20',
                  'public_port': 4100,
                  'public_url': 'http://203.0.113.20:4100',
                  'relay_urls': ['https://relay.example/p2p/peer-1'],
                  'nat_type': 'restricted',
                  'role': 'storage_provider',
                  'available_storage': 4096,
                },
              ],
            }),
          );
          await request.response.close();
          return;
        }

        if (request.uri.path == '/api/v1/peers/device-local' &&
            request.method == 'DELETE') {
          deleteCount++;
          request.response.statusCode = 200;
          request.response.write('{}');
          await request.response.close();
          return;
        }

        request.response.statusCode = 404;
        await request.response.close();
      });

      final client = SignalingClient(
        serverUrl: 'http://${server.address.address}:${server.port}',
        identity: _FakeIdentity(),
        roleManager: _FakeRoleManager(
          currentRole: NodeRole.storageProvider,
          availableBytes: 1024,
        ),
        nodePort: 4001,
        accountId: 'owner-1',
        publicIpOverride: '198.51.100.1',
      );

      await client.start();

      expect(registerCount, greaterThanOrEqualTo(1));
      expect(peersCount, greaterThanOrEqualTo(1));
      expect(client.peers.length, equals(1));
      expect(client.peers.single.deviceId, equals('peer-1'));
      expect(client.peers.single.requiresRelay, isTrue);

      await client.stop();
      expect(deleteCount, greaterThanOrEqualTo(1));

      await server.close(force: true);
    });

    test('register payload includes account and NAT metadata', () async {
      Map<String, dynamic>? registerPayload;
      String? authHeader;
      String? accountHeader;

      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        if (request.uri.path == '/api/v1/peers/register' &&
            request.method == 'POST') {
          final body = await utf8.decoder.bind(request).join();
          registerPayload = jsonDecode(body) as Map<String, dynamic>;
          authHeader = request.headers.value('authorization');
          accountHeader = request.headers.value('x-firecloud-account-id');
          request.response.statusCode = 200;
          request.response.write('{}');
          await request.response.close();
          return;
        }

        if (request.uri.path == '/api/v1/peers' && request.method == 'GET') {
          request.response.statusCode = 200;
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode({'peers': []}));
          await request.response.close();
          return;
        }

        if (request.uri.path == '/api/v1/peers/device-local' &&
            request.method == 'DELETE') {
          request.response.statusCode = 200;
          await request.response.close();
          return;
        }

        request.response.statusCode = 404;
        await request.response.close();
      });

      final client = SignalingClient(
        serverUrl: 'http://${server.address.address}:${server.port}',
        identity: _FakeIdentity(),
        roleManager: _FakeRoleManager(
          currentRole: NodeRole.consumer,
          availableBytes: 0,
        ),
        nodePort: 4555,
        accountId: 'owner-xyz',
        publicIpOverride: '203.0.113.5',
        relayBaseUrl: 'https://relay.custom.example',
        authTokenProvider: () async => 'test-token',
      );

      await client.start();
      await client.stop();

      expect(registerPayload, isNotNull);
      expect(registerPayload!['device_id'], equals('device-local'));
      expect(registerPayload!['account_id'], equals('owner-xyz'));
      expect(registerPayload!['public_ip'], equals('203.0.113.5'));
      expect(registerPayload!['public_port'], equals(4555));
      expect(registerPayload!['nat_type'], equals('cone'));
      expect(
        registerPayload!['relay_urls'],
        equals(['https://relay.custom.example/p2p/device-local']),
      );
      expect(authHeader, equals('Bearer test-token'));
      expect(accountHeader, equals('owner-xyz'));

      await server.close(force: true);
    });
  });
}

class _FakeIdentity extends DeviceIdentity {
  @override
  String get deviceId => 'device-local';

  @override
  String get publicKeyHex => 'public-key-hex';
}

class _FakeRoleManager extends NodeRoleManager {
  _FakeRoleManager({required this.currentRole, required this.availableBytes});

  final NodeRole currentRole;
  final int availableBytes;

  @override
  NodeRole get role => currentRole;

  @override
  bool get isStorageProvider => currentRole == NodeRole.storageProvider;

  @override
  int get availableStorageBytes => availableBytes;
}
