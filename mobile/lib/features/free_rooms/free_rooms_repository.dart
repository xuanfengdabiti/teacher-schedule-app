import '../../core/json.dart';
import '../../core/network/api_client.dart';
import 'free_rooms_models.dart';

class FreeRoomsRepository {
  FreeRoomsRepository(this._api);

  final ApiClient _api;

  Future<FreeRoomPageData> search(FreeRoomQuery query, {int page = 1}) async {
    final response = await _api.get(
      'classrooms/availability/',
      query: query.toQuery(page: page),
    );
    return FreeRoomPageData.fromJson(jsonMap(response.data));
  }
}
