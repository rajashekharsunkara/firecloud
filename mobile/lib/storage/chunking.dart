import 'dart:typed_data';

import 'package:crypto/crypto.dart';

/// FastCDC (Content-Defined Chunking) implementation.
/// Splits files into variable-sized chunks based on content.
/// This ensures deduplication works across similar files.
class FastCDC {
  static const minChunkSize = 64 * 1024;       // 64 KB
  static const avgChunkSize = 1024 * 1024;     // 1 MB
  static const maxChunkSize = 4 * 1024 * 1024; // 4 MB

  // Gear hash lookup table (precomputed random values)
  static final List<int> _gearTable = _generateGearTable();

  /// Chunk a file into content-defined chunks.
  static List<Chunk> chunk(Uint8List data) {
    if (data.isEmpty) return [];
    
    final chunks = <Chunk>[];
    var offset = 0;

    while (offset < data.length) {
      final remaining = data.length - offset;
      
      if (remaining <= minChunkSize) {
        // Last chunk - take everything remaining
        chunks.add(_createChunk(data, offset, remaining));
        break;
      }

      // Find chunk boundary using gear hash
      var chunkSize = _findBoundary(data, offset, remaining);
      chunks.add(_createChunk(data, offset, chunkSize));
      offset += chunkSize;
    }

    return chunks;
  }

  /// Find chunk boundary using gear hash rolling algorithm.
  static int _findBoundary(Uint8List data, int start, int remaining) {
    final maxLen = remaining < maxChunkSize ? remaining : maxChunkSize;
    
    // Skip minimum chunk size before looking for boundary
    if (maxLen <= minChunkSize) return maxLen;

    var hash = 0;
    // Mask that gives ~1MB average chunk size
    const mask = 0x0003590703530000;

    for (var i = minChunkSize; i < maxLen; i++) {
      final byte = data[start + i];
      hash = ((hash << 1) + _gearTable[byte]) & 0xFFFFFFFFFFFFFFFF;
      
      if ((hash & mask) == 0) {
        return i;
      }
    }

    // No boundary found, use max chunk size
    return maxLen;
  }

  /// Create a chunk with its metadata.
  static Chunk _createChunk(Uint8List data, int offset, int length) {
    final chunkData = data.sublist(offset, offset + length);
    final hashDigest = sha256.convert(chunkData);
    
    return Chunk(
      data: chunkData,
      hash: hashDigest.toString(),
      offset: offset,
      size: length,
    );
  }

  /// Generate Gear hash lookup table.
  static List<int> _generateGearTable() {
    // Deterministic PRNG for reproducible chunking
    final table = List<int>.filled(256, 0);
    var seed = 0x5851F42D4C957F2D; // Fixed seed
    
    for (var i = 0; i < 256; i++) {
      // Simple xorshift64
      seed ^= seed << 13;
      seed ^= seed >> 7;
      seed ^= seed << 17;
      table[i] = seed & 0xFFFFFFFFFFFFFFFF;
    }
    
    return table;
  }
}

/// Represents a content-defined chunk.
class Chunk {
  /// The chunk data.
  final Uint8List data;
  
  /// SHA-256 hash of the chunk (used for deduplication).
  final String hash;
  
  /// Offset in original file.
  final int offset;
  
  /// Size in bytes.
  final int size;

  Chunk({
    required this.data,
    required this.hash,
    required this.offset,
    required this.size,
  });

  Map<String, dynamic> toJson() => {
    'hash': hash,
    'offset': offset,
    'size': size,
  };
}

/// File manifest containing chunk references.
class FileManifest {
  final String fileId;
  final String fileName;
  final int fileSize;
  final String fileHash;
  final List<ChunkRef> chunks;
  final DateTime createdAt;
  
  /// Google account UID of the file owner (for cross-device sync).
  final String? ownerId;
  
  /// Device ID that uploaded this file.
  final String? uploaderDeviceId;

  FileManifest({
    required this.fileId,
    required this.fileName,
    required this.fileSize,
    required this.fileHash,
    required this.chunks,
    required this.createdAt,
    this.ownerId,
    this.uploaderDeviceId,
  });

  factory FileManifest.fromJson(Map<String, dynamic> json) {
    return FileManifest(
      fileId: json['file_id'] as String,
      fileName: json['file_name'] as String,
      fileSize: json['file_size'] as int,
      fileHash: json['file_hash'] as String,
      chunks: (json['chunks'] as List)
          .map((c) => ChunkRef.fromJson(c as Map<String, dynamic>))
          .toList(),
      createdAt: DateTime.parse(json['created_at'] as String),
      ownerId: json['owner_id'] as String?,
      uploaderDeviceId: json['uploader_device_id'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'file_id': fileId,
    'file_name': fileName,
    'file_size': fileSize,
    'file_hash': fileHash,
    'chunks': chunks.map((c) => c.toJson()).toList(),
    'created_at': createdAt.toIso8601String(),
    if (ownerId != null) 'owner_id': ownerId,
    if (uploaderDeviceId != null) 'uploader_device_id': uploaderDeviceId,
  };
  
  /// Create a copy with updated fields.
  FileManifest copyWith({
    String? fileId,
    String? fileName,
    int? fileSize,
    String? fileHash,
    List<ChunkRef>? chunks,
    DateTime? createdAt,
    String? ownerId,
    String? uploaderDeviceId,
  }) {
    return FileManifest(
      fileId: fileId ?? this.fileId,
      fileName: fileName ?? this.fileName,
      fileSize: fileSize ?? this.fileSize,
      fileHash: fileHash ?? this.fileHash,
      chunks: chunks ?? this.chunks,
      createdAt: createdAt ?? this.createdAt,
      ownerId: ownerId ?? this.ownerId,
      uploaderDeviceId: uploaderDeviceId ?? this.uploaderDeviceId,
    );
  }
}

/// Reference to a chunk stored on the network.
class ChunkRef {
  /// Hash of the chunk (content address).
  final String hash;
  
  /// Offset in original file.
  final int offset;
  
  /// Size in bytes.
  final int size;
  
  /// Node IDs where this chunk is stored.
  final List<String> nodeIds;

  ChunkRef({
    required this.hash,
    required this.offset,
    required this.size,
    required this.nodeIds,
  });

  factory ChunkRef.fromJson(Map<String, dynamic> json) {
    return ChunkRef(
      hash: json['hash'] as String,
      offset: json['offset'] as int,
      size: json['size'] as int,
      nodeIds: (json['node_ids'] as List).cast<String>(),
    );
  }

  Map<String, dynamic> toJson() => {
    'hash': hash,
    'offset': offset,
    'size': size,
    'node_ids': nodeIds,
  };
}
