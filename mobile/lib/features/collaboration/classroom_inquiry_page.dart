import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import 'classroom_search_page.dart';
import 'inquiry_compose_page.dart';
import 'inquiry_list_page.dart';

class ClassroomInquiryPage extends StatefulWidget {
  const ClassroomInquiryPage({super.key, required this.api});

  final ApiClient api;

  @override
  State<ClassroomInquiryPage> createState() => _ClassroomInquiryPageState();
}

class _ClassroomInquiryPageState extends State<ClassroomInquiryPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 2, vsync: this);
  final GlobalKey<InquiryListPageState> _lostListKey =
      GlobalKey<InquiryListPageState>();
  bool _sentAfterInquiry = false;

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  void _openLostMessages() {
    setState(() => _sentAfterInquiry = true);
    _tabs.animateTo(1);
    final listState = _lostListKey.currentState;
    if (listState != null) {
      listState.refreshSent();
    }
  }

  Future<void> _startInquiry() async {
    final sent = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (context) => InquiryComposePage(api: widget.api),
      ),
    );
    if (sent == true && mounted) {
      _openLostMessages();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('教室与失物'),
        bottom: TabBar(
          controller: _tabs,
          tabs: const [
            Tab(text: '教室记录', icon: Icon(Icons.meeting_room_outlined)),
            Tab(text: '失物消息', icon: Icon(Icons.inventory_2_outlined)),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: [
          ClassroomSearchPage(
            api: widget.api,
            embedded: true,
            onInquirySent: _openLostMessages,
          ),
          InquiryListPage(
            key: _lostListKey,
            api: widget.api,
            embedded: true,
            initialBox: _sentAfterInquiry ? 'sent' : 'received',
            refreshToken: _sentAfterInquiry ? 1 : 0,
          ),
        ],
      ),
      bottomNavigationBar: SafeArea(
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
              key: const Key('hubStartInquiryButton'),
              onPressed: _startInquiry,
              icon: const Icon(Icons.add_alert_outlined),
              label: const Text('发起失物询问'),
            ),
          ),
        ),
      ),
    );
  }
}
