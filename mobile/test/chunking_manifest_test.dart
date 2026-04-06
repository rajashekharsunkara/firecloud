import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:firecloud_mobile/storage/chunking.dart';
import 'package:firecloud_mobile/crypto/encryption.dart';

void main() {
  group('FastCDC', () {
    test('returns empty chunks for empty input', () {
      final chunks = FastCDC.chunk(Uint8List(0));
      expect(chunks, isEmpty);
    });

    test('returns one chunk for small payload', () {
      final data = Uint8List.fromList(List<int>.generate(1024, (i) => i % 256));
      final chunks = FastCDC.chunk(data);

      expect(chunks.length, equals(1));
      expect(chunks.first.offset, equals(0));
      expect(chunks.first.size, equals(data.length));
      expect(chunks.first.data, equals(data));
    });

    test('chunk sequence reconstructs original data', () {
      final data = Uint8List.fromList(
        List<int>.generate(6 * 1024 * 1024 + 123, (i) => (i * 31) % 256),
      );
      final chunks = FastCDC.chunk(data);

      expect(chunks, isNotEmpty);
      final reconstructed = Uint8List(data.length);
      var expectedOffset = 0;

      for (final chunk in chunks) {
        expect(chunk.offset, equals(expectedOffset));
        reconstructed.setRange(
          chunk.offset,
          chunk.offset + chunk.size,
          chunk.data,
        );
        expectedOffset += chunk.size;
      }

      expect(expectedOffset, equals(data.length));
      expect(reconstructed, equals(data));
    });
  });

  group('Manifest models', () {
    test('FileManifest JSON roundtrip preserves optional owner metadata', () {
      final manifest = FileManifest(
        fileId: 'file-1',
        fileName: 'hello.txt',
        fileSize: 42,
        fileHash: 'abc123',
        chunks: [
          ChunkRef(hash: 'h1', offset: 0, size: 21, nodeIds: const ['n1', 'n2']),
          ChunkRef(hash: 'h2', offset: 21, size: 21, nodeIds: const ['n2']),
        ],
        createdAt: DateTime.parse('2026-04-01T12:00:00Z'),
        ownerId: 'owner-xyz',
        uploaderDeviceId: 'device-1',
      );

      final decoded = FileManifest.fromJson(manifest.toJson());
      expect(decoded.fileId, equals(manifest.fileId));
      expect(decoded.ownerId, equals('owner-xyz'));
      expect(decoded.uploaderDeviceId, equals('device-1'));
      expect(decoded.chunks.length, equals(2));
      expect(decoded.chunks.first.nodeIds, equals(['n1', 'n2']));
    });

    test('copyWith updates selected fields only', () {
      final original = FileManifest(
        fileId: 'file-1',
        fileName: 'a.txt',
        fileSize: 10,
        fileHash: 'hash-a',
        chunks: [ChunkRef(hash: 'h', offset: 0, size: 10, nodeIds: const ['n1'])],
        createdAt: DateTime.parse('2026-04-01T12:00:00Z'),
        ownerId: 'owner-a',
        uploaderDeviceId: 'dev-a',
      );

      final updated = original.copyWith(
        fileName: 'b.txt',
        ownerId: 'owner-b',
      );

      expect(updated.fileId, equals('file-1'));
      expect(updated.fileName, equals('b.txt'));
      expect(updated.ownerId, equals('owner-b'));
      expect(updated.uploaderDeviceId, equals('dev-a'));
      expect(updated.chunks.single.hash, equals('h'));
    });
  });

  group('ContentHash', () {
    test('verify returns true for matching data/hash', () {
      final data = Uint8List.fromList([1, 2, 3, 4]);
      final hash = ContentHash.hash(data);
      expect(ContentHash.verify(data, hash), isTrue);
    });

    test('verify returns false for mismatched data/hash', () {
      final hash = ContentHash.hash(Uint8List.fromList([1, 2, 3]));
      expect(ContentHash.verify(Uint8List.fromList([1, 2, 4]), hash), isFalse);
    });
  });
}

