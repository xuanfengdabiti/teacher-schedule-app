import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../shared/status_views.dart';
import '../teaching/teaching_widgets.dart';
import 'preparation_controller.dart';
import 'preparation_models.dart';
import 'preparation_repository.dart';

class PreparationPage extends StatefulWidget {
  const PreparationPage({
    super.key,
    required this.api,
    required this.sessionId,
  });

  final PreparationApi api;
  final String sessionId;

  @override
  State<PreparationPage> createState() => _PreparationPageState();
}

class _PreparationPageState extends State<PreparationPage> {
  late final PreparationController _controller;
  final TextEditingController _topic = TextEditingController();
  final TextEditingController _outline = TextEditingController();
  final TextEditingController _caseStudy = TextEditingController();
  final TextEditingController _discussion = TextEditingController();
  final TextEditingController _preparation = TextEditingController();
  bool _prefilled = false;
  String? _inlineError;

  @override
  void initState() {
    super.initState();
    _controller = PreparationController(
      api: widget.api,
      sessionId: widget.sessionId,
    );
    _controller.addListener(_onChanged);
    _load();
  }

  @override
  void dispose() {
    _controller
      ..removeListener(_onChanged)
      ..dispose();
    _topic.dispose();
    _outline.dispose();
    _caseStudy.dispose();
    _discussion.dispose();
    _preparation.dispose();
    super.dispose();
  }

  void _onChanged() {
    if (!mounted) {
      return;
    }
    setState(() {
      final bundle = _controller.bundle;
      if (!_prefilled && bundle != null) {
        _prefilled = true;
        final draft = bundle.draft;
        _topic.text = draft == null
            ? defaultPreparationTopic(bundle.context)
            : draft.topic;
        _outline.text = draft?.outline ?? '';
        _caseStudy.text = draft?.caseStudy ?? '';
        _discussion.text = draft?.discussionQuestions ?? '';
        _preparation.text = draft?.preparation ?? '';
      }
    });
  }

  Future<void> _load() async {
    await _controller.load();
  }

  bool get _hasSections =>
      _outline.text.trim().isNotEmpty ||
      _caseStudy.text.trim().isNotEmpty ||
      _discussion.text.trim().isNotEmpty ||
      _preparation.text.trim().isNotEmpty;

