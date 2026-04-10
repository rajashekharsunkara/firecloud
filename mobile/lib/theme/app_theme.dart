import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Elegant monochrome Material Design 3 theme for FireCloud.
/// Black, white, and grey with subtle accent for important actions.

class AppTheme {
  // Monochrome palette
  static const _black = Color(0xFF000000);
  static const _white = Color(0xFFFFFFFF);
  static const _grey50 = Color(0xFFFAFAFA);
  static const _grey100 = Color(0xFFF5F5F5);
  static const _grey200 = Color(0xFFEEEEEE);
  static const _grey300 = Color(0xFFE0E0E0);
  static const _grey400 = Color(0xFFBDBDBD);
  static const _grey500 = Color(0xFF9E9E9E);
  static const _grey600 = Color(0xFF757575);
  static const _grey700 = Color(0xFF616161);
  static const _grey800 = Color(0xFF424242);
  static const _grey900 = Color(0xFF212121);

  // Accent for important actions (subtle teal/cyan)
  static const _accent = Color(0xFF00BFA5);
  static const _accentDark = Color(0xFF1DE9B6);

  // Error color
  static const _error = Color(0xFFCF6679);
  static const _errorLight = Color(0xFFB00020);

  static ThemeData lightTheme() {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      scaffoldBackgroundColor: _white,
      colorScheme: const ColorScheme.light(
        primary: _black,
        onPrimary: _white,
        primaryContainer: _grey100,
        onPrimaryContainer: _black,
        secondary: _grey700,
        onSecondary: _white,
        secondaryContainer: _grey200,
        onSecondaryContainer: _black,
        tertiary: _accent,
        onTertiary: _white,
        tertiaryContainer: Color(0xFFB2DFDB),
        onTertiaryContainer: _black,
        surface: _white,
        onSurface: _black,
        surfaceContainerHighest: _grey100,
        outline: _grey400,
        outlineVariant: _grey200,
        error: _errorLight,
        onError: _white,
      ),
      appBarTheme: const AppBarTheme(
        centerTitle: false,
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: _white,
        foregroundColor: _black,
        systemOverlayStyle: SystemUiOverlayStyle.dark,
        titleTextStyle: TextStyle(
          color: _black,
          fontSize: 24,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.5,
        ),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: _grey50,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        margin: EdgeInsets.zero,
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        elevation: 0,
        highlightElevation: 0,
        backgroundColor: _black,
        foregroundColor: _white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          elevation: 0,
          backgroundColor: _black,
          foregroundColor: _white,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          elevation: 0,
          backgroundColor: _black,
          foregroundColor: _white,
          disabledBackgroundColor: _grey300,
          disabledForegroundColor: _grey600,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: _black,
          side: const BorderSide(color: _grey300, width: 1.5),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: _black,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: _white,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _grey300, width: 1.2),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _grey300, width: 1.2),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _black, width: 1.8),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 20,
          vertical: 18,
        ),
        labelStyle: const TextStyle(color: _grey700),
        floatingLabelStyle: const TextStyle(
          color: _black,
          fontWeight: FontWeight.w600,
        ),
        hintStyle: TextStyle(color: _grey500),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: _grey100,
        labelStyle: const TextStyle(color: _black, fontWeight: FontWeight.w500),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        side: BorderSide.none,
      ),
      navigationBarTheme: NavigationBarThemeData(
        elevation: 0,
        backgroundColor: _white,
        indicatorColor: _grey200,
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const TextStyle(
              color: _black,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            );
          }
          return const TextStyle(
            color: _grey500,
            fontSize: 12,
            fontWeight: FontWeight.w500,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const IconThemeData(color: _black, size: 24);
          }
          return const IconThemeData(color: _grey500, size: 24);
        }),
      ),
      dividerTheme: const DividerThemeData(
        color: _grey200,
        thickness: 1,
        space: 1,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: _grey900,
        contentTextStyle: const TextStyle(color: _white),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: _black,
        linearTrackColor: _grey200,
        circularTrackColor: _grey200,
      ),
      sliderTheme: SliderThemeData(
        activeTrackColor: _black,
        inactiveTrackColor: _grey300,
        thumbColor: _black,
        overlayColor: _black.withValues(alpha: 0.1),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return _black;
          return _grey400;
        }),
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return _grey300;
          return _grey200;
        }),
      ),
      textTheme: _buildTextTheme(Brightness.light),
    );
  }

  static ThemeData darkTheme() {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: _black,
      colorScheme: const ColorScheme.dark(
        primary: _white,
        onPrimary: _black,
        primaryContainer: _grey900,
        onPrimaryContainer: _white,
        secondary: _grey400,
        onSecondary: _black,
        secondaryContainer: _grey800,
        onSecondaryContainer: _white,
        tertiary: _accentDark,
        onTertiary: _black,
        tertiaryContainer: Color(0xFF004D40),
        onTertiaryContainer: _white,
        surface: _grey900,
        onSurface: _white,
        surfaceContainerHighest: _grey800,
        outline: _grey600,
        outlineVariant: _grey800,
        error: _error,
        onError: _black,
      ),
      appBarTheme: const AppBarTheme(
        centerTitle: false,
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: _black,
        foregroundColor: _white,
        systemOverlayStyle: SystemUiOverlayStyle.light,
        titleTextStyle: TextStyle(
          color: _white,
          fontSize: 24,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.5,
        ),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: _grey900,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        margin: EdgeInsets.zero,
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        elevation: 0,
        highlightElevation: 0,
        backgroundColor: _white,
        foregroundColor: _black,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          elevation: 0,
          backgroundColor: _grey300,
          foregroundColor: _black,
          disabledBackgroundColor: _grey800,
          disabledForegroundColor: _grey600,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          elevation: 0,
          backgroundColor: _grey300,
          foregroundColor: _black,
          disabledBackgroundColor: _grey800,
          disabledForegroundColor: _grey600,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: _white,
          side: const BorderSide(color: _grey700, width: 1.5),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: _white,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: _grey800,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _grey600, width: 1.2),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _grey600, width: 1.2),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: _white, width: 1.8),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 20,
          vertical: 18,
        ),
        labelStyle: const TextStyle(color: _grey400),
        floatingLabelStyle: const TextStyle(
          color: _white,
          fontWeight: FontWeight.w600,
        ),
        hintStyle: TextStyle(color: _grey500),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: _grey800,
        labelStyle: const TextStyle(color: _white, fontWeight: FontWeight.w500),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        side: BorderSide.none,
      ),
      navigationBarTheme: NavigationBarThemeData(
        elevation: 0,
        backgroundColor: _black,
        indicatorColor: _grey800,
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const TextStyle(
              color: _white,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            );
          }
          return const TextStyle(
            color: _grey500,
            fontSize: 12,
            fontWeight: FontWeight.w500,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const IconThemeData(color: _white, size: 24);
          }
          return const IconThemeData(color: _grey500, size: 24);
        }),
      ),
      dividerTheme: const DividerThemeData(
        color: _grey800,
        thickness: 1,
        space: 1,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: _grey100,
        contentTextStyle: const TextStyle(color: _black),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: _white,
        linearTrackColor: _grey800,
        circularTrackColor: _grey800,
      ),
      sliderTheme: SliderThemeData(
        activeTrackColor: _white,
        inactiveTrackColor: _grey700,
        thumbColor: _white,
        overlayColor: _white.withValues(alpha: 0.1),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return _grey200;
          return _grey600;
        }),
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return _grey500;
          return _grey800;
        }),
      ),
      textTheme: _buildTextTheme(Brightness.dark),
    );
  }

  static TextTheme _buildTextTheme(Brightness brightness) {
    final color = brightness == Brightness.light ? _black : _white;
    final muted = brightness == Brightness.light ? _grey600 : _grey400;

    return TextTheme(
      displayLarge: TextStyle(
        color: color,
        fontSize: 57,
        fontWeight: FontWeight.w400,
        letterSpacing: -0.25,
      ),
      displayMedium: TextStyle(
        color: color,
        fontSize: 45,
        fontWeight: FontWeight.w400,
      ),
      displaySmall: TextStyle(
        color: color,
        fontSize: 36,
        fontWeight: FontWeight.w400,
      ),
      headlineLarge: TextStyle(
        color: color,
        fontSize: 32,
        fontWeight: FontWeight.w600,
        letterSpacing: -0.5,
      ),
      headlineMedium: TextStyle(
        color: color,
        fontSize: 28,
        fontWeight: FontWeight.w600,
        letterSpacing: -0.5,
      ),
      headlineSmall: TextStyle(
        color: color,
        fontSize: 24,
        fontWeight: FontWeight.w600,
      ),
      titleLarge: TextStyle(
        color: color,
        fontSize: 22,
        fontWeight: FontWeight.w600,
      ),
      titleMedium: TextStyle(
        color: color,
        fontSize: 16,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.15,
      ),
      titleSmall: TextStyle(
        color: color,
        fontSize: 14,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.1,
      ),
      bodyLarge: TextStyle(
        color: color,
        fontSize: 16,
        fontWeight: FontWeight.w400,
        letterSpacing: 0.15,
      ),
      bodyMedium: TextStyle(
        color: color,
        fontSize: 14,
        fontWeight: FontWeight.w400,
        letterSpacing: 0.25,
      ),
      bodySmall: TextStyle(
        color: muted,
        fontSize: 12,
        fontWeight: FontWeight.w400,
        letterSpacing: 0.4,
      ),
      labelLarge: TextStyle(
        color: color,
        fontSize: 14,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.1,
      ),
      labelMedium: TextStyle(
        color: muted,
        fontSize: 12,
        fontWeight: FontWeight.w500,
        letterSpacing: 0.5,
      ),
      labelSmall: TextStyle(
        color: muted,
        fontSize: 11,
        fontWeight: FontWeight.w500,
        letterSpacing: 0.5,
      ),
    );
  }
}
