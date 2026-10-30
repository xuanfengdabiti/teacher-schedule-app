import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:teacher_schedule/core/network/api_exception.dart';
import 'package:teacher_schedule/core/providers.dart';
import 'package:teacher_schedule/features/auth/auth_controller.dart';
import 'package:teacher_schedule/features/notes/note_models.dart';
import 'package:teacher_schedule/features/notes/notes_controller.dart';
import 'package:teacher_schedule/features/notes/notes_page.dart';
import 'package:teacher_schedule/features/notes/notes_repository.dart';

class _FakeNotesRepository implements NotesRepository {
  _FakeNotesRepository({this.fetchError});

  final NoteText serverDraft = const NoteText(covered: '服务器旧稿');
  final int serverVersion = 2;
  final Object? fetchError;
  final List<int> saveBaseVersions = [];
  final List<String> mutationIds = [];

  @override
  Future<NotesBundle> fetchNotes(String sessionId) async {
    if (fetchError != null) {
      throw fetchError!;
    }
    return NotesBundle(
      mine: MineNote(
        id: 'note-1',
        version: serverVersion,
        draft: serverDraft,
        published: null,
        publishedAt: null,
        publishedVersion: null,
        updatedAt: DateTime(2026, 3, 2),
      ),
      others: const [],
      previous: null,
    );
  }

  @override
  Future<MineNote> saveDraft({
    required String sessionId,
    required NoteText text,
    required int baseVersion,
    required String mutationId,
  }) async {
    saveBaseVersions.add(baseVersion);
    mutationIds.add(mutationId);
    throw NoteConflictException(
      message: '备注已在其他设备更新。',
      current: MineNote(
        id: 'note-1',
        version: 2,
        draft: const NoteText(
          covered: '服务器 v2',
          unfinished: '服务器未完成',
          nextPlan: '服务器下次计划',
        ),
        published: null,
        publishedAt: null,
        publishedVersion: null,
        updatedAt: DateTime(2026, 3, 3),
      ),
    );
  }

  @override
  Future<MineNote> publish({
    required String sessionId,
    required int baseVersion,
    required String mutationId,
  }) => throw UnimplementedError();
}

Future<void> _waitForLoaded(ProviderContainer container) async {
  for (var attempt = 0; attempt < 40; attempt++) {
    await Future<void>.delayed(const Duration(milliseconds: 10));
    final state = container.read(notesControllerProvider('session-1'));
    if (!state.isLoading &&
        (state.bundle != null || state.offline || state.loadError != null)) {
      return;
    }
  }
  fail('备注加载超时');
}

