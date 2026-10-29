import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/features/auth/account_access_repository.dart';

void main() {
  group('邮件链接解析', () {
    test('完整验证链接、裸验证码与 key= 形式', () {
      expect(
        parseVerifyLink('teacherschedule://verify-email?key=abc-123').key,
        'abc-123',
      );
      expect(parseVerifyLink(' abc-123 ').key, 'abc-123');
      expect(parseVerifyLink('key=abc%2D123').key, 'abc-123');
      expect(parseVerifyLink('').key, isNull);
    });

    test('损坏的百分号不抛异常，返回空值', () {
      expect(parseVerifyLink('bad%zz').key, isNull);
      expect(parseResetLink('bad%zz').uid, isNull);
      expect(parseResetLink('bad%zz').token, isNull);
      expect(parseResetLink('').uid, isNull);
    });

    test('完整重置链接与查询串', () {
      final values = parseResetLink(
        'teacherschedule://reset-password?uid=MQ&token=abc-def',
      );
      expect(values.uid, 'MQ');
      expect(values.token, 'abc-def');
      final pair = parseResetLink('uid=MQ&token=abc');
      expect(pair.uid, 'MQ');
      expect(pair.token, 'abc');
    });
  });
}
