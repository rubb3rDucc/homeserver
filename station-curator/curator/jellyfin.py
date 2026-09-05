"""
Jellyfin watch data -- the "have I actually watched this lately" signal.

Airplay tells you what the channel showed; this tells you what *you* chose to
watch on demand. A title you just watched is a poor thing to put back into
heavy rotation, so recent viewing rests it for a while.

The join is exact rather than fuzzy: every item in this library was synced from
Jellyfin, so ErsatzTV stores the Jellyfin ItemId alongside its own media id
(see ErsatzTV.jellyfin_item_ids). No title matching anywhere.

Entirely optional. Without JELLYFIN_API_KEY, or if Jellyfin is unreachable,
the curator logs it and carries on with the other staleness signals.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .net import HttpError, request

log = logging.getLogger("curator.jellyfin")


@dataclass(frozen=True)
class Watch:
    play_count: int
    last_played: datetime | None


def _parse(raw: str | None) -> datetime | None:
    """
    Parse a Jellyfin timestamp.

    Jellyfin writes .NET tick precision ('...T12:00:00.0000000Z') -- seven
    fractional digits, one more than datetime.fromisoformat accepts, so the
    fraction is trimmed to six before parsing.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fetch(base_url: str, api_key: str) -> dict[str, Watch]:
    """
    Jellyfin ItemId -> Watch, aggregated across every user on the server.

    Aggregating means a title watched by anyone counts as watched; play counts
    take the maximum and last-played the most recent.
    """
    if not api_key:
        log.info("no JELLYFIN_API_KEY -- skipping watch data")
        return {}

    headers = {"X-Emby-Token": api_key}
    try:
        users = request(f"{base_url}/Users", headers=headers) or []
    except HttpError as exc:
        log.warning("jellyfin users unavailable (%s) -- skipping watch data", exc)
        return {}

    out: dict[str, Watch] = {}
    for user in users:
        uid = user.get("Id")
        if not uid:
            continue
        try:
            payload = request(
                f"{base_url}/Users/{uid}/Items",
                headers=headers,
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie,Series",
                    "Fields": "UserData",
                    "EnableTotalRecordCount": "false",
                },
            )
        except HttpError as exc:
            log.warning("jellyfin items for user %s failed: %s", uid, exc)
            continue

        for entry in (payload or {}).get("Items", []):
            item_id = entry.get("Id")
            data = entry.get("UserData") or {}
            if not item_id:
                continue
            count = int(data.get("PlayCount") or 0)
            when = _parse(data.get("LastPlayedDate"))
            if count == 0 and when is None:
                continue
            existing = out.get(item_id)
            if existing is None:
                out[item_id] = Watch(count, when)
            else:
                out[item_id] = Watch(
                    max(existing.play_count, count),
                    max(
                        [d for d in (existing.last_played, when) if d],
                        default=None,
                    ),
                )
    log.info("jellyfin: watch data for %d item(s)", len(out))
    return out