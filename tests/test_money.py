#!/usr/bin/env python3
"""
test_money.py — the money rounding/formatting authority.

Exercises the exact integer-cents conversion (no float drift), half-up rounding,
and the canonical display format, plus a round-trip with the SQL store: a column of
amounts summed as integer cents equals the cent-exact total, where summing the
floats would drift.
"""
import sqlite3
import unittest

from finance_mcp import money


class ToCentsTest(unittest.TestCase):
    def test_classic_float_drift_is_exact(self):
        # 0.1 + 0.2 == 0.30000000000000004 as floats; cents must be exact.
        self.assertEqual(money.to_cents(0.1) + money.to_cents(0.2), 30)
        self.assertEqual(money.to_cents(0.1 + 0.2), 30)

    def test_half_up_rounding(self):
        self.assertEqual(money.to_cents(1.005), 101)   # half rounds up, not banker's
        self.assertEqual(money.to_cents(2.675), 268)
        self.assertEqual(money.to_cents("19.99"), 1999)
        self.assertEqual(money.to_cents(0), 0)

    def test_round_trip(self):
        for v in (0, 0.01, 19.99, 1234.56, 800.0):
            self.assertAlmostEqual(money.from_cents(money.to_cents(v)), v, places=2)


class FmtTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(money.fmt(1234.5), "$1,234.50")
        self.assertEqual(money.fmt(-1234.56), "-$1,234.56")
        self.assertEqual(money.fmt(0), "$0.00")
        self.assertEqual(money.fmt(money.from_cents(9900)), "$99.00")


class SummationExactnessTest(unittest.TestCase):
    def test_summing_cents_beats_summing_floats(self):
        # 1000 charges of $0.10. Float sum drifts off $100.00; integer cents is exact.
        amounts = [0.10] * 1000
        cents_total = sum(money.to_cents(a) for a in amounts)
        self.assertEqual(cents_total, 10000)                 # exactly $100.00
        self.assertEqual(money.fmt(money.from_cents(cents_total)), "$100.00")

    def test_sqlite_sum_of_cents_is_exact(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (amount INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(money.to_cents(0.10),)] * 1000)
        (total,) = conn.execute("SELECT SUM(amount) FROM t").fetchone()
        conn.close()
        self.assertEqual(total, 10000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
