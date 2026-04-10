import 'package:flutter_test/flutter_test.dart';

import 'package:firecloud_mobile/node/node_role.dart';
import 'package:firecloud_mobile/p2p/peer_discovery.dart';

void main() {
  PeerInfo makePeer({
    String? natType,
    bool includePublicUrl = true,
    List<String> relayUrls = const [
      'https://relay1.example',
      'https://relay2.example',
    ],
  }) {
    return PeerInfo(
      deviceId: 'dev-1',
      publicKey: 'pk',
      ipAddress: '192.168.1.10',
      port: 4001,
      role: NodeRole.storageProvider,
      availableStorageBytes: 1024,
      lastSeen: DateTime.now(),
      publicUrl: includePublicUrl ? 'http://8.8.8.8:4001' : null,
      relayUrls: relayUrls,
      natType: natType,
    );
  }

  group('PeerInfo endpointCandidates', () {
    test('prefers public URL then relays then LAN by default', () {
      final peer = makePeer();
      final endpoints = peer.endpointCandidates('/chunks/hash');

      expect(endpoints.map((u) => u.toString()), [
        'http://8.8.8.8:4001/chunks/hash',
        'https://relay1.example/chunks/hash',
        'https://relay2.example/chunks/hash',
        'http://192.168.1.10:4001/chunks/hash',
      ]);
    });

    test('prefers relays first when relay is required by NAT', () {
      final peer = makePeer(natType: 'symmetric');
      final endpoints = peer.endpointCandidates('chunks/hash');

      expect(peer.requiresRelay, isTrue);
      expect(endpoints.first.toString(), 'https://relay1.example/chunks/hash');
      expect(endpoints.last.toString(), 'http://192.168.1.10:4001/chunks/hash');
    });

    test('deduplicates duplicate endpoint URLs', () {
      final peer = makePeer(
        relayUrls: const ['http://8.8.8.8:4001', 'http://8.8.8.8:4001'],
      );
      final endpoints = peer.endpointCandidates('/files');

      final asStrings = endpoints.map((u) => u.toString()).toList();
      expect(asStrings.toSet().length, equals(asStrings.length));
    });

    test('supports relay-only peers without direct endpoint', () {
      final peer = PeerInfo(
        deviceId: 'dev-1',
        publicKey: 'pk',
        ipAddress: '',
        port: 0,
        role: NodeRole.storageProvider,
        availableStorageBytes: 1024,
        lastSeen: DateTime.now(),
        relayUrls: const ['https://relay1.example/p2p/dev-1'],
        hasDirectEndpoint: false,
      );

      final endpoints = peer.endpointCandidates(
        '/chunks/hash',
        preferRelay: true,
      );
      expect(endpoints.map((u) => u.toString()), [
        'https://relay1.example/p2p/dev-1/chunks/hash',
      ]);
    });

    test('fromJson and toJson keep NAT and relay metadata', () {
      final json = <String, dynamic>{
        'device_id': 'dev-2',
        'public_key': 'pk2',
        'ip_address': '10.0.0.2',
        'port': 5000,
        'role': 'consumer',
        'available_storage': 0,
        'public_url': 'http://1.2.3.4:5000',
        'relay_urls': ['https://relay.example'],
        'nat_type': 'restricted',
      };

      final peer = PeerInfo.fromJson(json);
      final out = peer.toJson();

      expect(peer.requiresRelay, isTrue);
      expect(peer.role, equals(NodeRole.consumer));
      expect(out['public_url'], equals('http://1.2.3.4:5000'));
      expect(out['relay_urls'], equals(['https://relay.example']));
      expect(out['nat_type'], equals('restricted'));
    });
  });
}
