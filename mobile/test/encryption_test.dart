import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:firecloud_mobile/crypto/encryption.dart';

void main() {
  group('ChunkEncryption', () {
    test('encrypt/decrypt roundtrip preserves data', () {
      final key = Uint8List.fromList(List<int>.generate(32, (i) => i));
      final plaintext = Uint8List.fromList(utf8.encode('firecloud roundtrip'));

      final ciphertext = ChunkEncryption.encrypt(plaintext, key);
      final decrypted = ChunkEncryption.decrypt(ciphertext, key);

      expect(decrypted, equals(plaintext));
    });

    test('encrypted payload includes version, nonce, and hmac tag', () {
      final key = Uint8List.fromList(List<int>.generate(32, (i) => 255 - i));
      final plaintext = Uint8List.fromList(utf8.encode('format-check'));

      final ciphertext = ChunkEncryption.encrypt(plaintext, key);

      expect(ciphertext.length, equals(1 + 24 + plaintext.length + 32));
    });

    test('decrypt rejects tampered authenticated payloads', () {
      final key = Uint8List.fromList(List<int>.generate(32, (i) => i + 10));
      final plaintext = Uint8List.fromList(utf8.encode('auth-required'));
      final ciphertext = ChunkEncryption.encrypt(plaintext, key);

      ciphertext[ciphertext.length - 1] ^= 0x01;

      expect(
        () => ChunkEncryption.decrypt(ciphertext, key),
        throwsArgumentError,
      );
    });

    test('decrypt supports legacy payloads without hmac tag', () {
      final key = Uint8List.fromList(List<int>.generate(32, (i) => i + 1));
      final nonce = Uint8List.fromList(List<int>.filled(24, 7));
      final plaintext = Uint8List.fromList(
        utf8.encode('legacy payload compatibility'),
      );
      final legacyCiphertext = _encryptLegacyForTest(plaintext, key, nonce);

      final decrypted = ChunkEncryption.decrypt(legacyCiphertext, key);

      expect(decrypted, equals(plaintext));
    });

    test('encrypt rejects invalid key length', () {
      final invalidKey = Uint8List.fromList([1, 2, 3]);
      final plaintext = Uint8List.fromList([4, 5, 6]);

      expect(
        () => ChunkEncryption.encrypt(plaintext, invalidKey),
        throwsArgumentError,
      );
    });
  });

  group('ManifestEncryption', () {
    test('manifest encrypt/decrypt roundtrip for same owner', () {
      const ownerId = 'owner-123';
      const manifestJson = '{"file_id":"abc","owner_id":"owner-123"}';

      final encrypted = ManifestEncryption.encryptManifestJson(
        manifestJson: manifestJson,
        ownerId: ownerId,
      );
      final decrypted = ManifestEncryption.decryptManifestJson(
        encryptedBase64: encrypted,
        ownerId: ownerId,
      );

      expect(decrypted, equals(manifestJson));
    });

    test('account key derivation is stable and owner-specific', () {
      final keyA1 = ManifestEncryption.deriveAccountKey('owner-A');
      final keyA2 = ManifestEncryption.deriveAccountKey('owner-A');
      final keyB = ManifestEncryption.deriveAccountKey('owner-B');

      expect(keyA1, equals(keyA2));
      expect(keyA1, isNot(equals(keyB)));
    });
  });
}

Uint8List _encryptLegacyForTest(
  Uint8List plaintext,
  Uint8List key,
  Uint8List nonce,
) {
  final keystream = Uint8List(plaintext.length);
  var offset = 0;
  var counter = 0;

  while (offset < plaintext.length) {
    final blockInput = Uint8List(key.length + nonce.length + 4);
    blockInput.setAll(0, key);
    blockInput.setAll(key.length, nonce);
    blockInput[key.length + nonce.length] = counter & 0xFF;
    blockInput[key.length + nonce.length + 1] = (counter >> 8) & 0xFF;
    blockInput[key.length + nonce.length + 2] = (counter >> 16) & 0xFF;
    blockInput[key.length + nonce.length + 3] = (counter >> 24) & 0xFF;

    final digest = sha256.convert(blockInput).bytes;
    for (var i = 0; i < 32 && offset < plaintext.length; i++, offset++) {
      keystream[offset] = digest[i];
    }
    counter++;
  }

  final encrypted = Uint8List(plaintext.length);
  for (var i = 0; i < plaintext.length; i++) {
    encrypted[i] = plaintext[i] ^ keystream[i];
  }

  final result = Uint8List(nonce.length + encrypted.length);
  result.setAll(0, nonce);
  result.setAll(nonce.length, encrypted);
  return result;
}
