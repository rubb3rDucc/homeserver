"""
station-curator -- keeps each channel's collection fresh, on-charter and
rerun-safe, and shortlists what's worth acquiring next.

One cycle, in order:

  1. snapshot the playout window into the ledger (airings vanish when
     ErsatzTV rolls its window forward, so this has to happen first)
  2. expire finished cooldowns
  3. read the library; classify shows as serial/episodic; judge every
     episode for rerun safety
  4. per channel: retire what's gone stale, then fill back to target
  5. shortlist acquisitions, write proposals.md, push a summary

Collections come in three shapes, matching how you already use them:

  movie     a pool of films                  ("black films greatest hits")
  show      whole series, expanded by ErsatzTV ("anime", "kids shows")
  episode   individual episodes              ("superhero shows without finales")

Episode collections are the interesting case: the charter is matched against
each *show*, then only rerun-safe episodes of the shows that qualify are
scheduled -- no finales, no part 2 of a two-parter. Episodes are interleaved
across shows so a channel doesn't play six of one series back to back.
"""

import logging
import signal
import threading
import sys
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone

from . import discover, program, report, reruns, score
from .config import load_channels, load_env
from .ersatztv import ErsatzTV
from .jellyfin import fetch as fetch_watch
from .ledger import Ledger, utcnow
from .llm import LLM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("curator")

_stop = False


def serve_report(directory, port: int):
    """
    Serve the report over HTTP so it can be read in a browser.

    Reachable on the tailnet as http://optiplex:PORT -- no ssh, and the ntfy
    push links straight to it. Read-only, and it only ever exposes the state
    directory (report + ledger), which is a list of film titles.
    """
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.path = "/report.html"
            return super().do_GET()

        def log_message(self, *args):
            pass   # don't narrate every page view into the cycle log

    handler = partial(Handler, directory=str(directory))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("report served on port %d", port)
    return server


def _handle_signal(signum, _frame):
    global _stop
    _stop = True
    log.info("signal %s received -- finishing the current cycle", signum)


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #
def retirements(channel, members, ledger, watch, jellyfin_ids):
    """
    Decide what should leave the collection, worst offender first.

    Returns [(media_id, reason)]. Nothing is applied here -- the caller
    enforces min_size and max_churn.
    """
    fresh = channel.freshness
    airings = ledger.airing_counts(fresh.airing_window_days)
    ages = ledger.days_in_rotation(channel.collection)
    now = utcnow()

    out = []
    for media_id in members:
        played = airings.get(media_id, 0)
        age = ages.get(media_id, 0.0)

        if played >= fresh.max_airings:
            out.append((media_id, played, f"aired {played}x in "
                                          f"{fresh.airing_window_days}d"))
            continue
        if age >= fresh.max_days_in_rotation:
            out.append((media_id, played, f"{age:.0f}d in rotation"))
            continue

        item_id = jellyfin_ids.get(media_id)
        seen = watch.get(item_id) if item_id else None
        if seen and seen.last_played:
            days = (now - seen.last_played).total_seconds() / 86400
            if days <= fresh.rest_after_watch_days:
                out.append((media_id, played, f"watched {days:.0f}d ago"))

    # Retire the most-aired first; it's the one viewers are most sick of.
    out.sort(key=lambda row: -row[1])
    return [(media_id, reason) for media_id, _, reason in out]


# --------------------------------------------------------------------------- #
# Candidate pools
# --------------------------------------------------------------------------- #
def _interleave(groups):
    """Round-robin across groups so one show can't dominate the collection."""
    out = []
    pools = [list(g) for g in groups if g]
    while pools:
        for pool in list(pools):
            out.append(pool.pop(0))
            if not pool:
                pools.remove(pool)
    return out


def _spread(episode_ids):
    """
    Order a show's episodes so we don't always pick episode 1.

    Sorted by a stable hash of the media id: varied across shows, but
    identical on every run, so a rebuild doesn't reshuffle the channel.
    """
    return sorted(episode_ids, key=lambda mid: (mid * 2654435761) % 2147483647)


