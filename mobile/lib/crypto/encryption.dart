import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

/// Simple XOR-based encryption for chunks.
/// Note: In production, use proper XChaCha20-Poly1305 from a native library.
class ChunkEncryption {
  static const int _versionLength = 1;
  static const int _nonceLength = 24;
  static const int _hmacLength = 32;
  static const int _versionedFormat = 1;

  /// Encrypt data using XOR stream cipher.
  /// Returns encrypted data as [version (1)] [nonce (24)] [ciphertext (N)] [hmac (32)].
  static Uint8List encrypt(Uint8List plaintext, Uint8List key) {
    if (key.length != 32) {
      throw ArgumentError('Key must be 32 bytes');
    }

    // Generate random 24-byte nonce
    final nonce = _generateNonce();

    // Generate keystream
    final keystream = _generateKeystream(key, nonce, plaintext.length);

    // XOR plaintext with keystream
    final ciphertext = Uint8List(plaintext.length);
    for (var i = 0; i < plaintext.length; i++) {
      ciphertext[i] = plaintext[i] ^ keystream[i];
    }

    final hmacTag = _computeHmacTag(
      key,
      nonce,
      ciphertext,
      version: _versionedFormat,
    );

    // Wire format: [version] [nonce] [ciphertext] [hmac]
    final result = Uint8List(
      _versionLength + nonce.length + ciphertext.length + hmacTag.length,
    );
    result[0] = _versionedFormat;
    result.setAll(_versionLength, nonce);
    result.setAll(_versionLength + nonce.length, ciphertext);
    result.setAll(_versionLength + nonce.length + ciphertext.length, hmacTag);

    return result;
  }

  /// Decrypt data.
  /// Expects either:
  /// - Current format: [version] [nonce] [ciphertext] [hmac]
  /// - Legacy formats: [nonce] [ciphertext] or [nonce] [ciphertext] [hmac]
  ///
  /// Current versioned payloads require a valid HMAC tag.
  /// Legacy payloads without HMAC are still supported.
  static Uint8List decrypt(Uint8List ciphertext, Uint8List key) {
    if (key.length != 32) {
      throw ArgumentError('Key must be 32 bytes');
    }
    if (ciphertext.isEmpty) {
      throw ArgumentError('Ciphertext is empty');
    }

    late final Uint8List nonce;
    Uint8List encrypted;

    if (ciphertext[0] == _versionedFormat) {
      final minLength = _versionLength + _nonceLength + _hmacLength;
      if (ciphertext.length < minLength) {
        throw ArgumentError('Ciphertext too short for authenticated payload');
      }

      final nonceStart = _versionLength;
      final nonceEnd = nonceStart + _nonceLength;
      final encryptedEnd = ciphertext.length - _hmacLength;
      nonce = ciphertext.sublist(nonceStart, nonceEnd);
      encrypted = ciphertext.sublist(nonceEnd, encryptedEnd);
      final providedTag = ciphertext.sublist(encryptedEnd);
      final expectedTag = _computeHmacTag(
        key,
        nonce,
        encrypted,
        version: _versionedFormat,
      );
      if (!_constantTimeEquals(providedTag, expectedTag)) {
        throw ArgumentError('Ciphertext authentication failed');
      }
    } else {
      if (ciphertext.length < _nonceLength) {
        throw ArgumentError('Ciphertext too short (missing nonce)');
      }

      // Legacy mode:
      // - [nonce][ciphertext]
      // - [nonce][ciphertext][hmac] (pre-version compatibility)
      nonce = ciphertext.sublist(0, _nonceLength);
      encrypted = ciphertext.sublist(_nonceLength);
      if (ciphertext.length >= _nonceLength + _hmacLength) {
        final encryptedEnd = ciphertext.length - _hmacLength;
        final candidateEncrypted = ciphertext.sublist(_nonceLength, encryptedEnd);
        final providedTag = ciphertext.sublist(encryptedEnd);
        final expectedTag = _computeHmacTag(key, nonce, candidateEncrypted);
        if (_constantTimeEquals(providedTag, expectedTag)) {
          encrypted = candidateEncrypted;
        }
      }
    }

    // Generate keystream
    final keystream = _generateKeystream(key, nonce, encrypted.length);

    // XOR ciphertext with keystream
    final plaintext = Uint8List(encrypted.length);
    for (var i = 0; i < encrypted.length; i++) {
      plaintext[i] = encrypted[i] ^ keystream[i];
    }

    return plaintext;
  }

