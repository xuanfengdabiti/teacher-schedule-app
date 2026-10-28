import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import '../../shared/status_views.dart';
import 'classroom_search_page.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';

class InquiryComposePage extends StatefulWidget {
  const InquiryComposePage({
    super.key,
    required this.api,
    this.classroomId,
    this.initialClassroom,
    this.initialDate,
  });

  final ApiClient api;
  final String? classroomId;
  final ClassroomItem? initialClassroom;
  final DateTime? initialDate;

  @override
  State<InquiryComposePage> createState() => _InquiryComposePageState();
}

class _InquiryComposePageState extends State<InquiryComposePage> {
  final TextEditingController _description = TextEditingController();
  final Set<String> _selected = {};
  late DateTime _from;
  late DateTime _to;
  String? _classroomId;
  ClassroomItem? _classroom;
  InquiryCandidatesData? _candidates;
  Object? _candidatesError;
  bool _loadingCandidates = false;
  bool _submitting = false;
  int _candidatesRequestNo = 0;

  @override
  void initState() {
    super.initState();
    _classroomId = widget.classroomId;
    _classroom = widget.initialClassroom;
    final initial = widget.initialDate;
    if (initial != null) {
      _from = DateTime(initial.year, initial.month, initial.day, 8, 10);
      _to = DateTime(initial.year, initial.month, initial.day, 17, 10);
    } else {
      final now = DateTime.now();
      _from = DateTime(now.year, now.month, now.day, 8, 10);
      _to = DateTime(now.year, now.month, now.day, 17, 10);
    }
    _description.addListener(_onChanged);
    if (_classroomId != null) {
      _loadCandidates();
    }
  }

  @override
  void dispose() {
    _description
      ..removeListener(_onChanged)
      ..dispose();
    super.dispose();
  }

  void _onChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  String get _classroomLabel {
    final room = _classroom;
    if (room == null) {
      return '已选择教室';
    }
    final location = [
      room.campus,
      room.building,
      room.roomNo,
    ].where((part) => part.isNotEmpty).join(' · ');
    return location.isEmpty ? room.classroomCode : location;
  }

  String? get _rangeError {
    if (!_to.isAfter(_from)) {
      return '结束时间必须晚于开始时间。';
    }
    if (_to.difference(_from) > const Duration(days: 7)) {
      return '询问时间跨度不能超过 7 天。';
    }
    return null;
  }

  Future<void> _loadCandidates({bool keepData = false}) async {
    final classroomId = _classroomId;
    if (classroomId == null) {
      return;
    }
    final rangeError = _rangeError;
    if (rangeError != null) {
      showCollabError(context, ApiException(message: rangeError));
      return;
    }
    final requestNo = ++_candidatesRequestNo;
    setState(() {
      _loadingCandidates = true;
      if (!keepData) {
        _candidatesError = null;
      }
    });
    try {
      final response = await widget.api.get(
        'inquiries/candidates/',
        query: {
          'classroom_id': classroomId,
          'from_at': isoDateTimeWithOffset(_from),
          'to_at': isoDateTimeWithOffset(_to),
        },
      );
      final data = InquiryCandidatesData.fromJson(mapOf(response.data));
      final validIds = data.candidates.map((item) => item.teacher.id).toSet();
      if (!mounted || requestNo != _candidatesRequestNo) {
        return;
      }
      setState(() {
        _candidates = data;
        _candidatesError = null;
        _loadingCandidates = false;
        _selected.removeWhere((id) => !validIds.contains(id));
      });
    } catch (error) {
      if (!mounted || requestNo != _candidatesRequestNo) {
        return;
      }
      setState(() {
        _candidatesError = error;
        _loadingCandidates = false;
      });
    }
  }

  Future<void> _pickClassroom() async {
    final picked = await Navigator.of(context).push<ClassroomItem>(
      MaterialPageRoute<ClassroomItem>(
        builder: (context) => ClassroomSearchPage(
          api: widget.api,
          title: '选择教室',
          onSelect: (room) => Navigator.of(context).pop(room),
        ),
      ),
    );
    if (picked == null || !mounted) {
      return;
    }
    setState(() {
      _classroom = picked;
      _classroomId = picked.id;
      _candidates = null;
      _candidatesError = null;
      _selected.clear();
    });
    await _loadCandidates();
  }

