#!/usr/bin/env python3
"""
test_safehttp.py — the SSRF guard and the transient-retry policy.

Covers: scheme/host validation (the security contract) and that retries fire on
transient failures (timeout, 5xx) but NOT on a real 4xx, with backoff.
"""
import unittest
import urllib.error
from unittest import mock

from bank_mcp.ingest import safehttp


class ValidationTest(unittest.TestCase):
    def test_rejects_non_https(self):
        with self.assertRaises(ValueError):
            safehttp.fetch("http://example.com/")

    def test_allows_localhost_only_when_opted_in(self):
        with self.assertRaises(ValueError):
            safehttp.fetch("http://127.0.0.1:8765/x")  # not opted in
        # opted-in localhost passes validation (then the open is mocked away)
        with mock.patch.object(safehttp._OPENER, "open", return_value="ok") as op:
            self.assertEqual(safehttp.fetch("http://127.0.0.1:8765/x", allow_localhost=True), "ok")
            op.assert_called_once()

    def test_host_allowlist(self):
        with self.assertRaises(ValueError):
            safehttp.fetch("https://evil.example/x", allowed_hosts={"api.anthropic.com"})

    def test_blocks_private_and_metadata_ip_literals(self):
        # cloud metadata endpoint + private/loopback ranges must be refused
        for url in ("https://169.254.169.254/latest/meta-data/",
                    "https://10.0.0.5/x", "https://192.168.1.1/x", "https://127.0.0.1/x"):
            with self.assertRaises(ValueError):
                safehttp.fetch(url)

    def test_allows_public_ip_literal(self):
        with mock.patch.object(safehttp._OPENER, "open", return_value="ok"):
            self.assertEqual(safehttp.fetch("https://1.1.1.1/"), "ok")


class RetryTest(unittest.TestCase):
    def test_retries_transient_then_succeeds(self):
        calls = {"n": 0}

        def flaky(req, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("temporary failure")
            return "ok"

        with mock.patch.object(safehttp._OPENER, "open", side_effect=flaky), \
                mock.patch.object(safehttp.time, "sleep") as slept:
            out = safehttp.fetch("https://api.anthropic.com/v1/messages", retries=2)
        self.assertEqual(out, "ok")
        self.assertEqual(calls["n"], 3)          # 1 try + 2 retries
        self.assertEqual(slept.call_count, 2)    # backed off twice

    def test_does_not_retry_a_real_4xx(self):
        err = urllib.error.HTTPError("https://api.anthropic.com/v1/messages", 400, "bad", {}, None)
        with mock.patch.object(safehttp._OPENER, "open", side_effect=err), \
                mock.patch.object(safehttp.time, "sleep") as slept:
            with self.assertRaises(urllib.error.HTTPError):
                safehttp.fetch("https://api.anthropic.com/v1/messages", retries=2)
        slept.assert_not_called()                # 400 is a real error, not transient

    def test_retries_429_and_5xx(self):
        for code in (429, 503):
            err = urllib.error.HTTPError("https://api.anthropic.com/v1/messages", code, "x", {}, None)
            with mock.patch.object(safehttp._OPENER, "open", side_effect=err), \
                    mock.patch.object(safehttp.time, "sleep") as slept:
                with self.assertRaises(urllib.error.HTTPError):
                    safehttp.fetch("https://api.anthropic.com/v1/messages", retries=2)
            self.assertEqual(slept.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
