# bank.mcp

I built this to read my own money, so I wasn't about to let a language model do the arithmetic.

A wrong "all clear" here has a real price: you think your savings pace is on track and it isn't, or the cash-flow forecast says you clear your buffer and you don't. So every binding number in this pipeline is computed in plain, deterministic, integer-cents Python. The model never touches it.

What the LLM is allowed to do is narrow and tested: narrate a short summary, match merchant-name strings, and pull text out of receipt emails — all from small per-section summary dicts. **Raw transaction rows never enter a prompt** (no dates, no account numbers, no transaction IDs). If there's no API key, or the model returns garbage, the deterministic result stands. `--no-voice` runs the entire pipeline at zero tokens, no network, and is fully correct — the model is an optional voice on top of an engine that already has the answer.

## What it does

It turns a SQLite store of bank transactions into one unified digest: cash-flow forecast, savings pace, spending breakdown, fee/duplicate scan, recurring-charge detection, and receipt reconciliation. It also ships an MCP server and a static report site.

```
bank-mcp demo

# bank.mcp — UNIFIED MONTHLY DIGEST
## What matters
- Clear: balance stays at or above the $100.00 buffer for the full 35-day horizon
  (min $1,087.44 on May 6, 2026).
- Fee/fraud: $49.99 recoverable this 30d.
## Cash-flow forecast
- Status: 🟢 CLEAR · Start $1,200.00 → projected end $4,188.00 (35d, buffer $100.00)
```

`demo` runs the whole thing on bundled synthetic data — no bank, no credentials, no key.

## The line I care about

- `money.py` is the single integer-cents authority. `db.py` stores `amount` as integer cents, so the SQL read-models sum exact integers, never floats.
- The SQL analytics in `store/queries.sql` are cross-checked against an independent Python recompute in `analytics.py`. Two paths to the same number.
- Outbound HTTP is HTTPS-only and SSRF-guarded (blocks private/link-local/loopback literals, pins allowed hosts at the Anthropic call site). It retries only transient failures (429/5xx/timeouts), never 4xx.
- Sync is an idempotent upsert keyed on transaction id, with a pluggable transport (snapshot file / Plaid). Re-running never double-counts.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

bank-mcp demo        # full digest from synthetic data
bank-mcp analytics   # SQL reporting rollups
pytest -q            # 339 tests
```

Zero runtime dependencies — `dependencies = []`, Python stdlib only. 339 tests pass at 75% coverage (CI gates ≥70% across Python 3.10–3.13); `ruff` and `mypy` are clean. The `bank-mcp-server` console script speaks JSON-RPC 2.0 over stdio for MCP clients.

Public, sanitized version of a system I run on my own accounts; all data in the repo is synthetic, and secrets resolve from env → macOS Keychain, never the tree.

## Going deeper

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layers, data flow, SQLite schema, the LLM boundary.
- [docs/DECISIONS.md](docs/DECISIONS.md) — why SQLite, the SQL/Python split, what I left alone.
- [docs/SETUP.md](docs/SETUP.md) — connecting a real bank, the optional secondary-currency report toggle, and the private deploy.
- [CHANGES.md](CHANGES.md) — what changed when this was prepared as a public work sample.

MIT — see [LICENSE](LICENSE).
