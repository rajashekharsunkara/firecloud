import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:firecloud_mobile/node/node_role.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  group('NodeRoleManager', () {
    test('loads default consumer role and zero quotas', () async {
      final manager = NodeRoleManager();
      await manager.load();

      expect(manager.role, equals(NodeRole.consumer));
      expect(manager.storageQuotaBytes, equals(0));
      expect(manager.usedStorageBytes, equals(0));
      expect(manager.availableStorageBytes, equals(0));
      expect(manager.usagePercent, equals(0));
    });

    test('persists role changes', () async {
      final manager = NodeRoleManager();
      await manager.load();
      await manager.setRole(NodeRole.storageProvider);

      final reloaded = NodeRoleManager();
      await reloaded.load();
      expect(reloaded.role, equals(NodeRole.storageProvider));
    });

    test('cannot switch provider to consumer while storing data', () async {
      final manager = NodeRoleManager();
      await manager.load();
      await manager.setRole(NodeRole.storageProvider);
      await manager.setStorageQuota(1024);
      await manager.updateUsedStorage(1);

      expect(
        () => manager.setRole(NodeRole.consumer),
        throwsA(isA<StateError>()),
      );
    });

    test('cannot set quota below used bytes', () async {
      final manager = NodeRoleManager();
      await manager.load();
      await manager.setRole(NodeRole.storageProvider);
      await manager.setStorageQuota(4096);
      await manager.updateUsedStorage(2048);

      expect(
        () => manager.setStorageQuota(1024),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('canStore depends on role and available quota', () async {
      final manager = NodeRoleManager();
      await manager.load();

      expect(manager.canStore(1), isFalse);

      await manager.setRole(NodeRole.storageProvider);
      await manager.setStorageQuota(1024);
      await manager.updateUsedStorage(100);

      expect(manager.canStore(800), isTrue);
      expect(manager.canStore(1000), isFalse);
    });
  });
}

