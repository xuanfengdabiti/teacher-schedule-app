import 'package:flutter_riverpod/flutter_riverpod.dart';

/// One-shot notice shown on the login page after account actions.
class AccountAccessNotice extends Notifier<String?> {
  @override
  String? build() => null;

  void set(String? value) {
    state = value;
  }

  void clear() {
    state = null;
  }
}

final accountAccessNoticeProvider =
    NotifierProvider<AccountAccessNotice, String?>(AccountAccessNotice.new);
