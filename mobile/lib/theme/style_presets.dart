/// Interface style presets: density + shape, matching the desktop app.
class StylePreset {
  const StylePreset({
    required this.key,
    required this.label,
    required this.radius,
    required this.spacing,
    required this.cardPadding,
    required this.pagePadding,
    required this.navLabelBehavior,
  });

  final String key;
  final String label;
  final double radius;
  final double spacing;
  final double cardPadding;
  final double pagePadding;
  final bool navLabelBehavior;
}

const stylePresets = <String, StylePreset>{
  'discord': StylePreset(
    key: 'discord',
    label: 'Discord',
    radius: 8,
    spacing: 14,
    cardPadding: 16,
    pagePadding: 16,
    navLabelBehavior: true,
  ),
  'comfortable': StylePreset(
    key: 'comfortable',
    label: 'Comfortable',
    radius: 12,
    spacing: 18,
    cardPadding: 20,
    pagePadding: 20,
    navLabelBehavior: true,
  ),
  'compact': StylePreset(
    key: 'compact',
    label: 'Compact',
    radius: 6,
    spacing: 10,
    cardPadding: 12,
    pagePadding: 12,
    navLabelBehavior: false,
  ),
};

StylePreset stylePresetFor(String? key) =>
    stylePresets[key] ?? stylePresets['discord']!;
