"""Decimal-only helpers for teaching hours.

Hours are stored in ``Decimal(6, 1)`` columns and returned as one-decimal
strings.  A ``float`` is never an accepted input anywhere in this slice because
binary fractions make a 2.5-versus-2.0 allocation comparison unreliable.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

HOUR_QUANTUM = Decimal("0.1")
MAX_HOURS = Decimal("99999.9")


def decimal_hours(value: object, *, field: str = "actual_hours") -> Decimal:
    """Parse a non-negative, one-decimal value without passing through float."""
    if isinstance(value, float):
        raise ValueError(f"{field} 必须使用十进制字符串或 Decimal，不能使用 float。")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, (int, str)):
        try:
            parsed = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise ValueError(f"{field} 不是合法的十进制学时。") from None
    else:
        raise ValueError(f"{field} 不是合法的十进制学时。")

    if not parsed.is_finite():
        raise ValueError(f"{field} 必须是有限十进制数。")
    if parsed < 0:
        raise ValueError(f"{field} 不能为负数。")
    if parsed > MAX_HOURS:
        raise ValueError(f"{field} 超出最大可存储值 {MAX_HOURS}。")
    quantized = parsed.quantize(HOUR_QUANTUM)
    if quantized != parsed:
        raise ValueError(f"{field} 最多保留一位小数。")
    return quantized


def hours_string(value: Decimal | int | str | None) -> str | None:
    """Format any non-negative one-decimal value as ``"2.0"``.

    Aggregates can legitimately exceed the per-row ``99999.9`` storage limit, so
    this formatter deliberately does not apply that single-field cap.
    """
    if value is None:
        return None
    if isinstance(value, float):
        raise ValueError("学时输出不能使用 float。")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, (int, str)):
        try:
            parsed = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise ValueError("学时输出不是合法的十进制数。") from None
    else:
        raise ValueError("学时输出不是合法的十进制数。")
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("学时输出必须是非负有限十进制数。")
    if parsed.quantize(HOUR_QUANTUM) != parsed:
        raise ValueError("学时输出最多保留一位小数。")
    return f"{parsed.quantize(HOUR_QUANTUM):.1f}"


def threshold_hours(value: object) -> Decimal:
    """Parse the configured behind threshold; zero is allowed but not negative."""
    if isinstance(value, float):
        raise ValueError("PLAN_BEHIND_THRESHOLD_HOURS 不能使用 float。")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("PLAN_BEHIND_THRESHOLD_HOURS 不是合法的十进制数。") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("PLAN_BEHIND_THRESHOLD_HOURS 必须是非负有限十进制数。")
    return parsed.quantize(HOUR_QUANTUM)
