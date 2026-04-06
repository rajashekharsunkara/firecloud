import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';

import '../providers/node_provider.dart';
import '../providers/auth_provider.dart' show authProvider;
import '../storage/chunking.dart';
import '../storage/local_storage.dart' show P2PStorageUnavailableError, UploadReplicationFailedError;

/// Files screen using P2P node (no central server).
class FilesScreen extends ConsumerStatefulWidget {
  const FilesScreen({super.key});

  @override
  ConsumerState<FilesScreen> createState() => _FilesScreenState();
}

class _FilesScreenState extends ConsumerState<FilesScreen> {
  bool _isBusy = false;
  String _busyMessage = '';
  double _progress = 0;

  void _setBusy(String message, [double progress = 0]) {
    if (!mounted) return;
    setState(() {
      _isBusy = true;
      _busyMessage = message;
      _progress = progress;
    });
  }

  void _clearBusy() {
    if (!mounted) return;
    setState(() {
      _isBusy = false;
      _busyMessage = '';
      _progress = 0;
    });
  }

  Future<void> _uploadFile() async {
    try {
      final result = await FilePicker.platform.pickFiles();
      if (result == null || result.files.isEmpty) return;

      final file = result.files.first;
      if (file.bytes == null && file.path == null) {
        _showError('Cannot read selected file');
        return;
      }

      final bytes = file.bytes ?? await File(file.path!).readAsBytes();

      if (!mounted) return;

      _setBusy('Encrypting & distributing...');

      await ref.read(fileActionsProvider.notifier).uploadFile(file.name, bytes);

      _clearBusy();
      _showSuccess('File distributed to P2P network');
      ref.invalidate(filesProvider);
    } catch (e) {
      _clearBusy();
      if (e is P2PStorageUnavailableError) {
        _showError(e.message);
        return;
      }
      if (e is UploadReplicationFailedError) {
        _showError(
          'Upload failed to reach provider nodes. '
          'Please check both phones are online and retry.',
        );
        return;
      }
      _showError('Upload failed: $e');
    }
  }

  Future<void> _downloadFile(FileManifest file) async {
    try {
      _setBusy('Retrieving from peers...');

      final bytes = await ref.read(fileActionsProvider.notifier).downloadFile(file.fileId);

      final dir =
          await getDownloadsDirectory() ?? await getApplicationDocumentsDirectory();
      final outputFile = File('${dir.path}/${file.fileName}');
      await outputFile.writeAsBytes(bytes);

      _clearBusy();
      _showSuccess('Saved to ${outputFile.path}');
    } catch (e) {
      _clearBusy();
      _showError('Download failed: $e');
    }
  }