  /// Generate keystream using key and nonce.
  static Uint8List _generateKeystream(Uint8List key, Uint8List nonce, int length) {
    final stream = Uint8List(length);
    var offset = 0;
    var counter = 0;
    
    while (offset < length) {
      // Create block input: key + nonce + counter
      final blockInput = Uint8List(key.length + nonce.length + 4);
      blockInput.setAll(0, key);
      blockInput.setAll(key.length, nonce);
      blockInput[key.length + nonce.length] = counter & 0xFF;
      blockInput[key.length + nonce.length + 1] = (counter >> 8) & 0xFF;
      blockInput[key.length + nonce.length + 2] = (counter >> 16) & 0xFF;
      blockInput[key.length + nonce.length + 3] = (counter >> 24) & 0xFF;
      
      // Hash to get keystream block
      final digest = sha256.convert(blockInput);
      
      // Copy block to stream
      for (var i = 0; i < 32 && offset < length; i++, offset++) {
        stream[offset] = digest.bytes[i];
      }
      
      counter++;
    }
    
    return stream;
  }

  static Uint8List _computeHmacTag(
    Uint8List key,
    Uint8List nonce,
    Uint8List ciphertext,
    {int? version}
  ) {
    final hmac = Hmac(sha256, key);
    final input = <int>[];
    if (version != null) {
      input.add(version);
    }
    input.addAll(nonce);
    input.addAll(ciphertext);
    final digest = hmac.convert(input);
    return Uint8List.fromList(digest.bytes);
  }

  static bool _constantTimeEquals(Uint8List a, Uint8List b) {
    if (a.length != b.length) {
      return false;
    }
    var diff = 0;
    for (var i = 0; i < a.length; i++) {
      diff |= a[i] ^ b[i];
    }
    return diff == 0;
  }

  /// Derive encryption key from password and salt.
  static Uint8List deriveKey(String password, Uint8List salt) {
    final combined = Uint8List.fromList([
      ...utf8.encode(password),
      ...salt,
    ]);
    
    final digest = sha256.convert(combined);
    return Uint8List.fromList(digest.bytes);
  }

  /// Generate random nonce (24 bytes).
  static Uint8List _generateNonce() {
    final random = Random.secure();
    final nonce = Uint8List(_nonceLength);
    for (var i = 0; i < _nonceLength; i++) {
      nonce[i] = random.nextInt(256);
    }
    return nonce;
  }

  /// Generate random encryption key (32 bytes).
  static Uint8List generateKey() {
    final random = Random.secure();
    final key = Uint8List(32);
    for (var i = 0; i < 32; i++) {
      key[i] = random.nextInt(256);
    }
    return key;
  }
}

/// Manifest encryption helper for account-scoped metadata sync.
class ManifestEncryption {
  static Uint8List deriveAccountKey(String ownerId) {
    return ChunkEncryption.deriveKey(
      ownerId,
      Uint8List.fromList(utf8.encode('firecloud-manifest-salt-v1')),
    );
  }

  static String encryptManifestJson({
    required String manifestJson,
    required String ownerId,
  }) {
    final key = deriveAccountKey(ownerId);
    final ciphertext = ChunkEncryption.encrypt(
      Uint8List.fromList(utf8.encode(manifestJson)),
      key,
    );
    return base64Encode(ciphertext);
  }

  static String decryptManifestJson({
    required String encryptedBase64,
    required String ownerId,
  }) {
    final key = deriveAccountKey(ownerId);
    final plaintext = ChunkEncryption.decrypt(
      Uint8List.fromList(base64Decode(encryptedBase64)),
      key,
    );
    return utf8.decode(plaintext);
  }
}

/// Content hashing for deduplication.
class ContentHash {
  /// Compute hash of data.
  static String hash(Uint8List data) {
    final digest = sha256.convert(data);
    return digest.toString();
  }

  /// Verify data matches expected hash.
  static bool verify(Uint8List data, String expectedHash) {
    return hash(data) == expectedHash;
  }
}
