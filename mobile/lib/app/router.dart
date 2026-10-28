import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/account_status_page.dart';
import '../features/auth/auth_controller.dart';
import '../features/auth/login_page.dart';
import '../features/offerings/features_page.dart';
import '../features/profile/profile_page.dart';
import '../features/schedule/import_page.dart';
import '../features/schedule/session_detail_page.dart';
import '../features/schedule/today_page.dart';
import '../features/schedule/week_page.dart';
import '../features/notes/notes_page.dart';
import '../features/shell/app_shell.dart';
import 'splash_page.dart';

class _AuthRefresh extends ChangeNotifier {
  _AuthRefresh(Ref ref) {
    ref.listen(authControllerProvider, (previous, next) {
      notifyListeners();
    });
  }
}

String? _redirect(Ref ref, GoRouterState state) {
  final session = ref.read(authControllerProvider);
  final location = state.matchedLocation;
  final atSplash = location == '/splash';
  final atLogin = location == '/login';
  final atStatus = location == '/account-status';
  final atImport = location == '/import';

  return switch (session.phase) {
    SessionPhase.loading || SessionPhase.error => atSplash ? null : '/splash',
    SessionPhase.signedOut => atLogin ? null : '/login',
    SessionPhase.active =>
      ref.read(needsFirstImportProvider)
          ? (atImport ? null : '/import')
          : (atSplash || atLogin || atStatus || atImport)
          ? '/today'
          : null,
    SessionPhase.restricted => atStatus ? null : '/account-status',
  };
}

final routerProvider = Provider<GoRouter>((ref) {
  final refresh = _AuthRefresh(ref);
  ref.onDispose(refresh.dispose);
  return GoRouter(
    initialLocation: '/splash',
    refreshListenable: refresh,
    redirect: (context, state) => _redirect(ref, state),
    routes: [
      GoRoute(path: '/splash', builder: (context, state) => const SplashPage()),
      GoRoute(path: '/login', builder: (context, state) => const LoginPage()),
      GoRoute(path: '/import', builder: (context, state) => const ImportPage()),
      GoRoute(
        path: '/account-status',
        builder: (context, state) => const AccountStatusPage(),
      ),
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) =>
            AppShell(navigationShell: navigationShell),
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/today',
                builder: (context, state) => const TodayPage(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/week',
                builder: (context, state) => const WeekPage(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/features',
                builder: (context, state) => const FeaturesPage(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/profile',
                builder: (context, state) => const ProfilePage(),
              ),
            ],
          ),
        ],
      ),
      GoRoute(
        path: '/sessions/:id',
        builder: (context, state) =>
            SessionDetailPage(sessionId: state.pathParameters['id'] ?? ''),
      ),
      GoRoute(
        path: '/sessions/:id/notes',
        builder: (context, state) {
          final extra = state.extra;
          final data = extra is Map ? extra : const {};
          return NotesPage(
            sessionId: state.pathParameters['id'] ?? '',
            title: data['title']?.toString(),
            editable: data['editable'] != false,
          );
        },
      ),
    ],
  );
});
