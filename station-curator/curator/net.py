"""
Tiny JSON-over-HTTP helper (stdlib urllib).

Used for Jellyfin, Radarr/Sonarr, TMDB and ntfy. The Anthropic SDK is the one
place we don't hand-roll HTTP -- see llm.py.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("curator.net")

# Query-string keys whose values must never reach a log line.
_SECRET_KEYS = {"api_key", "apikey", "X-Api-Key", "api-key"}


class HttpError(Exception):
    """Any non-2xx response or transport failure."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def transient(self) -> bool:
        """
        Worth retrying: overload, gateway errors, timeouts.

        429 is deliberately excluded. On Gemini it usually means a daily
        free-tier quota, not a momentary spike, and retrying twice in six
        seconds just spends the little that's left.
        """
        return self.status is None or self.status == 408 or \
            (self.status is not None and self.status >= 500)


def redact(url: str) -> str:
    """Strip secret query params so a URL is safe to log."""
    split = urllib.parse.urlsplit(url)
    if not split.query:
        return url
    pairs = urllib.parse.parse_qsl(split.query, keep_blank_values=True)
    clean = [(k, "***" if k in _SECRET_KEYS else v) for k, v in pairs]
    query = urllib.parse.urlencode(clean)
    return urllib.parse.urlunsplit(split._replace(query=query))


def request(url, *, method="GET", headers=None, params=None, body=None,
            timeout=30, retries=0):
    """
    Perform an HTTP request and decode a JSON response.

    Returns the decoded payload, or None for an empty body (e.g. 204).
    Raises HttpError on any transport error or non-2xx status.

    `retries` re-attempts transient failures (429/5xx/timeouts) with a simple
    backoff. Model endpoints really do return 503 "high demand", and an
    unattended curator shouldn't lose a whole cycle's classification to one.
    """
    attempt = 0
    while True:
        try:
            return _once(url, method=method, headers=headers, params=params,
                         body=body, timeout=timeout)
        except HttpError as exc:
            attempt += 1
            if attempt > retries or not exc.transient:
                raise
            delay = 2 ** attempt
            log.info("%s -- retrying in %ss (%d/%d)",
                     exc, delay, attempt, retries)
            time.sleep(delay)


def _once(url, *, method, headers, params, body, timeout):
    if params:
        query = {k: v for k, v in params.items() if v is not None}
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(query)}"

    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": "station-curator/1.0"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise HttpError(
            f"{method} {redact(url)} -> {exc.code} {detail}", exc.code
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HttpError(f"{method} {redact(url)} -> {exc}") from exc

    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HttpError(f"{method} {redact(url)} -> invalid JSON: {exc}") from exc


def post_text(url: str, text: str, *, headers=None, timeout=15):
    """POST a plain-text body (ntfy). Returns the raw response bytes."""
    hdrs = {"Content-Type": "text/plain; charset=utf-8"}
    hdrs.update(headers or {})
    req = urllib.request.Request(
        url, data=text.encode("utf-8"), headers=hdrs, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "replace")
        raise HttpError(f"POST {redact(url)} -> {exc.code} {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HttpError(f"POST {redact(url)} -> {exc}") from exc