def dedupe_key(media_id, library, episodes_by_id) -> str:
    """
    Identity used to avoid scheduling the same thing twice.

    This library really does contain duplicates -- 'The Prince of Egypt' is in
    it three times under three media ids -- so identity has to be the title,
    not the id.
    """
    if media_id in episodes_by_id:
        ep = episodes_by_id[media_id]
        show = library.get(ep.show_id)
        name = show.title.lower() if show else str(ep.show_id)
        return f"{name}|s{ep.season}e{ep.number}"
    item = library.get(media_id)
    if item is None:
        return f"#{media_id}"
    return f"{item.title.strip().lower()}|{item.year or ''}"


def is_new(media_id, library, episodes_by_id, within_days) -> bool:
    """Was this acquired recently enough to deserve a slot on a full channel?"""
    if within_days <= 0:
        return False
    added = ""
    if media_id in episodes_by_id:
        added = episodes_by_id[media_id].added
    elif media_id in library:
        added = library[media_id].added
    if not added:
        return False
    try:
        when = datetime.fromisoformat(added.strip().replace(" ", "T")[:26])
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (utcnow() - when).total_seconds() <= within_days * 86400


def staleness_rank(members, ledger, channel):
    """
    Order incumbents worst-first, whether or not they meet a retirement rule.

    Retirement asks "has this earned a rest?". Intake asks a different
    question -- "if something must go to make room, which?" -- so it needs a
    ranking over everything, not just the ones over threshold.
    """
    airings = ledger.airing_counts(channel.freshness.airing_window_days)
    ages = ledger.days_in_rotation(channel.collection)
    return sorted(members,
                  key=lambda m: (-airings.get(m, 0), -ages.get(m, 0.0)))


def candidate_pool(channel, library, episodes_by_show, verdicts, serials):
    """
    Everything eligible for this channel, best first.

    For movie/show collections that's ranked library items. For episode
    collections the charter ranks *shows*, then their rerun-safe episodes are
    interleaved in show-rank order.

    A `show` collection can't exclude individual finales -- ErsatzTV expands
    the series and plays all of it -- but it can still honour skip_serialized
    by keeping story-arc shows out of the collection entirely. That is what
    a channel like "adult animation comedy non linear" is asking for.
    """
    if channel.kind == "show" and channel.reruns.skip_serialized:
        pool = [i for i in library.values()
                if i.kind == "show" and i.media_id not in serials]
        ranked = score.rank(pool, channel.charter, channel.weights,
                            channel.min_score)
        return [s.item.media_id for s in ranked]

    if channel.kind == "episode":
        shows = [i for i in library.values() if i.kind == "show"]
        ranked = score.rank(shows, channel.charter, channel.weights,
                            channel.min_score)
        groups = []
        for scored in ranked:
            safe = [
                ep.media_id
                for ep in episodes_by_show.get(scored.item.media_id, [])
                if verdicts.get(ep.media_id) and verdicts[ep.media_id].safe
            ]
            if safe:
                groups.append(_spread(safe))
        return _interleave(groups)

    pool = [i for i in library.values() if i.kind == channel.kind]
    ranked = score.rank(pool, channel.charter, channel.weights, channel.min_score)
    return [scored.item.media_id for scored in ranked]


