"""
The curator's own state, in its own SQLite file.

ErsatzTV's database is treated as *its* data: we read the library and we edit
collection membership, but everything the curator needs to remember lives here
instead. That keeps our bookkeeping out of a schema that migrates between
ErsatzTV releases, and makes the whole thing recoverable -- delete this file and
the curator rebuilds from a clean slate.

The one thing that genuinely must be persisted is airplay history: ErsatzTV
prunes PlayoutItem rows as the playout window rolls forward, so airings are
snapshotted here before they disappear.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("curator.ledger")

SCHEMA = """
CREATE TABLE IF NOT EXISTS airing (
    media_id   INTEGER NOT NULL,
    channel    TEXT    NOT NULL,
    started    TEXT    NOT NULL,
    PRIMARY KEY (media_id, channel, started)
);
CREATE INDEX IF NOT EXISTS ix_airing_media ON airing (media_id, started);

CREATE TABLE IF NOT EXISTS rotation (
    collection TEXT    NOT NULL,
    media_id   INTEGER NOT NULL,
    added_at   TEXT    NOT NULL,
    PRIMARY KEY (collection, media_id)
);

CREATE TABLE IF NOT EXISTS cooldown (
    collection TEXT    NOT NULL,
    media_id   INTEGER NOT NULL,
    until      TEXT    NOT NULL,
    reason     TEXT    NOT NULL,
    PRIMARY KEY (collection, media_id)
);

CREATE TABLE IF NOT EXISTS proposal (
    source     TEXT    NOT NULL,   -- "tmdb-movie" | "tmdb-show"
    ext_id     TEXT    NOT NULL,   -- TMDB id
    collection TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    year       INTEGER,
    score      REAL,
    reason     TEXT,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (source, ext_id, collection)
);

