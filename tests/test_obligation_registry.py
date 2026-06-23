#!/usr/bin/env python3
"""
test_obligation_registry.py — the forward-plan registry that feeds the forecast.

Covers loading, the monthly obligation floor, and the stream adapter — including the
edge that matters: an amortizing obligation past its end_date drops out of the plan.
"""
import datetime as dt
import os
import unittest

from bank_mcp.store import obligation_registry as oblreg

_EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples", "obligations.example.json")


class LoadTest(unittest.TestCase):
    def test_load_example_registry(self):
        reg = oblreg.load_registry(_EXAMPLE)
        self.assertIn("obligations", reg)
        names = {o["name"] for o in reg["obligations"]}
        self.assertIn("Car Loan", names)        # the amortizing one

    def test_missing_file_returns_empty(self):
        self.assertEqual(oblreg.load_registry("/nonexistent/x.json"), {})


class FloorTest(unittest.TestCase):
    def test_monthly_floor_sums_active_obligations(self):
        reg = oblreg.load_registry(_EXAMPLE)
        # as_of before the car loan's end_date: it counts toward the floor
        floor = oblreg.obligation_floor_monthly(reg, dt.date(2026, 1, 15))
        self.assertGreater(floor, 0)

    def test_empty_registry_floor_is_zero(self):
        self.assertEqual(oblreg.obligation_floor_monthly({}, dt.date(2026, 1, 1)), 0.0)


class StreamsTest(unittest.TestCase):
    def test_amortizing_drops_after_end_date(self):
        reg = oblreg.load_registry(_EXAMPLE)
        txns = []
        before = {s["merchant"] for s in oblreg.registry_to_streams(reg, txns, dt.date(2026, 6, 1))}
        after = {s["merchant"] for s in oblreg.registry_to_streams(reg, txns, dt.date(2027, 6, 1))}
        # Car Loan ends 2026-11-30: present before, gone in 2027
        self.assertIn("Car Loan", before)
        self.assertNotIn("Car Loan", after)

    def test_streams_have_projectable_shape(self):
        reg = oblreg.load_registry(_EXAMPLE)
        for s in oblreg.registry_to_streams(reg, [], dt.date(2026, 1, 15)):
            self.assertIn("merchant", s)
            self.assertIn("cadence", s)
            self.assertIn("avg_amount", s)
            self.assertIn("last_date", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
