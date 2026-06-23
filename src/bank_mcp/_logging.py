"""_logging.py — logging for bank.mcp.

One logger tree (`bank_mcp.*`). Library modules just call `logging.getLogger(__name__)`
and emit at sensible levels; they do NOT configure handlers, so importing the package is
silent. CLI entrypoints call `configure()` once to attach a stderr handler.

Conventions:
- Operational/diagnostic messages (sync progress, fallbacks, failures) go through
  logging. User-facing output (the digest, analytics tables, JSON dumps) stays on
  stdout via print() — it is the program's result, not a log line.
- Logs go to STDERR, which matters for the MCP server (stdout is its JSON-RPC channel).
- Never log secrets, tokens, or raw monetary amounts. Log that something happened and
  whether it succeeded, not the customer's balance.

Level is INFO by default; override with BANK_MCP_LOG_LEVEL (e.g. DEBUG, WARNING).
"""
import json
import logging
import os
import sys
import time

_CONFIGURED = False
_llm_log = logging.getLogger("bank_mcp.llm")


def configure(level: str | None = None) -> None:
    """Attach a stderr handler to the root logger for a CLI run (idempotent).

    Configures root (not just `bank_mcp`) so logs emit whether a module is imported
    by name or run via `python -m ...` (where its logger is `__main__`). Only called
    from CLI entrypoints, never on import, so library consumers keep their own setup.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or os.environ.get("BANK_MCP_LOG_LEVEL", "INFO")).upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(lvl)
    _CONFIGURED = True
    if init_sentry():
        logging.getLogger("bank_mcp").info("Sentry error tracking enabled")


def trace_llm(purpose: str, model: str, system: str, user: str,
              response, ok: bool, latency_ms: float) -> None:
    """Observability for the few model calls (narrate / merchant-match / receipt-extract).

    Always logs a metadata line (no content) so a failure is diagnosable. If
    BANK_MCP_LLM_TRACE is set to a file path, also appends one JSON record per call —
    by default just sizes + latency + outcome (so you can audit *that* a call happened
    and how big it was without storing content). Set BANK_MCP_LLM_TRACE_FULL=1 to also
    capture the full prompt/response transcript (opt-in, since it's the content the
    privacy thesis is about).
    """
    _llm_log.info("llm %s (%s) ok=%s %.0fms in=%dch out=%dch",
                  purpose, model, ok, latency_ms, len(system) + len(user),
                  len(response or ""))
    path = os.environ.get("BANK_MCP_LLM_TRACE")
    if not path:
        return
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "purpose": purpose, "model": model,
           "ok": ok, "latency_ms": round(latency_ms), "in_chars": len(system) + len(user),
           "out_chars": len(response or "")}
    if os.environ.get("BANK_MCP_LLM_TRACE_FULL") == "1":
        rec["system"], rec["user"], rec["response"] = system, user, response
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        _llm_log.warning("could not write LLM trace to %s: %s", path, e)


def init_sentry() -> bool:
    """Initialize Sentry error tracking IF it's installed and SENTRY_DSN is set.

    Optional: `pip install 'bank-mcp[observability]'`. A no-op otherwise, so the core
    stays zero-dependency. Returns True if Sentry was initialized.
    """
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger("bank_mcp").warning(
            "SENTRY_DSN set but sentry-sdk not installed; run pip install 'bank-mcp[observability]'")
        return False
    sentry_sdk.init(dsn=dsn, traces_sample_rate=0.0)
    return True