-- Answers we never want to pay for twice (LLM charter drafts + taste calls).
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # ---- airplay history -------------------------------------------------- #
    def record_airings(self, rows) -> int:
        """
        Persist (media_id, channel, start) tuples; returns rows newly stored.

        Called every cycle against ErsatzTV's current playout window; the
        primary key makes re-snapshotting an overlapping window a no-op.
        """
        rows = list(rows)
        if not rows:
            return 0
        before = self.db.total_changes
        self.db.executemany(
            "INSERT OR IGNORE INTO airing (media_id, channel, started) "
            "VALUES (?,?,?)",
            rows,
        )
        self.db.commit()
        return self.db.total_changes - before

    def airing_counts(self, window_days: int) -> dict[int, int]:
        """media_id -> airings inside the window. Future airings don't count."""
        now = utcnow()
        since = _iso(now - timedelta(days=window_days))
        rows = self.db.execute(
            "SELECT media_id, COUNT(*) AS n FROM airing "
            "WHERE started >= ? AND started <= ? GROUP BY media_id",
            (since, _iso(now)),
        ).fetchall()
        return {row["media_id"]: row["n"] for row in rows}

    # ---- rotation --------------------------------------------------------- #
    def sync_rotation(self, collection: str, media_ids: set[int]):
        """
        Reconcile the rotation table with what is actually in the collection.

        Anything in ErsatzTV but unknown here (e.g. you added a title by hand in
        the UI) gets an added_at of now, so it ages from first sight rather than
        being retired the moment the curator notices it.
        """
        known = {
            row["media_id"]
            for row in self.db.execute(
                "SELECT media_id FROM rotation WHERE collection = ?",
                (collection,),
            )
        }
        now = _iso(utcnow())
        added = [(collection, mid, now) for mid in media_ids - known]
        if added:
            self.db.executemany(
                "INSERT OR IGNORE INTO rotation (collection, media_id, added_at) "
                "VALUES (?,?,?)",
                added,
            )
        gone = known - media_ids
        if gone:
            self.db.executemany(
                "DELETE FROM rotation WHERE collection = ? AND media_id = ?",
                [(collection, mid) for mid in gone],
            )
        self.db.commit()

    def days_in_rotation(self, collection: str) -> dict[int, float]:
        now = utcnow()
        out = {}
        for row in self.db.execute(
            "SELECT media_id, added_at FROM rotation WHERE collection = ?",
            (collection,),
        ):
            try:
                added = datetime.fromisoformat(row["added_at"])
            except ValueError:
                continue
            out[row["media_id"]] = (now - added).total_seconds() / 86400
        return out

    def note_added(self, collection: str, media_ids):
        now = _iso(utcnow())
        self.db.executemany(
            "INSERT OR REPLACE INTO rotation (collection, media_id, added_at) "
            "VALUES (?,?,?)",
            [(collection, mid, now) for mid in media_ids],
        )
        self.db.commit()

    # ---- cooldown --------------------------------------------------------- #
    def bench(self, collection: str, media_id: int, days: int, reason: str):
        until = _iso(utcnow() + timedelta(days=days))
        self.db.execute(
            "INSERT OR REPLACE INTO cooldown "
            "(collection, media_id, until, reason) "
            "VALUES (?,?,?,?)",
            (collection, media_id, until, reason),
        )
        self.db.execute(
            "DELETE FROM rotation WHERE collection = ? AND media_id = ?",
            (collection, media_id),
        )
        self.db.commit()

    def benched(self, collection: str) -> dict[int, str]:
        """media_id -> reason, for cooldowns that have not yet expired."""
        rows = self.db.execute(
            "SELECT media_id, reason FROM cooldown "
            "WHERE collection = ? AND until > ?",
            (collection, _iso(utcnow())),
        ).fetchall()
        return {row["media_id"]: row["reason"] for row in rows}

    def expire_cooldowns(self) -> int:
        before = self.db.total_changes
        self.db.execute(
            "DELETE FROM cooldown WHERE until <= ?", (_iso(utcnow()),)
        )
        self.db.commit()
        return self.db.total_changes - before

    # ---- proposals -------------------------------------------------------- #
    def propose(self, source, ext_id, collection, title, year, score,
                reason) -> bool:
        """Record an acquisition suggestion. False if it was already proposed."""
        before = self.db.total_changes
        self.db.execute(
            "INSERT OR IGNORE INTO proposal "
            "(source, ext_id, collection, title, year, score, reason, "
            "created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (source, str(ext_id), collection, title, year, score, reason,
             _iso(utcnow())),
        )
        self.db.commit()
        return self.db.total_changes > before

    def proposals(self):
        return self.db.execute(
            "SELECT * FROM proposal ORDER BY collection, score DESC"
        ).fetchall()

    def forget_proposal(self, source: str, ext_id: str, collection: str):
        """Drop a suggestion -- normally because you acted on it."""
        self.db.execute(
            "DELETE FROM proposal "
            "WHERE source = ? AND ext_id = ? AND collection = ?",
            (source, str(ext_id), collection),
        )
        self.db.commit()

    def already_proposed(self, source: str, collection: str) -> set[str]:
        return {
            row["ext_id"]
            for row in self.db.execute(
                "SELECT ext_id FROM proposal WHERE source = ? AND collection = ?",
                (source, collection),
            )
        }

    # ---- cache ------------------------------------------------------------ #
    def cache_get(self, key: str, max_age_days: float | None = None):
        """
        Read a cached answer.

        `max_age_days` expires it -- used for suggestions, which are worth
        re-asking occasionally but certainly not every cycle. Judgements about
        a specific title (serialised? fits?) never expire.
        """
        row = self.db.execute(
            "SELECT value, created_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        if max_age_days is not None:
            try:
                made = datetime.fromisoformat(row["created_at"])
            except ValueError:
                return None
            if (utcnow() - made).total_seconds() > max_age_days * 86400:
                return None
        return row["value"]

    def cache_set(self, key: str, value: str):
        self.db.execute(
            "INSERT OR REPLACE INTO cache (key, value, created_at) "
            "VALUES (?,?,?)",
            (key, value, _iso(utcnow())),
        )
        self.db.commit()
