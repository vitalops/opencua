"""Server-side audit log.

Every protocol method dispatched on the controlled machine, plus session
lifecycle events (open / close / rejected), is appended to a per-day JSONL
file under ``<home>/audit/YYYY-MM-DD.jsonl`` (mode ``0600``).

Why JSONL one-file-per-day instead of a single rolling file or SQLite:

* ``tail -f`` and ``grep`` work out of the box — the audit log is operator
  ergonomics first, programmatic analysis second.
* Rotation is free: delete files older than your retention window.  No
  size-based rotation logic to get wrong.
* JSON is universally parseable, including by the controlling user's
  preferred analytics tool.

The log records *summaries* of params (e.g. ``"send pointer down at (812,
410)"``, ``"type 18 chars: 'hello world…'"``, ``"run shell: 'ls -la /tmp'"``),
not the raw bytes / full text — this avoids leaking PII or large payloads
into the log file.  Reuses :func:`opendesk.remote.policy._summarise` so the
two surfaces stay aligned.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from opendesk.protocol.auth.identity import DEFAULT_HOME
from opendesk.remote.policy import _summarise


log = logging.getLogger("opendesk.remote.audit")

AUDIT_DIR_NAME = "audit"


def _today_iso() -> str:
    return _dt.date.today().isoformat()


def _peer_id(public_key: bytes, name: str) -> dict[str, str]:
    return {
        "name": name,
        "fp": public_key.hex()[:16],
    }


class AuditLog:
    """Append-only JSONL audit log, rotating once per day.

    Thread-safe via an asyncio lock around every write.  All entries carry:

    * ``ts``    — unix epoch seconds (float)
    * ``type``  — ``session.opened`` | ``session.closed`` | ``session.rejected`` | ``call``
    * ``peer``  — ``{"name": ..., "fp": "<first 16 hex>"}``

    Plus per-type fields documented in each ``record_*`` method.
    """

    def __init__(self, home: Optional[Path] = None) -> None:
        base = Path(home) if home else DEFAULT_HOME
        self._dir = base / AUDIT_DIR_NAME
        self._lock = asyncio.Lock()
        self._fh: Optional[Any] = None
        self._current_date: Optional[str] = None
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # ------------------------------------------------------------------
    # Public recorders
    # ------------------------------------------------------------------

    async def record_session_opened(
        self,
        *,
        peer_public: bytes,
        peer_name: str,
        session_id: str,
        remote_addr: str,
        mode: str,
    ) -> None:
        await self._write({
            "type": "session.opened",
            "peer": _peer_id(peer_public, peer_name),
            "session_id": session_id,
            "remote_addr": remote_addr,
            "mode": mode,
        })

    async def record_session_closed(
        self,
        *,
        peer_public: bytes,
        peer_name: str,
        session_id: str,
        duration: float,
        reason: str = "",
    ) -> None:
        await self._write({
            "type": "session.closed",
            "peer": _peer_id(peer_public, peer_name),
            "session_id": session_id,
            "duration": round(duration, 3),
            "reason": reason,
        })

    async def record_session_rejected(
        self,
        *,
        peer_public: bytes,
        peer_name: str,
        remote_addr: str,
        reason: str,
    ) -> None:
        await self._write({
            "type": "session.rejected",
            "peer": _peer_id(peer_public, peer_name),
            "remote_addr": remote_addr,
            "reason": reason,
        })

    async def record_call(
        self,
        *,
        peer_public: bytes,
        peer_name: str,
        session_id: str,
        method: str,
        params: dict[str, Any],
        outcome: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        entry: dict[str, Any] = {
            "type": "call",
            "peer": _peer_id(peer_public, peer_name),
            "session_id": session_id,
            "method": method,
            "summary": _summarise(method, params),
            "outcome": outcome,
        }
        if error_code:
            entry["error_code"] = error_code
        if error_message:
            entry["error_message"] = error_message[:200]
        await self._write(entry)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        async with self._lock:
            if self._fh is not None:
                with contextlib.suppress(Exception):
                    self._fh.flush()
                with contextlib.suppress(Exception):
                    self._fh.close()
                self._fh = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _write(self, payload: dict[str, Any]) -> None:
        payload = {"ts": time.time(), **payload}
        line = json.dumps(payload, sort_keys=True, default=str)
        async with self._lock:
            self._roll_if_needed()
            assert self._fh is not None
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
            except OSError as exc:
                log.warning("audit log write failed: %s", exc)

    def _roll_if_needed(self) -> None:
        today = _today_iso()
        if today == self._current_date and self._fh is not None:
            return
        if self._fh is not None:
            with contextlib.suppress(Exception):
                self._fh.flush()
            with contextlib.suppress(Exception):
                self._fh.close()
            self._fh = None
        path = self._dir / f"{today}.jsonl"
        existed = path.exists()
        self._fh = open(path, "a", encoding="utf-8")
        if not existed:
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
        self._current_date = today

    # ------------------------------------------------------------------
    # Read helpers (used by `opendesk audit`)
    # ------------------------------------------------------------------

    def iter_entries(
        self, *, date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Read entries from a single day's file.  Returns parsed dicts."""
        import re
        if date is not None and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError(f"invalid date format {date!r}; expected YYYY-MM-DD")
        path = self._dir / f"{date or _today_iso()}.jsonl"
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    @property
    def directory(self) -> Path:
        return self._dir
