import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;

import 'package:dio/dio.dart';

import '../node/device_identity.dart';
import '../node/node_role.dart';
import 'peer_discovery.dart';

typedef AuthTokenProvider = Future<String?> Function();

/// Signaling client for WAN peer discovery.
///
/// Uses HTTP polling to a signaling server for discovering peers
/// across different networks (not just LAN).
///
/// Protocol:
/// - POST /api/v1/peers/register - Register this device with server
/// - GET /api/v1/peers - Get peers scoped to authenticated account
/// - GET /api/v1/peers?account_id=X - Explicit account scope (must match auth)
/// - DELETE /api/v1/peers/{device_id} - Unregister (on app close)
class SignalingClient {
  // Build-time defaults (can be overridden in Settings at runtime).
  static const defaultServerUrl = String.fromEnvironment(
    'FIRECLOUD_SIGNALING_URL',
    defaultValue: 'https://signal.firecloud.app',
  );
  static const defaultRelayBaseUrl = String.fromEnvironment(
    'FIRECLOUD_RELAY_URL',
    defaultValue: 'https://relay.firecloud.app',
  );

  final String serverUrl;
  final String relayBaseUrl;
  final DeviceIdentity identity;
  final NodeRoleManager roleManager;
  final int nodePort;
  final String? accountId;
  final String? publicIpOverride;
  final AuthTokenProvider? authTokenProvider;

  final Dio _dio;
  Timer? _pollTimer;
  Timer? _heartbeatTimer;

  final Map<String, PeerInfo> _wanPeers = {};
  final _peerStreamController = StreamController<List<PeerInfo>>.broadcast();

  bool _isRunning = false;
  String? _publicIp;
  int? _publicPort;
  String _natType = 'unknown';

  Stream<List<PeerInfo>> get peerStream => _peerStreamController.stream;
  List<PeerInfo> get peers => _wanPeers.values.toList();
  bool get isRunning => _isRunning;

