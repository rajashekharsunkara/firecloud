import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../node/node_role.dart';
import '../p2p/signaling_client.dart';
import '../providers/node_provider.dart';
import '../providers/auth_provider.dart' show AuthState, authProvider;
import '../providers/storage_lock_provider.dart'
    show StorageLockState, storageLockProvider;
import '../main.dart' show themeModeProvider, sharedPreferencesProvider;

ButtonStyle _settingsFilledButtonStyle(ThemeData theme) {
  final isDark = theme.brightness == Brightness.dark;
  return FilledButton.styleFrom(
    backgroundColor: isDark
        ? theme.colorScheme.secondary
        : theme.colorScheme.primary,
    foregroundColor: isDark
        ? theme.colorScheme.onSecondary
        : theme.colorScheme.onPrimary,
    disabledBackgroundColor: theme.colorScheme.surfaceContainerHighest,
    disabledForegroundColor: theme.colorScheme.onSurface.withValues(
      alpha: 0.45,
    ),
  );
}

ButtonStyle _settingsTonalButtonStyle(ThemeData theme) {
  return FilledButton.styleFrom(
    backgroundColor: theme.colorScheme.surfaceContainerHighest,
    foregroundColor: theme.colorScheme.onSurface,
    disabledBackgroundColor: theme.colorScheme.surfaceContainerHighest
        .withValues(alpha: 0.55),
    disabledForegroundColor: theme.colorScheme.onSurface.withValues(
      alpha: 0.45,
    ),
    side: BorderSide(color: theme.colorScheme.outline.withValues(alpha: 0.25)),
  );
}

/// Node settings screen - configure role, storage quota, and auth.
class NodeSettingsScreen extends ConsumerStatefulWidget {
  const NodeSettingsScreen({super.key});

  @override
  ConsumerState<NodeSettingsScreen> createState() => _NodeSettingsScreenState();
}

class _NodeSettingsScreenState extends ConsumerState<NodeSettingsScreen> {
  bool _isChangingRole = false;
  double _selectedQuotaGB = 10;
  bool _isLockOperationRunning = false;
  bool _isUpdatingInternetSettings = false;

  void _syncStorageLockUsage(NodeConfigState config) {
    ref
        .read(storageLockProvider.notifier)
        .refreshUsage(usedBytes: config.usedStorageMB * 1024 * 1024);
  }

  Future<void> _setRole(NodeRole role) async {
    if (!mounted) return;
    setState(() => _isChangingRole = true);
    try {
      await ref.read(nodeConfigProvider.notifier).setRole(role);
      if (!mounted) return;
      if (role == NodeRole.storageProvider) {
        await ref
            .read(nodeConfigProvider.notifier)
            .setStorageQuota(_selectedQuotaGB.toInt());
        if (!mounted) return;
      }
      _showSuccess('Role updated');
    } catch (e) {
      _showError('Failed to change role: $e');
    } finally {
      if (mounted) {
        setState(() => _isChangingRole = false);
      }
    }
  }

  bool _isValidHttpUrl(String value) {
    final parsed = Uri.tryParse(value.trim());
    return parsed != null &&
        parsed.hasScheme &&
        parsed.host.isNotEmpty &&
        (parsed.scheme == 'http' || parsed.scheme == 'https');
  }

