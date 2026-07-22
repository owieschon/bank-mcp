"""mcp_server.py — a Model Context Protocol server over stdio (standard library only).

Exposes the finance engines as MCP tools so an MCP client (e.g. Claude Desktop) can
ask for a digest or the SQL analytics. Implements the JSON-RPC 2.0 messages MCP needs
over the newline-delimited stdio transport: `initialize`, `tools/list`, `tools/call`.
No third-party SDK — the protocol surface is small enough to implement directly, which
keeps the project dependency-free.

Run:  python -m bank_mcp.mcp_server      (or the `bank-mcp-server` console script)

The four public tools always run against the bundled synthetic demo data, so the
server is usable with no real financial data. Their schemas accept filters and
presentation options, not database or transaction-file paths. Real SQLite analysis
is a separate local CLI path (`bank-mcp analytics --db PATH`).
"""
import json
import logging
import math
import sqlite3
import sys
from typing import Any, Optional

from bank_mcp import __version__, demo
from bank_mcp.store import analytics, db

log = logging.getLogger(__name__)
PROTOCOL_VERSION = "2024-11-05"

# ---------------------------------------------------------------- tool registry

TOOLS: list[dict[str, Any]] = [
    {
        "name": "build_digest",
        "description": "Build the full personal-finance digest (cash-flow forecast, "
                       "savings pace, fee/duplicate scan, recurring + reconciliation) "
                       "from synthetic demo data. Returns the Markdown digest.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "balance": {"type": "number", "description": "starting balance for the forecast"},
                "mode": {"type": "string", "enum": ["weekly", "monthly"], "default": "monthly"},
            },
        },
    },
    {
        "name": "monthly_cashflow",
        "description": "SQL rollup over the synthetic demo store: per-month income, "
                       "spend, net, running net, and month-over-month change.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
            "owner": {"type": "string", "description": "filter to one account owner"}}},
    },
    {
        "name": "category_breakdown",
        "description": "SQL rollup over the synthetic demo store: spend per category with each "
                       "category's share of total spend.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
            "owner": {"type": "string"}}},
    },
    {
        "name": "top_merchants",
        "description": "SQL rollup over the synthetic demo store: top merchants by total "
                       "spend, ranked.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
            "owner": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        }},
    },
]

TOOLS_BY_NAME: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in TOOLS}


def _validate_schema_value(value: Any, schema: dict, path: str) -> None:
    """Validate the JSON Schema subset published by this server."""
    expected = schema.get("type")
    if not isinstance(expected, str):
        raise ValueError(f"unsupported input schema type at {path}: {expected}")
    valid_type = {
        "object": lambda item: isinstance(item, dict),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
    }.get(expected)
    if valid_type is None:
        raise ValueError(f"unsupported input schema type at {path}: {expected}")
    if not valid_type(value):
        raise ValueError(f"{path} must be {expected}")
    if expected in {"number", "integer"} and not math.isfinite(value):
        raise ValueError(f"{path} must be finite")

    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"{path} missing required field(s): {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(f"{path} has unknown field(s): {', '.join(unknown)}")
        for name, item in value.items():
            property_schema = properties.get(name)
            if property_schema is not None:
                _validate_schema_value(item, property_schema, f"{path}.{name}")

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(item) for item in schema["enum"])
        raise ValueError(f"{path} must be one of: {allowed}")
    if expected in {"number", "integer"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} must be at most {schema['maximum']}")


def _validate_tool_arguments(name: str, arguments: Any) -> dict:
    """Return validated tool arguments or fail before dispatch."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ValueError(f"unknown tool: {name}")
    _validate_schema_value(arguments, tool["inputSchema"], "arguments")
    return arguments


def _demo_conn() -> sqlite3.Connection:
    conn = db.connect(":memory:")
    db.init_schema(conn)
    db.upsert_transactions(conn, demo.generate())
    return conn


def _call_tool(name: str, args: Optional[dict]) -> str:
    """Run a tool, returning text. Raises ValueError on an unknown tool."""
    args = args or {}
    if name == "build_digest":
        from bank_mcp import finance_agent as fa
        from bank_mcp.store import obligation_registry as oblreg
        import os
        data = os.path.join(os.path.dirname(__file__), "data")
        oblreg.REGISTRY_PATH = os.path.join(data, "obligations.demo.json")
        digest = fa.build_digest(
            demo.generate(), balance=float(args.get("balance", 1200.0)),
            mode=args.get("mode", "monthly"), forecast_days=35, buffer=100.0,
            include_burn=True, scan_days=30,
            rules_path=os.path.join(data, "rules.demo.md"))
        return fa.render(digest) + "\n\n" + fa.headline_line(digest)

    conn = _demo_conn()
    try:
        if name == "monthly_cashflow":
            rows = analytics.monthly_cashflow(conn, args.get("owner"))
        elif name == "category_breakdown":
            rows = analytics.category_breakdown(conn, args.get("owner"))
        elif name == "top_merchants":
            rows = analytics.top_merchants(conn, args.get("owner"), int(args.get("limit", 10)))
        else:
            raise ValueError(f"unknown tool: {name}")
        return json.dumps(rows, indent=2)
    finally:
        conn.close()


# ---------------------------------------------------------------- JSON-RPC layer


def handle(request: dict) -> Optional[dict]:
    """Map one JSON-RPC request dict to a response dict (or None for a notification)."""
    method = request.get("method")
    rid = request.get("id")
    params = request.get("params") or {}

    # Notifications (no id) get no response.
    if rid is None and method != "initialize":
        return None

    if method == "initialize":
        return _ok(rid, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "bank-mcp", "version": __version__},
        })
    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})
    if method == "tools/call":
        try:
            if not isinstance(params, dict):
                raise ValueError("tools/call params must be an object")
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("tools/call name must be a non-empty string")
            arguments = params["arguments"] if "arguments" in params else {}
            validated = _validate_tool_arguments(name, arguments)
            log.info("tool call: %s", name)
            text = _call_tool(name, validated)
            return _ok(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:  # surface tool errors as an MCP tool result, not a transport error
            log.warning("tool call failed: %s", e)
            return _ok(rid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    return _err(rid, -32601, f"method not found: {method}")


def _ok(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def serve_stdio(stdin: Any = None, stdout: Any = None) -> None:
    """Read newline-delimited JSON-RPC messages from stdin, write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        response = handle(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main() -> None:
    from bank_mcp import _logging
    _logging.configure()  # logs to stderr; stdout is the JSON-RPC channel
    log.info("bank-mcp MCP server ready (%d tools)", len(TOOLS))
    serve_stdio()


if __name__ == "__main__":
    main()
