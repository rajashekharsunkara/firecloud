import 'package:shared_preferences/shared_preferences.dart';

/// Node roles in the FireCloud network.
enum NodeRole {
  /// Storage Provider - stores chunks for others, earns credits.
  storageProvider,
  
  /// Consumer - uses network storage, doesn't provide storage.
  consumer,
}

/// Node role configuration and management.
class NodeRoleManager {
  static const _keyRole = 'firecloud_node_role';
  static const _keyStorageQuota = 'firecloud_storage_quota';
  static const _keyUsedStorage = 'firecloud_used_storage';

  NodeRole _role = NodeRole.consumer;
  int _storageQuotaBytes = 0;
  int _usedStorageBytes = 0;

  NodeRole get role => _role;
  int get storageQuotaBytes => _storageQuotaBytes;
  int get usedStorageBytes => _usedStorageBytes;
  int get availableStorageBytes => _storageQuotaBytes - _usedStorageBytes;
  double get usagePercent => _storageQuotaBytes > 0 
      ? (_usedStorageBytes / _storageQuotaBytes * 100) 
      : 0;
  
  bool get isStorageProvider => _role == NodeRole.storageProvider;
  bool get isConsumer => _role == NodeRole.consumer;

  /// Load role configuration from storage.
  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    
    final roleStr = prefs.getString(_keyRole);
    _role = roleStr == 'storage_provider' 
        ? NodeRole.storageProvider 
        : NodeRole.consumer;
    
    _storageQuotaBytes = prefs.getInt(_keyStorageQuota) ?? 0;
    _usedStorageBytes = prefs.getInt(_keyUsedStorage) ?? 0;
  }

  /// Set node role.
  /// When switching FROM storage provider, must transfer data first.
  Future<void> setRole(NodeRole newRole) async {
    if (_role == NodeRole.storageProvider && newRole == NodeRole.consumer) {
      // Must transfer stored chunks to other nodes first
      if (_usedStorageBytes > 0) {
        throw StateError(
          'Cannot switch to consumer while storing ${_formatBytes(_usedStorageBytes)} of data. '
          'Transfer data to other nodes first.',
        );
      }
    }
    
    _role = newRole;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyRole, newRole == NodeRole.storageProvider 
        ? 'storage_provider' 
        : 'consumer');
  }

  /// Set storage quota (only for storage providers).
  Future<void> setStorageQuota(int bytes) async {
    if (bytes < _usedStorageBytes) {
      throw ArgumentError(
        'Cannot set quota below used storage (${_formatBytes(_usedStorageBytes)})',
      );
    }
    
    _storageQuotaBytes = bytes;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_keyStorageQuota, bytes);
  }

  /// Update used storage.
  Future<void> updateUsedStorage(int bytes) async {
    _usedStorageBytes = bytes;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_keyUsedStorage, bytes);
  }

  /// Check if we can store additional bytes.
  bool canStore(int additionalBytes) {
    if (!isStorageProvider) return false;
    return (_usedStorageBytes + additionalBytes) <= _storageQuotaBytes;
  }

  String _formatBytes(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
    }
    return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
  }
}
