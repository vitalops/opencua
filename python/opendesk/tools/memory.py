"""MemoryTool — recall what was on screen at any point in the past.

Backed by the local screen-memory index in ``~/.opendesk/memory`` (see
:mod:`opendesk.memory`).  The index is populated by the background daemon
(``opendesk memory start``); this tool only *reads* it, plus a few control
actions (pause / resume / deny list / delete).

The tool always talks to the *local* index — screen memory never leaves
the machine it was recorded on, so it is intentionally not peer-routable.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, List, Literal, Optional

from pydantic import Field

from opendesk.tools.base import Attachment, Tool, ToolContext, ToolResult

_MAX_TEXT_CHARS = 6000


class MemoryTool(Tool):
    """Search and manage the local screen-memory history."""

    name = "memory"
    description = (
        "Recall what the user saw on screen in the past. A local background "
        "daemon (`opendesk memory start`) captures the screen every ~30s, OCRs it, "
        "and indexes the text with a thumbnail. Nothing is uploaded.\n\n"
        "Actions:\n"
        "  search   — full-text search. query='error', since='tuesday', app='Terminal'. "
        "Returns matching moments (id, time, app, window title, snippet).\n"
        "  show     — fetch one moment by id: full OCR text + thumbnail image.\n"
        "  timeline — list captured moments in a period, optionally filtered by app "
        "(e.g. 'every time the dashboard was open this month').\n"
        "  status   — is the daemon running/paused, how much is stored, config.\n"
        "  pause / resume — stop or restart capture (pause accepts duration='1h').\n"
        "  deny     — manage the per-app deny list (deny_add / deny_remove).\n"
        "  config   — change interval_seconds, storage_cap_mb, retention_days.\n"
        "  delete   — remove stored moments for a time range and/or app.\n\n"
        "Time phrases for since/until: '2h', '3d', 'yesterday', 'tuesday', "
        "'last week', 'this month', 'yesterday afternoon', '2026-09-01'.\n"
        "Typical flow: search → pick an id → show to read the full text / image."
    )

    class Params(Tool.Params):
        action: Literal[
            "search", "show", "timeline", "status", "pause", "resume",
            "deny", "config", "delete",
        ] = Field(description="What to do. See tool description.")
        query: Optional[str] = Field(
            default=None,
            description=(
                "Search terms (action=search). All terms must appear; quote phrases. "
                "Append * for prefix match (e.g. 'invoice*')."
            ),
        )
        since: Optional[str] = Field(
            default=None,
            description="Start of the time window: '2h', 'yesterday', 'tuesday', 'last week', ISO date.",
        )
        until: Optional[str] = Field(
            default=None,
            description="End of the time window (same formats as since).",
        )
        app: Optional[str] = Field(
            default=None,
            description="Only moments whose app name or window title contains this (case-insensitive).",
        )
        limit: int = Field(default=15, ge=1, le=200, description="Max results for search/timeline.")
        id: Optional[int] = Field(default=None, description="Frame id (action=show).")
        include_image: bool = Field(
            default=True, description="For action=show: attach the thumbnail image.",
        )
        duration: Optional[str] = Field(
            default=None,
            description="For action=pause: how long ('30m', '2h'). Omit to pause until resumed.",
        )
        deny_add: Optional[str] = Field(
            default=None, description="For action=deny: app name / title substring to add.",
        )
        deny_remove: Optional[str] = Field(
            default=None, description="For action=deny: entry to remove.",
        )
        interval_seconds: Optional[float] = Field(
            default=None, ge=5, description="For action=config: seconds between captures.",
        )
        storage_cap_mb: Optional[int] = Field(
            default=None, ge=16, description="For action=config: storage ceiling in MB.",
        )
        retention_days: Optional[int] = Field(
            default=None, ge=1, description="For action=config: delete frames older than this.",
        )
        confirm: bool = Field(
            default=False,
            description="For action=delete: must be true to actually delete.",
        )

    def __init__(self, home: Optional[Path] = None) -> None:
        self._home = home

    # ------------------------------------------------------------------

    def _resolve_home(self, ctx: ToolContext) -> Optional[Path]:
        if self._home is not None:
            return self._home
        meta_home = ctx.metadata.get("opendesk_home") if ctx.metadata else None
        if meta_home:
            return Path(str(meta_home)).expanduser()
        import os
        env = os.environ.get("OPENDESK_HOME")
        return Path(env).expanduser() if env else None

    async def execute(self, ctx: ToolContext, params: "MemoryTool.Params") -> ToolResult:
        action = params.action
        await ctx.check_permission(
            tool="memory", argument=action,
            description=f"Screen memory: {action}"
            + (f" {params.query!r}" if params.query else ""),
        )
        home = self._resolve_home(ctx)

        try:
            if action == "search":
                return self._search(home, params)
            if action == "show":
                return self._show(home, params)
            if action == "timeline":
                return self._timeline(home, params)
            if action == "status":
                return self._status(home)
            if action == "pause":
                return self._pause(home, params)
            if action == "resume":
                return self._resume(home)
            if action == "deny":
                return self._deny(home, params)
            if action == "config":
                return self._config(home, params)
            if action == "delete":
                return self._delete(home, params)
        except ValueError as exc:
            return ToolResult(title="Memory error", output=str(exc), error=True)
        except ImportError as exc:
            return ToolResult(title="Memory error", output=str(exc), error=True)
        return ToolResult(title="Memory error", output=f"Unknown action {action!r}", error=True)

    # -- search / show / timeline ---------------------------------------------

    def _search(self, home: Optional[Path], p: "MemoryTool.Params") -> ToolResult:
        from opendesk.memory.store import MemoryStore
        from opendesk.memory.timeparse import fmt_range, parse_range

        if not p.query or not p.query.strip():
            raise ValueError("query is required for action='search' (use action='timeline' to browse).")
        start, end = parse_range(p.since, p.until)
        with MemoryStore(home) as store:
            frames = store.search(p.query, start=start, end=end, app=p.app, limit=p.limit)
            empty_hint = self._empty_hint(store, home)

        header = f"Screen memory search {p.query!r} — {fmt_range(start, end)}"
        if p.app:
            header += f" — app~{p.app!r}"
        if not frames:
            return ToolResult(
                title="Memory: no matches",
                output=f"{header}\nNo matching moments.{empty_hint}",
                metadata={"count": 0},
            )
        lines = [header, f"{len(frames)} match(es), best first:", ""]
        for f in frames:
            lines.append(_frame_line(f))
        lines.append("")
        lines.append("Use memory(action='show', id=<id>) for the full text and thumbnail.")
        return ToolResult(
            title=f"Memory: {len(frames)} match(es)",
            output="\n".join(lines),
            metadata={"count": len(frames), "ids": [f.id for f in frames]},
        )

    def _timeline(self, home: Optional[Path], p: "MemoryTool.Params") -> ToolResult:
        from opendesk.memory.store import MemoryStore
        from opendesk.memory.timeparse import fmt_range, parse_range

        start, end = parse_range(p.since, p.until)
        with MemoryStore(home) as store:
            frames = store.timeline(start=start, end=end, app=p.app, limit=p.limit)
            apps = store.apps(start=start, end=end)
            empty_hint = self._empty_hint(store, home)

        header = f"Screen memory timeline — {fmt_range(start, end)}"
        if p.app:
            header += f" — app~{p.app!r}"
        if not frames:
            return ToolResult(
                title="Memory: nothing recorded",
                output=f"{header}\nNo moments recorded in this window.{empty_hint}",
                metadata={"count": 0},
            )
        lines = [header, ""]
        if apps and not p.app:
            top = ", ".join(f"{a or '(unknown)'} ×{n}" for a, n in apps[:8])
            lines.append(f"Apps in window: {top}")
            lines.append("")
        lines.append(f"Most recent {len(frames)}:")
        for f in frames:
            lines.append(_frame_line(f, snippet=False))
        lines.append("")
        lines.append("Use memory(action='show', id=<id>) to read a moment in full.")
        return ToolResult(
            title=f"Memory timeline ({len(frames)})",
            output="\n".join(lines),
            metadata={"count": len(frames), "ids": [f.id for f in frames]},
        )

    def _show(self, home: Optional[Path], p: "MemoryTool.Params") -> ToolResult:
        from opendesk.memory.store import MemoryStore

        if p.id is None:
            raise ValueError("id is required for action='show'.")
        with MemoryStore(home) as store:
            frame = store.get(int(p.id))
            thumb = store.thumbnail(frame) if (frame and p.include_image) else None
        if frame is None:
            return ToolResult(title="Memory: not found", output=f"No moment with id {p.id}.", error=True)

        text = frame.text.strip() or "(no text was recognised in this frame)"
        truncated = ""
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS]
            truncated = f"\n… [truncated, {len(frame.text)} chars total]"
        lines = [
            f"Moment #{frame.id} — {frame.when}",
            f"App:    {frame.app or '(unknown)'}",
        ]
        if frame.title:
            lines.append(f"Window: {frame.title}")
        lines.append("")
        lines.append("Screen text:")
        lines.append(text + truncated)
        attachments = []
        if thumb:
            attachments.append(Attachment(f"memory-{frame.id}.jpg", thumb, "image/jpeg"))
        return ToolResult(
            title=f"Memory #{frame.id} ({frame.app or 'unknown'})",
            output="\n".join(lines),
            attachments=attachments,
            metadata=frame.to_dict(),
        )

    # -- status / control ----------------------------------------------------

    def _status(self, home: Optional[Path]) -> ToolResult:
        from opendesk.memory.config import daemon_alive, get_pause, load_config, read_daemon_state
        from opendesk.memory.store import MemoryStore
        from opendesk.memory.timeparse import fmt_ts

        cfg = load_config(home)
        pause = get_pause(home)
        alive = daemon_alive(home)
        state = read_daemon_state(home) or {}
        with MemoryStore(home) as store:
            st = store.stats()
            fts = store.has_fts
            store_dir = store.dir

        if alive:
            daemon_line = f"running (pid {state.get('pid')}, last tick: {state.get('status', '?')})"
        else:
            daemon_line = "NOT running — start it with `opendesk memory start`"
        lines = [
            "Screen memory status",
            f"  daemon:    {daemon_line}",
            f"  capture:   {'PAUSED — ' + pause.describe() if pause else 'active'}",
            f"  store:     {store_dir}",
            f"  frames:    {st.frames}"
            + (f"  ({fmt_ts(st.oldest)} → {fmt_ts(st.newest)})" if st.frames and st.oldest and st.newest else ""),
            f"  size:      {_mb(st.total_bytes)} MB of {cfg.storage_cap_mb} MB cap "
            f"(thumbnails {_mb(st.thumb_bytes)} MB, index {_mb(st.db_bytes)} MB)",
            f"  retention: {cfg.retention_days} days",
            f"  interval:  every {cfg.interval_seconds:g}s",
            f"  deny list: {', '.join(cfg.deny_apps) if cfg.deny_apps else '(empty)'}",
            f"  hotkey:    {cfg.pause_hotkey or '(disabled)'}",
            f"  search:    {'FTS5 full-text' if fts else 'LIKE fallback (no FTS5)'}",
        ]
        if st.apps:
            lines.append("  top apps:  " + ", ".join(f"{a or '(unknown)'} ×{n}" for a, n in st.apps[:6]))
        return ToolResult(
            title="Memory status",
            output="\n".join(lines),
            metadata={
                "daemon_alive": alive, "paused": pause is not None, "frames": st.frames,
                "bytes": st.total_bytes, "config": cfg.to_dict(),
            },
        )

    def _pause(self, home: Optional[Path], p: "MemoryTool.Params") -> ToolResult:
        from opendesk.memory.config import parse_duration, set_pause

        secs = parse_duration(p.duration) if p.duration else None
        state = set_pause(home, duration_seconds=secs, reason="tool")
        return ToolResult(title="Memory paused", output=f"Screen memory capture {state.describe()}.")

    def _resume(self, home: Optional[Path]) -> ToolResult:
        from opendesk.memory.config import clear_pause

        cleared = clear_pause(home)
        return ToolResult(
            title="Memory resumed",
            output="Screen memory capture resumed." if cleared else "Capture was not paused.",
        )

    def _deny(self, home: Optional[Path], p: "MemoryTool.Params") -> ToolResult:
        from opendesk.memory.config import load_config, save_config

        cfg = load_config(home)
        msgs: list[str] = []
        if p.deny_add:
            msgs.append(
                f"Added {p.deny_add!r}." if cfg.deny_add(p.deny_add) else f"{p.deny_add!r} already listed."
            )
        if p.deny_remove:
            msgs.append(
                f"Removed {p.deny_remove!r}." if cfg.deny_remove(p.deny_remove) else f"{p.deny_remove!r} not found."
            )
        if p.deny_add or p.deny_remove:
            save_config(cfg, home)
        listing = "\n".join(f"  - {d}" for d in cfg.deny_apps) or "  (empty)"
        out = ("\n".join(msgs) + "\n\n" if msgs else "") + (
            "Deny list (apps / window titles never captured):\n" + listing
        )
        return ToolResult(title="Memory deny list", output=out, metadata={"deny_apps": cfg.deny_apps})

    def _config(self, home: Optional[Path], p: "MemoryTool.Params") -> ToolResult:
        from opendesk.memory.config import load_config, save_config

        cfg = load_config(home)
        changed: list[str] = []
        if p.interval_seconds is not None:
            cfg.interval_seconds = float(p.interval_seconds)
            changed.append(f"interval_seconds={cfg.interval_seconds:g}")
        if p.storage_cap_mb is not None:
            cfg.storage_cap_mb = int(p.storage_cap_mb)
            changed.append(f"storage_cap_mb={cfg.storage_cap_mb}")
        if p.retention_days is not None:
            cfg.retention_days = int(p.retention_days)
            changed.append(f"retention_days={cfg.retention_days}")
        if changed:
            save_config(cfg, home)
        lines = []
        if changed:
            lines.append("Updated: " + ", ".join(changed))
            lines.append("(the running daemon picks this up within a few ticks)")
            lines.append("")
        lines.append("Current config:")
        for k, v in cfg.to_dict().items():
            lines.append(f"  {k}: {v}")
        return ToolResult(title="Memory config", output="\n".join(lines), metadata=cfg.to_dict())

    def _delete(self, home: Optional[Path], p: "MemoryTool.Params") -> ToolResult:
        from opendesk.memory.store import MemoryStore
        from opendesk.memory.timeparse import fmt_range, parse_range

        if not (p.since or p.until or p.app):
            raise ValueError(
                "Refusing to delete everything: pass since/until and/or app. "
                "Use the CLI `opendesk memory clear` to wipe the whole index."
            )
        start, end = parse_range(p.since, p.until)
        scope = fmt_range(start, end) + (f", app~{p.app!r}" if p.app else "")
        with MemoryStore(home) as store:
            candidates = store.timeline(start=start, end=end, app=p.app, limit=100000)
            if not p.confirm:
                return ToolResult(
                    title="Memory delete (dry run)",
                    output=(
                        f"{len(candidates)} moment(s) would be deleted for {scope}. "
                        "Re-run with confirm=true to delete."
                    ),
                    metadata={"count": len(candidates)},
                )
            n = store.delete([f.id for f in candidates])
        return ToolResult(
            title="Memory deleted",
            output=f"Deleted {n} moment(s) for {scope}.",
            metadata={"count": n},
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _empty_hint(store: Any, home: Optional[Path]) -> str:
        from opendesk.memory.config import daemon_alive, get_pause

        if store.stats().frames == 0:
            if not daemon_alive(home):
                return (
                    "\nThe index is empty and the capture daemon is not running. "
                    "Start it with `opendesk memory start`."
                )
            return "\nThe index is empty — the daemon has only just started."
        if get_pause(home) is not None:
            return "\n(Capture is currently paused.)"
        return ""


# ---------------------------------------------------------------------------


def _frame_line(f: Any, *, snippet: bool = True) -> str:
    app = f.app or "(unknown)"
    title = f" — {f.title[:70]}" if f.title and f.title != f.app else ""
    line = f"  #{f.id:<6} {f.when}  [{app}]{title}"
    if snippet:
        snip = (f.snippet or f.text[:120]).replace("\n", " ").strip()
        if snip:
            line += f"\n          {snip[:220]}"
    return line


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f}"