def taste_filter(channel, llm, library, episodes_by_id, media_ids):
    """
    Drop shortlisted titles that don't actually belong, per the prose charter.

    Episodes are judged by their *show* -- the question "is this Batman: TAS a
    superhero cartoon" is answered once, not once per episode -- so an episode
    collection costs one verdict per series.
    """
    subjects = {}                      # media_id -> Item being judged
    for media_id in media_ids:
        if media_id in episodes_by_id:
            show = library.get(episodes_by_id[media_id].show_id)
            if show is not None:
                subjects[media_id] = show
        elif media_id in library:
            subjects[media_id] = library[media_id]

    unique = {item.media_id: item for item in subjects.values()}
    if not unique:
        return media_ids

    candidates = [
        {
            "id": str(item.media_id),
            "title": item.title,
            "year": item.year,
            "overview": ", ".join(
                sorted(item.genres | item.tags)
            ) + (f" [{item.rating}]" if item.rating else ""),
        }
        for item in unique.values()
    ]
    verdicts = llm.vibe_filter(
        channel.collection, channel.brief or channel.name, candidates,
        namespace="lib",
    )
    if not verdicts:
        return media_ids

    kept = []
    for media_id in media_ids:
        subject = subjects.get(media_id)
        if subject is None or verdicts.get(str(subject.media_id)) is not False:
            kept.append(media_id)
    dropped = len(media_ids) - len(kept)
    if dropped:
        log.info("    taste gate dropped %d candidate(s)", dropped)
    return kept


def detect_kind(members, library, episodes_by_id) -> str:
    """Infer a collection's shape from what's currently in it."""
    counts = defaultdict(int)
    for media_id in members:
        if media_id in episodes_by_id:
            counts["episode"] += 1
        elif media_id in library:
            counts[library[media_id].kind] += 1
    if not counts:
        return "movie"
    return max(counts.items(), key=lambda kv: kv[1])[0]


