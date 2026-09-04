"""LocalComputer — :class:`~opendesk.computer.Computer` for the machine
running this process.

Wraps the existing screen-capture, accessibility, and input backends behind
the unified Computer interface so every tool (and every integration) can
target a single API regardless of whether the computer is local or remote.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from opendesk.computer.base import CapabilityUnsupported, Computer, InputEvent
from opendesk.computer.capture import capture_screen
from opendesk.computer.types import (
    Capability,
    CapabilityManifest,
    ClipboardContents,
    ClipboardEntry,
    CompletedCommand,
    Display,
    DisplayFrame,
    Environment,
    FileEntry,
    KeyAction,
    KeyEvent,
    Modifier,
    Notification,
    Pixmap,
    PixmapFormat,
    Point,
    PointerAction,
    PointerButton,
    PointerEvent,
    Process,
    Rect,
    TextInput,
    UIElement,
    Window,
)

_PLATFORM = platform.system()


# ---------------------------------------------------------------------------
# Lazy backend imports
# ---------------------------------------------------------------------------


def _pyautogui() -> Any:
    try:
        import pyautogui  # type: ignore[import-not-found]
        return pyautogui
    except ImportError as exc:
        raise ImportError(
            "pyautogui is required for LocalComputer input: pip install 'opendesk[core]'"
        ) from exc


def _check_macos_accessibility() -> None:
    if _PLATFORM != "Darwin":
        return
    r = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to get name of first process'],
        capture_output=True, text=True, timeout=5,
    )
    if r.returncode != 0 and "not allowed" in (r.stderr + r.stdout).lower():
        raise RuntimeError(
            "macOS Accessibility permission is required. Grant it under "
            "System Settings -> Privacy & Security -> Accessibility."
        )


def _osascript(script: str, *, timeout: int = 15) -> str:
    r = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# Modifier / key name translation
# ---------------------------------------------------------------------------


_PYAUTOGUI_KEY_ALIASES = {
    "return": "enter",
    "esc": "escape",
    "del": "delete",
    "pgup": "pageup",
    "pgdn": "pagedown",
}


def _to_pyautogui_key(keysym: str) -> str:
    """Translate a normalised keysym to the name pyautogui expects."""
    k = keysym.lower()
    return _PYAUTOGUI_KEY_ALIASES.get(k, k)


def _modifier_to_pyautogui(mod: Modifier) -> str:
    if mod is Modifier.META:
        return "command" if _PLATFORM == "Darwin" else ("winleft" if _PLATFORM == "Windows" else "super")
    if mod is Modifier.ALT:
        return "alt"
    if mod is Modifier.CTRL:
        return "ctrl"
    if mod is Modifier.SHIFT:
        return "shift"
    if mod is Modifier.FN:
        return "fn"
    if mod is Modifier.CAPS_LOCK:
        return "capslock"
    return mod.value


# ---------------------------------------------------------------------------
# LocalComputer
# ---------------------------------------------------------------------------


class LocalComputer(Computer):
    """The computer this process is running on.

    All methods do their blocking work in a thread executor so the event loop
    stays responsive.  Most heavy backends (mss, pyautogui, AppleScript,
    pyatspi, pywinauto) are imported lazily so importing this module never
    forces an optional dependency to load.
    """

    BACKEND = "local"

    def __init__(self) -> None:
        self._closed = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def capabilities(self) -> CapabilityManifest:
        caps: set[Capability] = {
            Capability.DISPLAY_CAPTURE,
            Capability.DISPLAY_MULTI,
            Capability.DISPLAY_STREAM,
            Capability.INPUT_POINTER,
            Capability.INPUT_KEYBOARD,
            Capability.INPUT_TEXT,
            Capability.UI_TREE,
            Capability.UI_ACTIONS,
            Capability.WINDOWS_LIST,
            Capability.APP_LIFECYCLE,
            Capability.CLIPBOARD_READ,
            Capability.CLIPBOARD_WRITE,
            Capability.FS_READ,
            Capability.FS_WRITE,
            Capability.PROCESS_LIST,
            Capability.PROCESS_SHELL,
            Capability.PROCESS_SPAWN,
            Capability.POWER_LOCK,
        }
        return CapabilityManifest(
            capabilities=caps,
            limits={"display.stream": {"max_fps": 30}},
            backend=f"local/{_PLATFORM.lower()}",
        )

    async def environment(self) -> Environment:
        return await asyncio.to_thread(self._environment_sync)

    def _environment_sync(self) -> Environment:
        import locale as _locale
        try:
            loc = _locale.getlocale()[0] or ""
        except Exception:
            loc = ""
        tz = time.strftime("%Z") if time.tzname else ""
        displays = self._displays_sync()
        return Environment(
            os=_PLATFORM,
            os_version=platform.release(),
            hostname=platform.node(),
            locale=loc,
            timezone=tz,
            displays=displays,
        )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    async def displays(self) -> list[Display]:
        return await asyncio.to_thread(self._displays_sync)

    def _displays_sync(self) -> list[Display]:
        try:
            import mss  # type: ignore[import-not-found]
        except ImportError:
            return []
        out: list[Display] = []
        with mss.mss() as sct:
            mons = list(sct.monitors)
            for i, m in enumerate(mons[1:], start=1):
                out.append(
                    Display(
                        id=str(i),
                        bounds=Rect(x=m["left"], y=m["top"], width=m["width"], height=m["height"]),
                        primary=(i == 1),
                        name=f"display-{i}",
                    )
                )
        return out

    async def capture(
        self,
        *,
        display_id: Optional[str] = None,
        region: Optional[Rect] = None,
        downscale: bool = True,
    ) -> Pixmap:
        return await asyncio.to_thread(self._capture_sync, display_id, region, downscale)

    def _capture_sync(
        self, display_id: Optional[str], region: Optional[Rect], downscale: bool
    ) -> Pixmap:
        capture_region: Optional[tuple[int, int, int, int]] = None
        if region is not None:
            capture_region = (int(region.x), int(region.y), int(region.width), int(region.height))

        png_bytes, width, height = capture_screen(capture_region)

        logical_w, logical_h = width, height
        try:
            pag = _pyautogui()
            lw, lh = pag.size()
            logical_w, logical_h = int(lw), int(lh)
        except Exception:
            pass

        if region is not None:
            logical_w, logical_h = int(region.width), int(region.height)

        return Pixmap(
            data=png_bytes,
            format=PixmapFormat.PNG,
            width=width,
            height=height,
            logical_width=logical_w,
            logical_height=logical_h,
            display_id=display_id,
        )

    async def cursor_position(self) -> Point:
        return await asyncio.to_thread(self._cursor_position_sync)

    def _cursor_position_sync(self) -> Point:
        pag = _pyautogui()
        x, y = pag.position()
        return Point(x=x, y=y)

    # ------------------------------------------------------------------
    # Display subscription — poll-based default
    # ------------------------------------------------------------------

    def subscribe_display(
        self,
        *,
        display_id: Optional[str] = None,
        fps: int = 30,
        region: Optional[Rect] = None,
    ) -> AsyncIterator[DisplayFrame]:
        return self._display_stream(display_id, fps, region)

    async def _display_stream(
        self, display_id: Optional[str], fps: int, region: Optional[Rect],
    ) -> AsyncIterator[DisplayFrame]:
        period = 1.0 / max(1, min(fps, 30))
        n = 0
        while True:
            pixmap = await self.capture(display_id=display_id, region=region)
            yield DisplayFrame(pixmap=pixmap, frame_number=n, keyframe=True)
            n += 1
            await asyncio.sleep(period)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    async def pointer(self, event: PointerEvent) -> None:
        await asyncio.to_thread(self._pointer_sync, event)

    def _pointer_sync(self, event: PointerEvent) -> None:
        _check_macos_accessibility()
        pag = _pyautogui()
        pag.FAILSAFE = True
        x, y = int(event.point.x), int(event.point.y)

        if event.action is PointerAction.MOVE:
            pag.moveTo(x, y, duration=0)
            return

        if event.action is PointerAction.SCROLL:
            if abs(event.dy) >= abs(event.dx):
                pag.scroll(int(event.dy), x=x, y=y)
            else:
                pag.hscroll(int(event.dx), x=x, y=y)
            return

        button = (event.button or PointerButton.LEFT).value
        if event.action is PointerAction.DOWN:
            pag.moveTo(x, y, duration=0)
            pag.mouseDown(button=button)
            return
        if event.action is PointerAction.UP:
            pag.moveTo(x, y, duration=0)
            pag.mouseUp(button=button)
            return
        raise ValueError(f"Unknown pointer action: {event.action!r}")

    async def key(self, event: KeyEvent) -> None:
        await asyncio.to_thread(self._key_sync, event)

    def _key_sync(self, event: KeyEvent) -> None:
        _check_macos_accessibility()
        pag = _pyautogui()
        keyname = _to_pyautogui_key(event.keysym)
        if event.action is KeyAction.DOWN:
            pag.keyDown(keyname)
        else:
            pag.keyUp(keyname)

    async def text(self, text_input: TextInput) -> None:
        await asyncio.to_thread(self._text_sync, text_input)

    def _text_sync(self, text_input: TextInput) -> None:
        """Insert text via clipboard-paste for full Unicode support."""
        _check_macos_accessibility()
        text = text_input.text
        if not text:
            return
        if _PLATFORM == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            time.sleep(0.05)
            _pyautogui().hotkey("command", "v")
            return
        if _PLATFORM == "Linux":
            for clip_cmd in (
                ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
            ):
                try:
                    subprocess.run(clip_cmd, input=text.encode("utf-8"), check=True,
                                   capture_output=True, timeout=5)
                    time.sleep(0.05)
                    _pyautogui().hotkey("ctrl", "v")
                    return
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            try:
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", "--delay",
                     str(text_input.interval_ms), "--", text],
                    check=True, timeout=30,
                )
                return
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                raise RuntimeError(
                    "Cannot type text on Linux: install xclip or xdotool"
                ) from exc
        if _PLATFORM == "Windows":
            from opendesk.computer.deps import ensure_import
            pyperclip = ensure_import("pyperclip")
            pyperclip.copy(text)
            time.sleep(0.05)
            _pyautogui().hotkey("ctrl", "v")
            return
        _pyautogui().typewrite(text, interval=text_input.interval_ms / 1000.0)

    def subscribe_input(self) -> AsyncIterator[InputEvent]:
        async def _gen() -> AsyncIterator[InputEvent]:
            raise CapabilityUnsupported(Capability.INPUT_POINTER, backend=self.BACKEND)
            yield  # pragma: no cover  (makes this an async generator)
        return _gen()

    # ------------------------------------------------------------------
    # Apps and windows
    # ------------------------------------------------------------------

    async def open_app(self, name: str) -> None:
        await asyncio.to_thread(self._open_app_sync, name)

    def _open_app_sync(self, name: str) -> None:
        if _PLATFORM == "Darwin":
            r = subprocess.run(["open", "-a", name], capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                r2 = subprocess.run(["open", name], capture_output=True, text=True, timeout=15)
                if r2.returncode != 0:
                    raise RuntimeError(r.stderr.strip() or r2.stderr.strip())
            time.sleep(1.5)
            try:
                _osascript(
                    f'tell application "{name}"\n'
                    f'  activate\n'
                    f'  try\n'
                    f'    if (count of documents) = 0 then make new document\n'
                    f'  end try\n'
                    f'end tell'
                )
            except Exception:
                pass
            time.sleep(0.5)
            return
        if _PLATFORM == "Linux":
            try:
                subprocess.Popen([name], start_new_session=True)
            except FileNotFoundError:
                subprocess.Popen(["xdg-open", name], start_new_session=True)
            time.sleep(2.0)
            return
        if _PLATFORM == "Windows":
            subprocess.Popen(["start", "", name], shell=True, start_new_session=True)
            time.sleep(2.0)
            return
        raise RuntimeError(f"Unsupported platform: {_PLATFORM}")

    async def close_app(self, name: str) -> None:
        await asyncio.to_thread(self._close_app_sync, name)

    def _close_app_sync(self, name: str) -> None:
        if _PLATFORM == "Darwin":
            _osascript(f'tell application "{name}" to quit')
            return
        if _PLATFORM == "Linux":
            r = subprocess.run(["pkill", "-f", name], capture_output=True, timeout=10)
            if r.returncode not in (0, 1):
                raise RuntimeError(r.stderr.decode(errors="replace").strip())
            return
        if _PLATFORM == "Windows":
            r = subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip())
            return
        raise RuntimeError(f"Unsupported platform: {_PLATFORM}")

    async def focus_app(self, name: str) -> None:
        await asyncio.to_thread(self._focus_app_sync, name)

    def _focus_app_sync(self, name: str) -> None:
        if _PLATFORM == "Darwin":
            _osascript(f'tell application "{name}" to activate')
            return
        if _PLATFORM == "Linux":
            r = subprocess.run(["wmctrl", "-a", name], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                r2 = subprocess.run(
                    ["xdotool", "search", "--name", name, "windowactivate"],
                    capture_output=True, text=True, timeout=10,
                )
                if r2.returncode != 0:
                    raise RuntimeError(
                        f"wmctrl: {r.stderr.strip()} | xdotool: {r2.stderr.strip()}"
                    )
            return
        if _PLATFORM == "Windows":
            try:
                import pygetwindow as gw  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("pygetwindow required: pip install pygetwindow") from exc
            wins = gw.getWindowsWithTitle(name)
            if not wins:
                raise RuntimeError(f"No window found with title '{name}'.")
            wins[0].activate()
            return
        raise RuntimeError(f"Unsupported platform: {_PLATFORM}")

    async def list_apps(self) -> list[str]:
        return await asyncio.to_thread(self._list_apps_sync)

    def _list_apps_sync(self) -> list[str]:
        if _PLATFORM == "Darwin":
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get the name of every process whose background only is false'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return []
            return [n.strip() for n in r.stdout.strip().split(",") if n.strip()]
        if _PLATFORM == "Linux":
            r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
            r2 = subprocess.run(["ps", "-eo", "comm="], capture_output=True, text=True, timeout=10)
            return sorted(set(r2.stdout.strip().splitlines()))[:50]
        if _PLATFORM == "Windows":
            try:
                import pygetwindow as gw  # type: ignore[import-not-found]
                return [w.title for w in gw.getAllWindows() if w.title.strip()]
            except ImportError:
                r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10)
                return r.stdout.strip().splitlines()[:30] if r.returncode == 0 else []
        return []

    async def windows(self) -> list[Window]:
        return await asyncio.to_thread(self._windows_sync)

    def _windows_sync(self) -> list[Window]:
        names = self._list_apps_sync()
        return [Window(id=str(i), title=n, app_name=n) for i, n in enumerate(names)]

    async def focused_window(self) -> Optional[Window]:
        return await asyncio.to_thread(self._focused_window_sync)

    def _focused_window_sync(self) -> Optional[Window]:
        """Frontmost app and, where cheaply available, its front window title."""
        if _PLATFORM == "Darwin":
            try:
                r = subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to get name of first process whose frontmost is true'],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    name = r.stdout.strip()
                    title = name
                    try:
                        r2 = subprocess.run(
                            ["osascript", "-e",
                             'tell application "System Events" to tell '
                             '(first process whose frontmost is true) to '
                             'get name of front window'],
                            capture_output=True, text=True, timeout=5,
                        )
                        if r2.returncode == 0 and r2.stdout.strip():
                            title = r2.stdout.strip()
                    except Exception:
                        pass
                    return Window(id=name, title=title, app_name=name, focused=True)
            except Exception:
                pass
            return None
        if _PLATFORM == "Linux":
            if shutil.which("xdotool"):
                try:
                    r = subprocess.run(
                        ["xdotool", "getactivewindow", "getwindowname"],
                        capture_output=True, text=True, timeout=5,
                    )
                    title = r.stdout.strip() if r.returncode == 0 else ""
                    r2 = subprocess.run(
                        ["xdotool", "getactivewindow", "getwindowclassname"],
                        capture_output=True, text=True, timeout=5,
                    )
                    app = r2.stdout.strip() if r2.returncode == 0 else ""
                    if title or app:
                        return Window(id=app or title, title=title or app, app_name=app or title, focused=True)
                except Exception:
                    pass
            return None
        if _PLATFORM == "Windows":
            try:
                import pygetwindow as gw  # type: ignore[import-not-found]
                w = gw.getActiveWindow()
                if w is not None and w.title:
                    return Window(id=w.title, title=w.title, app_name=w.title, focused=True)
            except Exception:
                pass
            return None
        return None

    async def focus_window(self, window_id: str) -> None:
        await self.focus_app(window_id)

    async def move_window(self, window_id: str, bounds: Rect) -> None:
        raise CapabilityUnsupported(Capability.WINDOWS_MANIPULATE, backend=self.BACKEND)

    async def close_window(self, window_id: str) -> None:
        await self.close_app(window_id)

    # ------------------------------------------------------------------
    # UI tree (accessibility)
    # ------------------------------------------------------------------

    async def ui_tree(
        self,
        *,
        window_id: Optional[str] = None,
        app: Optional[str] = None,
        max_depth: int = 8,
    ) -> UIElement:
        return await asyncio.to_thread(self._ui_tree_sync, window_id, app, max_depth)

    def _ui_tree_sync(
        self, window_id: Optional[str], app: Optional[str], max_depth: int
    ) -> UIElement:
        target = app or window_id
        if _PLATFORM == "Darwin":
            return self._macos_ui_tree(target, max_depth)
        if _PLATFORM == "Linux":
            return self._linux_ui_tree(target, max_depth)
        if _PLATFORM == "Windows":
            return self._windows_ui_tree(target, max_depth)
        return UIElement(role="root", name="(unsupported platform)")

    def _macos_ui_tree(self, app: Optional[str], max_depth: int) -> UIElement:
        if not app:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first process whose frontmost is true'],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return UIElement(role="root", name="(no frontmost app)")
            app = r.stdout.strip()
        script = f"""
