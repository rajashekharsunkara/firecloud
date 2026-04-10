import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:disk_space_plus/disk_space_plus.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Storage lock state for providers.
class StorageLockState {
  final int totalDeviceBytes;
  final int freeDeviceBytes;
  final int lockedBytes;
  final int usedBytes;
  final int garbageBytes;
  final bool isLocked;
  final bool isLoading;
  final String? error;

  const StorageLockState({
    this.totalDeviceBytes = 0,
    this.freeDeviceBytes = 0,
    this.lockedBytes = 0,
    this.usedBytes = 0,
    this.garbageBytes = 0,
    this.isLocked = false,
    this.isLoading = false,
    this.error,
  });

  int get availableToLock => freeDeviceBytes > (1024 * 1024 * 1024) // Leave 1GB buffer
      ? freeDeviceBytes - (1024 * 1024 * 1024)
      : 0;

  int get effectiveUsed {
    if (lockedBytes <= 0) return usedBytes;
    return usedBytes.clamp(0, lockedBytes);
  }

  int get freeInLock {
    if (lockedBytes <= 0) return 0;
    return (lockedBytes - effectiveUsed).clamp(0, lockedBytes);
  }

  double get usagePercent {
    if (lockedBytes <= 0) return 0;
    final percent = (effectiveUsed / lockedBytes) * 100;
    return percent.clamp(0, 100).toDouble();
  }

  /// Get recommended storage amount based on device free space.
  int get recommendedBytes {
    if (freeDeviceBytes < 5 * 1024 * 1024 * 1024) {
      // Less than 5GB free: recommend nothing
      return 0;
    } else if (freeDeviceBytes < 20 * 1024 * 1024 * 1024) {
      // 5-20GB free: recommend 2GB
      return 2 * 1024 * 1024 * 1024;
    } else if (freeDeviceBytes < 50 * 1024 * 1024 * 1024) {
      // 20-50GB free: recommend 10GB
      return 10 * 1024 * 1024 * 1024;
    } else if (freeDeviceBytes < 100 * 1024 * 1024 * 1024) {
      // 50-100GB free: recommend 25GB
      return 25 * 1024 * 1024 * 1024;
    } else {
      // 100GB+ free: recommend 50GB
      return 50 * 1024 * 1024 * 1024;
    }
  }

  String get recommendationText {
    final recGB = recommendedBytes / (1024 * 1024 * 1024);
    final freeGB = freeDeviceBytes / (1024 * 1024 * 1024);
    
    if (recommendedBytes == 0) {
      return 'Not enough free space. You have ${freeGB.toStringAsFixed(1)} GB free.';
    }
    return 'Recommended: ${recGB.toStringAsFixed(0)} GB (you have ${freeGB.toStringAsFixed(1)} GB free)';
  }

  StorageLockState copyWith({
    int? totalDeviceBytes,
    int? freeDeviceBytes,
    int? lockedBytes,
    int? usedBytes,
    int? garbageBytes,
    bool? isLocked,
    bool? isLoading,
    String? error,
  }) {
    return StorageLockState(
      totalDeviceBytes: totalDeviceBytes ?? this.totalDeviceBytes,
      freeDeviceBytes: freeDeviceBytes ?? this.freeDeviceBytes,
      lockedBytes: lockedBytes ?? this.lockedBytes,
      usedBytes: usedBytes ?? this.usedBytes,
      garbageBytes: garbageBytes ?? this.garbageBytes,
      isLocked: isLocked ?? this.isLocked,
      isLoading: isLoading ?? this.isLoading,
      error: error,
    );
  }
}

/// Manages storage locking and garbage fill for providers.
class StorageLockNotifier extends StateNotifier<StorageLockState> {
  final SharedPreferences _prefs;
  Directory? _garbageDir;

  static const _lockedKey = 'firecloud_locked_bytes';
  static const _isLockedKey = 'firecloud_is_locked';

