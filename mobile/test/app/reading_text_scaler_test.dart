import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/app/reading_text_scaler.dart';

class _NonLinearScaler extends TextScaler {
  const _NonLinearScaler();

  @override
  double scale(double fontSize) {
    return fontSize <= 10 ? fontSize * 1.4 : 12 + (fontSize - 10) * 1.2;
  }

  @override
  // ignore: deprecated_member_use
  double get textScaleFactor => 1.3;
}

void main() {
  test('阅读基准乘在上游非线性缩放之后', () {
    const combined = ReadingTextScaler(
      upstream: _NonLinearScaler(),
      factor: 1.15,
    );

    expect(combined.scale(8), closeTo(8 * 1.4 * 1.15, 0.0001));
    expect(combined.scale(20), closeTo((12 + 10 * 1.2) * 1.15, 0.0001));
    // ignore: deprecated_member_use
    expect(combined.textScaleFactor, closeTo(1.3 * 1.15, 0.0001));
  });
}