tell application "System Events"
    tell process "{app}"
        if (count of windows) = 0 then return ""
        set win to window 1
        set winTitle to ""
        try
            set winTitle to title of win
        end try
        set output to winTitle & "\\n"
        set allElems to entire contents of win
        set elemCount to 0
        repeat with e in allElems
            if elemCount > 200 then exit repeat
            try
                set r to role of e
                set t to ""
                try
                    set t to title of e
                end try
                if t is "" then
                    try
                        set t to description of e
                    end try
                end if
                if t is "" then
                    try
                        set t to value of e as text
                    on error
                        set t to ""
                    end try
                end if
                if t is not missing value then
                    set pos to position of e
                    set sz to size of e
                    set output to output & r & "|" & t & "|" & ¬
                        (item 1 of pos as integer) & "|" & ¬
                        (item 2 of pos as integer) & "|" & ¬
                        (item 1 of sz as integer) & "|" & ¬
                        (item 2 of sz as integer) & "\\n"
                    set elemCount to elemCount + 1
                end if
            end try
        end repeat
        return output
    end tell
end tell
"""
        try:
            output = _osascript(script, timeout=20)
        except Exception as exc:
            return UIElement(role="root", name=app, value=str(exc))

        lines = output.splitlines()
        title = lines[0] if lines else ""
        children: list[UIElement] = []
        for line in lines[1:]:
            parts = line.strip().split("|", 5)
            if len(parts) < 6:
                continue
            try:
                role, name, x, y, w, h = parts
                children.append(
                    UIElement(
                        role=role,
                        name=name,
                        bounds=Rect(x=float(x), y=float(y), width=float(w), height=float(h)),
                        actions=["click"],
                    )
                )
            except ValueError:
                continue
        return UIElement(role="window", name=title, children=children, metadata={"app": app})

    def _linux_ui_tree(self, target: Optional[str], max_depth: int) -> UIElement:
        try:
            import pyatspi  # type: ignore[import-not-found]
        except ImportError:
            return UIElement(role="root", name="(pyatspi not installed)")

        desktop = pyatspi.Registry.getDesktop(0)
        target_app = None
        if target:
            tl = target.lower()
            for a in desktop:
                if a and a.name and tl in a.name.lower():
                    target_app = a
                    break
        if target_app is None:
            for a in desktop:
                try:
                    if a and pyatspi.STATE_ACTIVE in a.getState().getStates():
                        target_app = a
                        break
                except Exception:
                    pass
        if target_app is None:
            return UIElement(role="root", name="(no active app)")

        def walk(node: Any, depth: int) -> UIElement:
            if depth > max_depth:
                return UIElement(role="…", name="(truncated)")
            try:
                role = node.getLocalizedRoleName() or "?"
                name = (node.name or "").strip()[:120]
                bounds = None
                try:
                    bbox = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                    if bbox.width > 0 and bbox.height > 0:
                        bounds = Rect(x=bbox.x, y=bbox.y, width=bbox.width, height=bbox.height)
                except Exception:
                    pass
                children: list[UIElement] = []
                try:
                    for i in range(min(node.childCount, 80)):
                        try:
                            children.append(walk(node.getChildAtIndex(i), depth + 1))
                        except Exception:
                            pass
                except Exception:
                    pass
                actions: list[str] = []
                try:
                    act = node.queryAction()
                    actions = [act.getName(i) for i in range(act.nActions)]
                except Exception:
                    pass
                return UIElement(
                    role=role, name=name, bounds=bounds,
                    actions=actions, children=children,
                )
            except Exception as exc:
                return UIElement(role="error", name=str(exc))

        return walk(target_app, 0)

    def _windows_ui_tree(self, target: Optional[str], max_depth: int) -> UIElement:
        try:
            import pywinauto  # type: ignore[import-not-found]
        except ImportError:
            return UIElement(role="root", name="(pywinauto not installed)")
        try:
            if target:
                app = pywinauto.Application(backend="uia").connect(
                    title_re=f".*{target}.*", timeout=3,
                )
            else:
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                app = pywinauto.Application(backend="uia").connect(handle=hwnd)
            dlg = app.top_window()
        except Exception as exc:
            return UIElement(role="root", name=f"(connect failed: {exc})")

        children: list[UIElement] = []
        try:
            for ctrl in dlg.descendants():
                try:
                    rect = ctrl.rectangle()
                    children.append(
                        UIElement(
                            role=ctrl.friendly_class_name(),
                            name=(ctrl.window_text() or "").strip()[:120],
                            bounds=Rect(
                                x=rect.left, y=rect.top,
                                width=rect.right - rect.left,
                                height=rect.bottom - rect.top,
                            ),
                            actions=["click"],
                        )
                    )
                except Exception:
                    pass
        except Exception:
            pass
        return UIElement(
            role="window", name=dlg.window_text(),
            children=children, metadata={"app": target or ""},
        )

    async def perform_ui_action(
        self,
        element: UIElement,
        action: str = "click",
        *,
        app: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(self._perform_ui_action_sync, element, action, app)

    def _perform_ui_action_sync(
        self, element: UIElement, action: str, app: Optional[str],
    ) -> None:
        if _PLATFORM == "Darwin":
            self._macos_perform_ui_action(element, action, app)
            return
        if _PLATFORM == "Linux":
            self._linux_perform_ui_action(element, action, app)
            return
        if _PLATFORM == "Windows":
            self._windows_perform_ui_action(element, action, app)
            return
        raise CapabilityUnsupported(Capability.UI_ACTIONS, backend=self.BACKEND)

    # ------------------------------------------------------------------
    # macOS — AppleScript
    # ------------------------------------------------------------------

    def _macos_perform_ui_action(
        self, element: UIElement, action: str, app: Optional[str],
    ) -> None:
        target_app = app or element.metadata.get("app")
        if not target_app:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first process whose frontmost is true'],
                capture_output=True, text=True, timeout=5,
            )
            target_app = r.stdout.strip() if r.returncode == 0 else None
        if not target_app:
            raise RuntimeError("Cannot determine target app for UI action.")

        menu_path = element.metadata.get("menu_path")
        if menu_path:
            self._macos_invoke_menu_path(target_app, menu_path)
            return

        if not element.name and not element.role:
            raise ValueError("UIElement must have name or role to locate.")

        name_match = element.name.replace('"', '\\"')
        if element.bounds is not None:
            bx, by = int(element.bounds.x), int(element.bounds.y)
            bw, bh = int(element.bounds.width), int(element.bounds.height)
            bounds_clause = (
                f"and (item 1 of pos) is {bx} and (item 2 of pos) is {by} "
                f"and (item 1 of sz) is {bw} and (item 2 of sz) is {bh}"
            )
        else:
            bounds_clause = ""

        script = f"""
