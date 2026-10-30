import 'dart:async';

import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import '../../shared/status_views.dart';
import '../schedule/models.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';

const _warm = Color(0xFFB7791F);
const _danger = Color(0xFFC2410C);
const _softFill = Color(0xFFF8FAFD);

Color _eligibilityColor(String eligibility) => switch (eligibility) {
  'available' => appTeal,
  'unverified' => _warm,
  'conflict' => _danger,
  _ => appMuted,
};

class ReschedulePage extends StatefulWidget {
  const ReschedulePage({super.key, required this.api, required this.sessionId});

  final ApiClient api;
  final String sessionId;

  @override
  State<ReschedulePage> createState() => _ReschedulePageState();
}

class _ReschedulePageState extends State<ReschedulePage> {
  late DateTime _from;
  late DateTime _until;
  final TextEditingController _reason = TextEditingController();
  final List<ClassroomItem> _classroomFilter = [];

  RescheduleOptionsData? _data;
  Object? _error;
  RescheduleOption? _selected;
  bool _loading = true;
  bool _submitting = false;
  int _requestNo = 0;
  Session? _sourceSession;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _from = DateTime(now.year, now.month, now.day);
    _until = _from.add(const Duration(days: 14));
    _loadSourceSession();
    _load();
  }

  Future<void> _loadSourceSession() async {
    try {
      final response = await widget.api.get('sessions/${widget.sessionId}/');
      if (!mounted) {
        return;
      }
      setState(() {
        _sourceSession = Session.fromJson(mapOf(response.data));
      });
    } catch (_) {
      // The options request surfaces connectivity errors; the header simply
      // keeps its neutral fallback title.
    }
  }

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
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
      final response = await widget.api.post(
        'sessions/${widget.sessionId}/reschedule-options/',
        data: {
          'date_from': isoDate(_from),
          'date_until': isoDate(_until),
          'allow_weekend': false,
          if (_classroomFilter.isNotEmpty)
            'classroom_ids': _classroomFilter.map((room) => room.id).toList(),
        },
      );
      final data = RescheduleOptionsData.fromJson(mapOf(response.data));
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() {
        _data = data;
        _selected = null;
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

  Future<void> _pickRange() async {
    final now = DateTime.now();
    final range = await showDateRangePicker(
      context: context,
      firstDate: DateTime(now.year - 1, 1, 1),
      lastDate: DateTime(now.year + 2, 12, 31),
      initialDateRange: DateTimeRange(start: _from, end: _until),
      helpText: '选择候补日期范围',
      saveText: '应用',
    );
    if (range == null || !mounted) {
      return;
    }
    setState(() {
      _from = DateTime(range.start.year, range.start.month, range.start.day);
      _until = DateTime(range.end.year, range.end.month, range.end.day);
      _data = null;
      _selected = null;
    });
    await _load();
  }

  Future<void> _pickClassrooms() async {
    final result = await showModalBottomSheet<List<ClassroomItem>>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      builder: (context) => FractionallySizedBox(
        heightFactor: 0.82,
        child: _ClassroomFilterSheet(
          api: widget.api,
          initial: _classroomFilter,
        ),
      ),
    );
    if (result == null || !mounted) {
      return;
    }
    setState(() {
      _classroomFilter
        ..clear()
        ..addAll(result);
      _data = null;
      _selected = null;
    });
    await _load();
  }

  Future<void> _submit() async {
    final option = _selected;
    if (option == null || option.startAt == null) {
      showCollabError(context, const ApiException(message: '请先选择可用或需确认的时段。'));
      return;
    }
    final reason = _reason.text.trim();
    if (reason.isEmpty) {
      showCollabError(context, const ApiException(message: '请填写调课原因。'));
      return;
    }
    setState(() => _submitting = true);
    try {
      final response = await widget.api.post(
        'reschedule-requests/',
        data: {
          'session_id': widget.sessionId,
          'target_date': isoDate(option.startAt!),
          'target_start_period': option.startPeriod,
          'target_end_period': option.endPeriod,
          'target_classroom_id': option.classroomId,
          'reason': reason,
          'submit': true,
        },
      );
      final item = RescheduleRequestItem.fromJson(mapOf(response.data));
      if (!mounted) {
        return;
      }
      setState(() => _submitting = false);
      final verification = item.needsVerification
          ? '目标课表信息不全，申请已标记为需教务确认。'
          : '申请已提交给教务。';
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('调课申请已提交'),
          content: Text('$verification\n\n正式课表尚未改变，审批通过后才会生效。'),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('知道了'),
            ),
          ],
        ),
      );
      if (mounted) {
        Navigator.pop(context, item);
      }
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() => _submitting = false);
      final apiError = ApiException.fromUnknown(error);
      final suffix = apiError.code == 'RESCHEDULE_CONFLICT'
          ? '\n目标时段存在冲突，申请未创建，课表未改变。'
          : '';
      showCollabError(
        context,
        ApiException(
          message: '${apiError.message}$suffix',
          code: apiError.code,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    return Scaffold(
      appBar: AppBar(title: const Text('查询调课')),
      body: data == null
          ? _loading
                ? const LoadingView(message: '正在检查可用时段…')
                : CollabErrorView(
                    error: _error ?? const ApiException(message: '没有拿到调课候选。'),
                    onRetry: () => _load(),
                  )
          : LayoutBuilder(
              builder: (context, constraints) {
                final panelMaxHeight = constraints.maxHeight < 280
                    ? constraints.maxHeight
                    : 280.0;
                return Column(
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
                            ..._buildGroups(data.options),
                          ],
                        ),
                      ),
                    ),
                    ConstrainedBox(
                      constraints: BoxConstraints(maxHeight: panelMaxHeight),
                      child: _buildApplicationBar(context),
                    ),
                  ],
                );
              },
            ),
    );
  }

  Widget _buildApplicationBar(BuildContext context) {
    final option = _selected;
    return Material(
      color: Colors.white,
      elevation: 4,
      shadowColor: const Color(0x14000000),
      child: SafeArea(
        top: false,
        child: Container(
          decoration: const BoxDecoration(
            border: Border(top: BorderSide(color: appLine)),
          ),
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(14, 8, 14, 8),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (option == null)
                  const Row(
                    children: [
                      Icon(Icons.touch_app_outlined, size: 16, color: appMuted),
                      SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          '先选择上方可用或需确认的时段',
                          style: TextStyle(fontSize: 12.5, color: appMuted),
                        ),
                      ),
                    ],
                  )
                else
                  Row(
                    children: [
                      const Icon(
                        Icons.check_circle_outline,
                        size: 16,
                        color: appTeal,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          '${dateLabel(option.startAt)} · '
                          '${periodLabel(option.startPeriod, option.endPeriod)} · '
                          '${option.classroomCode}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w600,
                            color: appInk,
                          ),
                        ),
                      ),
                      if (option.isUnverified) ...[
                        const SizedBox(width: 6),
                        _StatusTag(
                          label: '需确认',
                          color: _eligibilityColor('unverified'),
                        ),
                      ],
                    ],
                  ),
                const SizedBox(height: 7),
                TextField(
                  key: const ValueKey('reschedule-reason'),
                  controller: _reason,
                  minLines: 1,
                  maxLines: 2,
                  maxLength: 500,
                  style: const TextStyle(fontSize: 13.5, height: 1.45),
                  decoration: InputDecoration(
                    isDense: true,
                    labelText: '调课原因（必填）',
                    hintText: '简要说明原因',
                    counterText: '',
                    filled: true,
                    fillColor: _softFill,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 11,
                      vertical: 10,
                    ),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8),
                      borderSide: const BorderSide(color: appLine),
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8),
                      borderSide: const BorderSide(color: appLine),
                    ),
                    focusedBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8),
                      borderSide: const BorderSide(color: appTeal, width: 1.2),
                    ),
                  ),
                ),
                const SizedBox(height: 6),
                FilledButton.icon(
                  key: const ValueKey('reschedule-submit'),
                  onPressed: option == null || _submitting || _loading
                      ? null
                      : _submit,
                  style: FilledButton.styleFrom(
                    minimumSize: const Size.fromHeight(42),
                    textStyle: const TextStyle(
                      fontSize: 13.5,
                      fontWeight: FontWeight.w600,
                    ),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(9),
                    ),
                  ),
                  icon: _submitting
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Icon(Icons.send_outlined, size: 17),
                  label: Text(_submitting ? '提交中…' : '提交申请'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildHeader(RescheduleOptionsData data) {
    final available = data.options.where((item) => item.isAvailable).length;
    final unverified = data.options.where((item) => item.isUnverified).length;
    final conflicts = data.options.where((item) => item.isConflict).length;
    final source = _sourceSession;
    final sourceDetail = source == null
        ? null
        : '${dateLabel(source.date)} ${periodLabel(source.startPeriod, source.endPeriod)}'
              '${source.classroom.displayName.isEmpty ? '' : ' · ${source.classroom.displayName}'}';
    return _Panel(
      padding: const EdgeInsets.fromLTRB(12, 11, 12, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 3,
                height: 32,
                margin: const EdgeInsets.only(top: 1),
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
                      source?.courseName ?? '调课申请',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 16,
                        height: 1.35,
                        fontWeight: FontWeight.w600,
                        color: appInk,
                      ),
                    ),
                    if (sourceDetail != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        sourceDetail,
                        style: const TextStyle(
                          fontSize: 12.5,
                          height: 1.4,
                          color: appMuted,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (!data.complete)
                const _StatusTag(label: '部分待确认', color: _warm),
            ],
          ),
          const SizedBox(height: 9),
          const Divider(height: 1),
          _FilterRow(
            icon: Icons.date_range_outlined,
            label: '日期范围',
            value: '${dateLabel(_from)} — ${dateLabel(_until)}',
            onTap: _pickRange,
          ),
          const Divider(height: 1),
          _FilterRow(
            icon: Icons.meeting_room_outlined,
            label: '教室条件',
            value: _classroomFilter.isEmpty
                ? '全部教室'
                : '已选 ${_classroomFilter.length} 间教室',
            onTap: _pickClassrooms,
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 12,
            runSpacing: 4,
            children: [
              _CountLabel(label: '可用 $available', color: appTeal),
              _CountLabel(label: '需确认 $unverified', color: _warm),
              _CountLabel(label: '已知冲突 $conflicts', color: _danger),
            ],
          ),
          if (_classroomFilter.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 7),
              child: Wrap(
                spacing: 6,
                runSpacing: 6,
                children: _classroomFilter
                    .map(
                      (room) => InputChip(
                        label: Text(room.classroomCode),
                        onDeleted: () {
                          setState(() {
                            _classroomFilter.remove(room);
                            _data = null;
                            _selected = null;
                          });
                          _load();
                        },
                        visualDensity: VisualDensity.compact,
                        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                        labelStyle: const TextStyle(fontSize: 12.5),
                        padding: const EdgeInsets.symmetric(horizontal: 2),
                      ),
                    )
                    .toList(growable: false),
              ),
            ),
        ],
      ),
    );
  }

  List<Widget> _buildGroups(List<RescheduleOption> options) {
    final groups = <({String key, String title, List<RescheduleOption> items})>[
      (
        key: 'available',
        title: '可用时段',
        items: options.where((item) => item.isAvailable).toList(),
      ),
      (
        key: 'unverified',
        title: '需确认时段',
        items: options.where((item) => item.isUnverified).toList(),
      ),
      (
        key: 'conflict',
        title: '已知冲突',
        items: options.where((item) => item.isConflict).toList(),
      ),
    ];
    return [
      for (final group in groups)
        if (group.items.isNotEmpty) ...[
          _GroupHeader(
            title: group.title,
            subtitle: group.key == 'unverified'
                ? '可选，提交后标记为待教务核验'
                : group.key == 'conflict'
                ? '仅供查看，不能选中提交'
                : null,
            count: group.items.length,
            color: _eligibilityColor(
              group.key == 'available'
                  ? 'available'
                  : group.key == 'unverified'
                  ? 'unverified'
                  : 'conflict',
            ),
          ),
          for (var index = 0; index < group.items.length; index++)
            Padding(
              padding: const EdgeInsets.only(bottom: 7),
              child: _OptionCard(
                key: ValueKey('reschedule-option-${group.key}-$index'),
                option: group.items[index],
                selected: identical(_selected, group.items[index]),
                onSelect: group.items[index].canChoose
                    ? () => setState(() => _selected = group.items[index])
                    : null,
              ),
            ),
        ],
      if (options.isEmpty) ...[
        const SizedBox(height: 20),
        const EmptyView(
          title: '这段时间没有候选时段',
          detail: '可以调整日期范围或教室条件后重试。',
          icon: Icons.event_busy_outlined,
        ),
      ],
    ];
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

class _FilterRow extends StatelessWidget {
  const _FilterRow({
    required this.icon,
    required this.label,
    required this.value,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final String value;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Icon(icon, size: 17, color: appMuted),
            const SizedBox(width: 8),
            SizedBox(
              width: 58,
              child: Text(
                label,
                style: const TextStyle(fontSize: 12.5, color: appMuted),
              ),
            ),
            Expanded(
              child: Text(
                value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                  color: appInk,
                ),
              ),
            ),
            const Icon(Icons.chevron_right, size: 17, color: appMuted),
          ],
        ),
      ),
    );
  }
}

