import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import '../../shared/status_views.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';
import 'inquiry_compose_page.dart';

class ClassroomUsagePage extends StatefulWidget {
  const ClassroomUsagePage({
    super.key,
    required this.api,
    required this.classroomId,
    this.initialDate,
    this.onInquirySent,
  });

  final ApiClient api;
  final String classroomId;
  final DateTime? initialDate;
  final VoidCallback? onInquirySent;

  @override
  State<ClassroomUsagePage> createState() => _ClassroomUsagePageState();
}

class _ClassroomUsagePageState extends State<ClassroomUsagePage> {
  late DateTime _date;
  ClassroomUsageData? _data;
  Object? _error;
  bool _loading = true;
  int _requestNo = 0;

  @override
  void initState() {
    super.initState();
    final initial = widget.initialDate ?? DateTime.now();
    _date = DateTime(initial.year, initial.month, initial.day);
    _load();
  }

  Future<void> _load({bool keepData = false}) async {
    final requestNo = ++_requestNo;
    setState(() {
      _loading = true;
      if (!keepData) {
        _error = null;
      }
    });
    try {
      final response = await widget.api.get(
        'classrooms/${widget.classroomId}/usage/',
        query: {'date': isoDate(_date)},
      );
      final data = ClassroomUsageData.fromJson(mapOf(response.data));
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() {
        _data = data;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() {
        _loading = false;
        if (_data == null) {
          _error = error;
        }
      });
      if (_data != null) {
        showCollabError(context, error);
      }
    }
  }

  Future<void> _shiftDay(int days) async {
    setState(() {
      _date = _date.add(Duration(days: days));
      _data = null;
    });
    await _load();
  }

