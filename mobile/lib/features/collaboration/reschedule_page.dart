import 'dart:async';

import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import '../../shared/status_views.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';

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

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _from = DateTime(now.year, now.month, now.day);
    _until = _from.add(const Duration(days: 14));
    _load();
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
      showCollabError(context, const ApiException(message: '请先选择可用或待核验的时段。'));
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
          ? '目标数据不完整，申请已标记为待教务核验。'
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
      appBar: AppBar(title: const Text('调课申请')),
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
                          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
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
      elevation: 8,
      child: SafeArea(
        top: false,
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (option == null)
                Row(
                  children: [
                    const Icon(
                      Icons.touch_app_outlined,
                      size: 18,
                      color: appMuted,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '先在上方选择可用或待核验时段',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ),
                  ],
                )
              else
                Row(
                  children: [
                    const Icon(
                      Icons.check_circle_outline,
                      size: 18,
                      color: appTeal,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '${dateLabel(option.startAt)} · '
                        '${periodLabel(option.startPeriod, option.endPeriod)} · '
                        '${option.classroomCode}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.bodyMedium
                            ?.copyWith(fontWeight: FontWeight.w600),
                      ),
                    ),
                    if (option.isUnverified) ...[
                      const SizedBox(width: 6),
                      CollabStatusPill(
                        label: '待核验',
                        color: rescheduleEligibilityColor('unverified'),
                      ),
                    ],
                  ],
                ),
              const SizedBox(height: 8),
              TextField(
                key: const ValueKey('reschedule-reason'),
                controller: _reason,
                minLines: 1,
                maxLines: 2,
                maxLength: 500,
                decoration: const InputDecoration(
                  isDense: true,
                  labelText: '调课原因（必填）',
                  hintText: '说明为什么需要调整，便于教务审批',
                  counterText: '',
                ),
              ),
              const SizedBox(height: 6),
              FilledButton.icon(
                key: const ValueKey('reschedule-submit'),
                onPressed: option == null || _submitting || _loading
                    ? null
                    : _submit,
                style: FilledButton.styleFrom(
                  minimumSize: const Size.fromHeight(44),
                ),
                icon: _submitting
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Icon(Icons.send_outlined),
                label: Text(_submitting ? '提交中…' : '提交申请'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader(RescheduleOptionsData data) {
    final available = data.options.where((item) => item.isAvailable).length;
    final unverified = data.options.where((item) => item.isUnverified).length;
    final conflicts = data.options.where((item) => item.isConflict).length;
    return CollabSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  '课次 ${shortId(widget.sessionId)}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              CollabStatusPill(
                label: data.complete ? '数据完整' : '数据不完整',
                color: data.complete ? appMint : const Color(0xFFB7791F),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            '${dateLabel(_from)} — ${dateLabel(_until)}'
            '${_classroomFilter.isEmpty ? '' : ' · 已筛选 ${_classroomFilter.length} 间教室'}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton.icon(
                onPressed: _pickRange,
                icon: const Icon(Icons.date_range_outlined, size: 18),
                label: const Text('日期范围'),
              ),
              OutlinedButton.icon(
                onPressed: _pickClassrooms,
                icon: const Icon(Icons.meeting_room_outlined, size: 18),
                label: Text(
                  _classroomFilter.isEmpty
                      ? '教室条件'
                      : '教室 ${_classroomFilter.length}',
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            '可用 $available · 待核验 $unverified · 已知冲突 $conflicts',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (_classroomFilter.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
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
        title: '待核验时段',
        items: options.where((item) => item.isUnverified).toList(),
      ),
      (
        key: 'conflict',
        title: '已知冲突（不可直接提交）',
        items: options.where((item) => item.isConflict).toList(),
      ),
    ];
    return [
      for (final group in groups)
        if (group.items.isNotEmpty) ...[
          CollabSectionTitle(
            title: group.title,
            subtitle: group.key == 'unverified'
                ? '可选，提交后会标记为待教务核验'
                : group.key == 'conflict'
                ? '仅供查看，不能选中提交'
                : null,
            trailing: CollabStatusPill(
              label: '${group.items.length}',
              color: rescheduleEligibilityColor(
                group.key == 'available'
                    ? 'available'
                    : group.key == 'unverified'
                    ? 'unverified'
                    : 'conflict',
              ),
            ),
          ),
          for (var index = 0; index < group.items.length; index++)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
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
        const SizedBox(height: 28),
        const EmptyView(
          title: '这段时间没有候选时段',
          detail: '可以调整日期范围或教室条件后重试。',
          icon: Icons.event_busy_outlined,
        ),
      ],
    ];
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
    final color = rescheduleEligibilityColor(option.eligibility);
    final missing = option.missingData.map(coverageLabel).toSet().toList();
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(
          color: selected ? appTeal : appLine,
          width: selected ? 2 : 1,
        ),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onSelect,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      '${dateLabel(option.startAt)} · ${periodLabel(option.startPeriod, option.endPeriod)}',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                  const SizedBox(width: 8),
                  CollabStatusPill(label: option.label, color: color),
                ],
              ),
              const SizedBox(height: 6),
              Row(
                children: [
                  const Icon(
                    Icons.meeting_room_outlined,
                    size: 17,
                    color: appMuted,
                  ),
                  const SizedBox(width: 5),
                  Expanded(child: Text(option.classroomCode)),
                  if (selected)
                    const Icon(Icons.check_circle, color: appTeal, size: 20),
                ],
              ),
              if (option.studentConflictCount > 0) ...[
                const SizedBox(height: 6),
                Text(
                  '学生冲突 ${option.studentConflictCount} 人',
                  style: Theme.of(context).textTheme.bodySmall
                      ?.copyWith(color: color),
                ),
              ],
              if (option.reasons.isNotEmpty) ...[
                const SizedBox(height: 8),
                for (final reason in option.reasons)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 3),
                    child: Text(
                      '· $reason',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
              ],
              if (option.conflicts.isNotEmpty) ...[
                const SizedBox(height: 6),
                for (final conflict in option.conflicts)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 3),
                    child: Text(
                      '· ${conflict.label}',
                      style: Theme.of(context).textTheme.bodySmall
                          ?.copyWith(color: color),
                    ),
                  ),
              ],
              if (missing.isNotEmpty) ...[
                const SizedBox(height: 8),
                Text(
                  '缺少数据：${missing.join('、')}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
              if (option.isUnverified) ...[
                const SizedBox(height: 8),
                Text(
                  '该时段数据不完整，提交后需要教务核验。',
                  style: Theme.of(context).textTheme.bodySmall,
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
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '按教室筛选（可选）',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                ),
                TextButton(
                  onPressed: () => setState(_selected.clear),
                  child: const Text('清空'),
                ),
              ],
            ),
            TextField(
              controller: _search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: '搜索教室代码、楼宇或房间',
              ),
            ),
            if (_loading) const CollabInlineProgress(),
            const SizedBox(height: 8),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  children: [
                    Text(ApiException.fromUnknown(_error!).message),
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
                    title: Text(room.classroomCode),
                    subtitle: Text(
                      '${room.location} · 容量 ${room.capacity}${room.isActive ? '' : ' · 停用'}',
                    ),
                    controlAffinity: ListTileControlAffinity.leading,
                  );
                },
              ),
            ),
            FilledButton(
              onPressed: _apply,
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
