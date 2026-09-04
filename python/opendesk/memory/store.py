"""Local, searchable index of screen captures.

One SQLite database (``index.db``) holds a row per kept frame — timestamp,
frontmost app, window title, OCR text, and the path of a JPEG thumbnail on
disk.  Full-text search uses SQLite's FTS5 when the interpreter's SQLite
was built with it (virtually always), with a plain ``LIKE`` fallback.

The store is deliberately boring: no vector embeddings, no cloud, no
background threads.  Everything an agent needs to answer "what did I see
on Tuesday" is a single indexed query away.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from opendesk.memory.config import memory_dir

_SCHEMA_VERSION = 1


@dataclass
class Frame:
    """One captured moment."""

    id: int
    ts: float
    app: str
    title: str
    text: str
    thumb_path: Optional[str]
    thumb_bytes: int = 0
    width: int = 0
    height: int = 0
    snippet: str = ""  # populated by search()
    score: float = 0.0

    @property
    def when(self) -> str:
        return _dt.datetime.fromtimestamp(self.ts).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "when": self.when,
            "app": self.app,
            "title": self.title,
            "text": self.text,
            "thumb_path": self.thumb_path,
            "snippet": self.snippet,
        }


@dataclass
class StoreStats:
    frames: int = 0
    oldest: Optional[float] = None
    newest: Optional[float] = None
    thumb_bytes: int = 0
    db_bytes: int = 0
    apps: list[tuple[str, int]] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return self.thumb_bytes + self.db_bytes


class MemoryStore:
    """SQLite-backed frame index.  Safe for use from one process at a time
    per connection; the daemon and the tool each open their own.

    ``check_same_thread`` is disabled so the recorder can write from a
    worker thread; writes are serialised through an internal lock.
    """

    def __init__(self, home: Optional[Path] = None, *, db_path: Optional[Path] = None) -> None:
        self.dir = memory_dir(home)
        self.thumbs_dir = self.dir / "thumbs"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path or (self.dir / "index.db")
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        self._fts = self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> bool:
        c = self._conn
        with self._lock:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS frames (
                    id          INTEGER PRIMARY KEY,
                    ts          REAL    NOT NULL,
                    app         TEXT    NOT NULL DEFAULT '',
                    title       TEXT    NOT NULL DEFAULT '',
                    text        TEXT    NOT NULL DEFAULT '',
                    text_hash   TEXT    NOT NULL DEFAULT '',
                    thumb_path  TEXT,
                    thumb_bytes INTEGER NOT NULL DEFAULT 0,
                    width       INTEGER NOT NULL DEFAULT 0,
                    height      INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS frames_ts ON frames(ts)")
            c.execute("CREATE INDEX IF NOT EXISTS frames_app ON frames(app)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            c.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            try:
                c.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS frames_fts USING fts5(
                        text, app, title,
                        content='frames', content_rowid='id',
                        tokenize='unicode61'
                    )
                    """
                )
                c.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS frames_ai AFTER INSERT ON frames BEGIN
                        INSERT INTO frames_fts(rowid, text, app, title)
                        VALUES (new.id, new.text, new.app, new.title);
                    END;
                    CREATE TRIGGER IF NOT EXISTS frames_ad AFTER DELETE ON frames BEGIN
                        INSERT INTO frames_fts(frames_fts, rowid, text, app, title)
                        VALUES ('delete', old.id, old.text, old.app, old.title);
                    END;
                    CREATE TRIGGER IF NOT EXISTS frames_au AFTER UPDATE ON frames BEGIN
                        INSERT INTO frames_fts(frames_fts, rowid, text, app, title)
                        VALUES ('delete', old.id, old.text, old.app, old.title);
                        INSERT INTO frames_fts(rowid, text, app, title)
                        VALUES (new.id, new.text, new.app, new.title);
                    END;
                    """
                )
                return True
            except sqlite3.OperationalError:
                return False

    @property
    def has_fts(self) -> bool:
        return self._fts

    def close(self) -> None:
        with self._lock:
            with contextlib.suppress(Exception):
                self._conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add(
        self,
        *,
        ts: float,
        app: str,
        title: str,
        text: str,
        thumb: Optional[bytes],
        width: int = 0,
        height: int = 0,
    ) -> Frame:
        """Insert a frame.  *thumb* (JPEG bytes) is written under
        ``thumbs/YYYY-MM-DD/<id>.jpg``."""
        text = text or ""
        text_hash = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO frames(ts, app, title, text, text_hash, width, height) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, app or "", title or "", text, text_hash, width, height),
            )
            frame_id = int(cur.lastrowid)
            thumb_path: Optional[str] = None
            thumb_bytes = 0
            if thumb:
                day = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                day_dir = self.thumbs_dir / day
                day_dir.mkdir(parents=True, exist_ok=True)
                p = day_dir / f"{frame_id}.jpg"
                p.write_bytes(thumb)
                with contextlib.suppress(OSError):
                    os.chmod(p, 0o600)
                thumb_path = str(p.relative_to(self.dir))
                thumb_bytes = len(thumb)
                self._conn.execute(
                    "UPDATE frames SET thumb_path=?, thumb_bytes=? WHERE id=?",
                    (thumb_path, thumb_bytes, frame_id),
                )
        return Frame(
            id=frame_id, ts=ts, app=app or "", title=title or "", text=text,
            thumb_path=thumb_path, thumb_bytes=thumb_bytes, width=width, height=height,
        )

    def last_text_hash(self) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT text_hash FROM frames ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["text_hash"] if row else None

    def delete(self, ids: list[int]) -> int:
        """Delete frames (and their thumbnails) by id."""
        if not ids:
            return 0
        n = 0
        with self._lock:
            for chunk in _chunks(ids, 500):
                marks = ",".join("?" * len(chunk))
                rows = self._conn.execute(
                    f"SELECT id, thumb_path FROM frames WHERE id IN ({marks})", chunk,
                ).fetchall()
                for r in rows:
                    self._unlink_thumb(r["thumb_path"])
                cur = self._conn.execute(f"DELETE FROM frames WHERE id IN ({marks})", chunk)
                n += cur.rowcount
        self._prune_empty_thumb_dirs()
        return n

    def delete_range(self, start: float = 0.0, end: Optional[float] = None) -> int:
        with self._lock:
            if end is None:
                rows = self._conn.execute(
                    "SELECT id FROM frames WHERE ts >= ?", (start,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id FROM frames WHERE ts >= ? AND ts < ?", (start, end)
                ).fetchall()
        return self.delete([r["id"] for r in rows])

    def delete_app(self, app_pattern: str) -> int:
        pat = f"%{app_pattern}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM frames WHERE app LIKE ? OR title LIKE ?", (pat, pat)
            ).fetchall()
        return self.delete([r["id"] for r in rows])

    def clear(self) -> int:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM frames").fetchall()
        return self.delete([r["id"] for r in rows])

    # -- retention ---------------------------------------------------------

    def enforce_retention(self, retention_days: int) -> int:
        """Delete frames older than *retention_days*.  Returns count."""
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM frames WHERE ts < ?", (cutoff,)
            ).fetchall()
        return self.delete([r["id"] for r in rows])

    def enforce_cap(self, cap_bytes: int, *, target_fraction: float = 0.9) -> int:
        """Rolling deletion: drop oldest frames until on-disk usage is under
        ``cap_bytes * target_fraction``.  Returns count deleted.

        Usage is thumbnails + the main database file (the WAL is checkpointed
        first so its transient size doesn't count).  Each deleted frame is
        assumed to free its thumbnail plus roughly three times its text
        length (row + FTS index); the database is vacuumed afterwards so the
        file actually shrinks.  At least one frame is always kept.
        """
        if cap_bytes <= 0:
            return 0
        self._checkpoint()
        usage = self._disk_usage(include_wal=False)
        if usage <= cap_bytes:
            return 0
        target = int(cap_bytes * target_fraction)
        deleted = 0
        with self._lock:
            while usage > target:
                total = self._conn.execute("SELECT COUNT(*) AS n FROM frames").fetchone()["n"]
                if total <= 1:
                    break
                batch = max(1, min(200, total - 1))
                rows = self._conn.execute(
                    "SELECT id, thumb_bytes, length(text) AS tlen FROM frames "
                    "ORDER BY ts ASC LIMIT ?", (batch,),
                ).fetchall()
                if not rows:
                    break
                ids = [r["id"] for r in rows]
                freed = sum(int(r["thumb_bytes"] or 0) + 3 * int(r["tlen"] or 0) for r in rows)
                deleted += self.delete(ids)
                usage -= freed
        if deleted:
            self._checkpoint()
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute("VACUUM")
        return deleted

    def _checkpoint(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _disk_usage(self, *, include_wal: bool = True) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(thumb_bytes), 0) AS tb FROM frames"
            ).fetchone()
        total = int(row["tb"] or 0)
        suffixes = ("", "-wal", "-shm") if include_wal else ("",)
        for suffix in suffixes:
            with contextlib.suppress(OSError):
                total += os.path.getsize(str(self.db_path) + suffix)
        return total

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, frame_id: int) -> Optional[Frame]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM frames WHERE id=?", (frame_id,)
            ).fetchone()
        return self._row_to_frame(row) if row else None

    def thumbnail(self, frame: Frame) -> Optional[bytes]:
        if not frame.thumb_path:
            return None
        p = self.dir / frame.thumb_path
        try:
            return p.read_bytes()
        except OSError:
            return None

    def search(
        self,
        query: str,
        *,
        start: float = 0.0,
        end: Optional[float] = None,
        app: Optional[str] = None,
        limit: int = 20,
    ) -> list[Frame]:
        """Full-text search.  All query terms must match (AND); when that
        yields nothing, retries as OR so near-misses still surface."""
        terms = _terms(query)
        if not terms:
            return self.timeline(start=start, end=end, app=app, limit=limit)
        hits = self._search_terms(terms, "AND", start, end, app, limit)
        if not hits and len(terms) > 1:
            hits = self._search_terms(terms, "OR", start, end, app, limit)
        return hits

    def timeline(
        self,
        *,
        start: float = 0.0,
        end: Optional[float] = None,
        app: Optional[str] = None,
        limit: int = 50,
        newest_first: bool = True,
    ) -> list[Frame]:
        where, params = self._filters(start, end, app)
        order = "DESC" if newest_first else "ASC"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM frames{where} ORDER BY ts {order} LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_frame(r) for r in rows]

    def apps(self, *, start: float = 0.0, end: Optional[float] = None) -> list[tuple[str, int]]:
        where, params = self._filters(start, end, None)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT app, COUNT(*) AS n FROM frames{where} GROUP BY app ORDER BY n DESC",
                params,
            ).fetchall()
        return [(r["app"], int(r["n"])) for r in rows]

    def stats(self) -> StoreStats:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS oldest, MAX(ts) AS newest, "
                "COALESCE(SUM(thumb_bytes), 0) AS tb FROM frames"
            ).fetchone()
            apps = self._conn.execute(
                "SELECT app, COUNT(*) AS n FROM frames GROUP BY app ORDER BY n DESC LIMIT 10"
            ).fetchall()
        db_bytes = 0
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                db_bytes += os.path.getsize(str(self.db_path) + suffix)
        return StoreStats(
            frames=int(row["n"] or 0),
            oldest=row["oldest"],
            newest=row["newest"],
            thumb_bytes=int(row["tb"] or 0),
            db_bytes=db_bytes,
            apps=[(a["app"], int(a["n"])) for a in apps],
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _search_terms(
        self, terms: list[str], joiner: str, start: float, end: Optional[float],
        app: Optional[str], limit: int,
    ) -> list[Frame]:
        where, params = self._filters(start, end, app, alias="f")
        if self._fts:
            match = f" {joiner} ".join(_fts_quote(t) for t in terms)
            sql = (
                "SELECT f.*, "
                "snippet(frames_fts, 0, '[', ']', ' … ', 12) AS snippet, "
                "bm25(frames_fts) AS score "
                "FROM frames_fts JOIN frames f ON f.id = frames_fts.rowid "
                f"WHERE frames_fts MATCH ?{where.replace(' WHERE ', ' AND ', 1)} "
                "ORDER BY score, f.ts DESC LIMIT ?"
            )
            with self._lock:
                try:
                    rows = self._conn.execute(sql, (match, *params, limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            return [self._row_to_frame(r, with_snippet=True) for r in rows]

        # LIKE fallback.
        clauses = []
        like_params: list[Any] = []
        for t in terms:
            clauses.append("(f.text LIKE ? OR f.app LIKE ? OR f.title LIKE ?)")
            like_params += [f"%{t}%"] * 3
        cond = f" {joiner} ".join(clauses)
        extra = where.replace(" WHERE ", " AND ", 1)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT f.* FROM frames f WHERE ({cond}){extra} ORDER BY f.ts DESC LIMIT ?",
                (*like_params, *params, limit),
            ).fetchall()
        frames = [self._row_to_frame(r) for r in rows]
        for fr in frames:
            fr.snippet = _like_snippet(fr.text, terms)
        return frames

    @staticmethod
    def _filters(
        start: float, end: Optional[float], app: Optional[str], *, alias: str = "",
    ) -> tuple[str, list[Any]]:
        pre = f"{alias}." if alias else ""
        clauses: list[str] = []
        params: list[Any] = []
        if start and start > 0:
            clauses.append(f"{pre}ts >= ?")
            params.append(start)
        if end is not None:
            clauses.append(f"{pre}ts < ?")
            params.append(end)
        if app:
            clauses.append(f"({pre}app LIKE ? OR {pre}title LIKE ?)")
            params += [f"%{app}%", f"%{app}%"]
        if not clauses:
            return "", params
        return " WHERE " + " AND ".join(clauses), params

    @staticmethod
    def _row_to_frame(row: sqlite3.Row, *, with_snippet: bool = False) -> Frame:
        keys = row.keys()
        return Frame(
            id=int(row["id"]),
            ts=float(row["ts"]),
            app=row["app"] or "",
            title=row["title"] or "",
            text=row["text"] or "",
            thumb_path=row["thumb_path"],
            thumb_bytes=int(row["thumb_bytes"] or 0),
            width=int(row["width"] or 0),
            height=int(row["height"] or 0),
            snippet=(row["snippet"] or "") if with_snippet and "snippet" in keys else "",
            score=float(row["score"]) if with_snippet and "score" in keys else 0.0,
        )

    def _unlink_thumb(self, rel: Optional[str]) -> None:
        if not rel:
            return
        with contextlib.suppress(OSError):
            (self.dir / rel).unlink()

    def _prune_empty_thumb_dirs(self) -> None:
        with contextlib.suppress(OSError):
            for d in self.thumbs_dir.iterdir():
                if d.is_dir():
                    with contextlib.suppress(OSError):
                        d.rmdir()  # only succeeds when empty


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[\w#@.\-/:]+", re.UNICODE)


def _terms(query: str) -> list[str]:
    """Split a free-text query into search terms.  Quoted phrases stay
    together.  Punctuation-only tokens are dropped."""
    out: list[str] = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', query or ""):
        tok = (m.group(1) or m.group(2)).strip()
        if not tok:
            continue
        if m.group(1):
            out.append(tok)
            continue
        cleaned = tok.strip("\"'.,;:!?()[]{}")
        if cleaned and any(ch.isalnum() for ch in cleaned):
            out.append(cleaned)
    return out


def _fts_quote(term: str) -> str:
    """Quote a term for FTS5.  Trailing ``*`` keeps prefix semantics."""
    prefix = term.endswith("*")
    core = term.rstrip("*").replace('"', '""')
    q = f'"{core}"'
    return q + "*" if prefix else q


def _like_snippet(text: str, terms: list[str], width: int = 80) -> str:
    low = text.lower()
    for t in terms:
        i = low.find(t.lower().rstrip("*"))
        if i >= 0:
            a = max(0, i - width // 2)
            b = min(len(text), i + len(t) + width // 2)
            return ("…" if a > 0 else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")
    return text[:width].replace("\n", " ")


def _chunks(seq: list[Any], n: int) -> Iterator[list[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
