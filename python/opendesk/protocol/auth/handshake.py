"""Mutual-authentication handshake.

Two flavours, both running over a raw :class:`Connection` (typically a
:class:`~opendesk.protocol.transports.websocket.WebSocketConnection`):

* :func:`pair_server` / :func:`pair_client` — first contact, authenticated by
  a short numeric code (PSK).  Both sides learn each other's long-lived
  static public key as part of the exchange.
* :func:`auth_server` / :func:`auth_client` — subsequent connections,
  authenticated by the static keys exchanged at pairing.  The client must
  hold the private key corresponding to a public key in the server's
  :class:`TrustedPeers` store, and vice versa.

Both flavours produce a :class:`Session` carrying an
:class:`EncryptedConnection` ready to be handed to a
:class:`~opendesk.protocol.peer.Peer`.

Wire shape — handshake messages
-------------------------------
Each handshake step is a msgpack-encoded dict, sent as one binary message on
the underlying Connection.

Pairing (PSK-authenticated, 3 messages)::

    1. C → S: {"v": 1, "kind": "pair_hello", "e": <e_c_pub>}
    2. S → C: {"v": 1, "kind": "pair_offer", "e": <e_s_pub>,
                       "ct": AEAD(k1, 0, s_s_pub)}
    3. C → S: {"v": 1, "kind": "pair_finish",
                       "ct": AEAD(k2, 0, s_c_pub)}

    k1 = HKDF(salt=PSK_DERIVED, ikm=DH(e_s, e_c), info="opendesk-pair-k1")
    k2 = HKDF(salt=PSK_DERIVED || ct1, ikm=DH(e_c, e_s),
              info="opendesk-pair-k2")

Reconnect (static-key, 2 messages)::

    1. C → S: {"v": 1, "kind": "auth_hello",
                       "e": <e_c_pub>, "s": <s_c_pub>}
    2. S → C: {"v": 1, "kind": "auth_offer", "e": <e_s_pub>,
                       "ct": AEAD(k, 0, b"ok")}

    k = HKDF(salt=transcript_hash,
            ikm=DH(e_s, e_c) || DH(s_s, e_c),
            info="opendesk-auth-k")

Threat-model notes
------------------
The 6-digit pairing code has only 10⁶ entropy and a sniffer could attempt an
offline brute force after recording the exchange.  PSK derivation therefore
runs PBKDF2-HMAC-SHA256 at 200 000 iterations, making one offline check
cost ~100 ms on a modern CPU — about a CPU-month to exhaust the space.  For
v1 LAN pairing this is comfortable; v2 should consider Argon2id.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Optional

import msgpack

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "cryptography is required for opendesk auth. "
        "Install with: pip install 'opendesk[remote]'"
    ) from _exc

from opendesk.protocol.auth.encrypted import EncryptedConnection
from opendesk.protocol.auth.identity import Identity
from opendesk.protocol.auth.storage import TrustedPeers
from opendesk.protocol.connection import Connection, ConnectionClosed


HANDSHAKE_VERSION = 1
PSK_ITERATIONS = 200_000
PSK_SALT = b"opendesk-psk-v1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuthFailure(RuntimeError):
    """Raised when the handshake cannot complete successfully.

    Subtypes (carried as ``reason``):
    * ``"wrong_code"`` — pairing PSK mismatch (peer cannot decrypt our offer).
    * ``"untrusted_peer"`` — static key not in trusted-peers.
    * ``"unexpected_peer"`` — server's static key didn't match expected.
    * ``"protocol"`` — malformed handshake message.
    """

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """The outcome of a successful handshake.

    ``connection`` is the :class:`EncryptedConnection` ready to be wrapped by
    a :class:`Peer`.  ``peer_public`` is the now-verified long-lived static
    public key of the other side.
    """

    connection: EncryptedConnection
    peer_public: bytes
    is_pairing: bool


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------


def derive_psk(code: str) -> bytes:
    """Stretch a numeric pairing code into a 32-byte symmetric key.

    Uses PBKDF2-HMAC-SHA256 with 200 000 iterations to make offline brute
    force expensive even against the small (10⁶) keyspace of a 6-digit code.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=PSK_SALT,
        iterations=PSK_ITERATIONS,
    )
    return kdf.derive(code.encode("utf-8"))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(ikm)


