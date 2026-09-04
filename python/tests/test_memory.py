"""Tests for screen memory — store, time parsing, recorder, and the tool.

Everything runs against a ``tmp_path`` home so the user's real
``~/.opendesk/memory`` is never touched.  Screen capture and OCR are faked.
"""

from __future__ import annotations

import datetime as _dt
import io
import time
from pathlib import Path

import pytest

from opendesk.memory.config import (
    MemoryConfig,
    clear_pause,
    get_pause,
    is_paused,
    load_config,
    parse_duration,
    save_config,
    set_pause,
    write_daemon_state,
    daemon_alive,
)
from opendesk.memory.store import MemoryStore, _terms
from opendesk.memory.timeparse import parse_range, parse_when
from opendesk.tools.base import ToolContext
from opendesk.tools.memory import MemoryTool

PIL = pytest.importorskip("PIL")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png(color=(255, 255, 255), size=(64, 48), text_seed: int = 0) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, color)
    if text_seed:
        d = ImageDraw.Draw(img)
        d.rectangle([text_seed % 40, 5, text_seed % 40 + 20, 25], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _ts(days_ago: float = 0, hour: int = 12) -> float:
    day = _dt.date.today() - _dt.timedelta(days=days_ago)
    return _dt.datetime.combine(day, _dt.time(hour=hour)).timestamp()


def seeded_store(home: Path) -> MemoryStore:
    store = MemoryStore(home)
    store.add(ts=_ts(3), app="Terminal", title="zsh", text="Error: ECONNREFUSED 127.0.0.1:5432", thumb=b"jpg1")
    store.add(ts=_ts(2), app="Google Chrome", title="Grafana — Ops dashboard", text="Ops dashboard p95 latency 120ms", thumb=b"jpg2")
    store.add(ts=_ts(1), app="Preview", title="invoice.pdf", text="Invoice INV-2026-0042 total $1,234.00", thumb=b"jpg3")
    store.add(ts=_ts(0, 9), app="Google Chrome", title="Grafana — Ops dashboard", text="Ops dashboard error rate 0.2%", thumb=b"jpg4")
    store.add(ts=_ts(0, 10), app="Slack", title="#general", text="lunch at noon?", thumb=None)
    return store


# ---------------------------------------------------------------------------
# Config / pause state
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults_roundtrip(self, tmp_path: Path):
        cfg = load_config(tmp_path)
        assert cfg.interval_seconds == 30
        assert "1Password" in cfg.deny_apps
        cfg.interval_seconds = 12
        cfg.deny_add("Signal")
        save_config(cfg, tmp_path)
        again = load_config(tmp_path)
        assert again.interval_seconds == 12
        assert "Signal" in again.deny_apps

    def test_unknown_keys_ignored(self, tmp_path: Path):
        (tmp_path / "memory").mkdir(parents=True)
        (tmp_path / "memory" / "config.json").write_text('{"interval_seconds": 5, "bogus": 1}')
        assert load_config(tmp_path).interval_seconds == 5

    def test_corrupt_config_falls_back(self, tmp_path: Path):
        (tmp_path / "memory").mkdir(parents=True)
        (tmp_path / "memory" / "config.json").write_text("{not json")
        assert load_config(tmp_path).interval_seconds == 30

    def test_deny_matching_is_case_insensitive_substring(self):
        cfg = MemoryConfig(deny_apps=["1password", "bank"])
        assert cfg.is_denied("1Password 8", None)
        assert cfg.is_denied("Safari", "My Bank — Accounts")
        assert not cfg.is_denied("Safari", "News")
        assert not cfg.is_denied("", "")

    def test_deny_add_remove(self):
        cfg = MemoryConfig(deny_apps=[])
        assert cfg.deny_add("Signal")
        assert not cfg.deny_add("signal")  # duplicate, case-insensitive
        assert cfg.deny_remove("SIGNAL")
        assert not cfg.deny_remove("Signal")
        assert not cfg.deny_add("   ")

    def test_pause_until_resumed(self, tmp_path: Path):
        assert not is_paused(tmp_path)
        st = set_pause(tmp_path, reason="test")
        assert st.until is None
        assert is_paused(tmp_path)
        assert clear_pause(tmp_path)
        assert not is_paused(tmp_path)
        assert not clear_pause(tmp_path)

    def test_pause_expires(self, tmp_path: Path):
        set_pause(tmp_path, duration_seconds=0.05)
        assert is_paused(tmp_path)
        time.sleep(0.1)
        assert get_pause(tmp_path) is None
        assert not (tmp_path / "memory" / "paused.json").exists()

    def test_parse_duration(self):
        assert parse_duration("30m") == 1800
        assert parse_duration("2h") == 7200
        assert parse_duration("1d") == 86400
        assert parse_duration("90") == 90
        assert parse_duration("2 hours") == 7200
        with pytest.raises(ValueError):
            parse_duration("soon")

    def test_daemon_alive_requires_live_pid_and_fresh_heartbeat(self, tmp_path: Path):
        assert not daemon_alive(tmp_path)
        write_daemon_state(tmp_path, status="captured")
        assert daemon_alive(tmp_path)  # our own pid, fresh heartbeat
        assert not daemon_alive(tmp_path, stale_after=0)


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------


class TestTimeParse:
    NOW = _dt.datetime(2026, 9, 4, 15, 30)  # a Friday

    def test_relative_duration(self):
        start, end = parse_when("2h", now=self.NOW)
        assert end is None
        assert start == pytest.approx((self.NOW - _dt.timedelta(hours=2)).timestamp())

    def test_yesterday_is_whole_day(self):
        start, end = parse_when("yesterday", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 3)
        assert _dt.datetime.fromtimestamp(end) == _dt.datetime(2026, 9, 4)

    def test_weekday_most_recent(self):
        start, end = parse_when("tuesday", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 1)
        assert _dt.datetime.fromtimestamp(end) == _dt.datetime(2026, 9, 2)

    def test_weekday_today_and_last(self):
        start, _ = parse_when("friday", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 4)
        start, _ = parse_when("last friday", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 8, 28)

    def test_day_part(self):
        start, end = parse_when("tuesday afternoon", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 1, 12)
        assert _dt.datetime.fromtimestamp(end) == _dt.datetime(2026, 9, 1, 17)

    def test_last_week_and_this_month(self):
        start, end = parse_when("last week", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 8, 24)
        assert _dt.datetime.fromtimestamp(end) == _dt.datetime(2026, 8, 31)
        start, end = parse_when("this month", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 1)
        assert end is None

    def test_last_month(self):
        start, end = parse_when("last month", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 8, 1)
        assert _dt.datetime.fromtimestamp(end) == _dt.datetime(2026, 9, 1)

    def test_iso_date_and_datetime(self):
        start, end = parse_when("2026-09-01", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 1)
        assert _dt.datetime.fromtimestamp(end) == _dt.datetime(2026, 9, 2)
        start, end = parse_when("2026-09-01 14:30", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 1, 14, 30)
        assert end is None

    def test_n_days_ago(self):
        start, end = parse_when("3 days ago", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 1)
        assert end is not None

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_when("whenever", now=self.NOW)

    def test_parse_range_combines(self):
        start, end = parse_range("tuesday", "thursday", now=self.NOW)
        assert _dt.datetime.fromtimestamp(start) == _dt.datetime(2026, 9, 1)
        assert _dt.datetime.fromtimestamp(end) == _dt.datetime(2026, 9, 4)
        start, end = parse_range(None, None, now=self.NOW)
        assert start == 0.0 and end is None
        with pytest.raises(ValueError):
            parse_range("today", "yesterday", now=self.NOW)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestStore:
    def test_add_writes_thumbnail_and_index(self, tmp_path: Path):
        with MemoryStore(tmp_path) as store:
            f = store.add(ts=time.time(), app="A", title="t", text="hello world", thumb=b"jpeg")
            assert f.id == 1
            assert (store.dir / f.thumb_path).read_bytes() == b"jpeg"
            assert store.get(1).text == "hello world"
            assert store.thumbnail(f) == b"jpeg"
            assert store.stats().frames == 1

    def test_search_finds_terms_and_snippets(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            hits = store.search("ECONNREFUSED")
            assert [h.app for h in hits] == ["Terminal"]
            assert "ECONNREFUSED" in (hits[0].snippet or hits[0].text)

            hits = store.search("invoice total")
            assert len(hits) == 1 and hits[0].app == "Preview"

    def test_search_matches_title_and_app(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            assert len(store.search("Grafana")) == 2
            assert len(store.search("slack")) == 1

    def test_search_falls_back_to_or(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            hits = store.search("invoice latency")  # never co-occur → OR
            apps = {h.app for h in hits}
            assert apps == {"Preview", "Google Chrome"}

    def test_search_prefix_and_punctuation_safe(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            assert len(store.search("INV-2026*")) == 1
            assert store.search('"$1,234.00"')  # phrase with punctuation
            assert store.search("(((") == store.timeline()  # no usable terms → timeline

    def test_time_and_app_filters(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            start, end = parse_when("today")
            hits = store.search("dashboard", start=start, end=end)
            assert len(hits) == 1
            hits = store.search("dashboard", app="chrome")
            assert len(hits) == 2
            assert store.search("dashboard", app="Terminal") == []

    def test_timeline_and_apps(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            tl = store.timeline(limit=2)
            assert [f.app for f in tl] == ["Slack", "Google Chrome"]
            assert store.apps()[0] == ("Google Chrome", 2)
            start, _ = parse_when("today")
            assert len(store.timeline(start=start)) == 2

    def test_delete_removes_thumbnails(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            f = store.get(1)
            path = store.dir / f.thumb_path
            assert path.exists()
            assert store.delete([1]) == 1
            assert not path.exists()
            assert store.get(1) is None
            assert store.search("ECONNREFUSED") == []

    def test_delete_range_and_app(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            start, end = parse_when("today")
            assert store.delete_range(start, end) == 2
            assert store.delete_app("chrome") == 1
            assert store.stats().frames == 2

    def test_retention(self, tmp_path: Path):
        now = time.time()
        with MemoryStore(tmp_path) as store:
            for days in (10, 5, 2.5, 1, 0):
                store.add(ts=now - days * 86400, app="A", title="", text=f"{days}d", thumb=b"x")
            assert store.enforce_retention(0) == 0  # disabled
            assert store.enforce_retention(2) == 3  # 10d, 5d, 2.5d
            assert {f.text for f in store.timeline()} == {"1d", "0d"}

    def test_storage_cap_rolls_oldest_first(self, tmp_path: Path):
        with MemoryStore(tmp_path) as store:
            for i in range(20):
                store.add(ts=1000 + i, app="A", title="", text="x" * 10, thumb=b"j" * 100_000)
            before = store.stats()
            assert before.frames == 20
            assert before.total_bytes > 2_000_000
            deleted = store.enforce_cap(1_000_000)
            assert 0 < deleted < 20
            remaining = store.timeline(limit=100, newest_first=False)
            # Oldest go first; newest survive.
            assert remaining[-1].ts == 1019
            assert remaining[0].ts > 1000
            assert store.stats().total_bytes <= 1_000_000
            assert store.enforce_cap(10**12) == 0  # under cap → no-op

    def test_cap_never_deletes_last_frame(self, tmp_path: Path):
        with MemoryStore(tmp_path) as store:
            for i in range(3):
                store.add(ts=1000 + i, app="A", title="", text="", thumb=b"j" * 100_000)
            assert store.enforce_cap(1) == 2
            assert store.stats().frames == 1
            assert store.get(3) is not None

    def test_clear(self, tmp_path: Path):
        with seeded_store(tmp_path) as store:
            assert store.clear() == 5
            assert store.stats().frames == 0
            assert not any(store.thumbs_dir.iterdir())

    def test_terms_tokeniser(self):
        assert _terms('error "connection refused" port:5432') == ["error", "connection refused", "port:5432"]
        assert _terms("...") == []
        assert _terms("foo*") == ["foo*"]


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class _Win:
    def __init__(self, app, title):
        self.app_name = app
        self.title = title


class _Computer:
    """Fake Computer: serves a queue of (png, app, title)."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.captures = 0

    async def focused_window(self):
        _, app, title = self.frames[0]
        return _Win(app, title)

    async def capture(self, **_):
        from opendesk.computer.types import Pixmap, PixmapFormat
        png, _, _ = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        self.captures += 1
        return Pixmap(data=png, format=PixmapFormat.PNG, width=64, height=48,
                      logical_width=64, logical_height=48)


def _recorder(tmp_path, frames, **kw):
    from opendesk.memory.recorder import ScreenMemoryRecorder
    cfg = kw.pop("config", MemoryConfig(deny_apps=["1Password"]))
    ocr = kw.pop("ocr", lambda png: f"text for {len(png)} bytes")
    return ScreenMemoryRecorder(tmp_path, computer=_Computer(frames), config=cfg, ocr=ocr, **kw)


class TestRecorder:
    @pytest.mark.asyncio
    async def test_tick_captures_and_indexes(self, tmp_path: Path):
        rec = _recorder(tmp_path, [(_png(text_seed=1), "Terminal", "zsh")], ocr=lambda _: "npm ERR! code ELIFECYCLE")
        assert await rec.tick() == "captured"
        rec.store.close()
        with MemoryStore(tmp_path) as store:
            hits = store.search("ELIFECYCLE")
            assert len(hits) == 1
            assert hits[0].app == "Terminal" and hits[0].title == "zsh"
            assert store.thumbnail(hits[0])[:3] == b"\xff\xd8\xff"  # JPEG magic
            assert hits[0].width == 64

    @pytest.mark.asyncio
    async def test_duplicate_frames_skipped(self, tmp_path: Path):
        png = _png(text_seed=3)
        rec = _recorder(tmp_path, [(png, "A", ""), (png, "A", ""), (_png(color=(0, 0, 0)), "A", "")])
        assert await rec.tick() == "captured"
        assert await rec.tick() == "duplicate"
        assert await rec.tick() == "captured"
        assert rec.captured == 2 and rec.skipped == 1

    @pytest.mark.asyncio
    async def test_denied_app_not_captured(self, tmp_path: Path):
        rec = _recorder(tmp_path, [(_png(), "1Password 8", "Vault")])
        assert (await rec.tick()).startswith("denied:")
        assert rec.computer.captures == 0
        assert rec.store.stats().frames == 0

    @pytest.mark.asyncio
    async def test_denied_by_window_title(self, tmp_path: Path):
        cfg = MemoryConfig(deny_apps=["bank"])
        rec = _recorder(tmp_path, [(_png(), "Safari", "Acme Bank — Login")], config=cfg)
        assert (await rec.tick()).startswith("denied:")

    @pytest.mark.asyncio
    async def test_paused_skips_capture(self, tmp_path: Path):
        rec = _recorder(tmp_path, [(_png(), "A", "")])
        set_pause(tmp_path)
        assert await rec.tick() == "paused"
        assert rec.computer.captures == 0
        clear_pause(tmp_path)
        assert await rec.tick() == "captured"

    @pytest.mark.asyncio
    async def test_toggle_pause_hotkey_handler(self, tmp_path: Path):
        rec = _recorder(tmp_path, [(_png(), "A", "")])
        assert rec.toggle_pause() is True
        assert is_paused(tmp_path)
        assert rec.toggle_pause() is False
        assert not is_paused(tmp_path)

    @pytest.mark.asyncio
    async def test_ocr_failure_stores_frame_without_text(self, tmp_path: Path):
        rec = _recorder(tmp_path, [(_png(), "A", "")], ocr=lambda _: "OCR not available. Install …")
        assert await rec.tick() == "captured"
        assert rec.store.get(1).text == ""

    @pytest.mark.asyncio
    async def test_capture_error_is_reported_not_raised(self, tmp_path: Path):
        rec = _recorder(tmp_path, [(_png(), "A", "")])

        async def boom(**_):
            raise RuntimeError("no screen recording permission")
        rec.computer.capture = boom
        assert (await rec.tick()).startswith("error:capture:")

    @pytest.mark.asyncio
    async def test_config_reload_picks_up_deny_list(self, tmp_path: Path):
        cfg = MemoryConfig(deny_apps=[])
        save_config(cfg, tmp_path)
        rec = _recorder(tmp_path, [(_png(text_seed=i), "Slack", "") for i in range(1, 30)], config=cfg)
        assert await rec.tick() == "captured"
        cfg2 = load_config(tmp_path)
        cfg2.deny_add("Slack")
        save_config(cfg2, tmp_path)
        statuses = [await rec.tick() for _ in range(10)]
        assert statuses[-1].startswith("denied:")

    @pytest.mark.asyncio
    async def test_interval_override_survives_reload(self, tmp_path: Path):
        rec = _recorder(tmp_path, [(_png(), "A", "")], interval_override=7)
        assert rec.config.interval_seconds == 7
        rec.reload_config()
        assert rec.config.interval_seconds == 7

    @pytest.mark.asyncio
    async def test_housekeep_applies_cap(self, tmp_path: Path):
        cfg = MemoryConfig(storage_cap_mb=1, retention_days=365)
        rec = _recorder(tmp_path, [(_png(), "A", "")], config=cfg)
        now = time.time()
        for i in range(30):
            rec.store.add(ts=now - 30 + i, app="A", title="", text="", thumb=b"x" * 100_000)
        result = rec.housekeep()
        assert result["expired"] == 0
        assert result["capped"] > 0
        assert rec.store.stats().total_bytes <= 1024 * 1024

    @pytest.mark.asyncio
    async def test_run_loop_stops_cleanly(self, tmp_path: Path):
        import asyncio
        cfg = MemoryConfig(interval_seconds=5, pause_hotkey="")
        rec = _recorder(tmp_path, [(_png(text_seed=1), "A", ""), (_png(text_seed=9), "A", "")], config=cfg)
        task = asyncio.create_task(rec.run())
        await asyncio.sleep(0.3)
        assert daemon_alive(tmp_path)
        rec.stop()
        await asyncio.wait_for(task, timeout=3)
        assert not daemon_alive(tmp_path)
        assert rec.captured >= 1


class TestImageHelpers:
    def test_thumbnail_downscales(self):
        from opendesk.memory.recorder import make_thumbnail
        jpg, w, h = make_thumbnail(_png(size=(1600, 900)), width=400)
        assert (w, h) == (400, 225)
        assert jpg[:3] == b"\xff\xd8\xff"

    def test_signature_delta(self):
        from opendesk.memory.recorder import frame_signature, signature_delta
        a = frame_signature(_png(color=(255, 255, 255)))
        b = frame_signature(_png(color=(250, 250, 250)))
        c = frame_signature(_png(color=(0, 0, 0)))
        assert signature_delta(a, b) < 0.05
        assert signature_delta(a, c) > 0.9
        assert signature_delta(None, a) == 1.0


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


def _ctx(home: Path) -> ToolContext:
    from tests._fakes import FakeComputer
    return ToolContext(session_id="mem-test", computer=FakeComputer(), metadata={"opendesk_home": str(home)})


async def call(tool: MemoryTool, home: Path, **kw):
    return await tool.execute(_ctx(home), tool.parse_params(kw))


class TestMemoryTool:
    @pytest.mark.asyncio
    async def test_registered_and_schema(self):
        from opendesk.registry import create_registry
        reg = create_registry()
        assert "memory" in reg
        schema = reg.get("memory").get_schema()
        assert "search" in schema["properties"]["action"]["enum"]

    @pytest.mark.asyncio
    async def test_search_returns_hits_with_ids(self, tmp_path: Path):
        seeded_store(tmp_path).close()
        tool = MemoryTool()
        r = await call(tool, tmp_path, action="search", query="error", app="Terminal")
        assert not r.error
        assert "ECONNREFUSED" in r.output
        assert r.metadata["count"] == 1
        assert "#1" in r.output

    @pytest.mark.asyncio
    async def test_search_with_time_phrase(self, tmp_path: Path):
        seeded_store(tmp_path).close()
        r = await call(MemoryTool(), tmp_path, action="search", query="dashboard", since="today")
        assert r.metadata["count"] == 1
        r = await call(MemoryTool(), tmp_path, action="search", query="dashboard", since="this week")
        assert r.metadata["count"] >= 1

    @pytest.mark.asyncio
    async def test_search_requires_query_and_validates_time(self, tmp_path: Path):
        r = await call(MemoryTool(), tmp_path, action="search")
        assert r.error
        r = await call(MemoryTool(), tmp_path, action="search", query="x", since="whenever")
        assert r.error and "Cannot parse" in r.output

    @pytest.mark.asyncio
    async def test_empty_index_hints_to_start_daemon(self, tmp_path: Path):
        r = await call(MemoryTool(), tmp_path, action="search", query="anything")
        assert "opendesk memory start" in r.output

    @pytest.mark.asyncio
    async def test_show_returns_text_and_thumbnail(self, tmp_path: Path):
        seeded_store(tmp_path).close()
        r = await call(MemoryTool(), tmp_path, action="show", id=3)
        assert "INV-2026-0042" in r.output
        assert r.attachments and r.attachments[0].media_type == "image/jpeg"
        assert r.attachments[0].content == b"jpg3"
        r = await call(MemoryTool(), tmp_path, action="show", id=3, include_image=False)
        assert not r.attachments
        r = await call(MemoryTool(), tmp_path, action="show", id=999)
        assert r.error

    @pytest.mark.asyncio
    async def test_timeline_groups_apps(self, tmp_path: Path):
        seeded_store(tmp_path).close()
        r = await call(MemoryTool(), tmp_path, action="timeline", since="this week", app="chrome")
        assert r.metadata["count"] == 2
        assert "Grafana" in r.output

    @pytest.mark.asyncio
    async def test_status(self, tmp_path: Path):
        seeded_store(tmp_path).close()
        r = await call(MemoryTool(), tmp_path, action="status")
        assert "frames:    5" in r.output
        assert "NOT running" in r.output
        assert r.metadata["daemon_alive"] is False

    @pytest.mark.asyncio
    async def test_pause_resume(self, tmp_path: Path):
        r = await call(MemoryTool(), tmp_path, action="pause", duration="1h")
        assert "paused" in r.output.lower()
        assert is_paused(tmp_path)
        r = await call(MemoryTool(), tmp_path, action="status")
        assert "PAUSED" in r.output
        r = await call(MemoryTool(), tmp_path, action="resume")
        assert not is_paused(tmp_path)

    @pytest.mark.asyncio
    async def test_deny_list_management(self, tmp_path: Path):
        r = await call(MemoryTool(), tmp_path, action="deny", deny_add="Signal")
        assert "Signal" in r.metadata["deny_apps"]
        assert "Signal" in load_config(tmp_path).deny_apps
        r = await call(MemoryTool(), tmp_path, action="deny", deny_remove="Signal")
        assert "Signal" not in load_config(tmp_path).deny_apps

    @pytest.mark.asyncio
    async def test_config_update(self, tmp_path: Path):
        r = await call(MemoryTool(), tmp_path, action="config", interval_seconds=15, storage_cap_mb=512, retention_days=7)
        cfg = load_config(tmp_path)
        assert (cfg.interval_seconds, cfg.storage_cap_mb, cfg.retention_days) == (15, 512, 7)
        assert "Updated" in r.output

    @pytest.mark.asyncio
    async def test_delete_requires_scope_and_confirm(self, tmp_path: Path):
        seeded_store(tmp_path).close()
        r = await call(MemoryTool(), tmp_path, action="delete")
        assert r.error
        r = await call(MemoryTool(), tmp_path, action="delete", app="chrome")
        assert "would be deleted" in r.output and r.metadata["count"] == 2
        with MemoryStore(tmp_path) as s:
            assert s.stats().frames == 5
        r = await call(MemoryTool(), tmp_path, action="delete", app="chrome", confirm=True)
        assert r.metadata["count"] == 2
        with MemoryStore(tmp_path) as s:
            assert s.stats().frames == 3

    @pytest.mark.asyncio
    async def test_permission_handler_is_consulted(self, tmp_path: Path):
        from opendesk.tools.base import PermissionDeniedError

        async def deny(tool, arg, desc):
            raise PermissionDeniedError("nope")

        ctx = ToolContext(permission_handler=deny, metadata={"opendesk_home": str(tmp_path)})
        tool = MemoryTool()
        with pytest.raises(PermissionDeniedError):
            await tool.execute(ctx, tool.parse_params({"action": "status"}))

    @pytest.mark.asyncio
    async def test_home_via_constructor(self, tmp_path: Path):
        seeded_store(tmp_path).close()
        tool = MemoryTool(home=tmp_path)
        r = await tool.execute(ToolContext(), tool.parse_params({"action": "status"}))
        assert "frames:    5" in r.output

    @pytest.mark.asyncio
    async def test_mcp_dispatch_not_peer_aware(self, tmp_path: Path):
        from opendesk.integrations.mcp import MCPDispatcher, PEER_AWARE_TOOLS, TextResult
        from opendesk.integrations.mcp_session import MCPSession
        from opendesk.registry import ToolRegistry
        from tests._fakes import FakeComputer

        assert "memory" not in PEER_AWARE_TOOLS
        reg = ToolRegistry()
        reg.register(MemoryTool(home=tmp_path))
        session = MCPSession(home=tmp_path, local=FakeComputer())
        disp = MCPDispatcher(reg, session)
        entries = await disp.list_tools()
        mem = next(e for e in entries if e.name == "memory")
        assert "peer" not in mem.schema["properties"]
        out = await disp.call_tool("memory", {"action": "status"})
        assert isinstance(out[0], TextResult) and "Screen memory status" in out[0].text


# ---------------------------------------------------------------------------
# Service renderers + CLI wiring
# ---------------------------------------------------------------------------


class TestServiceAndCli:
    def test_launchd_plist(self):
        import xml.etree.ElementTree as ET
        from opendesk.memory.service import LAUNCHD_LABEL, _render_launchd_plist
        plist = _render_launchd_plist("/usr/bin/python3", 20, Path("/tmp/h"))
        ET.fromstring(plist)
        assert f"<string>{LAUNCHD_LABEL}</string>" in plist
        for s in ("memory", "start", "--interval", "20", "--home", "/tmp/h"):
            assert f"<string>{s}</string>" in plist

    def test_systemd_unit(self):
        from opendesk.memory.service import _render_systemd_unit
        unit = _render_systemd_unit("/usr/bin/python3", None, None)
        assert "ExecStart=/usr/bin/python3 -m opendesk.cli memory start\n" in unit
        assert "--home" not in unit

    def test_schtasks_command(self):
        from opendesk.memory.service import _render_schtasks_command
        cmd = _render_schtasks_command("C:\\py\\python.exe", 45, Path("C:\\h"))
        assert cmd.startswith('"C:\\py\\python.exe" -m opendesk.cli memory start')
        assert '--interval 45 --home "C:\\h"' in cmd

    def test_cli_memory_help(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, "-m", "opendesk.cli", "memory", "--help"],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0
        for sub in ("start", "status", "pause", "resume", "search", "deny", "clear"):
            assert sub in r.stdout

    def test_cli_search_and_status(self, tmp_path: Path):
        import subprocess, sys
        seeded_store(tmp_path).close()
        r = subprocess.run(
            [sys.executable, "-m", "opendesk.cli", "memory", "search", "invoice", "--home", str(tmp_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0 and "INV-2026-0042" in r.stdout
        r = subprocess.run(
            [sys.executable, "-m", "opendesk.cli", "memory", "status", "--home", str(tmp_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0 and "frames:    5" in r.stdout
