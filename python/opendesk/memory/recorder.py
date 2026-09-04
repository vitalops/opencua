"""Background capture loop for screen memory.

Every ``interval_seconds`` the recorder:

1. Checks the shared pause flag (hotkey / ``memory(action=pause)`` / CLI).
2. Reads the frontmost app + window title and consults the deny list.
3. Grabs the screen through the :class:`~opendesk.computer.Computer`.
4. Skips the frame when it is visually identical to the previous one.
5. Runs OCR locally, builds a JPEG thumbnail, and writes both to the
   :class:`~opendesk.memory.store.MemoryStore`.
6. Periodically enforces retention and the storage cap.

Nothing leaves the machine: capture, OCR, and storage are all local.
"""

from __future__ import annotations

import asyncio
import io
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from opendesk.memory.config import (
    MemoryConfig,
    clear_daemon_state,
    clear_pause,
    get_pause,
    load_config,
    set_pause,
    write_daemon_state,
)
from opendesk.memory.store import MemoryStore

log = logging.getLogger("opendesk.memory")

OCRFunc = Callable[[bytes], str]


# ---------------------------------------------------------------------------
# Image helpers (pure functions, unit-testable)
# ---------------------------------------------------------------------------


def make_thumbnail(png_bytes: bytes, width: int = 480, quality: int = 60) -> tuple[bytes, int, int]:
    """Downscale a PNG to a JPEG thumbnail.  Returns ``(jpeg, w, h)``."""
    from PIL import Image  # type: ignore[import-not-found]

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if img.width > width:
        ratio = width / img.width
        img = img.resize((width, max(1, int(img.height * ratio))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), img.width, img.height


def frame_signature(png_bytes: bytes, size: int = 16) -> list[int]:
    """Tiny grayscale fingerprint used for duplicate detection."""
    from PIL import Image  # type: ignore[import-not-found]

    img = Image.open(io.BytesIO(png_bytes)).convert("L").resize((size, size), Image.BILINEAR)
    return list(img.getdata())


def signature_delta(a: Optional[list[int]], b: Optional[list[int]]) -> float:
    """Mean absolute difference between two signatures, normalised to 0–1."""
    if not a or not b or len(a) != len(b):
        return 1.0
    total = sum(abs(x - y) for x, y in zip(a, b))
    return total / (255.0 * len(a))


def _default_ocr(png_bytes: bytes) -> str:
    from opendesk.computer.ocr import ocr_image
    return ocr_image(png_bytes)


def _ocr_failed(text: str) -> bool:
    return (
        text.startswith("OCR not available")
        or text.startswith("OCR error")
        or text.startswith("pytesseract error")
    )


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class ScreenMemoryRecorder:
    """Drives one capture tick at a time.  :meth:`run` loops forever."""

    def __init__(
        self,
        home: Optional[Path] = None,
        *,
        computer: Any = None,
        config: Optional[MemoryConfig] = None,
        store: Optional[MemoryStore] = None,
        ocr: Optional[OCRFunc] = None,
        interval_override: Optional[float] = None,
    ) -> None:
        self.home = home
        self.config = config or load_config(home)
        self._interval_override = float(interval_override) if interval_override else None
        if self._interval_override:
            self.config.interval_seconds = self._interval_override
        self.store = store or MemoryStore(home)
        self._computer = computer
        self._ocr: OCRFunc = ocr or _default_ocr
        self._last_sig: Optional[list[int]] = None
        self._ticks = 0
        self._captured = 0
        self._skipped = 0
        self._last_status = "idle"
        self._ocr_warned = False
        self._stop = asyncio.Event()
        self._hotkey_listener: Any = None

    # -- accessors -----------------------------------------------------

    @property
    def computer(self) -> Any:
        if self._computer is None:
            from opendesk.computer.local import LocalComputer
            self._computer = LocalComputer()
        return self._computer

    @property
    def captured(self) -> int:
        return self._captured

    @property
    def skipped(self) -> int:
        return self._skipped

    @property
    def last_status(self) -> str:
        return self._last_status

    def reload_config(self) -> None:
        """Pick up deny-list / interval edits made by other processes."""
        fresh = load_config(self.home)
        # Keep a CLI interval override sticky across reloads.
        if self._interval_override:
            fresh.interval_seconds = self._interval_override
        self.config = fresh

    # -- one tick --------------------------------------------------------

    async def tick(self) -> str:
        """Perform one capture attempt.  Returns a short status string."""
        self._ticks += 1
        if self._ticks % 10 == 0:
            self.reload_config()

        pause = get_pause(self.home)
        if pause is not None:
            return self._done("paused")

        app, title = await self._frontmost()
        if self.config.is_denied(app, title):
            return self._done(f"denied:{app or title}")

        try:
            pixmap = await self.computer.capture()
        except Exception as exc:
            log.warning("capture failed: %s", exc)
            return self._done(f"error:capture:{exc}")

        png = pixmap.data
        loop = asyncio.get_running_loop()
        try:
            sig = await loop.run_in_executor(None, frame_signature, png)
        except Exception as exc:
            log.warning("signature failed: %s", exc)
            sig = None
        if sig is not None and self._last_sig is not None:
            if signature_delta(sig, self._last_sig) < self.config.dedupe_threshold:
                return self._done("duplicate")
        self._last_sig = sig

        try:
            text = await loop.run_in_executor(None, self._ocr, png)
        except Exception as exc:
            text = f"OCR error: {exc}"
        if _ocr_failed(text):
            if not self._ocr_warned:
                log.warning("OCR unavailable — frames will be stored without text: %s", text.splitlines()[0])
                self._ocr_warned = True
            text = ""
        elif text == "(no text detected)":
            text = ""

        try:
            thumb, tw, th = await loop.run_in_executor(
                None, make_thumbnail, png,
                self.config.thumbnail_width, self.config.thumbnail_quality,
            )
        except Exception as exc:
            log.warning("thumbnail failed: %s", exc)
            thumb, tw, th = None, 0, 0

        frame = self.store.add(
            ts=time.time(), app=app, title=title, text=text,
            thumb=thumb, width=tw, height=th,
        )
        self._captured += 1
        log.debug("captured frame %s app=%r title=%r chars=%d", frame.id, app, title, len(text))

        if self._captured % 20 == 1:
            await loop.run_in_executor(None, self.housekeep)
        return self._done("captured")

    def housekeep(self) -> dict[str, int]:
        """Apply retention + storage cap.  Safe to call from any thread."""
        expired = self.store.enforce_retention(self.config.retention_days)
        capped = self.store.enforce_cap(self.config.storage_cap_mb * 1024 * 1024)
        if expired or capped:
            log.info("housekeeping: removed %d expired, %d over-cap frames", expired, capped)
        return {"expired": expired, "capped": capped}

    def _done(self, status: str) -> str:
        self._last_status = status
        if status != "captured":
            self._skipped += 1
        write_daemon_state(
            self.home,
            status=status,
            captured=self._captured,
            skipped=self._skipped,
            interval=self.config.interval_seconds,
        )
        return status

    async def _frontmost(self) -> tuple[str, str]:
        try:
            win = await self.computer.focused_window()
        except Exception:
            return "", ""
        if win is None:
            return "", ""
        app = (getattr(win, "app_name", "") or "").strip()
        title = (getattr(win, "title", "") or "").strip()
        if title == app:
            title = ""
        return app, title

    # -- loop --------------------------------------------------------------

    async def run(self) -> None:
        """Capture until :meth:`stop` is called."""
        write_daemon_state(self.home, status="starting", captured=0, skipped=0,
                           interval=self.config.interval_seconds)
        self._start_hotkey()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    await self.tick()
                except Exception as exc:  # never let one bad tick kill the loop
                    log.exception("tick failed: %s", exc)
                    self._done(f"error:{exc}")
                elapsed = time.monotonic() - started
                delay = max(1.0, self.config.interval_seconds - elapsed)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._stop_hotkey()
            clear_daemon_state(self.home)
            self.store.close()

    def stop(self) -> None:
        self._stop.set()

    # -- pause hotkey --------------------------------------------------------

    def toggle_pause(self) -> bool:
        """Flip the pause flag.  Returns the new paused state."""
        if get_pause(self.home) is not None:
            clear_pause(self.home)
            log.info("screen memory resumed (hotkey)")
            print(f"[{_now()}] screen memory resumed", flush=True)
            return False
        set_pause(self.home, reason="hotkey")
        log.info("screen memory paused (hotkey)")
        print(f"[{_now()}] screen memory paused — press the hotkey again to resume", flush=True)
        return True

    def _start_hotkey(self) -> None:
        combo = (self.config.pause_hotkey or "").strip()
        if not combo:
            return
        try:
            from pynput import keyboard  # type: ignore[import-not-found]
        except ImportError:
            log.warning(
                "pynput not installed — pause hotkey disabled. "
                "pip install 'opendesk[memory]' to enable it."
            )
            return
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys({combo: self.toggle_pause})
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
            log.info("pause hotkey armed: %s", combo)
        except Exception as exc:
            log.warning("could not register pause hotkey %r: %s", combo, exc)
            self._hotkey_listener = None

    def _stop_hotkey(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None


# ---------------------------------------------------------------------------
# Daemon entry point
# ---------------------------------------------------------------------------


def start_daemon(home: Optional[Path] = None, *, interval: Optional[float] = None) -> None:
    """Blocking entry used by ``opendesk memory start``."""
    from opendesk.computer.ocr import available_backend, warm_up

    config = load_config(home)
    backend = available_backend()
    if backend == "macos-vision":
        print("Preparing the macOS Vision OCR helper (one-time compile, may take a minute)…", flush=True)
        warm_up()
    if backend is None:
        print(
            "WARNING: no OCR backend available — frames will be stored without text.\n"
            "  pip install pytesseract  (and install the tesseract binary)",
            file=sys.stderr,
        )

    recorder = ScreenMemoryRecorder(home, config=config, interval_override=interval)
    config = recorder.config
    store_dir = recorder.store.dir
    print("opendesk screen memory")
    print(f"  store:      {store_dir}")
    print(f"  interval:   every {config.interval_seconds:g}s")
    print(f"  cap:        {config.storage_cap_mb} MB, retention {config.retention_days} days")
    print(f"  deny list:  {', '.join(config.deny_apps) or '(empty)'}")
    print(f"  OCR:        {backend or 'unavailable'}")
    print(f"  pause key:  {config.pause_hotkey or '(disabled)'}")
    if get_pause(home) is not None:
        print("  state:      PAUSED (resume with `opendesk memory resume` or the hotkey)")
    print("Ctrl-C to stop.\n")

    async def _main() -> None:
        loop = asyncio.get_running_loop()

        def _request_stop(*_: Any) -> None:
            recorder.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, _request_stop)
        await recorder.run()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    print(f"\nStopped. {recorder.captured} frames captured this run.")


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%H:%M:%S")
