import 'package:flutter/services.dart';

import '../services/config_service.dart';

/// Copy [links] and apply the configured clipboard lifetime.
Future<void> copyLinks(List<String> links, AppConfig config) async {
  if (links.isEmpty) return;
  final text = links.join('\n');
  await Clipboard.setData(ClipboardData(text: text));
  if (config.clipboardClearMode != 'timeout') return;

  final seconds = config.clipboardClearSeconds.clamp(2, 600).toInt();
  await Future<void>.delayed(Duration(seconds: seconds));
  final current = await Clipboard.getData(Clipboard.kTextPlain);
  if (current?.text == text) {
    await Clipboard.setData(const ClipboardData(text: ''));
  }
}