  Future<String?> _showUrlEditDialog({
    required String title,
    required String initialValue,
    required String hintText,
    bool allowEmpty = false,
  }) async {
    final controller = TextEditingController(text: initialValue);
    String? errorText;
    final result = await showDialog<String>(
      context: context,
      builder: (context) {
        final theme = Theme.of(context);
        return StatefulBuilder(
          builder: (context, setModalState) {
            return AlertDialog(
              backgroundColor: theme.colorScheme.surface,
              title: Text(title),
              content: TextField(
                controller: controller,
                decoration: InputDecoration(
                  hintText: hintText,
                  errorText: errorText,
                ),
                keyboardType: TextInputType.url,
                textInputAction: TextInputAction.done,
                minLines: 1,
                maxLines: 2,
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('CANCEL'),
                ),
                FilledButton(
                  style: _settingsFilledButtonStyle(theme),
                  onPressed: () {
                    final value = controller.text.trim();
                    if (!allowEmpty && value.isEmpty) {
                      setModalState(() => errorText = 'Value cannot be empty');
                      return;
                    }
                    if (value.isNotEmpty && !_isValidHttpUrl(value)) {
                      setModalState(
                        () =>
                            errorText = 'Enter a valid http:// or https:// URL',
                      );
                      return;
                    }
                    Navigator.of(context).pop(value);
                  },
                  child: const Text('SAVE'),
                ),
              ],
            );
          },
        );
      },
    );
    controller.dispose();
    return result;
  }

  Future<void> _editSignalingServerUrl(NodeConfigState config) async {
    if (_isUpdatingInternetSettings) return;
    final value = await _showUrlEditDialog(
      title: 'Signaling Server URL',
      initialValue: config.signalingServerUrl,
      hintText: 'https://your-signaling-server.example',
      allowEmpty: false,
    );
    if (!mounted || value == null || value == config.signalingServerUrl) return;
    setState(() => _isUpdatingInternetSettings = true);
    try {
      await ref.read(nodeConfigProvider.notifier).setSignalingServerUrl(value);
      _showSuccess('Signaling server updated');
    } catch (e) {
      _showError('Failed to update signaling server: $e');
    } finally {
      if (mounted) setState(() => _isUpdatingInternetSettings = false);
    }
  }

  Future<void> _editRelayBaseUrl(NodeConfigState config) async {
    if (_isUpdatingInternetSettings) return;
    final value = await _showUrlEditDialog(
      title: 'Relay Base URL',
      initialValue: config.relayBaseUrl,
      hintText: 'https://your-relay-server.example (optional)',
      allowEmpty: true,
    );
    if (!mounted || value == null || value == config.relayBaseUrl) return;
    setState(() => _isUpdatingInternetSettings = true);
    try {
      await ref.read(nodeConfigProvider.notifier).setRelayBaseUrl(value);
      _showSuccess(
        value.isEmpty ? 'Relay URL cleared' : 'Relay base URL updated',
      );
    } catch (e) {
      _showError('Failed to update relay URL: $e');
    } finally {
      if (mounted) setState(() => _isUpdatingInternetSettings = false);
    }
  }

  Future<void> _resetInternetDiscoveryDefaults() async {
    if (_isUpdatingInternetSettings) return;
    setState(() => _isUpdatingInternetSettings = true);
    try {
      await ref
          .read(nodeConfigProvider.notifier)
          .resetInternetDiscoveryDefaults();
      _showSuccess('Internet discovery defaults restored');
    } catch (e) {
      _showError('Failed to reset internet discovery settings: $e');
    } finally {
      if (mounted) setState(() => _isUpdatingInternetSettings = false);
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: Theme.of(context).colorScheme.error,
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  void _showSuccess(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), behavior: SnackBarBehavior.floating),
    );
  }

  @override
  Widget build(BuildContext context) {
    final nodeConfigAsync = ref.watch(nodeConfigProvider);
    final authState = ref.watch(authProvider);
    final storageLock = ref.watch(storageLockProvider);
    final isDarkMode = ref.watch(themeModeProvider);
    final theme = Theme.of(context);

    return Scaffold(
      backgroundColor: theme.scaffoldBackgroundColor,
      body: CustomScrollView(
        slivers: [
          // App bar
          SliverAppBar(
            expandedHeight: 100,
            floating: true,
            pinned: true,
            backgroundColor: theme.scaffoldBackgroundColor,
            flexibleSpace: FlexibleSpaceBar(
              background: Container(
                padding: const EdgeInsets.fromLTRB(20, 60, 20, 0),
                child: Row(
                  children: [
                    Icon(
                      Icons.settings_outlined,
                      size: 28,
                      color: theme.colorScheme.onSurface,
                    ),
                    const SizedBox(width: 16),
                    Text(
                      'Settings',
                      style: theme.textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
              ).animate().fadeIn(duration: 400.ms),
            ),
          ),

          // Content
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 100),
            sliver: nodeConfigAsync.when(
              data: (config) => SliverList(
                delegate: SliverChildListDelegate([
                  Builder(
                    builder: (context) {
                      WidgetsBinding.instance.addPostFrameCallback((_) {
                        if (mounted) _syncStorageLockUsage(config);
                      });
                      return const SizedBox.shrink();
                    },
                  ),
                  // Account Section
                  _SectionHeader(
                    title: 'Account',
                    icon: Icons.person_outline,
                  ).animate().fadeIn().slideX(begin: -0.1),
                  const SizedBox(height: 12),
                  _AccountCard(
                    authState: authState,
                    onSignIn: () => ref.read(authProvider.notifier).signIn(),
                    onSignOut: () => ref.read(authProvider.notifier).signOut(),
                  ).animate().fadeIn(delay: 100.ms).slideY(begin: 0.1),
                  const SizedBox(height: 24),

                  // Node Identity Section
                  _SectionHeader(
                    title: 'Node Identity',
                    icon: Icons.fingerprint_outlined,
                  ).animate().fadeIn(delay: 200.ms).slideX(begin: -0.1),
                  const SizedBox(height: 12),
                  _InfoCard(
                    children: [
                      _InfoRow(
                        label: 'Node ID',
                        value: config.deviceId.length > 16
                            ? '${config.deviceId.substring(0, 8)}...${config.deviceId.substring(config.deviceId.length - 8)}'
                            : config.deviceId,
                        onCopy: () {
                          // Copy to clipboard
                        },
                      ),
                      const Divider(height: 24),
                      _InfoRow(
                        label: 'Status',
                        value: config.isRunning ? 'Running' : 'Stopped',
                        valueColor: config.isRunning
                            ? theme.colorScheme.primary
                            : theme.colorScheme.error,
                      ),
                      const Divider(height: 24),
                      _InfoRow(
                        label: 'Background Service',
                        value: config.isBackgroundServiceRunning
                            ? 'Active'
                            : 'Inactive',
                        valueColor: config.isBackgroundServiceRunning
                            ? theme.colorScheme.primary
                            : theme.colorScheme.error,
                      ),
                    ],
                  ).animate().fadeIn(delay: 300.ms).slideY(begin: 0.1),
                  const SizedBox(height: 24),

                  // Role Section
                  _SectionHeader(
                    title: 'Node Role',
                    icon: Icons.hub_outlined,
                  ).animate().fadeIn(delay: 400.ms).slideX(begin: -0.1),
                  const SizedBox(height: 8),
                  Text(
                    'Choose how this device participates in the network',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                    ),
                  ),
                  const SizedBox(height: 16),
                  _RoleSelector(
                    currentRole: config.role,
                    isLoading: _isChangingRole,
                    onSelect: _setRole,
                  ).animate().fadeIn(delay: 500.ms).slideY(begin: 0.1),
                  const SizedBox(height: 24),

                  // Storage Lock Section (for providers)
                  if (config.role == NodeRole.storageProvider) ...[
                    _SectionHeader(
                      title: 'Storage Lock',
                      icon: Icons.lock_outline,
                    ).animate().fadeIn(delay: 600.ms).slideX(begin: -0.1),
                    const SizedBox(height: 8),
                    Text(
                      'Reserve storage space for the network',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.6,
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    _StorageLockCard(
                      state: storageLock,
                      selectedQuotaGB: _selectedQuotaGB,
                      isOperationRunning: _isLockOperationRunning,
                      onQuotaChanged: (v) =>
                          setState(() => _selectedQuotaGB = v),
                      onLock: () async {
                        if (_isLockOperationRunning) return;
                        setState(() => _isLockOperationRunning = true);
                        try {
                          final ok = await ref
                              .read(storageLockProvider.notifier)
                              .lockStorage(
                                (_selectedQuotaGB * 1024 * 1024 * 1024).toInt(),
                              );
                          if (ok) {
                            await ref
                                .read(nodeConfigProvider.notifier)
                                .setStorageQuota(_selectedQuotaGB.toInt());
                            _showSuccess(
                              'Storage locked at ${_selectedQuotaGB.toStringAsFixed(0)} GB',
                            );
                          } else {
                            final error = ref.read(storageLockProvider).error;
                            if (error != null && mounted) _showError(error);
                          }
                        } catch (e) {
                          if (mounted) _showError('Failed to lock storage: $e');
                        } finally {
                          if (mounted) {
                            setState(() => _isLockOperationRunning = false);
                          }
                        }
                      },
                      onUnlock: () async {
                        if (_isLockOperationRunning) return;
                        setState(() => _isLockOperationRunning = true);
                        try {
                          final ok = await ref
                              .read(storageLockProvider.notifier)
                              .unlockStorage();
                          if (ok) {
                            await ref
                                .read(nodeConfigProvider.notifier)
                                .setStorageQuota(0);
                            _showSuccess('Storage unlocked');
                          } else {
                            final error = ref.read(storageLockProvider).error;
                            if (error != null && mounted) _showError(error);
                          }
                        } catch (e) {
                          if (mounted) {
                            _showError('Failed to unlock storage: $e');
                          }
                        } finally {
                          if (mounted) {
                            setState(() => _isLockOperationRunning = false);
                          }
                        }
                      },
                    ).animate().fadeIn(delay: 700.ms).slideY(begin: 0.1),
                    const SizedBox(height: 24),
                  ],

                  // Background Mode Section (for providers)
                  if (config.role == NodeRole.storageProvider &&
                      config.storageQuotaGB > 0) ...[
                    _SectionHeader(
                      title: 'Background Mode',
                      icon: Icons.sync_outlined,
                    ).animate().fadeIn(delay: 750.ms).slideX(begin: -0.1),
                    const SizedBox(height: 8),
                    Text(
                      'Keep running when app is closed to serve network requests',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.6,
                        ),
                      ),
                    ),
                    const SizedBox(height: 12),
                    _InfoCard(
                      children: [
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      Icon(
                                        config.isBackgroundServiceRunning
                                            ? Icons.sync
                                            : Icons.sync_disabled,
                                        size: 20,
                                        color: config.isBackgroundServiceRunning
                                            ? theme.colorScheme.primary
                                            : theme.colorScheme.onSurface
                                                  .withValues(alpha: 0.5),
                                      ),
                                      const SizedBox(width: 12),
                                      Text(
                                        'Run in Background',
                                        style: theme.textTheme.bodyLarge,
                                      ),
                                    ],
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    config.isBackgroundServiceRunning
                                        ? 'Node stays online when app is minimized'
                                        : 'Node stops when app is closed',
                                    style: theme.textTheme.bodySmall?.copyWith(
                                      color: theme.colorScheme.onSurface
                                          .withValues(alpha: 0.5),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            Switch(
                              value: config.backgroundModeEnabled,
                              onChanged: (value) {
                                ref
                                    .read(nodeConfigProvider.notifier)
                                    .setBackgroundModeEnabled(value);
                              },
                            ),
                          ],
                        ),
                      ],
                    ).animate().fadeIn(delay: 800.ms).slideY(begin: 0.1),
                    const SizedBox(height: 24),
                  ],

                  _SectionHeader(
                    title: 'Internet Discovery',
                    icon: Icons.public_outlined,
                  ).animate().fadeIn(delay: 820.ms).slideX(begin: -0.1),
                  const SizedBox(height: 8),
                  Text(
                    'Configure signaling + relay endpoints for cross-network peers (Google sign-in required)',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _InfoCard(
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.cloud_sync_outlined,
                            size: 18,
                            color: theme.colorScheme.primary,
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(
                              config.signalingServerUrl,
                              style: theme.textTheme.bodySmall?.copyWith(
                                fontFamily: 'monospace',
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 10),
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.swap_calls_outlined,
                            size: 18,
                            color: theme.colorScheme.primary,
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(
                              config.relayBaseUrl.isEmpty
                                  ? '(disabled)'
                                  : config.relayBaseUrl,
                              style: theme.textTheme.bodySmall?.copyWith(
                                fontFamily: 'monospace',
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 14),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          FilledButton.tonal(
                            style: _settingsTonalButtonStyle(theme),
                            onPressed: _isUpdatingInternetSettings
                                ? null
                                : () => _editSignalingServerUrl(config),
                            child: const Text('Edit Signaling'),
                          ),
                          FilledButton.tonal(
                            style: _settingsTonalButtonStyle(theme),
                            onPressed: _isUpdatingInternetSettings
                                ? null
                                : () => _editRelayBaseUrl(config),
                            child: const Text('Edit Relay'),
                          ),
                          TextButton(
                            onPressed: _isUpdatingInternetSettings
                                ? null
                                : _resetInternetDiscoveryDefaults,
                            child: const Text('Reset defaults'),
                          ),
                        ],
                      ),
                      if (config.signalingServerUrl ==
                          SignalingClient.defaultServerUrl) ...[
                        const SizedBox(height: 12),
                        Text(
                          'Default URL is a placeholder. Set your deployed signaling server for normal internet discovery.',
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: theme.colorScheme.onSurface.withValues(
                              alpha: 0.6,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ).animate().fadeIn(delay: 860.ms).slideY(begin: 0.1),
                  const SizedBox(height: 24),

                  _SectionHeader(
                    title: 'Appearance',
                    icon: Icons.palette_outlined,
                  ).animate().fadeIn(delay: 800.ms).slideX(begin: -0.1),
                  const SizedBox(height: 12),
                  _InfoCard(
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Row(
                            children: [
                              Icon(
                                isDarkMode ? Icons.dark_mode : Icons.light_mode,
                                size: 20,
                                color: theme.colorScheme.onSurface.withValues(
                                  alpha: 0.7,
                                ),
                              ),
                              const SizedBox(width: 12),
                              Text(
                                'Dark Mode',
                                style: theme.textTheme.bodyLarge,
                              ),
                            ],
                          ),
                          Switch(
                            value: isDarkMode,
                            onChanged: (value) {
                              ref.read(themeModeProvider.notifier).state =
                                  value;
                              ref
                                  .read(sharedPreferencesProvider)
                                  .setBool('dark_mode', value);
                            },
                          ),
                        ],
                      ),
                    ],
                  ).animate().fadeIn(delay: 900.ms).slideY(begin: 0.1),
                  const SizedBox(height: 24),

                  // About Section
                  _SectionHeader(
                    title: 'About',
                    icon: Icons.info_outline,
                  ).animate().fadeIn(delay: 1000.ms).slideX(begin: -0.1),
                  const SizedBox(height: 12),
                  _AboutCard()
                      .animate()
                      .fadeIn(delay: 1100.ms)
                      .slideY(begin: 0.1),
                ]),
              ),
              loading: () => const SliverFillRemaining(
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (error, _) => SliverFillRemaining(
                child: Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(
                        Icons.error_outline,
                        size: 48,
                        color: theme.colorScheme.error,
                      ),
                      const SizedBox(height: 16),
                      Text('Error: $error'),
                      const SizedBox(height: 16),
                      FilledButton.tonal(
                        style: _settingsTonalButtonStyle(theme),
                        onPressed: () => ref.invalidate(nodeConfigProvider),
                        child: const Text('Retry'),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  final String title;
  final IconData icon;

  const _SectionHeader({required this.title, required this.icon});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      children: [
        Icon(icon, size: 18, color: theme.colorScheme.primary),
        const SizedBox(width: 8),
        Text(
          title.toUpperCase(),
          style: theme.textTheme.labelMedium?.copyWith(
            color: theme.colorScheme.primary,
            fontWeight: FontWeight.w600,
            letterSpacing: 1.2,
          ),
        ),
      ],
    );
  }
}

class _InfoCard extends StatelessWidget {
  final List<Widget> children;

  const _InfoCard({required this.children});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: theme.colorScheme.outline.withValues(alpha: 0.1),
        ),
      ),
      child: Column(children: children),
    );
  }
}

