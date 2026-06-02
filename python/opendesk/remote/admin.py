"""Local IPC for inspecting / killing active opendesk-serve sessions.

A user-only channel — Unix domain socket at ``~/.opendesk/admin.sock`` (mode
``0600``), or localhost TCP on a file-recorded port on Windows.  Same-user
processes can connect; other users on the machine cannot, because the
socket file is owner-only on Unix and localhost-bound on Windows (the file
itself is also ``0600`` on platforms that honour it).

Wire format (one round trip per CLI invocation):

* Request frame: 4-byte big-endian length + msgpack-encoded dict.
* Response frame: same shape.

Requests::

    {"op": "list"}
    {"op": "kill", "id": "abc1"}
    {"op": "kill_all"}

Responses::

    {"ok": true, "sessions": [{...}, ...]}
    {"ok": true, "killed": N}
    {"ok": false, "error": "no such session"}
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
from pathlib import Path
from typing import Any, Optional

import msgpack

from opendesk.protocol.auth.identity import DEFAULT_HOME


SOCKET_NAME = "admin.sock"
PORT_FILE = "admin.port"
TOKEN_FILE = "admin.token"

_IS_WINDOWS = platform.system() == "Windows"


def _socket_path(home: Optional[Path]) -> Path:
    return (Path(home) if home else DEFAULT_HOME) / SOCKET_NAME


def _port_path(home: Optional[Path]) -> Path:
    return (Path(home) if home else DEFAULT_HOME) / PORT_FILE


def _token_path(home: Optional[Path]) -> Path:
    return (Path(home) if home else DEFAULT_HOME) / TOKEN_FILE


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    length_bytes = await reader.readexactly(4)
    length = int.from_bytes(length_bytes, "big")
    data = await reader.readexactly(length)
    obj = msgpack.unpackb(data, raw=False)
    if not isinstance(obj, dict):
        raise ValueError(f"admin frame is not a dict: {type(obj).__name__}")
    return obj


async def _write_frame(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    data = msgpack.packb(payload, use_bin_type=True)
    writer.write(len(data).to_bytes(4, "big") + data)
    await writer.drain()


# ---------------------------------------------------------------------------
# Server side
# ---------------------------------------------------------------------------


class AdminServer:
    """Local IPC listener bound to an :class:`OpendeskServer`'s registry."""

    def __init__(self, opendesk_server, *, home: Optional[Path] = None) -> None:
        self._server = opendesk_server
        self._home = home
        self._asyncio_server: Optional[asyncio.AbstractServer] = None
        self._token: str = ""

    async def start(self) -> None:
        import secrets
        home = Path(self._home) if self._home else DEFAULT_HOME
        home.mkdir(parents=True, exist_ok=True, mode=0o700)

        if _IS_WINDOWS:
            self._token = secrets.token_hex(32)
            srv = await asyncio.start_server(
                self._handle, host="127.0.0.1", port=0,
            )
            port = srv.sockets[0].getsockname()[1]
            _port_path(home).write_text(str(port))
            tok_path = _token_path(home)
            tok_path.write_text(self._token)
            # best-effort restriction — ineffective on Windows but harmless
            with contextlib.suppress(OSError):
                os.chmod(tok_path, 0o600)
        else:
            path = _socket_path(home)
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            srv = await asyncio.start_unix_server(self._handle, path=str(path))
            os.chmod(path, 0o600)
        self._asyncio_server = srv

    async def aclose(self) -> None:
        if self._asyncio_server is None:
            return
        self._asyncio_server.close()
        with contextlib.suppress(Exception):
            await self._asyncio_server.wait_closed()
        self._asyncio_server = None
        # Best-effort cleanup of files.
        if _IS_WINDOWS:
            with contextlib.suppress(FileNotFoundError):
                _port_path(self._home).unlink()
            with contextlib.suppress(FileNotFoundError):
                _token_path(self._home).unlink()
        else:
            with contextlib.suppress(FileNotFoundError):
                _socket_path(self._home).unlink()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        import hmac
        try:
            request = await _read_frame(reader)
            if _IS_WINDOWS and self._token:
                provided = request.get("token", "")
                if not hmac.compare_digest(provided, self._token):
                    response: dict = {"ok": False, "error": "unauthorized"}
                else:
                    response = await self._dispatch(request)
            else:
                response = await self._dispatch(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        try:
            await _write_frame(writer, response)
        finally:
            with contextlib.suppress(Exception):
                writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        op = request.get("op")
        if op == "list":
            sessions = await self._server.sessions.list()
            return {"ok": True, "sessions": [_session_to_dict(s) for s in sessions]}
        if op == "kill":
            sid = request.get("id")
            if not isinstance(sid, str):
                return {"ok": False, "error": "missing 'id'"}
            killed = await self._server.sessions.kill(sid)
            return {"ok": killed, "killed": int(killed)}
        if op == "kill_all":
            killed = await self._server.sessions.kill_all()
            return {"ok": True, "killed": killed}
        return {"ok": False, "error": f"unknown op {op!r}"}


def _session_to_dict(s) -> dict[str, Any]:
    return {
        "id": s.id,
        "peer_name": s.peer_name,
        "peer_pubkey_hex": s.peer_public.hex(),
        "remote_addr": s.remote_addr,
        "started_at": s.started_at,
        "age_seconds": s.age_seconds(),
        "mode": s.mode.value,
    }


# ---------------------------------------------------------------------------
# Client side
# ---------------------------------------------------------------------------


class AdminError(RuntimeError):
    """Raised when an admin operation fails or the server isn't reachable."""


class AdminClient:
    """Connect to the admin IPC of a running ``opendesk serve`` instance."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        token: str = "",
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._token = token

    @classmethod
    async def connect(cls, *, home: Optional[Path] = None) -> "AdminClient":
        if _IS_WINDOWS:
            port_path = _port_path(home)
            if not port_path.exists():
                raise AdminError(
                    f"No opendesk admin endpoint at {port_path}.  "
                    "Is `opendesk serve` running?"
                )
            try:
                port = int(port_path.read_text().strip())
            except ValueError as exc:
                raise AdminError(f"corrupt admin port file: {exc}") from exc
            tok_path = _token_path(home)
            token = tok_path.read_text().strip() if tok_path.exists() else ""
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        else:
            path = _socket_path(home)
            if not path.exists():
                raise AdminError(
                    f"No opendesk admin socket at {path}.  "
                    "Is `opendesk serve` running?"
                )
            token = ""
            reader, writer = await asyncio.open_unix_connection(path=str(path))
        return cls(reader, writer, token=token)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()

    async def _round_trip(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._token:
            request = {**request, "token": self._token}
        await _write_frame(self._writer, request)
        return await _read_frame(self._reader)

    async def list_sessions(self) -> list[dict[str, Any]]:
        resp = await self._round_trip({"op": "list"})
        if not resp.get("ok"):
            raise AdminError(resp.get("error") or "list failed")
        return list(resp.get("sessions") or [])

    async def kill(self, session_id: str) -> bool:
        resp = await self._round_trip({"op": "kill", "id": session_id})
        return bool(resp.get("ok"))

    async def kill_all(self) -> int:
        resp = await self._round_trip({"op": "kill_all"})
        if not resp.get("ok"):
            raise AdminError(resp.get("error") or "kill_all failed")
        return int(resp.get("killed") or 0)
