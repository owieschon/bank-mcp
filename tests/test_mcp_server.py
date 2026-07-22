#!/usr/bin/env python3
"""
test_mcp_server.py — the MCP server's JSON-RPC dispatch and stdio transport.

Exercises the protocol surface (initialize / tools/list / tools/call), the tool
results, error handling, and a full newline-delimited stdio round-trip.
"""
import io
import json
import unittest
from unittest.mock import patch

from bank_mcp import mcp_server as srv


def _req(method, params=None, rid=1):
    r = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        r["id"] = rid
    if params is not None:
        r["params"] = params
    return r


class HandleTest(unittest.TestCase):
    def test_initialize(self):
        resp = srv.handle(_req("initialize", {"protocolVersion": "2024-11-05"}))
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertIn("tools", resp["result"]["capabilities"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "bank-mcp")
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")

    def test_tools_list(self):
        resp = srv.handle(_req("tools/list"))
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"build_digest", "monthly_cashflow",
                                 "category_breakdown", "top_merchants"})
        for t in resp["result"]["tools"]:
            self.assertIn("description", t)
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_public_tool_schemas_are_synthetic_only(self):
        expected_properties = {
            "build_digest": {"balance", "mode"},
            "monthly_cashflow": {"owner"},
            "category_breakdown": {"owner"},
            "top_merchants": {"owner", "limit"},
        }
        for tool in srv.TOOLS:
            schema = tool["inputSchema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["properties"]), expected_properties[tool["name"]])
            self.assertIn("synthetic", tool["description"].lower())
            self.assertTrue(
                {"db", "database", "transactions", "file", "path"}.isdisjoint(
                    schema["properties"]
                )
            )
        limit = srv.TOOLS_BY_NAME["top_merchants"]["inputSchema"]["properties"]["limit"]
        self.assertEqual((limit["minimum"], limit["maximum"]), (1, 100))

    def test_invalid_arguments_fail_before_tool_dispatch(self):
        cases = [
            ("nope", {}, "unknown tool"),
            ("build_digest", None, "arguments must be object"),
            ("build_digest", [], "arguments must be object"),
            ("build_digest", {"database": "private.sqlite"}, "unknown field"),
            ("build_digest", {"balance": True}, "balance must be number"),
            ("build_digest", {"balance": float("nan")}, "balance must be finite"),
            ("build_digest", {"balance": float("inf")}, "balance must be finite"),
            ("build_digest", {"mode": "quarterly"}, "mode must be one of"),
            ("monthly_cashflow", {"owner": 7}, "owner must be string"),
            ("top_merchants", {"limit": True}, "limit must be integer"),
            ("top_merchants", {"limit": 0}, "limit must be at least 1"),
            ("top_merchants", {"limit": 101}, "limit must be at most 100"),
        ]
        with patch.object(srv, "_call_tool") as call_tool:
            for name, arguments, message in cases:
                with self.subTest(name=name, arguments=arguments):
                    resp = srv.handle(
                        _req("tools/call", {"name": name, "arguments": arguments})
                    )
                    self.assertTrue(resp["result"]["isError"])
                    self.assertIn(message, resp["result"]["content"][0]["text"])
            call_tool.assert_not_called()

    def test_required_fields_are_enforced_by_the_shared_validator(self):
        with self.assertRaisesRegex(ValueError, "missing required field.*owner"):
            srv._validate_schema_value(
                {},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["owner"],
                    "properties": {"owner": {"type": "string"}},
                },
                "arguments",
            )

    def test_call_build_digest(self):
        resp = srv.handle(_req("tools/call", {"name": "build_digest", "arguments": {"balance": 1500}}))
        self.assertFalse(resp["result"]["isError"])
        text = resp["result"]["content"][0]["text"]
        self.assertIn("DIGEST", text.upper())
        self.assertIn("HEADLINE", text.upper())

    def test_call_analytics_returns_json(self):
        resp = srv.handle(_req("tools/call", {"name": "monthly_cashflow", "arguments": {}}))
        self.assertFalse(resp["result"]["isError"])
        rows = json.loads(resp["result"]["content"][0]["text"])
        self.assertTrue(rows and "running_net" in rows[0])

    def test_unknown_tool_is_tool_error_not_transport_error(self):
        resp = srv.handle(_req("tools/call", {"name": "nope", "arguments": {}}))
        self.assertTrue(resp["result"]["isError"])
        self.assertNotIn("error", resp)  # JSON-RPC envelope is still a success

    def test_unknown_method(self):
        resp = srv.handle(_req("frobnicate"))
        self.assertEqual(resp["error"]["code"], -32601)

    def test_notification_gets_no_response(self):
        self.assertIsNone(srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


class StdioRoundTripTest(unittest.TestCase):
    def test_newline_delimited_exchange(self):
        lines = [
            json.dumps(_req("initialize", {"protocolVersion": "2024-11-05"}, rid=1)),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(_req("tools/list", rid=2)),
        ]
        out = io.StringIO()
        srv.serve_stdio(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
        responses = [json.loads(line) for line in out.getvalue().splitlines()]
        # initialize + tools/list answered; the notification produced no line
        self.assertEqual([r["id"] for r in responses], [1, 2])
        self.assertEqual(responses[1]["result"]["tools"][0]["name"], "build_digest")

    def test_bad_json_yields_parse_error(self):
        out = io.StringIO()
        srv.serve_stdio(stdin=io.StringIO("{not json}\n"), stdout=out)
        resp = json.loads(out.getvalue())
        self.assertEqual(resp["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main(verbosity=2)
