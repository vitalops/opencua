"""``opendesk serve`` and ``opendesk pair`` — the controlled-machine daemon.

Composition
-----------

::

    incoming WebSocket
       │
       ▼
    auth_server / pair_server  ──►  Session(EncryptedConnection)
       │
       ▼
    Peer(role="server", dispatcher=ComputerDispatcher(local_computer))
       │
       ▼
    one session in SessionRegistry until the peer disconnects

Two operating modes
-------------------
* :attr:`ServerMode.SERVE` — accept any peer whose static key is in
  :class:`~opendesk.protocol.auth.TrustedPeers`.  Long-running, systemd-friendly.
* :attr:`ServerMode.PAIR` — accept exactly one new peer who proves the
  pairing code, store its static key, then stop accepting new pairings (still
  serves the existing peer for the lifetime of the connection).

The two modes can be combined on the same listening socket if the user runs
``opendesk pair`` and ``opendesk serve`` concurrently — they share the same
port and trusted-peers file via the filesystem.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from opendesk.computer import (
    Computer,
    ComputerDispatcher,
    LocalComputer,
)
from opendesk.protocol import Peer
from opendesk.protocol.auth import (
    AuthFailure,
    Identity,
    Session,
    TrustedPeers,
    auth_server,
    pair_server,
)
from opendesk.protocol.connection import Connection
from opendesk.protocol.transports.websocket import (
    WebSocketConnection,
    WebSocketServer,
    serve_websocket,
)
from opendesk.remote.audit import AuditLog
from opendesk.remote.policy import AllowAllPolicy, Policy


log = logging.getLogger("opendesk.remote.server")


DEFAULT_PORT = 8423
DESCRIPTION_FILE = "description.txt"


def read_description(home: Optional[Path]) -> str:
    """Read the controlled-machine description from ``<home>/description.txt``.

    Re-read on every handshake (so ``opendesk describe ...`` edits land in
    the next session's HELLO without a daemon restart).  Returns ``""`` if
    the file doesn't exist or is empty.
    """
    base = Path(home) if home else _default_home()
    path = base / DESCRIPTION_FILE
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_description(home: Optional[Path], text: str) -> Path:
    """Persist the description (``opendesk describe ...`` writes this)."""
    base = Path(home) if home else _default_home()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = base / DESCRIPTION_FILE
    path.write_text(text + "\n" if text else "", encoding="utf-8")
    return path


def clear_description(home: Optional[Path]) -> bool:
    base = Path(home) if home else _default_home()
    path = base / DESCRIPTION_FILE
    if not path.exists():
        return False
    path.unlink()
    return True


def _default_home() -> Path:
    from opendesk.protocol.auth.identity import DEFAULT_HOME
    return DEFAULT_HOME


class ServerMode(str, enum.Enum):
    """Acceptance mode for incoming connections."""

    SERVE = "serve"
    PAIR = "pair"


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------


@dataclass
class SessionInfo:
    """One active opendesk connection.  Held in the server's :class:`SessionRegistry`."""

    id: str
    peer_public: bytes
    peer_name: str
    remote_addr: str
    started_at: float = field(default_factory=time.time)
    mode: ServerMode = ServerMode.SERVE
    peer: Optional[Peer] = field(default=None, repr=False)

    def age_seconds(self) -> float:
        return time.time() - self.started_at


class SessionRegistry:
    """Thread-safe (asyncio-safe) registry of active sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = asyncio.Lock()

    async def add(self, info: SessionInfo) -> None:
        async with self._lock:
            self._sessions[info.id] = info

    async def remove(self, session_id: str) -> Optional[SessionInfo]:
        async with self._lock:
            return self._sessions.pop(session_id, None)

    async def list(self) -> list[SessionInfo]:
        async with self._lock:
            return list(self._sessions.values())

    async def kill(self, session_id: str, *, reason: str = "admin_disconnect") -> bool:
        """Remove and close one session.

        Sends a ``session.evicted`` PUSH frame just before closing so a
        cooperative client (the in-tree :class:`RemoteComputer`) suppresses
        its auto-reconnect.  A hostile client could ignore the hint — for
        enforced eviction, ``unpair`` the peer instead.

        Synchronous wrt the registry — after this returns, ``session_id``
        is gone, even if the underlying ``peer.aclose()`` is still finishing
        in the background.
        """
        async with self._lock:
            info = self._sessions.pop(session_id, None)
        if info is None or info.peer is None:
            return False
        with contextlib.suppress(Exception):
            await info.peer.push("session.evicted", {"reason": reason})
        with contextlib.suppress(Exception):
            await info.peer.aclose()
        return True

    async def kill_all(self, *, reason: str = "admin_disconnect") -> int:
        async with self._lock:
            peers = [s.peer for s in self._sessions.values() if s.peer is not None]
            self._sessions.clear()
        for p in peers:
            with contextlib.suppress(Exception):
                await p.push("session.evicted", {"reason": reason})
            with contextlib.suppress(Exception):
                await p.aclose()
        return len(peers)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


PairingHook = Callable[[bytes, str], Awaitable[None]]
"""Invoked after a successful pairing: ``async (peer_public, suggested_name) → None``.

The hook is responsible for persisting the new peer into :class:`TrustedPeers`
and any caller-side bookkeeping.  Defaults to writing to the server's own
trusted-peers store with a default name.
"""


class OpendeskServer:
    """A running opendesk server.

    Construct it with the local :class:`Computer` to expose, the local
    :class:`Identity`, and the :class:`TrustedPeers` store.  Use
    :meth:`start` to bring up the WebSocket listener (and optionally mDNS),
    then ``await server.serve_forever()``.

    Pair mode is a one-shot operation: call :meth:`enable_pairing` with the
    code, accept exactly one new peer, then revert to serve mode.
    """

    def __init__(
        self,
        computer: Computer,
        identity: Identity,
        trusted: TrustedPeers,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        advertise_mdns: bool = True,
        service_name: Optional[str] = None,
        home: Optional[Path] = None,
        policy: Optional[Policy] = None,
        audit: Optional[AuditLog] = None,
        enable_audit: bool = True,
    ) -> None:
        self._computer = computer
        self._identity = identity
        self._trusted = trusted
        self._host = host
        self._port = port
        self._advertise_mdns = advertise_mdns
        self._service_name = service_name
        self._home = home
        self._policy: Policy = policy or AllowAllPolicy()
        if audit is not None:
            self._audit: Optional[AuditLog] = audit
            self._owns_audit = False
        elif enable_audit:
            self._audit = AuditLog(home=home)
            self._owns_audit = True
        else:
            self._audit = None
            self._owns_audit = False

        self._sessions = SessionRegistry()
        self._ws_server: Optional[WebSocketServer] = None
        self._mdns_handle = None
        self._admin_server = None
        self._accept_lock = asyncio.Lock()

        # Pairing state.
        self._pairing_lock = asyncio.Lock()
        self._pairing_code: Optional[str] = None
        self._pairing_event = asyncio.Event()
        self._pairing_hook: Optional[PairingHook] = None
        self._pairing_in_progress = asyncio.Lock()  # one handshake at a time

    @property
    def port(self) -> int:
        return self._ws_server.port if self._ws_server else self._port

    @property
    def host(self) -> str:
        return self._host

    @property
    def public_key(self) -> bytes:
        return self._identity.public_bytes

    @property
    def sessions(self) -> SessionRegistry:
        return self._sessions

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind the WebSocket listener and start mDNS advertisement + admin IPC."""
        if self._ws_server is not None:
            return
        self._ws_server = await serve_websocket(
            self._handle_connection, host=self._host, port=self._port,
        )
        if self._advertise_mdns:
            try:
                from opendesk.remote.discovery import advertise
                self._mdns_handle = await advertise(
                    name=self._service_name or _default_service_name(self._identity),
                    port=self._ws_server.port,
                    public_key=self._identity.public_bytes,
                    description=read_description(self._home),
                )
            except ImportError:
                log.warning("zeroconf not installed; mDNS advertisement disabled")
            except Exception as exc:
                log.warning("mDNS advertisement failed: %s", exc)
        try:
            from opendesk.remote.admin import AdminServer
            self._admin_server = AdminServer(self, home=self._home)
            await self._admin_server.start()
        except Exception as exc:
            log.warning("admin IPC failed to start: %s", exc)
            self._admin_server = None

    async def aclose(self) -> None:
        """Stop accepting new connections, kill all sessions, release mDNS + admin + audit."""
        if self._admin_server is not None:
            with contextlib.suppress(Exception):
                await self._admin_server.aclose()
            self._admin_server = None
        if self._mdns_handle is not None:
            with contextlib.suppress(Exception):
                await self._mdns_handle.aclose()
            self._mdns_handle = None
        if self._ws_server is not None:
            await self._ws_server.aclose()
            self._ws_server = None
        await self._sessions.kill_all()
        if self._audit is not None and self._owns_audit:
            with contextlib.suppress(Exception):
                await self._audit.aclose()

    async def serve_forever(self) -> None:
        if self._ws_server is None:
            await self.start()
        await self._ws_server.wait_closed()  # type: ignore[union-attr]

    async def __aenter__(self) -> "OpendeskServer":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    async def enable_pairing(
        self,
        code: str,
        *,
        pairing_hook: Optional[PairingHook] = None,
        timeout: Optional[float] = None,
    ) -> Optional[bytes]:
        """Accept one pairing attempt and return the new peer's public key.

        Only one pairing can be in progress at a time.  When *timeout* is set
        and elapses with no successful pairing, returns ``None``.
        """
        if not self._pairing_lock.locked():
            await self._pairing_lock.acquire()
        try:
            self._pairing_code = code
            self._pairing_hook = pairing_hook
            self._pairing_event.clear()
            try:
                await asyncio.wait_for(self._pairing_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            # Hook (if any) has already run; the trusted-peers store reflects
            # the new peer.  Return the most-recently-added trusted public key.
            peers = self._trusted.list()
            return peers[-1].public_bytes if peers else None
        finally:
            self._pairing_code = None
            self._pairing_hook = None
            self._pairing_lock.release()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _handle_connection(self, raw: WebSocketConnection) -> None:
        addr = "?"
        try:
            ws = getattr(raw, "_ws", None)
            if ws is not None and getattr(ws, "remote_address", None):
                ra = ws.remote_address
                addr = f"{ra[0]}:{ra[1]}"
        except Exception:
            pass

        try:
            session, mode = await self._handshake(raw)
        except AuthFailure as exc:
            log.info("auth failed from %s: %s", addr, exc)
            if self._audit is not None:
                with contextlib.suppress(Exception):
                    await self._audit.record_session_rejected(
                        peer_public=b"", peer_name="",
                        remote_addr=addr, reason=f"auth_failed: {exc.reason}",
                    )
            return
        except Exception as exc:
            log.warning("handshake error from %s: %s", addr, exc)
            if self._audit is not None:
                with contextlib.suppress(Exception):
                    await self._audit.record_session_rejected(
                        peer_public=b"", peer_name="",
                        remote_addr=addr, reason=f"handshake_error: {exc}",
                    )
            return

        # Single-controller enforcement.  At most one session is active at
        # a time; a same-peer reconnect (common after a controller crash or
        # network roam) replaces the previous session, a different peer is
        # rejected outright.  The lock makes "kick + check + add" atomic
        # against concurrent incoming connections.
        info: Optional[SessionInfo] = None
        async with self._accept_lock:
            for existing in await self._sessions.list():
                if existing.peer_public == session.peer_public:
                    log.info(
                        "same peer (%s) reconnecting; bumping previous session %s",
                        existing.peer_name or "?", existing.id,
                    )
                    await self._sessions.kill(existing.id)
                    break

            active = await self._sessions.list()
            if active:
                names = ", ".join(s.peer_name or "?" for s in active)
                log.info(
                    "rejecting %s — another controller is active: %s",
                    addr, names,
                )
                if self._audit is not None:
                    with contextlib.suppress(Exception):
                        await self._audit.record_session_rejected(
                            peer_public=session.peer_public,
                            peer_name=self._trusted.find(session.peer_public).name
                                if self._trusted.find(session.peer_public) else "",
                            remote_addr=addr,
                            reason=f"busy: {names}",
                        )
                await _send_rejection(
                    session.connection,
                    code="busy",
                    message=(
                        f"server is busy: {names} is the active controller. "
                        "Disconnect them to take over."
                    ),
                )
                return

            peer_entry = self._trusted.find(session.peer_public)
            peer_name = peer_entry.name if peer_entry else ""
            info = SessionInfo(
                id=uuid.uuid4().hex[:8],
                peer_public=session.peer_public,
                peer_name=peer_name,
                remote_addr=addr,
                mode=mode,
            )
            peer = Peer(
                session.connection, role="server",
                dispatcher=ComputerDispatcher(
                    self._computer,
                    policy=self._policy,
                    audit=self._audit,
                    peer_public=session.peer_public,
                    peer_name=peer_name,
                    session_id=info.id,
                ),
            )
            info.peer = peer
            await self._sessions.add(info)

        if self._audit is not None:
            with contextlib.suppress(Exception):
                await self._audit.record_session_opened(
                    peer_public=info.peer_public, peer_name=info.peer_name,
                    session_id=info.id, remote_addr=info.remote_addr,
                    mode=info.mode.value,
                )

        close_reason = ""
        try:
            await peer.hello(self._build_hello_payload())
            peer.start()
            await peer.wait_closed()
        except Exception as exc:
            close_reason = f"error: {exc}"
            log.warning("session %s error: %s", info.id, exc)
        finally:
            await self._sessions.remove(info.id)
            with contextlib.suppress(Exception):
                await peer.aclose()
            if self._audit is not None:
                with contextlib.suppress(Exception):
                    await self._audit.record_session_closed(
                        peer_public=info.peer_public, peer_name=info.peer_name,
                        session_id=info.id,
                        duration=info.age_seconds(),
                        reason=close_reason,
                    )

    def _build_hello_payload(self) -> dict[str, Any]:
        """Compose the capabilities dict sent on every HELLO.

        The description is re-read from disk every time so
        ``opendesk describe …`` edits show up in the next session without
        restarting the daemon.
        """
        payload = self._computer.capabilities().model_dump()
        desc = read_description(self._home)
        if desc:
            payload["description"] = desc
        return payload

    async def _handshake(self, raw: Connection) -> tuple[Session, ServerMode]:
        """Negotiate auth.

        If pairing is currently enabled, the first message determines mode:
        a ``pair_hello`` frame routes to :func:`pair_server`; an
        ``auth_hello`` frame to :func:`auth_server`.  Because we haven't
        consumed any bytes yet we need to peek — but our wire format doesn't
        give us peek.  Solution: try ``pair_server`` first when pairing is
        enabled and the peer's first message is ``pair_hello``; otherwise
        ``auth_server``.

        v1 simplification: if pairing is on, ALL new connections are treated
        as pairing attempts.  Already-paired clients should reconnect after
        the user finishes pairing.  This avoids the peek complication.
        """
        if self._pairing_code is not None:
            # Reject simultaneous pairing attempts — only one handshake at a time.
            if self._pairing_in_progress.locked():
                raise AuthFailure("protocol", "another pairing handshake is in progress")
            async with self._pairing_in_progress:
                session = await pair_server(raw, self._identity, self._pairing_code)
            # Register the new peer with role="controller" so it can connect back
            # as a controller but cannot itself be connected to as a controlled machine.
            name = _default_peer_name(session.peer_public)
            if self._pairing_hook is not None:
                await self._pairing_hook(session.peer_public, name)
            else:
                self._trusted.add(session.peer_public, name=name, role="controller")
            self._pairing_event.set()
            return session, ServerMode.PAIR

        session = await auth_server(raw, self._identity, self._trusted)
        return session, ServerMode.SERVE


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


async def serve(
    *,
    computer: Optional[Computer] = None,
    home: Optional[Path] = None,
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    advertise_mdns: bool = True,
    pairing_code: Optional[str] = None,
    pairing_timeout: Optional[float] = None,
    policy: Optional[Policy] = None,
    enable_audit: bool = True,
) -> None:
    """Run opendesk serve until the process is interrupted.

    Most callers (and the CLI) use this.  Builds a :class:`LocalComputer` if
    none is provided, loads identity and trusted-peers from ``home``, and
    optionally accepts one pairing if ``pairing_code`` is set.
    """
    if computer is None:
        computer = LocalComputer()
    identity = Identity.load_or_create(home)
    trusted = TrustedPeers(home)

    server = OpendeskServer(
        computer, identity, trusted,
        host=host, port=port, advertise_mdns=advertise_mdns, home=home,
        policy=policy, enable_audit=enable_audit,
    )
    await server.start()
    try:
        if pairing_code is not None:
            log.info("Pairing enabled.  Code: %s", pairing_code)
            await server.enable_pairing(pairing_code, timeout=pairing_timeout)
            log.info("Pairing complete.")
        await server.serve_forever()
    finally:
        await server.aclose()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _default_service_name(identity: Identity) -> str:
    import platform
    short = identity.public_bytes.hex()[:6]
    return f"{platform.node()}-{short}"


def _default_peer_name(public_key: bytes) -> str:
    return f"peer-{public_key.hex()[:6]}"


async def _send_rejection(connection, *, code: str, message: str) -> None:
    """Send a HELLO frame carrying an error and close the connection.

    The client's :meth:`Peer.hello` sees the error and raises
    :class:`ProtocolError` with the code, so the rejection reason flows
    cleanly through the protocol instead of looking like an opaque
    disconnect.

    After sending, drain one frame (the client's HELLO send) before
    closing — otherwise the WebSocket close handshake races against the
    client's write and the client sees a generic ``ConnectionClosed``
    instead of our nicely-coded rejection.
    """
    from opendesk.protocol.codec import encode
    from opendesk.protocol.frames import ErrorInfo, HelloFrame
    frame = HelloFrame(
        role="server",
        capabilities={},
        error=ErrorInfo(code=code, message=message),
    )
    with contextlib.suppress(Exception):
        await connection.send(encode(frame))
    with contextlib.suppress(Exception):
        await asyncio.wait_for(connection.recv(), timeout=2.0)
    with contextlib.suppress(Exception):
        await connection.aclose()
