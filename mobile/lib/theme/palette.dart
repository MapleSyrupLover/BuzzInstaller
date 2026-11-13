import 'dart:ui';

/// Discord-inspired colour palette used across the app.
///
/// Mirrors `src/BuzzInstaller/ui/theme.py` so the desktop and mobile apps
/// look like the same product.
class BuzzPalette {
  const BuzzPalette({
    required this.name,
    required this.isDark,
    required this.background,
    required this.surface,
    required this.surfaceAlt,
    required this.border,
    required this.inputBg,
    required this.textPrimary,
    required this.textSecondary,
    required this.textTertiary,
    required this.textOnAccent,
    required this.accent,
    required this.accentHover,
    required this.accentSoft,
    required this.progressBg,
    required this.error,
    required this.success,
    required this.warning,
    required this.hoverBg,
    required this.selection,
  });

  final String name;
  final bool isDark;
  final Color background;
  final Color surface;
  final Color surfaceAlt;
  final Color border;
  final Color inputBg;
  final Color textPrimary;
  final Color textSecondary;
  final Color textTertiary;
  final Color textOnAccent;
  final Color accent;
  final Color accentHover;
  final Color accentSoft;
  final Color progressBg;
  final Color error;
  final Color success;
  final Color warning;
  final Color hoverBg;
  final Color selection;

  BuzzPalette withAccent(Color color) {
    final softWeight = isDark ? 0.24 : 0.12;
    return BuzzPalette(
      name: name,
      isDark: isDark,
      background: background,
      surface: surface,
      surfaceAlt: surfaceAlt,
      border: border,
      inputBg: inputBg,
      textPrimary: textPrimary,
      textSecondary: textSecondary,
      textTertiary: textTertiary,
      textOnAccent: textOnAccent,
      accent: color,
      accentHover: _shift(color, 0.12, true),
      accentSoft: Color.lerp(surface, color, softWeight)!,
      progressBg: progressBg,
      error: error,
      success: success,
      warning: warning,
      hoverBg: hoverBg,
      selection: Color.lerp(
        surface,
        color,
        isDark ? 0.32 : 0.18,
      )!,
    );
  }
}

Color _shift(Color color, double amount, bool lighten) {
  final target = lighten ? const Color(0xFFFFFFFF) : const Color(0xFF000000);
  return Color.lerp(color, target, amount)!;
}

/// Parse `#RRGGBB` / `RRGGBB` / `#AARRGGBB` into a colour.
Color? parseHexColor(String? raw) {
  final text = (raw ?? '').trim().replaceFirst('#', '');
  if (text.isEmpty) return null;
  final normalised = text.length == 6 ? 'FF$text' : text;
  final value = int.tryParse(normalised, radix: 16);
  if (value == null) return null;
  return Color(value);
}

String hexFromColor(Color color) {
  final value = color.toARGB32() & 0xFFFFFF;
  return '#${value.toRadixString(16).padLeft(6, '0').toUpperCase()}';
}

const lightPalette = BuzzPalette(
  name: 'Light',
  isDark: false,
  background: Color(0xFFF2F3F5),
  surface: Color(0xFFFFFFFF),
  surfaceAlt: Color(0xFFF7F8FA),
  border: Color(0xFFE3E5E8),
  inputBg: Color(0xFFFFFFFF),
  textPrimary: Color(0xFF060607),
  textSecondary: Color(0xFF5C5E66),
  textTertiary: Color(0xFF80848E),
  textOnAccent: Color(0xFFFFFFFF),
  accent: Color(0xFF5865F2),
  accentHover: Color(0xFF4752C4),
  accentSoft: Color(0xFFEAEBFF),
  progressBg: Color(0xFFE3E5E8),
  error: Color(0xFFD83C3E),
  success: Color(0xFF248046),
  warning: Color(0xFFB7791F),
  hoverBg: Color(0xFFEBEDEF),
  selection: Color(0xFFDDE1FF),
);

const darkPalette = BuzzPalette(
  name: 'Dark',
  isDark: true,
  background: Color(0xFF313338),
  surface: Color(0xFF2B2D31),
  surfaceAlt: Color(0xFF35373C),
  border: Color(0xFF3F4147),
  inputBg: Color(0xFF1E1F22),
  textPrimary: Color(0xFFF2F3F5),
  textSecondary: Color(0xFFB5BAC1),
  textTertiary: Color(0xFF949BA4),
  textOnAccent: Color(0xFFFFFFFF),
  accent: Color(0xFF5865F2),
  accentHover: Color(0xFF4752C4),
  accentSoft: Color(0xFF3A3D63),
  progressBg: Color(0xFF3F4147),
  error: Color(0xFFF23F43),
  success: Color(0xFF23A55A),
  warning: Color(0xFFF0B232),
  hoverBg: Color(0xFF35373C),
  selection: Color(0xFF404675),
);

BuzzPalette paletteFor({required bool dark, String? accent}) {
  final base = dark ? darkPalette : lightPalette;
  final accentColor = parseHexColor(accent);
  if (accentColor == null) return base;
  return base.withAccent(accentColor);
}
