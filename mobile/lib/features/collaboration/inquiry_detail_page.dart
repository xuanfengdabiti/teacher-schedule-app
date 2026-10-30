import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import '../../shared/status_views.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';

class InquiryDetailPage extends StatefulWidget {
  const InquiryDetailPage({
    super.key,
    required this.api,
    required this.inquiryId,
  });

  final ApiClient api;
  final String inquiryId;

  @override
  State<InquiryDetailPage> createState() => _InquiryDetailPageState();
}

class _InquiryDetailPageState extends State<InquiryDetailPage> {
  final TextEditingController _replyBody = TextEditingController();
  InquiryItem? _item;
  Object? _error;
  String? _readWarning;
  bool _loading = true;
  bool _busy = false;
  String _replyResult = 'not_seen';

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _replyBody.dispose();
    super.dispose();
  }

  Future<void> _load({bool keepData = false}) async {
    setState(() {
      _loading = true;
      if (!keepData) {
        _error = null;
      }
    });
    try {
      final response = await widget.api.get('inquiries/${widget.inquiryId}/');
      var item = InquiryItem.fromJson(mapOf(response.data));
      String? warning;
      if (!item.isInitiator) {
        try {
          final readResponse = await widget.api.post(
            'inquiries/${widget.inquiryId}/read/',
          );
          item = InquiryItem.fromJson(mapOf(readResponse.data));
        } catch (error) {
          warning = ApiException.fromUnknown(error).message;
        }
      }
      if (!mounted) {
        return;
      }
      setState(() {
        _item = item;
        _readWarning = warning;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _loading = false;
        if (_item == null) {
          _error = error;
        }
      });
      if (_item != null) {
        showCollabError(context, error);
      }
    }
  }

  Future<void> _sendReply() async {
    final body = _replyBody.text.trim();
    if (_replyResult == 'other' && body.isEmpty) {
      showCollabError(context, ApiException(message: '选择“其他说明”时必须填写文字。'));
      return;
    }
    setState(() => _busy = true);
    try {
      final response = await widget.api.post(
        'inquiries/${widget.inquiryId}/replies/',
        data: {'result': _replyResult, if (body.isNotEmpty) 'body': body},
      );
      final item = InquiryItem.fromJson(mapOf(response.data));
      if (!mounted) {
        return;
      }
      setState(() {
        _item = item;
        _busy = false;
        _replyBody.clear();
      });
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('回复已追加到失物记录。')));
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() => _busy = false);
      showCollabError(context, error);
    }
  }

  Future<void> _closeInquiry() async {
    final noteController = TextEditingController();
    final note = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('结案'),
        content: TextField(
          controller: noteController,
          maxLines: 3,
          maxLength: 1000,
          decoration: const InputDecoration(
            labelText: '结案说明（可选）',
            hintText: '例如：物品已找回',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, noteController.text.trim()),
            child: const Text('确认结案'),
          ),
        ],
      ),
    );
    noteController.dispose();
    if (note == null || !mounted) {
      return;
    }
    setState(() => _busy = true);
    try {
      final response = await widget.api.post(
        'inquiries/${widget.inquiryId}/close/',
        data: {'close_note': note},
      );
      final item = InquiryItem.fromJson(mapOf(response.data));
      if (!mounted) {
        return;
      }
      setState(() {
        _item = item;
        _busy = false;
      });
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('失物询问已结案，仍保留历史记录。')));
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() => _busy = false);
      showCollabError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final item = _item;
    return Scaffold(
      appBar: AppBar(title: const Text('失物详情')),
      body: item == null
          ? _loading
                ? const LoadingView(message: '正在读取失物详情…')
                : CollabErrorView(error: _error!, onRetry: () => _load())
          : Column(
              children: [
                if (_loading || _busy) const CollabInlineProgress(),
                Expanded(
                  child: RefreshIndicator(
                    onRefresh: () => _load(keepData: true),
                    child: ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                      children: [
                        _buildHeader(item),
                        if (_readWarning != null) ...[
                          const SizedBox(height: 8),
                          CollabNotice(
                            text: '没能记录已读，可稍后刷新。',
                            icon: Icons.mark_email_unread_outlined,
                          ),
                        ],
                        const CollabSectionTitle(title: '接收人'),
                        for (final recipient in item.recipients)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: _RecipientRow(recipient: recipient),
                          ),
                        CollabSectionTitle(
                          title: '回复记录',
                          trailing: CollabStatusPill(
                            label: '${item.replies.length} 条',
                            color: appMuted,
                          ),
                        ),
                        if (item.replies.isEmpty)
                          const Padding(
                            padding: EdgeInsets.symmetric(vertical: 22),
                            child: EmptyView(
                              title: '还没有回复',
                              detail: '接收人回复后会显示在这里。',
                              icon: Icons.forum_outlined,
                            ),
                          )
                        else
                          for (final reply in item.replies)
                            Padding(
                              key: ValueKey('inquiry-reply-${reply.id}'),
                              padding: const EdgeInsets.only(bottom: 8),
                              child: _ReplyCard(reply: reply),
                            ),
                        if (item.isClosed) ...[
                          const SizedBox(height: 8),
                          CollabNotice(
                            text:
                                '已结案${item.closedAt == null ? '' : '于 ${dateTimeLabel(item.closedAt)}'}'
                                '${item.closeNote.isEmpty ? '。' : '：${item.closeNote}'}',
                            icon: Icons.task_alt_outlined,
                          ),
                        ] else if (item.isInitiator) ...[
                          const SizedBox(height: 12),
                          SizedBox(
                            width: double.infinity,
                            child: OutlinedButton.icon(
                              onPressed: _busy ? null : _closeInquiry,
                              icon: const Icon(Icons.task_alt_outlined),
                              label: const Text('结案'),
                            ),
                          ),
                        ] else ...[
                          CollabSectionTitle(title: '回复'),
                          _buildReplyForm(),
                        ],
                      ],
                    ),
                  ),
                ),
              ],
            ),
    );
  }

  Widget _buildHeader(InquiryItem item) {
    return CollabSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  item.itemDescription,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
              const SizedBox(width: 8),
              CollabStatusPill(
                label: item.statusLabel,
                color: item.isClosed ? appMuted : const Color(0xFFB7791F),
              ),
            ],
          ),
          const SizedBox(height: 10),
          CollabDetailRow(
            label: '教室',
            value:
                '${item.classroom.classroomCode} · ${item.classroom.location}',
          ),
          CollabDetailRow(
            label: '时间',
            value: rangeLabel(item.fromAt, item.toAt),
          ),
          CollabDetailRow(label: '发起人', value: item.initiator.name),
          const SizedBox(height: 8),
          CollabNotice(
            text: item.notice.isEmpty ? '排定课程记录，不代表实际到场或物品去向。' : item.notice,
          ),
        ],
      ),
    );
  }

  Widget _buildReplyForm() {
    return Column(
      children: [
        DropdownButtonFormField<String>(
          initialValue: _replyResult,
          decoration: const InputDecoration(labelText: '回复结果'),
          items: const [
            DropdownMenuItem(value: 'not_seen', child: Text('未见到')),
            DropdownMenuItem(value: 'in_custody', child: Text('已保管')),
            DropdownMenuItem(value: 'other', child: Text('其他说明')),
          ],
          onChanged: _busy
              ? null
              : (value) {
                  if (value != null) {
                    setState(() => _replyResult = value);
                  }
                },
        ),
        const SizedBox(height: 8),
        TextField(
          key: const ValueKey('inquiry-reply-body'),
          controller: _replyBody,
          minLines: 2,
          maxLines: 4,
          maxLength: 1000,
          decoration: InputDecoration(
            labelText: _replyResult == 'other' ? '说明（必填）' : '补充说明（可选）',
            hintText: '例如：在讲台旁看到一把黑色雨伞',
          ),
        ),
        SizedBox(
          width: double.infinity,
          child: FilledButton.icon(
            key: const ValueKey('inquiry-reply-submit'),
            onPressed: _busy ? null : _sendReply,
            icon: const Icon(Icons.reply_outlined),
            label: const Text('回复'),
          ),
        ),
      ],
    );
  }
}

