import 'dart:convert';
import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

class NoteText {
  const NoteText({this.covered = '', this.unfinished = '', this.nextPlan = ''});

  final String covered;
  final String unfinished;
  final String nextPlan;

  bool get isBlank =>
      covered.trim().isEmpty &&
      unfinished.trim().isEmpty &&
      nextPlan.trim().isEmpty;

  bool sameAs(NoteText other) =>
      covered == other.covered &&
      unfinished == other.unfinished &&
      nextPlan == other.nextPlan;

  Map<String, dynamic> toJson() => {
    'covered': covered,
    'unfinished': unfinished,
    'next_plan': nextPlan,
  };

  factory NoteText.fromJson(Object? value) {
    if (value is! Map) {
      return const NoteText();
    }
    return NoteText(
      covered: value['covered']?.toString() ?? '',
      unfinished: value['unfinished']?.toString() ?? '',
      nextPlan: value['next_plan']?.toString() ?? '',
    );
  }
}

class MineNote {
  const MineNote({
    required this.id,
    required this.version,
    required this.draft,
    required this.published,
    required this.publishedAt,
    required this.publishedVersion,
    required this.updatedAt,
  });

  final String id;
  final int version;
  final NoteText draft;
  final NoteText? published;
  final DateTime? publishedAt;
  final int? publishedVersion;
  final DateTime? updatedAt;

  factory MineNote.fromJson(Map<String, dynamic> json) {
    return MineNote(
      id: json['id']?.toString() ?? '',
      version: _int(json['version']),
      draft: NoteText.fromJson(json['draft']),
      published: json['published'] == null
          ? null
          : NoteText.fromJson(json['published']),
      publishedAt: _date(json['published_at']),
      publishedVersion: _nullableInt(json['published_version']),
      updatedAt: _date(json['updated_at']),
    );
  }
}

class SharedNote {
  const SharedNote({
    required this.authorName,
    required this.published,
    required this.publishedAt,
  });

  final String authorName;
  final NoteText published;
  final DateTime? publishedAt;

  factory SharedNote.fromJson(Map<String, dynamic> json) {
    final author = json['author'];
    return SharedNote(
      authorName: author is Map ? author['name']?.toString() ?? '同课教师' : '同课教师',
      published: NoteText.fromJson(json['published']),
      publishedAt: _date(json['published_at']),
    );
  }
}

class PreviousNotes {
  const PreviousNotes({
    required this.sessionId,
    required this.date,
    required this.startPeriod,
    required this.endPeriod,
    required this.courseName,
    required this.notes,
  });

  final String sessionId;
  final DateTime? date;
  final int startPeriod;
  final int endPeriod;
  final String courseName;
  final List<SharedNote> notes;

  factory PreviousNotes.fromJson(Map<String, dynamic> json) {
    final session = json['session'];
    final notes = json['notes'];
    return PreviousNotes(
      sessionId: session is Map ? session['id']?.toString() ?? '' : '',
      date: session is Map ? _date(session['date']) : null,
      startPeriod: session is Map ? _int(session['start_period']) : 0,
      endPeriod: session is Map ? _int(session['end_period']) : 0,
      courseName: session is Map
          ? session['course_name']?.toString() ?? ''
          : '',
      notes: notes is List
          ? notes
                .whereType<Map>()
                .map(
                  (item) =>
                      SharedNote.fromJson(Map<String, dynamic>.from(item)),
                )
                .toList(growable: false)
          : const [],
    );
  }
}

class NotesBundle {
  const NotesBundle({
    required this.mine,
    required this.others,
    required this.previous,
  });

  final MineNote? mine;
  final List<SharedNote> others;
  final PreviousNotes? previous;

  factory NotesBundle.fromJson(Map<String, dynamic> json) {
    final mine = json['mine'];
    final others = json['others'];
    final previous = json['previous'];
    return NotesBundle(
      mine: mine is Map
          ? MineNote.fromJson(Map<String, dynamic>.from(mine))
          : null,
      others: others is List
          ? others
                .whereType<Map>()
                .map(
                  (item) =>
                      SharedNote.fromJson(Map<String, dynamic>.from(item)),
                )
                .toList(growable: false)
          : const [],
      previous: previous is Map
          ? PreviousNotes.fromJson(Map<String, dynamic>.from(previous))
          : null,
    );
  }

  NotesBundle withMine(MineNote? updated) =>
      NotesBundle(mine: updated, others: others, previous: previous);
}

class LocalDraft {
  const LocalDraft({
    required this.text,
    required this.baseVersion,
    required this.savedAt,
  });

  final NoteText text;
  final int baseVersion;
  final DateTime savedAt;
}

class DraftStore {
  DraftStore(this._prefs);

  final SharedPreferences _prefs;

  String _key(String accountId, String sessionId) =>
      'teaching_note_draft_${accountId}_$sessionId';
  String _backupKey(String accountId, String sessionId) =>
      'teaching_note_backup_${accountId}_$sessionId';

  LocalDraft? read(String accountId, String sessionId) =>
      _read(_key(accountId, sessionId));

  LocalDraft? readBackup(String accountId, String sessionId) =>
      _read(_backupKey(accountId, sessionId));

  Future<void> save(
    String accountId,
    String sessionId,
    NoteText text, {
    required int baseVersion,
  }) async {
    await _write(
      _key(accountId, sessionId),
      LocalDraft(text: text, baseVersion: baseVersion, savedAt: DateTime.now()),
    );
  }

  Future<void> saveBackup(
    String accountId,
    String sessionId,
    NoteText text,
    int baseVersion,
  ) => _write(
    _backupKey(accountId, sessionId),
    LocalDraft(text: text, baseVersion: baseVersion, savedAt: DateTime.now()),
  );

  Future<void> clearIfMatches(
    String accountId,
    String sessionId,
    NoteText text,
  ) async {
    final current = read(accountId, sessionId);
    if (current != null && current.text.sameAs(text)) {
      await _prefs.remove(_key(accountId, sessionId));
    }
  }

  Future<void> clear(String accountId, String sessionId) =>
      _prefs.remove(_key(accountId, sessionId));

  Future<void> clearBackup(String accountId, String sessionId) =>
      _prefs.remove(_backupKey(accountId, sessionId));

  LocalDraft? _read(String key) {
    final raw = _prefs.getString(key);
    if (raw == null || raw.isEmpty) {
      return null;
    }
    try {
      final json = jsonDecode(raw);
      if (json is! Map) {
        return null;
      }
      return LocalDraft(
        text: NoteText.fromJson(json['text']),
        baseVersion: _int(json['base_version']),
        savedAt: _date(json['saved_at']) ?? DateTime.now(),
      );
    } on FormatException {
      return null;
    }
  }

  Future<void> _write(String key, LocalDraft draft) async {
    await _prefs.setString(
      key,
      jsonEncode({
        'text': draft.text.toJson(),
        'base_version': draft.baseVersion,
        'saved_at': draft.savedAt.toIso8601String(),
      }),
    );
  }
}

String newMutationId() {
  final random = Random.secure();
  final buffer = StringBuffer();
  for (var index = 0; index < 16; index++) {
    buffer.write(random.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return buffer.toString();
}

int _int(Object? value) {
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  return int.tryParse(value?.toString() ?? '') ?? 0;
}

int? _nullableInt(Object? value) => value == null ? null : _int(value);

DateTime? _date(Object? value) {
  final text = value?.toString();
  return text == null ? null : DateTime.tryParse(text);
}
