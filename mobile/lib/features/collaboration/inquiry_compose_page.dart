import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';

class InquiryComposePage extends StatefulWidget {
  const InquiryComposePage({
    super.key,
    required this.api,
    required this.classroomId,
  });

  final ApiClient api;
  final String classroomId;

  @override
  State<InquiryComposePage> createState() => _InquiryComposePageState();
}

class _InquiryComposePageState extends State<InquiryComposePage> {
  final TextEditingController _description = TextEditingController();
  final Set<String> _selected = {};
  late DateTime _from;
  late DateTime _to;
  InquiryCandidatesData? _candidates;
  Object? _candidatesError;
  bool _loadingCandidates = true;
  bool _submitting = false;
  int _candidatesRequestNo = 0;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _from = DateTime(now.year, now.month, now.day, now.hour);
    _to = _from.add(const Duration(hours: 2));
    _description.addListener(_onChanged);
    _loadCandidates();
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

  String? get _rangeError {
    if (!_to.isAfter(_from)) {
      return '结束时间必须晚于开始时间。';
    }
    if (_to.difference(_from) > const Duration(days: 7)) {
      return '协查时间跨度不能超过 7 天。';
    }
    return null;
  }

  Future<void> _loadCandidates({bool keepData = false}) async {
    final rangeError = _rangeError;
    if (rangeError != null) {
      showCollabError(context, Exception(rangeError));
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
          'classroom_id': widget.classroomId,
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

  Future<void> _chooseFrom() async {
    final selected = await pickCollabDateTime(
      context,
      initial: _from,
      helpText: '选择协查开始时间',
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
      helpText: '选择协查结束时间',
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
    final description = _description.text.trim();
    if (description.isEmpty) {
      showCollabError(context, Exception('请填写物品描述。'));
      return;
    }
    if (_selected.isEmpty) {
      showCollabError(context, Exception('请至少选择一位接收教师。'));
      return;
    }
    final rangeError = _rangeError;
    if (rangeError != null) {
      showCollabError(context, Exception(rangeError));
      return;
    }
    setState(() => _submitting = true);
    try {
      await widget.api.post(
        'inquiries/',
        data: {
          'classroom_id': widget.classroomId,
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
          title: const Text('协查已发起'),
          content: const Text('已只向选中的教师发送。回复会追加在协查详情中。'),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('知道了'),
            ),
          ],
        ),
      );
      if (mounted) {
        Navigator.pop(context, true);
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
        _rangeError == null;
    return Scaffold(
      appBar: AppBar(title: const Text('发起失物协查')),
      body: Column(
        children: [
          if (_loadingCandidates) const CollabInlineProgress(),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
              children: [
                const CollabNotice(
                  text: '协查只发给当前时段相关且被选中的教师；排定课程记录不代表实际到场或物品去向。',
                  icon: Icons.volunteer_activism_outlined,
                ),
                const SizedBox(height: 12),
                CollabSectionCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '协查时间范围',
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
                          onPressed: _loadingCandidates
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
                  subtitle: '只会发送给勾选的人，未选中的教师不会收到协查。',
                  trailing: CollabStatusPill(
                    label: '已选 ${_selected.length}',
                    color: _selected.isEmpty ? appMuted : appTeal,
                  ),
                ),
                if (candidates == null)
                  _loadingCandidates
                      ? const Padding(
                          padding: EdgeInsets.symmetric(vertical: 36),
                          child: LoadingView(message: '正在查找相关教师…'),
                        )
                      : CollabErrorView(
                          error: _candidatesError ?? Exception('没有拿到协查候选。'),
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
