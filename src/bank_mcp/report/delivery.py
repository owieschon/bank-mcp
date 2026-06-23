#!/usr/bin/env python3
"""
delivery.py — shared delivery layer for the bank.mcp finance-agent suite.

Factored out of budget_scorer.py's proven implementations so every tool
(ledger, recurring, forecaster, fee/fraud scan, orchestrator) imports ONE
canonical pair of side-effecting helpers instead of reaching into bs internals:

  send_email(to, subject, body) -> bool
      Gmail SMTP_SSL delivery. Env-gated on GMAIL_ADDRESS + GMAIL_APP_PASSWORD.
      Degrades gracefully (prints a notice, returns False) when env is missing.

  call_haiku(system, user) -> str | None
      Raw Haiku call via urllib. Env-gated on ANTHROPIC_API_KEY.
      Returns None when the key is absent (so callers fall back to numbers-only).
      On an HTTP/transport error it returns a short "_(narration unavailable …)_"
      marker string rather than raising, so a flaky network never crashes a tool.

  narrate(summary, tone, mode) -> str | None
      The high-level wrapper the contract mandates. Builds the system/voice block
      from the user's tone + the report mode, then runs call_haiku on the
      precomputed SUMMARY DICT ONLY. The model NEVER sees raw transactions.

  money(x) -> str
      Canonical money formatter, re-exported for convenience.

Design rule: narrate() serializes only the compact summary
dict. If a raw transaction row ever lands in a model prompt, the build is wrong.
"""

import datetime as dt
import json
import os
import smtplib
import subprocess
import time
import urllib.error
import urllib.request
from bank_mcp.ingest import safehttp
from bank_mcp import money as _money
from bank_mcp import _logging
import html


def _e(x):
    """HTML-escape untrusted text before interpolating into an email template."""
    return html.escape(str(x), quote=True)


def _updated_stamp(digest):
    """Human-readable build/sync time (always current) — when the data was last
    refreshed, distinct from `as_of` (last transaction date)."""
    raw = digest.get("generated_at")
    try:
        t = dt.datetime.fromisoformat(raw) if raw else dt.datetime.now()
    except (ValueError, TypeError):
        t = dt.datetime.now()
    hour = t.hour % 12 or 12
    return f"{t.strftime('%b')} {t.day}, {t.year}, {hour}:{t.minute:02d} {t.strftime('%p')}"


def fmt_date(s):
    """ISO date -> 'Jun 24, 2026'. Passes non-ISO / empty values through unchanged
    so it's safe to wrap any date string headed for human-facing text."""
    try:
        d = dt.date.fromisoformat(str(s))
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    except (ValueError, TypeError):
        return str(s) if s else ""


