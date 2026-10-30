import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/providers.dart';
import '../../shared/status_views.dart';
import '../schedule/schedule_controller.dart';
import 'note_models.dart';
import 'notes_controller.dart';

class NotesPage extends ConsumerStatefulWidget {
  const NotesPage({
    super.key,
    required this.sessionId,
    this.title,
    this.editable = true,
  });

  final String sessionId;
  final String? title;
  final bool editable;

  @override
  ConsumerState<NotesPage> createState() => _NotesPageState();
}

class _NotesPageState extends ConsumerState<NotesPage> {
  ({String courseName, String? contextLabel})? _context;

  @override
  void didUpdateWidget(covariant NotesPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.sessionId != widget.sessionId) {
      _context = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(notesControllerProvider(widget.sessionId));
    final schedule = ref.watch(scheduleControllerProvider);
    final found = _findSessionContext(schedule, widget.sessionId);
    if (found != null) {
      _context = found;
    }
    final cached = _context;
    final suppliedTitle = widget.title?.trim() ?? '';
    final courseName = suppliedTitle.isNotEmpty
        ? suppliedTitle
        : cached?.courseName ?? '本课记录';
    return Scaffold(
      appBar: AppBar(title: const Text('教学备注')),
      body: state.isLoading
          ? const LoadingView(message: '正在加载…')
          : state.loadError != null && !state.offline
          ? ErrorView(
              message: state.loadError!,
              onRetry: () => ref
                  .read(notesControllerProvider(widget.sessionId).notifier)
                  .retryLoad(),
            )
          : _NoteEditor(
              sessionId: widget.sessionId,
              accountId: state.accountId,
              initialText: state.initialText ?? const NoteText(),
              initialBaseVersion: state.effectiveBaseVersion,
              serverText: state.mine?.draft ?? const NoteText(),
              offlineError: state.offline ? state.loadError : null,
              editable: widget.editable,
              courseName: courseName,
              contextLabel: cached?.contextLabel,
            ),
    );
  }
}

({String courseName, String? contextLabel})? _findSessionContext(
  ScheduleState schedule,
  String sessionId,
) {
  for (final week in schedule.weeks.values) {
    final data = week.asData?.value;
    if (data == null) {
      continue;
    }
    for (final session in data.sessions) {
      if (session.id != sessionId) {
        continue;
      }
      final details = <String>[
        '${monthDayZh(session.date)} ${weekdayZh(session.weekday)}',
        session.periodsLabel,
        if (session.classroom.displayName.isNotEmpty)
          session.classroom.displayName,
      ];
      return (
        courseName: session.courseName,
        contextLabel: details.join(' · '),
      );
    }
  }
  return null;
}

class _NoteEditor extends ConsumerStatefulWidget {
  const _NoteEditor({
    required this.sessionId,
    required this.accountId,
    required this.initialText,
    required this.initialBaseVersion,
    required this.serverText,
    required this.offlineError,
    required this.editable,
    required this.courseName,
    required this.contextLabel,
  });

  final String sessionId;
  final String accountId;
  final NoteText initialText;
  final int initialBaseVersion;
  final NoteText serverText;
  final String? offlineError;
  final bool editable;
  final String courseName;
  final String? contextLabel;

  @override
  ConsumerState<_NoteEditor> createState() => _NoteEditorState();
}

class _NoteEditorState extends ConsumerState<_NoteEditor> {
  static const _warm = Color(0xFFB7791F);
  static const _fieldFill = Color(0xFFF8FAFD);

  late final TextEditingController _covered;
  late final TextEditingController _unfinished;
  late final TextEditingController _nextPlan;
  late final DraftStore _drafts;
  late NoteText _serverText;
  late int _baseVersion;
  Timer? _localSaveTimer;
  bool _saving = false;
  bool _publishing = false;
  String? _inlineError;
  String? _inlineStatus;

  NotesController get _controller =>
      ref.read(notesControllerProvider(widget.sessionId).notifier);

