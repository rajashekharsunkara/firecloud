import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Device identity manager - generates hardware-bound node identity.
/// Each device can only run ONE node (enforced by hardware fingerprint).
class DeviceIdentity {
  static const _keyDeviceId = 'firecloud_device_id';
  static const _keyPrivateKey = 'firecloud_private_key';
  static const _keyPublicKey = 'firecloud_public_key';

  String? _deviceId;
  Uint8List? _privateKey;
  Uint8List? _publicKey;

  String get deviceId => _deviceId!;
  Uint8List get publicKey => _publicKey!;
  Uint8List get privateKey => _privateKey!;
  String get publicKeyHex => _bytesToHex(_publicKey!);

  bool get isInitialized => _deviceId != null && _privateKey != null;

  /// Initialize device identity - generates or loads existing keys.
  Future<void> initialize() async {
    final prefs = await SharedPreferences.getInstance();

    // Check for existing identity
    _deviceId = prefs.getString(_keyDeviceId);
    final storedPrivate = prefs.getString(_keyPrivateKey);
    final storedPublic = prefs.getString(_keyPublicKey);

    if (_deviceId != null && storedPrivate != null && storedPublic != null) {
      _privateKey = _hexToBytes(storedPrivate);
      _publicKey = _hexToBytes(storedPublic);
      return;
    }

    // Generate new identity
    _deviceId = await _generateDeviceFingerprint();
    final keyPair = _generateKeyPair();
    _privateKey = keyPair.privateKey;
    _publicKey = keyPair.publicKey;

    // Store identity
    await prefs.setString(_keyDeviceId, _deviceId!);
    await prefs.setString(_keyPrivateKey, _bytesToHex(_privateKey!));
    await prefs.setString(_keyPublicKey, _bytesToHex(_publicKey!));
  }

  /// Generate hardware fingerprint from device info.
  Future<String> _generateDeviceFingerprint() async {
    final deviceInfo = DeviceInfoPlugin();
    String fingerprint;

    try {
      final androidInfo = await deviceInfo.androidInfo;
      fingerprint = [
        androidInfo.id,
        androidInfo.device,
        androidInfo.model,
        androidInfo.hardware,
        androidInfo.fingerprint,
      ].join(':');
    } catch (_) {
      try {
        final iosInfo = await deviceInfo.iosInfo;
        fingerprint = [
          iosInfo.identifierForVendor ?? 'unknown',
          iosInfo.model,
          iosInfo.name,
          iosInfo.systemName,
        ].join(':');
      } catch (_) {
        try {
          // Windows support
          final windowsInfo = await deviceInfo.windowsInfo;
          fingerprint = [
            windowsInfo.computerName,
            windowsInfo.deviceId,
            windowsInfo.userName,
            windowsInfo.numberOfCores.toString(),
            windowsInfo.systemMemoryInMegabytes.toString(),
          ].join(':');
        } catch (_) {
          try {
            // Linux support
            final linuxInfo = await deviceInfo.linuxInfo;
            fingerprint = [
              linuxInfo.id,
              linuxInfo.machineId ?? 'unknown',
              linuxInfo.name,
              linuxInfo.prettyName,
            ].join(':');
          } catch (_) {
            try {
              // macOS support
              final macInfo = await deviceInfo.macOsInfo;
              fingerprint = [
                macInfo.computerName,
                macInfo.hostName,
                macInfo.model,
                macInfo.systemGUID ?? 'unknown',
              ].join(':');
            } catch (_) {
              // Fallback for other platforms
              fingerprint = DateTime.now().microsecondsSinceEpoch.toString();
            }
          }
        }
      }
    }

    // Hash the fingerprint
    final bytes = utf8.encode(fingerprint);
    final digest = sha256.convert(bytes);
    return digest.toString().substring(0, 32);
  }

  /// Generate a simple key pair for signing.
  /// Note: In production, use proper Ed25519 from a native library.
  _KeyPair _generateKeyPair() {
    final random = Random.secure();
    final privateKey = Uint8List(32);
    final publicKey = Uint8List(32);
    
    // Generate random private key
    for (var i = 0; i < 32; i++) {
      privateKey[i] = random.nextInt(256);
    }
    
    // Derive public key (simplified - hash of private key)
    final pubDigest = sha256.convert(privateKey);
    for (var i = 0; i < 32; i++) {
      publicKey[i] = pubDigest.bytes[i];
    }
    
    return _KeyPair(privateKey, publicKey);
  }

  /// Sign data with private key (HMAC-based for simplicity).
  Uint8List sign(Uint8List data) {
    final hmac = Hmac(sha256, _privateKey!);
    final digest = hmac.convert(data);
    return Uint8List.fromList(digest.bytes);
  }

  /// Verify signature with public key.
  bool verify(Uint8List data, Uint8List signature, Uint8List publicKey) {
    // Derive expected signature using the public key as HMAC key
    // Note: This is simplified - real implementation would use Ed25519
    final hmac = Hmac(sha256, publicKey);
    final expectedDigest = hmac.convert(data);
    
    if (signature.length != expectedDigest.bytes.length) return false;
    for (var i = 0; i < signature.length; i++) {
      if (signature[i] != expectedDigest.bytes[i]) return false;
    }
    return true;
  }

  String _bytesToHex(Uint8List bytes) {
    return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }

  Uint8List _hexToBytes(String hex) {
    final result = Uint8List(hex.length ~/ 2);
    for (var i = 0; i < result.length; i++) {
      result[i] = int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16);
    }
    return result;
  }
}

class _KeyPair {
  final Uint8List privateKey;
  final Uint8List publicKey;
  _KeyPair(this.privateKey, this.publicKey);
}
