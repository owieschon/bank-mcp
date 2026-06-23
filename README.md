# bank.mcp

A personal-finance analysis suite that turns a stream of bank transactions into a
single digest — cash-flow forecast, savings-goal pace, spending breakdown,
fee/duplicate detection, recurring-charge detection, and receipt reconciliation —
rendered as both a Markdown/email digest and an auth-gated static web report.

The line it draws between the math and the LLM:

> All financial math is plain, deterministic, unit-tested Python. A language model
> is used only to *narrate* a compact summary, *match* merchant-name strings, and
> *extract* text from receipt emails. **Raw transaction rows never enter a model
> prompt** — only small per-section summary dicts do. A `--no-voice` run is fully
> correct with zero tokens and no network.

It runs on the **Python standard library only — zero runtime dependencies.**

> Status: a personal project, cleaned up as a work sample. All data in the repo is
> synthetic (`examples/`, `src/bank_mcp/demo.py`); there is no real financial data
> here. 324 tests pass (74% coverage of the testable core); `ruff` and `mypy` are clean;
> CI runs lint + types + tests on Python 3.10–3.12.

## What it produces

`bank-mcp demo` runs the whole pipeline on synthetic data and prints a digest:

```
# bank.mcp — UNIFIED MONTHLY DIGEST
## What matters
- Clear: balance stays at or above the $100.00 buffer for the full 35-day horizon
  (min $1,087.44 on May 6, 2026).
- Fee/fraud: $49.99 recoverable this 30d.
## Cash-flow forecast
- Status: 🟢 CLEAR   ·   Start $1,200.00 → projected end $4,188.00 (35d, buffer $100.00)
- Next income $800.00 from PAYROLL on May 7, 2026  ·  upcoming: May 26 Car Loan $285.00
```

`bank-mcp analytics` runs the SQL read-models over the store:

```
## Monthly cash flow
month    income  spend    net      running_net  net_mom_change
2026-01  4000.0  945.05   3054.95  3054.95      None
2026-02  3200.0  681.31   2518.69  5573.64      -536.26
```

## What this demonstrates

Beyond personal finance, the repo is meant to show transferable craft for data- and
GTM-infrastructure work:

- **Responsible data handling** — local-first, read-only posture; credentials via
  env→Keychain (never committed); SSRF-guarded outbound HTTP; owner-scoped queries;
  zero secrets/PII in the tree or git history. Synthetic data only.
- **Integration craft** — a pluggable bank transport (bank-mcp fork / Plaid / snapshot)
  with graceful fallback, idempotent upsert keyed on transaction id, and retries with
  backoff on transient API failures.
- **An MCP server** (`bank-mcp-server`) exposing the engines as JSON-RPC tools.
- **SQL analytics** — CTE/window-function read-models (`store/queries.sql`) cross-checked
  against a Python recompute.
- **Judgment about LLMs** — deterministic, tested math with the model confined to the
  edges (narrate/match/extract), plus an opt-in trace of every model call.
- **Observability, tests, types** — structured logging, 324 tests, mypy, green CI.

## Shape of the system

```
  bank (Plaid / bank-mcp)          ← real source, not committed
        │
        ▼
  ingest/   transport + sync ──────► store/   SQLite (canonical, lossless `raw` JSON)
                                          │
                                          ▼
                                     engines/  deterministic cores
                                     (forecast · pace · fees · recurring · receipts)
                                          │
                                          ▼   compact summary dicts (never raw rows)
                                     report/   digest (md/email) + static site
                                          ▲
                                     finance_agent.py  ← orchestrator
                                          │
                                     LLM: narrate / match / extract  (edges only)
```

The package layout mirrors that flow:

```
src/bank_mcp/
  ingest/    safehttp · plaid_bridge · plaid_link · sync
  store/     db (SQLite) · subscription_creep (field/cadence accessors) ·
             obligation_registry · merchant_categorizer ·
             queries.sql + analytics (SQL reporting read-models)
  engines/   cashflow_forecaster · budget_scorer · fee_fraud_scan ·
             recurring · receipt_scanner · dispute_agent · llm_matcher
  report/    delivery · digest_templates · build_site · web/
  finance_agent.py   # orchestrator: reconcile → run each engine → one digest
  demo.py            # synthetic data + `python -m bank_mcp demo`
tests/        unit tests + a synthetic transaction fixture
examples/     copy-these config templates (synthetic)
ops/          launchd plist + deploy scripts (author-local)
docs/         ARCHITECTURE · SETUP · DECISIONS
```

## Quickstart (two minutes)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

bank-mcp demo        # build + print a full digest from synthetic data
bank-mcp analytics   # SQL reporting rollups (see src/bank_mcp/store/queries.sql)
pytest -q               # 324 tests
ruff check src tests    # lint
mypy                    # type-check the package
```

`bank-mcp demo` needs no bank credentials and no real data — it generates a
synthetic dataset and runs the whole pipeline end to end. To build the static
report site from a dataset:

```bash
python -m bank_mcp.report.build_site --balance 1200 --txns path/to/transactions.json
# writes ./site/  (index.html + report.html + assets)
```

## Using it with real data

See [docs/SETUP.md](docs/SETUP.md). In short: copy the `examples/*.example.*`
templates to real filenames, point the loaders at them, and connect a bank via
Plaid / a bank-mcp subprocess fork (transport lives in `ingest/`). Real data
files are gitignored by default.

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layers, data flow, the SQLite
  schema, and the LLM boundary.
- [docs/DECISIONS.md](docs/DECISIONS.md) — why SQLite (not Postgres), the SQL/Python
  split, and what was left alone, and why.
- [docs/SETUP.md](docs/SETUP.md) — install, run, test, and the deploy model.
- [CHANGES.md](CHANGES.md) — what changed when this was prepared as a public work sample.

## License

MIT — see [LICENSE](LICENSE).
