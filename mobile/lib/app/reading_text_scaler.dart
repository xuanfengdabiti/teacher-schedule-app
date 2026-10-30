import 'package:flutter/material.dart';

/// Applies the app's reading-size factor on top of the platform text scaler.
///
/// Scaling through [TextScaler.scale] preserves non-linear platform scalers.
class ReadingTextScaler extends TextScaler {
  const ReadingTextScaler({required this.upstream, required this.factor})
    : assert(factor >= 0);

  final TextScaler upstream;
  final double factor;

  @override
  double scale(double fontSize) => upstream.scale(fontSize) * factor;

  @override
  // ignore: deprecated_member_use
  double get textScaleFactor => upstream.textScaleFactor * factor;

  @override
  bool operator ==(Object other) {
    return other is ReadingTextScaler &&
        other.upstream == upstream &&
        other.factor == factor;
  }

  @override
  int get hashCode => Object.hash(upstream, factor);

  @override
  String toString() => 'ReadingTextScaler($upstream × $factor)';
}