class _InfoRow extends StatelessWidget {
  final String label;
  final String value;
  final Color? valueColor;
  final VoidCallback? onCopy;

  const _InfoRow({
    required this.label,
    required this.value,
    this.valueColor,
    this.onCopy,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          label,
          style: theme.textTheme.bodyMedium?.copyWith(
            color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
          ),
        ),
        Row(
          children: [
            Text(
              value,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: valueColor ?? theme.colorScheme.onSurface,
                fontFamily: 'monospace',
              ),
            ),
            if (onCopy != null) ...[
              const SizedBox(width: 8),
              IconButton(
                icon: const Icon(Icons.copy, size: 16),
                onPressed: onCopy,
                visualDensity: VisualDensity.compact,
              ),
            ],
          ],
        ),
      ],
    );
  }
}

class _AccountCard extends StatelessWidget {
  final AuthState authState;
  final VoidCallback onSignIn;
  final VoidCallback onSignOut;

  const _AccountCard({
    required this.authState,
    required this.onSignIn,
    required this.onSignOut,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: theme.colorScheme.outline.withValues(alpha: 0.1),
        ),
      ),
      child: authState.isAuthenticated
          ? Row(
              children: [
                CircleAvatar(
                  radius: 24,
                  backgroundColor: theme.colorScheme.surfaceContainerHighest,
                  backgroundImage: authState.photoUrl != null
                      ? NetworkImage(authState.photoUrl!)
                      : null,
                  child: authState.photoUrl == null
                      ? Icon(Icons.person, color: theme.colorScheme.onSurface)
                      : null,
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        authState.displayName ?? 'User',
                        style: theme.textTheme.titleMedium,
                      ),
                      Text(
                        authState.email ?? '',
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurface.withValues(
                            alpha: 0.6,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                TextButton(
                  onPressed: authState.isLoading ? null : onSignOut,
                  child: authState.isLoading
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('Sign Out'),
                ),
              ],
            )
          : Column(
              children: [
                Icon(
                  Icons.account_circle_outlined,
                  size: 48,
                  color: theme.colorScheme.outline,
                ),
                const SizedBox(height: 12),
                Text(
                  'Sign in to sync files across devices',
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                  ),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 16),
                FilledButton.icon(
                  style: _settingsFilledButtonStyle(theme),
                  onPressed: authState.isLoading ? null : onSignIn,
                  icon: authState.isLoading
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.login),
                  label: const Text('Sign in with Google'),
                ),
                if (authState.error != null) ...[
                  const SizedBox(height: 8),
                  Text(
                    authState.error!,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.error,
                    ),
                  ),
                ],
              ],
            ),
    );
  }
}

