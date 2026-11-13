import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// BZ:// links handed to the app by Android (see `MainActivity.kt`).
class IncomingLink {
  static const MethodChannel _channel = MethodChannel('buzzinstaller/intent');

  /// Latest link received; the Downloads page listens for changes.
  static final ValueNotifier<String?> latest = ValueNotifier<String?>(null);

  static Future<void> init() async {
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'link' && call.arguments is String) {
        latest.value = call.arguments as String;
      }
    });
    try {
      final initial = await _channel.invokeMethod<String>('getInitialLink');
      if (initial != null && initial.isNotEmpty) {
        latest.value = initial;
      }
    } on PlatformException {
      // Not running on Android, or no link was supplied.
    } on MissingPluginException {
      // Widget tests and desktop builds do not register the channel.
    }
  }
}
