"""
Finding things you don't own yet -- as proposals, never as downloads.

The curator suggests; you decide. Nothing here adds a movie to Radarr, grabs a
release, or spends a byte of disk. Candidates are written to a markdown
shortlist and (optionally) pushed to your phone via ntfy.

Dedupe is layered, because a proposal you already own is noise:
  1. Radarr / Sonarr know every title they manage, with TMDB ids -- exact.
  2. The ErsatzTV library is checked on normalised title + year, since this
     library carries no TMDB ids of its own (MetadataGuid is empty here).
  3. The ledger remembers everything already proposed, so a title you passed
     on doesn't come back next cycle.

TMDB Discover needs a free read-only API key (TMDB_API_KEY). Without one this
module is skipped entirely and the rest of the curator runs unchanged.
"""

import logging
import re

from .net import HttpError, post_text, request

log = logging.getLogger("curator.discover")

TMDB = "https://api.themoviedb.org/3"


def normalise(title: str) -> str:
    """Fold a title for comparison: lowercase, no punctuation, no articles."""
    text = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower())
    text = re.sub(r"^(the|a|an)\s+", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# What the *arrs already manage
# --------------------------------------------------------------------------- #
def arr_tmdb_ids(base_url: str, api_key: str, kind: str) -> set[str]:
    """TMDB ids known to Radarr (movies) or Sonarr (series)."""
    if not api_key:
        return set()
    path = "movie" if kind == "movie" else "series"
    try:
        payload = request(
            f"{base_url}/api/v3/{path}", headers={"X-Api-Key": api_key}
        )
    except HttpError as exc:
        log.warning("%s unreachable (%s) -- dedupe falls back to the "
                    "library", kind, exc)
        return set()
    ids = set()
    for entry in payload or []:
        tmdb = entry.get("tmdbId")
        if tmdb:
            ids.add(str(tmdb))
    return ids


# --------------------------------------------------------------------------- #
# TMDB Discover
# --------------------------------------------------------------------------- #
def _discover(api_key: str, kind: str, cfg, years) -> list[dict]:
    """One page of TMDB Discover, normalised into plain dicts."""
    is_movie = kind == "movie"
    params = {
        "api_key": api_key,
        "include_adult": "false",
        "sort_by": "vote_average.desc",
        "vote_count.gte": cfg.min_votes,
        "vote_average.gte": cfg.min_rating,
        "page": 1,
    }
    if cfg.tmdb_genres:
        params["with_genres"] = "|".join(str(g) for g in cfg.tmdb_genres)
    if cfg.tmdb_keywords:
        params["with_keywords"] = "|".join(str(k) for k in cfg.tmdb_keywords)
    if years:
        low, high = years
        date_key = "primary_release_date" if is_movie else "first_air_date"
        params[f"{date_key}.gte"] = f"{low}-01-01"
        params[f"{date_key}.lte"] = f"{high}-12-31"

    endpoint = f"{TMDB}/discover/{'movie' if is_movie else 'tv'}"
    try:
        payload = request(endpoint, params=params)
    except HttpError as exc:
        log.warning("tmdb discover failed: %s", exc)
        return []

    out = []
    for entry in (payload or {}).get("results", []):
        title = entry.get("title") if is_movie else entry.get("name")
        date = (entry.get("release_date") if is_movie
                else entry.get("first_air_date"))
        if not title:
            continue
        year = None
        if date and len(date) >= 4 and date[:4].isdigit():
            year = int(date[:4])
        out.append({
            "id": str(entry.get("id")),
            "title": title,
            "year": year,
            "overview": entry.get("overview") or "",
            "rating": entry.get("vote_average"),
            "votes": entry.get("vote_count"),
        })
    return out


def candidates_for(channel, env, library, owned_ids) -> list[dict]:
    """
    Fresh candidates for one channel, already deduped against what you own.

    Returns dicts as produced by _discover, minus anything in Radarr/Sonarr,
    already in the ErsatzTV library, or previously proposed.
    """
    if not env.tmdb_key or not channel.discover.enabled:
        return []

    kind = channel.kind if channel.kind in ("movie", "show") else "movie"
    found = _discover(env.tmdb_key, kind, channel.discover, channel.charter.years)
    if not found:
        return []

    have = {
        (normalise(item.title), item.year)
        for item in library.values()
        if item.kind == kind
    }
    have_titles = {title for title, _ in have}

    fresh = []
    for cand in found:
        if cand["id"] in owned_ids:
            continue
        key = normalise(cand["title"])
        # Same title and year is certainly a match; same title alone is
        # near-certain in a library this size, so treat it as owned too.
        if (key, cand["year"]) in have or key in have_titles:
            continue
        fresh.append(cand)
    return fresh


def retire_acquired(ledger, library) -> list[str]:
    """
    Drop suggestions for titles that are now in the library.

    This is what closes the loop: you act on a shortlist entry, the file lands,
    Jellyfin scans it, ErsatzTV syncs it, and on the next cycle the curator
    both removes the suggestion and -- through ordinary charter scoring --
    adds the title to the channel that asked for it. Nothing to tick off by
    hand, and the shortlist stays short.
    """
    owned = {(normalise(i.title), i.year) for i in library.values()}
    owned_titles = {title for title, _ in owned}

    acquired = []
    for row in ledger.proposals():
        key = normalise(row["title"])
        if (key, row["year"]) in owned or key in owned_titles:
            ledger.forget_proposal(row["source"], row["ext_id"],
                                   row["collection"])
            acquired.append(f"{row['title']} -> {row['collection']}")
    return acquired


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def write_shortlist(path, ledger) -> int:
    """Render every outstanding proposal as a markdown shortlist."""
    rows = ledger.proposals()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Curator shortlist",
        "",
        "Titles that fit a channel charter but aren't in the library yet.",
        "Nothing here has been downloaded -- add what you want in Radarr/Sonarr.",
        "",
    ]
    current = None
    for row in rows:
        if row["collection"] != current:
            current = row["collection"]
            lines += ["", f"## {current}", ""]
        year = f" ({row['year']})" if row["year"] else ""
        kind = "movie" if row["source"].endswith("movie") else "tv"
        link = f"https://www.themoviedb.org/{kind}/{row['ext_id']}"
        lines.append(
            f"- **{row['title']}**{year} — {row['reason']}  \n  {link}"
        )
    if not rows:
        lines.append("_Nothing outstanding._")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def ntfy_endpoint(url: str) -> str:
    """
    Accept either ntfy URL style found in this repo's .env.

    diskmon sends through shoutrrr, which wants `ntfy://ntfy.sh/topic`. The
    curator POSTs directly, which needs `https://ntfy.sh/topic`. Both live in
    the same .env, so accept either rather than failing silently on a
    copy-paste. A bare topic name works too.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("ntfy://"):
        return "https://" + url[len("ntfy://"):]
    if not url.startswith(("http://", "https://")):
        return "https://ntfy.sh/" + url.lstrip("/")
    return url


def notify(ntfy_url: str, title: str, message: str):
    """Best-effort push. A failed notification never fails a run."""
    endpoint = ntfy_endpoint(ntfy_url)
    if not endpoint:
        return
    try:
        post_text(endpoint, message, headers={"Title": title})
    except HttpError as exc:
        log.warning("ntfy push failed: %s", exc)