  Future<void> _deleteFile(FileManifest file) async {
    final theme = Theme.of(context);
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: theme.colorScheme.surface,
        title: Text('Delete File', style: theme.textTheme.titleLarge),
        content: Text(
          'Remove "${file.fileName}" from the network?',
          style: theme.textTheme.bodyMedium,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('CANCEL'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: TextButton.styleFrom(
              foregroundColor: theme.colorScheme.error,
            ),
            child: const Text('DELETE'),
          ),
        ],
      ),
    );

    if (confirm != true) return;

    try {
      await ref.read(fileActionsProvider.notifier).deleteFile(file.fileId);
      _showSuccess('File removed');
    } catch (e) {
      _showError('Delete failed: $e');
    }
  }

  void _showError(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: Theme.of(context).colorScheme.error,
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  void _showSuccess(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final filesAsync = ref.watch(filesProvider);
    final peersAsync = ref.watch(peersProvider);
    final capacityAsync = ref.watch(networkCapacityProvider);
    final authState = ref.watch(authProvider);
    final theme = Theme.of(context);

    return Scaffold(
      backgroundColor: theme.scaffoldBackgroundColor,
      body: CustomScrollView(
        slivers: [
          // Custom app bar with user info
          SliverAppBar(
            expandedHeight: 120,
            floating: true,
            pinned: true,
            backgroundColor: theme.scaffoldBackgroundColor,
            flexibleSpace: FlexibleSpaceBar(
              background: Container(
                padding: const EdgeInsets.fromLTRB(20, 60, 20, 0),
                child: Row(
                  children: [
                    // User avatar or sign in prompt
                    if (authState.isAuthenticated)
                      CircleAvatar(
                        radius: 24,
                        backgroundColor: theme.colorScheme.surfaceContainerHighest,
                        backgroundImage: authState.photoUrl != null
                            ? NetworkImage(authState.photoUrl!)
                            : null,
                        child: authState.photoUrl == null
                            ? Icon(Icons.person, color: theme.colorScheme.onSurface)
                            : null,
                      )
                    else
                      GestureDetector(
                        onTap: () => ref.read(authProvider.notifier).signIn(),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 12,
                            vertical: 8,
                          ),
                          decoration: BoxDecoration(
                            border: Border.all(
                              color: theme.colorScheme.outline.withValues(alpha: 0.3),
                            ),
                            borderRadius: BorderRadius.circular(20),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                Icons.login,
                                size: 16,
                                color: theme.colorScheme.primary,
                              ),
                              const SizedBox(width: 6),
                              Text(
                                'Sign In',
                                style: theme.textTheme.labelMedium?.copyWith(
                                  color: theme.colorScheme.primary,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    const SizedBox(width: 16),
                    // Title and peer count
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(
                            authState.isAuthenticated
                                ? 'Welcome, ${authState.displayName?.split(' ').first ?? 'User'}'
                                : 'My Files',
                            style: theme.textTheme.titleLarge?.copyWith(
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          const SizedBox(height: 4),
                          peersAsync.when(
                            data: (peers) {
                              final capacity = capacityAsync.valueOrNull;
                              final providerCount = capacity?.providerCount ?? 0;
                              final totalStorage = capacity?.totalAvailableBytes ?? 0;
                              final hasProviderCapacity = providerCount > 0 && totalStorage > 0;
                              return Row(
                                children: [
                                  Container(
                                    width: 8,
                                    height: 8,
                                    decoration: BoxDecoration(
                                      shape: BoxShape.circle,
                                      color: hasProviderCapacity
                                          ? theme.colorScheme.primary
                                          : theme.colorScheme.error,
                                    ),
                                  ),
                                  const SizedBox(width: 6),
                                  Expanded(
                                    child: Text(
                                      peers.isEmpty
                                          ? 'No peers connected'
                                          : hasProviderCapacity
                                              ? '$providerCount provider${providerCount == 1 ? '' : 's'} • ${_formatBytes(totalStorage)} available'
                                              : 'No provider storage available',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: theme.textTheme.bodySmall?.copyWith(
                                        color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                                      ),
                                    ),
                                  ),
                                ],
                              );
                            },
                            loading: () => Text(
                              'Discovering peers...',
                              style: theme.textTheme.bodySmall?.copyWith(
                                color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                              ),
                            ),
                            error: (_, stackTrace) => Text(
                              'Connection error',
                              style: theme.textTheme.bodySmall?.copyWith(
                                color: theme.colorScheme.error,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      icon: const Icon(Icons.refresh),
                      onPressed: () => ref.invalidate(filesProvider),
                    ),
                  ],
                ),
              ).animate().fadeIn(duration: 400.ms),
            ),
          ),
          // Files content
          filesAsync.when(
            data: (files) => files.isEmpty
                ? SliverFillRemaining(
                    child: _buildEmptyState(theme),
                  )
                : SliverPadding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 100),
                    sliver: SliverList(
                      delegate: SliverChildBuilderDelegate(
                        (context, index) => _FileCard(
                          file: files[index],
                          index: index,
                          onDownload: () => _downloadFile(files[index]),
                          onDelete: () => _deleteFile(files[index]),
                        ),
                        childCount: files.length,
                      ),
                    ),
                  ),
            loading: () => const SliverFillRemaining(
              child: Center(
                child: CircularProgressIndicator(),
              ),
            ),
            error: (error, _) => SliverFillRemaining(
              child: _buildErrorState(theme, error, ref),
            ),
          ),
        ],
      ),
      // Busy overlay
      floatingActionButton: _isBusy
          ? null
          : Builder(
              builder: (context) {
                final capacity = capacityAsync.valueOrNull;
                final canUpload = capacity != null &&
                    capacity.providerCount > 0 &&
                    capacity.totalAvailableBytes > 0;
                return FloatingActionButton(
                  onPressed: canUpload
                      ? _uploadFile
                      : () => _showError(
                            'Uploads are blocked: no provider storage is available on the network.',
                          ),
                  backgroundColor: canUpload
                      ? theme.colorScheme.primary
                      : theme.colorScheme.surfaceContainerHighest,
                  foregroundColor: canUpload
                      ? theme.colorScheme.onPrimary
                      : theme.colorScheme.onSurface.withValues(alpha: 0.5),
                  child: const Icon(Icons.add),
                ).animate().scale(delay: 300.ms, duration: 300.ms);
              },
            ),
      // Loading overlay
      bottomSheet: _isBusy
          ? Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: theme.colorScheme.surface,
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.1),
                    blurRadius: 10,
                    offset: const Offset(0, -2),
                  ),
                ],
              ),
              child: Row(
                children: [
                  SizedBox(
                    width: 24,
                    height: 24,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      value: _progress > 0 ? _progress : null,
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _busyMessage,
                          style: theme.textTheme.bodyMedium,
                        ),
                        if (_progress > 0)
                          Text(
                            '${(_progress * 100).toInt()}%',
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: theme.colorScheme.primary,
                            ),
                          ),
                      ],
                    ),
                  ),
                ],
              ),
            ).animate().slideY(begin: 1)
          : null,
    );
  }

  Widget _buildEmptyState(ThemeData theme) {
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
              Icons.cloud_upload_outlined,
              size: 48,
              color: theme.colorScheme.outline,
            ),
          ),
          const SizedBox(height: 24),
          Text(
            'No files yet',
            style: theme.textTheme.titleMedium?.copyWith(
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Upload files to distribute them\nacross the P2P network',
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium?.copyWith(
              color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
            ),
          ),
          const SizedBox(height: 32),
          FilledButton.icon(
            onPressed: _uploadFile,
            icon: const Icon(Icons.add),
            label: const Text('Upload File'),
          ),
        ],
      ).animate().fadeIn(duration: 600.ms).slideY(begin: 0.1),
    );
  }

  Widget _buildErrorState(ThemeData theme, Object error, WidgetRef ref) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(
            Icons.error_outline,
            size: 48,
            color: theme.colorScheme.error,
          ),
          const SizedBox(height: 16),
          Text(
            'Something went wrong',
            style: theme.textTheme.titleMedium,
          ),
          const SizedBox(height: 8),
          Text(
            error.toString(),
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
            ),
          ),
          const SizedBox(height: 24),
          FilledButton.tonal(
            onPressed: () => ref.invalidate(filesProvider),
            child: const Text('Retry'),
          ),
        ],
      ),
    );
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

