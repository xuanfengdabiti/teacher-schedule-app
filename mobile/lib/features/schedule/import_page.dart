import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/config/api_config.dart';
import '../../core/providers.dart';
import '../auth/auth_controller.dart';
import 'import_controller.dart';

/// First-run demo presentation: shows the matching/importing steps, then lets
/// the teacher enter the schedule explicitly.
class ImportPage extends ConsumerStatefulWidget {
  const ImportPage({super.key});

  @override
  ConsumerState<ImportPage> createState() => _ImportPageState();
}

class _ImportPageState extends ConsumerState<ImportPage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _startImport();
      }
    });
  }

  void _startImport() {
    ref
        .read(importControllerProvider.notifier)
        .start(
          teacherNo: ref.read(authControllerProvider).me?.teacher?.teacherNo,
        );
  }

  Future<void> _enterSchedule() async {
    final session = ref.read(authControllerProvider);
    final me = session.me;
    final termId = me?.currentTerm?.id;
    if (me == null || termId == null || termId.isEmpty) {
      context.go('/today');
      return;
    }
    await ref
        .read(importRecordStoreProvider)
        .markDone(
          baseUrl: ApiConfig.normalizeBaseUrl(
            ref.read(settingsRepositoryProvider).apiBaseUrl,
          ),
          accountId: me.id,
          termId: termId,
        );
    ref.invalidate(needsFirstImportProvider);
    if (mounted) {
      context.go('/today');
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(authControllerProvider);
    final state = ref.watch(importControllerProvider);
    final me = session.me;
    final teacherName = me?.displayName ?? '';
    final teacherNo = me?.teacher?.teacherNo ?? '';
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 28, 24, 24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _TeacherHeader(name: teacherName, teacherNo: teacherNo),
                  const SizedBox(height: 28),
                  if (state.isSuccess)
                    _SuccessBody(
                      teacherName: teacherName,
                      state: state,
                      onEnter: _enterSchedule,
                    )
                  else
                    _ProgressBody(state: state, onRetry: _startImport),
                  const SizedBox(height: 24),
                  const Text(
                    '演示模式 · 使用示例课表',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 12.5, color: appMuted),
                  ),
                  if (state.isFailure) ...[
                    const SizedBox(height: 10),
                    TextButton(
                      key: const Key('importBackToLoginButton'),
                      onPressed: () =>
                          ref.read(authControllerProvider.notifier).logout(),
                      child: const Text('返回登录'),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _TeacherHeader extends StatelessWidget {
  const _TeacherHeader({required this.name, required this.teacherNo});

  final String name;
  final String teacherNo;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: 68,
          height: 68,
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(22),
            border: Border.all(color: appLine),
          ),
          child: const Icon(
            Icons.calendar_month_rounded,
            color: appTeal,
            size: 36,
          ),
        ),
        const SizedBox(height: 16),
        Text(
          name.isEmpty ? '老师' : name,
          key: const Key('importTeacherName'),
          style: const TextStyle(
            fontSize: 22,
            fontWeight: FontWeight.w700,
            color: appInk,
          ),
        ),
        if (teacherNo.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(
            '工号 $teacherNo',
            key: const Key('importTeacherNo'),
            style: const TextStyle(fontSize: 14, color: appMuted),
          ),
        ],
      ],
    );
  }
}

class _ProgressBody extends StatelessWidget {
  const _ProgressBody({required this.state, required this.onRetry});

  final ImportState state;
  final VoidCallback onRetry;

  static const _steps = ['匹配教师信息', '获取本学期课表', '整理课程安排'];

  @override
  Widget build(BuildContext context) {
    final failed = state.isFailure;
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 22, 20, 20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              failed ? '导入未完成' : '正在导入课表',
              key: const Key('importTitle'),
              style: const TextStyle(
                fontSize: 21,
                fontWeight: FontWeight.w700,
                color: appInk,
              ),
            ),
            const SizedBox(height: 18),
            for (var index = 0; index < _steps.length; index++) ...[
              _StepRow(
                label: _steps[index],
                done: state.step > index,
                active: !failed && state.step == index,
              ),
              if (index != _steps.length - 1) const SizedBox(height: 14),
            ],
            if (failed) ...[
              const SizedBox(height: 18),
              Text(
                state.errorMessage ?? '课表获取失败，请稍后重试。',
                key: const Key('importError'),
                style: const TextStyle(fontSize: 14, color: Color(0xFF9A3B30)),
              ),
              const SizedBox(height: 14),
              FilledButton(
                key: const Key('importRetryButton'),
                onPressed: onRetry,
                child: const Text('重试'),
              ),
            ] else ...[
              const SizedBox(height: 20),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: (state.step + 1) / 4,
                  minHeight: 6,
                  backgroundColor: const Color(0xFFE6F1EE),
                  color: appTeal,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow({
    required this.label,
    required this.done,
    required this.active,
  });

  final String label;
  final bool done;
  final bool active;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        SizedBox(
          width: 26,
          height: 26,
          child: done
              ? const Icon(Icons.check_circle, size: 24, color: appTeal)
              : active
              ? const Padding(
                  padding: EdgeInsets.all(3),
                  child: CircularProgressIndicator(
                    strokeWidth: 2.2,
                    color: appTeal,
                  ),
                )
              : Container(
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(color: appLine, width: 2),
                  ),
                ),
        ),
        const SizedBox(width: 12),
        Text(
          label,
          style: TextStyle(
            fontSize: 15.5,
            color: done || active ? appInk : appMuted,
            fontWeight: active ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ],
    );
  }
}

class _SuccessBody extends StatelessWidget {
  const _SuccessBody({
    required this.teacherName,
    required this.state,
    required this.onEnter,
  });

  final String teacherName;
  final ImportState state;
  final VoidCallback onEnter;

  @override
  Widget build(BuildContext context) {
    final summary = state.summary;
    final empty = summary == null || summary.isEmpty;
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 24, 20, 20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Icon(Icons.check_circle_rounded, size: 48, color: appTeal),
            const SizedBox(height: 12),
            const Text(
              '课表已导入',
              key: Key('importDoneTitle'),
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.w700,
                color: appInk,
              ),
            ),
            const SizedBox(height: 16),
            if (empty)
              const Text(
                '暂未排课',
                key: Key('importEmptyText'),
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 15, color: appMuted),
              )
            else ...[
              _SummaryRow(
                label: '老师',
                value: teacherName.isEmpty ? '老师' : teacherName,
              ),
              _SummaryRow(
                label: '学期',
                value: summary.termName.isEmpty ? '本学期' : summary.termName,
              ),
              _SummaryRow(label: '课程', value: '${summary.courseCount} 门'),
              _SummaryRow(label: '课次', value: '${summary.sessionCount} 次'),
            ],
            const SizedBox(height: 22),
            FilledButton(
              key: const Key('importEnterButton'),
              onPressed: onEnter,
              child: const Text('进入课表'),
            ),
          ],
        ),
      ),
    );
  }
}

class _SummaryRow extends StatelessWidget {
  const _SummaryRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 52,
            child: Text(
              label,
              style: const TextStyle(fontSize: 14, color: appMuted),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w600,
                color: appInk,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
