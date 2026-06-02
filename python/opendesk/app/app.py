"""FastAPI backend for the ``opendesk app`` local UI.

Owns:

* The running :class:`OpendeskServer` (hosting mode).
* A cache of outbound :class:`RemoteComputer` connections (controlling mode).
* The currently-active pairing code (if any) so the UI can display it.

Endpoints are mostly thin facades over the same primitives the CLI uses —
the UI is just another consumer.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import socket
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, Response
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "fastapi is required for the opendesk app. "
        "Install with: pip install 'opendesk[app]'"
    ) from _exc

from opendesk.computer import LocalComputer, Pixmap, Point, PointerAction, PointerButton, PointerEvent, Rect, RemoteComputer, SessionEvicted, TextInput
from opendesk.computer.types import KeyAction, KeyEvent, Modifier
from opendesk.protocol.auth import Identity, TrustedPeers
from opendesk.protocol.auth.identity import generate_pairing_code
from opendesk.remote.audit import AuditLog
from opendesk.remote.client import connect as client_connect
from opendesk.remote.server import (
    OpendeskServer,
    clear_description,
    read_description,
    write_description,
)
from opendesk import wsl as _wsl


log = logging.getLogger("opendesk.app")

_STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_APP_PORT = 8424


# ---------------------------------------------------------------------------
# AppState — shared mutable state for the FastAPI app
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    """Per-process state for the app.

    Owned by :func:`create_app`; passed to every endpoint via the request
    state object so tests can substitute a fake.
    """

    home: Optional[Path] = None
    server: Optional[OpendeskServer] = None
    outbound: dict[str, RemoteComputer] = field(default_factory=dict)
    pairing_task: Optional[asyncio.Task] = None
    pairing_code: Optional[str] = None
    pairing_result: Optional[dict[str, Any]] = None  # populated when pairing settles

    def trusted(self) -> TrustedPeers:
        return TrustedPeers(self.home)

    def identity(self) -> Identity:
        return Identity.load_or_create(self.home)

    def audit(self) -> AuditLog:
        return AuditLog(self.home)


# ---------------------------------------------------------------------------
# Host environment — surfaces WSL situation + LAN IPs to the UI
# ---------------------------------------------------------------------------


def _host_environment(state: "AppState") -> dict[str, Any]:
    """Network reachability info the UI needs to render WSL banners + IPs.

    ``reachable_ipv4s`` is the list the UI shows controllers: on WSL it's
    the Windows host's LAN adapters (what other devices on the network
    can actually reach); on a regular machine it's this host's own LAN
    interfaces.

    The mirrored-mode block has *two* booleans for a reason — a user can
    have ``.wslconfig`` set up correctly (``mirrored_configured = True``)
    but still be running on the old NAT'd kernel because they haven't
    done ``wsl --shutdown`` yet.  The UI shows a different prompt for
    each state.
    """
    in_wsl = _wsl.is_wsl()
    if in_wsl:
        reachable = _wsl.windows_lan_ipv4s()
        mirrored_active = _wsl.is_mirrored_mode()
        mirrored_configured = _wsl.wslconfig_has_mirrored()
        cfg_path = _wsl.wslconfig_path()
    else:
        reachable = _wsl.local_ipv4s()
        mirrored_active = False
        mirrored_configured = False
        cfg_path = None
    return {
        "wsl": in_wsl,
        "wsl_ip": _wsl.wsl_interface_ipv4() if in_wsl else "",
        "reachable_ipv4s": reachable,
        "server_port": state.server.port if state.server is not None else None,
        "mirrored_active": mirrored_active,
        "mirrored_configured": mirrored_configured,
        "wslconfig_path": str(cfg_path) if cfg_path else "",
    }


# ---------------------------------------------------------------------------
# FastAPI factory
# ---------------------------------------------------------------------------


_APP_TOKEN_FILE = "app-token"


def create_app(state: AppState) -> FastAPI:
    """Build a FastAPI app bound to *state*.

    Pulled out as a factory so tests can build their own state (no real
    OpendeskServer needed) and exercise endpoints via ``TestClient``.
    """
    import hmac
    import secrets

    app = FastAPI(title="opendesk", docs_url=None, redoc_url=None)
    app.state.opendesk = state  # type: ignore[attr-defined]

    # Generate a per-process secret token and write it to ~/.opendesk/app-token
    # so CLI tools (opendesk sessions, opendesk disconnect) can authenticate.
    _app_token = secrets.token_urlsafe(32)
    _token_home = Path(state.home) if state.home else Path.home() / ".opendesk"
    _token_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    _tok_path = _token_home / _APP_TOKEN_FILE
    _tok_path.write_text(_app_token)
    import os as _os
    with contextlib.suppress(OSError):
        _os.chmod(_tok_path, 0o600)

    # Block state-mutating requests that don't carry the session token.
    # GET requests are read-only and serve the HTML that bootstraps the token.
    class _TokenMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                provided = request.headers.get("X-Opendesk-Token", "")
                if not hmac.compare_digest(provided, _app_token):
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        {"detail": "missing or invalid X-Opendesk-Token header"},
                        status_code=403,
                    )
            return await call_next(request)

    app.add_middleware(_TokenMiddleware)
    # Block all cross-origin requests so browser-based CSRF is impossible.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
    )

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        index_path = _STATIC_DIR / "index.html"
        if not index_path.exists():
            return Response(
                content=(
                    "<h1>opendesk</h1><p>UI not installed.  "
                    "Reinstall with <code>pip install 'opendesk[app]'</code>.</p>"
                ),
                media_type="text/html",
            )
        html = index_path.read_text(encoding="utf-8")
        # Inject the session token so the JS can include it in every POST.
        injection = f"<script>window.__OPENDESK_TOKEN__={_app_token!r};</script>"
        html = html.replace("</head>", f"{injection}\n</head>", 1)
        return HTMLResponse(content=html)

    # ------------------------------------------------------------------
    # State endpoint
    # ------------------------------------------------------------------

    @app.get("/api/state")
    async def get_state(req: Request) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        identity = s.identity()
        trusted = s.trusted()
        peers = []
        for p in trusted.list():
            peers.append({
                "name": p.name,
                "fingerprint": p.fingerprint,
                "paired_at": p.paired_at,
                "description": p.effective_description,
                "description_override": p.description_override,
                "description_broadcast": p.description,
                "is_default": p.name == trusted.get_default(),
                "outbound_active": p.name in s.outbound,
            })

        active_session = None
        if s.server is not None:
            sessions = await s.server.sessions.list()
            if sessions:
                sess = sessions[0]
                active_session = {
                    "id": sess.id,
                    "peer_name": sess.peer_name,
                    "peer_fingerprint": ":".join(
                        sess.peer_public.hex()[i:i+4] for i in range(0, 16, 4)
                    ),
                    "remote_addr": sess.remote_addr,
                    "started_at": sess.started_at,
                    "age_seconds": sess.age_seconds(),
                    "mode": sess.mode.value,
                }

        # If pairing finished, surface the result.
        pairing_result = s.pairing_result
        if pairing_result is not None:
            # One-shot: drain after read.
            s.pairing_result = None

        return {
            "identity": {
                "fingerprint": ":".join(
                    identity.public_bytes.hex()[i:i+4] for i in range(0, 16, 4)
                ),
                "description": read_description(s.home),
            },
            "trusted_peers": peers,
            "active_session": active_session,
            "pairing_active": s.pairing_task is not None and not s.pairing_task.done(),
            "pairing_code": s.pairing_code,
            "pairing_result": pairing_result,
            "default_peer": trusted.get_default(),
            "host_environment": _host_environment(s),
        }

    # ------------------------------------------------------------------
    # Hosting — accept new pairings
    # ------------------------------------------------------------------

    @app.post("/api/pair/begin")
    async def pair_begin(req: Request) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        if s.server is None:
            raise HTTPException(503, "server is not running")
        if s.pairing_task is not None and not s.pairing_task.done():
            return {"code": s.pairing_code, "already_active": True}
        code = generate_pairing_code()
        s.pairing_code = code
        s.pairing_result = None

        async def _run_pairing() -> None:
            try:
                new_pub = await s.server.enable_pairing(code, timeout=300.0)
                if new_pub is None:
                    s.pairing_result = {"ok": False, "reason": "timeout"}
                else:
                    entry = s.trusted().find(new_pub)
                    s.pairing_result = {
                        "ok": True,
                        "peer_name": entry.name if entry else "",
                        "fingerprint": entry.fingerprint if entry else "",
                    }
            except Exception as exc:  # pragma: no cover
                s.pairing_result = {"ok": False, "reason": str(exc)}
            finally:
                s.pairing_code = None

        s.pairing_task = asyncio.create_task(_run_pairing())
        return {"code": code, "already_active": False}

    @app.post("/api/pair/cancel")
    async def pair_cancel(req: Request) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        if s.pairing_task is None or s.pairing_task.done():
            return {"cancelled": False}
        s.pairing_task.cancel()
        with contextlib.suppress(BaseException):
            await s.pairing_task
        s.pairing_task = None
        s.pairing_code = None
        s.pairing_result = None
        return {"cancelled": True}

    # ------------------------------------------------------------------
    # Hosting — eject / unpair
    # ------------------------------------------------------------------

    @app.post("/api/disconnect")
    async def do_disconnect(req: Request) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        if s.server is None:
            raise HTTPException(503, "server is not running")
        killed = await s.server.sessions.kill_all()
        return {"killed": killed}

    @app.post("/api/unpair")
    async def do_unpair(req: Request, body: dict = None) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        name = (body or {}).get("name")
        if not name:
            raise HTTPException(400, "missing 'name'")
        peer = s.trusted().find_by_name(name)
        if peer is None:
            raise HTTPException(404, f"unknown peer: {name}")

        # Disconnect first if active on the server side.
        if s.server is not None:
            for sess in await s.server.sessions.list():
                if sess.peer_public == peer.public_bytes:
                    await s.server.sessions.kill(sess.id)
                    break
        # Also close any outbound connection to this peer.
        if name in s.outbound:
            with contextlib.suppress(Exception):
                await s.outbound[name].aclose()
            del s.outbound[name]
        ok = s.trusted().remove(name)
        return {"ok": ok}

    @app.post("/api/unpair-all")
    async def do_unpair_all(req: Request) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        count = 0
        for p in s.trusted().list():
            peer = s.trusted().find_by_name(p.name)
            if peer is None:
                continue
            if s.server is not None:
                for sess in await s.server.sessions.list():
                    if sess.peer_public == peer.public_bytes:
                        await s.server.sessions.kill(sess.id)
                        break
            if p.name in s.outbound:
                with contextlib.suppress(Exception):
                    await s.outbound[p.name].aclose()
                del s.outbound[p.name]
            if s.trusted().remove(p.name):
                count += 1
        return {"unpaired": count}

    # ------------------------------------------------------------------
    # Peer-default + description
    # ------------------------------------------------------------------

    @app.post("/api/peers/default")
    async def set_default_peer(req: Request, body: dict = None) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        body = body or {}
        if body.get("clear"):
            cleared = s.trusted().clear_default()
            return {"cleared": cleared, "default": None}
        name = body.get("name")
        if not name:
            raise HTTPException(400, "missing 'name'")
        if not s.trusted().set_default(name):
            raise HTTPException(404, f"unknown peer: {name}")
        return {"default": name}

    @app.post("/api/peers/{name}/description")
    async def set_peer_description(req: Request, name: str, body: dict = None) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        body = body or {}
        if body.get("clear"):
            ok = s.trusted().clear_description_override(name)
            return {"ok": ok}
        text = body.get("text", "")
        if not s.trusted().set_description_override(name, text):
            raise HTTPException(404, f"unknown peer: {name}")
        return {"ok": True}

    @app.post("/api/describe")
    async def set_self_description(req: Request, body: dict = None) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        body = body or {}
        if body.get("clear"):
            clear_description(s.home)
            return {"cleared": True}
        write_description(s.home, body.get("text", ""))
        return {"ok": True}

    # ------------------------------------------------------------------
    # Controlling — discover / pair-with / connect / actions / screenshot
    # ------------------------------------------------------------------

    @app.get("/api/discover")
    async def do_discover(req: Request, timeout: float = 2.0) -> dict[str, Any]:
        try:
            from opendesk.remote.discovery import discover
        except ImportError as exc:
            raise HTTPException(503, f"discovery unavailable: {exc}")
        s: AppState = req.app.state.opendesk
        peers = await discover(timeout=timeout)
        own_key = s.identity().public_bytes
        return {
            "peers": [
                {
                    "name": p.name, "host": p.host, "port": p.port,
                    "fingerprint": p.fingerprint,
                    "description": p.description,
                    "public_key_hex": p.public_key.hex(),
                }
                for p in peers
                if p.public_key != own_key
            ],
        }

    @app.post("/api/pair-with")
    async def do_pair_with(req: Request, body: dict = None) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        body = body or {}
        host = body.get("host")
        code = body.get("code")
        name = body.get("name") or ""
        description = body.get("description") or ""
        port = int(body.get("port") or 8423)
        if not host or not code:
            raise HTTPException(400, "missing 'host' or 'code'")
        try:
            from opendesk.remote.client import pair_with
            remote, server_pub = await pair_with(
                host=host, port=port, code=code, home=s.home, name=name,
            )
        except Exception as exc:
            raise HTTPException(400, str(exc))
        trusted = s.trusted()
        peer_entry = trusted.find(server_pub)
        peer_name = peer_entry.name if peer_entry else (name or f"peer-{server_pub.hex()[:6]}")
        if description:
            trusted.set_description_override(peer_name, description)
        # Hand the live RemoteComputer off into the outbound cache so the
        # user doesn't need to reconnect to start driving.
        s.outbound[peer_name] = remote
        return {
            "ok": True,
            "peer_name": peer_name,
            "fingerprint": peer_entry.fingerprint if peer_entry else "",
        }

    @app.post("/api/connect")
    async def do_connect(req: Request, body: dict = None) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        name = (body or {}).get("peer")
        if not name:
            raise HTTPException(400, "missing 'peer'")
        if name in s.outbound:
            return {"ok": True, "reused": True}
        try:
            remote = await client_connect(name, home=s.home)
        except Exception as exc:
            raise HTTPException(400, f"connect failed: {exc}")
        s.outbound[name] = remote
        return {"ok": True, "reused": False}

    @app.delete("/api/peer/{name}")
    async def close_outbound(req: Request, name: str) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        remote = s.outbound.pop(name, None)
        if remote is None:
            return {"closed": False}
        with contextlib.suppress(Exception):
            await remote.aclose()
        return {"closed": True}

    @app.get("/api/peer/{name}/screenshot")
    async def peer_screenshot(req: Request, name: str) -> Response:
        s: AppState = req.app.state.opendesk
        remote = s.outbound.get(name)
        if remote is None:
            raise HTTPException(404, f"not connected: {name}")
        try:
            pix: Pixmap = await remote.capture()
        except SessionEvicted as exc:
            s.outbound.pop(name, None)
            raise HTTPException(410, f"session evicted: {exc.reason}")
        except Exception as exc:
            raise HTTPException(502, f"capture failed: {exc}")
        return Response(content=pix.data, media_type=f"image/{pix.format.value}", headers={
            "X-Logical-Width": str(pix.logical_width),
            "X-Logical-Height": str(pix.logical_height),
            "X-Pixel-Width": str(pix.width),
            "X-Pixel-Height": str(pix.height),
        })

    @app.post("/api/peer/{name}/action")
    async def peer_action(req: Request, name: str, body: dict = None) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        remote = s.outbound.get(name)
        if remote is None:
            raise HTTPException(404, f"not connected: {name}")
        body = body or {}
        kind = body.get("kind")
        try:
            if kind == "click":
                await remote.click(
                    Point(x=float(body["x"]), y=float(body["y"])),
                    button=PointerButton(body.get("button", "left")),
                    count=int(body.get("count", 1)),
                )
            elif kind == "move":
                await remote.pointer(PointerEvent(
                    action=PointerAction.MOVE,
                    point=Point(x=float(body["x"]), y=float(body["y"])),
                ))
            elif kind == "scroll":
                await remote.scroll(
                    Point(x=float(body.get("x", 0)), y=float(body.get("y", 0))),
                    dx=float(body.get("dx", 0)),
                    dy=float(body.get("dy", 0)),
                )
            elif kind == "type":
                await remote.type_text(body.get("text", ""))
            elif kind == "key":
                await remote.press(body.get("keysym", ""))
            else:
                raise HTTPException(400, f"unknown action kind: {kind!r}")
        except SessionEvicted as exc:
            s.outbound.pop(name, None)
            raise HTTPException(410, f"session evicted: {exc.reason}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"action failed: {exc}")
        return {"ok": True}

    # ------------------------------------------------------------------
    # WSL — one-click Windows-side port forwarding setup
    # ------------------------------------------------------------------

    @app.post("/api/wsl/setup")
    async def wsl_setup(req: Request) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        if not _wsl.is_wsl():
            raise HTTPException(400, "not running inside WSL")
        if s.server is None:
            raise HTTPException(503, "server is not running")
        wsl_ip = _wsl.wsl_interface_ipv4()
        if not wsl_ip:
            raise HTTPException(500, "could not determine WSL interface IP")
        port = s.server.port
        cmds = _wsl.render_setup_commands(port=port, wsl_ip=wsl_ip)
        # run_via_uac() is sync; offload so we don't stall the event loop
        # while the user is staring at the UAC prompt.
        try:
            rc = await asyncio.to_thread(_wsl.run_via_uac, cmds)
        except Exception as exc:
            raise HTTPException(500, f"could not invoke powershell: {exc}")
        return {
            "ok": rc == 0,
            "returncode": rc,
            "port": port,
            "wsl_ip": wsl_ip,
            "reachable_ipv4s": _wsl.windows_lan_ipv4s(),
        }

    @app.post("/api/wsl/enable-mirrored")
    async def wsl_enable_mirrored(req: Request) -> dict[str, Any]:
        """Write ``[wsl2] networkingMode=mirrored`` to ``~/.wslconfig``.

        Mirrored mode is the actual fix for WSL2 LAN reachability — once
        active, WSL binds to the Windows adapters directly so mDNS works
        in both directions and no port-forwarding is needed.  Activating
        it requires ``wsl --shutdown`` on the Windows side; we can't do
        that ourselves (it would kill this very process), so the response
        includes the command to run.
        """
        if not _wsl.is_wsl():
            raise HTTPException(400, "not running inside WSL")
        path = _wsl.wslconfig_path()
        if path is None:
            raise HTTPException(
                500,
                "could not locate the Windows user profile via interop",
            )
        try:
            existing = path.read_text() if path.exists() else ""
        except OSError as exc:
            raise HTTPException(500, f"could not read {path}: {exc}")
        new_text = _wsl.render_wslconfig_with_mirrored(existing)
        already = new_text == existing or (
            existing and new_text == existing
        )
        wrote = False
        if new_text != existing:
            try:
                path.write_text(new_text)
                wrote = True
            except OSError as exc:
                raise HTTPException(500, f"could not write {path}: {exc}")
        return {
            "ok": True,
            "wrote": wrote,
            "already_configured": _wsl.wslconfig_has_mirrored(),
            "mirrored_active": _wsl.is_mirrored_mode(),
            "path": str(path),
            "next_step": (
                None if _wsl.is_mirrored_mode()
                else "Run 'wsl --shutdown' in PowerShell, then reopen your WSL terminal."
            ),
        }

    @app.post("/api/wsl/undo")
    async def wsl_undo(req: Request) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        if not _wsl.is_wsl():
            raise HTTPException(400, "not running inside WSL")
        if s.server is None:
            raise HTTPException(503, "server is not running")
        port = s.server.port
        cmds = _wsl.render_undo_commands(port=port)
        try:
            rc = await asyncio.to_thread(_wsl.run_via_uac, cmds)
        except Exception as exc:
            raise HTTPException(500, f"could not invoke powershell: {exc}")
        return {"ok": rc == 0, "returncode": rc, "port": port}

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    @app.get("/api/audit")
    async def get_audit(
        req: Request,
        date: Optional[str] = Query(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
        peer: Optional[str] = None,
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        s: AppState = req.app.state.opendesk
        entries = s.audit().iter_entries(date=date)
        if peer:
            entries = [
                e for e in entries
                if peer in ((e.get("peer") or {}).get("name") or "")
                or peer in ((e.get("peer") or {}).get("fp") or "")
            ]
        return {"entries": entries[-limit:]}

    return app


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _port_in_use(host: str, port: int) -> bool:
    """Return True if *host:port* can't be bound — i.e. something already owns it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return True
        return False
    finally:
        s.close()


