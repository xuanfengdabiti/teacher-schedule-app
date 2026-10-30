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

const _warm = Color(0xFFB7791F);
const _danger = Color(0xFFB3261E);

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
          initialClassroom: _data?.classroom,
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
                ? const LoadingView(message: '正在读取教室记录…')
                : CollabErrorView(error: _error!, onRetry: () => _load())
          : Column(
              children: [
                if (_loading) const CollabInlineProgress(),
                Expanded(
                  child: RefreshIndicator(
                    onRefresh: () => _load(keepData: true),
                    child: ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      padding: const EdgeInsets.fromLTRB(14, 6, 14, 20),
                      children: [
                        _buildHeader(data),
                        const SizedBox(height: 10),
                        if (data.entries.isEmpty)
                          const _EmptyUsage()
                        else
                          _UsageTimeline(
                            entries: data.entries,
                            onContact: _openContact,
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
    final notice = data.notice.trim().isEmpty
        ? '排定课程记录，不代表实际到场或物品去向。'
        : data.notice.trim();
    return _Panel(
      padding: const EdgeInsets.fromLTRB(13, 12, 13, 11),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 3,
                height: 34,
                margin: const EdgeInsets.only(top: 2),
                decoration: BoxDecoration(
                  color: appTeal,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              const SizedBox(width: 9),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      data.classroom.classroomCode,
                      style: const TextStyle(
                        fontSize: 19,
                        height: 1.3,
                        fontWeight: FontWeight.w700,
                        color: appInk,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      data.classroom.location,
                      style: const TextStyle(
                        fontSize: 12.5,
                        height: 1.45,
                        color: appMuted,
                      ),
                    ),
                  ],
                ),
              ),
              if (!data.complete)
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 6,
                    vertical: 2,
                  ),
                  decoration: BoxDecoration(
                    color: _warm.withValues(alpha: 0.10),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Text(
                    '部分待确认',
                    style: TextStyle(
                      fontSize: 11.5,
                      fontWeight: FontWeight.w600,
                      color: _warm,
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 10),
          Container(
            decoration: BoxDecoration(
              color: const Color(0xFFF8FAFD),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: appLine),
            ),
            child: Row(
              children: [
                IconButton(
                  onPressed: () => _shiftDay(-1),
                  icon: const Icon(Icons.chevron_left, size: 20),
                  tooltip: '前一天',
                  visualDensity: VisualDensity.compact,
                  constraints: const BoxConstraints(
                    minWidth: 36,
                    minHeight: 36,
                  ),
                  padding: EdgeInsets.zero,
                ),
                Expanded(
                  child: InkWell(
                    onTap: _pickDay,
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text(
                        '${dateLabel(_date)} · ${weekdayZh(_date.weekday)}',
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                          color: appInk,
                        ),
                      ),
                    ),
                  ),
                ),
                IconButton(
                  onPressed: () => _shiftDay(1),
                  icon: const Icon(Icons.chevron_right, size: 20),
                  tooltip: '后一天',
                  visualDensity: VisualDensity.compact,
                  constraints: const BoxConstraints(
                    minWidth: 36,
                    minHeight: 36,
                  ),
                  padding: EdgeInsets.zero,
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              onPressed: _openInquiry,
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(40),
                foregroundColor: appTeal,
                side: const BorderSide(color: appLine),
                textStyle: const TextStyle(
                  fontSize: 13.5,
                  fontWeight: FontWeight.w600,
                ),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(9),
                ),
              ),
              icon: const Icon(Icons.search_outlined, size: 17),
              label: const Text('发起失物询问'),
            ),
          ),
          const SizedBox(height: 7),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(Icons.info_outline, size: 14, color: appMuted),
              const SizedBox(width: 5),
              Expanded(
                child: Text(
                  notice,
                  style: const TextStyle(
                    fontSize: 11.5,
                    height: 1.4,
                    color: appMuted,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({required this.child, required this.padding});

  final Widget child;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: appLine),
      ),
      child: child,
    );
  }
}

class _EmptyUsage extends StatelessWidget {
  const _EmptyUsage();

  @override
  Widget build(BuildContext context) {
    return const _Panel(
      padding: EdgeInsets.fromLTRB(14, 24, 14, 24),
      child: Column(
        children: [
          Icon(Icons.event_busy_outlined, size: 26, color: appMuted),
          SizedBox(height: 8),
          Text(
            '当天没有记录',
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: appInk,
            ),
          ),
          SizedBox(height: 3),
          Text(
            '可切换日期查看其它安排。',
            style: TextStyle(fontSize: 12.5, color: appMuted),
          ),
        ],
      ),
    );
  }
}