class _RoleSelector extends StatelessWidget {
  final NodeRole currentRole;
  final bool isLoading;
  final Function(NodeRole) onSelect;

  const _RoleSelector({
    required this.currentRole,
    required this.isLoading,
    required this.onSelect,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _RoleOption(
          title: 'Consumer',
          description: 'Use storage from the network',
          icon: Icons.cloud_download_outlined,
          isSelected: currentRole == NodeRole.consumer,
          isLoading: isLoading && currentRole != NodeRole.consumer,
          onTap: () => onSelect(NodeRole.consumer),
        ),
        const SizedBox(height: 12),
        _RoleOption(
          title: 'Storage Provider',
          description: 'Provide storage to the network',
          icon: Icons.storage_outlined,
          isSelected: currentRole == NodeRole.storageProvider,
          isLoading: isLoading && currentRole != NodeRole.storageProvider,
          onTap: () => onSelect(NodeRole.storageProvider),
        ),
      ],
    );
  }
}

class _RoleOption extends StatelessWidget {
  final String title;
  final String description;
  final IconData icon;
  final bool isSelected;
  final bool isLoading;
  final VoidCallback onTap;

  const _RoleOption({
    required this.title,
    required this.description,
    required this.icon,
    required this.isSelected,
    required this.isLoading,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: isLoading ? null : onTap,
        borderRadius: BorderRadius.circular(16),
        child: Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: isSelected
                ? theme.colorScheme.primary.withValues(alpha: 0.1)
                : theme.colorScheme.surfaceContainer,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
              color: isSelected
                  ? theme.colorScheme.primary
                  : theme.colorScheme.outline.withValues(alpha: 0.1),
              width: isSelected ? 2 : 1,
            ),
          ),
          child: Row(
            children: [
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  color: isSelected
                      ? theme.colorScheme.primary
                      : theme.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(
                  icon,
                  color: isSelected
                      ? theme.colorScheme.onPrimary
                      : theme.colorScheme.onSurface.withValues(alpha: 0.7),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: isSelected
                            ? FontWeight.w600
                            : FontWeight.w500,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      description,
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.6,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              if (isLoading)
                const SizedBox(
                  width: 24,
                  height: 24,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              else if (isSelected)
                Icon(Icons.check_circle, color: theme.colorScheme.primary),
            ],
          ),
        ),
      ),
    );
  }
}

