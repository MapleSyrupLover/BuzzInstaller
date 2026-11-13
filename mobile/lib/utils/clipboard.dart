import 'dart:async';

import 'package:flutter/services.dart';

import '../services/config_service.dart';

/// Copy [links] immediately and honour the configured clipboard lifetime.
///
/// The timeout is *not* awaited, so a "clear after 10 s" setting never holds
/// up the transfer UI that called this.
Future<void> copyLinks(List<String> links, AppConfig config) async {
  if (links.isEmpty) return;
  final text = links.join('\n');
  await Clipboard.setData(ClipboardData(text: text));
  if (config.clipboardClearMode != 'timeout') return;

  final seconds = config.clipboardClearSeconds.clamp(2, 600).toInt();
  unawaited(_clearAfter(text, seconds));
}

Future<void> _clearAfter(String text, int seconds) async {
  await Future<void>.delayed(Duration(seconds: seconds));
  try {
    final current = await Clipboard.getData(Clipboard.kTextPlain);
    // Only clear when the user has not copied something else meanwhile.
    if (current?.text == text) {
      await Clipboard.setData(const ClipboardData(text: ''));
    }
  } catch (_) {
    // The clipboard can be locked by another app; nothing to do.
  }
}
