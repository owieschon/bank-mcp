#!/usr/bin/env python3
"""
test_observability.py — logging config, the opt-in LLM trace, and the optional
Sentry hook. Verifies the privacy-sensitive default: the trace records metadata
only unless full capture is explicitly requested.
"""
import json
import logging
import os
import tempfile
import unittest

from bank_mcp import _logging


class LlmTraceTest(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".jsonl")
        os.environ["BANK_MCP_LLM_TRACE"] = self.path
        os.environ.pop("BANK_MCP_LLM_TRACE_FULL", None)

    def tearDown(self):
        os.environ.pop("BANK_MCP_LLM_TRACE", None)
        os.environ.pop("BANK_MCP_LLM_TRACE_FULL", None)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_metadata_only_by_default(self):
        _logging.trace_llm("narrate", "claude-haiku-4-5", "system", "user", "response", True, 42.0)
        rec = json.loads(open(self.path).read())
        self.assertEqual(rec["purpose"], "narrate")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["in_chars"], len("system") + len("user"))
        self.assertEqual(rec["out_chars"], len("response"))
        # the content the privacy thesis is about is NOT stored by default
        self.assertNotIn("system", rec)
        self.assertNotIn("response", rec)

    def test_full_transcript_is_opt_in(self):
        os.environ["BANK_MCP_LLM_TRACE_FULL"] = "1"
        _logging.trace_llm("match/extract", "m", "the system", "the user", "the response", True, 1.0)
        rec = json.loads(open(self.path).read())
        self.assertEqual(rec["system"], "the system")
        self.assertEqual(rec["response"], "the response")

    def test_no_trace_file_means_no_write(self):
        os.environ.pop("BANK_MCP_LLM_TRACE", None)
        # should not raise and should not create anything
        _logging.trace_llm("narrate", "m", "s", "u", "r", False, 0.0)
        self.assertFalse(os.path.exists(self.path))


class ConfigAndSentryTest(unittest.TestCase):
    def test_configure_is_idempotent_and_attaches_a_handler(self):
        _logging.configure()
        before = list(logging.getLogger().handlers)
        _logging.configure()  # second call is a no-op
        self.assertEqual(list(logging.getLogger().handlers), before)
        self.assertTrue(logging.getLogger().handlers)

    def test_sentry_noops_without_dsn(self):
        os.environ.pop("SENTRY_DSN", None)
        self.assertFalse(_logging.init_sentry())


if __name__ == "__main__":
    unittest.main(verbosity=2)