class _UsageTimeline extends StatelessWidget {
  const _UsageTimeline({required this.entries, required this.onContact});

  final List<ClassroomUsageEntry> entries;
  final ValueChanged<ClassroomUsageTeacher> onContact;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      padding: EdgeInsets.zero,
      child: Column(
        children: [
          for (var index = 0; index < entries.length; index++) ...[
            if (index > 0) const Divider(height: 1),
            _UsageEntryRow(
              key: ValueKey(
                'usage-${entries[index].kind}-${entries[index].sessionId ?? entries[index].roomBlockId ?? index}',
              ),
              entry: entries[index],
              onContact: onContact,
            ),
          ],
        ],
      ),
    );
  }
}

class _UsageEntryRow extends StatelessWidget {
  const _UsageEntryRow({
    super.key,
    required this.entry,
    required this.onContact,
  });

  final ClassroomUsageEntry entry;
  final ValueChanged<ClassroomUsageTeacher> onContact;

  Color get _kindColor => switch (entry.kind) {
    'session' => appTeal,
    'cancelled_session' => _danger,
    'moved_out' => _warm,
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
    final statusText = switch (entry.status) {
      'scheduled' => '已排定',
      'completed' => '已完成',
      'cancelled' => '已停课',
      _ => null,
    };
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 11, 12, 10),
      child: IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 54,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _clock(entry.startAt),
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      color: appInk,
                    ),
                  ),
                  Text(
                    _clock(entry.endAt),
                    style: const TextStyle(fontSize: 12, color: appMuted),
                  ),
                  if (entry.periodLabelText.isNotEmpty) ...[
                    const SizedBox(height: 3),
                    Text(
                      entry.periodLabelText.replaceAll(' ', ''),
                      style: const TextStyle(fontSize: 10.5, color: appMuted),
                    ),
                  ],
                ],
              ),
            ),
            SizedBox(
              width: 16,
              child: Column(
                children: [
                  Container(
                    width: 7,
                    height: 7,
                    margin: const EdgeInsets.only(top: 4),
                    decoration: BoxDecoration(
                      color: _kindColor,
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Expanded(child: Container(width: 1, color: appLine)),
                ],
              ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(_kindIcon, size: 15, color: _kindColor),
                      const SizedBox(width: 5),
                      Expanded(
                        child: Text(
                          entry.kindLabel,
                          style: TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w600,
                            color: _kindColor,
                          ),
                        ),
                      ),
                      if (statusText != null)
                        Text(
                          statusText,
                          style: const TextStyle(
                            fontSize: 11.5,
                            color: appMuted,
                          ),
                        ),
                    ],
                  ),
                  if (entry.courseName != null) ...[
                    const SizedBox(height: 3),
                    Text(
                      entry.courseName!,
                      style: const TextStyle(
                        fontSize: 15,
                        height: 1.35,
                        fontWeight: FontWeight.w600,
                        color: appInk,
                      ),
                    ),
                  ],
                  if (entry.teachingClass != null &&
                      entry.teachingClass!.isNotEmpty) ...[
                    const SizedBox(height: 1),
                    Text(
                      entry.teachingClass!,
                      style: const TextStyle(
                        fontSize: 12,
                        height: 1.4,
                        color: appMuted,
                      ),
                    ),
                  ],
                  if (entry.kind == 'room_block' &&
                      entry.reason != null &&
                      entry.reason!.isNotEmpty) ...[
                    const SizedBox(height: 3),
                    Text(
                      entry.reason!,
                      style: const TextStyle(
                        fontSize: 12,
                        height: 1.4,
                        color: appMuted,
                      ),
                    ),
                  ],
                  if (entry.kind == 'moved_out' &&
                      entry.currentClassroomCode != null) ...[
                    const SizedBox(height: 3),
                    Text(
                      '现调至 ${entry.currentClassroomCode} · '
                      '${rangeLabel(entry.currentStartAt, entry.currentEndAt)}',
                      style: const TextStyle(
                        fontSize: 11.5,
                        height: 1.4,
                        color: _warm,
                      ),
                    ),
                  ],
                  if (entry.statusReason?.isNotEmpty == true) ...[
                    const SizedBox(height: 3),
                    Text(
                      entry.statusReason!,
                      style: const TextStyle(
                        fontSize: 11.5,
                        height: 1.4,
                        color: appMuted,
                      ),
                    ),
                  ],
                  if (entry.teachers.isNotEmpty) ...[
                    const SizedBox(height: 5),
                    for (final teacher in entry.teachers)
                      _TeacherLine(teacher: teacher, onContact: onContact),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TeacherLine extends StatelessWidget {
  const _TeacherLine({required this.teacher, required this.onContact});

  final ClassroomUsageTeacher teacher;
  final ValueChanged<ClassroomUsageTeacher> onContact;

  @override
  Widget build(BuildContext context) {
    return ConstrainedBox(
      constraints: const BoxConstraints(minHeight: 28),
      child: Row(
        children: [
          const Icon(Icons.person_outline, size: 14, color: appMuted),
          const SizedBox(width: 4),
          Expanded(
            child: Text(
              teacher.name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12.5, color: appInk),
            ),
          ),
          if (teacher.hasVisibleContact)
            TextButton(
              key: ValueKey('usage-contact-${teacher.id}'),
              onPressed: () => onContact(teacher),
              style: TextButton.styleFrom(
                minimumSize: const Size(0, 26),
                padding: const EdgeInsets.symmetric(horizontal: 6),
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                textStyle: const TextStyle(
                  fontSize: 11.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
              child: const Text('联系方式'),
            )
          else
            const Text(
              '联系方式不可查看',
              style: TextStyle(fontSize: 11, color: appMuted),
            ),
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
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 18),
        child: contact == null
            ? SizedBox(
                height: 190,
                child: _loading
                    ? const LoadingView(message: '正在读取联系方式…')
                    : Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(
                            _error == null ? '没有联系方式' : '联系方式读取失败',
                            style: const TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w600,
                              color: appInk,
                            ),
                          ),
                          if (_error != null) ...[
                            const SizedBox(height: 6),
                            Text(
                              ApiException.fromUnknown(_error!).message,
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                fontSize: 12.5,
                                color: appMuted,
                              ),
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
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                      color: appInk,
                    ),
                  ),
                  if (contact.department.isNotEmpty)
                    Text(
                      contact.department,
                      style: const TextStyle(fontSize: 12.5, color: appMuted),
                    ),
                  const SizedBox(height: 12),
                  _ContactRow(
                    label: '电话',
                    value: contact.phone,
                    status: contact.phoneStatus,
                    onCopy: contact.phone == null
                        ? null
                        : () => _copy('电话', contact.phone!),
                  ),
                  const SizedBox(height: 7),
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
      padding: const EdgeInsets.fromLTRB(10, 8, 8, 8),
      decoration: BoxDecoration(
        color: const Color(0xFFF8FAFD),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: appLine),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  style: const TextStyle(fontSize: 11.5, color: appMuted),
                ),
                const SizedBox(height: 1),
                Text(
                  visible ? value! : contactStatusLabel(status),
                  style: const TextStyle(fontSize: 13.5, color: appInk),
                ),
              ],
            ),
          ),
          if (visible)
            IconButton(
              onPressed: onCopy,
              icon: const Icon(Icons.copy_outlined, size: 18),
              tooltip: '复制',
              visualDensity: VisualDensity.compact,
              constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
              padding: EdgeInsets.zero,
            ),
        ],
      ),
    );
  }
}

String _clock(DateTime? value) => value == null ? '--:--' : timeZh(value);
