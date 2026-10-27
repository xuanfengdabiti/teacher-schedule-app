import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/auth_controller.dart';
import '../shared/status_views.dart';

class SplashPage extends ConsumerWidget {
  const SplashPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(authControllerProvider);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 78,
                  height: 78,
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(24),
                    border: Border.all(color: const Color(0xFFE2ECE9)),
                  ),
                  child: const Icon(
                    Icons.calendar_month_rounded,
                    size: 42,
                    color: Color(0xFF2F7F8A),
                  ),
                ),
                const SizedBox(height: 22),
                Text('教师课表', style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: 24),
                if (session.phase == SessionPhase.error) ...[
                  Text(
                    session.errorMessage ?? '恢复登录失败。',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodyLarge,
                  ),
                  const SizedBox(height: 18),
                  FilledButton(
                    key: const Key('retryRestoreButton'),
                    onPressed: () => ref
                        .read(authControllerProvider.notifier)
                        .retryRestore(),
                    child: const Text('重试'),
                  ),
                  const SizedBox(height: 8),
                  TextButton(
                    onPressed: () async {
                      await ref.read(authControllerProvider.notifier).logout();
                      if (context.mounted) {
                        context.go('/login');
                      }
                    },
                    child: const Text('返回登录'),
                  ),
                ] else
                  const LoadingView(message: '正在恢复登录…'),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
