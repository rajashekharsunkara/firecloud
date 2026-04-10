import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../screens/p2p_files_screen.dart';
import '../screens/peers_screen.dart';
import '../screens/node_settings_screen.dart';

class _NavItem {
  final String pathPrefix;
  final IconData icon;
  final IconData selectedIcon;
  final String label;

  const _NavItem({
    required this.pathPrefix,
    required this.icon,
    required this.selectedIcon,
    required this.label,
  });
}

/// Shell for bottom navigation with monochrome design.
class AppShell extends ConsumerStatefulWidget {
  final Widget child;
  final GoRouterState state;

  const AppShell({super.key, required this.child, required this.state});

  @override
  ConsumerState<AppShell> createState() => _AppShellState();
}

class _AppShellState extends ConsumerState<AppShell> {
  static const _navItems = [
    _NavItem(
      pathPrefix: '/files',
      icon: Icons.folder_outlined,
      selectedIcon: Icons.folder,
      label: 'Files',
    ),
    _NavItem(
      pathPrefix: '/peers',
      icon: Icons.hub_outlined,
      selectedIcon: Icons.hub,
      label: 'Network',
    ),
    _NavItem(
      pathPrefix: '/node',
      icon: Icons.settings_outlined,
      selectedIcon: Icons.settings,
      label: 'Settings',
    ),
  ];

  int _calculateSelectedIndex(String location, List<_NavItem> items) {
    final index = items.indexWhere(
      (item) => location.startsWith(item.pathPrefix),
    );
    return index >= 0 ? index : 0;
  }

  @override
  Widget build(BuildContext context) {
    final selectedIndex = _calculateSelectedIndex(widget.state.uri.path, _navItems);
    final theme = Theme.of(context);

    return Scaffold(
      body: widget.child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: selectedIndex,
        onDestinationSelected: (index) {
          context.go(_navItems[index].pathPrefix);
        },
        backgroundColor: theme.colorScheme.surface,
        indicatorColor: theme.colorScheme.primary.withValues(alpha: 0.15),
        height: 65,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        destinations: _navItems.map((item) {
          return NavigationDestination(
            icon: Icon(item.icon),
            selectedIcon: Icon(item.selectedIcon, color: theme.colorScheme.primary),
            label: item.label,
          );
        }).toList(),
      ),
    );
  }
}

/// App router configuration for P2P mode.
final appRouter = GoRouter(
  initialLocation: '/files',
  routes: [
    ShellRoute(
      builder: (context, state, child) => AppShell(state: state, child: child),
      routes: [
        GoRoute(
          path: '/files',
          pageBuilder: (context, state) => CustomTransitionPage(
            child: const FilesScreen(),
            transitionsBuilder: (context, animation, secondaryAnimation, child) {
              return FadeTransition(opacity: animation, child: child);
            },
          ),
        ),
        GoRoute(
          path: '/peers',
          pageBuilder: (context, state) => CustomTransitionPage(
            child: const PeersScreen(),
            transitionsBuilder: (context, animation, secondaryAnimation, child) {
              return FadeTransition(opacity: animation, child: child);
            },
          ),
        ),
        GoRoute(
          path: '/node',
          pageBuilder: (context, state) => CustomTransitionPage(
            child: const NodeSettingsScreen(),
            transitionsBuilder: (context, animation, secondaryAnimation, child) {
              return FadeTransition(opacity: animation, child: child);
            },
          ),
        ),
      ],
    ),
  ],
);