class _RecipientRow extends StatelessWidget {
  const _RecipientRow({required this.recipient});

  final InquiryRecipient recipient;

  @override
  Widget build(BuildContext context) {
    return CollabSectionCard(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Row(
        children: [
          const CircleAvatar(
            radius: 17,
            backgroundColor: Color(0xFFDDEFEA),
            child: Icon(Icons.person_outline, size: 19, color: appTeal),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(recipient.teacher.name),
                Text(
                  recipient.teacher.department.isEmpty
                      ? '相关课次 ${recipient.relatedSessionIds.length} 条'
                      : '${recipient.teacher.department} · 相关课次 ${recipient.relatedSessionIds.length} 条',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          CollabStatusPill(
            label: recipient.hasReplied ? '已回复' : '未回复',
            color: recipient.hasReplied ? appMint : appMuted,
          ),
        ],
      ),
    );
  }
}

class _ReplyCard extends StatelessWidget {
  const _ReplyCard({required this.reply});

  final InquiryReply reply;

  @override
  Widget build(BuildContext context) {
    return CollabSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  reply.author.name,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              CollabStatusPill(
                label: reply.resultLabel,
                color: reply.result == 'in_custody' ? appMint : appMuted,
              ),
            ],
          ),
          if (reply.body.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(reply.body),
          ],
          const SizedBox(height: 6),
          Text(
            dateTimeLabel(reply.createdAt),
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
