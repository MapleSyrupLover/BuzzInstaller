import 'dart:convert';

import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Settings storage, persisted as JSON in SharedPreferences.
///
/// The keys mirror the desktop configuration where it makes sense so both
/// apps describe the same options, even though the storage location differs.
class AppConfig extends ChangeNotifier {
  AppConfig._(this._prefs, this._values);

  static const _storageKey = 'buzzinstaller_config';

  static const Map<String, Object> defaults = <String, Object>{
    // Transfer defaults
    'upload_chunk_mode': 'auto', // auto | ask | count | custom
    'upload_chunk_count': 4,
    'upload_chunk_size_bytes': 0,
    'chunk_size_bytes': 10 * 1024 * 1024 * 1024,
    'buzzheavier_account_id': '',
    // Downloads
    'download_subfolder': 'BuzzInstaller',
    'open_after_download': false,
    // Clipboard
    'auto_copy_clear_mode': 'never', // never | timeout
    'auto_copy_clear_seconds': 10,
    // Appearance
    'theme_mode': 'system', // system | light | dark
    'ui_style': 'discord',
    'accent_color': '',
    'ui_radius': 8,
    'ui_scale': 100,
    // Behaviour
    'notifications_enabled': true,
    'confirm_before_upload': false,
  };

  final SharedPreferences _prefs;
  final Map<String, Object?> _values;

  static Future<AppConfig> load() async {
    final prefs = await SharedPreferences.getInstance();
    final values = <String, Object?>{...defaults};
    final raw = prefs.getString(_storageKey);
    if (raw != null && raw.isNotEmpty) {
      try {
        final decoded = jsonDecode(raw);
        if (decoded is Map) {
          decoded.forEach((key, value) {
            if (key is String) values[key] = value;
          });
        }
      } catch (_) {
        // Corrupt settings fall back to defaults, same as the desktop app.
      }
    }
    return AppConfig._(prefs, values);
  }

  Map<String, Object?> get values => Map.unmodifiable(_values);

  String stringValue(String key) => '${_values[key] ?? defaults[key] ?? ''}';

  int intValue(String key) {
    final raw = _values[key];
    if (raw is int) return raw;
    if (raw is num) return raw.toInt();
    return int.tryParse('$raw') ?? (defaults[key] as int? ?? 0);
  }

  bool boolValue(String key) {
    final raw = _values[key];
    if (raw is bool) return raw;
    if (raw is String) return raw.toLowerCase() == 'true';
    return defaults[key] as bool? ?? false;
  }

  String get themeMode => stringValue('theme_mode');
  String get uiStyle => stringValue('ui_style');
  String get accentColor => stringValue('accent_color');
  int get uiRadius => intValue('ui_radius');
  int get uiScale => intValue('ui_scale');

  String get uploadChunkMode => stringValue('upload_chunk_mode');
  int get uploadChunkCount => intValue('upload_chunk_count');
  int get uploadChunkSizeBytes => intValue('upload_chunk_size_bytes');

  String get accountId => stringValue('buzzheavier_account_id');
  bool get notificationsEnabled => boolValue('notifications_enabled');
  bool get openAfterDownload => boolValue('open_after_download');
  String get clipboardClearMode => stringValue('auto_copy_clear_mode');
  int get clipboardClearSeconds => intValue('auto_copy_clear_seconds');

  Future<void> set(String key, Object? value) async {
    _values[key] = value;
    notifyListeners();
    await _prefs.setString(_storageKey, jsonEncode(_values));
  }

  Future<void> setMany(Map<String, Object?> updates) async {
    _values.addAll(updates);
    notifyListeners();
    await _prefs.setString(_storageKey, jsonEncode(_values));
  }

  Future<void> resetAppearance() => setMany({
    'theme_mode': 'system',
    'ui_style': 'discord',
    'accent_color': '',
    'ui_radius': 8,
    'ui_scale': 100,
  });

  Future<void> resetAll() async {
    _values
      ..clear()
      ..addAll(defaults);
    notifyListeners();
    await _prefs.setString(_storageKey, jsonEncode(_values));
  }
}

/// Makes the configuration available to every widget and rebuilds them on
/// change.
class AppConfigScope extends InheritedNotifier<AppConfig> {
  const AppConfigScope({
    super.key,
    required AppConfig config,
    required super.child,
  }) : super(notifier: config);

  static AppConfig of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppConfigScope>();
    assert(scope != null, 'AppConfigScope is missing from the widget tree');
    return scope!.notifier!;
  }
}
