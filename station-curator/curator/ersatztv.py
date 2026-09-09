"""
Everything that touches ErsatzTV's database.

Reads: the library (movies, shows, episodes with genres/tags), collection
membership, and the current playout window.

Writes: `CollectionItem` rows, and nothing else. That restraint is deliberate --
collections are a two-column join table, and ErsatzTV tracks a collection etag
so it notices membership changes and rebuilds affected playouts by itself. Its
scheduling tables, by contrast, are stateful and version-specific. So blocks,
templates, schedules and filler presets stay exactly as you configured them in
the UI; they simply consume the collections we keep fresh.

The database is WAL-mode, so our short writes coexist with the running
container. Every connection sets a generous busy_timeout anyway.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("curator.ersatztv")


@dataclass(frozen=True)
class Item:
    """One schedulable thing in the library."""
    media_id: int
    kind: str                # "movie" | "show"
    title: str
    year: int | None
    genres: frozenset        # lowercased
    tags: frozenset          # lowercased TMDB keywords
    rating: str = ""         # ContentRating: R, PG-13, TV-MA, TV-Y7, ...
    added: str = ""          # DateAdded -- how "recently acquired" is known

    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


@dataclass(frozen=True)
class Episode:
    media_id: int
    show_id: int
    season: int
    number: int | None
    title: str
    added: str = ""


def _parse_dt(raw: str) -> datetime | None:
    """
    ErsatzTV writes 'YYYY-MM-DD HH:MM:SS.fffffff' (UTC, no offset).

    Normalised to an aware UTC datetime so it can be compared with, and stored
    alongside, the curator's own ISO timestamps.
    """
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


class ErsatzTV:
    def __init__(self, path: Path, dry_run: bool = False):
        if not path.exists():
            raise FileNotFoundError(f"ErsatzTV database not found at {path}")
        self.path = path
        self.dry_run = dry_run
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout = 30000")

    def close(self):
        self.db.close()

    # ---- library ---------------------------------------------------------- #
    def _facets(self, table: str, metadata: str, id_expr: str) -> dict[int, set]:
        """Genre/Tag names per media id (small library -> one query each)."""
        out: dict[int, set] = {}
        for row in self.db.execute(
            f"SELECT {id_expr} AS media_id, f.Name AS name FROM {table} f "
            f"JOIN {metadata} md ON md.Id = f.{metadata}Id "
            f"WHERE f.Name IS NOT NULL"
        ):
            name = row["name"].strip().lower()
            out.setdefault(row["media_id"], set()).add(name)
        return out

    def library(self) -> dict[int, Item]:
        """Every movie and show, keyed by media id (== MediaItem.Id)."""
        items: dict[int, Item] = {}

        movie_genres = self._facets("Genre", "MovieMetadata", "md.MovieId")
        movie_tags = self._facets("Tag", "MovieMetadata", "md.MovieId")
        for row in self.db.execute(
            "SELECT m.Id AS media_id, mm.Title AS title, mm.Year AS year, "
            "       mm.ContentRating AS rating, mm.DateAdded AS added "
            "FROM Movie m JOIN MovieMetadata mm ON mm.MovieId = m.Id"
        ):
            mid = row["media_id"]
            items[mid] = Item(
                media_id=mid,
                kind="movie",
                title=row["title"] or f"#{mid}",
                year=row["year"],
                genres=frozenset(movie_genres.get(mid, ())),
                tags=frozenset(movie_tags.get(mid, ())),
                rating=(row["rating"] or "").strip().upper(),
                added=(row["added"] or ""),
            )

        show_genres = self._facets("Genre", "ShowMetadata", "md.ShowId")
        show_tags = self._facets("Tag", "ShowMetadata", "md.ShowId")
        for row in self.db.execute(
            "SELECT s.Id AS media_id, sm.Title AS title, sm.Year AS year, "
            "       sm.ContentRating AS rating, sm.DateAdded AS added "
            "FROM Show s JOIN ShowMetadata sm ON sm.ShowId = s.Id"
        ):
            mid = row["media_id"]
            items[mid] = Item(
                media_id=mid,
                kind="show",
                title=row["title"] or f"#{mid}",
                year=row["year"],
                genres=frozenset(show_genres.get(mid, ())),
                tags=frozenset(show_tags.get(mid, ())),
                rating=(row["rating"] or "").strip().upper(),
                added=(row["added"] or ""),
            )
        return items

    def episodes(self) -> list[Episode]:
        """Every episode with its show/season -- input to rerun safety."""
        return [
            Episode(
                media_id=row["media_id"],
                show_id=row["show_id"],
                season=row["season"] if row["season"] is not None else 0,
                number=row["number"],
                title=(row["title"] or "").strip(),
                added=(row["added"] or ""),
            )
            for row in self.db.execute(
                "SELECT e.Id AS media_id, s.ShowId AS show_id, "
                "       s.SeasonNumber AS season, em.EpisodeNumber AS number, "
                "       em.Title AS title, em.DateAdded AS added "
                "FROM Episode e "
                "JOIN Season s ON s.Id = e.SeasonId "
                "JOIN EpisodeMetadata em ON em.EpisodeId = e.Id"
            )
        ]

    def jellyfin_item_ids(self) -> dict[int, str]:
        """media_id -> Jellyfin ItemId, for joining on-demand watch data."""
        out = {}
        for sql in (
            "SELECT Id AS media_id, ItemId FROM JellyfinMovie",
            "SELECT Id AS media_id, ItemId FROM JellyfinShow",
        ):
            for row in self.db.execute(sql):
                if row["ItemId"]:
                    out[row["media_id"]] = row["ItemId"]
        return out

    # ErsatzTV's MediaItemState: 0 Normal, 1 FileNotFound, 2 Unavailable,
    # 3 RemoteOnly. This library is Jellyfin-sourced, so 3 is the normal case
    # for most of it -- only 1 and 2 mean "cannot be played".
    UNPLAYABLE_STATES = (1, 2)

    def unplayable(self) -> set[int]:
        """
        Media ids ErsatzTV can no longer play -- deleted or moved files.

        Deleting content doesn't remove it from a collection: ErsatzTV marks
        the item FileNotFound and leaves the CollectionItem alone, so the
        entry lingers, occupies a slot against target_size, and can even get
        scheduled -- which is dead air.
        """
        placeholders = ",".join("?" * len(self.UNPLAYABLE_STATES))
        return {
            row["Id"] for row in self.db.execute(
                f"SELECT Id FROM MediaItem WHERE State IN ({placeholders})",
                self.UNPLAYABLE_STATES,
            )
        }

    # ---- collections ------------------------------------------------------ #
    def collections(self) -> dict[str, int]:
        """Collection name -> id. Names are unique (ErsatzTV enforces it)."""
        return {
            row["Name"]: row["Id"]
            for row in self.db.execute("SELECT Id, Name FROM Collection")
            if row["Name"]
        }

    def collection_items(self, collection_id: int) -> set[int]:
        return {
            row["MediaItemId"]
            for row in self.db.execute(
                "SELECT MediaItemId FROM CollectionItem WHERE CollectionId = ?",
                (collection_id,),
            )
        }

    def add_items(self, collection_id: int, media_ids) -> int:
        media_ids = list(media_ids)
        if not media_ids or self.dry_run:
            return 0
        before = self.db.total_changes
        self.db.executemany(
            "INSERT OR IGNORE INTO CollectionItem "
            "(CollectionId, MediaItemId, CustomIndex) VALUES (?, ?, NULL)",
            [(collection_id, mid) for mid in media_ids],
        )
        self.db.commit()
        return self.db.total_changes - before

    def remove_items(self, collection_id: int, media_ids) -> int:
        media_ids = list(media_ids)
        if not media_ids or self.dry_run:
            return 0
        before = self.db.total_changes
        self.db.executemany(
            "DELETE FROM CollectionItem "
            "WHERE CollectionId = ? AND MediaItemId = ?",
            [(collection_id, mid) for mid in media_ids],
        )
        self.db.commit()
        return self.db.total_changes - before

    # ---- programmed order ------------------------------------------------- #
    def expands_at_playout(self, collection_id: int) -> int:
        """
        Count collection entries that are a Show or Season rather than a
        directly playable item.

        This matters because ErsatzTV's CustomOrderCollectionEnumerator maps
        collection items to resolved media items one-for-one. A Show entry
        expands into its episodes at build time, the expansion has no
        CollectionItem of its own, and the enumerator throws
        "Sequence contains no matching element" -- taking the whole channel's
        playout down. So a mixed collection cannot carry a custom order.
        """
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM CollectionItem ci "
            "WHERE ci.CollectionId = ? AND ("
            "  ci.MediaItemId IN (SELECT Id FROM Show) OR"
            "  ci.MediaItemId IN (SELECT Id FROM Season))",
            (collection_id,),
        ).fetchone()
        return row["n"] if row else 0

    def set_custom_order(self, collection_id: int, ordered_ids) -> bool:
        """
        Write an explicit playback sequence as CollectionItem.CustomIndex.

        ErsatzTV honours this only when the collection's UseCustomPlaybackOrder
        flag is set, which is what makes this safe to adopt: the flag is off on
        every collection here today, so nothing changes until a charter asks
        for it. Setting it overrides the schedule item's PlaybackOrder for that
        collection.
        """
        if self.dry_run:
            return False
        with self.db:
            self.db.executemany(
                "UPDATE CollectionItem SET CustomIndex = ? "
                "WHERE CollectionId = ? AND MediaItemId = ?",
                [(index, collection_id, mid)
                 for index, mid in enumerate(ordered_ids)],
            )
            self.db.execute(
                "UPDATE Collection SET UseCustomPlaybackOrder = 1 WHERE Id = ?",
                (collection_id,),
            )
        return True

    def clear_custom_order(self, collection_id: int) -> bool:
        """Hand ordering back to ErsatzTV's own PlaybackOrder."""
        if self.dry_run:
            return False
        with self.db:
            self.db.execute(
                "UPDATE Collection SET UseCustomPlaybackOrder = 0 WHERE Id = ?",
                (collection_id,),
            )
            self.db.execute(
                "UPDATE CollectionItem SET CustomIndex = NULL "
                "WHERE CollectionId = ?",
                (collection_id,),
            )
        return True

    def uses_custom_order(self, collection_id: int) -> bool:
        row = self.db.execute(
            "SELECT UseCustomPlaybackOrder AS flag FROM Collection WHERE Id = ?",
            (collection_id,),
        ).fetchone()
        return bool(row and row["flag"])

    # ---- playout ---------------------------------------------------------- #
    def airings(self):
        """
        (media_id, channel_number, iso_start) for real programming.

        FillerKind = 0 is content; 1 is filler/pre-roll (your junction and
        feature cards), which must not count as an airing of anything.
        """
        rows = []
        for row in self.db.execute(
            "SELECT pi.MediaItemId AS media_id, ch.Number AS channel, "
            "       pi.Start AS start "
            "FROM PlayoutItem pi "
            "JOIN Playout p ON p.Id = pi.PlayoutId "
            "JOIN Channel ch ON ch.Id = p.ChannelId "
            "WHERE pi.FillerKind = 0"
        ):
            started = _parse_dt(row["start"])
            if started is None:
                continue
            rows.append(
                (row["media_id"], row["channel"],
                 started.replace(microsecond=0).isoformat())
            )
        return rows
