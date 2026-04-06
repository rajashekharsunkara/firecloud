import 'dart:convert';
import 'dart:typed_data';

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