# --------------------------------------------------------------------------- #
# One channel
# --------------------------------------------------------------------------- #
def curate(channel, etv, ledger, library, episodes_by_show, episodes_by_id,
           verdicts, watch, jellyfin_ids, collections, llm, serials, gone):
    collection_id = collections.get(channel.collection)
    if collection_id is None:
        log.warning("ch%s: no ErsatzTV collection named %r -- skipped",
                    channel.number, channel.collection)
        return None

    members = etv.collection_items(collection_id)

    # Sweep out anything ErsatzTV can't actually play -- a missing file, or a
    # show with no episodes under it. This is not curation: neither can air,
    # and leaving them in silently costs a slot.
    ghosts = members & gone
    if ghosts:
        etv.remove_items(collection_id, ghosts)
        members -= ghosts
        log.warning("ch%s: removed %d unplayable item(s)",
                    channel.number, len(ghosts))

    ledger.sync_rotation(channel.collection, members)

    if channel.kind == "auto":
        kind = detect_kind(members, library, episodes_by_id)
        channel = replace(channel, kind=kind)
        log.info("ch%s: detected collection kind = %s", channel.number, kind)

    # ---- retire ---------------------------------------------------------- #
    leaving = retirements(channel, members, ledger, watch, jellyfin_ids)
    room = max(0, len(members) - channel.min_size)
    leaving = leaving[:min(len(leaving), room, channel.max_churn)]

    for media_id, reason in leaving:
        ledger.bench(channel.collection, media_id,
                     channel.freshness.cooldown_days, reason)
    if leaving:
        etv.remove_items(collection_id, [mid for mid, _ in leaving])
        members -= {mid for mid, _ in leaving}

    # ---- fill ------------------------------------------------------------ #
    benched = ledger.benched(channel.collection)
    pool = candidate_pool(channel, library, episodes_by_show, verdicts,
                          serials)
    wanted = max(0, channel.target_size - len(members))
    budget = max(0, channel.max_churn - len(leaving))

    # Identity is the title, not the media id: the library holds the same film
    # under several ids, and a channel must not play it twice.
    taken = {dedupe_key(mid, library, episodes_by_id) for mid in members}

    room = min(wanted, budget)
    # Everything eligible right now, best first. The head of this becomes the
    # shortlist; the tail is what the report shows as "next up" -- the queue
    # max_churn is deliberately pacing.
    eligible = []
    for media_id in pool:
        if len(eligible) >= max(room * 3, 20):
            break
        if media_id in members or media_id in benched:
            continue
        key = dedupe_key(media_id, library, episodes_by_id)
        if key in taken:
            continue
        taken.add(key)
        eligible.append(media_id)
    shortlisted = eligible[: room * (3 if channel.taste_gate else 1)]

    # A taste-gated charter shortlists three times what it needs and lets
    # Claude strike the ones that only look right on paper.
    if channel.taste_gate and shortlisted and llm.enabled:
        shortlisted = taste_filter(channel, llm, library, episodes_by_id,
                                   shortlisted)
        # The gate can approve only one or two shows out of the shortlist,
        # which would otherwise hand the channel eight episodes of the same
        # series in a row. Re-interleave what survived.
        if channel.kind == "episode":
            by_show = {}
            for mid in shortlisted:
                ep = episodes_by_id.get(mid)
                by_show.setdefault(ep.show_id if ep else mid, []).append(mid)
            shortlisted = _interleave(by_show.values())

    joining = shortlisted[:room]

    if joining:
        etv.add_items(collection_id, joining)
        ledger.note_added(channel.collection, joining)

    def label(media_id):
        if media_id in episodes_by_id:
            ep = episodes_by_id[media_id]
            show = library.get(ep.show_id)
            name = show.title if show else f"show#{ep.show_id}"
            return f"{name} S{ep.season}E{ep.number} {ep.title}".strip()
        item = library.get(media_id)
        return item.label() if item else f"#{media_id}"

    # ---- intake: let genuinely new arrivals onto a full channel ---------- #
    displaced = []
    spare = channel.max_churn - len(leaving) - len(joining)
    if (channel.intake.max_displacements > 0 and spare > 0
            and len(members) + len(joining) >= channel.target_size):
        # Dedupe against what is actually in the collection, not `taken` --
        # that set also holds everything the eligible scan merely *looked at*,
        # which would hide every new arrival that scoring had already ranked.
        held = {dedupe_key(m, library, episodes_by_id)
                for m in list(members) + joining}
        arrivals = [
            mid for mid in pool
            if mid not in members and mid not in joining
            and mid not in benched
            and is_new(mid, library, episodes_by_id,
                       channel.intake.new_within_days)
            and dedupe_key(mid, library, episodes_by_id) not in held
        ]
        room = min(len(arrivals), channel.intake.max_displacements, spare,
                   max(0, len(members) - channel.min_size))
        if room:
            for old, new in zip(staleness_rank(members, ledger, channel),
                                arrivals[:room]):
                ledger.bench(channel.collection, old,
                             channel.intake.displaced_cooldown_days,
                             "made room for new arrival")
                displaced.append(old)
                joining.append(new)
                taken.add(dedupe_key(new, library, episodes_by_id))
            etv.remove_items(collection_id, displaced)
            etv.add_items(collection_id, joining[-room:])
            ledger.note_added(channel.collection, joining[-room:])
            members -= set(displaced)

    # ---- program the order ------------------------------------------------ #
    final = (members | set(joining))
    if channel.programming.order == program.HANDBACK:
        if etv.uses_custom_order(collection_id):
            etv.clear_custom_order(collection_id)
            log.info("ch%s: handed ordering back to ErsatzTV", channel.number)
    else:
        # A collection holding whole shows can't carry a custom order --
        # ErsatzTV expands them at build time and the ordering enumerator
        # then fails, killing the channel's playout entirely.
        expanding = etv.expands_at_playout(collection_id)
        if expanding and channel.programming.order in program.CURATED:
            log.warning(
                "ch%s: %d entr(ies) in %r are whole shows, which ErsatzTV "
                "expands at playout -- a custom order would break the build, "
                "so leaving ErsatzTV's playback order. Make the collection "
                "episodes-only to program it.",
                channel.number, expanding, channel.collection)
            if etv.uses_custom_order(collection_id):
                etv.clear_custom_order(collection_id)
                log.warning("ch%s: cleared the custom order that was set",
                            channel.number)
            ordered = None
        else:
            ordered = program.arrange(
                channel, final, library=library,
                episodes_by_id=episodes_by_id,
                airings=ledger.airing_counts(
                    channel.freshness.airing_window_days),
            )
        if ordered:
            wrote = etv.set_custom_order(collection_id, ordered)
            log.info("ch%s: %s %s", channel.number,
                     "programmed" if wrote else "would program",
                     program.describe(channel, ordered))

    log.info("ch%s %-34s %3d -> %3d  (-%d +%d)",
             channel.number, channel.collection, len(members) + len(leaving),
             len(members) + len(joining), len(leaving), len(joining))
    for media_id, reason in leaving:
        log.info("    retired  %s  [%s]", label(media_id), reason)
    for media_id in joining:
        log.info("    added    %s%s", label(media_id),
                 "  [new arrival]" if is_new(
                     media_id, library, episodes_by_id,
                     channel.intake.new_within_days) else "")
    for media_id in displaced:
        log.info("    displaced %s  [to make room for new content]",
                 label(media_id))

    resting = ledger.benched(channel.collection)
    return {
        "number": channel.number,
        "name": channel.name,
        "collection": channel.collection,
        "kind": channel.kind,
        "size": len(members) + len(joining),
        "target": channel.target_size,
        "order": (f"programmed: {channel.programming.order}"
                  if channel.programming.order in program.CURATED
                  else "ErsatzTV playback order"),
        "taste_gate": channel.taste_gate,
        # What the channel actually plays -- the context an LLM needs to
        # suggest more of the same. Titles only, deduped for episodes.
        "playing": sorted({(library[m].title if m in library
                            else label(m).split(" S")[0])
                           for m in (members | set(joining))}),
        "added": [(label(m), "") for m in joining],
        "retired": [(label(m), r) for m, r in leaving],
        "next_up": [(label(m), "") for m in eligible[len(joining):][:12]],
        "resting": [(label(m), r) for m, r in resting.items()],
    }