  @override
  void initState() {
    super.initState();
    _covered = TextEditingController(text: widget.initialText.covered);
    _unfinished = TextEditingController(text: widget.initialText.unfinished);
    _nextPlan = TextEditingController(text: widget.initialText.nextPlan);
    _drafts = ref.read(draftStoreProvider);
    _serverText = widget.serverText;
    _baseVersion = widget.initialBaseVersion;
    _covered.addListener(_onChanged);
    _unfinished.addListener(_onChanged);
    _nextPlan.addListener(_onChanged);
  }

  @override
  void didUpdateWidget(covariant _NoteEditor oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.initialBaseVersion != widget.initialBaseVersion) {
      _baseVersion = widget.initialBaseVersion;
    }
    if (_text().sameAs(widget.serverText) &&
        !_text().sameAs(oldWidget.serverText)) {
      _serverText = widget.serverText;
    }
  }

  @override
  void dispose() {
    _localSaveTimer?.cancel();
    final current = _text();
    if (widget.editable && !current.sameAs(_serverText)) {
      unawaited(
        _drafts.save(
          widget.accountId,
          widget.sessionId,
          current,
          baseVersion: _baseVersion,
        ),
      );
    }
    _covered.dispose();
    _unfinished.dispose();
    _nextPlan.dispose();
    super.dispose();
  }

  NoteText _text() => NoteText(
    covered: _covered.text,
    unfinished: _unfinished.text,
    nextPlan: _nextPlan.text,
  );

  void _onChanged() {
    _localSaveTimer?.cancel();
    _localSaveTimer = Timer(const Duration(milliseconds: 500), () {
      if (mounted && widget.editable) {
        unawaited(_controller.saveLocal(_text()));
      }
    });
  }

  Future<void> _save({int? baseVersion}) async {
    if (_saving || _publishing) {
      return;
    }
    final snapshot = _text();
    setState(() {
      _saving = true;
      _inlineError = null;
      _inlineStatus = null;
    });
    final result = await _controller.saveDraft(
      snapshot,
      baseVersion: baseVersion,
    );
    if (!mounted) {
      return;
    }
    setState(() {
      _saving = false;
      if (result.ok) {
        _inlineStatus = result.message;
      } else {
        _inlineError = result.message;
      }
    });
    if (result.ok) {
      _serverText = snapshot;
      _baseVersion = ref
          .read(notesControllerProvider(widget.sessionId))
          .effectiveBaseVersion;
      await _cleanupIfNeeded(snapshot);
      ref.read(scheduleControllerProvider.notifier).invalidateWeekCache();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(result.message)));
      }
    } else {
      await _preserveCurrentIfNeeded(snapshot);
    }
  }

  Future<void> _publish() async {
    if (_saving || _publishing) {
      return;
    }
    final snapshot = _text();
    if (snapshot.isBlank) {
      setState(() => _inlineError = '请填写备注');
      return;
    }
    setState(() {
      _publishing = true;
      _inlineError = null;
      _inlineStatus = null;
    });
    final result = await _controller.publish(snapshot);
    if (!mounted) {
      return;
    }
    setState(() {
      _publishing = false;
      if (result.ok) {
        _inlineStatus = result.message;
      } else {
        _inlineError = result.message.contains('都为空')
            ? '请填写备注'
            : result.message;
      }
    });
    if (result.ok) {
      _serverText = snapshot;
      _baseVersion = ref
          .read(notesControllerProvider(widget.sessionId))
          .effectiveBaseVersion;
      await _cleanupIfNeeded(snapshot);
      ref.read(scheduleControllerProvider.notifier).invalidateWeekCache();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(result.message)));
      }
    } else {
      await _preserveCurrentIfNeeded(snapshot);
    }
  }

  Future<void> _cleanupIfNeeded(NoteText snapshot) async {
    final current = _text();
    if (!current.sameAs(snapshot)) {
      await _controller.saveLocal(current);
    }
  }

  Future<void> _preserveCurrentIfNeeded(NoteText snapshot) async {
    final current = _text();
    if (!current.sameAs(snapshot)) {
      await _controller.saveLocal(current);
    }
  }

  Future<void> _adoptServerVersion() async {
    final currentText = _text();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('使用最新内容？'),
        content: const Text('编辑框会换成最新内容；当前输入会另行备份，可随时恢复。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('使用最新内容'),
          ),
        ],
      ),
    );
    if (confirmed != true) {
      return;
    }
    final adopted = await _controller.adoptServerVersion(currentText);
    if (!mounted || adopted == null) {
      return;
    }
    _covered.text = adopted.covered;
    _unfinished.text = adopted.unfinished;
    _nextPlan.text = adopted.nextPlan;
    _serverText = adopted;
    _baseVersion = ref
        .read(notesControllerProvider(widget.sessionId))
        .effectiveBaseVersion;
    setState(() {
      _inlineError = null;
      _inlineStatus = '已使用最新内容，当前输入已备份。';
    });
  }

  void _restoreBackup() {
    final backup = _controller.restoreBackup();
    if (backup == null) {
      return;
    }
    _covered.text = backup.covered;
    _unfinished.text = backup.unfinished;
    _nextPlan.text = backup.nextPlan;
    setState(() {
      _inlineError = null;
      _inlineStatus = '已恢复未保存的草稿，确认后可保存。';
    });
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(notesControllerProvider(widget.sessionId));
    final conflict = state.conflict;
    final server = conflict?.current;
    final published = state.mine?.published;

    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(14, 8, 14, 22),
            children: [
              _ContextStrip(
                courseName: widget.courseName,
                detail: widget.contextLabel,
              ),
              if (!widget.editable) ...[
                const SizedBox(height: 8),
                const _NoticeStrip(
                  icon: Icons.lock_outline,
                  title: '只读',
                  text: '仅任课老师可编辑。',
                ),
              ],
              if (widget.offlineError != null) ...[
                const SizedBox(height: 8),
                const _NoticeStrip(
                  icon: Icons.wifi_off_outlined,
                  title: '暂时无法连接',
                  text: '可继续编辑，保存时会再试。',
                ),
              ],
              if (state.recoveredLocalDraft) ...[
                const SizedBox(height: 8),
                const _NoticeStrip(
                  icon: Icons.restore,
                  title: '已恢复未保存的草稿',
                  text: '保存成功后会清除本机草稿。',
                ),
              ],
              const SizedBox(height: 10),
              Container(
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: appLine),
                ),
                padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(
                      children: [
                        const Text(
                          '本次记录',
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w700,
                            color: appInk,
                          ),
                        ),
                        const Spacer(),
                        if (!widget.editable)
                          const Text(
                            '只读',
                            style: TextStyle(fontSize: 12, color: appMuted),
                          ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    const Divider(height: 1),
                    _NoteField(
                      fieldKey: const Key('coveredField'),
                      controller: _covered,
                      label: '已讲内容',
                      hint: '本节讲完的内容',
                      accent: appTeal,
                      readOnly: !widget.editable,
                      fillColor: _fieldFill,
                    ),
                    const Divider(height: 1),
                    _NoteField(
                      fieldKey: const Key('unfinishedField'),
                      controller: _unfinished,
                      label: '未完成内容',
                      hint: '留到下次的内容',
                      accent: _warm,
                      readOnly: !widget.editable,
                      fillColor: _fieldFill,
                    ),
                    const Divider(height: 1),
                    _NoteField(
                      fieldKey: const Key('nextPlanField'),
                      controller: _nextPlan,
                      label: '下次课计划',
                      hint: '下次课的简短安排',
                      accent: appMint,
                      readOnly: !widget.editable,
                      fillColor: _fieldFill,
                    ),
                    if (_inlineStatus != null) ...[
                      const SizedBox(height: 2),
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Icon(
                              Icons.check_circle_outline,
                              size: 16,
                              color: appMint,
                            ),
                            const SizedBox(width: 6),
                            Expanded(
                              child: Text(
                                _inlineStatus!,
                                key: const Key('notesStatus'),
                                style: const TextStyle(
                                  fontSize: 13,
                                  height: 1.45,
                                  color: appInk,
                                  fontWeight: FontWeight.w500,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                    if (_inlineError != null && conflict == null) ...[
                      const SizedBox(height: 8),
                      _InlineError(message: _inlineError!),
                    ],
                  ],
                ),
              ),
              if (conflict != null) ...[
                const SizedBox(height: 10),
                _ConflictPanel(
                  conflict: conflict,
                  server: server,
                  saving: _saving,
                  onSaveCurrent: () => _save(baseVersion: server?.version),
                  onAdoptServer: _adoptServerVersion,
                  onDismiss: _controller.clearConflict,
                ),
              ],
              if (state.backup != null) ...[
                const SizedBox(height: 10),
                Container(
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: appLine),
                  ),
                  padding: const EdgeInsets.fromLTRB(12, 9, 8, 9),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.inventory_2_outlined,
                        size: 18,
                        color: appMuted,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              '已保存未提交的草稿',
                              style: TextStyle(
                                fontSize: 13.5,
                                fontWeight: FontWeight.w600,
                                color: appInk,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              '保存于 ${monthDayZh(state.backup!.savedAt)} '
                              '${timeZh(state.backup!.savedAt)}，可恢复后继续编辑。',
                              style: const TextStyle(
                                fontSize: 12.5,
                                height: 1.4,
                                color: appMuted,
                              ),
                            ),
                          ],
                        ),
                      ),
                      TextButton(
                        onPressed: _restoreBackup,
                        child: const Text('恢复'),
                      ),
                    ],
                  ),
                ),
              ],
              if (published != null) ...[
                const SizedBox(height: 10),
                _PublishedCard(
                  title: '已共享的备注',
                  note: published,
                  publishedAt: state.mine?.publishedAt,
                ),
              ],
              const SizedBox(height: 10),
              _SharedNotesCard(notes: state.bundle?.others ?? const []),
              if (state.bundle?.previous != null) ...[
                const SizedBox(height: 10),
                _PreviousNotesCard(previous: state.bundle!.previous!),
              ],
            ],
          ),
        ),
        if (widget.editable)
          _ActionBar(
            saving: _saving,
            publishing: _publishing,
            onSave: _save,
            onPublish: _publish,
          ),
      ],
    );
  }
}

