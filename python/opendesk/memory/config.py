"""Configuration and on-disk state for screen memory.

Everything lives under ``<home>/memory/`` (``~/.opendesk/memory`` by default):

* ``config.json``  — :class:`MemoryConfig` (interval, storage cap, deny list …)
* ``index.db``     — SQLite index with FTS5 full-text search (see ``store.py``)
* ``thumbs/``      — JPEG thumbnails, one folder per day
* ``paused.json``  — present while capture is paused (hotkey / tool / CLI)
* ``daemon.json``  — heartbeat written by the capture loop so other
  processes (the MCP server, the CLI) can report whether it's alive.

The capture daemon and the agent's ``memory`` tool run in *different*
processes, so all shared state goes through these files rather than
module globals.  Nothing here ever touches the network.
"""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_HOME = Path.home() / ".opendesk"
MEMORY_DIR_NAME = "memory"

# Password managers and similar are excluded out of the box.  Users can
# clear or extend this via ``opendesk memory deny`` / the ``memory`` tool.
DEFAULT_DENY_APPS: list[str] = [
    "1Password",
    "Bitwarden",
    "KeePassXC",
    "Keychain Access",
    "LastPass",
    "Dashlane",
]


def _default_hotkey() -> str:
    # pynput GlobalHotKeys syntax.  Cmd on macOS, Ctrl elsewhere.
    if platform.system() == "Darwin":
        return "<cmd>+<shift>+<alt>+p"
    return "<ctrl>+<shift>+<alt>+p"


def memory_dir(home: Optional[Path] = None) -> Path:
    """Return (and create) the screen-memory directory under *home*."""
    base = Path(home).expanduser() if home else DEFAULT_HOME
    d = base / MEMORY_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