tell application "System Events"
    tell process "{target_app}"
        set allElems to entire contents of window 1
        repeat with elem in allElems
            try
                set elemName to ""
                try
                    set elemName to title of elem
                end try
                if elemName is "" then
                    try
                        set elemName to description of elem
                    end try
                end if
                if elemName is "{name_match}" then
                    set pos to position of elem
                    set sz to size of elem
                    if true {bounds_clause} then
                        click elem
                        return "ok"
                    end if
                end if
            end try
        end repeat
        error "no_match"
    end tell
end tell
"""
        try:
            _osascript(script, timeout=15)
        except Exception as exc:
            raise RuntimeError(
                f"AppleScript {action!r} on {element.role}:{element.name!r} "
                f"in {target_app} failed: {exc}"
            ) from exc

    def _macos_invoke_menu_path(self, app: str, menu_path: list[str]) -> None:
        if len(menu_path) < 2:
            raise ValueError("menu_path needs at least [menu, item].")
        safe = [m.replace('"', '\\"') for m in menu_path]
        top, intermediates, leaf = safe[0], safe[1:-1], safe[-1]
        chain = f'menu item "{leaf}"'
        for m in reversed(intermediates):
            chain += f' of menu "{m}" of menu item "{m}"'
        chain += f' of menu "{top}" of menu bar item "{top}" of menu bar 1'
        script = f"""