class _ContextStrip extends StatelessWidget {
  const _ContextStrip({required this.courseName, required this.detail});

  final String courseName;
  final String? detail;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: appLine),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Container(
            width: 3,
            height: 34,
            decoration: BoxDecoration(
              color: appTeal,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  courseName,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 16,
                    height: 1.35,
                    fontWeight: FontWeight.w600,
                    color: appInk,
                  ),
                ),
                if (detail != null && detail!.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    detail!,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
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
        ],
      ),
    );
  }
}

class _NoteField extends StatelessWidget {
  const _NoteField({
    required this.fieldKey,
    required this.controller,
    required this.label,
    required this.hint,
    required this.accent,
    required this.readOnly,
    required this.fillColor,
  });

  final Key fieldKey;
  final TextEditingController controller;
  final String label;
  final String hint;
  final Color accent;
  final bool readOnly;
  final Color fillColor;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 3,
                height: 14,
                decoration: BoxDecoration(
                  color: accent,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              const SizedBox(width: 8),
              Text(
                label,
                style: const TextStyle(
                  fontSize: 13.5,
                  fontWeight: FontWeight.w600,
                  color: appInk,
                ),
              ),
            ],
          ),
          const SizedBox(height: 7),
          TextField(
            key: fieldKey,
            controller: controller,
            readOnly: readOnly,
            minLines: 2,
            maxLines: null,
            maxLength: 5000,
            keyboardType: TextInputType.multiline,
            textInputAction: TextInputAction.newline,
            style: const TextStyle(fontSize: 14, height: 1.5, color: appInk),
            decoration: InputDecoration(
              hintText: hint,
              hintStyle: const TextStyle(fontSize: 13, color: appMuted),
              counterText: '',
              isDense: true,
              filled: true,
              fillColor: fillColor,
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
                borderSide: BorderSide(color: accent, width: 1.2),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _NoticeStrip extends StatelessWidget {
  const _NoticeStrip({
    required this.icon,
    required this.title,
    required this.text,
  });

  final IconData icon;
  final String title;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
      decoration: BoxDecoration(
        color: const Color(0xFFF3F7FD),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 17, color: appTeal),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: appInk,
                  ),
                ),
                if (text.isNotEmpty)
                  Text(
                    text,
                    style: const TextStyle(
                      fontSize: 12.5,
                      height: 1.4,
                      color: appMuted,
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _InlineError extends StatelessWidget {
  const _InlineError({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF4F2),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        message,
        key: const Key('notesError'),
        style: const TextStyle(
          fontSize: 13,
          height: 1.45,
          color: Color(0xFF9A3B30),
        ),
      ),
    );
  }
}

class _ConflictPanel extends StatelessWidget {
  const _ConflictPanel({
    required this.conflict,
    required this.server,
    required this.saving,
    required this.onSaveCurrent,
    required this.onAdoptServer,
    required this.onDismiss,
  });

  final NoteConflict conflict;
  final MineNote? server;
  final bool saving;
  final VoidCallback onSaveCurrent;
  final VoidCallback onAdoptServer;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const Key('conflictCard'),
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: const Color(0xFFFFFBF4),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFFF1D5A7)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Row(
            children: [
              Icon(Icons.sync_problem, size: 18, color: Color(0xFFAD6A12)),
              SizedBox(width: 7),
              Expanded(
                child: Text(
                  '内容已更新',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w700,
                    color: Color(0xFF7D4C0B),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 5),
          const Text(
            '其他设备已更新这条备注。当前输入已保留，请选择使用哪份内容。',
            style: TextStyle(
              fontSize: 12.5,
              height: 1.45,
              color: Color(0xFF6F4B18),
            ),
          ),
          if (server != null) ...[
            const SizedBox(height: 9),
            const Text(
              '最新内容',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
                color: Color(0xFF6F4B18),
              ),
            ),
            const SizedBox(height: 5),
            _ServerDraft(draft: server!.draft),
            const SizedBox(height: 9),
            FilledButton(
              key: const Key('retryConflictButton'),
              onPressed: saving ? null : onSaveCurrent,
              style: FilledButton.styleFrom(
                minimumSize: const Size.fromHeight(40),
                textStyle: const TextStyle(
                  fontSize: 13.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
              child: const Text('保留当前输入并保存'),
            ),
            const SizedBox(height: 6),
            OutlinedButton(
              key: const Key('adoptServerButton'),
              onPressed: onAdoptServer,
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(40),
                textStyle: const TextStyle(
                  fontSize: 13.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
              child: const Text('使用最新内容'),
            ),
          ] else
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: onDismiss,
                child: const Text('关闭提示'),
              ),
            ),
        ],
      ),
    );
  }
}

