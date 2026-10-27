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

class NotesPage extends ConsumerWidget {
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
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(notesControllerProvider(sessionId));
    return Scaffold(
      appBar: AppBar(
        title: Text(title?.isNotEmpty == true ? '备注 · $title' : '教学备注'),
      ),
      body: state.isLoading
          ? const LoadingView(message: '正在加载…')
          : state.loadError != null && !state.offline
          ? ErrorView(
              message: state.loadError!,
              onRetry: () => ref
                  .read(notesControllerProvider(sessionId).notifier)
                  .retryLoad(),
            )
          : _NoteEditor(
              sessionId: sessionId,
              accountId: state.accountId,
              initialText: state.initialText ?? const NoteText(),
              initialBaseVersion: state.effectiveBaseVersion,
              serverText: state.mine?.draft ?? const NoteText(),
              offlineError: state.offline ? state.loadError : null,
              editable: editable,
            ),
    );
  }
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
  });

  final String sessionId;
  final String accountId;
  final NoteText initialText;
  final int initialBaseVersion;
  final NoteText serverText;
  final String? offlineError;
  final bool editable;

  @override
  ConsumerState<_NoteEditor> createState() => _NoteEditorState();
}

class _NoteEditorState extends ConsumerState<_NoteEditor> {
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
            padding: const EdgeInsets.fromLTRB(18, 6, 18, 24),
            children: [
              if (!widget.editable)
                const _NoticeCard(
                  icon: Icons.lock_outline,
                  title: '只读',
                  text: '仅任课老师可编辑。',
                ),
              if (widget.offlineError != null)
                const _NoticeCard(
                  icon: Icons.wifi_off_outlined,
                  title: '暂时无法连接',
                  text: '可继续编辑草稿，保存时会再试。',
                ),
              if (state.recoveredLocalDraft)
                const _NoticeCard(
                  icon: Icons.restore,
                  title: '已恢复未保存的草稿',
                  text: '下面是上次未保存成功的文字，保存成功后会清除本机草稿。',
                ),
              const SizedBox(height: 12),
              Card(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(18, 18, 18, 18),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Row(
                        children: [
                          Text(
                            '本次记录',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                        ],
                      ),
                      const SizedBox(height: 16),
                      _NoteField(
                        fieldKey: const Key('coveredField'),
                        controller: _covered,
                        label: '已讲内容',
                        readOnly: !widget.editable,
                      ),
                      const SizedBox(height: 14),
                      _NoteField(
                        fieldKey: const Key('unfinishedField'),
                        controller: _unfinished,
                        label: '未完成内容',
                        readOnly: !widget.editable,
                      ),
                      const SizedBox(height: 14),
                      _NoteField(
                        fieldKey: const Key('nextPlanField'),
                        controller: _nextPlan,
                        label: '下次课计划',
                        readOnly: !widget.editable,
                      ),
                      if (_inlineStatus != null) ...[
                        const SizedBox(height: 14),
                        Text(
                          _inlineStatus!,
                          key: const Key('notesStatus'),
                          style: const TextStyle(
                            fontSize: 15,
                            color: appTeal,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                      if (_inlineError != null && conflict == null) ...[
                        const SizedBox(height: 14),
                        _InlineError(message: _inlineError!),
                      ],
                    ],
                  ),
                ),
              ),
              if (conflict != null) ...[
                const SizedBox(height: 14),
                Card(
                  key: const Key('conflictCard'),
                  color: const Color(0xFFFFFAF1),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(18),
                    side: const BorderSide(color: Color(0xFFF1D5A7)),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.all(18),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Row(
                          children: [
                            const Icon(
                              Icons.sync_problem,
                              color: Color(0xFFAD6A12),
                            ),
                            const SizedBox(width: 8),
                            const Expanded(
                              child: Text(
                                '内容已更新',
                                style: TextStyle(
                                  fontSize: 17,
                                  fontWeight: FontWeight.w700,
                                  color: Color(0xFF7D4C0B),
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 10),
                        const Text(
                          '这条备注已在其他设备更新，请选择使用哪份内容。',
                          style: TextStyle(
                            fontSize: 15,
                            height: 1.5,
                            color: Color(0xFF6F4B18),
                          ),
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          '当前输入已保留，可稍后恢复。',
                          style: TextStyle(
                            fontSize: 14,
                            height: 1.5,
                            color: Color(0xFF6F4B18),
                          ),
                        ),
                        if (server != null) ...[
                          const SizedBox(height: 16),
                          const Text(
                            '最新内容',
                            style: TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          const SizedBox(height: 8),
                          _ServerDraft(draft: server.draft),
                          const SizedBox(height: 16),
                          FilledButton(
                            key: const Key('retryConflictButton'),
                            onPressed: _saving
                                ? null
                                : () => _save(baseVersion: server.version),
                            child: const Text('保留当前输入并保存'),
                          ),
                          const SizedBox(height: 8),
                          OutlinedButton(
                            key: const Key('adoptServerButton'),
                            onPressed: _adoptServerVersion,
                            child: const Text('使用最新内容'),
                          ),
                        ] else
                          TextButton(
                            onPressed: _controller.clearConflict,
                            child: const Text('关闭提示'),
                          ),
                      ],
                    ),
                  ),
                ),
              ],
              if (state.backup != null) ...[
                const SizedBox(height: 12),
                Card(
                  child: ListTile(
                    leading: const Icon(Icons.inventory_2_outlined),
                    title: const Text('已保存未提交的草稿'),
                    subtitle: Text(
                      '保存于 ${monthDayZh(state.backup!.savedAt)} '
                      '${timeZh(state.backup!.savedAt)}，可恢复后继续编辑。',
                    ),
                    trailing: TextButton(
                      onPressed: _restoreBackup,
                      child: const Text('恢复'),
                    ),
                  ),
                ),
              ],
              if (published != null) ...[
                const SizedBox(height: 14),
                _PublishedCard(
                  title: '已共享的备注',
                  note: published,
                  publishedAt: state.mine?.publishedAt,
                ),
              ],
              const SizedBox(height: 14),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(18),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '同课老师的备注',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 10),
                      if (state.bundle?.others.isEmpty ?? true)
                        const Text(
                          '暂无同课老师的备注。',
                          style: TextStyle(fontSize: 15, color: appMuted),
                        )
                      else
                        for (final note in state.bundle!.others)
                          _SharedNoteTile(note: note),
                    ],
                  ),
                ),
              ),
              if (state.bundle?.previous != null) ...[
                const SizedBox(height: 14),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(18),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '上次共享的备注',
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '${state.bundle!.previous!.courseName} · '
                          '${state.bundle!.previous!.date == null ? '' : monthDayZh(state.bundle!.previous!.date!)} '
                          '${state.bundle!.previous!.startPeriod}-${state.bundle!.previous!.endPeriod}节',
                          style: const TextStyle(fontSize: 14, color: appMuted),
                        ),
                        const SizedBox(height: 10),
                        if (state.bundle!.previous!.notes.isEmpty)
                          const Text(
                            '上次课没有共享的备注。',
                            style: TextStyle(fontSize: 15, color: appMuted),
                          )
                        else
                          for (final note in state.bundle!.previous!.notes)
                            _SharedNoteTile(note: note),
                      ],
                    ),
                  ),
                ),
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

