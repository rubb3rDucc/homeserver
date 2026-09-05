"""
Programming order -- the sequence, not just the membership.

ErsatzTV already has a PlaybackOrder on every schedule item, and it is good:
Chronological, Random, Shuffle, ShuffleInOrder, MultiEpisodeShuffle,
SeasonEpisode, RandomRotation and Marathon (with MarathonBatchSize /
MarathonGroupBy for "N episodes of one show, then the next show"). Anything
ErsatzTV can already express should be set there, in the UI, not here --
duplicating it in the curator would just be a second source of truth.

What ErsatzTV cannot do is order a collection by *what has actually aired*,
because it doesn't keep that history -- the curator does. So these orders
exist:

  rested       least-aired first, so the channel leads with what you haven't
               seen lately
  heavy        most-aired first -- the hits, deliberately
  doubles      N consecutive in-order episodes of a show, then the next show;
               like Marathon, but the episodes are the rerun-safe ones, so a
               pair never straddles a finale or splits a two-parter
  sequential   in-order within each show, shows in charter rank
  shuffle      deterministic shuffle -- stable across runs, so a rebuild
               doesn't reshuffle the channel underneath a playout

The order is written as CollectionItem.CustomIndex with the collection's
UseCustomPlaybackOrder flag set. That lane is currently unused on this server
(zero rows carry a CustomIndex), so the curator can take it without disturbing
anything already configured.
"""

import logging

log = logging.getLogger("curator.program")

# Orders the curator writes itself.
CURATED = {"rested", "heavy", "doubles", "sequential", "shuffle", "era"}
# Sentinels: leave ErsatzTV's own playback order alone / hand control back.
NONE = "none"
HANDBACK = "ersatztv"

VALID = CURATED | {NONE, HANDBACK}


def _stable_shuffle(media_ids, seed_text: str):
    """
    Deterministic shuffle keyed on the collection name.

    Deliberately not random.shuffle(): the same membership must produce the
    same order on every run, or each cycle would reshuffle a channel that
    ErsatzTV has already built a playout from.
    """
    seed = 0
    for char in seed_text:
        seed = (seed * 131 + ord(char)) & 0xFFFFFFFF
    return sorted(
        media_ids,
        key=lambda mid: ((mid * 2654435761) ^ seed) % 4294967291,
    )


def _by_show(media_ids, episodes_by_id):
    """Group episode ids by show, each group in true season/episode order."""
    groups: dict[int, list] = {}
    for media_id in media_ids:
        episode = episodes_by_id.get(media_id)
        if episode is None:
            continue
        groups.setdefault(episode.show_id, []).append(episode)
    for episodes in groups.values():
        episodes.sort(key=lambda e: (e.season, e.number or 0))
    return groups


def arrange(channel, media_ids, *, library, episodes_by_id, airings):
    """
    Return `media_ids` in programmed order, or None to leave ErsatzTV's own
    playback order alone.

    `airings` is media_id -> times aired, from the curator's ledger.
    """
    order = (channel.programming.order or NONE).lower()
    if order in (NONE, HANDBACK) or not media_ids:
        return None
    if order not in CURATED:
        log.warning("ch%s: unknown programming order %r -- leaving ErsatzTV's",
                    channel.number, order)
        return None

    ids = list(media_ids)

    if order == "shuffle":
        return _stable_shuffle(ids, channel.collection)

    if order == "era":
        # Period-accurate programming: drift forward through time instead of
        # cutting from 1985 to 2024 and back. Grouped by decade, shuffled
        # within it, so a block feels of-its-era without being predictable.
        def year_of(mid):
            item = library.get(mid)
            if item is None and mid in episodes_by_id:
                item = library.get(episodes_by_id[mid].show_id)
            return (item.year or 0) if item else 0
        spread = {m: i for i, m in
                  enumerate(_stable_shuffle(ids, channel.collection))}
        return sorted(ids, key=lambda m: (year_of(m) // 10, spread[m]))

    if order in ("rested", "heavy"):
        # Ties broken by the stable shuffle so equally-aired titles don't
        # clump by media id (which correlates with when they were imported).
        spread = {mid: i for i, mid in
                  enumerate(_stable_shuffle(ids, channel.collection))}
        reverse = order == "heavy"
        return sorted(
            ids,
            key=lambda mid: (-airings.get(mid, 0) if reverse
                             else airings.get(mid, 0), spread[mid]),
        )

    # sequential / doubles -- episode-shaped orders.
    groups = _by_show(ids, episodes_by_id)
    if not groups:
        # A movie or show collection: "in order" means by year, then title.
        def key(mid):
            item = library.get(mid)
            return (item.year or 0, item.title.lower()) if item else (0, "")
        return sorted(ids, key=key)

    # Shows visit in a stable but non-alphabetical order, so the channel
    # doesn't always open with the same series.
    show_order = _stable_shuffle(list(groups), channel.collection)
    batch = max(1, channel.programming.batch) if order == "doubles" else 10**9

    out = []
    cursors = {show_id: 0 for show_id in show_order}
    remaining = True
    while remaining:
        remaining = False
        for show_id in show_order:
            episodes = groups[show_id]
            start = cursors[show_id]
            if start >= len(episodes):
                continue
            chunk = episodes[start:start + batch]
            out.extend(e.media_id for e in chunk)
            cursors[show_id] = start + len(chunk)
            remaining = remaining or cursors[show_id] < len(episodes)

    # Anything without episode metadata (a whole show or a movie mixed into an
    # episode collection) keeps its place at the end rather than being lost.
    seen = set(out)
    out.extend(mid for mid in ids if mid not in seen)
    return out


def describe(channel, ordered) -> str:
    order = channel.programming.order
    if order == "doubles":
        return f"doubles(batch={channel.programming.batch}) over {len(ordered)}"
    return f"{order} over {len(ordered)}"
