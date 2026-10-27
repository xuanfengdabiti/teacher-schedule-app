import 'package:flutter/material.dart';

import '../../core/network/api_client.dart';
import 'classroom_search_page.dart';
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
    );
  }
}
