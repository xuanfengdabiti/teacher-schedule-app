import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'reminder_models.dart';
import 'reminders_controller.dart';

const reminderMinuteChoices = <int>[5, 10, 15, 30];

/// Local class-reminder settings. The page has no parameters so the profile
/// page can open it directly.
class ReminderSettingsPage extends ConsumerWidget {
  const ReminderSettingsPage({super.key});

  Future<void> _save(WidgetRef ref, ReminderPreferences next) async {
    await ref.read(remindersControllerProvider.notifier).save(next);
  }

  Future<void> _requestPermission(BuildContext context, WidgetRef ref) async {
    final permission = await ref
        .read(remindersControllerProvider.notifier)
        .requestPermission();
    if (!context.mounted) {
      return;
    }
    if (permission != NotificationPermission.allowed) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('通知权限未允许，提醒不会显示。可在系统设置中开启。')),
      );
    }
  }

  Future<void> _sendTest(BuildContext context, WidgetRef ref) async {
    final error = await ref
        .read(remindersControllerProvider.notifier)
        .sendTestNotification();
    if (!context.mounted) {
      return;
    }
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(error ?? '测试提醒已发出。')));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(remindersControllerProvider);
    final preferences = state.displayed;
    final unavailable = state.unavailableReason;

    return Scaffold(
      appBar: AppBar(title: const Text('上课提醒')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (unavailable != null)
            Card(
              key: const Key('reminderUnavailable'),
              color: Theme.of(context).colorScheme.errorContainer,
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Text(unavailable),
              ),
            ),
          if (state.loading && preferences == null)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 24),
              child: Center(child: CircularProgressIndicator()),
            )
          else ...[
            SwitchListTile(
              key: const Key('reminderEnabledSwitch'),
              title: const Text('上课前提醒'),
              subtitle: const Text('按课程开始时间提前通知'),
              value: preferences?.enabled ?? false,
              onChanged:
                  unavailable != null || preferences == null || state.saving
                  ? null
                  : (value) => _save(ref, preferences.copyWith(enabled: value)),
            ),
            const SizedBox(height: 8),
            Text('提前时间', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final minutes in _minuteChoices(preferences?.minutes))
                  ChoiceChip(
                    key: Key('reminderMinutes-$minutes'),
                    label: Text(_minutesLabel(minutes)),
                    selected: preferences?.minutes == minutes,
                    onSelected:
                        unavailable != null ||
                            preferences == null ||
                            state.saving
                        ? null
                        : (selected) {
                            if (!selected) {
                              return;
                            }
                            _save(ref, preferences.copyWith(minutes: minutes));
                          },
                  ),
              ],
            ),
            const SizedBox(height: 8),
            Text('提醒时间以系统安排为准。', style: Theme.of(context).textTheme.bodySmall),
            const Divider(height: 32),
            _PermissionTile(
              state: state,
              unavailable: unavailable != null,
              onRequest: () => _requestPermission(context, ref),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              key: const Key('sendTestNotificationButton'),
              onPressed: unavailable != null
                  ? null
                  : () => _sendTest(context, ref),
              icon: const Icon(Icons.notifications_active_outlined),
              label: const Text('测试提醒'),
            ),
            if (state.error != null) ...[
              const SizedBox(height: 12),
              Card(
                key: const Key('reminderSaveError'),
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(state.error!),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          TextButton(
                            key: const Key('reminderRetryButton'),
                            onPressed: preferences == null || state.saving
                                ? null
                                : () => _save(ref, preferences),
                            child: const Text('重试'),
                          ),
                          if (preferences != null && preferences.enabled)
                            TextButton(
                              key: const Key('reminderClosePermissionButton'),
                              onPressed: () {
                                ref
                                    .read(remindersControllerProvider.notifier)
                                    .refreshPermission();
                              },
                              child: const Text('刷新权限状态'),
                            ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ],
            const SizedBox(height: 12),
            Text(
              '已安排 ${state.scheduledCount} 个上课提醒。',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 16),
            TextButton(
              onPressed: () {
                if (context.canPop()) {
                  context.pop();
                }
              },
              child: const Text('返回'),
            ),
          ],
        ],
      ),
    );
  }

  List<int> _minuteChoices(int? current) {
    if (current == null || reminderMinuteChoices.contains(current)) {
      return reminderMinuteChoices;
    }
    return [...reminderMinuteChoices, current]..sort();
  }

  String _minutesLabel(int minutes) => minutes == 0 ? '上课时' : '提前$minutes分钟';
}

class _PermissionTile extends StatelessWidget {
  const _PermissionTile({
    required this.state,
    required this.unavailable,
    required this.onRequest,
  });

  final RemindersState state;
  final bool unavailable;
  final VoidCallback onRequest;

  @override
  Widget build(BuildContext context) {
    final (label, detail) = _permissionText(state);
    return Card(
      key: const Key('reminderPermissionCard'),
      child: ListTile(
        leading: Icon(
          state.permission == NotificationPermission.allowed
              ? Icons.notifications_on_outlined
              : Icons.notifications_off_outlined,
        ),
        title: Text(label),
        subtitle: Text(detail),
        trailing:
            state.permission == NotificationPermission.denied && !unavailable
            ? TextButton(
                key: const Key('reminderRequestPermissionButton'),
                onPressed: onRequest,
                child: const Text('允许通知'),
              )
            : null,
      ),
    );
  }

  (String, String) _permissionText(RemindersState state) {
    if (state.unavailableReason != null) {
      return ('通知不可用', '这台手机暂时无法使用上课提醒。');
    }
    if (!state.permissionKnown) {
      return ('通知权限正在确认', '稍后可重新查看。');
    }
    return switch (state.permission) {
      NotificationPermission.allowed => ('通知权限已允许', '可以收到上课提醒。'),
      NotificationPermission.denied => ('通知权限未允许', '开启后可收到提醒；此前不会发出通知。'),
      NotificationPermission.unknown => ('通知权限无法确认', '可打开系统设置检查通知开关。'),
    };
  }
}
