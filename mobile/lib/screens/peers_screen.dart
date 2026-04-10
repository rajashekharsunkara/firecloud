import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../p2p/peer_discovery.dart';
import '../providers/node_provider.dart';

/// Peers screen - view discovered nodes on the network.
class PeersScreen extends ConsumerWidget {
  const PeersScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final peersAsync = ref.watch(peersProvider);
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
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Row(
                      children: [
                        Icon(
                          Icons.hub_outlined,
                          size: 28,
                          color: theme.colorScheme.onSurface,
                        ),
                        const SizedBox(width: 16),
                        Text(
                          'Network',
                          style: theme.textTheme.headlineSmall?.copyWith(
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                    IconButton(
                      icon: const Icon(Icons.refresh),
                      onPressed: () => ref.invalidate(peersProvider),
                    ),
                  ],
                ),
              ).animate().fadeIn(duration: 400.ms),
            ),
          ),

          // Content
          peersAsync.when(
            data: (peers) => peers.isEmpty
                ? SliverFillRemaining(child: _buildEmptyState(context, theme))
                : _buildPeersList(context, peers, theme),
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
                      onPressed: () => ref.invalidate(peersProvider),
                      child: const Text('Retry'),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyState(BuildContext context, ThemeData theme) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
                width: 100,
                height: 100,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(
                    color: theme.colorScheme.outline.withValues(alpha: 0.2),
                    width: 2,
                  ),
                ),
                child: Icon(
                  Icons.wifi_find,
                  size: 48,
                  color: theme.colorScheme.outline,
                ),
              )
              .animate(onPlay: (c) => c.repeat())
              .shimmer(
                duration: 2000.ms,
                color: theme.colorScheme.primary.withValues(alpha: 0.3),
              ),
          const SizedBox(height: 24),
          Text(
            'Searching for peers',
            style: theme.textTheme.titleMedium?.copyWith(
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 8),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 48),
            child: Text(
              'For LAN discovery, keep devices on the same WiFi. For internet discovery, sign in and set signaling URL in Settings.',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
              ),
              textAlign: TextAlign.center,
            ),
          ),
          const SizedBox(height: 32),
          Container(
            padding: const EdgeInsets.all(16),
            margin: const EdgeInsets.symmetric(horizontal: 32),
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainer,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: theme.colorScheme.outline.withValues(alpha: 0.1),
              ),
            ),
            child: Row(
              children: [
                Icon(
                  Icons.info_outline,
                  size: 20,
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    'LAN discovery uses mDNS. WAN discovery requires a reachable signaling server.',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ).animate().fadeIn(duration: 600.ms).slideY(begin: 0.1),
    );
  }

  Widget _buildPeersList(
    BuildContext context,
    List<PeerInfo> peers,
    ThemeData theme,
  ) {
    final storageProviders = peers.where((p) => p.isStorageProvider).toList();
    final consumers = peers.where((p) => !p.isStorageProvider).toList();

    return SliverPadding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 100),
      sliver: SliverList(
        delegate: SliverChildListDelegate([
          // Stats card
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainer,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: theme.colorScheme.outline.withValues(alpha: 0.1),
              ),
            ),
            child: Row(
              children: [
                _StatCircle(
                  value: peers.length.toString(),
                  label: 'Total',
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(width: 16),
                _StatCircle(
                  value: storageProviders.length.toString(),
                  label: 'Providers',
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(width: 16),
                _StatCircle(
                  value: consumers.length.toString(),
                  label: 'Consumers',
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                ),
              ],
            ),
          ).animate().fadeIn(delay: 100.ms).slideY(begin: 0.1),
          const SizedBox(height: 24),

          // Storage Providers Section
          if (storageProviders.isNotEmpty) ...[
            _SectionHeader(
              title: 'Storage Providers',
              icon: Icons.storage_outlined,
              count: storageProviders.length,
            ).animate().fadeIn(delay: 200.ms).slideX(begin: -0.1),
            const SizedBox(height: 12),
            ...storageProviders.asMap().entries.map(
              (entry) => _PeerCard(peer: entry.value, index: entry.key)
                  .animate()
                  .fadeIn(delay: Duration(milliseconds: 250 + entry.key * 50))
                  .slideX(begin: 0.1),
            ),
            const SizedBox(height: 24),
          ],

          // Consumers Section
          if (consumers.isNotEmpty) ...[
            _SectionHeader(
                  title: 'Consumers',
                  icon: Icons.devices_outlined,
                  count: consumers.length,
                )
                .animate()
                .fadeIn(
                  delay: Duration(
                    milliseconds: 300 + storageProviders.length * 50,
                  ),
                )
                .slideX(begin: -0.1),
            const SizedBox(height: 12),
            ...consumers.asMap().entries.map(
              (entry) => _PeerCard(peer: entry.value, index: entry.key)
                  .animate()
                  .fadeIn(
                    delay: Duration(
                      milliseconds:
                          350 + (storageProviders.length + entry.key) * 50,
                    ),
                  )
                  .slideX(begin: 0.1),
            ),
          ],
        ]),
      ),
    );
  }
}

