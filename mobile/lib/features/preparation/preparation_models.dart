import '../../core/json.dart';

class PreparationPlanItem {
  const PreparationPlanItem({required this.title, required this.plannedHours});

  final String title;
  final String plannedHours;

  factory PreparationPlanItem.fromJson(Map<String, dynamic> json) {
    return PreparationPlanItem(
      title: jsonString(json['title']) ?? '',
      plannedHours: jsonString(json['planned_hours']) ?? '',
    );
  }
}

class PreparationContext {
  const PreparationContext({
    required this.courseName,
    required this.date,
    required this.startPeriod,
    required this.endPeriod,
    required this.teachingMinutes,
    required this.previousNote,
    required this.planItems,
  });

  final String courseName;
  final DateTime? date;
  final int startPeriod;
  final int endPeriod;
  final int teachingMinutes;
  final String previousNote;
  final List<PreparationPlanItem> planItems;

  String get periodsLabel =>
      startPeriod == endPeriod ? '$startPeriod节' : '$startPeriod-$endPeriod节';

  factory PreparationContext.fromJson(Map<String, dynamic> json) {
    final items = json['plan_items'];
    return PreparationContext(
      courseName: jsonString(json['course_name']) ?? '未命名课程',
      date: jsonDate(json['date']),
      startPeriod: jsonInt(json['start_period']) ?? 0,
      endPeriod: jsonInt(json['end_period']) ?? 0,
      teachingMinutes: jsonInt(json['teaching_minutes']) ?? 0,
      previousNote: jsonString(json['previous_note']) ?? '',
      planItems: items is List
          ? items
                .whereType<Map>()
                .map((item) => PreparationPlanItem.fromJson(jsonMap(item)))
                .toList(growable: false)
          : const [],
    );
  }
}

class PreparationDraft {
  const PreparationDraft({
    required this.id,
    required this.topic,
    required this.outline,
    required this.caseStudy,
    required this.discussionQuestions,
    required this.preparation,
    required this.version,
    required this.generatedModel,
    required this.generatedAt,
    required this.updatedAt,
  });

  final String id;
  final String topic;
  final String outline;
  final String caseStudy;
  final String discussionQuestions;
  final String preparation;
  final int version;

  /// Internal generation metadata is only carried back on save.
  final String generatedModel;
  final String? generatedAt;
  final DateTime? updatedAt;

  factory PreparationDraft.fromJson(Map<String, dynamic> json) {
    return PreparationDraft(
      id: jsonString(json['id']) ?? '',
      topic: jsonString(json['topic']) ?? '',
      outline: jsonString(json['outline']) ?? '',
      caseStudy: jsonString(json['case_study']) ?? '',
      discussionQuestions: jsonString(json['discussion_questions']) ?? '',
      preparation: jsonString(json['preparation']) ?? '',
      version: jsonInt(json['version']) ?? 0,
      generatedModel: jsonString(json['generated_model']) ?? '',
      generatedAt: jsonString(json['generated_at']),
      updatedAt: jsonDateTime(json['updated_at']),
    );
  }
}

class PreparationBundle {
  const PreparationBundle({
    required this.context,
    required this.draft,
    required this.aiAvailable,
  });

  final PreparationContext context;
  final PreparationDraft? draft;
  final bool aiAvailable;

  factory PreparationBundle.fromJson(Map<String, dynamic> json) {
    final context = json['context'];
    final draft = json['draft'];
    return PreparationBundle(
      context: PreparationContext.fromJson(jsonMap(context)),
      draft: draft is Map ? PreparationDraft.fromJson(jsonMap(draft)) : null,
      aiAvailable: json['ai_available'] == true,
    );
  }

  PreparationBundle withDraft(PreparationDraft draft) {
    return PreparationBundle(
      context: context,
      draft: draft,
      aiAvailable: aiAvailable,
    );
  }
}

class GeneratedPreparation {
  const GeneratedPreparation({
    required this.topic,
    required this.outline,
    required this.caseStudy,
    required this.discussionQuestions,
    required this.preparation,
    required this.generatedModel,
    required this.generatedAt,
  });

  final String topic;
  final String outline;
  final String caseStudy;
  final String discussionQuestions;
  final String preparation;
  final String generatedModel;
  final String? generatedAt;

  factory GeneratedPreparation.fromJson(Map<String, dynamic> json) {
    return GeneratedPreparation(
      topic: jsonString(json['topic']) ?? '',
      outline: jsonString(json['outline']) ?? '',
      caseStudy: jsonString(json['case_study']) ?? '',
      discussionQuestions: jsonString(json['discussion_questions']) ?? '',
      preparation: jsonString(json['preparation']) ?? '',
      generatedModel: jsonString(json['generated_model']) ?? '',
      generatedAt: jsonString(json['generated_at']),
    );
  }
}

/// Suggests a short lesson topic: the previous note is often a long summary,
/// so take its first clause instead of copying the whole text.
String defaultPreparationTopic(PreparationContext context) {
  final summary = context.previousNote.replaceAll(RegExp(r'\s+'), ' ').trim();
  if (summary.isNotEmpty) {
    for (final part in summary.split(RegExp(r'[。；;，,\n]'))) {
      final clause = part.trim();
      if (clause.isEmpty) {
        continue;
      }
      return clause.length > 24 ? clause.substring(0, 24) : clause;
    }
  }
  return context.courseName;
}
