import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../core/providers.dart';
import '../../shared/status_views.dart';
import '../collaboration/classroom_inquiry_page.dart';
import '../collaboration/reschedule_requests_page.dart';
import '../free_rooms/free_rooms_page.dart';
import '../schedule/models.dart';
import '../schedule/schedule_controller.dart';
import '../teaching/handoff_list_page.dart';
import '../teaching/teaching_plan_page.dart';

class FeaturesPage extends ConsumerWidget {
  const FeaturesPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final offerings = ref.watch(offeringsProvider);
    final items = offerings.value ?? const <Offering>[];
    final api = ref.read(apiClientProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('功能'),
        actions: [
          IconButton(
            tooltip: '刷新课程',
            onPressed: () => ref.invalidate(offeringsProvider),
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(offeringsProvider);
          try {
            await ref.read(offeringsProvider.future);
          } catch (_) {
            // The provider error branch renders the retry state.
          }
        },
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(18, 4, 18, 12),
              sliver: SliverToBoxAdapter(child: _QuickActions(api: api)),
            ),
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(18, 0, 18, 10),
              sliver: SliverToBoxAdapter(
                child: Row(
                  children: [
                    Text('我的课程', style: Theme.of(context).textTheme.titleLarge),
                    const Spacer(),
                    if (items.isNotEmpty)
                      Text(
                        '${items.length} 门',
                        style: const TextStyle(fontSize: 14, color: appMuted),
                      ),
                  ],
                ),
              ),
            ),
            if (offerings.isLoading && items.isEmpty)
              const SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: 18),
                  child: LoadingView(message: '正在加载课程…'),
                ),
              )
            else if (offerings.hasError && items.isEmpty)
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(18, 0, 18, 24),
                sliver: SliverToBoxAdapter(
                  child: _LoadError(
                    message: '${offerings.error}',
                    onRetry: () => ref.invalidate(offeringsProvider),
                  ),
                ),
              )
            else if (items.isEmpty)
              const SliverToBoxAdapter(
                child: EmptyView(
                  title: '本学期暂无课程',
                  detail: '当前学期没有本人任课或同教学班的课程。',
                  icon: Icons.menu_book_outlined,
                ),
              )
            else
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(18, 0, 18, 28),
                sliver: SliverList.builder(
                  itemCount: items.length,
                  itemBuilder: (context, index) {
                    final offering = items[index];
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: _OfferingCard(
                        key: ValueKey('offering-${offering.id}'),
                        offering: offering,
                        onPlan: () => Navigator.of(context).push<void>(
                          MaterialPageRoute(
                            builder: (context) => TeachingPlanPage(
                              api: api,
                              offeringId: offering.id,
                            ),
                          ),
                        ),
                      ),
                    );
                  },
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _QuickActions extends StatelessWidget {
  const _QuickActions({required this.api});

  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 16, 14, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 4),
              child: Text(
                '常用工具',
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                  color: appInk,
                ),
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: _QuickTile(
                    key: const Key('freeRoomsEntry'),
                    icon: Icons.event_available_outlined,
                    label: '空教室查询',
                    onTap: () => Navigator.of(context).push<void>(
                      MaterialPageRoute(
                        builder: (context) => FreeRoomsPage(api: api),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _QuickTile(
                    key: const Key('handoffEntry'),
                    icon: Icons.swap_horiz_rounded,
                    label: '进度交接',
                    onTap: () => Navigator.of(context).push<void>(
                      MaterialPageRoute(
                        builder: (context) => HandoffListPage(api: api),
                      ),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: _QuickTile(
                    key: const Key('rescheduleRequestsEntry'),
                    icon: Icons.published_with_changes,
                    label: '调课记录',
                    onTap: () => Navigator.of(context).push<void>(
                      MaterialPageRoute(
                        builder: (context) => RescheduleRequestsPage(api: api),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _QuickTile(
                    key: const Key('classroomInquiryEntry'),
                    icon: Icons.room_preferences_outlined,
                    label: '教室与失物',
                    onTap: () => Navigator.of(context).push<void>(
                      MaterialPageRoute(
                        builder: (context) => ClassroomInquiryPage(api: api),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _QuickTile extends StatelessWidget {
  const _QuickTile({
    super.key,
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0xFFEAF5F2),
      borderRadius: BorderRadius.circular(15),
      child: InkWell(
        borderRadius: BorderRadius.circular(15),
        onTap: onTap,
        child: SizedBox(
          height: 96,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 13, 12, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(icon, size: 26, color: appTeal),
                const Spacer(),
                Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 15.5,
                    fontWeight: FontWeight.w600,
                    color: appInk,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _OfferingCard extends StatelessWidget {
  const _OfferingCard({
    super.key,
    required this.offering,
    required this.onPlan,
  });

  final Offering offering;
  final VoidCallback onPlan;

  @override
  Widget build(BuildContext context) {
    final roles = offering.teachers
        .map((teacher) => '${teacher.name}（${_roleLabel(teacher.role)}）')
        .join('、');
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Text(
                    offering.courseName,
                    style: const TextStyle(
                      fontSize: 18,
                      height: 1.35,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                InfoPill(
                  label: offering.isAssigned ? '我任课' : '同班教师',
                  color: offering.isAssigned ? appTeal : appMuted,
                ),
              ],
            ),
            const SizedBox(height: 10),
            _OfferingLine(
              icon: Icons.groups_outlined,
              text: roles.isEmpty ? '未安排教师' : roles,
            ),
            _OfferingLine(
              icon: Icons.timelapse,
              text:
                  '目标 ${offering.targetHours} 学时'
                  '${offering.leadTeacherName.isEmpty ? '' : ' · 负责人 ${offering.leadTeacherName}'}',
              last: true,
            ),
            const SizedBox(height: 14),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                key: ValueKey('offering-plan-${offering.id}'),
                onPressed: onPlan,
                icon: const Icon(Icons.checklist_rtl),
                label: const Text('教学计划 / 进度'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

String _roleLabel(String role) => switch (role.trim().toLowerCase()) {
  'lead' => '负责人',
  'teacher' => '任课教师',
  _ => role,
};

class _OfferingLine extends StatelessWidget {
  const _OfferingLine({
    required this.icon,
    required this.text,
    this.last = false,
  });

  final IconData icon;
  final String text;
  final bool last;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: last ? 0 : 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(icon, size: 18, color: appMuted),
          ),
          const SizedBox(width: 7),
          Expanded(
            child: Text(
              text,
              style: TextStyle(
                fontSize: 14.5,
                height: 1.45,
                color: last ? appMuted : appInk,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _LoadError extends StatelessWidget {
  const _LoadError({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            const Icon(Icons.cloud_off_outlined, color: appMuted),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                message,
                style: const TextStyle(fontSize: 15, color: appMuted),
              ),
            ),
            TextButton(onPressed: onRetry, child: const Text('重试')),
          ],
        ),
      ),
    );
  }
}