class _StatCircle extends StatelessWidget {
  final String value;
  final String label;
  final Color color;

  const _StatCircle({
    required this.value,
    required this.label,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Expanded(
      child: Column(
        children: [
          Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(color: color.withValues(alpha: 0.3), width: 2),
            ),
            child: Center(
              child: Text(
                value,
                style: theme.textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w600,
                  color: color,
                ),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            label,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
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
  final int count;

  const _SectionHeader({
    required this.title,
    required this.icon,
    required this.count,
  });

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
        const Spacer(),
        Text(
          '$count',
          style: theme.textTheme.labelMedium?.copyWith(
            color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
          ),
        ),
      ],
    );
  }
}

class _PeerCard extends StatelessWidget {
  final PeerInfo peer;
  final int index;

  const _PeerCard({required this.peer, required this.index});

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
    final isOnline = peer.isOnline;
    final endpointLabel =
        (peer.hasDirectEndpoint && peer.ipAddress.isNotEmpty && peer.port > 0)
        ? '${peer.ipAddress}:${peer.port}'
        : (peer.relayUrls.isNotEmpty ? 'relay-only' : 'unreachable');

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: theme.colorScheme.outline.withValues(alpha: 0.1),
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            // Status indicator
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                color: theme.colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Stack(
                children: [
                  Center(
                    child: Icon(
                      peer.isStorageProvider
                          ? Icons.storage_outlined
                          : Icons.devices_outlined,
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.7),
                    ),
                  ),
                  Positioned(
                    right: 4,
                    bottom: 4,
                    child: Container(
                      width: 12,
                      height: 12,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: isOnline
                            ? theme.colorScheme.primary
                            : theme.colorScheme.outline,
                        border: Border.all(
                          color: theme.colorScheme.surfaceContainerHighest,
                          width: 2,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 16),
            // Peer info
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Text(
                        '${peer.deviceId.substring(0, 8)}...',
                        style: theme.textTheme.bodyLarge?.copyWith(
                          fontWeight: FontWeight.w500,
                          fontFamily: 'monospace',
                        ),
                      ),
                      const SizedBox(width: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: isOnline
                              ? theme.colorScheme.primary.withValues(alpha: 0.1)
                              : theme.colorScheme.outline.withValues(
                                  alpha: 0.1,
                                ),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Text(
                          isOnline ? 'Online' : 'Offline',
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: isOnline
                                ? theme.colorScheme.primary
                                : theme.colorScheme.outline,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      Text(
                        endpointLabel,
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurface.withValues(
                            alpha: 0.6,
                          ),
                        ),
                      ),
                      if (peer.isStorageProvider) ...[
                        Container(
                          width: 4,
                          height: 4,
                          margin: const EdgeInsets.symmetric(horizontal: 8),
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: theme.colorScheme.outline.withValues(
                              alpha: 0.5,
                            ),
                          ),
                        ),
                        Icon(
                          Icons.storage_outlined,
                          size: 14,
                          color: theme.colorScheme.primary,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          _formatBytes(peer.availableStorageBytes),
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: theme.colorScheme.primary,
                          ),
                        ),
                      ],
                    ],
                  ),
                ],
              ),
            ),
            // Action
            IconButton(
              icon: Icon(
                Icons.more_vert,
                color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
              ),
              onPressed: () {
                // Show peer actions
              },
            ),
          ],
        ),
      ),
    );
  }
}
