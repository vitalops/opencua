"""Long-lived peer identity — an X25519 keypair persisted on disk.

The private key file is the master secret for this opendesk install: anyone
who reads it can impersonate this machine to its paired peers.  We write it
``0600`` (owner read/write only) and trust filesystem permissions for the
v1 threat model (single-user machines).  Encrypting the key file with a
passphrase is a v2 concern.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "cryptography is required for opendesk auth. "
        "Install with: pip install 'opendesk[remote]'"
    ) from _exc


DEFAULT_HOME = Path.home() / ".opendesk"
IDENTITY_FILE = "identity.key"


def _raw_private(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _raw_public(key: X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class Identity:
    """An opendesk peer's long-lived X25519 keypair.

    Stored as 32 raw bytes (the private key) at ``<home>/identity.key`` with
    mode ``0600``.  The public key is derived on load.
    """

    KEY_BYTES = 32

    def __init__(self, private_key: X25519PrivateKey) -> None:
        self._private = private_key
        self._public_bytes = _raw_public(private_key.public_key())

    @property
    def public_bytes(self) -> bytes:
        return self._public_bytes

    @property
    def private_key(self) -> X25519PrivateKey:
        return self._private

    def exchange(self, peer_public: bytes) -> bytes:
        """X25519 DH between this static private and ``peer_public``."""
        if len(peer_public) != self.KEY_BYTES:
            raise ValueError(f"peer public key must be {self.KEY_BYTES} bytes")
        return self._private.exchange(X25519PublicKey.from_public_bytes(peer_public))

    @classmethod
    def generate(cls) -> "Identity":
        return cls(X25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, data: bytes) -> "Identity":
        if len(data) != cls.KEY_BYTES:
            raise ValueError(f"identity key must be {cls.KEY_BYTES} bytes")
        return cls(X25519PrivateKey.from_private_bytes(data))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load_or_create(cls, home: Optional[Path] = None) -> "Identity":
        """Load the identity from disk, generating + persisting it on first run."""
        home_dir = Path(home) if home else DEFAULT_HOME
        path = home_dir / IDENTITY_FILE
        if path.exists():
            return cls.from_private_bytes(path.read_bytes())
        home_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = cls.generate()
        _atomic_write_secret(path, _raw_private(identity._private))
        return identity

    def save(self, home: Optional[Path] = None) -> None:
        """Persist this identity (rare — usually ``load_or_create`` is enough)."""
        home_dir = Path(home) if home else DEFAULT_HOME
        home_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic_write_secret(home_dir / IDENTITY_FILE, _raw_private(self._private))


def _atomic_write_secret(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically, with 0600 permissions.

    Avoids leaving a half-written file if the process dies mid-write, and
    avoids briefly exposing the secret with world-readable permissions.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))

    try:
        os.write(fd, data)

        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            else:
                os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass

        os.close(fd)
        os.replace(tmp, path)

    except BaseException:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def generate_pairing_code(digits: int = 6) -> str:
    """Return a fresh numeric pairing code as a zero-padded string."""
    max_val = 10 ** digits
    n = secrets.randbelow(max_val)
    return str(n).zfill(digits)