# --------------------------------------------------------------------------- #
# Acquisition shortlist
# --------------------------------------------------------------------------- #
def shortlist(channel, env, library, ledger, llm, owned, playing, budget):
    """
    Suggest titles to acquire. `budget` is a mutable [n] of remaining LLM
    suggestion calls this cycle -- a free-tier key has a daily quota, and
    twelve channels asking every six hours exhausts it.
    """
    if not channel.discover.enabled:
        return []
    kind = "movie" if channel.kind == "movie" else "show"
    seen = ledger.already_proposed(f"tmdb-{kind}", channel.collection)

    if channel.discover.source == "llm":
        if not llm.enabled or budget[0] <= 0:
            return []
        before = llm.calls
        found = [c for c in discover.suggested_for(
            channel, env, llm, library, playing, owned[kind])
            if c["id"] not in seen]
        # Only a cache miss costs a call; a cached answer is free.
        if llm.calls > before:
            budget[0] -= 1
    else:
        if not env.tmdb_key:
            return []
        found = [c for c in discover.candidates_for(
            channel, env, library, owned[kind]) if c["id"] not in seen]
    if not found:
        return []

    found = found[: channel.discover.limit]
    verdicts = llm.vibe_filter(channel.collection, channel.brief or channel.name,
                               found) if channel.brief else {}

    added = []
    for cand in found:
        # Unjudged candidates are kept: the LLM narrows, it doesn't gatekeep.
        if verdicts.get(cand["id"]) is False:
            continue
        reason = cand.get("why") or ""
        if cand.get("rating"):
            score_txt = f"{cand['rating']}/10 from {cand['votes']} votes"
            reason = f"{reason} ({score_txt})" if reason else score_txt
        if ledger.propose(f"tmdb-{kind}", cand["id"], channel.collection,
                          cand["title"], cand["year"],
                          cand.get("rating") or 0.0, reason):
            added.append((channel.collection, cand["title"], cand["year"]))
    return added