  Future<void> _pickDay() async {
    final selected = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateTime(DateTime.now().year - 1),
      lastDate: DateTime(DateTime.now().year + 2, 12, 31),
      helpText: '选择要查看的日期',
    );
    if (selected == null || !mounted) {
      return;
    }
    setState(() {
      _date = DateTime(selected.year, selected.month, selected.day);
      _data = null;
    });
    await _load();
  }

  Future<void> _openContact(ClassroomUsageTeacher teacher) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      builder: (context) => _TeacherContactSheet(
        api: widget.api,
        teacherId: teacher.id,
        teacherName: teacher.name,
      ),
    );
  }

  Future<void> _openInquiry() async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (context) => InquiryComposePage(
          api: widget.api,
          classroomId: widget.classroomId,
          initialDate: _date,
        ),
      ),
    );
    if (changed == true && mounted) {
      final hubCallback = widget.onInquirySent;
      if (hubCallback != null) {
        hubCallback();
        if (mounted) {
          Navigator.of(context).pop();
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    return Scaffold(
      appBar: AppBar(title: const Text('教室记录')),
      body: data == null
          ? _loading
                ? const LoadingView(message: '正在读取教室使用记录…')
                : CollabErrorView(error: _error!, onRetry: () => _load())
          : Column(
              children: [
                if (_loading) const CollabInlineProgress(),
                Expanded(
                  child: RefreshIndicator(
                    onRefresh: () => _load(keepData: true),
                    child: ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                      children: [
                        _buildHeader(data),
                        const SizedBox(height: 10),
                        const CollabNotice(
                          text: '排定课程记录，不代表实际到场或物品去向。',
                          icon: Icons.info_outline,
                        ),
                        const SizedBox(height: 12),
                        if (data.entries.isEmpty)
                          const Padding(
                            padding: EdgeInsets.only(top: 50),
                            child: EmptyView(
                              title: '当天没有记录',
                              detail: '可切换日期查看其它安排。',
                              icon: Icons.event_busy_outlined,
                            ),
                          )
                        else
                          for (
                            var index = 0;
                            index < data.entries.length;
                            index++
                          )
                            Padding(
                              padding: const EdgeInsets.only(bottom: 10),
                              child: _UsageEntryCard(
                                key: ValueKey(
                                  'usage-${data.entries[index].kind}-${data.entries[index].sessionId ?? data.entries[index].roomBlockId ?? index}',
                                ),
                                entry: data.entries[index],
                                onContact: _openContact,
                              ),
                            ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
    );
  }

  Widget _buildHeader(ClassroomUsageData data) {
    return CollabSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      data.classroom.classroomCode,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 3),
                    Text(
                      data.classroom.location,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              CollabStatusPill(
                label: data.complete ? '覆盖完整' : '数据不完整',
                color: data.complete ? appMint : const Color(0xFFB7791F),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              IconButton.outlined(
                onPressed: () => _shiftDay(-1),
                icon: const Icon(Icons.chevron_left),
                tooltip: '前一天',
              ),
              Expanded(
                child: TextButton(
                  onPressed: _pickDay,
                  child: Text(
                    '${dateLabel(_date)} · ${weekdayZh(_date.weekday)}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              ),
              IconButton.outlined(
                onPressed: () => _shiftDay(1),
                icon: const Icon(Icons.chevron_right),
                tooltip: '后一天',
              ),
            ],
          ),
          const SizedBox(height: 6),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: _openInquiry,
              icon: const Icon(Icons.search_outlined),
              label: const Text('发起失物询问'),
            ),
          ),
        ],
      ),
    );
  }
}

class _UsageEntryCard extends StatelessWidget {
  const _UsageEntryCard({
    super.key,
    required this.entry,
    required this.onContact,
  });

  final ClassroomUsageEntry entry;
  final ValueChanged<ClassroomUsageTeacher> onContact;

  Color get _kindColor => switch (entry.kind) {
    'session' => appTeal,
    'cancelled_session' => const Color(0xFFB3261E),
    'moved_out' => const Color(0xFFB7791F),
    'room_block' => appMuted,
    _ => appMuted,
  };

  IconData get _kindIcon => switch (entry.kind) {
    'session' => Icons.menu_book_outlined,
    'cancelled_session' => Icons.event_busy_outlined,
    'moved_out' => Icons.swap_horiz,
    'room_block' => Icons.lock_clock_outlined,
    _ => Icons.circle_outlined,
  };

  @override
  Widget build(BuildContext context) {
    final status = entry.status;
    final statusText = switch (status) {
      'scheduled' => '已排定',
      'completed' => '已完成',
      'cancelled' => '已停课',
      _ => null,
    };
    return CollabSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(_kindIcon, size: 19, color: _kindColor),
              const SizedBox(width: 7),
              Expanded(
                child: Text(
                  entry.kindLabel,
                  style: Theme.of(context).textTheme.titleMedium
                      ?.copyWith(color: _kindColor),
                ),
              ),
              Text(
                entry.periodLabelText.isEmpty ? '' : entry.periodLabelText,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            rangeLabel(entry.startAt, entry.endAt),
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (entry.courseName != null) ...[
            const SizedBox(height: 8),
            CollabDetailRow(label: '课程', value: entry.courseName!),
            if (entry.teachingClass != null && entry.teachingClass!.isNotEmpty)
              CollabDetailRow(label: '教学班', value: entry.teachingClass!),
          ],
          if (entry.kind == 'room_block') ...[
            if (entry.blockCode != null)
              CollabDetailRow(label: '占用代码', value: entry.blockCode!),
            if (entry.reason != null && entry.reason!.isNotEmpty)
              CollabDetailRow(label: '原因', value: entry.reason!),
          ],
          if (entry.kind == 'moved_out' && entry.currentClassroomCode != null)
            CollabDetailRow(
              label: '现调至',
              value:
                  '${entry.currentClassroomCode} · ${rangeLabel(entry.currentStartAt, entry.currentEndAt)}',
              valueColor: const Color(0xFFB7791F),
            ),
          if (statusText != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '状态：$statusText${entry.statusReason?.isNotEmpty == true ? ' · ${entry.statusReason}' : ''}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          if (entry.teachers.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('相关教师', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 4),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final teacher in entry.teachers)
                  teacher.hasVisibleContact
                      ? ActionChip(
                          key: ValueKey('usage-contact-${teacher.id}'),
                          avatar: const Icon(Icons.person_outline, size: 17),
                          label: Text(teacher.name),
                          onPressed: () => onContact(teacher),
                        )
                      : Chip(
                          avatar: const Icon(Icons.person_outline, size: 17),
                          label: Text('${teacher.name} · 联系方式不可见'),
                        ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _TeacherContactSheet extends StatefulWidget {
  const _TeacherContactSheet({
    required this.api,
    required this.teacherId,
    required this.teacherName,
  });

  final ApiClient api;
  final String teacherId;
  final String teacherName;

  @override
  State<_TeacherContactSheet> createState() => _TeacherContactSheetState();
}

class _TeacherContactSheetState extends State<_TeacherContactSheet> {
  TeacherContact? _contact;
  Object? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final response = await widget.api.get(
        'teachers/${widget.teacherId}/contact/',
      );
      final contact = TeacherContact.fromJson(mapOf(response.data));
      if (!mounted) {
        return;
      }
      setState(() {
        _contact = contact;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  Future<void> _copy(String label, String value) async {
    await Clipboard.setData(ClipboardData(text: value));
    if (mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('已复制$label')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final contact = _contact;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 18, 20, 24),
        child: contact == null
            ? SizedBox(
                height: 220,
                child: _loading
                    ? const LoadingView(message: '正在读取联系方式…')
                    : Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(
                            _error == null ? '没有联系方式' : '联系方式读取失败',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          if (_error != null) ...[
                            const SizedBox(height: 8),
                            Text(
                              ApiException.fromUnknown(_error!).message,
                              textAlign: TextAlign.center,
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                            TextButton(
                              onPressed: _load,
                              child: const Text('重试'),
                            ),
                          ],
                        ],
                      ),
              )
            : Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    contact.name,
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  if (contact.department.isNotEmpty)
                    Text(
                      contact.department,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  const SizedBox(height: 14),
                  _ContactRow(
                    label: '电话（仅复制，不拨号）',
                    value: contact.phone,
                    status: contact.phoneStatus,
                    onCopy: contact.phone == null
                        ? null
                        : () => _copy('电话', contact.phone!),
                  ),
                  const SizedBox(height: 8),
                  _ContactRow(
                    label: '微信',
                    value: contact.wechat,
                    status: contact.wechatStatus,
                    onCopy: contact.wechat == null
                        ? null
                        : () => _copy('微信', contact.wechat!),
                  ),
                ],
              ),
      ),
    );
  }
}

class _ContactRow extends StatelessWidget {
  const _ContactRow({
    required this.label,
    required this.value,
    required this.status,
    required this.onCopy,
  });

  final String label;
  final String? value;
  final String status;
  final VoidCallback? onCopy;

  @override
  Widget build(BuildContext context) {
    final visible = status == 'visible' && value != null && value!.isNotEmpty;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFFF7FAF9),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: appLine),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: Theme.of(context).textTheme.bodySmall),
                const SizedBox(height: 2),
                Text(
                  visible ? value! : contactStatusLabel(status),
                  style: Theme.of(context).textTheme.bodyLarge,
                ),
              ],
            ),
          ),
          if (visible)
            IconButton(
              onPressed: onCopy,
              icon: const Icon(Icons.copy_outlined),
              tooltip: '复制',
            ),
        ],
      ),
    );
  }
}
