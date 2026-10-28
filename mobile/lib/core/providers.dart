import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../features/auth/auth_repository.dart';
import '../features/notes/note_models.dart';
import '../features/notes/notes_repository.dart';
import '../features/schedule/schedule_repository.dart';
import 'config/api_config.dart';
import 'network/api_client.dart';
import 'network/session_expiry_bus.dart';
import 'storage/import_record_store.dart';
import 'storage/settings_repository.dart';
import 'storage/token_store.dart';

final sharedPreferencesProvider = Provider<SharedPreferences>(
  (ref) => throw StateError('SharedPreferences 未初始化。'),
);

final tokenStoreProvider = Provider<TokenStore>((ref) => SecureTokenStore());

final settingsRepositoryProvider = Provider<SettingsRepository>(
  (ref) => SettingsRepository(ref.watch(sharedPreferencesProvider)),
);

final draftStoreProvider = Provider<DraftStore>(
  (ref) => DraftStore(ref.watch(sharedPreferencesProvider)),
);

final importRecordStoreProvider = Provider<ImportRecordStore>(
  (ref) => ImportRecordStore(ref.watch(sharedPreferencesProvider)),
);

final sessionExpiryProvider = Provider<SessionExpiryBus>((ref) {
  final bus = SessionExpiryBus();
  ref.onDispose(bus.dispose);
  return bus;
});

final httpClientProvider = Provider<Dio>((ref) {
  final dio = Dio();
  ref.onDispose(() => dio.close());
  return dio;
});

final apiClientProvider = Provider<ApiClient>((ref) {
  final settings = ref.watch(settingsRepositoryProvider);
  final bus = ref.watch(sessionExpiryProvider);
  return ApiClient(
    baseUrl: ApiConfig.normalizeBaseUrl(settings.apiBaseUrl),
    tokenStore: ref.watch(tokenStoreProvider),
    dio: ref.watch(httpClientProvider),
    onSessionExpired: bus.notifyExpired,
  );
});

final authRepositoryProvider = Provider<AuthRepository>(
  (ref) => AuthRepository(ref.watch(apiClientProvider)),
);

final scheduleRepositoryProvider = Provider<ScheduleRepository>(
  (ref) => ScheduleRepository(ref.watch(apiClientProvider)),
);

final notesRepositoryProvider = Provider<NotesRepository>(
  (ref) => NotesRepository(ref.watch(apiClientProvider)),
);
