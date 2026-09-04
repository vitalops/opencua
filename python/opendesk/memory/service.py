"""Install / uninstall the screen-memory daemon as a user-scoped service.

Same shape as :mod:`opendesk.remote.service` but for ``opendesk memory
start``:

* Linux   — ``systemd --user`` unit ``~/.config/systemd/user/opendesk-memory.service``
* macOS   — launchd agent ``~/Library/LaunchAgents/com.opendesk.memory.plist``
* Windows — Task Scheduler task ``opendesk-memory`` (on logon)

Runs as the current user so the daemon inherits Screen Recording /
Accessibility permissions.  The ``_render_*`` functions are pure and
unit-tested; ``install_memory_service`` does the file I/O.
"""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Optional

from opendesk.remote.service import ServiceInstallation, _run, _xml

SERVICE_NAME = "opendesk-memory"
LAUNCHD_LABEL = "com.opendesk.memory"


def install_memory_service(
    *,
    home: Optional[Path] = None,
    interval: Optional[float] = None,
    python: Optional[str] = None,
    autostart: bool = True,
) -> ServiceInstallation:
    py = python or sys.executable
    system = platform.system()
    if system == "Linux":
        return _install_systemd(home, interval, py, autostart)
    if system == "Darwin":
        return _install_launchd(home, interval, py, autostart)
    if system == "Windows":
        return _install_schtasks(home, interval, py, autostart)
    raise RuntimeError(f"Service install not supported on platform: {system!r}")


def uninstall_memory_service() -> bool:
    system = platform.system()
    if system == "Linux":
        return _uninstall_systemd()
    if system == "Darwin":
        return _uninstall_launchd()
    if system == "Windows":
        return _uninstall_schtasks()
    raise RuntimeError(f"Service uninstall not supported on platform: {system!r}")


def _args(python: str, interval: Optional[float], home: Optional[Path]) -> list[str]:
    args = [python, "-m", "opendesk.cli", "memory", "start"]
    if interval:
        args += ["--interval", f"{interval:g}"]
    if home is not None:
        args += ["--home", str(home)]
    return args


# -- systemd -----------------------------------------------------------------


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _render_systemd_unit(python: str, interval: Optional[float], home: Optional[Path]) -> str:
    cmd = " ".join(_args(python, interval, home))
    return f"""[Unit]
Description=opendesk screen memory — local searchable desktop history
After=graphical-session.target

[Service]
Type=simple
ExecStart={cmd}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def _install_systemd(home, interval, python, autostart) -> ServiceInstallation:
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_systemd_unit(python, interval, home))
    started = False
    if autostart and shutil.which("systemctl"):
        _run(["systemctl", "--user", "daemon-reload"])
        r = _run(["systemctl", "--user", "enable", "--now", SERVICE_NAME])
        started = r.returncode == 0
    return ServiceInstallation(path=path, started=started, manager="systemd")


def _uninstall_systemd() -> bool:
    path = _systemd_unit_path()
    if not path.exists():
        return False
    if shutil.which("systemctl"):
        _run(["systemctl", "--user", "disable", "--now", SERVICE_NAME])
    path.unlink()
    return True


# -- launchd -----------------------------------------------------------------


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _render_launchd_plist(python: str, interval: Optional[float], home: Optional[Path]) -> str:
    home_dir = Path(home) if home else Path.home() / ".opendesk"
    log_dir = home_dir / "memory"
    args_xml = "\n        ".join(f"<string>{_xml(a)}</string>" for a in _args(python, interval, home))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        {args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>{_xml(str(log_dir / 'daemon.log'))}</string>
    <key>StandardErrorPath</key>
    <string>{_xml(str(log_dir / 'daemon.err'))}</string>
</dict>
</plist>
"""


def _install_launchd(home, interval, python, autostart) -> ServiceInstallation:
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    (Path(home) if home else Path.home() / ".opendesk").joinpath("memory").mkdir(parents=True, exist_ok=True)
    path.write_text(_render_launchd_plist(python, interval, home))
    started = False
    if autostart and shutil.which("launchctl"):
        _run(["launchctl", "unload", str(path)])
        r = _run(["launchctl", "load", "-w", str(path)])
        started = r.returncode == 0
    return ServiceInstallation(path=path, started=started, manager="launchd")


def _uninstall_launchd() -> bool:
    path = _launchd_plist_path()
    if not path.exists():
        return False
    if shutil.which("launchctl"):
        _run(["launchctl", "unload", "-w", str(path)])
    path.unlink()
    return True


# -- Task Scheduler -----------------------------------------------------------


def _render_schtasks_command(python: str, interval: Optional[float], home: Optional[Path]) -> str:
    parts = [f'"{python}"', "-m", "opendesk.cli", "memory", "start"]
    if interval:
        parts += ["--interval", f"{interval:g}"]
    if home is not None:
        parts += ["--home", f'"{home}"']
    return " ".join(parts)


def _install_schtasks(home, interval, python, autostart) -> ServiceInstallation:
    if not shutil.which("schtasks"):
        raise RuntimeError("schtasks.exe not found — Task Scheduler unavailable")
    r = _run([
        "schtasks", "/create", "/tn", SERVICE_NAME,
        "/tr", _render_schtasks_command(python, interval, home),
        "/sc", "onlogon", "/rl", "limited", "/f",
    ])
    if r.returncode != 0:
        raise RuntimeError(f"schtasks /create failed: {r.stderr.strip() or r.stdout.strip()}")
    started = False
    if autostart:
        _run(["schtasks", "/run", "/tn", SERVICE_NAME])
        started = True
    return ServiceInstallation(
        path=Path(f"TaskScheduler:{SERVICE_NAME}"), started=started, manager="schtasks",
    )


def _uninstall_schtasks() -> bool:
    if not shutil.which("schtasks"):
        return False
    r = _run(["schtasks", "/delete", "/tn", SERVICE_NAME, "/f"])
    return r.returncode == 0
