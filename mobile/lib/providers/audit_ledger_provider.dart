import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/audit_ledger_service.dart';

final auditLedgerServiceProvider = FutureProvider<AuditLedgerService>((
  ref,
) async {
  return AuditLedgerService.create();
});

final auditLogsProvider = FutureProvider<List<AuditLogEntry>>((ref) async {
  final service = await ref.watch(auditLedgerServiceProvider.future);
  return service.listAuditLogs();
});

final ledgerRequestsProvider = FutureProvider<List<LedgerRequestEntry>>((
  ref,
) async {
  final service = await ref.watch(auditLedgerServiceProvider.future);
  return service.listLedgerRequests();
});
