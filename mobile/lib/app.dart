import 'package:flutter/material.dart';

import 'pages/about_page.dart';
import 'pages/customization_page.dart';
import 'pages/downloads_page.dart';
import 'pages/settings_page.dart';
import 'pages/uploads_page.dart';
import 'services/config_service.dart';
import 'services/history_service.dart';
import 'services/incoming_link.dart';
import 'theme/app_theme.dart';
import 'theme/palette.dart';
import 'theme/style_presets.dart';

const _destinations = <_Destination>[
  _Destination('Uploads', Icons.upload_file_outlined, Icons.upload_file),
  _Destination('Downloads', Icons.download_outlined, Icons.download),
  _Destination('Settings', Icons.settings_outlined, Icons.settings),
  _Destination('Style', Icons.palette_outlined, Icons.palette),
  _Destination('About', Icons.info_outline, Icons.info),
];

class _Destination {
  const _Destination(this.label, this.icon, this.selectedIcon);

  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

class BuzzApp extends StatelessWidget {
  const BuzzApp({super.key, required this.config, required this.history});

  final AppConfig config;
  final HistoryService history;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: config,
      builder: (context, _) {
        final preset = stylePresetFor(config.uiStyle);
        final radius = config.uiRadius.clamp(0, 18).toDouble();
        final themeMode = switch (config.themeMode) {
          'dark' => ThemeMode.dark,
          'light' => ThemeMode.light,
          _ => ThemeMode.system,
        };
        final fontScale = config.uiScale / 100;

        ThemeData themeFor(bool dark) => buildBuzzTheme(
          palette: paletteFor(dark: dark, accent: config.accentColor),
          preset: preset,
          radius: radius,
        );

        return AppConfigScope(
          config: config,
          child: MaterialApp(
            title: 'BuzzInstaller',
            debugShowCheckedModeBanner: false,
            theme: themeFor(false),
            darkTheme: themeFor(true),
            themeMode: themeMode,
            builder: (context, child) {
              final systemScale =
                  MediaQuery.textScalerOf(context).scale(100) / 100;
              return MediaQuery(
                data: MediaQuery.of(context).copyWith(
                  textScaler: TextScaler.linear(systemScale * fontScale),
                ),
                child: child ?? const SizedBox.shrink(),
              );
            },
            home: _HomeShell(history: history),
          ),
        );
      },
    );
  }
}

class _HomeShell extends StatefulWidget {
  const _HomeShell({required this.history});

  final HistoryService history;

  @override
  State<_HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<_HomeShell> {
  int _index = 0;

  @override
  void initState() {
    super.initState();
    IncomingLink.latest.addListener(_onIncomingLink);
  }

  @override
  void dispose() {
    IncomingLink.latest.removeListener(_onIncomingLink);
    super.dispose();
  }

  void _onIncomingLink() {
    if (IncomingLink.latest.value == null) return;
    if (!mounted) return;
    setState(() => _index = 1);
  }

  @override
  Widget build(BuildContext context) {
    final theme = BuzzThemeData.of(context);
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 20,
        title: Row(
          children: [
            Container(
              width: 26,
              height: 26,
              decoration: BoxDecoration(
                color: theme.palette.accent,
                borderRadius: BorderRadius.circular(7),
              ),
              child: const Icon(Icons.bolt, size: 17, color: Colors.white),
            ),
            const SizedBox(width: 10),
            Text(_destinations[_index].label),
          ],
        ),
      ),
      body: SafeArea(
        top: false,
        child: IndexedStack(
          index: _index,
          children: [
            UploadsPage(history: widget.history),
            DownloadsPage(history: widget.history),
            const SettingsPage(),
            const CustomizationPage(),
            const AboutPage(),
          ],
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (index) => setState(() => _index = index),
        destinations: [
          for (final destination in _destinations)
            NavigationDestination(
              icon: Icon(destination.icon),
              selectedIcon: Icon(destination.selectedIcon),
              label: destination.label,
              tooltip: destination.label,
            ),
        ],
      ),
    );
  }
}
