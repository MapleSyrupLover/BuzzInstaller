import 'package:flutter/material.dart';

import 'palette.dart';
import 'style_presets.dart';

/// Build the Material 3 theme from a Buzz palette and style preset.
ThemeData buildBuzzTheme({
  required BuzzPalette palette,
  required StylePreset preset,
  required double radius,
}) {
  final brightness = palette.isDark ? Brightness.dark : Brightness.light;
  final scheme = ColorScheme(
    brightness: brightness,
    primary: palette.accent,
    onPrimary: palette.textOnAccent,
    primaryContainer: palette.accentSoft,
    onPrimaryContainer: palette.textPrimary,
    secondary: palette.accent,
    onSecondary: palette.textOnAccent,
    secondaryContainer: palette.accentSoft,
    onSecondaryContainer: palette.textPrimary,
    surface: palette.surface,
    onSurface: palette.textPrimary,
    surfaceContainerHighest: palette.surfaceAlt,
    onSurfaceVariant: palette.textSecondary,
    outline: palette.border,
    outlineVariant: palette.border,
    error: palette.error,
    onError: Colors.white,
    shadow: Colors.black,
    scrim: Colors.black54,
    inverseSurface: palette.textPrimary,
    onInverseSurface: palette.surface,
  );

  final base = ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
  );

  final textTheme = base.textTheme
      .apply(
        bodyColor: palette.textPrimary,
        displayColor: palette.textPrimary,
      )
      .copyWith(
        headlineSmall: TextStyle(
          fontSize: 22,
          fontWeight: FontWeight.w700,
          color: palette.textPrimary,
        ),
        titleLarge: TextStyle(
          fontSize: 18,
          fontWeight: FontWeight.w700,
          color: palette.textPrimary,
        ),
        titleMedium: TextStyle(
          fontSize: 15,
          fontWeight: FontWeight.w600,
          color: palette.textPrimary,
        ),
        bodyMedium: TextStyle(
          fontSize: 14,
          height: 1.4,
          color: palette.textPrimary,
        ),
        bodySmall: TextStyle(
          fontSize: 12,
          height: 1.35,
          color: palette.textSecondary,
        ),
        labelLarge: TextStyle(
          fontSize: 14,
          fontWeight: FontWeight.w600,
          color: palette.textPrimary,
        ),
      );

  OutlineInputBorder inputBorder(Color color, double width) => OutlineInputBorder(
    borderRadius: BorderRadius.circular(radius),
    borderSide: BorderSide(color: color, width: width),
  );

  return base.copyWith(
    extensions: <ThemeExtension<dynamic>>[
      BuzzThemeData(palette: palette, preset: preset, radius: radius),
    ],
    scaffoldBackgroundColor: palette.background,
    canvasColor: palette.background,
    dividerColor: palette.border,
    textTheme: textTheme,
    iconTheme: IconThemeData(color: palette.textSecondary, size: 22),
    appBarTheme: AppBarTheme(
      backgroundColor: palette.background,
      foregroundColor: palette.textPrimary,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(
        fontSize: 18,
        fontWeight: FontWeight.w700,
        color: palette.textPrimary,
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: palette.surface,
      surfaceTintColor: Colors.transparent,
      indicatorColor: palette.accentSoft,
      elevation: 0,
      height: 68,
      labelBehavior: preset.navLabelBehavior
          ? NavigationDestinationLabelBehavior.alwaysShow
          : NavigationDestinationLabelBehavior.onlyShowSelected,
      labelTextStyle: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return TextStyle(
          fontSize: 11,
          fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
          color: selected ? palette.accent : palette.textSecondary,
        );
      }),
      iconTheme: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return IconThemeData(
          size: 23,
          color: selected ? palette.accent : palette.textSecondary,
        );
      }),
    ),
    cardTheme: CardThemeData(
      elevation: 0,
      margin: EdgeInsets.zero,
      color: palette.surface,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(radius),
        side: BorderSide(color: palette.border),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: palette.inputBg,
      hintStyle: TextStyle(color: palette.textTertiary, fontSize: 14),
      labelStyle: TextStyle(color: palette.textSecondary),
      contentPadding: const EdgeInsets.symmetric(
        horizontal: 14,
        vertical: 14,
      ),
      border: inputBorder(palette.border, 1),
      enabledBorder: inputBorder(palette.border, 1),
      focusedBorder: inputBorder(palette.accent, 1.5),
      errorBorder: inputBorder(palette.error, 1),
      focusedErrorBorder: inputBorder(palette.error, 1.5),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: palette.accent,
        foregroundColor: palette.textOnAccent,
        disabledBackgroundColor: palette.hoverBg,
        disabledForegroundColor: palette.textTertiary,
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 15),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radius),
        ),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: palette.textPrimary,
        side: BorderSide(color: palette.border),
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radius),
        ),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: palette.accent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radius),
        ),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(foregroundColor: palette.textSecondary),
    ),
    sliderTheme: SliderThemeData(
      activeTrackColor: palette.accent,
      inactiveTrackColor: palette.progressBg,
      thumbColor: palette.accent,
      overlayColor: palette.accent.withValues(alpha: 0.12),
      trackHeight: 4,
      valueIndicatorColor: palette.accent,
      valueIndicatorTextStyle: TextStyle(color: palette.textOnAccent),
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: palette.accent,
      linearTrackColor: palette.progressBg,
      linearMinHeight: 8,
    ),
    dividerTheme: DividerThemeData(
      color: palette.border,
      thickness: 1,
      space: 1,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: palette.isDark
          ? palette.surfaceAlt
          : const Color(0xFF2B2D31),
      contentTextStyle: const TextStyle(color: Colors.white, fontSize: 13),
      behavior: SnackBarBehavior.floating,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(radius + 2),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: palette.surface,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(radius + 6),
      ),
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: palette.surface,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(radius + 8),
        ),
      ),
    ),
    listTileTheme: ListTileThemeData(
      iconColor: palette.textSecondary,
      textColor: palette.textPrimary,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(radius),
      ),
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (states) => states.contains(WidgetState.selected)
            ? palette.textOnAccent
            : palette.surface,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (states) => states.contains(WidgetState.selected)
            ? palette.accent
            : palette.progressBg,
      ),
      trackOutlineColor: WidgetStateProperty.resolveWith(
        (states) => states.contains(WidgetState.selected)
            ? palette.accent
            : palette.border,
      ),
    ),
    dropdownMenuTheme: DropdownMenuThemeData(
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: palette.inputBg,
        border: inputBorder(palette.border, 1),
        enabledBorder: inputBorder(palette.border, 1),
        focusedBorder: inputBorder(palette.accent, 1.5),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 14,
          vertical: 14,
        ),
      ),
    ),
    tooltipTheme: TooltipThemeData(
      decoration: BoxDecoration(
        color: palette.isDark ? palette.surfaceAlt : const Color(0xFF2B2D31),
        borderRadius: BorderRadius.circular(6),
      ),
      textStyle: const TextStyle(color: Colors.white, fontSize: 12),
    ),
  );
}

/// Palette and density values shared with every widget.
@immutable
class BuzzThemeData extends ThemeExtension<BuzzThemeData> {
  const BuzzThemeData({
    required this.palette,
    required this.preset,
    required this.radius,
  });

  final BuzzPalette palette;
  final StylePreset preset;
  final double radius;

  static BuzzThemeData of(BuildContext context) {
    final data = Theme.of(context).extension<BuzzThemeData>();
    assert(data != null, 'BuzzThemeData missing from the theme');
    return data!;
  }

  @override
  BuzzThemeData copyWith({
    BuzzPalette? palette,
    StylePreset? preset,
    double? radius,
  }) {
    return BuzzThemeData(
      palette: palette ?? this.palette,
      preset: preset ?? this.preset,
      radius: radius ?? this.radius,
    );
  }

  @override
  BuzzThemeData lerp(ThemeExtension<BuzzThemeData>? other, double t) {
    if (other is! BuzzThemeData) return this;
    return t < 0.5 ? this : other;
  }
}
