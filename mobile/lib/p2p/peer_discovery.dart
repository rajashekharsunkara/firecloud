import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;
import 'dart:io';

import '../node/device_identity.dart';
import '../node/node_role.dart';
import 'signaling_client.dart';

/// Peer information discovered on the network.
class PeerInfo {
  final String deviceId;
  final String publicKey;
  final String ipAddress;
  final int port;
  final NodeRole role;
  final int availableStorageBytes;
  final DateTime lastSeen;
  final String? publicUrl;
  final List<String> relayUrls;
  final String? natType;

  PeerInfo({
    required this.deviceId,
    required this.publicKey,
    required this.ipAddress,
    required this.port,
    required this.role,
    required this.availableStorageBytes,
    required this.lastSeen,
    this.publicUrl,
    this.relayUrls = const [],
    this.natType,
  });

  factory PeerInfo.fromJson(Map<String, dynamic> json) {
    return PeerInfo(
      deviceId: json['device_id'] as String,
      publicKey: json['public_key'] as String,
      ipAddress: json['ip_address'] as String,
      port: json['port'] as int,
      role: json['role'] == 'storage_provider' 
          ? NodeRole.storageProvider 
          : NodeRole.consumer,
      availableStorageBytes: json['available_storage'] as int? ?? 0,
      lastSeen: DateTime.now(),
      publicUrl: json['public_url'] as String?,
      relayUrls: ((json['relay_urls'] as List?) ?? const [])
          .map((entry) => entry.toString())
          .where((entry) => entry.isNotEmpty)
          .toList(),
      natType: json['nat_type'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'device_id': deviceId,
    'public_key': publicKey,
    'ip_address': ipAddress,
    'port': port,
    'role': role == NodeRole.storageProvider ? 'storage_provider' : 'consumer',
    'available_storage': availableStorageBytes,
    if (publicUrl != null) 'public_url': publicUrl,
    if (relayUrls.isNotEmpty) 'relay_urls': relayUrls,
    if (natType != null) 'nat_type': natType,
  };

  bool get isStorageProvider => role == NodeRole.storageProvider;
  bool get isOnline => DateTime.now().difference(lastSeen).inSeconds < 120;
  bool get requiresRelay => natType == 'symmetric' || natType == 'restricted';

  List<Uri> endpointCandidates(
    String pathAndQuery, {
    bool preferRelay = false,
  }) {
    final normalizedPath = pathAndQuery.startsWith('/')
        ? pathAndQuery
        : '/$pathAndQuery';
    final candidates = <Uri>[];

    void addBase(String? base) {
      if (base == null || base.isEmpty) return;
      final normalizedBase = base.endsWith('/')
          ? base.substring(0, base.length - 1)
          : base;
      try {
        candidates.add(Uri.parse('$normalizedBase$normalizedPath'));
      } catch (_) {
        // Ignore malformed endpoint candidates.
      }
    }

    if (preferRelay || requiresRelay) {
      for (final relay in relayUrls) {
        addBase(relay);
      }
      addBase(publicUrl);
    } else {
      addBase(publicUrl);
      for (final relay in relayUrls) {
        addBase(relay);
      }
    }

    candidates.add(Uri.parse('http://$ipAddress:$port$normalizedPath'));
    final seen = <String>{};
    return candidates.where((uri) => seen.add(uri.toString())).toList();
  }
}

/// mDNS/UDP-based peer discovery for local network.
class PeerDiscovery {
  static const multicastAddress = '239.255.42.99';
  static const multicastPort = 45454;
  static const serviceType = '_firecloud._tcp.local';
  static const broadcastInterval = Duration(seconds: 5);
  static const peerTimeout = Duration(seconds: 120);

  final DeviceIdentity identity;
  final NodeRoleManager roleManager;
  final int nodePort;
  final String? accountId;
  final String signalingServerUrl;

  RawDatagramSocket? _socket;
  Timer? _broadcastTimer;
  SignalingClient? _signalingClient;
  StreamSubscription<List<PeerInfo>>? _signalingPeerSubscription;
  final Map<String, PeerInfo> _lanPeers = {};
  final Map<String, PeerInfo> _wanPeers = {};
  final _peerStreamController = StreamController<List<PeerInfo>>.broadcast();

  Stream<List<PeerInfo>> get peerStream => _peerStreamController.stream;
  List<PeerInfo> get peers {
    // Prefer LAN addresses when a peer is visible via both LAN and WAN.
    final merged = <String, PeerInfo>{..._wanPeers, ..._lanPeers};
    return merged.values.toList();
  }
  List<PeerInfo> get storageProviders => 
      peers
          .where((p) => p.isStorageProvider && p.isOnline && p.availableStorageBytes > 0)
          .toList();
  int get totalAvailableProviderStorageBytes => storageProviders.fold(
        0,
        (total, peer) => total + peer.availableStorageBytes,
      );

  PeerDiscovery({
    required this.identity,
    required this.roleManager,
    this.nodePort = 4001,
    this.accountId,
    this.signalingServerUrl = SignalingClient.defaultServerUrl,
  });

  /// Start peer discovery service.
  Future<void> start() async {
    try {
      // Bind to multicast socket for discovery.
      // Some Android vendors do not support reusePort and throw at runtime.
      try {
        _socket = await RawDatagramSocket.bind(
          InternetAddress.anyIPv4,
          multicastPort,
          reuseAddress: true,
          reusePort: true,
        );
      } on SocketException catch (e) {
        developer.log(
          'PeerDiscovery: reusePort unavailable, retrying without reusePort - $e',
          name: 'firecloud.peer_discovery',
        );
        _socket = await RawDatagramSocket.bind(
          InternetAddress.anyIPv4,
          multicastPort,
          reuseAddress: true,
          reusePort: false,
        );
      }

      // Join multicast group
      final multicastGroup = InternetAddress(multicastAddress);
      _socket!.joinMulticast(multicastGroup);

      // Listen for peer announcements
      _socket!.listen(_handleDatagram);

      // Start periodic discovery pulse
      _broadcastTimer = Timer.periodic(broadcastInterval, (_) {
        _cleanupOldPeers();
        _peerStreamController.add(peers);
        unawaited(_sendDiscoveryProbe());
        unawaited(_broadcast());
      });

      // Initial startup burst to avoid "simultaneous launch" misses.
      for (var i = 0; i < 3; i++) {
        await _sendDiscoveryProbe();
        await _broadcast();
        await Future<void>.delayed(const Duration(milliseconds: 400));
      }
    } catch (e) {
      developer.log('PeerDiscovery: Failed to start - $e', name: 'firecloud.peer_discovery');
    }

    await _startSignalingDiscovery();
  }

  /// Stop peer discovery service.
  Future<void> stop() async {
    _broadcastTimer?.cancel();
    await _signalingPeerSubscription?.cancel();
    _signalingPeerSubscription = null;
    await _signalingClient?.stop();
    _signalingClient = null;
    _socket?.close();
    _lanPeers.clear();
    _wanPeers.clear();
    await _peerStreamController.close();
  }

  Future<void> _startSignalingDiscovery() async {
    if (accountId == null || accountId!.isEmpty) {
      developer.log(
        'PeerDiscovery: signaling disabled (no account id)',
        name: 'firecloud.peer_discovery',
      );
      return;
    }

    _signalingClient = SignalingClient(
      serverUrl: signalingServerUrl,
      identity: identity,
      roleManager: roleManager,
      nodePort: nodePort,
      accountId: accountId,
    );

    _signalingPeerSubscription = _signalingClient!.peerStream.listen((wanPeers) {
      _wanPeers
        ..clear()
        ..addEntries(wanPeers.map((peer) => MapEntry(peer.deviceId, peer)));
      _cleanupOldPeers();
      _peerStreamController.add(peers);
    });

    await _signalingClient!.start();
  }

  /// Broadcast our presence to the network.
  Future<void> _broadcast() async {
    if (_socket == null) return;

    final announcement = jsonEncode({
      'type': 'firecloud_announce',
      'device_id': identity.deviceId,
      'public_key': identity.publicKeyHex,
      'port': nodePort,
      'role': roleManager.isStorageProvider ? 'storage_provider' : 'consumer',
      'available_storage': roleManager.availableStorageBytes,
      'timestamp': DateTime.now().toIso8601String(),
    });

    final data = utf8.encode(announcement);
    _socket!.send(data, InternetAddress(multicastAddress), multicastPort);
  }

  Future<void> _sendDiscoveryProbe() async {
    if (_socket == null) return;
    final probe = jsonEncode({
      'type': 'firecloud_probe',
      'device_id': identity.deviceId,
      'timestamp': DateTime.now().toIso8601String(),
    });
    final data = utf8.encode(probe);
    _socket!.send(data, InternetAddress(multicastAddress), multicastPort);
  }

  /// Broadcast immediately after role/quota updates.
  Future<void> announceNow() async {
    try {
      await _broadcast();
      await _signalingClient?.updateRegistration();
    } catch (e) {
      developer.log('PeerDiscovery: Failed to announce - $e', name: 'firecloud.peer_discovery');
    }
  }

  /// Handle incoming datagram.
  void _handleDatagram(RawSocketEvent event) {
    if (event != RawSocketEvent.read) return;
    
    final datagram = _socket!.receive();
    if (datagram == null) return;

    try {
      final message = utf8.decode(datagram.data);
      final json = jsonDecode(message) as Map<String, dynamic>;

      final type = json['type'] as String? ?? '';
      if (type == 'firecloud_probe') {
        final sender = json['device_id'] as String?;
        if (sender != null && sender != identity.deviceId) {
          unawaited(_broadcast());
        }
        return;
      }
      if (type != 'firecloud_announce') return;

      final deviceId = json['device_id'] as String;
      
      // Ignore our own announcements
      if (deviceId == identity.deviceId) return;

      // Create/update peer info
      final peer = PeerInfo(
        deviceId: deviceId,
        publicKey: json['public_key'] as String,
        ipAddress: datagram.address.address,
        port: (json['port'] as num).toInt(),
        role: json['role'] == 'storage_provider' 
            ? NodeRole.storageProvider 
            : NodeRole.consumer,
        availableStorageBytes: (json['available_storage'] as num?)?.toInt() ?? 0,
        lastSeen: DateTime.now(),
      );

      _lanPeers[deviceId] = peer;
      _cleanupOldPeers();
      _peerStreamController.add(peers);
    } catch (e) {
      // Ignore malformed datagrams
    }
  }

  /// Remove peers that haven't been seen recently.
  void _cleanupOldPeers() {
    final now = DateTime.now();
    _lanPeers.removeWhere((_, peer) => 
      now.difference(peer.lastSeen) > peerTimeout);
    _wanPeers.removeWhere((_, peer) => 
      now.difference(peer.lastSeen) > peerTimeout);
  }

  /// Get a specific peer by device ID.
  PeerInfo? getPeer(String deviceId) => _lanPeers[deviceId] ?? _wanPeers[deviceId];

  /// Get best storage providers (sorted by available space).
  List<PeerInfo> getBestStorageProviders({int count = 5}) {
    final providers = storageProviders;
    providers.sort((a, b) => 
      b.availableStorageBytes.compareTo(a.availableStorageBytes));
    return providers.take(count).toList();
  }
}
