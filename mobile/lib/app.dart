import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/router.dart';
import 'app/reading_text_scaler.dart';
import 'app/theme.dart';
import 'features/profile/font_size_controller.dart';
import 'features/reminders/reminder_coordinator.dart';

const _readingScale = 1.15;

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
        // One reading baseline for every page, combined with the untouched
        // platform scaler and the account's optional preference.
        final media = MediaQuery.of(context);
        final scaler = ReadingTextScaler(
          upstream: media.textScaler,
          factor: _readingScale * textScaleForFontSize(fontChoice),
        );
        // Keep one coordinator instance across preference changes so reminder
        // listeners and notification routing stay attached.
        return MediaQuery(
          data: media.copyWith(textScaler: scaler),
          child: ReminderCoordinator(child: child ?? const SizedBox.shrink()),
        );
      },
      routerConfig: ref.watch(routerProvider),
    );
  }
}
