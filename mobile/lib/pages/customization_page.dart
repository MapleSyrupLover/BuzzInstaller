import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../services/config_service.dart';
import '../theme/app_theme.dart';
import '../theme/palette.dart';
import '../theme/style_presets.dart';
import '../widgets/components.dart';

const _accentOptions = <String>[
  '#5865F2',
  '#23A55A',
  '#F23F43',
  '#F0B232',
  '#EB459E',
  '#00A8FC',
  '#9B59B6',
  '#E67E22',
];

class CustomizationPage extends StatefulWidget {
  const CustomizationPage({super.key});

  @override
  State<CustomizationPage> createState() => _CustomizationPageState();
}

class _CustomizationPageState extends State<CustomizationPage> {
  final TextEditingController _hexController = TextEditingController();
  bool _initialised = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_initialised) return;
    _initialised = true;
    _hexController.text = AppConfigScope.of(context).accentColor;
  }

  @override
  void dispose() {
    _hexController.dispose();
    super.dispose();
  }

  Future<void> _applyHex(AppConfig config) async {
    final parsed = parseHexColor(_hexController.text);
    if (parsed == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Enter a colour like #5865F2.'),
        ),
      );
      return;
    }
    final hex = hexFromColor(parsed);
    _hexController.text = hex;
    await config.set('accent_color', hex);
  }

  @override
  Widget build(BuildContext context) {
    final config = AppConfigScope.of(context);
    final theme = BuzzThemeData.of(context);
    final palette = theme.palette;
    final preset = stylePresetFor(config.uiStyle);

    return PageBody(
      children: [
        SectionCard(
          title: 'Theme',
          subtitle: 'Follow the system or lock the app to light or dark.',
          child: SegmentedButton<String>(
            segments: const [
              ButtonSegment(
                value: 'system',
                label: Text('System'),
                icon: Icon(Icons.brightness_auto, size: 17),
              ),
              ButtonSegment(
                value: 'light',
                label: Text('Light'),
                icon: Icon(Icons.light_mode, size: 17),
              ),
              ButtonSegment(
                value: 'dark',
                label: Text('Dark'),
                icon: Icon(Icons.dark_mode, size: 17),
              ),
            ],
            selected: {config.themeMode},
            showSelectedIcon: false,
            onSelectionChanged: (selection) =>
                config.set('theme_mode', selection.first),
          ),
        ),
        SectionCard(
          title: 'Accent colour',
          subtitle: 'Used for buttons, links, selected navigation and progress.',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Wrap(
                spacing: 10,
                runSpacing: 10,
                children: [
                  for (final hex in _accentOptions)
                    _Swatch(
                      color: parseHexColor(hex)!,
                      selected:
                          hexFromColor(
                            parseHexColor(config.accentColor) ??
                                palette.accent,
                          ) ==
                          hex,
                      onTap: () {
                        _hexController.text = hex;
                        config.set('accent_color', hex);
                      },
                    ),
                ],
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _hexController,
                      autocorrect: false,
                      textCapitalization: TextCapitalization.characters,
                      inputFormatters: [
                        FilteringTextInputFormatter.allow(
                          RegExp('[#0-9a-fA-F]'),
                        ),
                      ],
                      decoration: const InputDecoration(
                        labelText: 'Custom hex',
                        hintText: '#5865F2',
                      ),
                      onSubmitted: (_) => _applyHex(config),
                    ),
                  ),
                  const SizedBox(width: 10),
                  OutlinedButton(
                    onPressed: () => _applyHex(config),
                    child: const Text('Apply'),
                  ),
                ],
              ),
              if (config.accentColor.isNotEmpty) ...[
                const SizedBox(height: 8),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: () {
                      _hexController.text = '';
                      config.set('accent_color', '');
                    },
                    icon: const Icon(Icons.restart_alt, size: 17),
                    label: const Text('Use the default colour'),
                  ),
                ),
              ],
            ],
          ),
        ),
        SectionCard(
          title: 'Interface style',
          subtitle: 'Density and corner shape of cards, buttons and navigation.',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final entry in stylePresets.values)
                    ChoiceChip(
                      label: Text(entry.label),
                      selected: config.uiStyle == entry.key,
                      onSelected: (_) => config.setMany({
                        'ui_style': entry.key,
                        'ui_radius': entry.radius.round(),
                      }),
                    ),
                ],
              ),
              const SizedBox(height: 18),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      'Corner radius',
                      style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                        color: palette.textSecondary,
                      ),
                    ),
                  ),
                  Text(
                    '${config.uiRadius}px',
                    style: TextStyle(
                      fontSize: 12,
                      color: palette.textTertiary,
                    ),
                  ),
                ],
              ),
              Slider(
                value: config.uiRadius.clamp(0, 18).toDouble(),
                min: 0,
                max: 18,
                divisions: 18,
                label: '${config.uiRadius}px',
                onChanged: (value) =>
                    config.set('ui_radius', value.round()),
              ),
              const SizedBox(height: 4),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      'Text size',
                      style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                        color: palette.textSecondary,
                      ),
                    ),
                  ),
                  Text(
                    '${config.uiScale}%',
                    style: TextStyle(
                      fontSize: 12,
                      color: palette.textTertiary,
                    ),
                  ),
                ],
              ),
              Slider(
                value: config.uiScale.clamp(85, 135).toDouble(),
                min: 85,
                max: 135,
                divisions: 10,
                label: '${config.uiScale}%',
                onChanged: (value) =>
                    config.set('ui_scale', value.round()),
              ),
            ],
          ),
        ),
        BuzzCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Preview',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: palette.textSecondary,
                ),
              ),
              const SizedBox(height: 12),
              Container(
                padding: EdgeInsets.all(preset.cardPadding),
                decoration: BoxDecoration(
                  color: palette.surfaceAlt,
                  borderRadius: BorderRadius.circular(theme.radius),
                  border: Border.all(color: palette.border),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(
                          Icons.bolt,
                          size: 18,
                          color: palette.accent,
                        ),
                        const SizedBox(width: 6),
                        Text(
                          'BuzzInstaller',
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w700,
                            color: palette.textPrimary,
                          ),
                        ),
                        const Spacer(),
                        StatusPill(
                          label: '${config.uiScale}%',
                          icon: Icons.text_fields,
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    Text(
                      'Uploads, downloads and links look like this with the '
                      'current style.',
                      style: TextStyle(
                        fontSize: 12.5,
                        height: 1.4,
                        color: palette.textSecondary,
                      ),
                    ),
                    const SizedBox(height: 12),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(999),
                      child: LinearProgressIndicator(
                        value: 0.62,
                        minHeight: 7,
                      ),
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        FilledButton(
                          onPressed: () {},
                          child: const Text('Upload'),
                        ),
                        const SizedBox(width: 10),
                        OutlinedButton(
                          onPressed: () {},
                          child: const Text('Copy link'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        BuzzCard(
          child: Row(
            children: [
              Expanded(
                child: Text(
                  'Reset appearance',
                  style: TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                    color: palette.textPrimary,
                  ),
                ),
              ),
              OutlinedButton(
                onPressed: () async {
                  await config.resetAppearance();
                  _hexController.text = '';
                },
                child: const Text('Reset'),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Swatch extends StatelessWidget {
  const _Swatch({
    required this.color,
    required this.selected,
    required this.onTap,
  });

  final Color color;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = BuzzThemeData.of(context);
    return Tooltip(
      message: hexFromColor(color),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(999),
        child: Container(
          width: 38,
          height: 38,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            border: Border.all(
              color: selected
                  ? theme.palette.textPrimary
                  : theme.palette.border,
              width: selected ? 2 : 1,
            ),
          ),
          child: selected
              ? const Icon(Icons.check, size: 18, color: Colors.white)
              : null,
        ),
      ),
    );
  }
}
