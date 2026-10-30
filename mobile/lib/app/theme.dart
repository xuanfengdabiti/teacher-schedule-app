import 'package:flutter/material.dart';

const appTeal = Color(0xFF2F7F8A);
const appMint = Color(0xFF77B7A2);
const appInk = Color(0xFF1E2B2A);
const appMuted = Color(0xFF60706E);
const appCanvas = Color(0xFFF3F8F6);
const appLine = Color(0xFFE2ECE9);

ThemeData buildAppTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: appTeal,
    brightness: Brightness.light,
    surface: Colors.white,
  );
  final base = ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: appCanvas,
    fontFamilyFallback: const [
      'Noto Sans SC',
      'Microsoft YaHei',
      'PingFang SC',
    ],
  );
  return base.copyWith(
    textTheme: base.textTheme.copyWith(
      headlineSmall: const TextStyle(
        fontSize: 24,
        fontWeight: FontWeight.w700,
        color: appInk,
      ),
      titleLarge: const TextStyle(
        fontSize: 20,
        fontWeight: FontWeight.w700,
        color: appInk,
      ),
      titleMedium: const TextStyle(
        fontSize: 18,
        fontWeight: FontWeight.w600,
        color: appInk,
      ),
      bodyLarge: const TextStyle(fontSize: 16, height: 1.45, color: appInk),
      bodyMedium: const TextStyle(fontSize: 16, height: 1.45, color: appInk),
      bodySmall: const TextStyle(fontSize: 14, height: 1.4, color: appMuted),
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: appCanvas,
      surfaceTintColor: Colors.transparent,
      foregroundColor: appInk,
      centerTitle: false,
      titleTextStyle: TextStyle(
        fontSize: 20,
        fontWeight: FontWeight.w700,
        color: appInk,
      ),
    ),
    cardTheme: CardThemeData(
      color: Colors.white,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: const BorderSide(color: appLine),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: const Color(0xFFF7FAF9),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: appLine),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: appLine),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: appTeal, width: 1.5),
      ),
      labelStyle: const TextStyle(fontSize: 15, color: appMuted),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: appTeal,
        foregroundColor: Colors.white,
        minimumSize: const Size(64, 52),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: appTeal,
        minimumSize: const Size(64, 50),
        side: const BorderSide(color: appTeal),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: Colors.white,
      elevation: 0,
      height: 72,
      indicatorColor: const Color(0xFFDDEFEA),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (states) => TextStyle(
          fontSize: 13,
          fontWeight: states.contains(WidgetState.selected)
              ? FontWeight.w600
              : FontWeight.w500,
          color: states.contains(WidgetState.selected) ? appTeal : appMuted,
        ),
      ),
      iconTheme: WidgetStateProperty.resolveWith(
        (states) => IconThemeData(
          color: states.contains(WidgetState.selected) ? appTeal : appMuted,
          size: 24,
        ),
      ),
    ),
    dividerTheme: const DividerThemeData(color: appLine, thickness: 1),
  );
}
