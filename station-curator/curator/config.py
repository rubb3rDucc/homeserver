"""
Configuration: environment variables + charters.toml.

charters.toml is the only file you normally edit. It is TOML rather than YAML
so it stays hand-editable (comments!) while parsing with stdlib `tomllib`
-- no PyYAML, no dependency.

Every [[channel]] inherits [defaults] and overrides only what it names, so a
charter is usually a dozen lines. Unknown keys are warned about rather than
ignored, because a silent typo in a hand-edited charter is a bad failure mode.
"""

import logging
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

log = logging.getLogger("curator.config")


# --------------------------------------------------------------------------- #
# Charter pieces
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Freshness:
    """When a title has been on air long enough to deserve a rest."""
    max_airings: int = 4            # airings within the window before retiring
    airing_window_days: int = 30
    max_days_in_rotation: int = 60  # hard age-out regardless of airings
    cooldown_days: int = 90         # how long a retired title stays benched
    rest_after_watch_days: int = 21  # rest if watched on demand this recently


@dataclass(frozen=True)
class Weights:
    genre: float = 1.0
    keyword: float = 1.5   # keywords are far more specific than genres
    year: float = 1.0


@dataclass(frozen=True)
class Reruns:
    """
    Rules for picking episodes that stand on their own.

    A rerun block should not open on a finale or drop you into part 2 of a
    two-parter. These flags apply to `kind = "show"` channels only.
    """
    skip_finales: bool = True
    skip_premieres: bool = False
    skip_multipart: bool = True
    skip_serialized: bool = True    # skip shows classified as story-arc serials


@dataclass(frozen=True)
class Charter:
    """What belongs on the channel, in metadata terms."""
    genres: tuple = ()              # any match scores
    any_genres: tuple = ()          # at least one must be present, else reject
    require_genres: tuple = ()      # all must be present, else rejected
    exclude_genres: tuple = ()      # any match rejects
    keywords: tuple = ()            # TMDB keyword tags; any match scores
    any_keywords: tuple = ()        # at least one must be present, else reject
    require_keywords: tuple = ()    # all must be present, else rejected
    exclude_keywords: tuple = ()
    # ContentRating gate. Your library populates this on ~98% of titles
    # (R / PG-13 / PG / G, TV-MA / TV-14 / TV-PG / TV-Y7), which makes it the
    # reliable way to keep a kids channel free of late-night animation.
    ratings: tuple = ()             # allowlist; empty means "any"
    exclude_ratings: tuple = ()     # denylist
    years: tuple | None = None      # [min, max] inclusive
    years_strict: bool = False      # True = outside the window is rejected


@dataclass(frozen=True)
class Programming:
    """
    How the collection is ordered.

    Default "none" leaves ErsatzTV's own PlaybackOrder untouched -- prefer
    that for anything ErsatzTV can already express (Random, Shuffle,
    ShuffleInOrder, SeasonEpisode, Marathon). The curated orders exist only
    for what needs airplay history or rerun safety. See program.py.
    Set "ersatztv" to hand a previously-curated collection back.
    """
    order: str = "none"
    batch: int = 2      # episodes per show before moving on ("doubles")


@dataclass(frozen=True)
class Discover:
    """How to look for things you don't own yet (proposals only)."""
    enabled: bool = True
    # "llm"  -> ask the model what belongs, then verify every title through
    #           Radarr/Sonarr lookup. Better taste than genre filters, and
    #           needs no TMDB key (the *arrs already hold that credential).
    # "tmdb" -> TMDB Discover with the charter's genre/keyword filters.
    #           Needs TMDB_API_KEY.
    source: str = "llm"
    tmdb_genres: tuple = ()         # TMDB genre ids
    tmdb_keywords: tuple = ()       # TMDB keyword ids
    min_votes: int = 200
    min_rating: float = 6.0
    limit: int = 10                 # max proposals per channel per run


@dataclass(frozen=True)
class ChannelCfg:
    number: str
    name: str
    collection: str                 # ErsatzTV Collection name -- the join key
    brief: str = ""                 # prose charter, used for LLM taste calls
    # movie   -> films
    # show    -> whole series, expanded by ErsatzTV
    # episode -> individual rerun-safe episodes (no finales, no two-parters)
    # auto    -> infer from what the collection already holds
    kind: str = "auto"
    target_size: int = 40
    min_size: int = 10              # never shrink a collection below this
    min_score: float = 3.0
    max_churn: int = 6              # max adds+removes per run, per channel
    # Some charters simply cannot be written in TMDB metadata -- "Black
    # American cinema" and "superhero" are subject matter, not genres. For
    # those, scoring proposes and Claude confirms before anything joins.
    # Cached per title forever, so a channel costs a handful of calls once.
    # Without an API key the gate is skipped and scoring stands alone.
    taste_gate: bool = False
    charter: Charter = Charter()
    freshness: Freshness = Freshness()
    weights: Weights = Weights()
    reruns: Reruns = Reruns()
    programming: Programming = Programming()
    discover: Discover = Discover()