  Future<void> _generate() async {
    if (_controller.generating || _controller.saving) {
      return;
    }
    final topic = _topic.text.trim();
    if (topic.isEmpty) {
      setState(() => _inlineError = '请先填写本节主题。');
      return;
    }
    if (_hasSections) {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('重新生成备课？'),
          content: const Text('四段内容会换成新生成的内容。取消可继续编辑当前内容。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('取消'),
            ),
            FilledButton(
              key: const Key('prepRegenerateConfirm'),
              onPressed: () => Navigator.pop(context, true),
              child: const Text('重新生成'),
            ),
          ],
        ),
      );
      if (confirmed != true || !mounted) {
        return;
      }
    }
    setState(() => _inlineError = null);
    final ok = await _controller.generate(topic);
    if (!mounted || !ok) {
      return;
    }
    final generated = _controller.generated;
    if (generated == null) {
      return;
    }
    setState(() {
      if (generated.topic.trim().isNotEmpty) {
        _topic.text = generated.topic;
      }
      _outline.text = generated.outline;
      _caseStudy.text = generated.caseStudy;
      _discussion.text = generated.discussionQuestions;
      _preparation.text = generated.preparation;
    });
  }

  Future<void> _save() async {
    if (_controller.generating || _controller.saving) {
      return;
    }
    final topic = _topic.text.trim();
    if (topic.isEmpty) {
      setState(() => _inlineError = '请填写本节主题。');
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() => _inlineError = null);
    final result = await _controller.save(
      topic: topic,
      outline: _outline.text.trim(),
      caseStudy: _caseStudy.text.trim(),
      discussionQuestions: _discussion.text.trim(),
      preparation: _preparation.text.trim(),
    );
    if (!mounted) {
      return;
    }
    // Show the outcome next to the fixed save bar as well, so a failure while
    // editing near the bottom of a long page stays visible.
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(result.message)));
  }

  @override
  Widget build(BuildContext context) {
    final bundle = _controller.bundle;
    return Scaffold(
      appBar: AppBar(title: const Text('AI 备课')),
      body: bundle == null
          ? _controller.loading
                ? const LoadingView(message: '正在加载备课…')
                : ErrorView(
                    message: _controller.error ?? '暂时无法加载备课，请稍后重试。',
                    onRetry: _load,
                  )
          : _buildForm(context, bundle),
      bottomNavigationBar: bundle == null ? null : _buildSaveBar(),
    );
  }

  Widget _buildSaveBar() {
    final busy = _controller.generating || _controller.saving;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        decoration: const BoxDecoration(
          color: Colors.white,
          border: Border(top: BorderSide(color: appLine)),
        ),
        child: SizedBox(
          width: double.infinity,
          child: FilledButton.icon(
            key: const Key('prepSaveButton'),
            onPressed: busy ? null : _save,
            icon: _controller.saving
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Icon(Icons.save_outlined),
            label: const Text('保存备课'),
          ),
        ),
      ),
    );
  }

  Widget _buildForm(BuildContext context, PreparationBundle bundle) {
    final busy = _controller.generating || _controller.saving;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      children: [
        _ContextCard(details: bundle.context),
        const SizedBox(height: 12),
        if (!bundle.aiAvailable)
          const Padding(
            padding: EdgeInsets.only(bottom: 12),
            child: TeachingNotice(
              key: Key('prepAiUnavailable'),
              tone: TeachingNoticeTone.warning,
              icon: Icons.auto_awesome_outlined,
              text: 'AI 备课暂时不可用，已有备课仍可查看、编辑和保存。',
            ),
          ),
        if (_controller.actionError != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: TeachingNotice(
              key: const Key('prepError'),
              tone: TeachingNoticeTone.error,
              icon: Icons.error_outline,
              text: _controller.actionError!,
            ),
          ),
        if (_inlineError != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: TeachingNotice(
              key: const Key('prepInlineError'),
              tone: TeachingNoticeTone.error,
              icon: Icons.error_outline,
              text: _inlineError!,
            ),
          ),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('本节主题', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 4),
                const Text(
                  '按主题生成备课，生成后可逐段修改。',
                  style: TextStyle(fontSize: 14, color: appMuted),
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const Key('prepTopicField'),
                  controller: _topic,
                  readOnly: busy,
                  maxLength: 200,
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(
                    hintText: '例如：极限的定义与计算',
                    counterText: '',
                  ),
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  key: const Key('prepGenerateButton'),
                  onPressed: busy || !bundle.aiAvailable ? null : _generate,
                  icon: _controller.generating
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Icon(Icons.auto_awesome),
                  label: const Text('生成备课'),
                ),
                if (_controller.generating) ...[
                  const SizedBox(height: 10),
                  const Text(
                    '正在生成备课…',
                    key: Key('prepGenerating'),
                    style: TextStyle(fontSize: 14, color: appMuted),
                  ),
                ],
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        _SectionField(
          fieldKey: const Key('prepOutlineField'),
          controller: _outline,
          label: '授课提纲',
          hint: '按授课顺序列出要点',
          readOnly: busy,
        ),
        const SizedBox(height: 12),
        _SectionField(
          fieldKey: const Key('prepCaseField'),
          controller: _caseStudy,
          label: '课堂案例',
          hint: '用于讲解本节内容的教学案例',
          helper: '虚构教学案例',
          readOnly: busy,
        ),
        const SizedBox(height: 12),
        _SectionField(
          fieldKey: const Key('prepDiscussionField'),
          controller: _discussion,
          label: '讨论问题',
          hint: '课堂上可以提出的问题',
          readOnly: busy,
        ),
        const SizedBox(height: 12),
        _SectionField(
          fieldKey: const Key('prepPreparationField'),
          controller: _preparation,
          label: '课前准备',
          hint: '教师课前需要准备的材料和事项',
          readOnly: busy,
        ),
        if (_controller.notice != null) ...[
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Text(
              _controller.notice!,
              key: const Key('prepStatus'),
              style: const TextStyle(
                fontSize: 14,
                color: appTeal,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ],
    );
  }
}

class _ContextCard extends StatelessWidget {
  const _ContextCard({required this.details});

  final PreparationContext details;

  @override
  Widget build(BuildContext context) {
    final date = details.date;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              details.courseName,
              style: const TextStyle(
                fontSize: 19,
                fontWeight: FontWeight.w700,
                color: appInk,
                height: 1.35,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              date == null
                  ? '${details.periodsLabel} · 本节 ${details.teachingMinutes} 分钟'
                  : '${monthDayZh(date)} ${weekdayZh(date.weekday)} · '
                        '${details.periodsLabel} · 本节 ${details.teachingMinutes} 分钟',
              key: const Key('prepContextLine'),
              style: const TextStyle(
                fontSize: 15,
                color: appMuted,
                height: 1.45,
              ),
            ),
            if (details.previousNote.trim().isNotEmpty) ...[
              const SizedBox(height: 12),
              const Text(
                '上次备注',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: appMuted,
                ),
              ),
              const SizedBox(height: 3),
              Text(
                details.previousNote,
                key: const Key('prepPreviousNote'),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 15, height: 1.45),
              ),
            ],
            if (details.planItems.isNotEmpty) ...[
              const SizedBox(height: 12),
              const Text(
                '计划章节',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: appMuted,
                ),
              ),
              const SizedBox(height: 3),
              Text(
                details.planItems
                    .map((item) => '${item.title}（${item.plannedHours} 学时）')
                    .join('、'),
                key: const Key('prepPlanItems'),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 15, height: 1.45),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _SectionField extends StatelessWidget {
  const _SectionField({
    required this.fieldKey,
    required this.controller,
    required this.label,
    required this.hint,
    this.helper,
    this.readOnly = false,
  });

  final Key fieldKey;
  final TextEditingController controller;
  final String label;
  final String hint;
  final String? helper;
  final bool readOnly;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              label,
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w700,
                color: appInk,
              ),
            ),
            if (helper != null) ...[
              const SizedBox(height: 4),
              Row(
                children: [
                  const Icon(
                    Icons.menu_book_outlined,
                    size: 16,
                    color: appMuted,
                  ),
                  const SizedBox(width: 5),
                  Text(
                    helper!,
                    key: const Key('prepCaseHelper'),
                    style: const TextStyle(fontSize: 13, color: appMuted),
                  ),
                ],
              ),
            ],
            const SizedBox(height: 8),
            TextField(
              key: fieldKey,
              controller: controller,
              readOnly: readOnly,
              minLines: 3,
              maxLines: 10,
              maxLength: 5000,
              keyboardType: TextInputType.multiline,
              textInputAction: TextInputAction.newline,
              style: const TextStyle(fontSize: 16, height: 1.5),
              decoration: InputDecoration(
                hintText: hint,
                counterText: '',
                alignLabelWithHint: true,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