def run(
    *,
    home: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_APP_PORT,
    open_browser: bool = True,
) -> None:
    """Boot the FastAPI app + OpendeskServer behind it, then run uvicorn."""
    import uvicorn

    # Pre-flight: bail out with an actionable message rather than a 30-line
    # traceback if either port is already taken (typically another opendesk
    # instance — `opendesk app` and `opendesk serve` both bind 8423).
    for h, p, label in (("0.0.0.0", 8423, "WebSocket"), (host, port, "UI")):
        if _port_in_use(h, p):
            sys.stderr.write(
                f"opendesk: {label} port {p} on {h} is already in use.\n"
                f"  Another opendesk instance is probably running. "
                f"Stop it (e.g. `pkill -f 'opendesk app'` or close the other process) and try again.\n"
            )
            sys.exit(1)

    identity = Identity.load_or_create(home)
    trusted = TrustedPeers(home)
    server = OpendeskServer(
        LocalComputer(), identity, trusted,
        host="0.0.0.0", port=8423,
        advertise_mdns=True,
        home=home,
    )
    state = AppState(home=home, server=server)
    fast = create_app(state)

    @fast.on_event("startup")
    async def _startup() -> None:
        await server.start()
        if open_browser:
            url = f"http://{host}:{port}"
            try:
                webbrowser.open(url)
            except Exception:  # pragma: no cover
                pass

    @fast.on_event("shutdown")
    async def _shutdown() -> None:
        for r in list(state.outbound.values()):
            with contextlib.suppress(Exception):
                await r.aclose()
        state.outbound.clear()
        with contextlib.suppress(Exception):
            await server.aclose()

    uvicorn.run(fast, host=host, port=port, log_level="info")
