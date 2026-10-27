import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'teaching_controllers.dart';
import 'teaching_models.dart';
import 'teaching_repository.dart';
import 'teaching_widgets.dart';

class HandoffComposePage extends StatefulWidget {
  const HandoffComposePage({
    super.key,
    required this.api,
    required this.sourceSessionId,
  });

  final ApiClient api;
  final String sourceSessionId;

  @override
  State<HandoffComposePage> createState() => _HandoffComposePageState();
}

class _HandoffComposePageState extends State<HandoffComposePage> {
  late final HandoffComposeController _controller;
  late final TextEditingController _content;
  bool _contentInitialized = false;
  String? _inlineError;

  @override
  void initState() {
    super.initState();
    _controller = HandoffComposeController(
      api: TeachingRepository(widget.api),
      sourceSessionId: widget.sourceSessionId,
    );
    _content = TextEditingController();
    _controller.addListener(_onChanged);
    _load();
  }

  Future<void> _load() async {
    await _controller.load();
    if (!mounted) {
      return;
    }
    final prefill = _controller.prefill;
    if (prefill != null && !_contentInitialized && _content.text.isEmpty) {
      _content.text = prefill.content;
      _contentInitialized = true;
    }
    setState(() {});
  }

  void _onChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  @override
  void dispose() {
    _controller
      ..removeListener(_onChanged)
      ..dispose();
    _content.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (_controller.submitting) {
      return;
    }
    setState(() => _inlineError = null);
    final result = await _controller.submit(_content.text);
    if (!mounted) {
      return;
    }
    if (!result.ok) {
      setState(() => _inlineError = result.message);
      return;
    }
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('交接已发送'),
        content: Text(result.message),
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
  }

  @override
  Widget build(BuildContext context) {
    final prefill = _controller.prefill;
    return Scaffold(
      appBar: AppBar(
        title: const Text('发起教学交接'),
        actions: [
          IconButton(
            key: const Key('handoffComposeRefreshButton'),
            tooltip: '重新读取预填',
            onPressed: _controller.loading ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _buildBody(prefill),
      bottomNavigationBar: prefill == null
          ? null
          : SafeArea(
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
                      child: Text(
                        '已选 ${_controller.selectedRecipientIds.length} 位接收人',
                        style: const TextStyle(fontSize: 14, color: appMuted),
                      ),
                    ),
                    FilledButton.icon(
                      key: const Key('handoffSendButton'),
                      onPressed:
                          _controller.submitting ||
                              _controller.selectedRecipientIds.isEmpty
                          ? null
                          : _send,
                      icon: _controller.submitting
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.white,
                              ),
                            )
                          : const Icon(Icons.send_outlined),
                      label: const Text('发送交接'),
                    ),
                  ],
                ),
              ),
            ),
    );
  }

  Widget _buildBody(HandoffPrefill? prefill) {
    if (prefill == null && _controller.loading) {
      return const LoadingView(message: '正在读取交接预填…');
    }
    if (prefill == null && _controller.error != null) {
      return ErrorView(message: _controller.error!, onRetry: _load);
    }
    if (prefill == null) {
      return const SizedBox.shrink();
    }
    final suggested = prefill.suggestedTargetSession;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      children: [
        if (_controller.loading) const LinearProgressIndicator(minHeight: 2),
        if (_controller.error != null) ...[
          TeachingNotice(
            text: _controller.error!,
            tone: TeachingNoticeTone.error,
            icon: Icons.cloud_off_outlined,
          ),
          const SizedBox(height: 10),
        ],
        if (_inlineError != null) ...[
          TeachingNotice(
            text: _inlineError!,
            tone: TeachingNoticeTone.error,
            icon: Icons.error_outline,
          ),
          const SizedBox(height: 10),
        ],
        const TeachingNotice(
          text: '交接会立即发送给明确勾选的教师。只打开预填不会创建交接；发送成功后不能修改正文，请确认内容。',
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('接收人', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 2),
                const Text(
                  '只能选择同一课程当前任课教师；未勾选不会发送。',
                  style: TextStyle(fontSize: 13.5, color: appMuted),
                ),
                const SizedBox(height: 6),
                if (prefill.recipients.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 16),
                    child: Text(
                      '该课程暂无可选的其他任课教师。',
                      style: TextStyle(fontSize: 15, color: appMuted),
                    ),
                  )
                else
                  ListView.builder(
                    key: const Key('handoffRecipientList'),
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    itemCount: prefill.recipients.length,
                    itemBuilder: (context, index) {
                      final recipient = prefill.recipients[index];
                      final selected = _controller.selectedRecipientIds
                          .contains(recipient.id);
                      return CheckboxListTile(
                        key: ValueKey('recipient-${recipient.id}'),
                        value: selected,
                        contentPadding: EdgeInsets.zero,
                        controlAffinity: ListTileControlAffinity.leading,
                        title: Text(recipient.name),
                        subtitle: recipient.isSuggested
                            ? const Text(
                                '建议接收人：来源课次之后的第一节授课教师',
                                style: TextStyle(
                                  fontSize: 12.5,
                                  color: appTeal,
                                ),
                              )
                            : null,
                        onChanged: _controller.submitting
                            ? null
                            : (_) => _controller.toggleRecipient(recipient.id),
                      );
                    },
                  ),
              ],
            ),
          ),
        ),
        if (suggested != null) ...[
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(8, 8, 8, 8),
              child: SwitchListTile(
                key: const Key('handoffTargetSwitch'),
                value: _controller.includeSuggestedTarget,
                title: const Text('关联建议的下一课次'),
                subtitle: Text(
                  teachingSessionText(suggested),
                  style: const TextStyle(fontSize: 13.5, color: appMuted),
                ),
                onChanged: _controller.submitting
                    ? null
                    : _controller.setIncludeTarget,
              ),
            ),
          ),
        ],
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      '交接内容',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const Spacer(),
                    if (prefill.noteId != null)
                      TeachingStatusPill(
                        label:
                            '已预填发布备注'
                            '${prefill.notePublishedVersion == null ? '' : ' v${prefill.notePublishedVersion}'}',
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                TextField(
                  key: const Key('handoffContentField'),
                  controller: _content,
                  minLines: 7,
                  maxLines: 14,
                  maxLength: 5000,
                  textInputAction: TextInputAction.newline,
                  onChanged: (_) => setState(() {}),
                  decoration: const InputDecoration(
                    hintText: '填写未完成内容、下次计划或需要接任教师注意的事项',
                    alignLabelWithHint: true,
                    counterText: '',
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  '${_content.text.characters.length} / 5000',
                  style: const TextStyle(fontSize: 12.5, color: appMuted),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
