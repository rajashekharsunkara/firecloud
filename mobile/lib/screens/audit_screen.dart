import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../providers/audit_ledger_provider.dart';
import '../services/audit_ledger_service.dart';

class AuditScreen extends ConsumerStatefulWidget {
  const AuditScreen({super.key});

  @override
  ConsumerState<AuditScreen> createState() => _AuditScreenState();
}

class _AuditScreenState extends ConsumerState<AuditScreen> {
  final TextEditingController _titleController = TextEditingController();
  final TextEditingController _descriptionController = TextEditingController();
  final List<PlatformFile> _attachments = [];

  final List<String> _categories = const [
    'General',
    'Storage',
    'Access',
    'Integrity',
    'Billing',
    'Compliance',
  ];

  String _selectedCategory = 'General';
  bool _isSubmitting = false;

  @override
  void dispose() {
    _titleController.dispose();
    _descriptionController.dispose();
    super.dispose();
  }

  Future<void> _pickAttachments() async {
    final result = await FilePicker.platform.pickFiles(
      allowMultiple: true,
      type: FileType.any,
      withData: false,
      withReadStream: false,
    );
    if (result == null || result.files.isEmpty) return;
    setState(() {
      for (final file in result.files) {
        final exists = _attachments.any(
          (entry) => entry.path == file.path && entry.name == file.name,
        );
        if (!exists) {
          _attachments.add(file);
        }
      }
    });
  }

  void _removeAttachment(PlatformFile file) {
    setState(() {
      _attachments.removeWhere(
        (entry) => entry.path == file.path && entry.name == file.name,
      );
    });
  }

  Future<void> _submitLedgerRequest() async {
    final title = _titleController.text.trim();
    final description = _descriptionController.text.trim();
    if (title.isEmpty) {
      _showError('Please enter a request title');
      return;
    }
    if (description.isEmpty) {
      _showError('Please enter request details');
      return;
    }

    setState(() => _isSubmitting = true);
    try {
      final service = await ref.read(auditLedgerServiceProvider.future);
      final attachments = _attachments
          .map(
            (file) => LedgerRequestAttachment(
              name: file.name,
              path: file.path,
              sizeBytes: file.size,
              extension: _extensionOf(file.name),
            ),
          )
          .toList();
      final entry = await service.submitLedgerRequest(
        title: title,
        description: description,
        category: _selectedCategory,
        attachments: attachments,
      );
      await service.appendAuditLog(
        action: 'ledger_request_submit',
        status: AuditLogStatus.success,
        message: 'Submitted ledger request "${entry.title}"',
        details: {
          'request_id': entry.id,
          'category': _selectedCategory,
          'attachment_count': attachments.length,
        },
      );
      ref.invalidate(ledgerRequestsProvider);
      ref.invalidate(auditLogsProvider);
      _titleController.clear();
      _descriptionController.clear();
      setState(() {
        _attachments.clear();
        _selectedCategory = _categories.first;
      });
      _showSuccess('Ledger request submitted');
    } catch (e) {
      _showError('Failed to submit request: $e');
    } finally {
      if (mounted) {
        setState(() => _isSubmitting = false);
      }
    }
  }

