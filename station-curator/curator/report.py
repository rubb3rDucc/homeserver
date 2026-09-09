"""
The human-readable view of what the curator is thinking.

Two different questions get asked about "recommendations", and they have
different answers:

  what could go on a channel   -- things already in the library that clear the
                                  charter but haven't been added yet, usually
                                  because max_churn paces additions. This needs
                                  nothing but the library.
  what should be acquired      -- things not in the library at all, from TMDB
                                  Discover. Needs TMDB_API_KEY.

The report covers only the second: what to acquire. What each channel
currently holds, what it would add next and what is resting are all visible
in ErsatzTV's own schedule, so repeating them here was noise.
"""

from datetime import datetime, timezone


def write(path, channels, proposals, provider_note="", empty_shows=()):
    """
    Render the full report.

    `channels` is a list of dicts from the curation pass; `proposals` are
    ledger rows for titles not in the library yet; `empty_shows` are series
    the library lists but holds no episodes of.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    out = [
        "# Curator report",
        "",
        f"_Generated {now.isoformat()}_",
        "",
        "Titles worth adding, per channel. Everything else the curator does "
        "-- rotation, ordering, rerun safety -- it does on its own; the "
        "schedule shows the result.",
        "",
    ]

    # Acquisitions first: this is the part that needs a human, so it leads.
    out += ["## Worth acquiring", ""]
    if proposals:
        out += ["Not in your library yet. Add what you want in Radarr/Sonarr "
                "(or drop the file in and let Jellyfin scan it) — the curator "
                "sees it on the next cycle, clears it from this list, and puts "
                "it on the channel automatically.", ""]
        by_channel = {}
        for row in proposals:
            by_channel.setdefault(row["collection"], []).append(row)
        for name, rows in by_channel.items():
            ch = next((c for c in channels if c["collection"] == name), None)
            heading = (f"### {ch['number']} — {ch['name']}" if ch
                       else f"### {name}")
            out += [heading, f"`{name}`", ""]
            for row in rows:
                year = f" ({row['year']})" if row["year"] else ""
                kind = "movie" if row["source"].endswith("movie") else "tv"
                out.append(
                    f"- **{row['title']}**{year} — {row['reason']}  \n"
                    f"  https://www.themoviedb.org/{kind}/{row['ext_id']}"
                )
            out.append("")
    else:
        out += [provider_note or "_Nothing outstanding._", ""]

    # Shells: the library says you have the show, but there is nothing under
    # it to play. Only you can settle which it is -- fetch the episodes, or
    # delete the folder -- so it belongs in the report rather than the log.
    if empty_shows:
        out += [
            "## In the library, but empty", "",
            "These series exist in Jellyfin with no episode files behind "
            "them, so they can't air. The curator keeps them off the "
            "channels; get the episodes or remove the folder.", "",
        ]
        out += [f"- {title}" for title in empty_shows]
        out.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    markdown = "\n".join(out) + "\n"
    path.write_text(markdown, encoding="utf-8")
    # A browser-readable copy next to it, so the report can be opened over
    # the tailnet instead of catted over ssh.
    path.with_suffix(".html").write_text(_html(markdown), encoding="utf-8")
    return len(channels)


_CSS = """
:root { color-scheme: light dark; }
body { font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 0 auto; padding: 1.25rem 1rem 4rem; max-width: 46rem;
       background: #fbfbfa; color: #1a1a1a; }
@media (prefers-color-scheme: dark) {
  body { background: #16161a; color: #e8e8ea; }
  a { color: #7fb0ff; } h2 { border-color: #33333a !important; }
  li { border-color: #2a2a30 !important; }
}
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 .75rem; padding-bottom: .3rem;
     border-bottom: 1px solid #e2e2df; }
h3 { font-size: 1rem; margin: 1.4rem 0 .4rem; }
ul { list-style: none; padding: 0; margin: 0; }
li { padding: .55rem 0; border-bottom: 1px solid #ececea; }
a { color: #0b57d0; text-decoration: none; word-break: break-all; }
code { background: rgba(127,127,127,.16); padding: .1rem .35rem;
       border-radius: 4px; font-size: .85em; }
em { opacity: .75; }
hr { border: 0; border-top: 1px solid #ddd; margin: 2.5rem 0; }
details { margin: .5rem 0; }
summary { cursor: pointer; opacity: .75; }
"""


def _inline(text: str) -> str:
    """Escape, then apply the small subset of markdown the report emits."""
    import html
    import re
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"_(.+?)_", r"<em>\1</em>", text)
    text = re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', text)
    return text


def _html(markdown: str) -> str:
    """Render the report to a standalone, phone-friendly page."""
    body, in_list = [], False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_inline(line[2:].strip())}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("### "):
            body.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            body.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("---"):
            body.append("<hr>")
        elif line.startswith("<details") or line.startswith("</details"):
            body.append(line)
        else:
            body.append(f"<p>{_inline(line)}</p>")
    if in_list:
        body.append("</ul>")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,"
        "initial-scale=1\"><title>Curator report</title>"
        f"<style>{_CSS}</style></head><body>"
        + "\n".join(body) + "</body></html>"
    )
