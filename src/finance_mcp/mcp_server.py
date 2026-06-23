"""mcp_server.py — a Model Context Protocol server over stdio (standard library only).

Exposes the finance engines as MCP tools so an MCP client (e.g. Claude Desktop) can
ask for a digest or the SQL analytics. Implements the JSON-RPC 2.0 messages MCP needs
over the newline-delimited stdio transport: `initialize`, `tools/list`, `tools/call`.
No third-party SDK — the protocol surface is small enough to implement directly, which
keeps the project dependency-free.

Run:  python -m finance_mcp.mcp_server      (or the `finance-mcp-server` console script)

By default the tools run against the bundled synthetic demo data, so the server is
usable with no real financial data. Point `build_digest`/analytics at a real SQLite
DB or transactions file via tool arguments.
"""
import json
import sys

from finance_mcp import __version__, demo
from finance_mcp.store import analytics, db

PROTOCOL_VERSION = "2024-11-05"

# ---------------------------------------------------------------- tool registry

TOOLS = [
    {
        "name": "build_digest",
        "description": "Build the full personal-finance digest (cash-flow forecast, "
                       "savings pace, fee/duplicate scan, recurring + reconciliation) "
                       "from synthetic demo data. Returns the Markdown digest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "balance": {"type": "number", "description": "starting balance for the forecast"},
                "mode": {"type": "string", "enum": ["weekly", "monthly"], "default": "monthly"},
            },
        },
    },
    {
        "name": "monthly_cashflow",
        "description": "SQL rollup: per-month income, spend, net, running net, and "
                       "month-over-month change (over the demo store).",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string", "description": "filter to one account owner"}}},
    },
    {
        "name": "category_breakdown",
        "description": "SQL rollup: spend per category with each category's share of total spend.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"}}},
    },
    {
        "name": "top_merchants",
        "description": "SQL rollup: the top merchants by total spend, ranked.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        }},
    },
]


def _demo_conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    db.upsert_transactions(conn, demo.generate())
    return conn


def _call_tool(name, args):
    """Run a tool, returning text. Raises ValueError on an unknown tool."""
    args = args or {}
    if name == "build_digest":
        from finance_mcp import finance_agent as fa
        from finance_mcp.store import obligation_registry as oblreg
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


def handle(request):
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
            "serverInfo": {"name": "finance-mcp", "version": __version__},
        })
    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        try:
            text = _call_tool(name, params.get("arguments"))
            return _ok(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:  # surface tool errors as an MCP tool result, not a transport error
            return _ok(rid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    return _err(rid, -32601, f"method not found: {method}")


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def serve_stdio(stdin=None, stdout=None):
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


def main():
    serve_stdio()


if __name__ == "__main__":
    main()