class _ServerDraft extends StatelessWidget {
  const _ServerDraft({required this.draft});

  final NoteText draft;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(10, 7, 10, 7),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFFF1D5A7)),
      ),
      child: Column(
        children: [
          _DraftLine(label: '已讲内容', value: draft.covered),
          _DraftLine(label: '未完成', value: draft.unfinished),
          _DraftLine(label: '下次计划', value: draft.nextPlan, last: true),
        ],
      ),
    );
  }
}

class _DraftLine extends StatelessWidget {
  const _DraftLine({
    required this.label,
    required this.value,
    this.last = false,
  });

  final String label;
  final String value;
  final bool last;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: last ? 0 : 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 58,
            child: Text(
              label,
              style: const TextStyle(
                fontSize: 12,
                height: 1.5,
                color: appMuted,
              ),
            ),
          ),
          Expanded(
            child: SelectableText(
              value.trim().isEmpty ? '—' : value,
              style: const TextStyle(fontSize: 13, height: 1.5, color: appInk),
            ),
          ),
        ],
      ),
    );
  }
}

class _PublishedCard extends StatelessWidget {
  const _PublishedCard({
    required this.title,
    required this.note,
    required this.publishedAt,
  });

  final String title;
  final NoteText note;
  final DateTime? publishedAt;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: appLine),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w700,
                    color: appInk,
                  ),
                ),
              ),
              if (publishedAt != null)
                Text(
                  '${monthDayZh(publishedAt!)} ${timeZh(publishedAt!)}',
                  style: const TextStyle(fontSize: 12, color: appMuted),
                ),
            ],
          ),
          const SizedBox(height: 7),
          _DraftLine(label: '已讲内容', value: note.covered),
          _DraftLine(label: '未完成', value: note.unfinished),
          _DraftLine(label: '下次计划', value: note.nextPlan, last: true),
        ],
      ),
    );
  }
}

