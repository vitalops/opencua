"""On-disk store of peers we trust to connect to (or be connected to by).

Single JSON file at ``<home>/trusted-peers.json``::

    [
      {
        "public_key": "<hex 64 chars>",
        "name": "fariz-laptop",
        "paired_at": "2026-05-12T10:30:00Z",
        "fingerprint": "abcd:ef01:..."
      },
      ...
    ]

The file is loaded fresh per query so that pairing from another terminal mid-
session takes effect without restarting :command:`opendesk serve`.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from opendesk.protocol.auth.identity import DEFAULT_HOME


TRUSTED_PEERS_FILE = "trusted-peers.json"
DEFAULT_PEER_FILE = "default-peer"


def fingerprint(public_key: bytes) -> str:
    """A short human-readable fingerprint for a public key.

    Eight colon-separated groups of two hex digits — enough to compare
    visually without having to read the full 32-byte key.
    """
    h = public_key.hex()
    return ":".join(h[i : i + 4] for i in range(0, 16, 4))


@dataclass
class TrustedPeer:
    """One trusted peer entry.

    ``description`` holds the peer's own self-description as cached from the
    most recent successful HELLO.  ``description_override`` is the
    controller-side label the local user has set explicitly.  When non-empty,
    the override wins (see :meth:`TrustedPeers.effective_description`).

    ``last_host`` / ``last_port`` are the network endpoint where this peer
    was last reached successfully.  Reused on the next connect so we don't
    need an mDNS round-trip — important in environments (WSL2, restricted
    networks) where mDNS doesn't traverse.

    ``role`` encodes the directional trust relationship:
    * ``"controller"`` — this peer is permitted to control *us* (stored on
      the controlled machine after a pairing where we were the server).
    * ``"remote"`` — we connect *to* this peer to control it (stored on the
      controller machine after pairing as the client).
    * ``""`` — legacy entry written before roles were introduced; treated as
      both directions for backward compatibility.
    """

    public_key: str  # hex
    name: str = ""
    paired_at: float = field(default_factory=time.time)
    description: str = ""
    description_override: str = ""
    last_host: str = ""
    last_port: int = 0
    role: str = ""  # "controller" | "remote" | "" (legacy)

    @property
    def public_bytes(self) -> bytes:
        return bytes.fromhex(self.public_key)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.public_bytes)

    @property
    def effective_description(self) -> str:
        return self.description_override or self.description


class TrustedPeers:
    """Read/write access to the trusted-peers JSON file."""

    def __init__(self, home: Optional[Path] = None) -> None:
        self._home = Path(home) if home else DEFAULT_HOME
        self._path = self._home / TRUSTED_PEERS_FILE

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> list[TrustedPeer]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        out: list[TrustedPeer] = []
        for item in raw:
            try:
                out.append(TrustedPeer(
                    public_key=item["public_key"],
                    name=item.get("name", ""),
                    paired_at=float(item.get("paired_at") or 0.0),
                    description=item.get("description", "") or "",
                    description_override=item.get("description_override", "") or "",
                    last_host=item.get("last_host", "") or "",
                    last_port=int(item.get("last_port") or 0),
                    role=item.get("role", "") or "",
                ))
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def _save(self, peers: list[TrustedPeer]) -> None:
        self._home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.write_text(json.dumps(
            [asdict(p) for p in peers],
            indent=2, sort_keys=True,
        ))

    def list(self) -> list[TrustedPeer]:
        return self._load()

    def contains(self, public_key: bytes) -> bool:
        hex_key = public_key.hex()
        return any(p.public_key == hex_key for p in self._load())

    def contains_as_controller(self, public_key: bytes) -> bool:
        """True if this key is trusted to control us (role='controller' or legacy '')."""
        hex_key = public_key.hex()
        return any(
            p.public_key == hex_key and p.role in ("controller", "")
            for p in self._load()
        )

    def find(self, public_key: bytes) -> Optional[TrustedPeer]:
        hex_key = public_key.hex()
        for p in self._load():
            if p.public_key == hex_key:
                return p
        return None

    def find_by_name(self, name: str) -> Optional[TrustedPeer]:
        for p in self._load():
            if p.name == name:
                return p
        return None

    def add(self, public_key: bytes, *, name: str = "", role: str = "") -> TrustedPeer:
        """Add or update a trusted peer.  Returns the stored entry."""
        peers = self._load()
        hex_key = public_key.hex()
        for i, p in enumerate(peers):
            if p.public_key == hex_key:
                changed = False
                updated_name = p.name
                updated_role = p.role
                if name and p.name != name:
                    updated_name = name
                    changed = True
                if role and p.role != role:
                    updated_role = role
                    changed = True
                if changed:
                    peers[i] = TrustedPeer(
                        public_key=hex_key, name=updated_name, paired_at=p.paired_at,
                        description=p.description,
                        description_override=p.description_override,
                        last_host=p.last_host, last_port=p.last_port,
                        role=updated_role,
                    )
                    self._save(peers)
                return peers[i]
        peer = TrustedPeer(public_key=hex_key, name=name, role=role)
        peers.append(peer)
        self._save(peers)
        return peer

    # ------------------------------------------------------------------
    # Descriptions
    # ------------------------------------------------------------------

    def cache_description(self, public_key: bytes, description: str) -> bool:
        """Update the cached description (from the most recent HELLO).

        Called by :func:`opendesk.remote.client.connect` after every
        successful handshake.  Returns ``True`` if anything changed.
        """
        peers = self._load()
        hex_key = public_key.hex()
        for i, p in enumerate(peers):
            if p.public_key == hex_key and p.description != description:
                peers[i] = TrustedPeer(
                    public_key=p.public_key, name=p.name, paired_at=p.paired_at,
                    description=description,
                    description_override=p.description_override,
                    last_host=p.last_host, last_port=p.last_port,
                    role=p.role,
                )
                self._save(peers)
                return True
        return False

    def cache_endpoint(self, public_key: bytes, host: str, port: int) -> bool:
        """Update the cached last-known network endpoint.

        Called after every successful pair / connect so reconnects don't
        need an mDNS round-trip — and so the connection works at all in
        WSL2 / similar environments where mDNS doesn't traverse the NAT.
        """
        peers = self._load()
        hex_key = public_key.hex()
        for i, p in enumerate(peers):
            if p.public_key == hex_key and (p.last_host != host or p.last_port != port):
                peers[i] = TrustedPeer(
                    public_key=p.public_key, name=p.name, paired_at=p.paired_at,
                    description=p.description,
                    description_override=p.description_override,
                    last_host=host, last_port=int(port),
                    role=p.role,
                )
                self._save(peers)
                return True
        return False

    def set_description_override(self, name: str, text: str) -> bool:
        """Set the controller-side description override for a trusted peer."""
        peers = self._load()
        for i, p in enumerate(peers):
            if p.name == name:
                peers[i] = TrustedPeer(
                    public_key=p.public_key, name=p.name, paired_at=p.paired_at,
                    description=p.description, description_override=text,
                    last_host=p.last_host, last_port=p.last_port,
                    role=p.role,
                )
                self._save(peers)
                return True
        return False

    def clear_description_override(self, name: str) -> bool:
        return self.set_description_override(name, "")

    def effective_description(self, name: str) -> str:
        """Return the override if set, else the cached HELLO description."""
        p = self.find_by_name(name)
        return p.effective_description if p else ""

    def rename(self, public_key_or_name: str, new_name: str) -> bool:
        peers = self._load()
        for i, p in enumerate(peers):
            if p.public_key == public_key_or_name or p.name == public_key_or_name:
                peers[i] = TrustedPeer(
                    public_key=p.public_key, name=new_name, paired_at=p.paired_at,
                    role=p.role,
                )
                self._save(peers)
                return True
        return False

    # ------------------------------------------------------------------
    # Persistent default peer
    # ------------------------------------------------------------------
    #
    # Kept as a one-line text file at ``<home>/default-peer`` (instead of a
    # field on the trusted-peers JSON) so it's trivial to inspect / edit
    # with a text editor or shell pipeline.

    def _default_file(self) -> Path:
        return self._home / DEFAULT_PEER_FILE

    def get_default(self) -> Optional[str]:
        """Return the persistently-stored default peer name, or ``None``.

        The name is validated against the current trusted-peers — if the
        stored default has been deleted from trusted-peers, this returns
        ``None`` rather than a dangling reference.
        """
        path = self._default_file()
        if not path.exists():
            return None
        try:
            name = path.read_text().strip()
        except OSError:
            return None
        if not name:
            return None
        if self.find_by_name(name) is None:
            return None
        return name

    def set_default(self, name: str) -> bool:
        """Persist ``name`` as the default peer.  Must already be trusted."""
        if self.find_by_name(name) is None:
            return False
        self._home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._default_file().write_text(name + "\n")
        return True

    def clear_default(self) -> bool:
        """Remove the persistent default peer.  Returns ``True`` if one was set."""
        path = self._default_file()
        if not path.exists():
            return False
        path.unlink()
        return True

    def remove(self, public_key_or_name: str) -> bool:
        """Remove a peer by hex key or friendly name.

        Also clears the persistent default if it was pointing at this peer,
        so future `effective_peer` calls don't see a dangling reference.
        Returns ``True`` if anything was removed.
        """
        peers = self._load()
        before = len(peers)
        target_names = {
            p.name for p in peers
            if p.public_key == public_key_or_name or p.name == public_key_or_name
        }
        peers = [
            p for p in peers
            if p.public_key != public_key_or_name and p.name != public_key_or_name
        ]
        if len(peers) == before:
            return False
        self._save(peers)
        current_default = None
        path = self._default_file()
        if path.exists():
            try:
                current_default = path.read_text().strip() or None
            except OSError:
                current_default = None
        if current_default and current_default in target_names:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        return True
