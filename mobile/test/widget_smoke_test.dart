import 'package:buzzinstaller_mobile/app.dart';
import 'package:buzzinstaller_mobile/services/config_service.dart';
import 'package:buzzinstaller_mobile/services/history_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('every tab renders on a phone-sized screen', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final config = await AppConfig.load();
    final history = await HistoryService.load();

    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(BuzzApp(config: config, history: history));
    await tester.pumpAndSettle();

    expect(find.text('Upload to BuzzHeavier'), findsOneWidget);

    await tester.tap(find.text('Downloads'));
    await tester.pumpAndSettle();
    expect(find.text('Download from BuzzHeavier'), findsOneWidget);

    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Upload chunking'), findsOneWidget);

    await tester.tap(find.text('Style'));
    await tester.pumpAndSettle();
    expect(find.text('Accent colour'), findsOneWidget);

    await tester.tap(find.text('About'));
    await tester.pumpAndSettle();
    expect(find.text('BuzzInstaller'), findsWidgets);
  });

  testWidgets('theme mode and accent changes are persisted', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final config = await AppConfig.load();
    final history = await HistoryService.load();

    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(BuzzApp(config: config, history: history));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Style'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Dark'));
    await tester.pumpAndSettle();
    expect(config.themeMode, 'dark');

    await tester.tap(find.byTooltip('#23A55A'));
    await tester.pumpAndSettle();
    expect(config.accentColor, '#23A55A');
  });
}