def _push_pages(proposed, total, names=None, per_page=8, max_pages=8):
    """
    Split the proposals into notification-sized pages.

    A phone shade truncates a long body, so rather than cutting the list off
    the titles are paginated: each push carries a handful, and they keep
    coming until the list is exhausted. Pages are numbered so a gap is
    obvious if one goes missing.

    `max_pages` is a spam guard -- past it, the remainder is left to the
    report rather than filling your lock screen.
    """
    names = names or {}
    by_channel = {}
    for collection, title, year in proposed:
        label = f"{title} ({year})" if year else title
        by_channel.setdefault(names.get(collection, collection),
                              []).append(label)

    # Flatten to lines, keeping a channel header above its own titles.
    lines = []
    for channel, titles in by_channel.items():
        lines.append(("head", channel))
        lines += [("item", t) for t in titles]

    pages, current, items = [], [], 0
    for kind, text in lines:
        if items >= per_page and kind == "head":
            pages.append(current)
            current, items = [], 0
        current.append((kind, text))
        if kind == "item":
            items += 1
            if items >= per_page:
                pages.append(current)
                current, items = [], 0
                # Carry the channel header onto the next page for context,
                # without stacking "(cont.)" every time a channel spans
                # three or more pages.
                head = next((t for k, t in reversed(pages[-1])
                             if k == "head"), None)
                if head:
                    base = head.removesuffix(" (cont.)")
                    current.append(("head", f"{base} (cont.)"))
    if current and any(k == "item" for k, _ in current):
        pages.append(current)

    dropped = 0
    if len(pages) > max_pages:
        dropped = sum(1 for page in pages[max_pages:]
                      for k, _ in page if k == "item")
        pages = pages[:max_pages]

    out = []
    for index, page in enumerate(pages, 1):
        body = []
        for kind, text in page:
            body.append(text + ":" if kind == "head" else f"  \u2022 {text}")
        if index == len(pages):
            if dropped:
                body.append(f"\n+{dropped} more in report.md")
            body.append(f"\n{total} outstanding")
        out.append((f"New titles ({index}/{len(pages)})", "\n".join(body)))
    return out


