import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../../shared/status_views.dart';
import '../collaboration/classroom_usage_page.dart';
import '../collaboration/collaboration_models.dart';
import '../collaboration/inquiry_detail_page.dart';
import '../collaboration/notification_list_page.dart';
import '../collaboration/reschedule_requests_page.dart';
import '../teaching/handoff_list_page.dart';
import 'models.dart';
import 'schedule_controller.dart';
import 'session_action_sheet.dart';
import 'session_card.dart';

class TodayPage extends ConsumerStatefulWidget {
  const TodayPage({super.key});

  @override
  ConsumerState<TodayPage> createState() => _TodayPageState();
}

class _TodayPageState extends ConsumerState<TodayPage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(scheduleControllerProvider.notifier).ensureToday();
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final state = ref.read(scheduleControllerProvider);
    if (state.weekFor(DateTime.now()) == null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          ref.read(scheduleControllerProvider.notifier).ensureToday();
        }
      });
    }
  }

  void _openNotificationObject(String objectType, String objectId) {
    unawaited(_routeNotificationObject(objectType, objectId));
  }

  void _openSessionActions(Session session) {
    unawaited(
      showSessionActionSheet(
        context: context,
        session: session,
        api: ref.read(apiClientProvider),
      ),
    );
  }

  Future<void> _routeNotificationObject(
    String objectType,
    String objectId,
  ) async {
    final api = ref.read(apiClientProvider);
    final type = objectType.trim().toLowerCase().replaceAll('-', '_');
    if (type.contains('session')) {
      await context.push('/sessions/$objectId');
      return;
    }
    if (type.contains('handoff')) {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(builder: (context) => HandoffListPage(api: api)),
      );
      return;
    }
    if (type.contains('inquiry')) {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) =>
              InquiryDetailPage(api: api, inquiryId: objectId),
        ),
      );
      return;
    }
    if (type.contains('reschedule')) {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) => RescheduleRequestsPage(api: api),
        ),
      );
      return;
    }
    if (type.contains('classroom')) {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) =>
              ClassroomUsagePage(api: api, classroomId: objectId),
        ),
      );
      return;
    }
    await _showUnsupportedNotification(api, objectType, objectId);
  }

  Future<void> _showUnsupportedNotification(
    ApiClient api,
    String objectType,
    String objectId,
  ) async {
    Map<String, dynamic>? match;
    try {
      final response = await api.get(
        'notifications/',
        query: const {'limit': 50},
      );
      final body = jsonMap(response.data);
      final results = body['results'];
      if (results is List) {
        for (final raw in results.whereType<Map>()) {
          final item = jsonMap(raw);
          if (item['object_type'] == objectType &&
              item['object_id']?.toString() == objectId) {
            match = item;
            break;
          }
        }
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(ApiException.fromUnknown(error).message)),
        );
      }
      return;
    }
    if (!mounted) {
      return;
    }
    if (match == null) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('该消息对象暂不支持打开。')));
      return;
    }
    final message = match;
    final notification = NotificationItem.fromJson(message);
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(notification.displayTitle),
        content: SingleChildScrollView(
          child: SelectableText(
            notification.displayBody,
            style: const TextStyle(fontSize: 16, height: 1.5),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('关闭'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final today = DateTime.now();
    final state = ref.watch(scheduleControllerProvider);
    final weekAsync = state.weekFor(today);
    if (weekAsync == null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          ref.read(scheduleControllerProvider.notifier).ensureToday();
        }
      });
    }
    final calendar = ref.watch(termCalendarProvider).value;
    final sessions = weekAsync?.value?.sessionsOn(today) ?? const <Session>[];
    final hasOtherCoursesThisWeek =
        weekAsync?.value?.sessions.any(
          (session) => !sameDay(session.date, today),
        ) ??
        false;
    final sessionWeek = sessions
        .map((session) => session.teachingWeek)
        .whereType<int>()
        .toList();
    final teachingWeek = sessionWeek.isEmpty
        ? calendar?.teachingWeekFor(today)
        : sessionWeek.first;

    return Scaffold(
      appBar: AppBar(
        title: const Text('我的课表'),
        actions: [
          IconButton(
            key: const Key('notificationsButton'),
            tooltip: '消息',
            onPressed: () => Navigator.of(context).push<void>(
              MaterialPageRoute(
                builder: (context) => NotificationListPage(
                  api: ref.read(apiClientProvider),
                  onOpenObject: _openNotificationObject,
                ),
              ),
            ),
            icon: const Icon(Icons.notifications_none_rounded),
          ),
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref
                .read(scheduleControllerProvider.notifier)
                .refreshWeek(today),
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () =>
            ref.read(scheduleControllerProvider.notifier).refreshWeek(today),
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
                child: _TodayHeader(date: today, teachingWeek: teachingWeek),
              ),
            ),
            if (weekAsync == null || weekAsync.isLoading)
              const SliverFillRemaining(
                hasScrollBody: false,
                child: LoadingView(message: '正在加载今天的课程…'),
              )
            else if (weekAsync.hasError)
              SliverFillRemaining(
                hasScrollBody: false,
                child: ErrorView(
                  message: '${weekAsync.error}',
                  onRetry: () => ref
                      .read(scheduleControllerProvider.notifier)
                      .refreshWeek(today),
                ),
              )
            else if (sessions.isEmpty)
              SliverFillRemaining(
                hasScrollBody: false,
                child: EmptyView(
                  title: weekAsync.value?.termId == null ? '今天不在学期内' : '今天没有课程',
                  detail: weekAsync.value?.termId == null
                      ? '当前日期不在学期的教学周内。'
                      : hasOtherCoursesThisWeek
                      ? '今天没有课；本周其他日期有课程，可查看本周课表。'
                      : '如果学期已经开始却一直没有课程，请联系教务确认。',
                  action: FilledButton.tonalIcon(
                    key: const Key('openWeekButton'),
                    onPressed: () => context.go('/week'),
                    icon: const Icon(Icons.calendar_month_outlined),
                    label: const Text('查看本周课表'),
                  ),
                ),
              )
            else ...[
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                sliver: SliverToBoxAdapter(
                  child: Row(
                    children: [
                      Text(
                        '今日课程',
                        style: const TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                          color: appInk,
                        ),
                      ),
                      const Spacer(),
                      Text(
                        '${sessions.length} 节',
                        style: const TextStyle(fontSize: 12, color: appMuted),
                      ),
                    ],
                  ),
                ),
              ),
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                sliver: SliverList.builder(
                  itemCount: sessions.length,
                  itemBuilder: (context, index) {
                    final session = sessions[index];
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: SessionCard(
                        key: ValueKey('session-${session.id}'),
                        session: session,
                        onTap: () => context.push('/sessions/${session.id}'),
                        onLongPress: () => _openSessionActions(session),
                      ),
                    );
                  },
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _TodayHeader extends StatelessWidget {
  const _TodayHeader({required this.date, required this.teachingWeek});

  final DateTime date;
  final int? teachingWeek;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: Text(
            '${monthDayZh(date)} ${weekdayZh(date.weekday)}',
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              fontSize: 19,
              height: 1.25,
              fontWeight: FontWeight.w700,
              color: appInk,
            ),
          ),
        ),
        const SizedBox(width: 8),
        _HeaderTag(
          label: teachingWeek == null ? '学期外' : '第$teachingWeek周',
          color: appMuted,
          background: Colors.white,
          borderColor: appLine,
        ),
        const SizedBox(width: 6),
        const _HeaderTag(label: '今天', color: appTeal, background: appBlueSoft),
      ],
    );
  }
}

class _HeaderTag extends StatelessWidget {
  const _HeaderTag({
    required this.label,
    required this.color,
    required this.background,
    this.borderColor,
  });

  final String label;
  final Color color;
  final Color background;
  final Color? borderColor;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(7),
        border: borderColor == null ? null : Border.all(color: borderColor!),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 11.5,
          height: 1.2,
          fontWeight: FontWeight.w600,
          color: color,
        ),
      ),
    );
  }
}
