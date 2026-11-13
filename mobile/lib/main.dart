import 'package:flutter/material.dart';

import 'app.dart';
import 'services/config_service.dart';
import 'services/history_service.dart';
import 'services/incoming_link.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final config = await AppConfig.load();
  final history = await HistoryService.load();
  await IncomingLink.init();
  runApp(BuzzApp(config: config, history: history));
}
