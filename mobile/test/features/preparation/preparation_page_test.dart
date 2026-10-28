import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/app/theme.dart';
import 'package:teacher_schedule/core/network/api_exception.dart';
import 'package:teacher_schedule/features/preparation/preparation_models.dart';
import 'package:teacher_schedule/features/preparation/preparation_page.dart';
import 'package:teacher_schedule/features/preparation/preparation_repository.dart';

class _FakePreparationApi implements PreparationApi {
  _FakePreparationApi({required this.bundle, this.aiAvailable = true});

  PreparationBundle bundle;
  bool aiAvailable;
  Object? fetchError;
  Object? generateError;
  Object? saveError;
  Completer<GeneratedPreparation>? pendingGenerate;
  Completer<PreparationDraft>? pendingSave;
  final List<String> generateTopics = [];
  final List<Map<String, Object?>> saveBodies = [];

  @override
  Future<PreparationBundle> fetch(String sessionId) async {
    if (fetchError != null) {
      throw fetchError!;
    }
    return PreparationBundle(
      context: bundle.context,
      draft: bundle.draft,
      aiAvailable: aiAvailable,
    );
  }

  @override
  Future<GeneratedPreparation> generate(String sessionId, String topic) async {
    generateTopics.add(topic);
    if (generateError != null) {
      throw generateError!;
    }
    if (pendingGenerate != null) {
      return pendingGenerate!.future;
    }
    return _generated(topic);
  }

  @override
  Future<PreparationDraft> save({
    required String sessionId,
    required int baseVersion,
    required String topic,
    required String outline,
    required String caseStudy,
    required String discussionQuestions,
    required String preparation,
    String generatedModel = '',
    String? generatedAt,
  }) async {
    saveBodies.add({
      'base_version': baseVersion,
      'topic': topic,
      'outline': outline,
      'case_study': caseStudy,
      'discussion_questions': discussionQuestions,
      'preparation': preparation,
      'generated_model': generatedModel,
    });
    if (saveError != null) {
      throw saveError!;
    }
    if (pendingSave != null) {
      return pendingSave!.future;
    }
    final draft = PreparationDraft(
      id: 'draft-1',
      topic: topic,
      outline: outline,
      caseStudy: caseStudy,
      discussionQuestions: discussionQuestions,
      preparation: preparation,
      version: baseVersion + 1,
      generatedModel: generatedModel,
      generatedAt: generatedAt,
      updatedAt: DateTime(2026, 10, 27, 10),
    );
    bundle = bundle.withDraft(draft);
    return draft;
  }
}

GeneratedPreparation _generated(String topic) => GeneratedPreparation(
  topic: topic,
  outline: '一、引入极限概念\n二、讲解例题',
  caseStudy: '虚构案例：用测量误差说明极限。',
  discussionQuestions: '为什么不能用接近值代替极限？',
  preparation: '准备两道课堂练习题。',
  generatedModel: 'demo-model',
  generatedAt: '2026-10-27T09:00:00+08:00',
);

PreparationContext _context({String previousNote = ''}) => PreparationContext(
  courseName: '高等数学',
  date: DateTime(2026, 10, 27),
  startPeriod: 1,
  endPeriod: 2,
  teachingMinutes: 90,
  previousNote: previousNote,
  planItems: const [PreparationPlanItem(title: '极限与连续', plannedHours: '4.0')],
);

PreparationDraft _draft({
  String topic = '极限的定义',
  String outline = '复习数列极限',
  String caseStudy = '已有的虚构案例',
  String discussion = '已有讨论问题',
  String preparation = '已有课前准备',
  int version = 3,
}) => PreparationDraft(
  id: 'draft-1',
  topic: topic,
  outline: outline,
  caseStudy: caseStudy,
  discussionQuestions: discussion,
  preparation: preparation,
  version: version,
  generatedModel: 'demo-model',
  generatedAt: '2026-10-26T09:00:00+08:00',
  updatedAt: DateTime(2026, 10, 26, 9),
);

PreparationBundle _bundle({
  PreparationDraft? draft,
  String previousNote = '',
}) => PreparationBundle(
  context: _context(previousNote: previousNote),
  draft: draft,
  aiAvailable: true,
);

Future<void> _pumpPage(
  WidgetTester tester,
  PreparationApi api, {
  Size size = const Size(900, 2400),
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      theme: buildAppTheme(),
      home: PreparationPage(api: api, sessionId: 'session-1'),
    ),
  );
  await tester.pumpAndSettle();
}

