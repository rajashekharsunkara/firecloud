import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

/// Simple XOR-based encryption for chunks.
/// Note: In production, use proper XChaCha20-Poly1305 from a native library.
class ChunkEncryption {
  /// Encrypt data using XOR stream cipher.
  /// Returns encrypted data with nonce prepended.
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

    // Prepend nonce to ciphertext
    final result = Uint8List(nonce.length + ciphertext.length);
    result.setAll(0, nonce);
    result.setAll(nonce.length, ciphertext);

    return result;
  }

  /// Decrypt data.
  /// Expects nonce prepended to ciphertext.
  static Uint8List decrypt(Uint8List ciphertext, Uint8List key) {
    if (key.length != 32) {
      throw ArgumentError('Key must be 32 bytes');
    }
    if (ciphertext.length < 24) {
      throw ArgumentError('Ciphertext too short (missing nonce)');
    }

    // Extract nonce and encrypted data
    final nonce = ciphertext.sublist(0, 24);
    final encrypted = ciphertext.sublist(24);

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
    final nonce = Uint8List(24);
    for (var i = 0; i < 24; i++) {
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
