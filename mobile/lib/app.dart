import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/router.dart';
import 'app/theme.dart';
import 'features/profile/font_size_controller.dart';
import 'features/reminders/reminder_coordinator.dart';

/// Android Material 3 stretches edge content by default, which warps text and
/// cards at the end of a list. Keep normal scrolling and pull-to-refresh but
/// drop only the overscroll decoration.
final appScrollBehavior = const MaterialScrollBehavior().copyWith(
  overscroll: false,
);

class TeacherScheduleApp extends ConsumerWidget {
  const TeacherScheduleApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final fontChoice = ref.watch(fontSizeControllerProvider);
    return MaterialApp.router(
      title: '教师课表',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      scrollBehavior: appScrollBehavior,
      locale: const Locale('zh', 'CN'),
      supportedLocales: const [Locale('zh', 'CN')],
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      builder: (context, child) {
        // Following the system keeps the platform scaler untouched (including
        // non-linear scaling). Large choices add exactly one extra factor.
        Widget content;
        if (fontChoice == 'system') {
          content = child ?? const SizedBox.shrink();
        } else {
          final media = MediaQuery.of(context);
          final scale = textScaleForFontSize(fontChoice);
          content = MediaQuery(
            data: media.copyWith(
              textScaler: TextScaler.linear(
                media.textScaler.scale(1.0) * scale,
              ),
            ),
            child: child ?? const SizedBox.shrink(),
          );
        }
        // One coordinator instance wraps both font-size branches so reminder
        // setup and notification taps survive any font-size change.
        return ReminderCoordinator(child: content);
      },
      routerConfig: ref.watch(routerProvider),
    );
  }
}
