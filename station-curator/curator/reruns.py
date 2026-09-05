"""
Rerun safety: which episodes stand on their own.

A rerun block should never open on a finale or drop a viewer into part 2 of a
two-parter. Three signals decide it, all computed from metadata you already
have -- no LLM involved:

  multi-part   the title carries a part marker: "The Cat and the Claw (1)",
               "Lost in Parking Space, Part One", "Gangstalicious: Part 2"
  finale       the last episode of a season, or "Finale" in the title; the
               last episode of the last season is a series finale
  premiere     episode 1 of a season (off by default -- premieres usually
               stand alone fine, unlike the two-parters that often open a run)

The regexes are the fiddly part. Matching the substring "Part" is wrong: this
library contains "Drunken Office Party", "Runaway Float / Partners" and "The
Aptitude Test", none of which are multi-part episodes. Every pattern below is
word-anchored, and PART_RE requires an actual part *number* after the word, so
"Part of the Family" is not a false positive either.

Whether a show is serialised at all is a separate judgement -- see llm.py.
Episodic shows only lose their finales and two-parters; serialised shows can be
excluded outright via `reruns.skip_serialized`.
"""

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("curator.reruns")

# "Part One" / "Part 2" / "Part III" -- a part word must be followed by a
# number word, digit or roman numeral, which is what keeps "Party",
# "Partners" and "Part of the Family" out.
_ORDINALS = r"one|two|three|four|five|six|1st|2nd|3rd|4th"
_ROMAN = r"i{1,3}v?|iv|v"
PART_RE = re.compile(
    rf"\bpart\s*(?:{_ORDINALS}|\d+|{_ROMAN})\b|\bpt\.?\s*\d+\b",
    re.IGNORECASE,
)

# A trailing "(1)" / "(2)" -- the Batman: TAS and Justice League convention.
PAREN_PART_RE = re.compile(r"\(\s*(\d{1,2})\s*\)\s*$")

FINALE_RE = re.compile(r"\bfinale\b", re.IGNORECASE)

# Conclusion markers that mean the same thing as a finale for rerun purposes.
CONCLUSION_RE = re.compile(
    r"\b(?:series|season)\s+(?:finale|ender)\b|\bthe\s+end\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Verdict:
    media_id: int
    safe: bool
    reason: str  # empty when safe


def part_number(title: str) -> int | None:
    """Return the part number of a multi-part episode title, else None."""
    paren = PAREN_PART_RE.search(title)
    if paren:
        return int(paren.group(1))
    if PART_RE.search(title):
        return 0  # a part, but the number isn't worth parsing precisely
    return None


def analyse(episodes, rules, serialized_shows=frozenset()) -> dict[int, Verdict]:
    """
    Judge every episode. Returns media_id -> Verdict.

    `episodes` are ersatztv.Episode records; `rules` is a config.Reruns;
    `serialized_shows` is a set of show media ids known to be story-arc serials.
    """
    # Season 0 is specials -- never counted when locating a season/series end.
    real = [ep for ep in episodes if ep.season > 0 and ep.number is not None]

    last_ep: dict[tuple[int, int], int] = {}
    last_season: dict[int, int] = {}
    for ep in real:
        key = (ep.show_id, ep.season)
        last_ep[key] = max(last_ep.get(key, 0), ep.number)
        last_season[ep.show_id] = max(last_season.get(ep.show_id, 0), ep.season)

    verdicts: dict[int, Verdict] = {}
    for ep in episodes:
        reason = _judge(ep, rules, serialized_shows, last_ep, last_season)
        verdicts[ep.media_id] = Verdict(ep.media_id, reason == "", reason)
    return verdicts


def _judge(ep, rules, serialized_shows, last_ep, last_season) -> str:
    """Return a rejection reason, or '' if the episode is safe to rerun."""
    if rules.skip_serialized and ep.show_id in serialized_shows:
        return "serialized show"

    title = ep.title or ""

    if rules.skip_multipart and part_number(title) is not None:
        return "multi-part"

    if rules.skip_finales:
        if FINALE_RE.search(title) or CONCLUSION_RE.search(title):
            return "finale (by title)"
        if ep.season > 0 and ep.number is not None:
            end_of_season = last_ep.get((ep.show_id, ep.season))
            if end_of_season is not None and ep.number == end_of_season:
                if last_season.get(ep.show_id) == ep.season:
                    return "series finale"
                return "season finale"

    if rules.skip_premieres and ep.season > 0 and ep.number == 1:
        return "premiere"

    return ""


def summarise(verdicts: dict[int, Verdict]) -> str:
    """One-line breakdown of why episodes were held back, for the log."""
    counts: dict[str, int] = {}
    for verdict in verdicts.values():
        if not verdict.safe:
            counts[verdict.reason] = counts.get(verdict.reason, 0) + 1
    if not counts:
        return "all episodes rerun-safe"
    parts = ", ".join(f"{n} {why}" for why, n in sorted(counts.items()))
    safe = sum(1 for v in verdicts.values() if v.safe)
    return f"{safe} rerun-safe; held back: {parts}"