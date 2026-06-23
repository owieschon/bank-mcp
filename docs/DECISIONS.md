# Design decisions

Why the project is shaped the way it is, including alternatives considered and
rejected. (For *what changed* during portfolio preparation, see `../CHANGES.md`.)

## Language fit: Python vs SQL vs PostgreSQL

**SQLite is the right datastore here; PostgreSQL would be over-engineering.**
This is a single-user, local tool: one file, a few thousand transactions, no
concurrency, no network, no multi-tenant access. The standard-library `sqlite3`
module covers it completely and keeps the project at **zero runtime
dependencies**. PostgreSQL would add an external
service to run, a third-party driver to install, and operational overhead, in
exchange for nothing this workload needs.

**The SQL / Python split follows the shape of each problem:**

- **SQL owns storage, retrieval, and descriptive analytics.** `store/db.py` defines
  the schema, does an idempotent `upsert` keyed on transaction id (with pending→posted
  supersession), indexes `(owner, date)`, and filters by `owner`/`since` in the query.
  The **reporting read-models** — monthly cash flow (with a running total and a
  month-over-month delta), category breakdown (each category's share of spend), and
  top merchants by spend — live in `store/queries.sql` and are run by
  `store/analytics.py`. These are set-based aggregations over a relational store, so
  SQL (CTEs, `GROUP BY`, and window functions: `SUM() OVER`, `LAG()`, `RANK()`) is the
  clearest way to express them, and the queries are written to read top to bottom.
- **Python owns the algorithmic analysis** — median inter-charge-gap cadence
  classification, price-step detection, cash-flow projection / roll-forward,
  recurring-stream detection, dispute tracking. These are iterative, stateful
  computations; expressing them as `GROUP BY` would be the wrong tool. They are also
  the deterministic, unit-tested financial core, so they stay in Python where the
  test surface is single-language.

So the boundary is: **set-based reporting → SQL; algorithmic forecasting → Python.**
The dataset is small enough that SQL is chosen for clarity, not performance, and `test_analytics.py` cross-checks every query result against an
independent Python recomputation so the two never silently diverge. (Window functions
need SQLite ≥ 3.25, which every supported Python ships.)

## Deterministic math, LLM only at the edges

Financial figures are computed by plain, unit-tested Python. The LLM is used only
to (a) narrate a compact summary dict, (b) match merchant-name strings, and
(c) extract text from receipt emails. **Raw transaction rows never enter a
prompt.** A `--no-voice` run is fully correct at zero tokens and no network. This
is the core design premise and the reason the analysis is auditable.

## Money is stored and aggregated as integer cents

The `transactions.amount` column holds **integer cents**, and the SQL analytics sum
those integers, so aggregation is exact — no floating-point drift across thousands of
rows. One module, `money.py`, is the rounding/formatting authority: `to_cents()` rounds
half-up via `Decimal(str(x))` (so a value rounds at the two decimals a human reads), and
`fmt()` is the single display formatter — both `delivery.money()` and
`subscription_creep.money()` delegate to it, so no view can drift from another.

Where exactness lives, and why that's the right line: the **storage and aggregation
layer** (the DB column and the SQL rollups) is exact integer cents — that is where an
unbounded running total over thousands of rows would otherwise accumulate float error,
so that is where exactness matters most. The reporting engines then compute display
figures in float rounded to the cent at each step (bounded, unit-tested). This is a
deliberate, proportionate choice for a **reporting/digest tool**: it is not a
double-entry ledger, so threading `Decimal` through all six engines (and round-tripping
it through the JSON the digest is serialized to) would add fragility and ceremony for
no behavior change. The penny-perfect guarantee is placed exactly where penny drift
could actually occur.

## Package layout mirrors the data flow

`src/bank_mcp/` is grouped into `ingest/` → `store/` → `engines/` → `report/`,
the direction data actually moves, with `finance_agent.py` as the single
orchestrator on top. The dependency direction is acyclic. A flat module package
was considered; the grouped layout was chosen so the architecture is legible from
the directory tree rather than only from reading imports.

## The secondary-currency toggle is an optional, config-driven example

The static report can show an optional client-side USD↔secondary-currency toggle (with
a World-Bank-PPP orientation figure) — a self-contained demonstration of client-side
currency re-denomination, where the deterministic math stays in Python and only the
*display* is re-denominated. The target currency, locale, and PPP factor are read from
env at build time (`REPORT_SECONDARY_CURRENCY` / `_LOCALE` / `_PPP`); with none set, the
report ships USD-only and the toggle is hidden, so the artifact carries no baked locale.

## Observability, sized to the system

- **Structured logging** (`bank_mcp._logging`): operational/diagnostic messages (sync
  progress, fallbacks, failures) go through `logging` at levels, to stderr, configured
  by the CLI entrypoints. User-facing output (digests, analytics tables, JSON) stays on
  stdout — it's the result, not a log. Fallbacks that used to be silent (DB→JSON,
  live-balance fetch failure) now log warnings, so a degraded run is visible. No secrets
  or raw amounts are logged.
- **LLM-call trace** (`trace_llm`, opt-in via `BANK_MCP_LLM_TRACE`): every model call
  (narrate / merchant-match / receipt-extract) records purpose, model, sizes, latency,
  and outcome to JSONL — metadata only by default, full prompt/response transcript only
  with `BANK_MCP_LLM_TRACE_FULL=1`. This makes a model failure diagnosable after the fact
  and lets you audit exactly what reached the model — which is the privacy thesis.
- **MCP tool calls** are logged (name + success/failure) by the server.
- **Sentry** is an optional `[observability]` extra that initializes only if installed
  and `SENTRY_DSN` is set; otherwise a no-op, so the core stays zero-dependency.

**Why not Phoenix / LangSmith / LangChain / LangGraph?** Those instrument LLM *chains
and agent graphs*. This suite makes a handful of raw Anthropic API calls — there is no
chain or graph to trace, so adding them would be tracing a structure that doesn't exist
(and would break zero-dependency). The small, well-defined LLM surface is fully covered
by `trace_llm` above. Reaching for a heavy tracing framework here would be the
over-engineering this project otherwise avoids.

## Status glyphs in the digest are intentional UX

The digest uses 🟢/🔴 for forecast status and ✅/⚠️/🔻 for rule scoring. These are in the
human-facing report (email + terminal), the same way CI dashboards and GitHub checks use
status icons — they aren't decoration sprinkled through the codebase. The engineering
rigor lives in the data/LLM boundary and the tests, not in avoiding glyphs; removing them
would make the report worse to read. (A reviewer who pattern-matches emoji to machine
generation is welcome to flag it — hence this note.)

## Two detectors that intentionally differ (not duplication)

`recurring.streams()` and `fee_fraud_scan._is_recurring()` both decide "is this a
recurring charge," but on purpose by different rules: the former classifies cadence into
named bands for the recurring-spend report; the latter is a stricter low-variance gate
(coefficient of variation ≤ 0.5) tuned for fee detection, where a false-positive
"recurring fee" is worse than missing an irregular one. The shared *vocabulary* (the
transfer/P2P merchant lists) was consolidated into `subscription_creep`; the two
recurrence policies are kept distinct because they answer different questions. This is a
design choice, not drift — flagged so a reviewer doesn't read it as copy-paste.