const _editableFieldKeys = [
  Key('prepTopicField'),
  Key('prepOutlineField'),
  Key('prepCaseField'),
  Key('prepDiscussionField'),
  Key('prepPreparationField'),
];

bool _isReadOnly(WidgetTester tester, Key key) =>
    tester.widget<TextField>(find.byKey(key)).readOnly;

String _fieldText(WidgetTester tester, Key key) =>
    tester.widget<TextField>(find.byKey(key)).controller!.text;

void main() {
  testWidgets('显示服务端上下文与已有草稿，不显示内部版本和模型', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle(draft: _draft()));
    await _pumpPage(tester, api);

    expect(find.text('高等数学'), findsOneWidget);
    expect(find.text('10月27日 周二 · 1-2节 · 本节 90 分钟'), findsOneWidget);
    expect(find.byKey(const Key('prepPreviousNote')), findsNothing);
    expect(find.byKey(const Key('prepPlanItems')), findsOneWidget);
    expect(_fieldText(tester, const Key('prepTopicField')), '极限的定义');
    expect(_fieldText(tester, const Key('prepOutlineField')), '复习数列极限');
    expect(find.text('虚构教学案例'), findsOneWidget);
    expect(find.textContaining('demo-model'), findsNothing);
    expect(find.textContaining('draft-1'), findsNothing);
    expect(find.text('保存备课'), findsOneWidget);
  });

  testWidgets('无草稿时用上次备注短句预填主题，并显示简短摘要', (tester) async {
    final api = _FakePreparationApi(
      bundle: _bundle(previousNote: '下次讲证据链审查，带一个案例，请学生提前阅读材料后课堂讨论。'),
    );
    await _pumpPage(tester, api);

    expect(_fieldText(tester, const Key('prepTopicField')), '下次讲证据链审查');
    expect(find.byKey(const Key('prepPreviousNote')), findsOneWidget);
    expect(
      tester.widget<Text>(find.byKey(const Key('prepPreviousNote'))).maxLines,
      3,
    );
  });

  testWidgets('无备注时主题预填课程名', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle());
    await _pumpPage(tester, api);

    expect(_fieldText(tester, const Key('prepTopicField')), '高等数学');
  });

  testWidgets('生成四段、编辑并保存，重新进入读取保存内容', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle());
    await _pumpPage(tester, api);

    await tester.tap(find.byKey(const Key('prepGenerateButton')));
    await tester.pumpAndSettle();

    expect(api.generateTopics, ['高等数学']);
    expect(_fieldText(tester, const Key('prepOutlineField')), contains('极限概念'));
    expect(_fieldText(tester, const Key('prepCaseField')), contains('测量误差'));
    expect(
      _fieldText(tester, const Key('prepDiscussionField')),
      contains('接近值'),
    );
    expect(
      _fieldText(tester, const Key('prepPreparationField')),
      contains('练习题'),
    );

    await tester.enterText(
      find.byKey(const Key('prepOutlineField')),
      '一、引入概念（教师修改）',
    );
    final saveButton = find.byKey(const Key('prepSaveButton'));
    await tester.ensureVisible(saveButton);
    await tester.tap(saveButton);
    await tester.pumpAndSettle();

    expect(api.saveBodies, hasLength(1));
    final body = api.saveBodies.single;
    expect(body['base_version'], 0);
    expect(body['topic'], '高等数学');
    expect(body['outline'], '一、引入概念（教师修改）');
    expect(body['case_study'], contains('测量误差'));
    expect(body['generated_model'], 'demo-model');
    expect(find.text('备课已保存。'), findsWidgets);

    // Reopen the page: the saved draft is read back from the server bundle.
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    await _pumpPage(tester, api);

    expect(_fieldText(tester, const Key('prepOutlineField')), '一、引入概念（教师修改）');
    expect(_fieldText(tester, const Key('prepCaseField')), contains('测量误差'));
    expect(find.text('高等数学'), findsWidgets);
  });

  testWidgets('已有内容再次生成需确认，取消后保留原内容且不发起生成', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle(draft: _draft()));
    await _pumpPage(tester, api);

    await tester.tap(find.byKey(const Key('prepGenerateButton')));
    await tester.pumpAndSettle();

    expect(find.text('重新生成备课？'), findsOneWidget);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();

    expect(api.generateTopics, isEmpty);
    expect(_fieldText(tester, const Key('prepOutlineField')), '复习数列极限');

    await tester.tap(find.byKey(const Key('prepGenerateButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('prepRegenerateConfirm')));
    await tester.pumpAndSettle();

    expect(api.generateTopics, ['极限的定义']);
    expect(_fieldText(tester, const Key('prepOutlineField')), contains('极限概念'));
  });

  testWidgets('生成中禁用重复操作、锁定输入并显示进度', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle());
    api.pendingGenerate = Completer<GeneratedPreparation>();
    await _pumpPage(tester, api);

    await tester.tap(find.byKey(const Key('prepGenerateButton')));
    await tester.pump();

    expect(find.text('正在生成备课…'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const Key('prepGenerateButton')))
          .onPressed,
      isNull,
    );
    for (final key in _editableFieldKeys) {
      expect(_isReadOnly(tester, key), isTrue, reason: '生成中输入框应只读：$key');
    }
    expect(api.generateTopics, hasLength(1));

    api.pendingGenerate!.complete(_generated('高等数学'));
    await tester.pumpAndSettle();
    expect(find.text('正在生成备课…'), findsNothing);
    expect(_fieldText(tester, const Key('prepOutlineField')), contains('极限概念'));
    for (final key in _editableFieldKeys) {
      expect(_isReadOnly(tester, key), isFalse, reason: '生成后应恢复编辑：$key');
    }
  });

  testWidgets('保存中锁定输入，保存结束后恢复且保留已有内容', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle(draft: _draft()));
    api.pendingSave = Completer<PreparationDraft>();
    await _pumpPage(tester, api);
    await tester.enterText(
      find.byKey(const Key('prepOutlineField')),
      '等待保存前的提纲',
    );

    final saveButton = find.byKey(const Key('prepSaveButton'));
    await tester.ensureVisible(saveButton);
    await tester.tap(saveButton);
    await tester.pump();

    for (final key in _editableFieldKeys) {
      expect(_isReadOnly(tester, key), isTrue, reason: '保存中输入框应只读：$key');
    }
    expect(
      tester
          .widget<FilledButton>(find.byKey(const Key('prepSaveButton')))
          .onPressed,
      isNull,
    );

    api.pendingSave!.complete(
      PreparationDraft(
        id: 'draft-1',
        topic: '极限的定义',
        outline: '等待保存前的提纲',
        caseStudy: '已有的虚构案例',
        discussionQuestions: '已有讨论问题',
        preparation: '已有课前准备',
        version: 4,
        generatedModel: 'demo-model',
        generatedAt: '2026-10-26T09:00:00+08:00',
        updatedAt: DateTime(2026, 10, 27, 10),
      ),
    );
    await tester.pumpAndSettle();

    for (final key in _editableFieldKeys) {
      expect(_isReadOnly(tester, key), isFalse, reason: '保存后应恢复编辑：$key');
    }
    expect(_fieldText(tester, const Key('prepOutlineField')), '等待保存前的提纲');
    expect(find.text('备课已保存。'), findsWidgets);
  });

  testWidgets('真实手机宽度下保存失败，底部保存后可见错误提示且输入保留', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle(draft: _draft()));
    api.saveError = const ApiException(
      message: '备课草稿已在其他设备更新，请重新读取后再保存。',
      code: 'PREPARATION_VERSION_CONFLICT',
      statusCode: 409,
    );
    // Real handset logical size; the long form must scroll.
    await _pumpPage(tester, api, size: const Size(390, 844));

    // Scroll to the bottom fields so the inline error area is off-screen.
    await tester.scrollUntilVisible(
      find.byKey(const Key('prepPreparationField')),
      320,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pump();
    await tester.enterText(
      find.byKey(const Key('prepPreparationField')),
      '手机尺寸下的课前准备',
    );
    await tester.pump();

    final saveButton = find.byKey(const Key('prepSaveButton'));
    await tester.tap(saveButton);
    await tester.pumpAndSettle();

    // The inline error area is far above the viewport, but the snackbar is not.
    expect(find.byType(SnackBar), findsOneWidget);
    expect(
      find.descendant(
        of: find.byType(SnackBar),
        matching: find.text('备课草稿已在其他设备更新，请重新读取后再保存。'),
      ),
      findsOneWidget,
    );
    expect(
      find
          .descendant(
            of: find.byType(SnackBar),
            matching: find.text('备课草稿已在其他设备更新，请重新读取后再保存。'),
          )
          .hitTestable(),
      findsOneWidget,
      reason: '失败提示应在当前视口内可见',
    );
    expect(
      tester.getBottomLeft(find.byType(SnackBar)).dy,
      lessThanOrEqualTo(844),
    );
    expect(_fieldText(tester, const Key('prepPreparationField')), '手机尺寸下的课前准备');
    expect(api.saveBodies, hasLength(1));
    expect(api.saveBodies.single['base_version'], 3);
  });

  testWidgets('生成失败保留主题和已有内容，显示可读提示', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle(draft: _draft()));
    api.generateError = const ApiException(
      message: 'AI 生成超时，请稍后重试。',
      code: 'AI_TIMEOUT',
      statusCode: 504,
    );
    await _pumpPage(tester, api);

    await tester.tap(find.byKey(const Key('prepGenerateButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('prepRegenerateConfirm')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('prepError')), findsOneWidget);
    expect(find.text('AI 生成超时，请稍后重试。'), findsOneWidget);
    expect(_fieldText(tester, const Key('prepTopicField')), '极限的定义');
    expect(_fieldText(tester, const Key('prepOutlineField')), '复习数列极限');
    expect(_fieldText(tester, const Key('prepPreparationField')), '已有课前准备');
  });

  testWidgets('AI 不可用时提示简短，已有草稿仍可编辑保存', (tester) async {
    final api = _FakePreparationApi(
      bundle: _bundle(draft: _draft()),
      aiAvailable: false,
    );
    await _pumpPage(tester, api);

    expect(find.byKey(const Key('prepAiUnavailable')), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const Key('prepGenerateButton')))
          .onPressed,
      isNull,
    );
    await tester.enterText(
      find.byKey(const Key('prepPreparationField')),
      '改成新的课前准备',
    );
    final saveButton = find.byKey(const Key('prepSaveButton'));
    await tester.ensureVisible(saveButton);
    await tester.tap(saveButton);
    await tester.pumpAndSettle();

    expect(api.saveBodies, hasLength(1));
    expect(api.saveBodies.single['base_version'], 3);
    expect(api.saveBodies.single['preparation'], '改成新的课前准备');
  });

  testWidgets('保存冲突保留当前输入并显示可读说明，不改用服务器版本', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle(draft: _draft()));
    api.saveError = const ApiException(
      message: '备课草稿已在其他设备更新，请重新读取后再保存。',
      code: 'PREPARATION_VERSION_CONFLICT',
      statusCode: 409,
      current: {
        'id': 'draft-1',
        'topic': '服务器主题',
        'outline': '服务器提纲',
        'case_study': '',
        'discussion_questions': '',
        'preparation': '',
        'version': 9,
        'generated_model': '',
        'generated_at': null,
        'updated_at': '2026-10-27T10:00:00+08:00',
      },
    );
    await _pumpPage(tester, api);

    await tester.enterText(
      find.byKey(const Key('prepOutlineField')),
      '本机修改的提纲',
    );
    var saveButton = find.byKey(const Key('prepSaveButton'));
    await tester.ensureVisible(saveButton);
    await tester.tap(saveButton);
    await tester.pumpAndSettle();

    expect(api.saveBodies.single['base_version'], 3);
    expect(find.byKey(const Key('prepError')), findsOneWidget);
    expect(find.text('备课草稿已在其他设备更新，请重新读取后再保存。'), findsWidgets);
    expect(_fieldText(tester, const Key('prepOutlineField')), '本机修改的提纲');
    expect(find.text('服务器提纲'), findsNothing);

    // Retry still uses the original base version: no silent overwrite.
    await tester.enterText(
      find.byKey(const Key('prepOutlineField')),
      '再次修改的提纲',
    );
    saveButton = find.byKey(const Key('prepSaveButton'));
    await tester.ensureVisible(saveButton);
    await tester.tap(saveButton);
    await tester.pumpAndSettle();
    expect(api.saveBodies.last['base_version'], 3);
    expect(_fieldText(tester, const Key('prepOutlineField')), '再次修改的提纲');
  });

  testWidgets('主题为空时提示填写且不生成', (tester) async {
    final api = _FakePreparationApi(bundle: _bundle());
    await _pumpPage(tester, api);
    await tester.enterText(find.byKey(const Key('prepTopicField')), '  ');
    await tester.tap(find.byKey(const Key('prepGenerateButton')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('prepInlineError')), findsOneWidget);
    expect(find.text('请先填写本节主题。'), findsOneWidget);
    expect(api.generateTopics, isEmpty);
  });
}
