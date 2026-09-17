"""Exact, bounded decimal inputs; never silently round business quantities."""
from decimal import Decimal, InvalidOperation


def number(value, scale=4, positive=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) >= Decimal(10) ** (19 - scale):
            raise ValueError("INVALID_NUMBER")
        if result != result.quantize(Decimal(10) ** -scale):
            raise ValueError("INVALID_PRECISION")
        if positive and result <= 0:
            raise ValueError("POSITIVE_NUMBER_REQUIRED")
        return result
    except (InvalidOperation, TypeError):
        raise ValueError("INVALID_NUMBER") from None


def required_text(value, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError("INVALID_TEXT")
    return value.strip()