tell application "{app}" to activate
delay 0.1
tell application "System Events"
    tell process "{app}"
        click {chain}
    end tell
end tell
"""
        _osascript(script, timeout=15)

    # ------------------------------------------------------------------
    # Linux — pyatspi
    # ------------------------------------------------------------------

    def _linux_perform_ui_action(
        self, element: UIElement, action: str, app: Optional[str],
    ) -> None:
        try:
            import pyatspi  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CapabilityUnsupported(Capability.UI_ACTIONS, backend=self.BACKEND) from exc

        desktop = pyatspi.Registry.getDesktop(0)
        target_app = None
        if app:
            tl = app.lower()
            for a in desktop:
                if a and a.name and tl in a.name.lower():
                    target_app = a
                    break
        if target_app is None:
            for a in desktop:
                try:
                    if a and pyatspi.STATE_ACTIVE in a.getState().getStates():
                        target_app = a
                        break
                except Exception:
                    pass
        if target_app is None:
            raise RuntimeError(f"App {app!r} not found in AT-SPI tree.")

        match_name = (element.name or "").lower()
        match_role = (element.role or "").lower()

        def walk(node: Any, depth: int = 0):
            if depth > 12:
                return None
            try:
                role = (node.getLocalizedRoleName() or "").lower()
                name = (node.name or "").lower()
                if (not match_role or match_role in role) and (not match_name or match_name in name):
                    if element.bounds is not None:
                        try:
                            bbox = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                            if (abs(bbox.x - element.bounds.x) < 5 and
                                    abs(bbox.y - element.bounds.y) < 5):
                                return node
                        except Exception:
                            pass
                    else:
                        return node
                for i in range(node.childCount):
                    try:
                        found = walk(node.getChildAtIndex(i), depth + 1)
                        if found is not None:
                            return found
                    except Exception:
                        pass
            except Exception:
                pass
            return None

        target = walk(target_app)
        if target is None:
            raise RuntimeError(
                f"No matching element role={element.role!r} name={element.name!r} in '{app}'."
            )

        try:
            act = target.queryAction()
        except Exception as exc:
            raise CapabilityUnsupported(Capability.UI_ACTIONS, backend=self.BACKEND) from exc

        action_names = [act.getName(i).lower() for i in range(act.nActions)]
        preferred = [action.lower(), "click", "press", "activate", "toggle"]
        for pref in preferred:
            if pref in action_names:
                act.doAction(action_names.index(pref))
                return
        if act.nActions > 0:
            act.doAction(0)
            return
        raise RuntimeError(f"Element exposes no actions; tried {preferred}.")

    # ------------------------------------------------------------------
    # Windows — pywinauto UIA
    # ------------------------------------------------------------------

    def _windows_perform_ui_action(
        self, element: UIElement, action: str, app: Optional[str],
    ) -> None:
        try:
            import pywinauto  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CapabilityUnsupported(Capability.UI_ACTIONS, backend=self.BACKEND) from exc

        try:
            if app:
                pa = pywinauto.Application(backend="uia").connect(
                    title_re=f".*{app}.*", timeout=3,
                )
            else:
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                pa = pywinauto.Application(backend="uia").connect(handle=hwnd)
            dlg = pa.top_window()
        except Exception as exc:
            raise RuntimeError(f"Could not connect to {app!r}: {exc}") from exc

        menu_path = element.metadata.get("menu_path")
        if menu_path:
            try:
                dlg.menu_select("->".join(menu_path))
                return
            except Exception as exc:
                raise RuntimeError(f"menu_select({menu_path!r}) failed: {exc}") from exc

        kwargs: dict[str, Any] = {}
        if element.name:
            kwargs["title"] = element.name
        if element.role:
            kwargs["control_type"] = element.role
        if not kwargs:
            raise ValueError("UIElement must have name or role to locate.")

        try:
            ctrl = dlg.child_window(**kwargs)
            if action in ("click", "press", "activate") or action.lower() == "click":
                ctrl.click_input()
            elif action == "toggle":
                ctrl.toggle()
            else:
                ctrl.click_input()
        except Exception as exc:
            raise RuntimeError(
                f"Could not invoke {action!r} on {element.role}:{element.name!r} in {app!r}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    async def clipboard_read(self) -> ClipboardContents:
        return await asyncio.to_thread(self._clipboard_read_sync)

    def _clipboard_read_sync(self) -> ClipboardContents:
        text = self._clipboard_read_text()
        if not text:
            return ClipboardContents()
        return ClipboardContents(entries=[ClipboardEntry.from_text(text)])

    def _clipboard_read_text(self) -> str:
        if _PLATFORM == "Darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, timeout=5)
            if r.returncode != 0:
                raise RuntimeError(f"pbpaste failed: {r.stderr.decode(errors='replace')}")
            return r.stdout.decode("utf-8", errors="replace")
        if _PLATFORM == "Linux":
            for cmd in (
                ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"],
            ):
                r = subprocess.run(cmd, capture_output=True, timeout=5)
                if r.returncode == 0:
                    return r.stdout.decode("utf-8", errors="replace")
            try:
                import pyperclip  # type: ignore[import-not-found]
                return pyperclip.paste() or ""
            except ImportError as exc:
                raise RuntimeError("Clipboard read requires xclip, xsel, or pyperclip.") from exc
        if _PLATFORM == "Windows":
            from opendesk.computer.deps import ensure_import
            pyperclip = ensure_import("pyperclip")
            return pyperclip.paste() or ""
        raise RuntimeError(f"Clipboard not supported on platform: {_PLATFORM!r}")

    async def clipboard_write(self, contents: ClipboardContents) -> None:
        text = contents.text() or ""
        await asyncio.to_thread(self._clipboard_write_text, text)

    def _clipboard_write_text(self, text: str) -> None:
        if _PLATFORM == "Darwin":
            r = subprocess.run(["pbcopy"], input=text.encode("utf-8"), capture_output=True, timeout=5)
            if r.returncode != 0:
                raise RuntimeError(f"pbcopy failed: {r.stderr.decode(errors='replace')}")
            return
        if _PLATFORM == "Linux":
            for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                r = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=5)
                if r.returncode == 0:
                    return
            try:
                import pyperclip  # type: ignore[import-not-found]
                pyperclip.copy(text)
                return
            except ImportError as exc:
                raise RuntimeError("Clipboard write requires xclip, xsel, or pyperclip.") from exc
        if _PLATFORM == "Windows":
            from opendesk.computer.deps import ensure_import
            pyperclip = ensure_import("pyperclip")
            pyperclip.copy(text)
            return
        raise RuntimeError(f"Clipboard not supported on platform: {_PLATFORM!r}")

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------

    async def read_file(self, path: str) -> bytes:
        return await asyncio.to_thread(lambda: Path(path).expanduser().read_bytes())

    async def write_file(self, path: str, data: bytes) -> None:
        def _w() -> None:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        await asyncio.to_thread(_w)

    async def list_dir(self, path: str) -> list[FileEntry]:
        return await asyncio.to_thread(self._list_dir_sync, path)

    def _list_dir_sync(self, path: str) -> list[FileEntry]:
        p = Path(path).expanduser()
        entries: list[FileEntry] = []
        for child in p.iterdir():
            try:
                st = child.stat()
                entries.append(FileEntry(
                    path=str(child), name=child.name, is_dir=child.is_dir(),
                    size=st.st_size, mtime=st.st_mtime, mode=st.st_mode,
                ))
            except OSError:
                continue
        return entries

    async def stat(self, path: str) -> FileEntry:
        return await asyncio.to_thread(self._stat_sync, path)

    def _stat_sync(self, path: str) -> FileEntry:
        p = Path(path).expanduser()
        st = p.stat()
        return FileEntry(
            path=str(p), name=p.name, is_dir=p.is_dir(),
            size=st.st_size, mtime=st.st_mtime, mode=st.st_mode,
        )

    async def delete(self, path: str) -> None:
        def _d() -> None:
            p = Path(path).expanduser()
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
        await asyncio.to_thread(_d)

    async def move(self, src: str, dst: str) -> None:
        await asyncio.to_thread(
            lambda: shutil.move(str(Path(src).expanduser()), str(Path(dst).expanduser()))
        )

    async def mkdir(self, path: str, *, parents: bool = True) -> None:
        await asyncio.to_thread(
            lambda: Path(path).expanduser().mkdir(parents=parents, exist_ok=True)
        )

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------

    async def processes(self) -> list[Process]:
        return await asyncio.to_thread(self._processes_sync)

    def _processes_sync(self) -> list[Process]:
        if _PLATFORM == "Windows":
            r = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
            )
            out: list[Process] = []
            for line in r.stdout.strip().splitlines():
                parts = [p.strip('"') for p in line.split(",")]
                if len(parts) >= 2:
                    try:
                        out.append(Process(pid=int(parts[1]), name=parts[0]))
                    except ValueError:
                        continue
            return out
        r = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,comm="], capture_output=True, text=True, timeout=10,
        )
        out = []
        for line in r.stdout.strip().splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) >= 3:
                try:
                    out.append(Process(pid=int(parts[0]), ppid=int(parts[1]), name=parts[2]))
                except ValueError:
                    continue
        return out

    async def shell(
        self,
        command: str,
        *,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> CompletedCommand:
        start = time.time()
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=cwd, env={**os.environ, **(env or {})} if env else None,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise
        return CompletedCommand(
            returncode=proc.returncode or 0, stdout=stdout, stderr=stderr,
            duration=time.time() - start,
        )

    async def exec(
        self,
        argv: list[str],
        *,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        stdin: Optional[bytes] = None,
    ) -> CompletedCommand:
        start = time.time()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=cwd, env={**os.environ, **(env or {})} if env else None,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise
        return CompletedCommand(
            returncode=proc.returncode or 0, stdout=stdout, stderr=stderr,
            duration=time.time() - start,
        )

    # ------------------------------------------------------------------
    # Power
    # ------------------------------------------------------------------

    async def lock_screen(self) -> None:
        await asyncio.to_thread(self._lock_screen_sync)

    def _lock_screen_sync(self) -> None:
        if _PLATFORM == "Darwin":
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "q" using {control down, command down}'],
                check=True, timeout=5,
            )
            return
        if _PLATFORM == "Linux":
            for cmd in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"]):
                if subprocess.run(cmd, capture_output=True).returncode == 0:
                    return
            raise RuntimeError("Could not lock screen on Linux.")
        if _PLATFORM == "Windows":
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=True, timeout=5)
            return
        raise CapabilityUnsupported(Capability.POWER_LOCK, backend=self.BACKEND)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def notifications(self) -> list[Notification]:
        return []