class _SharedNotesCard extends StatelessWidget {
  const _SharedNotesCard({required this.notes});

  final List<SharedNote> notes;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: appLine),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            '同课老师的备注',
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w700,
              color: appInk,
            ),
          ),
          const SizedBox(height: 6),
          if (notes.isEmpty)
            const Text(
              '暂无同课老师的备注。',
              style: TextStyle(fontSize: 13, color: appMuted),
            )
          else
            for (var index = 0; index < notes.length; index++) ...[
              if (index > 0) const Divider(height: 14),
              _SharedNoteTile(note: notes[index]),
            ],
        ],
      ),
    );
  }
}

class _PreviousNotesCard extends StatelessWidget {
  const _PreviousNotesCard({required this.previous});

  final PreviousNotes previous;

  @override
  Widget build(BuildContext context) {
    final date = previous.date;
    final contextLine = <String>[
      if (previous.courseName.isNotEmpty) previous.courseName,
      if (date != null) monthDayZh(date),
      '${previous.startPeriod}-${previous.endPeriod}节',
    ].join(' · ');
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: appLine),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            '上次共享的备注',
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w700,
              color: appInk,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            contextLine,
            style: const TextStyle(fontSize: 12.5, color: appMuted),
          ),
          const SizedBox(height: 7),
          if (previous.notes.isEmpty)
            const Text(
              '上次课没有共享的备注。',
              style: TextStyle(fontSize: 13, color: appMuted),
            )
          else
            for (var index = 0; index < previous.notes.length; index++) ...[
              if (index > 0) const Divider(height: 14),
              _SharedNoteTile(note: previous.notes[index]),
            ],
        ],
      ),
    );
  }
}