class _StorageLockCard extends StatelessWidget {
  final StorageLockState state;
  final double selectedQuotaGB;
  final bool isOperationRunning;
  final Function(double) onQuotaChanged;
  final VoidCallback onLock;
  final VoidCallback onUnlock;

  const _StorageLockCard({
    required this.state,
    required this.selectedQuotaGB,
    required this.isOperationRunning,
    required this.onQuotaChanged,
    required this.onLock,
    required this.onUnlock,
  });

  String _formatBytes(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
    }
    return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(1)} GB';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final maxGB = (state.availableToLock / (1024 * 1024 * 1024)).clamp(
      1.0,
      100.0,
    );

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: theme.colorScheme.outline.withValues(alpha: 0.1),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Device capacity
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'Device Capacity',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                ),
              ),
              Text(
                _formatBytes(state.totalDeviceBytes),
                style: theme.textTheme.bodyMedium,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'Available',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                ),
              ),
              Text(
                _formatBytes(state.freeDeviceBytes),
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.primary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          const Divider(),
          const SizedBox(height: 16),

          if (state.isLocked) ...[
            // Locked state
            Row(
              children: [
                Icon(Icons.lock, size: 20, color: theme.colorScheme.primary),
                const SizedBox(width: 8),
                Text(
                  'Storage Locked',
                  style: theme.textTheme.titleMedium?.copyWith(
                    color: theme.colorScheme.primary,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            // Progress bar
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: state.usagePercent / 100,
                minHeight: 8,
                backgroundColor: theme.colorScheme.surfaceContainerHighest,
                color: theme.colorScheme.primary,
              ),
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'Used: ${_formatBytes(state.effectiveUsed)}',
                  style: theme.textTheme.bodySmall,
                ),
                Text(
                  'Free: ${_formatBytes(state.freeInLock)}',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              'Locked total: ${_formatBytes(state.lockedBytes)}',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
              ),
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: (state.isLoading || isOperationRunning)
                    ? null
                    : onUnlock,
                icon: (state.isLoading || isOperationRunning)
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.lock_open),
                label: const Text('Unlock Storage'),
              ),
            ),
          ] else ...[
            // Unlocked state - set quota
            Text(
              'Recommendation',
              style: theme.textTheme.labelMedium?.copyWith(
                color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
              ),
            ),
            const SizedBox(height: 4),
            Text(
              state.recommendationText,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.primary,
              ),
            ),
            const SizedBox(height: 16),
            Text(
              'Select Amount: ${selectedQuotaGB.toStringAsFixed(0)} GB',
              style: theme.textTheme.bodyMedium,
            ),
            Slider(
              value: selectedQuotaGB.clamp(1.0, maxGB),
              min: 1,
              max: maxGB,
              divisions: maxGB.toInt() - 1,
              onChanged: onQuotaChanged,
              activeColor: theme.colorScheme.primary,
              inactiveColor: theme.colorScheme.surfaceContainerHighest,
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                style: _settingsFilledButtonStyle(theme),
                onPressed:
                    (state.isLoading ||
                        isOperationRunning ||
                        state.recommendedBytes == 0)
                    ? null
                    : onLock,
                icon: (state.isLoading || isOperationRunning)
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.lock),
                label: const Text('Lock Storage'),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Locking fills the space with random data to reserve it for the network.',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
              ),
            ),
          ],
          if (state.error != null) ...[
            const SizedBox(height: 8),
            Text(
              state.error!,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.error,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _AboutCard extends StatelessWidget {
  const _AboutCard();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: theme.colorScheme.outline.withValues(alpha: 0.1),
        ),
      ),
      child: Column(
        children: [
          Row(
            children: [
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: theme.colorScheme.surfaceContainerHighest,
                ),
                child: Icon(
                  Icons.cloud_outlined,
                  color: theme.colorScheme.primary,
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'FireCloud',
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    Text(
                      'Version 1.0.0',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.6,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          const Divider(),
          const SizedBox(height: 16),
          Text(
            'Fully decentralized P2P storage. No central servers. '
            'Your files are encrypted, chunked, and distributed across the network.',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurface.withValues(alpha: 0.7),
            ),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Icon(
                Icons.lock_outlined,
                size: 16,
                color: theme.colorScheme.primary,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'End-to-end encrypted • Open source',
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.primary,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