def _raw_pub(key: X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _gen_ephemeral() -> tuple[X25519PrivateKey, bytes]:
    eph = X25519PrivateKey.generate()
    return eph, _raw_pub(eph.public_key())


def _dh(private: X25519PrivateKey, peer_pub_bytes: bytes) -> bytes:
    return private.exchange(X25519PublicKey.from_public_bytes(peer_pub_bytes))


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


async def _send_msg(conn: Connection, payload: dict) -> None:
    payload = {"v": HANDSHAKE_VERSION, **payload}
    await conn.send(msgpack.packb(payload, use_bin_type=True))


async def _recv_msg(conn: Connection, expected_kind: str) -> dict:
    try:
        data = await conn.recv()
    except ConnectionClosed as exc:
        raise AuthFailure("protocol", f"peer closed before {expected_kind}") from exc
    try:
        msg = msgpack.unpackb(data, raw=False)
    except Exception as exc:
        raise AuthFailure("protocol", f"malformed {expected_kind} msgpack") from exc
    if not isinstance(msg, dict):
        raise AuthFailure("protocol", f"{expected_kind} not a dict")
    if msg.get("v") != HANDSHAKE_VERSION:
        raise AuthFailure("protocol", f"unsupported handshake version {msg.get('v')!r}")
    if msg.get("kind") != expected_kind:
        raise AuthFailure("protocol", f"expected {expected_kind}, got {msg.get('kind')!r}")
    return msg


# ---------------------------------------------------------------------------
# Session key derivation
# ---------------------------------------------------------------------------


def _session_keys(
    *,
    is_server: bool,
    transcript: bytes,
    dh_chain: list[bytes],
    psk: Optional[bytes] = None,
) -> tuple[bytes, bytes]:
    """Derive (send_key, recv_key) from accumulated DH outputs + transcript hash."""
    ikm = b"".join(dh_chain)
    if psk:
        ikm += psk
    keys = _hkdf(salt=transcript, ikm=ikm, info=b"opendesk-session-keys", length=64)
    c2s, s2c = keys[:32], keys[32:]
    return (s2c, c2s) if is_server else (c2s, s2c)


# ---------------------------------------------------------------------------
# Pairing — server side
# ---------------------------------------------------------------------------


async def pair_server(
    connection: Connection,
    identity: Identity,
    code: str,
) -> Session:
    """Server-side pairing handshake.

    Returns a :class:`Session` containing the encrypted connection plus the
    client's now-verified long-lived static public key.  Callers are
    responsible for storing that key in their :class:`TrustedPeers` once
    pairing succeeds.
    """
    psk = derive_psk(code)
    eph, eph_pub = _gen_ephemeral()

    # Message 1: receive client ephemeral.
    msg1 = await _recv_msg(connection, "pair_hello")
    e_c = _require_bytes(msg1, "e", 32)

    # Derive k1 and encrypt our static public key as the second message.
    dh_ee = _dh(eph, e_c)
    k1 = _hkdf(salt=psk, ikm=dh_ee, info=b"opendesk-pair-k1")
    aead1 = ChaCha20Poly1305(k1)
    ct1 = aead1.encrypt(b"\x00" * 12, identity.public_bytes, None)

    await _send_msg(connection, {"kind": "pair_offer", "e": eph_pub, "ct": ct1})

    # Message 3: receive client's encrypted static public key.
    msg3 = await _recv_msg(connection, "pair_finish")
    ct2 = _require_bytes(msg3, "ct", None)

    # Derive k2; uses ct1 as a transcript-binding salt.
    k2 = _hkdf(
        salt=psk + ct1,
        ikm=_dh(eph, e_c),  # same DH because we don't have client's static yet
        info=b"opendesk-pair-k2",
    )
    aead2 = ChaCha20Poly1305(k2)
    try:
        client_static = aead2.decrypt(b"\x00" * 12, ct2, None)
    except InvalidTag as exc:
        raise AuthFailure("wrong_code", "client could not prove the pairing code") from exc
    if len(client_static) != 32:
        raise AuthFailure("protocol", "decrypted client static key wrong length")

    # Session keys.  Server perspective.
    transcript = hashlib.sha256(eph_pub + e_c + ct1 + ct2).digest()
    dh_chain = [
        _dh(eph, e_c),                             # DH(e_s, e_c)
        identity.exchange(e_c),                    # DH(s_s, e_c)
        _dh(eph, client_static),                   # DH(e_s, s_c)
    ]
    send_key, recv_key = _session_keys(
        is_server=True, transcript=transcript, dh_chain=dh_chain, psk=psk,
    )
    return Session(
        connection=EncryptedConnection(
            connection, send_key=send_key, recv_key=recv_key,
            peer_public=client_static,
        ),
        peer_public=client_static,
        is_pairing=True,
    )


# ---------------------------------------------------------------------------
# Pairing — client side
# ---------------------------------------------------------------------------


async def pair_client(
    connection: Connection,
    identity: Identity,
    code: str,
) -> Session:
    """Client-side pairing handshake."""
    psk = derive_psk(code)
    eph, eph_pub = _gen_ephemeral()

    # Message 1: send our ephemeral.
    await _send_msg(connection, {"kind": "pair_hello", "e": eph_pub})

    # Message 2: receive server ephemeral + encrypted server static.
    msg2 = await _recv_msg(connection, "pair_offer")
    e_s = _require_bytes(msg2, "e", 32)
    ct1 = _require_bytes(msg2, "ct", None)

    dh_ee = _dh(eph, e_s)
    k1 = _hkdf(salt=psk, ikm=dh_ee, info=b"opendesk-pair-k1")
    aead1 = ChaCha20Poly1305(k1)
    try:
        server_static = aead1.decrypt(b"\x00" * 12, ct1, None)
    except InvalidTag as exc:
        raise AuthFailure("wrong_code", "server PSK does not match — wrong code?") from exc
    if len(server_static) != 32:
        raise AuthFailure("protocol", "decrypted server static key wrong length")

    # Message 3: encrypt our static and send.
    k2 = _hkdf(
        salt=psk + ct1,
        ikm=dh_ee,
        info=b"opendesk-pair-k2",
    )
    aead2 = ChaCha20Poly1305(k2)
    ct2 = aead2.encrypt(b"\x00" * 12, identity.public_bytes, None)
    await _send_msg(connection, {"kind": "pair_finish", "ct": ct2})

    transcript = hashlib.sha256(e_s + eph_pub + ct1 + ct2).digest()
    # Order must match the server's chain byte-for-byte:
    # server pos 1  DH(e_s, e_c)  ≡  client DH(e_c, e_s)  =  _dh(eph, e_s)
    # server pos 2  DH(s_s, e_c)  ≡  client DH(e_c, s_s)  =  _dh(eph, server_static)
    # server pos 3  DH(e_s, s_c)  ≡  client DH(s_c, e_s)  =  identity.exchange(e_s)
    dh_chain = [
        _dh(eph, e_s),
        _dh(eph, server_static),
        identity.exchange(e_s),
    ]
    send_key, recv_key = _session_keys(
        is_server=False, transcript=transcript, dh_chain=dh_chain, psk=psk,
    )
    return Session(
        connection=EncryptedConnection(
            connection, send_key=send_key, recv_key=recv_key,
            peer_public=server_static,
        ),
        peer_public=server_static,
        is_pairing=True,
    )


# ---------------------------------------------------------------------------
# Reconnect — server side
# ---------------------------------------------------------------------------


async def auth_server(
    connection: Connection,
    identity: Identity,
    trusted: TrustedPeers,
) -> Session:
    """Server-side static-key handshake.

    Rejects clients whose long-lived static key is not in ``trusted``.
    """
    eph, eph_pub = _gen_ephemeral()

    msg1 = await _recv_msg(connection, "auth_hello")
    e_c = _require_bytes(msg1, "e", 32)
    s_c = _require_bytes(msg1, "s", 32)

    if not trusted.contains_as_controller(s_c):
        # Send a deliberately ambiguous failure: don't leak whether it was
        # an unknown key vs. a bad ephemeral.  Close after responding.
        await _send_msg(connection, {"kind": "auth_offer", "e": eph_pub, "ct": b""})
        raise AuthFailure("untrusted_peer", "client static key not in trusted-peers")

    dh_chain = [
        _dh(eph, e_c),                # DH(e_s, e_c)
        identity.exchange(e_c),       # DH(s_s, e_c)
        _dh(eph, s_c),                # DH(e_s, s_c)
        identity.exchange(s_c),       # DH(s_s, s_c)
    ]
    transcript = hashlib.sha256(eph_pub + e_c + s_c).digest()
    k = _hkdf(salt=transcript, ikm=b"".join(dh_chain[:2]),
              info=b"opendesk-auth-k")
    ct = ChaCha20Poly1305(k).encrypt(b"\x00" * 12, b"ok", None)
    await _send_msg(connection, {"kind": "auth_offer", "e": eph_pub, "ct": ct})

    send_key, recv_key = _session_keys(
        is_server=True, transcript=transcript, dh_chain=dh_chain,
    )
    return Session(
        connection=EncryptedConnection(
            connection, send_key=send_key, recv_key=recv_key, peer_public=s_c,
        ),
        peer_public=s_c,
        is_pairing=False,
    )


# ---------------------------------------------------------------------------
# Reconnect — client side
# ---------------------------------------------------------------------------


async def auth_client(
    connection: Connection,
    identity: Identity,
    expected_server_pubkey: bytes,
) -> Session:
    """Client-side static-key handshake.

    Verifies the server holds the private key for ``expected_server_pubkey``
    (the public key recorded during a previous pairing).
    """
    if len(expected_server_pubkey) != 32:
        raise ValueError("expected_server_pubkey must be 32 bytes")
    eph, eph_pub = _gen_ephemeral()

    await _send_msg(connection, {
        "kind": "auth_hello", "e": eph_pub, "s": identity.public_bytes,
    })

    msg2 = await _recv_msg(connection, "auth_offer")
    e_s = _require_bytes(msg2, "e", 32)
    ct = _require_bytes(msg2, "ct", None)

    # Order must match auth_server's chain byte-for-byte:
    # server pos 1  DH(e_s, e_c)  ≡  client _dh(eph, e_s)
    # server pos 2  DH(s_s, e_c)  ≡  client _dh(eph, expected_server_pubkey)
    # server pos 3  DH(e_s, s_c)  ≡  client identity.exchange(e_s)
    # server pos 4  DH(s_s, s_c)  ≡  client identity.exchange(expected_server_pubkey)
    dh_chain = [
        _dh(eph, e_s),
        _dh(eph, expected_server_pubkey),
        identity.exchange(e_s),
        identity.exchange(expected_server_pubkey),
    ]
    transcript = hashlib.sha256(e_s + eph_pub + identity.public_bytes).digest()
    k = _hkdf(salt=transcript, ikm=b"".join(dh_chain[:2]),
              info=b"opendesk-auth-k")
    try:
        ok = ChaCha20Poly1305(k).decrypt(b"\x00" * 12, ct, None)
    except InvalidTag as exc:
        raise AuthFailure(
            "unexpected_peer",
            "server does not hold the expected static key",
        ) from exc
    if ok != b"ok":
        raise AuthFailure("protocol", "unexpected confirmation payload")

    send_key, recv_key = _session_keys(
        is_server=False, transcript=transcript, dh_chain=dh_chain,
    )
    return Session(
        connection=EncryptedConnection(
            connection, send_key=send_key, recv_key=recv_key,
            peer_public=expected_server_pubkey,
        ),
        peer_public=expected_server_pubkey,
        is_pairing=False,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_bytes(msg: dict, field: str, length: Optional[int]) -> bytes:
    val = msg.get(field)
    if not isinstance(val, (bytes, bytearray)):
        raise AuthFailure("protocol", f"field {field!r} missing or wrong type")
    val = bytes(val)
    if length is not None and len(val) != length:
        raise AuthFailure(
            "protocol", f"field {field!r} must be {length} bytes, got {len(val)}",
        )
    return val