class _SharedNoteTile extends StatelessWidget {
  const _SharedNoteTile({required this.note});

  final SharedNote note;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                note.authorName,
                style: const TextStyle(
                  fontSize: 13.5,
                  fontWeight: FontWeight.w600,
                  color: appInk,
                ),
              ),
            ),
            if (note.publishedAt != null)
              Text(
                '${monthDayZh(note.publishedAt!)} ${timeZh(note.publishedAt!)}',
                style: const TextStyle(fontSize: 12, color: appMuted),
              ),
          ],
        ),
        const SizedBox(height: 4),
        _DraftLine(label: '已讲内容', value: note.published.covered),
        _DraftLine(label: '未完成', value: note.published.unfinished),
        _DraftLine(label: '下次计划', value: note.published.nextPlan, last: true),
      ],
    );
  }
}

class _ActionBar extends StatelessWidget {
  const _ActionBar({
    required this.saving,
    required this.publishing,
    required this.onSave,
    required this.onPublish,
  });

  final bool saving;
  final bool publishing;
  final Future<void> Function() onSave;
  final Future<void> Function() onPublish;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(14, 8, 14, 8),
        decoration: const BoxDecoration(
          color: Colors.white,
          border: Border(top: BorderSide(color: appLine)),
        ),
        child: Row(
          children: [
            Expanded(
              child: OutlinedButton(
                key: const Key('saveDraftButton'),
                onPressed: saving || publishing ? null : onSave,
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size.fromHeight(44),
                  foregroundColor: appInk,
                  side: const BorderSide(color: appLine),
                  textStyle: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
                child: saving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('保存草稿'),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: FilledButton(
                key: const Key('publishButton'),
                onPressed: saving || publishing ? null : onPublish,
                style: FilledButton.styleFrom(
                  minimumSize: const Size.fromHeight(44),
                  textStyle: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
                child: publishing
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Text('共享备注'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
