import 'dart:io';

import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:window_manager/window_manager.dart';

import 'providers/node_provider.dart';
import 'providers/auth_provider.dart' as auth;
import 'providers/storage_lock_provider.dart' as storage;
import 'router/app_router.dart';
import 'services/windows_tray_service.dart';
import 'theme/app_theme.dart';

/// Provider for SharedPreferences.
final sharedPreferencesProvider = Provider<SharedPreferences>((ref) {
  throw UnimplementedError('sharedPreferencesProvider must be overridden');
});

/// Provider for dark mode setting.
final themeModeProvider = StateProvider<bool>((ref) {
  final prefs = ref.watch(sharedPreferencesProvider);
  return prefs.getBool('dark_mode') ?? true; // Default to dark mode
});

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  // Initialize Firebase (skip on unsupported desktop platforms)
  if (Platform.isAndroid || Platform.isIOS) {
    await Firebase.initializeApp();
  }
  
  // Initialize window manager for desktop platforms
  if (Platform.isWindows || Platform.isLinux || Platform.isMacOS) {
    await windowManager.ensureInitialized();
    
    const windowOptions = WindowOptions(
      size: Size(400, 800),
      minimumSize: Size(360, 600),
      center: true,
      backgroundColor: Colors.transparent,
      skipTaskbar: false,
      titleBarStyle: TitleBarStyle.normal,
      title: 'FireCloud',
    );
    
    await windowManager.waitUntilReadyToShow(windowOptions, () async {
      await windowManager.show();
      await windowManager.focus();
    });
    
    // Initialize system tray for background mode
    await WindowsTrayService.initialize(
      onShowWindow: () {},
      onQuit: () => exit(0),
    );
  }
  
  // Set system UI overlay style for immersive experience (mobile only)
  if (Platform.isAndroid || Platform.isIOS) {
    SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: Colors.black,
      systemNavigationBarIconBrightness: Brightness.light,
    ));
  }
  
  final prefs = await SharedPreferences.getInstance();
  
  runApp(
    ProviderScope(
      overrides: [
        sharedPreferencesProvider.overrideWithValue(prefs),
        auth.sharedPreferencesProvider.overrideWithValue(prefs),
        storage.sharedPreferencesProvider.overrideWithValue(prefs),
      ],
      child: const FireCloudApp(),
    ),
  );
}

/// Splash screen shown while P2P node initializes.
class NodeInitScreen extends ConsumerWidget {
  const NodeInitScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final nodeAsync = ref.watch(fireCloudNodeProvider);
    final theme = Theme.of(context);
    
    return nodeAsync.when(
      data: (_) => const SizedBox.shrink(),
      loading: () => Scaffold(
        backgroundColor: theme.scaffoldBackgroundColor,
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              // Animated logo
              Container(
                width: 120,
                height: 120,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(
                    color: theme.colorScheme.primary,
                    width: 2,
                  ),
                ),
                child: Icon(
                  Icons.cloud_outlined,
                  size: 64,
                  color: theme.colorScheme.primary,
                ),
              )
                  .animate(onPlay: (c) => c.repeat())
                  .scale(
                    begin: const Offset(1, 1),
                    end: const Offset(1.1, 1.1),
                    duration: 1200.ms,
                    curve: Curves.easeInOut,
                  )
                  .then()
                  .scale(
                    begin: const Offset(1.1, 1.1),
                    end: const Offset(1, 1),
                    duration: 1200.ms,
                    curve: Curves.easeInOut,
                  ),
              const SizedBox(height: 40),
              Text(
                'FIRECLOUD',
                style: theme.textTheme.headlineMedium?.copyWith(
                  fontWeight: FontWeight.w300,
                  letterSpacing: 8,
                  color: theme.colorScheme.onSurface,
                ),
              ).animate().fadeIn(duration: 600.ms).slideY(begin: 0.3),
              const SizedBox(height: 16),
              Text(
                'Decentralized Storage',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                  letterSpacing: 2,
                ),
              ).animate().fadeIn(delay: 300.ms, duration: 600.ms),
              const SizedBox(height: 48),
              SizedBox(
                width: 200,
                child: LinearProgressIndicator(
                  backgroundColor: theme.colorScheme.surfaceContainerHighest,
                  color: theme.colorScheme.primary,
                )
                    .animate(onPlay: (c) => c.repeat())
                    .shimmer(duration: 1500.ms),
              ),
              const SizedBox(height: 16),
              Text(
                'Starting P2P Node...',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
                ),
              ).animate().fadeIn(delay: 600.ms),
            ],
          ),
        ),
      ),
      error: (error, _) => Scaffold(
        backgroundColor: theme.scaffoldBackgroundColor,
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                Icons.error_outline,
                size: 64,
                color: theme.colorScheme.error,
              ).animate().shake(),
              const SizedBox(height: 24),
              Text(
                'Failed to start node',
                style: theme.textTheme.titleLarge,
              ),
              const SizedBox(height: 8),
              Text(
                error.toString(),
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 24),
              FilledButton.tonal(
                onPressed: () => ref.invalidate(fireCloudNodeProvider),
                child: const Text('Retry'),
              ),
            ],
          ).animate().fadeIn(),
        ),
      ),
    );
  }
}

class FireCloudApp extends ConsumerStatefulWidget {
  const FireCloudApp({super.key});

  @override
  ConsumerState<FireCloudApp> createState() => _FireCloudAppState();
}

class _FireCloudAppState extends ConsumerState<FireCloudApp> with WindowListener {
  @override
  void initState() {
    super.initState();
    if (Platform.isWindows || Platform.isLinux || Platform.isMacOS) {
      windowManager.addListener(this);
      windowManager.setPreventClose(true);
    }
  }

  @override
  void dispose() {
    if (Platform.isWindows || Platform.isLinux || Platform.isMacOS) {
      windowManager.removeListener(this);
    }
    super.dispose();
  }

  @override
  Future<void> onWindowClose() async {
    // Check if provider mode is active - if so, minimize to tray instead of closing
    final nodeConfig = await ref.read(nodeConfigProvider.future);
    
    if (nodeConfig.role.name == 'storageProvider' && nodeConfig.storageQuotaGB > 0) {
      // Minimize to tray for background operation
      await WindowsTrayService.hideToTray();
    } else {
      // Actually close the app
      await windowManager.destroy();
    }
  }

  @override
  Widget build(BuildContext context) {
    final isDarkMode = ref.watch(themeModeProvider);

    return MaterialApp.router(
      title: 'FireCloud',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.lightTheme(),
      darkTheme: AppTheme.darkTheme(),
      themeMode: isDarkMode ? ThemeMode.dark : ThemeMode.light,
      routerConfig: appRouter,
    );
  }
}
