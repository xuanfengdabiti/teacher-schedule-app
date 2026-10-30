import 'package:flutter/material.dart';

const appTeal = Color(0xFF287CF4);
const appMint = Color(0xFF2E9E6B);
const appInk = Color(0xFF202632);
const appMuted = Color(0xFF8A93A3);
const appCanvas = Color(0xFFF7F9FC);
const appLine = Color(0xFFEDF0F5);

const appBlueSoft = Color(0xFFEAF2FF);
const appSuccessSoft = Color(0xFFE9F7F0);
const appOrange = Color(0xFFD9822B);
const appOrangeSoft = Color(0xFFFFF3E6);

ThemeData buildAppTheme() {
  final scheme =
      ColorScheme.fromSeed(
        seedColor: appTeal,
        brightness: Brightness.light,
        surface: Colors.white,
      ).copyWith(
        primary: appTeal,
        onPrimary: Colors.white,
        secondary: appMint,
        onSecondary: Colors.white,
        surface: Colors.white,
        onSurface: appInk,
        outline: appLine,
        outlineVariant: appLine,
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
        fontSize: 20,
        height: 1.3,
        fontWeight: FontWeight.w700,
        color: appInk,
      ),
      titleLarge: const TextStyle(
        fontSize: 19,
        height: 1.3,
        fontWeight: FontWeight.w700,
        color: appInk,
      ),
      titleMedium: const TextStyle(
        fontSize: 16,
        height: 1.35,
        fontWeight: FontWeight.w600,
        color: appInk,
      ),
      titleSmall: const TextStyle(
        fontSize: 14,
        height: 1.35,
        fontWeight: FontWeight.w600,
        color: appInk,
      ),
      bodyLarge: const TextStyle(fontSize: 14, height: 1.45, color: appInk),
      bodyMedium: const TextStyle(fontSize: 13, height: 1.45, color: appInk),
      bodySmall: const TextStyle(fontSize: 12, height: 1.4, color: appMuted),
      labelLarge: const TextStyle(
        fontSize: 14,
        height: 1.2,
        fontWeight: FontWeight.w600,
        color: appInk,
      ),
      labelMedium: const TextStyle(
        fontSize: 12,
        height: 1.25,
        fontWeight: FontWeight.w500,
        color: appMuted,
      ),
      labelSmall: const TextStyle(
        fontSize: 11,
        height: 1.25,
        fontWeight: FontWeight.w500,
        color: appMuted,
      ),
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: appCanvas,
      surfaceTintColor: Colors.transparent,
      foregroundColor: appInk,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      toolbarHeight: 54,
      titleSpacing: 16,
      iconTheme: IconThemeData(color: appInk, size: 21),
      actionsIconTheme: IconThemeData(color: appInk, size: 21),
      titleTextStyle: TextStyle(
        fontSize: 19,
        fontWeight: FontWeight.w700,
        color: appInk,
      ),
    ),
    iconTheme: const IconThemeData(color: appInk, size: 21),
    cardTheme: CardThemeData(
      color: Colors.white,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: const BorderSide(color: appLine),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: 13, vertical: 13),
      hintStyle: const TextStyle(fontSize: 13, color: appMuted),
      labelStyle: const TextStyle(fontSize: 13, color: appMuted),
      helperStyle: const TextStyle(fontSize: 11.5, color: appMuted),
      prefixIconColor: appMuted,
      suffixIconColor: appMuted,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: appLine),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: appLine),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: appTeal, width: 1.4),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: Color(0xFFD9544D)),
      ),
      focusedErrorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: Color(0xFFD9544D), width: 1.4),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: appTeal,
        foregroundColor: Colors.white,
        minimumSize: const Size(64, 42),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: appTeal,
        minimumSize: const Size(64, 42),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        side: const BorderSide(color: appLine),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: appTeal,
        minimumSize: const Size(40, 36),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        textStyle: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: Colors.white,
      elevation: 0,
      height: 60,
      indicatorColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      labelTextStyle: WidgetStateProperty.resolveWith(
        (states) => TextStyle(
          fontSize: 11,
          height: 1.2,
          fontWeight: states.contains(WidgetState.selected)
              ? FontWeight.w600
              : FontWeight.w500,
          color: states.contains(WidgetState.selected) ? appTeal : appMuted,
        ),
      ),
      iconTheme: WidgetStateProperty.resolveWith(
        (states) => IconThemeData(
          color: states.contains(WidgetState.selected) ? appTeal : appMuted,
          size: 21,
        ),
      ),
    ),
    dividerTheme: const DividerThemeData(color: appLine, thickness: 1),
    progressIndicatorTheme: const ProgressIndicatorThemeData(color: appTeal),
    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: Colors.white,
      modalBackgroundColor: Colors.white,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: Colors.white,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      titleTextStyle: const TextStyle(
        fontSize: 17,
        fontWeight: FontWeight.w700,
        color: appInk,
      ),
      contentTextStyle: const TextStyle(
        fontSize: 14,
        height: 1.5,
        color: appInk,
      ),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: appInk,
      contentTextStyle: const TextStyle(fontSize: 13, color: Colors.white),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
    ),
  );
}
