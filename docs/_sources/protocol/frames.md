# Frames & Errors

## 7. Frame types

After the handshake, both sides exchange typed frames. Every frame is a
msgpack dict with at minimum `{"v": 1, "type": "<name>", ...}`.

### HELLO

```
{
  "v":            1,
  "type":         "hello",
  "role":         "client" | "server",
  "principal":    "<friendly name, may be empty>",
  "auth":         {},          // reserved for future use
  "capabilities": { ... },    // CapabilityManifest dict
  "error":        null | {"code": "...", "message": "...", "details": {}}
}
```

* **First frame** sent by each side after the encrypted connection is
  established, before any REQ/RES exchange.
* `capabilities` carries the server's `CapabilityManifest` (supported methods,
  platform, backend info) so the client can check before calling.
* A server can **reject** the connection at the application level by setting
  `error` — e.g. `{"code": "busy", "message": "laptop is the active controller"}`.
  The client's `Peer.hello()` raises `ProtocolError` on a non-null error field.

### REQ

```
{
  "v":      1,
  "type":   "req",
  "id":     <monotonic integer, unique per Peer>,
  "method": "<namespace>.<name>",
  "stream": false | true,
  "params": { ... }
}
```

* `stream=false` (default): unary call, expects exactly one RES.
* `stream=true`: streaming call, expects multiple RES frames with `end=false`
  followed by a final RES with `end=true`.

### RES

```
{
  "v":      1,
  "type":   "res",
  "id":     <matches the REQ id>,
  "seq":    <0-based frame counter for streams>,
  "end":    true | false,
  "result": { ... } | null,
  "error":  null | {"code": "...", "message": "...", "details": {}}
}
```

* Unary: exactly one frame, `end=true`, `seq=0`.
* Streaming: N frames with `end=false`, then one with `end=true` (may have
  `result=null` if the stream ended cleanly).
* `result` and `error` are mutually exclusive; both null means empty success.

### CANCEL

```
{
  "v":      1,
  "type":   "cancel",
  "id":     <id of the call to abort>,
  "reason": "<optional human-readable string>"
}
```

Valid for both unary and streaming calls. The peer SHOULD respond with a
final RES carrying `error.code = "cancelled"`. Callers must tolerate
receiving the final RES before the CANCEL is processed.

### PUSH

```
{
  "v":       1,
  "type":    "push",
  "topic":   "<string>",
  "payload": { ... }
}
```

Server-originated event not tied to any request. Receivers that don't
recognise a topic SHOULD ignore it.

**Known topics:**

| Topic | Payload | Meaning |
|---|---|---|
| `session.evicted` | `{"reason": "<string>"}` | Server asked the client to leave (cooperative disconnect). A well-behaved client suppresses auto-reconnect and raises `SessionEvicted` on subsequent calls. |

---

## 8. Error codes

The closed set of `ErrorCode` values (stable strings):

| Code | Python exception | Meaning |
|---|---|---|
| `capability_unsupported` | `CapabilityUnsupported` | Method not supported by this platform or backend. |
| `permission_denied` | `PermissionDeniedError` | Server-side policy gate rejected the call. |
| `invalid_argument` | `ProtocolError` | Malformed or out-of-range parameter. |
| `not_found` | `ProtocolError` | Target resource (file, window, peer) doesn't exist. |
| `timeout` | `ProtocolError` | Operation timed out on the server side. |
| `cancelled` | `asyncio.CancelledError` | Call was cancelled (either side). |
| `internal` | `ProtocolError` | Unclassified server-side error. |
| `protocol` | `ProtocolError` | Wire-level violation (bad frame, wrong version, etc.). |
| `busy` | `ProtocolError` | Server already has an active controller session. |

---

Next: [Session Lifecycle & Method Namespace →](session.md)
