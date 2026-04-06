import 'dart:io';

import 'package:flutter/material.dart';
import 'package:system_tray/system_tray.dart';
import 'package:window_manager/window_manager.dart';

/// Service for Windows system tray integration.
/// Allows the app to minimize to tray and run in background.
class WindowsTrayService {
  static bool get isSupported => Platform.isWindows || Platform.isLinux || Platform.isMacOS;
  
  static SystemTray? _systemTray;
  static AppWindow? _appWindow;
  static bool _isInitialized = false;
  static VoidCallback? _onShowWindow;
  static VoidCallback? _onQuit;
  
  /// Initialize the system tray.
  static Future<void> initialize({
    VoidCallback? onShowWindow,
    VoidCallback? onQuit,
  }) async {
    if (!isSupported || _isInitialized) return;
    
    _onShowWindow = onShowWindow;
    _onQuit = onQuit;
    
    _systemTray = SystemTray();
    _appWindow = AppWindow();
    
    // Initialize tray icon
    String iconPath;
    if (Platform.isWindows) {
      iconPath = 'assets/app_icon.ico';
    } else if (Platform.isMacOS) {
      iconPath = 'assets/app_icon.png';
    } else {
      iconPath = 'assets/app_icon.png';
    }
    
    await _systemTray!.initSystemTray(
      title: 'FireCloud',
      iconPath: iconPath,
    );
    
    // Setup menu
    final menu = <MenuItemBase>[
      MenuItem(
        label: 'Show FireCloud',
        onClicked: _handleShow,
      ),
      MenuSeparator(),
      MenuItem(
        label: 'Storage Provider Active',
        enabled: false,
      ),
      MenuSeparator(),
      MenuItem(
        label: 'Quit',
        onClicked: _handleQuit,
      ),
    ];
    
    await _systemTray!.setContextMenu(menu);
    
    // Handle tray icon click
    _systemTray!.registerSystemTrayEventHandler((eventName) {
      if (eventName == 'leftMouseUp' || eventName == 'double-click') {
        _handleShow();
      }
    });
    
    _isInitialized = true;
  }
  
  static void _handleShow() {
    _showWindow();
    _onShowWindow?.call();
  }
  
  static void _handleQuit() {
    _onQuit?.call();
    exit(0);
  }
  
  /// Show the main window.
  static Future<void> _showWindow() async {
    if (!isSupported) return;
    _appWindow?.show();
  }
  
  /// Hide to system tray instead of closing.
  static Future<void> hideToTray() async {
    if (!isSupported) return;
    await windowManager.hide();
  }
  
  /// Check if window is visible.
  static Future<bool> isWindowVisible() async {
    if (!isSupported) return true;
    return await windowManager.isVisible();
  }
  
  /// Update tray tooltip with status.
  static Future<void> updateStatus({
    required bool isProvider,
    required int peerCount,
  }) async {
    if (!isSupported || _systemTray == null) return;
    
    final status = isProvider 
        ? 'Storage Provider • $peerCount peers'
        : 'Consumer • $peerCount peers';
    
    await _systemTray!.setTitle('FireCloud - $status');
  }
  
  /// Clean up tray resources.
  static Future<void> dispose() async {
    _systemTray = null;
    _appWindow = null;
    _isInitialized = false;
  }
}