  String? _extensionOf(String fileName) {
    final dot = fileName.lastIndexOf('.');
    if (dot <= 0 || dot == fileName.length - 1) return null;
    return fileName.substring(dot + 1).toLowerCase();
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
    final logsAsync = ref.watch(auditLogsProvider);
    final requestsAsync = ref.watch(ledgerRequestsProvider);
    final theme = Theme.of(context);

    return Scaffold(
      backgroundColor: theme.scaffoldBackgroundColor,
      body: CustomScrollView(
        slivers: [
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
                      Icons.fact_check_outlined,
                      size: 28,
                      color: theme.colorScheme.onSurface,
                    ),
                    const SizedBox(width: 16),
                    Text(
                      'Audit & Ledger',
                      style: theme.textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
              ).animate().fadeIn(duration: 400.ms),
            ),
          ),
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 100),
            sliver: SliverList(
              delegate: SliverChildListDelegate([
                _SectionHeader(
                  title: 'Audit Logs',
                  icon: Icons.history_outlined,
                ).animate().fadeIn().slideX(begin: -0.1),
                const SizedBox(height: 12),
                _InfoCard(
                  child: logsAsync.when(
                    data: (logs) {
                      if (logs.isEmpty) {
                        return Text(
                          'No audit events yet. Upload/download/delete actions will appear here.',
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: theme.colorScheme.onSurface.withValues(
                              alpha: 0.6,
                            ),
                          ),
                        );
                      }
                      return Column(
                        children: logs
                            .take(25)
                            .map((log) => _AuditLogTile(entry: log))
                            .toList(),
                      );
                    },
                    loading: () => const Center(
                      child: Padding(
                        padding: EdgeInsets.all(16),
                        child: CircularProgressIndicator(),
                      ),
                    ),
                    error: (error, _) => Text(
                      'Failed to load logs: $error',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.error,
                      ),
                    ),
                  ),
                ).animate().fadeIn(delay: 100.ms).slideY(begin: 0.1),
                const SizedBox(height: 24),
                _SectionHeader(
                  title: 'Ledger Request Form',
                  icon: Icons.edit_document,
                ).animate().fadeIn(delay: 150.ms).slideX(begin: -0.1),
                const SizedBox(height: 12),
                _InfoCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      TextField(
                        controller: _titleController,
                        decoration: const InputDecoration(
                          labelText: 'Request Title',
                          hintText:
                              'Need audit trail access for incident review',
                        ),
                      ),
                      const SizedBox(height: 12),
                      DropdownButtonFormField<String>(
                        initialValue: _selectedCategory,
                        items: _categories
                            .map(
                              (entry) => DropdownMenuItem(
                                value: entry,
                                child: Text(entry),
                              ),
                            )
                            .toList(),
                        onChanged: _isSubmitting
                            ? null
                            : (value) {
                                if (value == null) return;
                                setState(() => _selectedCategory = value);
                              },
                        decoration: const InputDecoration(
                          labelText: 'Category',
                        ),
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: _descriptionController,
                        minLines: 4,
                        maxLines: 6,
                        decoration: const InputDecoration(
                          labelText: 'Request Details',
                          hintText:
                              'Explain why you need ledger access and what evidence is attached.',
                        ),
                      ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          FilledButton.tonalIcon(
                            onPressed: _isSubmitting ? null : _pickAttachments,
                            icon: const Icon(Icons.attach_file),
                            label: const Text('Attach files'),
                          ),
                          Text(
                            'Supports audio, video, images, PDFs, DOCX, and other formats.',
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: theme.colorScheme.onSurface.withValues(
                                alpha: 0.6,
                              ),
                            ),
                          ),
                        ],
                      ),
                      if (_attachments.isNotEmpty) ...[
                        const SizedBox(height: 10),
                        Column(
                          children: _attachments
                              .map(
                                (file) => Padding(
                                  padding: const EdgeInsets.only(bottom: 6),
                                  child: Row(
                                    children: [
                                      Icon(
                                        _iconForFile(file.name),
                                        size: 18,
                                        color: theme.colorScheme.primary,
                                      ),
                                      const SizedBox(width: 8),
                                      Expanded(
                                        child: Text(
                                          '${file.name} (${_formatBytes(file.size)})',
                                          style: theme.textTheme.bodySmall,
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                      IconButton(
                                        icon: const Icon(Icons.close, size: 18),
                                        onPressed: _isSubmitting
                                            ? null
                                            : () => _removeAttachment(file),
                                        visualDensity: VisualDensity.compact,
                                      ),
                                    ],
                                  ),
                                ),
                              )
                              .toList(),
                        ),
                      ],
                      const SizedBox(height: 14),
                      SizedBox(
                        width: double.infinity,
                        child: FilledButton(
                          onPressed: _isSubmitting
                              ? null
                              : _submitLedgerRequest,
                          child: Text(
                            _isSubmitting
                                ? 'Submitting...'
                                : 'Submit Ledger Request',
                          ),
                        ),
                      ),
                    ],
                  ),
                ).animate().fadeIn(delay: 220.ms).slideY(begin: 0.1),
                const SizedBox(height: 24),
                _SectionHeader(
                  title: 'Submitted Requests',
                  icon: Icons.assignment_outlined,
                ).animate().fadeIn(delay: 260.ms).slideX(begin: -0.1),
                const SizedBox(height: 12),
                _InfoCard(
                  child: requestsAsync.when(
                    data: (requests) {
                      if (requests.isEmpty) {
                        return Text(
                          'No requests submitted yet.',
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: theme.colorScheme.onSurface.withValues(
                              alpha: 0.6,
                            ),
                          ),
                        );
                      }
                      return Column(
                        children: requests
                            .take(15)
                            .map((entry) => _LedgerRequestTile(entry: entry))
                            .toList(),
                      );
                    },
                    loading: () => const Center(
                      child: Padding(
                        padding: EdgeInsets.all(16),
                        child: CircularProgressIndicator(),
                      ),
                    ),
                    error: (error, _) => Text(
                      'Failed to load requests: $error',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.error,
                      ),
                    ),
                  ),
                ).animate().fadeIn(delay: 300.ms).slideY(begin: 0.1),
              ]),
            ),
          ),
        ],
      ),
    );
  }

  IconData _iconForFile(String fileName) {
    final extension = _extensionOf(fileName);
    switch (extension) {
      case 'png':
      case 'jpg':
      case 'jpeg':
      case 'gif':
      case 'webp':
      case 'heic':
      case 'bmp':
        return Icons.image_outlined;
      case 'mp4':
      case 'mov':
      case 'mkv':
      case 'webm':
      case 'avi':
        return Icons.movie_outlined;
      case 'mp3':
      case 'aac':
      case 'wav':
      case 'm4a':
      case 'ogg':
      case 'flac':
        return Icons.audio_file_outlined;
      case 'pdf':
      case 'doc':
      case 'docx':
      case 'txt':
      case 'csv':
      case 'xlsx':
      case 'ppt':
      case 'pptx':
        return Icons.description_outlined;
      default:
        return Icons.insert_drive_file_outlined;
    }
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
  final Widget child;

  const _InfoCard({required this.child});

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
      child: child,
    );
  }
}