class _FileCard extends StatelessWidget {
  final FileManifest file;
  final int index;
  final VoidCallback onDownload;
  final VoidCallback onDelete;

  const _FileCard({
    required this.file,
    required this.index,
    required this.onDownload,
    required this.onDelete,
  });

  IconData _getFileIcon(String fileName) {
    final ext = fileName.split('.').last.toLowerCase();
    return switch (ext) {
      'pdf' => Icons.picture_as_pdf_outlined,
      'jpg' || 'jpeg' || 'png' || 'gif' || 'webp' => Icons.image_outlined,
      'mp4' || 'mov' || 'avi' || 'mkv' => Icons.videocam_outlined,
      'mp3' || 'wav' || 'flac' || 'aac' => Icons.audiotrack_outlined,
      'zip' || 'tar' || 'gz' || 'rar' => Icons.folder_zip_outlined,
      'doc' || 'docx' => Icons.description_outlined,
      'xls' || 'xlsx' => Icons.table_chart_outlined,
      _ => Icons.insert_drive_file_outlined,
    };
  }

  String _formatBytes(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
    }
    return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final nodeCount = file.chunks.isNotEmpty ? file.chunks.first.nodeIds.length : 0;

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: theme.colorScheme.outline.withValues(alpha: 0.1),
        ),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: onDownload,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                // File icon
                Container(
                  width: 48,
                  height: 48,
                  decoration: BoxDecoration(
                    color: theme.colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Icon(
                    _getFileIcon(file.fileName),
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.7),
                  ),
                ),
                const SizedBox(width: 16),
                // File info
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        file.fileName,
                        style: theme.textTheme.bodyLarge?.copyWith(
                          fontWeight: FontWeight.w500,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 4),
                      Row(
                        children: [
                          Text(
                            _formatBytes(file.fileSize),
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                            ),
                          ),
                          Container(
                            width: 4,
                            height: 4,
                            margin: const EdgeInsets.symmetric(horizontal: 8),
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: theme.colorScheme.outline.withValues(alpha: 0.5),
                            ),
                          ),
                          Text(
                            '${file.chunks.length} chunks',
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                            ),
                          ),
                          Container(
                            width: 4,
                            height: 4,
                            margin: const EdgeInsets.symmetric(horizontal: 8),
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: theme.colorScheme.outline.withValues(alpha: 0.5),
                            ),
                          ),
                          Icon(
                            Icons.storage_outlined,
                            size: 14,
                            color: theme.colorScheme.primary,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            '$nodeCount',
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: theme.colorScheme.primary,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                // Actions
                PopupMenuButton<String>(
                  icon: Icon(
                    Icons.more_vert,
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                  onSelected: (value) {
                    switch (value) {
                      case 'download':
                        onDownload();
                      case 'delete':
                        onDelete();
                    }
                  },
                  itemBuilder: (context) => [
                    PopupMenuItem(
                      value: 'download',
                      child: Row(
                        children: [
                          Icon(
                            Icons.download_outlined,
                            size: 20,
                            color: theme.colorScheme.onSurface,
                          ),
                          const SizedBox(width: 12),
                          const Text('Download'),
                        ],
                      ),
                    ),
                    PopupMenuItem(
                      value: 'delete',
                      child: Row(
                        children: [
                          Icon(
                            Icons.delete_outline,
                            size: 20,
                            color: theme.colorScheme.error,
                          ),
                          const SizedBox(width: 12),
                          Text(
                            'Delete',
                            style: TextStyle(color: theme.colorScheme.error),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    ).animate(delay: Duration(milliseconds: 50 * index)).fadeIn().slideX(begin: 0.1);
  }
}
