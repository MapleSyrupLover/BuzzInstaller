import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/transfer_record.dart';

/// Recent uploads and downloads, persisted locally.
class HistoryService extends ChangeNotifier {
  HistoryService._(this._prefs, this._records);

  static const _storageKey = 'recent_transfers';
  static const _limit = 40;

  final SharedPreferences _prefs;
  final List<TransferRecord> _records;

  static Future<HistoryService> load() async {
    final prefs = await SharedPreferences.getInstance();
    final records = <TransferRecord>[];
    final raw = prefs.getString(_storageKey);
    if (raw != null && raw.isNotEmpty) {
      try {
        final decoded = jsonDecode(raw);
        if (decoded is List) {
          for (final item in decoded) {
            final record = TransferRecord.fromJson(item);
            if (record != null) records.add(record);
          }
        }
      } catch (_) {
        // Ignore corrupt history rather than blocking startup.
      }
    }
    records.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    return HistoryService._(prefs, records);
  }

  List<TransferRecord> get records => List.unmodifiable(_records);

  List<TransferRecord> get uploads =>
      _records.where((record) => record.isUpload).toList(growable: false);

  List<TransferRecord> get downloads =>
      _records.where((record) => !record.isUpload).toList(growable: false);

  Future<void> add(TransferRecord record) async {
    _records.insert(0, record);
    if (_records.length > _limit) {
      _records.removeRange(_limit, _records.length);
    }
    notifyListeners();
    await _persist();
  }

  Future<void> clear({bool? uploads}) async {
    if (uploads == null) {
      _records.clear();
    } else if (uploads) {
      _records.removeWhere((record) => record.isUpload);
    } else {
      _records.removeWhere((record) => !record.isUpload);
    }
    notifyListeners();
    await _persist();
  }

  Future<void> _persist() async {
    final data = jsonEncode(
      _records.map((record) => record.toJson()).toList(growable: false),
    );
    await _prefs.setString(_storageKey, data);
  }
}
