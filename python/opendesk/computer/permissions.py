"""Platform permission self-check.

On macOS, controlling mouse/keyboard requires the Accessibility entitlement
and capturing the screen requires the Screen Recording entitlement.  Both
are granted to the specific binary that *invokes* the API — usually the
terminal (when developing) or the Python binary running ``opendesk serve``.

When the entitlements are missing the failures are cryptic (silent
no-input, all-black screenshots, AppleScript ``-1002`` errors).  This
module probes for them up front so the operator sees an actionable message
and a one-click jump to the right System Settings pane.

Linux and Windows have no equivalent permission gate; the checks return
"not required" so the same code path works cross-platform.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


_IS_MACOS = platform.system() == "Darwin"


@dataclass
class PermissionStatus:
    """Result of one platform permission probe."""

    name: str
    granted: bool
    reason: str = ""
    settings_url: str = ""
    install_hint: str = ""

    def __bool__(self) -> bool:
        return self.granted


def check_all() -> list[PermissionStatus]:
    """Run every permission probe relevant to the current platform."""
    if not _IS_MACOS:
        return []
    return [_check_accessibility(), _check_screen_recording()]


# ---------------------------------------------------------------------------
# macOS probes
# ---------------------------------------------------------------------------


def _check_accessibility() -> PermissionStatus:
    """Probe whether AppleScript / System Events is allowed.

    AppleScript without Accessibility returns OSStatus -1002 ("not
    authorised").  We send a minimal innocuous query and inspect the
    response.
    """
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process'],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return PermissionStatus(
            name="Accessibility", granted=False,
            reason=f"osascript probe failed: {exc}",
            settings_url=_PRIVACY_ACCESSIBILITY,
            install_hint=_ACCESSIBILITY_HINT,
        )
    combined = (r.stdout + r.stderr).lower()
    if r.returncode == 0 and "not allowed" not in combined and "-1002" not in combined:
        return PermissionStatus(name="Accessibility", granted=True)
    return PermissionStatus(
        name="Accessibility", granted=False,
        reason="System Events refused the probe (missing Accessibility access).",
        settings_url=_PRIVACY_ACCESSIBILITY,
        install_hint=_ACCESSIBILITY_HINT,
    )


def _check_screen_recording() -> PermissionStatus:
    """Probe whether mss can capture a pixel.

    Without Screen Recording, ``mss`` either raises or returns blackmasked
    data.  We just check that the call completes — the masked-pixel edge
    case is too fragile to detect reliably; operators with rendering issues
    should run ``opendesk check`` and follow the install hint.
    """
    try:
        import mss  # type: ignore[import-not-found]
    except ImportError as exc:
        return PermissionStatus(
            name="Screen Recording", granted=False,
            reason=f"mss not installed: {exc}",
            install_hint="pip install 'opendesk[core]'",
        )
    try:
        # Goes through capture_screen so the macOS `screencapture` fast path
        # is exercised (mss alone can stall ~30 s on macOS 15+/26).
        from opendesk.computer.capture import capture_screen
        capture_screen(region=(0, 0, 2, 2))
    except Exception as exc:
        return PermissionStatus(
            name="Screen Recording", granted=False,
            reason=f"mss capture failed: {exc}",
            settings_url=_PRIVACY_SCREEN_CAPTURE,
            install_hint=_SCREEN_RECORDING_HINT,
        )
    return PermissionStatus(name="Screen Recording", granted=True)


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------


def open_settings(url: str) -> None:
    """Open System Settings to the named privacy pane (macOS only)."""
    if not _IS_MACOS or not url:
        return
    subprocess.run(["open", url], check=False)


def report(statuses: list[PermissionStatus], *, file=None) -> bool:
    """Print a one-line summary per status and return ``True`` if all granted.

    A missing permission also prints its install hint.
    """
    stream = file or sys.stderr
    if not statuses:
        return True
    all_ok = True
    for s in statuses:
        mark = "✓" if s.granted else "✗"
        line = f"  {mark} {s.name}"
        if s.granted:
            print(line, file=stream)
            continue
        all_ok = False
        print(line, file=stream)
        if s.reason:
            print(f"      {s.reason}", file=stream)
        if s.install_hint:
            for ln in s.install_hint.splitlines():
                print(f"      {ln}", file=stream)
    return all_ok


# ---------------------------------------------------------------------------
# Text constants
# ---------------------------------------------------------------------------


_PRIVACY_ACCESSIBILITY = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)
_PRIVACY_SCREEN_CAPTURE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)

_ACCESSIBILITY_HINT = (
    "Grant Accessibility access to the terminal / Python binary running opendesk:\n"
    "  System Settings → Privacy & Security → Accessibility → toggle the entry on.\n"
    "If you don't see it listed, click the + and add it."
)

_SCREEN_RECORDING_HINT = (
    "Grant Screen Recording access to the terminal / Python binary running opendesk:\n"
    "  System Settings → Privacy & Security → Screen Recording → toggle the entry on.\n"
    "macOS may require relaunching the app after granting."
)
