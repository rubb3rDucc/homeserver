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

Both land in one report so there's a single file to look at, plus what is
currently resting on cooldown and why -- which is the other question worth
being able to answer ("where did that film go?").
"""

from datetime import datetime, timezone


def _fmt(entry) -> str:
    title, why = entry
    return f"- **{title}**" + (f" — {why}" if why else "")


def write(path, channels, proposals, provider_note=""):
    """
    Render the full report.

    `channels` is a list of dicts from the curation pass; `proposals` are
    ledger rows for titles not in the library yet.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    out = [
        "# Curator report",
        "",
        f"_Generated {now.isoformat()}_",
        "",
        "**Worth acquiring** is the part that needs you. Everything below it "
        "is what the curator is already doing on its own.",
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

    out += ["---", "", "## Channel detail", "",
            "_What each channel holds now, what it would add next from titles "
            "you already own, and what is resting. Nothing here needs acting "
            "on._", ""]
    for ch in channels:
        head = (f"### {ch['number']} — {ch['name']}\n"
                f"`{ch['collection']}` · {ch['size']}/{ch['target']} items · "
                f"{ch['kind']} · {ch['order']}")
        out += [head, ""]

        if ch["added"]:
            out += ["**Just added**", ""]
            out += [_fmt(e) for e in ch["added"]] + [""]
        if ch["retired"]:
            out += ["**Just retired**", ""]
            out += [_fmt(e) for e in ch["retired"]] + [""]

        if ch["next_up"]:
            label = "**Next up** — clears the charter, waiting on room/pacing"
            if ch["taste_gate"]:
                label += " (a taste check runs before these are added)"
            out += [label, ""]
            out += [_fmt(e) for e in ch["next_up"]] + [""]
        elif ch["size"] >= ch["target"]:
            out += ["_At target; nothing queued._", ""]
        else:
            out += ["_Nothing else in the library clears this charter._", ""]

        if ch["resting"]:
            out += ["<details><summary>Resting "
                    f"({len(ch['resting'])})</summary>", ""]
            out += [_fmt(e) for e in ch["resting"]] + ["", "</details>", ""]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return len(channels)