@dataclass
class MemoryConfig:
    """User-tunable settings for the capture loop and index."""

    interval_seconds: float = 30.0
    """Seconds between captures.  Low frequency by design."""

    storage_cap_mb: int = 2048
    """Hard ceiling for thumbnails + index.  Oldest frames are deleted first."""

    retention_days: int = 30
    """Frames older than this are deleted regardless of the cap."""

    deny_apps: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_APPS))
    """Case-insensitive substrings matched against the frontmost app name
    and window title.  A match skips the capture entirely."""

    pause_hotkey: str = field(default_factory=_default_hotkey)
    """Global hotkey (pynput syntax) that toggles pause in the daemon."""

    thumbnail_width: int = 480
    """Thumbnail width in pixels.  Aspect ratio is preserved."""

    thumbnail_quality: int = 60
    """JPEG quality for thumbnails (1–95)."""

    dedupe_threshold: float = 0.02
    """Mean absolute pixel delta (0–1) below which a frame is considered a
    duplicate of the previous one and skipped."""

    ocr_backend: str = "auto"
    """``auto`` (pytesseract → native), ``tesseract``, or ``native``."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    # -- deny list helpers ------------------------------------------------

    def is_denied(self, app: Optional[str], title: Optional[str] = None) -> bool:
        """True when *app* or *title* matches any deny-list entry."""
        hay = " \n ".join(s for s in (app, title) if s).lower()
        if not hay:
            return False
        return any(pat.strip().lower() in hay for pat in self.deny_apps if pat.strip())

    def deny_add(self, pattern: str) -> bool:
        pattern = pattern.strip()
        if not pattern:
            return False
        if any(p.lower() == pattern.lower() for p in self.deny_apps):
            return False
        self.deny_apps.append(pattern)
        return True

    def deny_remove(self, pattern: str) -> bool:
        before = len(self.deny_apps)
        self.deny_apps = [p for p in self.deny_apps if p.lower() != pattern.strip().lower()]
        return len(self.deny_apps) < before


def config_path(home: Optional[Path] = None) -> Path:
    return memory_dir(home) / "config.json"


def load_config(home: Optional[Path] = None) -> MemoryConfig:
    path = config_path(home)
    if not path.exists():
        return MemoryConfig()
    try:
        return MemoryConfig.from_dict(json.loads(path.read_text()))
    except Exception:
        return MemoryConfig()


def save_config(config: MemoryConfig, home: Optional[Path] = None) -> Path:
    path = config_path(home)
    _atomic_write(path, json.dumps(config.to_dict(), indent=2))
    return path


# ---------------------------------------------------------------------------
# Pause state (shared between daemon, tool, CLI)
# ---------------------------------------------------------------------------


@dataclass
class PauseState:
    since: float
    until: Optional[float] = None  # None = until resumed
    reason: str = ""

    @property
    def expired(self) -> bool:
        return self.until is not None and time.time() >= self.until

    def describe(self) -> str:
        if self.until is None:
            return "paused until resumed"
        remaining = max(0, int(self.until - time.time()))
        return f"paused for another {_fmt_duration(remaining)}"


def pause_path(home: Optional[Path] = None) -> Path:
    return memory_dir(home) / "paused.json"


def get_pause(home: Optional[Path] = None) -> Optional[PauseState]:
    """Return the current pause state, clearing it if it has expired."""
    path = pause_path(home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        state = PauseState(
            since=float(data.get("since", 0)),
            until=data.get("until"),
            reason=str(data.get("reason", "")),
        )
    except Exception:
        path.unlink(missing_ok=True)
        return None
    if state.expired:
        path.unlink(missing_ok=True)
        return None
    return state


def set_pause(
    home: Optional[Path] = None,
    *,
    duration_seconds: Optional[float] = None,
    reason: str = "",
) -> PauseState:
    now = time.time()
    state = PauseState(
        since=now,
        until=(now + duration_seconds) if duration_seconds else None,
        reason=reason,
    )
    _atomic_write(pause_path(home), json.dumps(asdict(state)))
    return state


def clear_pause(home: Optional[Path] = None) -> bool:
    path = pause_path(home)
    if path.exists():
        path.unlink(missing_ok=True)
        return True
    return False


def is_paused(home: Optional[Path] = None) -> bool:
    return get_pause(home) is not None


# ---------------------------------------------------------------------------
# Daemon heartbeat
# ---------------------------------------------------------------------------


def daemon_state_path(home: Optional[Path] = None) -> Path:
    return memory_dir(home) / "daemon.json"


def write_daemon_state(home: Optional[Path], **fields: Any) -> None:
    data = {"pid": os.getpid(), "heartbeat": time.time(), **fields}
    _atomic_write(daemon_state_path(home), json.dumps(data))


def read_daemon_state(home: Optional[Path] = None) -> Optional[dict[str, Any]]:
    path = daemon_state_path(home)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def clear_daemon_state(home: Optional[Path] = None) -> None:
    daemon_state_path(home).unlink(missing_ok=True)


def daemon_alive(home: Optional[Path] = None, *, stale_after: float = 300.0) -> bool:
    """Heuristic liveness: pid exists *and* heartbeat is recent."""
    state = read_daemon_state(home)
    if not state:
        return False
    pid = int(state.get("pid") or 0)
    if pid <= 0 or not _pid_alive(pid):
        return False
    return (time.time() - float(state.get("heartbeat") or 0)) < stale_after


def _pid_alive(pid: int) -> bool:
    if platform.system() == "Windows":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h, m = divmod(seconds, 3600)
        return f"{h}h{m // 60:02d}m"
    return f"{seconds // 86400}d"


def parse_duration(text: str) -> float:
    """Parse ``"30m"``, ``"2h"``, ``"1d"``, ``"90s"`` or a bare number of
    seconds into seconds.  Raises :class:`ValueError` on junk."""
    t = str(text).strip().lower()
    if not t:
        raise ValueError("empty duration")
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if t[-1] in units and t[:-1].replace(".", "", 1).isdigit():
        return float(t[:-1]) * units[t[-1]]
    if t.replace(".", "", 1).isdigit():
        return float(t)
    for word, mult in (("minute", 60), ("hour", 3600), ("day", 86400), ("week", 604800), ("second", 1)):
        if word in t:
            num = "".join(ch for ch in t if ch.isdigit() or ch == ".")
            return float(num or 1) * mult
    raise ValueError(f"Cannot parse duration: {text!r} (try '30m', '2h', '1d')")
