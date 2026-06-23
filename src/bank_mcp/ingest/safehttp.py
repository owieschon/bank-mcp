"""safehttp.py — hardened outbound HTTP (public-service security posture).

Every outbound request in the suite goes through fetch(). It:
  - enforces HTTPS (the only exception is explicitly-opted-in localhost, for the
    local bank-mcp server),
  - optionally pins an allowlist of hosts (use this at sensitive call sites),
  - blocks IP-literal targets in private/link-local/loopback/reserved ranges
    (e.g. the cloud metadata endpoint 169.254.169.254),
  - bounds every request with a timeout and refuses redirects to non-HTTPS targets,
  - retries only transient failures with exponential backoff.

So a future edit that lets a URL come from config/user input can't trivially be
turned into a plaintext downgrade or a request to an internal IP literal.

Scope honestly: this is NOT a full DNS-rebinding defense — a hostname that *resolves*
to a private address is not caught (the IP block applies to literals). For call sites
that send credentials (the Anthropic API), `allowed_hosts` is pinned so the host can't
drift regardless. Centralizing this replaces the per-call-site `startswith("https://")`
guards that were duplicated (and missing) across the codebase.
"""

import ipaddress
import logging
import ssl
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 30
# Transient HTTP statuses worth retrying (rate-limit + transient server errors).
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
log = logging.getLogger(__name__)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to HTTPS targets; block plaintext/scheme downgrades."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.HTTPError(
                req.full_url, code, f"blocked non-HTTPS redirect to {newurl}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _verified_context():
    """A TLS context that ALWAYS verifies certs. Prefer certifi's bundle (some
    Python builds — e.g. python.org macOS framework — ship with no system CA file,
    which would otherwise make every HTTPS call fail). Never disable verification."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_OPENER = urllib.request.build_opener(
    _SafeRedirect,
    urllib.request.HTTPSHandler(context=_verified_context()),
)


def _validate(url, allow_localhost, allowed_hosts):
    p = urlparse(url)
    is_https = p.scheme == "https"
    is_local = (allow_localhost and p.scheme == "http"
                and p.hostname in ("localhost", "127.0.0.1"))
    if not (is_https or is_local):
        raise ValueError(f"Refusing non-HTTPS outbound URL: {url}")
    if allowed_hosts is not None and p.hostname not in allowed_hosts:
        raise ValueError(f"Host not allowlisted ({p.hostname}): {url}")
    # Block IP-literal targets in private/link-local/loopback/reserved ranges — e.g.
    # the cloud metadata endpoint 169.254.169.254, or 10.x / 192.168.x. (This is not a
    # DNS-rebinding defense: a hostname that resolves to a private IP is not caught;
    # pin allowed_hosts at sensitive call sites for that.)
    try:
        ip = ipaddress.ip_address(p.hostname or "")
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local
                           or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        if not (is_local and str(ip) == "127.0.0.1"):
            raise ValueError(f"Refusing request to non-public address: {p.hostname}")


def fetch(url, *, data=None, headers=None, method=None, timeout=DEFAULT_TIMEOUT,
          allow_localhost=False, allowed_hosts=None, retries=0, backoff=0.5):
    """Open a URL or a urllib Request safely. Returns the response object (use as a
    context manager). Raises ValueError if the scheme/host is not allowed.

    `allow_localhost` permits http://localhost|127.0.0.1 (the local bank-mcp server).
    `allowed_hosts` (iterable) pins the request to a set of hostnames.
    `retries` retries TRANSIENT failures only — timeouts, connection errors, and
    HTTP 429/5xx — with exponential backoff (`backoff` * 2**attempt seconds). A 4xx
    (other than 429) is a real error and is never retried. Default 0 = no retry.
    """
    if isinstance(url, urllib.request.Request):
        req = url
    else:
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    # Validate the Request's final URL (covers both string and Request inputs).
    _validate(req.full_url, allow_localhost, allowed_hosts)

    attempt = 0
    while True:
        try:
            return _OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in _RETRY_STATUS and attempt < retries:
                _backoff(req.full_url, attempt, backoff, f"HTTP {e.code}")
                attempt += 1
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                _backoff(req.full_url, attempt, backoff, str(getattr(e, "reason", e)))
                attempt += 1
                continue
            raise


def _backoff(url, attempt, base, why):
    delay = base * (2 ** attempt)
    log.warning("transient fetch failure (%s); retrying in %.1fs (attempt %d)",
                why, delay, attempt + 1)
    time.sleep(delay)