class _AuditLogTile extends StatelessWidget {
  final AuditLogEntry entry;

  const _AuditLogTile({required this.entry});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final icon = switch (entry.status) {
      AuditLogStatus.success => Icons.check_circle_outline,
      AuditLogStatus.failure => Icons.error_outline,
      AuditLogStatus.info => Icons.info_outline,
    };
    final color = switch (entry.status) {
      AuditLogStatus.success => theme.colorScheme.primary,
      AuditLogStatus.failure => theme.colorScheme.error,
      AuditLogStatus.info => theme.colorScheme.onSurface.withValues(alpha: 0.7),
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  entry.action.replaceAll('_', ' ').toUpperCase(),
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.6),
                    letterSpacing: 0.8,
                  ),
                ),
                const SizedBox(height: 2),
                Text(entry.message, style: theme.textTheme.bodySmall),
                const SizedBox(height: 2),
                Text(
                  DateFormat(
                    'yyyy-MM-dd HH:mm:ss',
                  ).format(entry.createdAt.toLocal()),
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
                    fontFamily: 'monospace',
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _LedgerRequestTile extends StatelessWidget {
  final LedgerRequestEntry entry;

  const _LedgerRequestTile({required this.entry});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: theme.colorScheme.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: theme.colorScheme.outline.withValues(alpha: 0.12),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  entry.title,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              Text(
                entry.status.toUpperCase(),
                style: theme.textTheme.labelSmall?.copyWith(
                  color: theme.colorScheme.primary,
                  letterSpacing: 0.8,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            entry.description,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurface.withValues(alpha: 0.75),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            '${entry.category} • ${entry.attachments.length} attachment(s) • ${DateFormat('yyyy-MM-dd HH:mm').format(entry.createdAt.toLocal())}',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurface.withValues(alpha: 0.55),
              fontFamily: 'monospace',
            ),
          ),
        ],
      ),
    );
  }
}