  SignalingClient({
    this.serverUrl = defaultServerUrl,
    this.relayBaseUrl = defaultRelayBaseUrl,
    required this.identity,
    required this.roleManager,
    required this.nodePort,
    this.accountId,
    this.publicIpOverride,
    this.authTokenProvider,
  }) : _dio = Dio(
         BaseOptions(
           baseUrl: serverUrl,
           connectTimeout: const Duration(seconds: 10),
           receiveTimeout: const Duration(seconds: 15),
           headers: {
             'Content-Type': 'application/json',
             'User-Agent': 'FireCloud/1.0',
           },
         ),
       ) {
    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) async {
          options.headers['X-Device-ID'] = identity.deviceId;
          final ownerId = accountId?.trim();
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
                'Unable to fetch Firebase auth token: $e',
                name: 'firecloud.signaling',
              );
            }
          }
          handler.next(options);
        },
      ),
    );
  }

  /// Start the signaling client.
  Future<void> start() async {
    if (_isRunning) return;
    _isRunning = true;

    developer.log(
      'SignalingClient starting (server=$serverUrl, account=$accountId)',
      name: 'firecloud.signaling',
    );

    // Detect public IP via STUN
    await _detectPublicAddress();

    // Register with signaling server
    await _register();

    // Start polling for peers
    _pollTimer = Timer.periodic(
      const Duration(seconds: 15),
      (_) => _pollPeers(),
    );

    // Start heartbeat to maintain registration
    _heartbeatTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => _heartbeat(),
    );

    // Initial peer fetch
    await _pollPeers();
  }

  /// Stop the signaling client.
  Future<void> stop() async {
    if (!_isRunning) return;
    _isRunning = false;

    _pollTimer?.cancel();
    _heartbeatTimer?.cancel();

    // Unregister from server
    await _unregister();

    _wanPeers.clear();
    await _peerStreamController.close();

    developer.log('SignalingClient stopped', name: 'firecloud.signaling');
  }

  /// Detect public IP address using STUN.
  Future<void> _detectPublicAddress() async {
    if (publicIpOverride != null && publicIpOverride!.isNotEmpty) {
      _publicIp = publicIpOverride;
      _publicPort = nodePort;
      _natType = 'cone';
      return;
    }

    try {
      // Use Google's public STUN server
      // For simplicity, we'll use an HTTP-based IP detection service
      final response = await Dio().get<Map<String, dynamic>>(
        'https://api.ipify.org?format=json',
      );

      _publicIp = response.data?['ip'] as String?;
      _publicPort = nodePort; // Assume same port (may need UPnP/NAT-PMP)
      _natType = _publicIp == null ? 'unknown' : 'cone';

      developer.log(
        'Public address detected: $_publicIp:$_publicPort',
        name: 'firecloud.signaling',
      );
    } catch (e) {
      developer.log(
        'Failed to detect public IP: $e',
        name: 'firecloud.signaling',
      );
      // Continue anyway - server may be able to detect our IP
      _natType = 'unknown';
    }
  }

  /// Register this device with the signaling server.
  Future<void> _register() async {
    try {
      await _dio.post<void>(
        '/api/v1/peers/register',
        data: jsonEncode({
          'device_id': identity.deviceId,
          'public_key': identity.publicKeyHex,
          'public_ip': _publicIp,
          'public_port': _publicPort ?? nodePort,
          'public_url': _publicIp == null
              ? null
              : 'http://$_publicIp:${_publicPort ?? nodePort}',
          'local_port': nodePort,
          'account_id': accountId,
          'role': roleManager.isStorageProvider
              ? 'storage_provider'
              : 'consumer',
          'available_storage': roleManager.availableStorageBytes,
          'nat_type': _natType,
          'relay_urls': _buildRelayUrls(),
        }),
      );

      developer.log(
        'Registered with signaling server',
        name: 'firecloud.signaling',
      );
    } catch (e) {
      developer.log(
        'Failed to register with signaling server: $e',
        name: 'firecloud.signaling',
      );
    }
  }

  /// Send heartbeat to maintain registration.
  Future<void> _heartbeat() async {
    if (!_isRunning) return;

    try {
      await _dio.post<void>(
        '/api/v1/peers/heartbeat',
        data: jsonEncode({
          'device_id': identity.deviceId,
          'available_storage': roleManager.availableStorageBytes,
        }),
      );
    } catch (e) {
      // Heartbeat failed - try to re-register
      developer.log(
        'Heartbeat failed, re-registering: $e',
        name: 'firecloud.signaling',
      );
      await _register();
    }
  }

  /// Unregister from the signaling server.
  Future<void> _unregister() async {
    try {
      await _dio.delete<void>('/api/v1/peers/${identity.deviceId}');
    } catch (e) {
      developer.log('Failed to unregister: $e', name: 'firecloud.signaling');
    }
  }

  /// Poll for peers from the signaling server.
  Future<void> _pollPeers() async {
    if (!_isRunning) return;

    try {
      final trimmedAccountId = accountId?.trim();

      // Prefer global peer discovery for prototype interoperability.
      var peersData = await _fetchPeersData();

      // Fallback for account-scoped signaling services.
      if (peersData.isEmpty &&
          trimmedAccountId != null &&
          trimmedAccountId.isNotEmpty) {
        peersData = await _fetchPeersData(accountScope: trimmedAccountId);
        if (peersData.isNotEmpty) {
          developer.log(
            'Using account-scoped peer list fallback',
            name: 'firecloud.signaling',
          );
        }
      }

      // Update peer list
      _wanPeers.clear();
      for (final peerJson in peersData) {
        final peer = _parsePeer(peerJson as Map<String, dynamic>);
        if (peer != null && peer.deviceId != identity.deviceId) {
          _wanPeers[peer.deviceId] = peer;
        }
      }

      _peerStreamController.add(peers);

      developer.log(
        'Polled ${_wanPeers.length} WAN peers',
        name: 'firecloud.signaling',
      );
    } catch (e) {
      developer.log('Failed to poll peers: $e', name: 'firecloud.signaling');
    }
  }

  Future<List<dynamic>> _fetchPeersData({String? accountScope}) async {
    final queryParams = <String, String>{};
    if (accountScope != null && accountScope.isNotEmpty) {
      queryParams['account_id'] = accountScope;
    }
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/v1/peers',
      queryParameters: queryParams.isEmpty ? null : queryParams,
    );
    return response.data?['peers'] as List<dynamic>? ?? const [];
  }

  /// Parse peer info from server response.
  PeerInfo? _parsePeer(Map<String, dynamic> json) {
    try {
      final publicIp = (json['public_ip'] as String?)?.trim();
      final publicPortRaw = json['public_port'];
      final publicPort = publicPortRaw is num
          ? publicPortRaw.toInt()
          : int.tryParse(publicPortRaw?.toString() ?? '');
      final publicUrl = (json['public_url'] as String?)?.trim();
      final relayUrls = ((json['relay_urls'] as List?) ?? const [])
          .map((entry) => entry.toString().trim())
          .where((entry) => entry.isNotEmpty)
          .toList();
      final hasDirectEndpoint =
          publicIp != null &&
          publicIp.isNotEmpty &&
          publicPort != null &&
          publicPort > 0;
      if (!hasDirectEndpoint &&
          (publicUrl == null || publicUrl.isEmpty) &&
          relayUrls.isEmpty) {
        return null;
      }

      return PeerInfo(
        deviceId: json['device_id'] as String,
        publicKey: json['public_key'] as String? ?? '',
        ipAddress: hasDirectEndpoint ? publicIp : '',
        port: hasDirectEndpoint ? publicPort : 0,
        role: json['role'] == 'storage_provider'
            ? NodeRole.storageProvider
            : NodeRole.consumer,
        availableStorageBytes:
            (json['available_storage'] as num?)?.toInt() ?? 0,
        lastSeen: DateTime.now(),
        publicUrl: publicUrl == null || publicUrl.isEmpty ? null : publicUrl,
        relayUrls: relayUrls,
        natType: json['nat_type'] as String?,
        hasDirectEndpoint: hasDirectEndpoint,
      );
    } catch (e) {
      return null;
    }
  }

  /// Force refresh peer list.
  Future<void> refresh() async {
    await _pollPeers();
  }

  /// Update registration info (e.g., when role changes).
  Future<void> updateRegistration() async {
    await _register();
  }

  List<String> _buildRelayUrls() {
    final base = _normalizeBaseUrl(relayBaseUrl);
    if (base.isEmpty) return const [];
    return ['$base/p2p/${identity.deviceId}'];
  }

  String _normalizeBaseUrl(String value) {
    final trimmed = value.trim();
    if (trimmed.isEmpty) return '';
    return trimmed.endsWith('/')
        ? trimmed.substring(0, trimmed.length - 1)
        : trimmed;
  }
}