def _format_window(window):
    """A full calendar month renders as 'May 2026'; otherwise 'start – end'.

    Keeps the digest's scope (the budget scores the last complete month) readable
    instead of a raw date range that looks like the report itself is month-old.
    """
    window = window or {}
    start, end = window.get("start", ""), window.get("end", "")
    try:
        s = dt.date.fromisoformat(start)
        e = dt.date.fromisoformat(end)
        month_end = (s.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        if s.day == 1 and e == month_end:
            return f"{s.strftime('%B')} {s.year}"
    except (ValueError, TypeError):
        pass
    return f"{start} &ndash; {end}" if (start or end) else ""


from email.message import EmailMessage

# Cheapest model; operates on the ~1K-token summary dict only.
HAIKU = "claude-haiku-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


# --------------------------------- formatting ---------------------------------

def money(x):
    """Canonical money formatter: $1,234.56 / -$1,234.56 (sign before the symbol).

    Delegates to the one rounding/formatting authority (bank_mcp.money)."""
    return _money.fmt(x)


# ----------------------------------- email ------------------------------------

def _gmail_password():
    """App password from env GMAIL_APP_PASSWORD, else the macOS Keychain (service
    GMAIL_APP_PASSWORD). Lets the secret live encrypted in Keychain instead of a
    plaintext shell file. Returns None if neither source has it, so send_email
    degrades gracefully."""
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if pw:
        return pw
    try:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password",
             "-a", os.environ.get("USER", ""), "-s", "GMAIL_APP_PASSWORD", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _gmail_address():
    """Gmail address from env GMAIL_ADDRESS, else macOS Keychain (service
    GMAIL_ADDRESS), else a neutral placeholder. Mirrors the
    env-then-Keychain pattern used for every other credential in the suite."""
    addr = os.environ.get("GMAIL_ADDRESS")
    if addr:
        return addr
    try:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password",
             "-a", os.environ.get("USER", ""), "-s", "GMAIL_ADDRESS", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.environ.get("GMAIL_ADDRESS", "you@example.com")


def send_email(to, subject, body, *, html=None):
    """Send `body` via Gmail SMTP_SSL. Graceful.

    Address from GMAIL_ADDRESS (env), else macOS Keychain (service
    GMAIL_ADDRESS), else a hardcoded default. App password from env
    GMAIL_APP_PASSWORD, else the macOS Keychain. If the password is missing,
    prints a one-line notice and returns False (no raise). Recipient defaults
    to the resolved address when `to` is falsy. Returns True on a successful
    send.

    When `html` is provided, the email is sent as multipart/alternative with
    the plain text body as fallback and the HTML version as the preferred
    rendering. Email clients that support HTML will show the rich version;
    plain-text clients fall back to `body`.
    """
    addr = _gmail_address()
    pw = _gmail_password()
    if not pw:
        print("  email skipped: set GMAIL_APP_PASSWORD (env or Keychain)")
        return False
    msg = EmailMessage()
    msg["From"] = addr
    msg["To"] = to or addr
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(addr, pw)
        srv.send_message(msg)
    print(f"  emailed to {to or addr}")
    return True





















# --------------------------------- narration ----------------------------------

def _anthropic_key():
    """API key from env ANTHROPIC_API_KEY, else macOS Keychain (service
    ANTHROPIC_API_KEY). Mirrors _gmail_password — lets the key live encrypted in
    Keychain instead of a plaintext shell file. Returns None if neither has it."""
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    try:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password",
             "-a", os.environ.get("USER", ""), "-s", "ANTHROPIC_API_KEY", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def call_haiku(system, user):
    """Raw Haiku call. Key from env or Keychain, graceful.

    Returns None when the key is absent (callers degrade to numbers-only).
    On an HTTP or transport error, returns a short "_(narration unavailable: …)_"
    marker string instead of raising, so a flaky network can't crash a tool.
    """
    key = _anthropic_key()
    if not key:
        return None
    body = json.dumps({
        "model": HAIKU,
        "max_tokens": 700,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    # Defense-in-depth: ANTHROPIC_URL is a hardcoded constant, but enforce HTTPS
    # so no future edit can introduce a file://-scheme SSRF through urlopen.
    if not req.full_url.startswith("https://"):
        return None
    start = time.monotonic()
    try:
        with safehttp.fetch(req, timeout=60, retries=2) as r:
            data = json.loads(r.read())
        out = "".join(b.get("text", "") for b in data.get("content", [])).strip()
        _logging.trace_llm("narrate", HAIKU, system, user, out, True,
                           (time.monotonic() - start) * 1000)
        return out
    except urllib.error.HTTPError as e:
        _logging.trace_llm("narrate", HAIKU, system, user, None, False,
                           (time.monotonic() - start) * 1000)
        return f"_(narration unavailable: HTTP {e.code})_"
    except Exception:
        _logging.trace_llm("narrate", HAIKU, system, user, None, False,
                           (time.monotonic() - start) * 1000)
        return "_(narration unavailable)_"


def _voice_for(mode):
    """The mode-specific instruction block. Mirrors budget_scorer's voices and
    generalizes to non-budget tools (mode is a free-form label like the tool
    name) so every tool gets a sensible default cadence."""
    if mode == "weekly":
        return ("WEEKLY pulse: short and glanceable, forward-leaning. Flag drift "
                "early, name what a slip costs toward the goal, one honest "
                "in-your-corner line. Max ~6 sentences.")
    if mode == "monthly":
        return ("MONTHLY check-in, three short movements: (1) Retrospective — last "
                "month vs budget and baseline, what was saved, where slipped, in "
                "plain dollars. (2) Projection — at this pace what lands by the "
                "move date, ahead/behind. (3) Prescription — concrete and few, "
                "exactly what to do next. Max ~12 sentences.")
    # Generic default for the other suite tools (ledger, recurring, forecaster,
    # fee/fraud scan, orchestrator): read the summary and speak plainly.
    return ("Read the summary and give a short, plain-spoken readout: the "
            "headline first, then the few details that matter, then anything "
            "flagged. Concrete, no fluff. Max ~8 sentences.")


def narrate(summary, tone, mode):
    """Thin Haiku narration over the SUMMARY DICT ONLY.

    Builds the system prompt from the user's own tone guidance plus a
    mode-specific voice, then calls call_haiku with the summary serialized as
    JSON. Returns None (no key) or a string. The model never sees raw
    transactions — only the precomputed compact summary.
    """
    voice = _voice_for(mode)
    system = (
        "You are a personal-finance check-in voice. You receive ONLY a "
        "precomputed JSON summary — never raw transactions. Do not invent "
        "numbers; use only what's in the summary. Tone guidance from the user's "
        "own rules file:\n" + (tone or "Direct, honest, in-your-corner.")
        + "\n\n" + voice
    )
    return call_haiku(system, "SUMMARY:\n" + json.dumps(summary, indent=2))