# --------------------------------------------------------------------------- #
# TOML loading
# --------------------------------------------------------------------------- #
_NESTED = {
    "charter": Charter,
    "freshness": Freshness,
    "weights": Weights,
    "reruns": Reruns,
    "programming": Programming,
    "discover": Discover,
}


def _merge(base: dict, over: dict) -> dict:
    """Recursive dict merge; `over` wins. Neither input is mutated."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _build(cls, data: dict, where: str):
    """Instantiate a frozen dataclass, warning about keys it doesn't define."""
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    for key in sorted(unknown):
        log.warning("charters.toml: unknown key %r in %s -- ignored", key, where)
    kwargs = {}
    for key, value in data.items():
        if key not in known:
            continue
        # Lists become tuples so the dataclasses stay hashable/frozen.
        kwargs[key] = tuple(value) if isinstance(value, list) else value
    return cls(**kwargs)


def load_channels(path: Path) -> list[ChannelCfg]:
    """Parse charters.toml into ChannelCfg objects with [defaults] applied."""
    with open(path, "rb") as handle:
        doc = tomllib.load(handle)

    defaults = doc.get("defaults", {})
    channels = []
    for raw in doc.get("channel", []):
        merged = _merge(defaults, raw)
        where = f"channel {merged.get('number', '?')}"

        nested = {}
        for key, cls in _NESTED.items():
            nested[key] = _build(cls, merged.pop(key, {}) or {}, f"{where}.{key}")

        # `years` is written as a 2-element list in TOML; normalise to a tuple.
        charter = nested["charter"]
        if charter.years is not None and len(charter.years) != 2:
            raise ValueError(f"{where}: charter.years must be [min, max]")

        channels.append(_build(ChannelCfg, {**merged, **nested}, where))

    names = [c.collection for c in channels]
    for name in set(names):
        if names.count(name) > 1:
            raise ValueError(f"charters.toml: collection {name!r} claimed twice")
    return channels


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Env:
    ersatztv_db: Path
    state_db: Path
    charters: Path
    proposals: Path
    report: Path
    interval: int
    suggest_per_cycle: int
    dry_run: bool
    jellyfin_url: str
    jellyfin_key: str
    radarr_url: str
    radarr_key: str
    sonarr_url: str
    sonarr_key: str
    tmdb_key: str
    ntfy_url: str
    llm_model: str
    gemini_key: str
    anthropic_key: str
    llm_provider: str

    @property
    def llm_key(self) -> str:
        return (self.gemini_key if self.llm_provider == "gemini"
                else self.anthropic_key)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_key)


def load_env() -> Env:
    env = os.environ.get
    return Env(
        ersatztv_db=Path(env("ERSATZTV_DB", "/ersatztv/ersatztv.sqlite3")),
        state_db=Path(env("CURATOR_STATE_DB", "/state/curator.sqlite3")),
        charters=Path(env("CURATOR_CHARTERS", "/app/charters.toml")),
        proposals=Path(env("CURATOR_PROPOSALS", "/state/proposals.md")),
        report=Path(env("CURATOR_REPORT", "/state/report.md")),
        interval=int(env("CURATOR_INTERVAL", "21600")),   # 6h
        # Channels asking for new-title ideas per cycle. Each is one LLM
        # call, cached for a week, so 3 covers all 12 channels comfortably
        # while staying inside a free-tier daily quota.
        suggest_per_cycle=int(env("CURATOR_SUGGEST_PER_CYCLE", "3")),
        dry_run=_flag("CURATOR_DRY_RUN", False),
        jellyfin_url=env("JELLYFIN_URL", "http://jellyfin:8096").rstrip("/"),
        jellyfin_key=env("JELLYFIN_API_KEY", ""),
        radarr_url=env("RADARR_URL", "http://radarr:7878").rstrip("/"),
        radarr_key=env("RADARR_API_KEY", ""),
        sonarr_url=env("SONARR_URL", "http://sonarr:8989").rstrip("/"),
        sonarr_key=env("SONARR_API_KEY", ""),
        tmdb_key=env("TMDB_API_KEY", ""),
        ntfy_url=env("CURATOR_NTFY_URL", ""),
        # Blank model = the provider's own default (see llm.LLM).
        llm_model=env("CURATOR_LLM_MODEL", ""),
        gemini_key=env("GEMINI_API_KEY", ""),
        anthropic_key=env("ANTHROPIC_API_KEY", ""),
        # Gemini is the default provider; an Anthropic key only wins when it
        # is the only one set. CURATOR_LLM_PROVIDER overrides either way.
        llm_provider=(
            env("CURATOR_LLM_PROVIDER")
            or ("anthropic"
                if env("ANTHROPIC_API_KEY") and not env("GEMINI_API_KEY")
                else "gemini")
        ).strip().lower(),
    )