class _NoteField extends StatelessWidget {
  const _NoteField({
    required this.fieldKey,
    required this.controller,
    required this.label,
    required this.readOnly,
  });

  final Key fieldKey;
  final TextEditingController controller;
  final String label;
  final bool readOnly;

  @override
  Widget build(BuildContext context) {
    return TextField(
      key: fieldKey,
      controller: controller,
      readOnly: readOnly,
      minLines: 1,
      maxLines: 6,
      maxLength: 5000,
      textInputAction: TextInputAction.newline,
      style: const TextStyle(fontSize: 16, height: 1.5),
      decoration: InputDecoration(
        labelText: label,
        alignLabelWithHint: true,
        counterText: '',
      ),
    );
  }
}

class _NoticeCard extends StatelessWidget {
  const _NoticeCard({
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
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFFEAF5F2),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 21, color: appTeal),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: appInk,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  text,
                  style: const TextStyle(
                    fontSize: 14,
                    height: 1.5,
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
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF1EF),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        message,
        key: const Key('notesError'),
        style: const TextStyle(fontSize: 15, color: Color(0xFF9A3B30)),
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
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFFF1D5A7)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
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
      padding: EdgeInsets.only(bottom: last ? 0 : 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w600,
              color: appMuted,
            ),
          ),
          const SizedBox(height: 2),
          SelectableText(
            value.trim().isEmpty ? '（空）' : value,
            style: const TextStyle(fontSize: 15, height: 1.45),
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
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(title, style: Theme.of(context).textTheme.titleMedium),
                const Spacer(),
                if (publishedAt != null)
                  Text(
                    '${monthDayZh(publishedAt!)} ${timeZh(publishedAt!)}',
                    style: const TextStyle(fontSize: 13, color: appMuted),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            _DraftLine(label: '已讲内容', value: note.covered),
            _DraftLine(label: '未完成', value: note.unfinished),
            _DraftLine(label: '下次计划', value: note.nextPlan, last: true),
          ],
        ),
      ),
    );
  }
}

class _SharedNoteTile extends StatelessWidget {
  const _SharedNoteTile({required this.note});

  final SharedNote note;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: const Color(0xFFF7FAF9),
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
                  note.authorName,
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              if (note.publishedAt != null)
                Text(
                  '${monthDayZh(note.publishedAt!)} ${timeZh(note.publishedAt!)}',
                  style: const TextStyle(fontSize: 12.5, color: appMuted),
                ),
            ],
          ),
          const SizedBox(height: 7),
          _DraftLine(label: '已讲内容', value: note.published.covered),
          _DraftLine(label: '未完成', value: note.published.unfinished),
          _DraftLine(label: '下次计划', value: note.published.nextPlan, last: true),
        ],
      ),
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
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
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
                child: saving
                    ? const SizedBox(
                        width: 20,
                        height: 20,
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
                child: publishing
                    ? const SizedBox(
                        width: 20,
                        height: 20,
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
