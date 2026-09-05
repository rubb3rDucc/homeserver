"""
Charter scoring: how well does a title fit a channel?

Deliberately a plain weighted sum over metadata you already have. Your library
carries full TMDB genres *and* keyword tags ("gangster", "coming of age",
"adult animation", "based on manga"), which is specific enough that taste
modelling isn't needed to decide whether a film belongs on "BET Films but not
ass". Keywords outweigh genres because "Crime" describes a thousand films while
"heist" describes the one you meant.

Hard rules reject outright; everything else accumulates points. A title joins a
channel when it clears `min_score`.
"""

import logging
from dataclasses import dataclass

log = logging.getLogger("curator.score")


@dataclass(frozen=True)
class Scored:
    item: object          # ersatztv.Item
    points: float
    why: str              # human-readable, shown in logs and proposals


def _lower(values) -> set:
    return {str(v).strip().lower() for v in values if str(v).strip()}


def evaluate(item, charter, weights) -> Scored | None:
    """
    Score one item against one charter.

    Returns None when a hard rule rejects it, otherwise a Scored with the
    reasons that earned the points.
    """
    genres = item.genres
    tags = item.tags

    # ---- hard rules ---------------------------------------------------- #
    if genres & _lower(charter.exclude_genres):
        return None
    if tags & _lower(charter.exclude_keywords):
        return None

    required = _lower(charter.require_genres)
    if required and not required <= genres:
        return None

    # "at least one of these genres" -- the gate that separates a drama
    # channel from every comedy that happens to carry a Crime tag.
    gate = _lower(charter.any_genres)
    if gate and not (genres & gate):
        return None

    required = _lower(charter.require_keywords)
    if required and not required <= tags:
        return None

    # `any_keywords` is the tightener that stops a channel defined by subject
    # matter (superhero animation, Black cinema) from swallowing everything
    # that merely shares its genres.
    gate = _lower(charter.any_keywords)
    if gate and not (tags & gate):
        return None

    rating = (getattr(item, "rating", "") or "").upper()
    allowed = {r.upper() for r in charter.ratings}
    if allowed and rating not in allowed:
        return None
    if rating and rating in {r.upper() for r in charter.exclude_ratings}:
        return None

    in_window = None
    if charter.years:
        low, high = charter.years
        in_window = item.year is not None and low <= item.year <= high
        if charter.years_strict and not in_window:
            return None

    # ---- points --------------------------------------------------------- #
    points = 0.0
    why = []

    hit_genres = genres & _lower(charter.genres)
    if hit_genres:
        points += len(hit_genres) * weights.genre
        why.append("genres: " + ", ".join(sorted(hit_genres)))

    hit_tags = tags & _lower(charter.keywords)
    if hit_tags:
        points += len(hit_tags) * weights.keyword
        why.append("keywords: " + ", ".join(sorted(hit_tags)))

    if in_window:
        points += weights.year
        why.append(f"year {item.year} in window")

    if points <= 0:
        return None
    return Scored(item=item, points=points, why="; ".join(why))


def rank(items, charter, weights, min_score: float) -> list[Scored]:
    """
    Score a pool of items, keep those clearing min_score, best first.

    Ties break on title so a run is reproducible rather than dependent on
    dict ordering -- a rebuild shouldn't reshuffle the channel for no reason.
    """
    scored = []
    for item in items:
        result = evaluate(item, charter, weights)
        if result is not None and result.points >= min_score:
            scored.append(result)
    scored.sort(key=lambda s: (-s.points, s.item.title.lower()))
    return scored