  StorageLockNotifier(this._prefs) : super(const StorageLockState(isLoading: true)) {
    _initialize();
  }

  Future<void> _initialize() async {
    // Get storage directories
    final appDir = await getApplicationDocumentsDirectory();
    _garbageDir = Directory('${appDir.path}/firecloud/garbage');
    await _garbageDir!.create(recursive: true);

    // Load saved state
    final lockedBytes = _prefs.getInt(_lockedKey) ?? 0;
    final isLocked = _prefs.getBool(_isLockedKey) ?? false;

    // Calculate garbage size
    final garbageBytes = await _calculateGarbageSize();

    // Refresh device storage info
    await refreshDeviceStorage();

    state = state.copyWith(
      lockedBytes: lockedBytes,
      isLocked: isLocked,
      garbageBytes: garbageBytes,
      usedBytes: 0,
      isLoading: false,
    );
  }

  /// Refresh device storage information.
  Future<void> refreshDeviceStorage() async {
    try {
      final diskSpace = DiskSpacePlus();
      final freeSpace = await diskSpace.getFreeDiskSpace ?? 0;
      final totalSpace = await diskSpace.getTotalDiskSpace ?? 0;

      state = state.copyWith(
        freeDeviceBytes: (freeSpace * 1024 * 1024).toInt(),
        totalDeviceBytes: (totalSpace * 1024 * 1024).toInt(),
      );
    } catch (e) {
      // Fallback: use default estimates
      try {
        await getApplicationDocumentsDirectory();
        // Use a default estimate
        state = state.copyWith(
          freeDeviceBytes: 10 * 1024 * 1024 * 1024, // 10GB default
          totalDeviceBytes: 64 * 1024 * 1024 * 1024, // 64GB default
        );
      } catch (_) {
        state = state.copyWith(error: 'Could not detect storage capacity');
      }
    }
  }

  Future<void> refreshUsage({required int usedBytes}) async {
    final normalizedUsed = usedBytes < 0 ? 0 : usedBytes;
    final garbageBytes = await _calculateGarbageSize();
    state = state.copyWith(
      usedBytes: normalizedUsed,
      garbageBytes: garbageBytes,
      error: null,
    );
  }

  /// Lock a specific amount of storage.
  /// Creates garbage files to reserve the space.
  Future<bool> lockStorage(int bytes) async {
    if (bytes > state.availableToLock) {
      state = state.copyWith(error: 'Not enough free space to lock ${_formatBytes(bytes)}');
      return false;
    }

    if (bytes < 1024 * 1024 * 100) { // Minimum 100MB
      state = state.copyWith(error: 'Minimum lock amount is 100 MB');
      return false;
    }

    state = state.copyWith(isLoading: true, error: null);

    try {
      // Fill with garbage to reserve space
      await _fillWithGarbage(bytes);

      // Persist lock state only after reserve succeeds.
      await _prefs.setInt(_lockedKey, bytes);
      await _prefs.setBool(_isLockedKey, true);

      state = state.copyWith(
        lockedBytes: bytes,
        isLocked: true,
        usedBytes: 0,
        isLoading: false,
      );
      return true;
    } catch (e) {
      await _clearGarbage();
      await _prefs.remove(_lockedKey);
      await _prefs.setBool(_isLockedKey, false);
      state = state.copyWith(
        isLoading: false,
        error: 'Failed to lock storage: $e',
      );
      return false;
    }
  }

