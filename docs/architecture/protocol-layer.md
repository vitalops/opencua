# Protocol Layer

## Layer 3: `protocol/`

The transport-agnostic wire protocol. Five frame types carry every byte
exchanged between peers:

| Frame | Direction | Purpose |
|---|---|---|
| `HELLO` | both | First frame each side sends. Carries protocol version, role, principal, auth proof, capability manifest. |
| `REQ` | caller → peer | Unary call (`stream=false`) or stream-starting call (`stream=true`). |
| `RES` | peer → caller | Response or one frame of a streaming response. Carries `result` or `error`. |
| `CANCEL` | either | Abort an in-flight call. |
| `PUSH` | either | Server-originated event not tied to a prior request. |

### `frames.py`

Pydantic models for the five frame types and `ErrorInfo` with a stable
`ErrorCode` enum (`capability_unsupported`, `permission_denied`,
`invalid_argument`, `not_found`, `timeout`, `cancelled`, `internal`,
`protocol`).

### `codec.py`

msgpack encode/decode. Bytes round-trip as native msgpack `bin`; no base64
ever. A `default` hook converts Python `set` and `Enum` instances to
list / value so Pydantic models with those types serialise cleanly.

### `connection.py`

The transport-shaped interface a `Peer` runs on. `Connection` ABC with
`send(bytes)`, `recv() → bytes`, `aclose()`. Includes `LoopbackConnection`
for hermetic in-process testing.

### `transports/websocket.py`

`WebSocketConnection`, `connect_websocket(url)`, `serve_websocket(handler, host, port)`.
Binary-only — text frames are rejected as a protocol violation.

### `peer.py`

`Peer` correlates call ids over a `Connection`. Public API:

```python
peer = Peer(connection, role="client", dispatcher=optional)
await peer.hello(my_manifest)        # 1 RTT handshake
peer.start()                          # spawn recv loop
result = await peer.call("display.capture", {...})
async for frame in peer.stream("display.subscribe", {...}): ...
await peer.aclose()
```

Owns in-flight tables (unary, stream, inbound), routes cancellation in both
directions, maps wire error codes to Python exceptions (`cancelled` →
`CancelledError`, `permission_denied` → `PermissionDeniedError`, everything
else → `ProtocolError(code, message, details)`).

### `auth/`

* `identity.py` — `Identity` long-lived X25519 keypair persisted at
  `~/.opendesk/identity.key` (mode 0600, atomic write).
* `storage.py` — `TrustedPeers` JSON file with entries
  `{public_key, name, paired_at, description, description_override,
  last_host, last_port}`. `description` is cached from the peer's
  mDNS/HELLO broadcast; `description_override` is the local user label
  (wins when non-empty). `last_host`/`last_port` allow reconnecting
  without an mDNS round-trip — essential in WSL2 environments.
* `handshake.py` — two flavours:
  * `pair_server` / `pair_client` — 3-message PSK-authenticated handshake.
    PSK derived from the 6-digit code via PBKDF2-HMAC-SHA256 at 200 000
    iterations. Both sides learn each other's static public key.
  * `auth_server` / `auth_client` — 2-message mutual-static-key handshake.
    Server rejects clients whose key isn't in `TrustedPeers`; client rejects
    servers that don't hold the expected static key.
* `encrypted.py` — `EncryptedConnection` wraps a Connection with
  ChaCha20-Poly1305 AEAD per frame. Per-direction counter as the nonce;
  counter not transmitted (sender and receiver each maintain their own).
  Tamper or sync loss → `ConnectionClosed`.

---

Next: [Remote & Integrations →](remote-layer.md)