ProviderContainer _containerFor(
  SharedPreferences prefs,
  _FakeNotesRepository repository,
) {
  final container = ProviderContainer(
    overrides: [
      sharedPreferencesProvider.overrideWithValue(prefs),
      notesRepositoryProvider.overrideWithValue(repository),
      currentTeacherIdProvider.overrideWithValue('teacher-a'),
    ],
  );
  addTearDown(container.dispose);
  final subscription = container.listen(
    notesControllerProvider('session-1'),
    (previous, next) {},
  );
  addTearDown(subscription.close);
  return container;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('同一课次的 A/B 教师草稿使用不同账号键，互不覆盖', () async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    final store = DraftStore(prefs);

    await store.save(
      'teacher-a',
      'session-1',
      const NoteText(covered: '教师甲的稿'),
      baseVersion: 1,
    );
    await store.save(
      'teacher-b',
      'session-1',
      const NoteText(covered: '教师乙的稿'),
      baseVersion: 3,
    );

    expect(store.read('teacher-a', 'session-1')?.text.covered, '教师甲的稿');
    expect(store.read('teacher-b', 'session-1')?.text.covered, '教师乙的稿');
    expect(
      prefs.getString('teaching_note_draft_teacher-a_session-1'),
      isNotNull,
    );
    expect(
      prefs.getString('teaching_note_draft_teacher-b_session-1'),
      isNotNull,
    );
  });

  test('本地 v1 稿面对服务器 v2 用 v1 提交，409 后两份都保留', () async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    final store = DraftStore(prefs);
    const localText = NoteText(covered: '本地 v1 离线内容');
    await store.save('teacher-a', 'session-1', localText, baseVersion: 1);

    final repository = _FakeNotesRepository();
    final container = _containerFor(prefs, repository);
    await _waitForLoaded(container);

    var state = container.read(notesControllerProvider('session-1'));
    expect(state.initialText?.covered, '本地 v1 离线内容');
    expect(state.draftBaseVersion, 1);
    expect(state.mine?.version, 2);

    final first = await container
        .read(notesControllerProvider('session-1').notifier)
        .saveDraft(localText);
    final second = await container
        .read(notesControllerProvider('session-1').notifier)
        .saveDraft(localText);

    expect(first.conflict, isTrue);
    expect(second.conflict, isTrue);
    expect(repository.saveBaseVersions, [1, 1]);
    expect(repository.mutationIds, hasLength(2));
    expect(repository.mutationIds[0], repository.mutationIds[1]);

    state = container.read(notesControllerProvider('session-1'));
    expect(state.conflict?.current?.version, 2);
    expect(state.mine?.draft.covered, '服务器旧稿');
    expect(state.initialText?.covered, '本地 v1 离线内容');

    final rawDraft = prefs.getString('teaching_note_draft_teacher-a_session-1');
    expect(rawDraft, isNotNull);
    final saved = jsonDecode(rawDraft!);
    expect(saved['text']['covered'], '本地 v1 离线内容');
    expect(saved['base_version'], 1);

    final serverText = await container
        .read(notesControllerProvider('session-1').notifier)
        .adoptServerVersion(localText);
    expect(serverText?.covered, '服务器 v2');
    expect(
      prefs.getString('teaching_note_backup_teacher-a_session-1'),
      isNotNull,
    );
  });

  test('断网时读取账号本地稿并保留原 baseVersion', () async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    final store = DraftStore(prefs);
    const localText = NoteText(covered: '断网时可编辑的本地稿', unfinished: '等联网再保存');
    await store.save('teacher-a', 'session-1', localText, baseVersion: 1);
    final repository = _FakeNotesRepository(
      fetchError: const ApiException(message: '网络暂时不可用。', statusCode: 503),
    );

    final container = _containerFor(prefs, repository);
    await _waitForLoaded(container);
    final state = container.read(notesControllerProvider('session-1'));
    expect(state.offline, isTrue);
    expect(state.loadError, '网络暂时不可用。');
    expect(state.initialText?.covered, '断网时可编辑的本地稿');
    expect(state.draftBaseVersion, 1);
  });

  testWidgets('断网时本机稿进入可编辑备注页而不是错误页', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    final store = DraftStore(prefs);
    await store.save(
      'teacher-a',
      'session-1',
      const NoteText(covered: '断网时可编辑的本地稿', unfinished: '等联网再保存'),
      baseVersion: 1,
    );
    final repository = _FakeNotesRepository(
      fetchError: const ApiException(message: '网络暂时不可用。', statusCode: 503),
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          sharedPreferencesProvider.overrideWithValue(prefs),
          notesRepositoryProvider.overrideWithValue(repository),
          currentTeacherIdProvider.overrideWithValue('teacher-a'),
        ],
        child: const MaterialApp(home: NotesPage(sessionId: 'session-1')),
      ),
    );
    for (var attempt = 0; attempt < 40; attempt++) {
      await tester.pump(const Duration(milliseconds: 50));
      if (find.byKey(const Key('coveredField')).evaluate().isNotEmpty) {
        break;
      }
    }
    expect(find.byKey(const Key('coveredField')), findsOneWidget);
    for (var attempt = 0; attempt < 40; attempt++) {
      await tester.pump(const Duration(milliseconds: 25));
      if (find.text('暂时无法连接').evaluate().isNotEmpty) {
        break;
      }
    }
    expect(find.text('暂时无法连接'), findsOneWidget);
    final field = tester.widget<TextField>(
      find.byKey(const Key('coveredField')),
    );
    expect(field.controller?.text, '断网时可编辑的本地稿');
  });
}
