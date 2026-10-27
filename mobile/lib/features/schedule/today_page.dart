import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../core/providers.dart';
import '../../shared/status_views.dart';
import '../auth/auth_controller.dart';
import '../collaboration/classroom_usage_page.dart';
import '../collaboration/inquiry_detail_page.dart';
import '../collaboration/notification_list_page.dart';
import '../collaboration/reschedule_requests_page.dart';
import '../teaching/handoff_list_page.dart';
import 'models.dart';
import 'schedule_controller.dart';
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

  void _openNotificationObject(String objectType, String objectId) {
    unawaited(_routeNotificationObject(objectType, objectId));
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
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('无法读取消息详情：$error')));
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
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(message['title']?.toString() ?? '消息详情'),
        content: SingleChildScrollView(
          child: SelectableText(
            message['body']?.toString() ?? '',
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
    final calendar = ref.watch(termCalendarProvider).value;
    final me = ref.watch(authControllerProvider).me;
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
    final term = me?.currentTerm;

    return Scaffold(
      appBar: AppBar(
        title: const Text('今天'),
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
                padding: const EdgeInsets.fromLTRB(18, 8, 18, 14),
                child: _TodayHeader(
                  date: today,
                  teachingWeek: teachingWeek,
                  termName: term?.name,
                ),
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
                      : '若学期已开始且一直为空，可能尚未导入课表，请联系教务。',
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
                padding: const EdgeInsets.fromLTRB(18, 0, 18, 10),
                sliver: SliverToBoxAdapter(
                  child: Row(
                    children: [
                      Text(
                        '今日课程',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const Spacer(),
                      Text(
                        '${sessions.length} 节',
                        style: const TextStyle(fontSize: 14, color: appMuted),
                      ),
                    ],
                  ),
                ),
              ),
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(18, 0, 18, 28),
                sliver: SliverList.builder(
                  itemCount: sessions.length,
                  itemBuilder: (context, index) {
                    final session = sessions[index];
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: SessionCard(
                        key: ValueKey('session-${session.id}'),
                        session: session,
                        onTap: () => context.push('/sessions/${session.id}'),
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
  const _TodayHeader({
    required this.date,
    required this.teachingWeek,
    required this.termName,
  });

  final DateTime date;
  final int? teachingWeek;
  final String? termName;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFFE4F3EF), Color(0xFFF2F9F7)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: const Color(0xFFD5E9E4)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  '今天',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: appTeal,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  '${monthDayZh(date)} ${weekdayZh(date.weekday)}',
                  style: const TextStyle(
                    fontSize: 25,
                    fontWeight: FontWeight.w800,
                    color: appInk,
                  ),
                ),
                if (termName != null && termName!.isNotEmpty) ...[
                  const SizedBox(height: 5),
                  Text(
                    termName!,
                    style: const TextStyle(fontSize: 14, color: appMuted),
                  ),
                ],
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.86),
              borderRadius: BorderRadius.circular(14),
            ),
            child: Text(
              teachingWeek == null ? '学期外' : '第$teachingWeek周',
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w700,
                color: appTeal,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
