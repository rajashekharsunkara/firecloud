import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_background/flutter_background.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../node/node_role.dart';

/// Background execution service for provider nodes.
/// On Android: uses foreground service with persistent notification + wake locks.
/// On Windows: uses system tray (app stays alive when minimized to tray).
/// On other platforms: no-op (runs only while app is visible).
class BackgroundNodeService {
  static bool get isAndroidSupported => Platform.isAndroid;
  static bool get isWindowsSupported => Platform.isWindows;
  static bool get isSupported => isAndroidSupported || isWindowsSupported;
  
  static bool _windowsBackgroundEnabled = false;

  static Future<bool> isRunning() async {
    if (isAndroidSupported) {
      return FlutterBackground.isBackgroundExecutionEnabled;
    }
    if (isWindowsSupported) {
      return _windowsBackgroundEnabled;
    }
    return false;
  }

  static Future<void> syncWithRole({
    required NodeRole role,
    required bool isStorageLocked,
  }) async {
    final shouldRun = role == NodeRole.storageProvider && isStorageLocked;
    
    if (isAndroidSupported) {
      await _syncAndroid(shouldRun);
    } else if (isWindowsSupported) {
      await _syncWindows(shouldRun);
    }
  }
  
  static Future<void> _syncAndroid(bool shouldRun) async {
    try {
      final running = FlutterBackground.isBackgroundExecutionEnabled;
      if (shouldRun == running) return;
      
      if (shouldRun) {
        // Initialize and enable background execution
        final initialized = await FlutterBackground.initialize(
          androidConfig: const FlutterBackgroundAndroidConfig(
            notificationTitle: 'FireCloud provider active',
            notificationText: 'Storage node stays online for decentralized transfers.',
            notificationImportance: AndroidNotificationImportance.normal,
            notificationIcon: AndroidResource(name: 'ic_launcher', defType: 'mipmap'),
            enableWifiLock: true,
          ),
        );
        if (!initialized) {
          throw StateError('Failed to initialize Android background execution');
        }
        final enabled = await FlutterBackground.enableBackgroundExecution();
        if (!enabled) {
          throw StateError('Failed to enable Android background execution');
        }
        
        // Enable CPU wake lock to prevent Doze from killing network ops
        await WakelockPlus.enable();
      } else {
        await FlutterBackground.disableBackgroundExecution();
        await WakelockPlus.disable();
      }
    } catch (e) {
      // Log but don't crash - background is optional enhancement
      debugPrint('BackgroundNodeService Android error: $e');
    }
  }
  
  static Future<void> _syncWindows(bool shouldRun) async {
    try {
      _windowsBackgroundEnabled = shouldRun;
      
      // On Windows, wakelock keeps screen on (prevents sleep)
      if (shouldRun) {
        await WakelockPlus.enable();
      } else {
        await WakelockPlus.disable();
      }
    } catch (e) {
      debugPrint('BackgroundNodeService Windows error: $e');
    }
  }
}
