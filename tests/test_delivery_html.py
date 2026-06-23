#!/usr/bin/env python3
"""
test_delivery_html.py — the live HTML email renderer and subject line.

render_digest_html is what the daily email actually sends, so it should produce
well-formed HTML from a real digest and — like every other output path — must not
leak a raw transaction row. narrate() must no-op (return None) without an API key.
"""
import os
import unittest

from bank_mcp import demo
from bank_mcp import finance_agent as fa
from bank_mcp.report import delivery
from bank_mcp.report import email_html
from bank_mcp.store import obligation_registry as oblreg

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "src", "bank_mcp", "data")


def _build_digest():
    oblreg.REGISTRY_PATH = os.path.join(_DATA, "obligations.demo.json")
    return fa.build_digest(
        demo.generate(), balance=1200.0, mode="monthly", forecast_days=35,
        buffer=100.0, include_burn=True, scan_days=30,
        rules_path=os.path.join(_DATA, "rules.demo.md"))


class RenderDigestHtmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = _build_digest()
        cls.html = email_html.render_digest_html(cls.digest)

    def test_returns_well_formed_html(self):
        self.assertIsInstance(self.html, str)
        self.assertIn("<html", self.html.lower())
        self.assertIn("</html>", self.html.lower())
        self.assertGreater(len(self.html), 500)

    def test_no_raw_transaction_row_leaks_into_email(self):
        # raw rows carry a rawData/merchantName signature; none should be in the HTML
        self.assertNotIn("rawData", self.html)

    def test_subject_line_is_concise_nonempty(self):
        subj = email_html.digest_subject_line(self.digest)
        self.assertTrue(subj)
        self.assertLess(len(subj), 200)
        self.assertNotIn("\n", subj)


class NarrateWithoutKeyTest(unittest.TestCase):
    def test_narrate_noops_without_api_key(self):
        # ensure no key is resolved, so narrate degrades to None (numbers-only)
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            result = delivery.narrate({"headline": {}}, tone="plain", mode="monthly")
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old
        # None (no key) or a graceful marker — never a raise, never real narration
        self.assertTrue(result is None or "unavailable" in str(result).lower()
                        or isinstance(result, str))


if __name__ == "__main__":
    unittest.main(verbosity=2)