# --------------------------------------------------------------------------- #
# Cycle
# --------------------------------------------------------------------------- #
def cycle(env, channels):
    etv = ErsatzTV(env.ersatztv_db, dry_run=env.dry_run)
    ledger = Ledger(env.state_db)
    try:
        stored = ledger.record_airings(etv.airings())
        expired = ledger.expire_cooldowns()
        log.info("snapshot: %d new airing(s), %d cooldown(s) expired",
                 stored, expired)

        library = etv.library()
        episodes = etv.episodes()
        # Deleted/moved content: never a candidate, and swept out of any
        # collection it is still sitting in.
        gone = etv.unplayable()
        library = {k: v for k, v in library.items() if k not in gone}
        episodes = [e for e in episodes if e.media_id not in gone]
        episodes_by_id = {ep.media_id: ep for ep in episodes}
        episodes_by_show = defaultdict(list)
        for ep in episodes:
            episodes_by_show[ep.show_id].append(ep)
        for eps in episodes_by_show.values():
            eps.sort(key=lambda e: (e.season, e.number or 0))

        # A show can exist with nothing under it: Jellyfin matches a series
        # folder whose season dirs are empty (Living Single), or every episode
        # file is gone while the series entry survives. ErsatzTV keeps the Show
        # itself in Normal state either way, so the sweep above can't see it --
        # but it expands to no episodes at playout, so it occupies a slot on a
        # `show` channel and airs nothing. Treat it as unplayable too, which
        # also frees it to be proposed for acquisition again.
        hollow = {
            media_id for media_id, item in library.items()
            if item.kind == "show" and not episodes_by_show.get(media_id)
        }
        empty_shows = sorted(library[m].label() for m in hollow)
        if hollow:
            log.warning("library: %d show(s) with no episodes -- %s",
                        len(hollow), ", ".join(empty_shows))
            gone |= hollow
            library = {k: v for k, v in library.items() if k not in hollow}

        # Anything you acted on since last cycle: drop the suggestion. The
        # title itself is picked up by ordinary charter scoring below.
        for got in discover.retire_acquired(ledger, library):
            log.info("acquired: %s -- suggestion cleared", got)

        llm = LLM(ledger, env.llm_model, env.llm_key, env.llm_provider)
        shows = [i for i in library.values() if i.kind == "show"]
        serials = llm.serialized_shows(shows)

        # Rerun rules vary per channel, but classification does not; judge with
        # the strictest rules present so a single pass serves every channel.
        verdicts = reruns.analyse(
            episodes,
            max((c.reruns for c in channels),
                key=lambda r: (r.skip_finales, r.skip_multipart,
                               r.skip_serialized, r.skip_premieres)),
            serials,
        )
        log.info("reruns: %s", reruns.summarise(verdicts))

        watch = fetch_watch(env.jellyfin_url, env.jellyfin_key)
        jellyfin_ids = etv.jellyfin_item_ids()
        collections = etv.collections()

        owned = {
            "movie": discover.arr_tmdb_ids(env.radarr_url, env.radarr_key,
                                           "movie"),
            "show": discover.arr_tmdb_ids(env.sonarr_url, env.sonarr_key,
                                          "show"),
        }

        proposed = []
        rows = []
        suggest_budget = [env.suggest_per_cycle]
        for channel in channels:
            row = curate(channel, etv, ledger, library, episodes_by_show,
                         episodes_by_id, verdicts, watch, jellyfin_ids,
                         collections, llm, serials, gone)
            if row:
                rows.append(row)
            proposed += shortlist(channel, env, library, ledger, llm, owned,
                                  row["playing"] if row else [],
                                  suggest_budget)

        sources = {c.discover.source for c in channels if c.discover.enabled}
        if "llm" in sources and not llm.enabled:
            note = ("_No LLM key set, so the curator can't suggest titles "
                    "outside your library. Set `GEMINI_API_KEY`._")
        elif "llm" in sources and not (env.radarr_key or env.sonarr_key):
            note = ("_Suggestions are generated but can't be verified: set "
                    "`RADARR_API_KEY` / `SONARR_API_KEY` so the curator can "
                    "confirm a title really exists before proposing it._")
        elif "tmdb" in sources and not env.tmdb_key:
            note = ("_No `TMDB_API_KEY` set for the channels using the tmdb "
                    "source._")
        else:
            note = "_Nothing outstanding._"
        report.write(env.report, rows, ledger.proposals(), note,
                     empty_shows=empty_shows)
        log.info("report -> %s", env.report)

        total = len(ledger.proposals())
        if proposed:
            log.info("%d new proposal(s); %d outstanding -> %s",
                     len(proposed), total, env.report)
            pages = _push_pages(
                proposed, total,
                {c.collection: c.name for c in channels},
                per_page=env.ntfy_per_push)
            for subject, body in pages:
                discover.notify(env.ntfy_url, subject, body,
                                click=env.report_url)
                if len(pages) > 1:
                    time.sleep(1)   # be kind to ntfy.sh
        if llm.calls:
            log.info("%s calls this cycle: %d", llm.provider, llm.calls)
    finally:
        etv.close()
        ledger.close()


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    env = load_env()
    channels = load_channels(env.charters)
    if env.report_port:
        try:
            serve_report(env.report.parent, env.report_port)
        except OSError as exc:
            log.warning("could not serve the report (%s)", exc)
    log.info("station-curator up. %d channel(s), every %ss%s",
             len(channels), env.interval, "  [DRY RUN]" if env.dry_run else "")

    while not _stop:
        started = time.monotonic()
        # Re-read the charters each cycle, so an edit applies without a
        # restart. A file that doesn't parse keeps the last good config
        # rather than taking the service down -- charters are hand-edited,
        # and a missing comma should cost you one cycle, not the daemon.
        try:
            channels = load_channels(env.charters)
        except Exception as exc:  # noqa: BLE001
            log.error("charters.toml not usable (%s) -- keeping the previous "
                      "config; fix it and the next cycle picks it up", exc)
        try:
            cycle(env, channels)
        except Exception:  # keep the daemon alive across a bad cycle
            log.exception("cycle failed")
        if _stop:
            break
        time.sleep(max(0.0, env.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