  /// Unlock storage and clear garbage.
  Future<bool> unlockStorage() async {
    if (state.usedBytes > 0) {
      state = state.copyWith(
        error: 'Cannot unlock: ${_formatBytes(state.usedBytes)} of real data stored. '
            'Transfer data to other nodes first.',
      );
      return false;
    }

    state = state.copyWith(isLoading: true, error: null);

    try {
      // Clear garbage files
      await _clearGarbage();

      // Clear lock settings
      await _prefs.remove(_lockedKey);
      await _prefs.setBool(_isLockedKey, false);

      state = state.copyWith(
        lockedBytes: 0,
        isLocked: false,
        garbageBytes: 0,
        usedBytes: 0,
        isLoading: false,
      );

      // Refresh device storage
      await refreshDeviceStorage();
      return true;
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: 'Failed to unlock storage: $e',
      );
      return false;
    }
  }

  /// Fill locked space with garbage files.
  Future<void> _fillWithGarbage(int totalBytes) async {
    if (_garbageDir == null) return;

    // Clear existing garbage first
    await _clearGarbage();

    // Create garbage files in bounded chunks to avoid UI stalls/OOM.
    const fileSize = 64 * 1024 * 1024; // 64MB files
    const writeChunk = 1024 * 1024; // 1MB writes
    final random = Random();
    final block = Uint8List(writeChunk);
    var remaining = totalBytes;
    var fileIndex = 0;
    var written = 0;

    while (remaining > 0) {
      final size = remaining > fileSize ? fileSize : remaining;
      final file = File('${_garbageDir!.path}/garbage_$fileIndex.bin');
      final sink = file.openWrite();
      var fileRemaining = size;
      while (fileRemaining > 0) {
        final current = fileRemaining > writeChunk ? writeChunk : fileRemaining;
        for (var i = 0; i < current; i++) {
          block[i] = random.nextInt(256);
        }
        sink.add(block.sublist(0, current));
        fileRemaining -= current;
        written += current;

        if (written % (8 * writeChunk) == 0) {
          state = state.copyWith(garbageBytes: written);
          await Future<void>.delayed(Duration.zero);
        }
      }
      await sink.flush();
      await sink.close();

      remaining -= size;
      fileIndex++;

      // Update progress
      state = state.copyWith(
        garbageBytes: written,
      );
      await Future<void>.delayed(Duration.zero);
    }
  }

  /// Clear all garbage files.
  Future<void> _clearGarbage() async {
    if (_garbageDir == null) return;

    if (await _garbageDir!.exists()) {
      await for (final entity in _garbageDir!.list()) {
        if (entity is File && entity.path.contains('garbage_')) {
          await entity.delete();
        }
      }
    }

    state = state.copyWith(garbageBytes: 0);
  }

  /// Release garbage space for real data.
  /// Called when consumers need to store chunks.
  Future<int> releaseGarbageForData(int bytesNeeded) async {
    if (_garbageDir == null) return 0;

    var released = 0;
    final files = <File>[];

    await for (final entity in _garbageDir!.list()) {
      if (entity is File && entity.path.contains('garbage_')) {
        files.add(entity);
      }
    }

    // Sort by name (oldest first)
    files.sort((a, b) => a.path.compareTo(b.path));

    for (final file in files) {
      if (released >= bytesNeeded) break;
      
      final size = await file.length();
      await file.delete();
      released += size;
    }

    state = state.copyWith(
      garbageBytes: state.garbageBytes - released,
    );

    return released;
  }

  /// Update used storage (real data).
  void updateUsedStorage(int bytes) {
    state = state.copyWith(usedBytes: bytes);
  }

  /// Calculate total garbage file size.
  Future<int> _calculateGarbageSize() async {
    if (_garbageDir == null || !await _garbageDir!.exists()) return 0;

    var total = 0;
    await for (final entity in _garbageDir!.list()) {
      if (entity is File && entity.path.contains('garbage_')) {
        total += await entity.length();
      }
    }
    return total;
  }

  String _formatBytes(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
    return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
  }
}

/// Provider for storage lock state.
final storageLockProvider = StateNotifierProvider<StorageLockNotifier, StorageLockState>((ref) {
  final prefs = ref.watch(sharedPreferencesProvider);
  return StorageLockNotifier(prefs);
});

/// Provider for SharedPreferences.
final sharedPreferencesProvider = Provider<SharedPreferences>((ref) {
  throw UnimplementedError('sharedPreferencesProvider must be overridden');
});
