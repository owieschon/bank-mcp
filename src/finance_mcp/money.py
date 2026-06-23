"""money.py — the single rounding and formatting authority for monetary values.

Money is persisted and aggregated as **integer cents** (exact — no floating-point
drift across sums) and rounded half-up at the boundary via one function here, so the
rounding policy lives in one place instead of scattered float `round()` calls.

- The canonical store (`db.py`) writes the `amount` column as integer cents, so the
  SQL analytics (`queries.sql`) sum exact integers.
- `fmt()` is the one money formatter used for display.

(The deterministic engines still compute in float dollars rounded to the cent for the
digest; the exact-integer guarantee is at the storage/aggregation layer, which is
where unbounded float accumulation would otherwise occur.)
"""
from decimal import Decimal, ROUND_HALF_UP

_CENT = Decimal("0.01")


def to_cents(value):
    """Round a dollar amount (float / int / str / Decimal) to exact integer cents.

    Uses Decimal half-up rounding via str() so the binary float is interpreted at the
    two-decimal value a human would read (e.g. 0.1 + 0.2 -> 30 cents, not 30.0000004).
    """
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_cents(cents):
    """Integer cents -> dollar float (for JSON / display)."""
    return cents / 100.0


def fmt(value):
    """Canonical money string: $1,234.56 / -$1,234.56 (sign before the symbol).

    Accepts dollars as float/int/Decimal. Use `fmt(from_cents(c))` for integer cents.
    """
    d = Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    return f"{sign}${abs(d):,.2f}"