class _CountLabel extends StatelessWidget {
  const _CountLabel({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 5,
          height: 5,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(
          label,
          style: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w500,
            color: color,
          ),
        ),
      ],
    );
  }
}

class _StatusTag extends StatelessWidget {
  const _StatusTag({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 11.5,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

class _GroupHeader extends StatelessWidget {
  const _GroupHeader({
    required this.title,
    required this.count,
    required this.color,
    this.subtitle,
  });

  final String title;
  final int count;
  final Color color;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(2, 13, 2, 7),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 5,
            height: 5,
            margin: const EdgeInsets.only(top: 6),
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 7),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w700,
                    color: appInk,
                  ),
                ),
                if (subtitle != null) ...[
                  const SizedBox(height: 1),
                  Text(
                    subtitle!,
                    style: const TextStyle(
                      fontSize: 12,
                      height: 1.4,
                      color: appMuted,
                    ),
                  ),
                ],
              ],
            ),
          ),
          Text(
            '$count 个',
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w500,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}

class _OptionCard extends StatelessWidget {
  const _OptionCard({
    super.key,
    required this.option,
    required this.selected,
    required this.onSelect,
  });

  final RescheduleOption option;
  final bool selected;
  final VoidCallback? onSelect;

  @override
  Widget build(BuildContext context) {
    final color = _eligibilityColor(option.eligibility);
    final missing = option.missingData.map(coverageLabel).toSet().toList();
    return Container(
      decoration: BoxDecoration(
        color: selected ? const Color(0xFFF4F8FF) : Colors.white,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: selected ? appTeal : appLine,
          width: selected ? 1.4 : 1,
        ),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: onSelect,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(11, 9, 11, 9),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      '${dateLabel(option.startAt)} · '
                      '${periodLabel(option.startPeriod, option.endPeriod)}',
                      style: const TextStyle(
                        fontSize: 14,
                        height: 1.4,
                        fontWeight: FontWeight.w600,
                        color: appInk,
                      ),
                    ),
                  ),
                  const SizedBox(width: 7),
                  _StatusTag(label: option.label, color: color),
                  if (selected) ...[
                    const SizedBox(width: 5),
                    const Icon(Icons.check_circle, color: appMint, size: 17),
                  ],
                ],
              ),
              const SizedBox(height: 3),
              Row(
                children: [
                  const Icon(
                    Icons.meeting_room_outlined,
                    size: 15,
                    color: appMuted,
                  ),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      option.classroomCode,
                      style: const TextStyle(fontSize: 12.5, color: appInk),
                    ),
                  ),
                  if (option.isConflict)
                    const Text(
                      '不可选',
                      style: TextStyle(fontSize: 11.5, color: _danger),
                    ),
                ],
              ),
              if (option.studentConflictCount > 0) ...[
                const SizedBox(height: 4),
                Text(
                  '学生冲突 ${option.studentConflictCount} 人',
                  style: TextStyle(fontSize: 12, color: color),
                ),
              ],
              if (option.reasons.isNotEmpty) ...[
                const SizedBox(height: 4),
                for (final reason in option.reasons)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 2),
                    child: Text(
                      '· $reason',
                      style: const TextStyle(
                        fontSize: 12,
                        height: 1.4,
                        color: appMuted,
                      ),
                    ),
                  ),
              ],
              if (option.conflicts.isNotEmpty) ...[
                const SizedBox(height: 3),
                for (final conflict in option.conflicts)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 2),
                    child: Text(
                      '· ${conflict.label}',
                      style: TextStyle(fontSize: 12, color: color),
                    ),
                  ),
              ],
              if (missing.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  '还缺：${missing.join('、')}',
                  style: const TextStyle(fontSize: 12, color: appMuted),
                ),
              ],
              if (option.isUnverified) ...[
                const SizedBox(height: 4),
                const Text(
                  '课表信息不全，提交后需教务确认。',
                  style: TextStyle(fontSize: 12, height: 1.4, color: _warm),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _ClassroomFilterSheet extends StatefulWidget {
  const _ClassroomFilterSheet({required this.api, required this.initial});

  final ApiClient api;
  final List<ClassroomItem> initial;

  @override
  State<_ClassroomFilterSheet> createState() => _ClassroomFilterSheetState();
}

class _ClassroomFilterSheetState extends State<_ClassroomFilterSheet> {
  final TextEditingController _search = TextEditingController();
  final Map<String, ClassroomItem> _known = {};
  final Set<String> _selected = {};
  Timer? _debounce;
  List<ClassroomItem> _results = const [];
  bool _loading = true;
  Object? _error;
  int _requestNo = 0;

  @override
  void initState() {
    super.initState();
    for (final room in widget.initial) {
      _known[room.id] = room;
      _selected.add(room.id);
    }
    _search.addListener(_onSearchChanged);
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search
      ..removeListener(_onSearchChanged)
      ..dispose();
    super.dispose();
  }

  void _onSearchChanged() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300), _load);
  }

  Future<void> _load() async {
    final requestNo = ++_requestNo;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final response = await widget.api.get(
        'classrooms/',
        query: {'q': _search.text.trim()},
      );
      final body = mapOf(response.data);
      final rooms = mapListOf(body['results'])
          .map(ClassroomItem.fromJson)
          .toList(growable: false);
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() {
        _results = rooms;
        for (final room in rooms) {
          _known[room.id] = room;
        }
        _loading = false;
      });
    } catch (error) {
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() {
        _loading = false;
        _error = error;
      });
    }
  }

  void _apply() {
    final selected = _selected
        .map((id) => _known[id])
        .whereType<ClassroomItem>()
        .toList(growable: false);
    Navigator.pop(context, selected);
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Expanded(
                  child: Text(
                    '按教室筛选（可选）',
                    style: TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                      color: appInk,
                    ),
                  ),
                ),
                TextButton(
                  onPressed: () => setState(_selected.clear),
                  child: const Text('清空'),
                ),
              ],
            ),
            const SizedBox(height: 2),
            TextField(
              controller: _search,
              style: const TextStyle(fontSize: 13.5),
              decoration: InputDecoration(
                isDense: true,
                prefixIcon: const Icon(Icons.search, size: 19),
                hintText: '搜索教室代码、楼宇或房间',
                filled: true,
                fillColor: _softFill,
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 10,
                ),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: appLine),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: appLine),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: appTeal, width: 1.2),
                ),
              ),
            ),
            if (_loading) const CollabInlineProgress(),
            const SizedBox(height: 6),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.all(10),
                child: Column(
                  children: [
                    Text(
                      ApiException.fromUnknown(_error!).message,
                      style: const TextStyle(fontSize: 13, color: appMuted),
                    ),
                    TextButton(onPressed: _load, child: const Text('重试')),
                  ],
                ),
              ),
            Expanded(
              child: ListView.builder(
                itemCount: _results.length,
                itemBuilder: (context, index) {
                  final room = _results[index];
                  return CheckboxListTile(
                    key: ValueKey('classroom-filter-${room.id}'),
                    value: _selected.contains(room.id),
                    onChanged: (checked) {
                      setState(() {
                        if (checked == true) {
                          _selected.add(room.id);
                        } else {
                          _selected.remove(room.id);
                        }
                      });
                    },
                    dense: true,
                    visualDensity: VisualDensity.compact,
                    title: Text(
                      room.classroomCode,
                      style: const TextStyle(fontSize: 14, color: appInk),
                    ),
                    subtitle: Text(
                      '${room.location} · 容量 ${room.capacity}${room.isActive ? '' : ' · 停用'}',
                      style: const TextStyle(fontSize: 12, color: appMuted),
                    ),
                    controlAffinity: ListTileControlAffinity.leading,
                  );
                },
              ),
            ),
            FilledButton(
              onPressed: _apply,
              style: FilledButton.styleFrom(
                minimumSize: const Size.fromHeight(42),
                textStyle: const TextStyle(
                  fontSize: 13.5,
                  fontWeight: FontWeight.w600,
                ),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(9),
                ),
              ),
              child: Text(
                '应用${_selected.isEmpty ? '' : '（${_selected.length}）'}',
              ),
            ),
          ],
        ),
      ),
    );
  }
}
