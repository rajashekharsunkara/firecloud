import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:path_provider/path_provider.dart';

enum AuditLogStatus { info, success, failure }

class AuditLogEntry {
  final String id;
  final DateTime createdAt;
  final String action;
  final AuditLogStatus status;
  final String message;
  final Map<String, Object?> details;

  const AuditLogEntry({
    required this.id,
    required this.createdAt,
    required this.action,
    required this.status,
    required this.message,
    this.details = const {},
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'created_at': createdAt.toIso8601String(),
    'action': action,
    'status': status.name,
    'message': message,
    'details': details,
  };

  factory AuditLogEntry.fromJson(Map<String, dynamic> json) {
    return AuditLogEntry(
      id: json['id'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      action: json['action'] as String,
      status: AuditLogStatus.values.firstWhere(
        (value) => value.name == json['status'],
        orElse: () => AuditLogStatus.info,
      ),
      message: json['message'] as String,
      details: (json['details'] as Map<String, dynamic>? ?? const {}).map(
        (key, value) => MapEntry(key, value),
      ),
    );
  }
}

class LedgerRequestAttachment {
  final String name;
  final String? path;
  final int sizeBytes;
  final String? extension;

  const LedgerRequestAttachment({
    required this.name,
    this.path,
    required this.sizeBytes,
    this.extension,
  });

  Map<String, dynamic> toJson() => {
    'name': name,
    'path': path,
    'size_bytes': sizeBytes,
    'extension': extension,
  };

  factory LedgerRequestAttachment.fromJson(Map<String, dynamic> json) {
    return LedgerRequestAttachment(
      name: json['name'] as String,
      path: json['path'] as String?,
      sizeBytes: (json['size_bytes'] as num?)?.toInt() ?? 0,
      extension: json['extension'] as String?,
    );
  }
}

class LedgerRequestEntry {
  final String id;
  final DateTime createdAt;
  final String title;
  final String description;
  final String category;
  final String status;
  final List<LedgerRequestAttachment> attachments;

  const LedgerRequestEntry({
    required this.id,
    required this.createdAt,
    required this.title,
    required this.description,
    required this.category,
    required this.status,
    required this.attachments,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'created_at': createdAt.toIso8601String(),
    'title': title,
    'description': description,
    'category': category,
    'status': status,
    'attachments': attachments.map((entry) => entry.toJson()).toList(),
  };

  factory LedgerRequestEntry.fromJson(Map<String, dynamic> json) {
    final attachments = (json['attachments'] as List<dynamic>? ?? const [])
        .map(
          (entry) =>
              LedgerRequestAttachment.fromJson(entry as Map<String, dynamic>),
        )
        .toList();
    return LedgerRequestEntry(
      id: json['id'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      title: json['title'] as String,
      description: json['description'] as String,
      category: json['category'] as String,
      status: json['status'] as String? ?? 'submitted',
      attachments: attachments,
    );
  }
}

class AuditLedgerService {
  static const _maxAuditEntries = 500;

  late final Directory _fireCloudDir;
  late final File _auditLogsFile;
  late final File _ledgerRequestsFile;

  final Random _random;

  AuditLedgerService._(this._random);

  static Future<AuditLedgerService> create() async {
    final service = AuditLedgerService._(Random());
    await service._initialize();
    return service;
  }

  Future<void> _initialize() async {
    final appDir = await getApplicationDocumentsDirectory();
    _fireCloudDir = Directory('${appDir.path}/firecloud');
    await _fireCloudDir.create(recursive: true);

    _auditLogsFile = File('${_fireCloudDir.path}/audit_logs.json');
    _ledgerRequestsFile = File('${_fireCloudDir.path}/ledger_requests.json');

    if (!await _auditLogsFile.exists()) {
      await _auditLogsFile.writeAsString('[]');
    }
    if (!await _ledgerRequestsFile.exists()) {
      await _ledgerRequestsFile.writeAsString('[]');
    }
  }

  Future<List<AuditLogEntry>> listAuditLogs({int limit = 200}) async {
    final rows = await _readJsonList(_auditLogsFile);
    final entries = rows.map((entry) => AuditLogEntry.fromJson(entry)).toList()
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    if (limit < entries.length) {
      return entries.take(limit).toList();
    }
    return entries;
  }

  Future<void> appendAuditLog({
    required String action,
    required AuditLogStatus status,
    required String message,
    Map<String, Object?> details = const {},
  }) async {
    final now = DateTime.now().toUtc();
    final entry = AuditLogEntry(
      id: _newId('audit'),
      createdAt: now,
      action: action,
      status: status,
      message: message,
      details: details,
    );
    final rows = await _readJsonList(_auditLogsFile);
    rows.insert(0, entry.toJson());
    if (rows.length > _maxAuditEntries) {
      rows.removeRange(_maxAuditEntries, rows.length);
    }
    await _writeJsonList(_auditLogsFile, rows);
  }

  Future<List<LedgerRequestEntry>> listLedgerRequests() async {
    final rows = await _readJsonList(_ledgerRequestsFile);
    final entries =
        rows.map((entry) => LedgerRequestEntry.fromJson(entry)).toList()
          ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    return entries;
  }

  Future<LedgerRequestEntry> submitLedgerRequest({
    required String title,
    required String description,
    required String category,
    required List<LedgerRequestAttachment> attachments,
  }) async {
    final now = DateTime.now().toUtc();
    final entry = LedgerRequestEntry(
      id: _newId('ledger'),
      createdAt: now,
      title: title.trim(),
      description: description.trim(),
      category: category,
      status: 'submitted',
      attachments: attachments,
    );
    final rows = await _readJsonList(_ledgerRequestsFile);
    rows.insert(0, entry.toJson());
    await _writeJsonList(_ledgerRequestsFile, rows);
    return entry;
  }

  Future<List<Map<String, dynamic>>> _readJsonList(File file) async {
    try {
      final raw = await file.readAsString();
      if (raw.trim().isEmpty) return <Map<String, dynamic>>[];
      final parsed = jsonDecode(raw);
      if (parsed is! List) return <Map<String, dynamic>>[];
      final rows = <Map<String, dynamic>>[];
      for (final entry in parsed) {
        if (entry is Map) {
          rows.add(entry.map((key, value) => MapEntry(key.toString(), value)));
        }
      }
      return rows;
    } catch (_) {
      return <Map<String, dynamic>>[];
    }
  }

  Future<void> _writeJsonList(
    File file,
    List<Map<String, dynamic>> rows,
  ) async {
    await file.writeAsString(jsonEncode(rows));
  }

  String _newId(String prefix) {
    final randomPart = _random.nextInt(0x7fffffff).toRadixString(16);
    return '$prefix-${DateTime.now().microsecondsSinceEpoch}-$randomPart';
  }
}
