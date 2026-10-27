import 'package:flutter/foundation.dart';

class SessionExpiryBus extends ChangeNotifier {
  void notifyExpired() => notifyListeners();
}