  Future<void> _chooseFrom() async {
    final selected = await pickCollabDateTime(
      context,
      initial: _from,
      helpText: '选择询问开始时间',
    );
    if (selected == null || !mounted) {
      return;
    }
    setState(() {
      _from = selected;
      _candidates = null;
      _candidatesError = null;
      _selected.clear();
      _loadingCandidates = false;
    });
    _candidatesRequestNo++;
    if (_rangeError == null) {
      await _loadCandidates();
    }
  }

  Future<void> _chooseTo() async {
    final selected = await pickCollabDateTime(
      context,
      initial: _to,
      helpText: '选择询问结束时间',
    );
    if (selected == null || !mounted) {
      return;
    }
    setState(() {
      _to = selected;
      _candidates = null;
      _candidatesError = null;
      _selected.clear();
      _loadingCandidates = false;
    });
    _candidatesRequestNo++;
    if (_rangeError == null) {
      await _loadCandidates();
    }
  }

  Future<void> _submit() async {
    final classroomId = _classroomId;
    if (classroomId == null) {
      showCollabError(context, ApiException(message: '请先选择教室。'));
      return;
    }
    final description = _description.text.trim();
    if (description.isEmpty) {
      showCollabError(context, ApiException(message: '请填写物品描述。'));
      return;
    }
    if (_selected.isEmpty) {
      showCollabError(context, ApiException(message: '请至少选择一位接收教师。'));
      return;
    }
    final rangeError = _rangeError;
    if (rangeError != null) {
      showCollabError(context, ApiException(message: rangeError));
      return;
    }
    setState(() => _submitting = true);
    try {
      await widget.api.post(
        'inquiries/',
        data: {
          'classroom_id': classroomId,
          'from_at': isoDateTimeWithOffset(_from),
          'to_at': isoDateTimeWithOffset(_to),
          'item_description': description,
          'recipient_ids': _selected.toList(growable: false),
        },
      );
      if (!mounted) {
        return;
      }
      setState(() => _submitting = false);
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('失物询问已发起'),
          content: const Text('已只向选中的教师发送。回复会追加在失物详情中。'),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('知道了'),
            ),
          ],
        ),
      );
      if (mounted) {
        Navigator.of(context).pop(true);
      }
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() => _submitting = false);
      showCollabError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final candidates = _candidates;
    final canSubmit =
        !_submitting &&
        !_loadingCandidates &&
        _candidates != null &&
        _selected.isNotEmpty &&
        _description.text.trim().isNotEmpty &&
        _rangeError == null &&
        _classroomId != null;
    return Scaffold(
      appBar: AppBar(title: const Text('发起失物询问')),
      body: Column(
        children: [
          if (_loadingCandidates) const CollabInlineProgress(),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
              children: [
                CollabSectionCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              '询问教室',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                          ),
                          TextButton.icon(
                            key: const ValueKey('inquiry-classroom-pick'),
                            onPressed: _pickClassroom,
                            icon: const Icon(Icons.meeting_room_outlined),
                            label: Text(_classroomId == null ? '选择教室' : '更换'),
                          ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      if (_classroomId == null)
                        const Text(
                          '先选择教室，再选择时间和相关教师。',
                          key: ValueKey('inquiry-classroom-empty'),
                          style: TextStyle(fontSize: 14, color: appMuted),
                        )
                      else
                        Text(
                          _classroomLabel,
                          key: const ValueKey('inquiry-classroom-selected'),
                          style: const TextStyle(fontSize: 16, color: appInk),
                        ),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
                const CollabNotice(
                  text: '失物询问只发给当前时段相关且被选中的教师；排定课程记录不代表实际到场或物品去向。',
                  icon: Icons.volunteer_activism_outlined,
                ),
                const SizedBox(height: 12),
                CollabSectionCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '询问时间范围',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 10),
                      _TimeButton(
                        key: const ValueKey('inquiry-from'),
                        label: '开始',
                        value: fullDateTimeLabel(_from),
                        onPressed: _chooseFrom,
                      ),
                      const SizedBox(height: 8),
                      _TimeButton(
                        key: const ValueKey('inquiry-to'),
                        label: '结束',
                        value: fullDateTimeLabel(_to),
                        onPressed: _chooseTo,
                      ),
                      if (_rangeError != null) ...[
                        const SizedBox(height: 8),
                        Text(
                          _rangeError!,
                          style: TextStyle(
                            color: Theme.of(context).colorScheme.error,
                          ),
                        ),
                      ],
                      const SizedBox(height: 14),
                      TextField(
                        key: const ValueKey('inquiry-description'),
                        controller: _description,
                        minLines: 2,
                        maxLines: 4,
                        maxLength: 1000,
                        decoration: const InputDecoration(
                          labelText: '物品描述（必填）',
                          hintText: '例如：黑色雨伞、蓝色水杯，说明可辨认的特征',
                        ),
                      ),
                      SizedBox(
                        width: double.infinity,
                        child: OutlinedButton.icon(
                          key: const ValueKey('inquiry-query'),
                          onPressed: _classroomId == null || _loadingCandidates
                              ? null
                              : () => _loadCandidates(keepData: true),
                          icon: const Icon(Icons.people_outline),
                          label: const Text('查询相关教师'),
                        ),
                      ),
                    ],
                  ),
                ),
                CollabSectionTitle(
                  title: '选择接收教师',
                  subtitle: '只会发送给勾选的人，未选中的教师不会收到失物询问。',
                  trailing: CollabStatusPill(
                    label: '已选 ${_selected.length}',
                    color: _selected.isEmpty ? appMuted : appTeal,
                  ),
                ),
                if (_classroomId == null)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 28),
                    child: EmptyView(
                      title: '请先选择教室',
                      detail: '选择后会查找该时段的相关教师。',
                      icon: Icons.meeting_room_outlined,
                    ),
                  )
                else if (candidates == null)
                  _loadingCandidates
                      ? const Padding(
                          padding: EdgeInsets.symmetric(vertical: 36),
                          child: LoadingView(message: '正在查找相关教师…'),
                        )
                      : CollabErrorView(
                          error:
                              _candidatesError ??
                              ApiException(message: '暂时无法加载接收老师，请重试。'),
                          onRetry: () => _loadCandidates(),
                        )
                else if (candidates.candidates.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 28),
                    child: EmptyView(
                      title: '该时段没有相关教师',
                      detail: '调整时间范围后重新查询。',
                      icon: Icons.person_search_outlined,
                    ),
                  )
                else
                  for (final candidate in candidates.candidates)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _CandidateTile(
                        key: ValueKey(
                          'inquiry-candidate-${candidate.teacher.id}',
                        ),
                        candidate: candidate,
                        selected: _selected.contains(candidate.teacher.id),
                        onChanged: (value) {
                          setState(() {
                            if (value) {
                              _selected.add(candidate.teacher.id);
                            } else {
                              _selected.remove(candidate.teacher.id);
                            }
                          });
                        },
                      ),
                    ),
                const SizedBox(height: 14),
                FilledButton.icon(
                  key: const ValueKey('inquiry-submit'),
                  onPressed: canSubmit ? _submit : null,
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
                  label: Text(_submitting ? '发送中…' : '发送给选中教师'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _TimeButton extends StatelessWidget {
  const _TimeButton({
    super.key,
    required this.label,
    required this.value,
    required this.onPressed,
  });

  final String label;
  final String value;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return OutlinedButton(
      onPressed: onPressed,
      style: OutlinedButton.styleFrom(
        alignment: Alignment.centerLeft,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      ),
      child: Row(
        children: [
          SizedBox(
            width: 46,
            child: Text(label, style: Theme.of(context).textTheme.bodySmall),
          ),
          Expanded(child: Text(value)),
          const Icon(Icons.schedule, size: 18),
        ],
      ),
    );
  }
}

class _CandidateTile extends StatelessWidget {
  const _CandidateTile({
    super.key,
    required this.candidate,
    required this.selected,
    required this.onChanged,
  });

  final InquiryCandidate candidate;
  final bool selected;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    final sessions = candidate.sessions.take(3).toList(growable: false);
    return CollabSectionCard(
      child: CheckboxListTile(
        value: selected,
        onChanged: (value) => onChanged(value == true),
        controlAffinity: ListTileControlAffinity.leading,
        contentPadding: EdgeInsets.zero,
        title: Text(
          candidate.teacher.name,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (candidate.teacher.department.isNotEmpty)
              Text(candidate.teacher.department),
            if (candidate.courseNames.isNotEmpty)
              Text('课程：${candidate.courseNames.join('、')}'),
            for (final session in sessions)
              Text(
                '${dateTimeLabel(session.startAt)} · ${session.courseName}'
                '${session.startPeriod == 0 ? '' : ' · ${periodLabel(session.startPeriod, session.endPeriod)}'}',
              ),
            if (candidate.sessions.length > sessions.length)
              Text('另有 ${candidate.sessions.length - sessions.length} 条相关课次'),
          ],
        ),
      ),
    );
  }
}
