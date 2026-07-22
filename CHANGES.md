# Changes

## Unreleased

- Store and aggregate money as exact integer cents.
- Make ingestion idempotent and reconcile pending transactions with posted records.
- Recompute analytics independently in SQL.
- Keep forecasting and anomaly detection deterministic.
- Bound optional model inputs and keep their outputs outside computed financial truth.
- Provide a synthetic, keyless CLI and test demo.

## Verification

```bash
pip install -e ".[dev]"
ruff check src tests
mypy src/bank_mcp
pytest -q
bank-mcp demo
```

Supported Python versions and required quality gates live in the README and CI.
