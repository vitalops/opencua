# Transport & Encoding

## 1. Transport

All traffic runs over **binary WebSocket** (RFC 6455) on top of TCP.

* Default port: **8423** on the server (controlled machine).
* Text frames are rejected as a protocol violation.
* The transport is **reliable and ordered**, which the encryption layer depends on (see [Encryption](encryption.md)).
* Future: QUIC transport is planned for v2 (eliminates head-of-line blocking for concurrent streams).

---

## 2. Encoding

Every frame is **msgpack-encoded** (see `protocol/codec.py`).

Key conventions:
* `bytes` values round-trip as native msgpack `bin` — **never base64**.
  Pixmaps, file contents, and public keys are all raw bytes on the wire.
* Python `set` is encoded as a msgpack array.
* Python `Enum` is encoded as its `.value`.
* Unknown fields on received frames are ignored (forward-compatible reads).

---

Next: [Encryption & Identity →](encryption.md)
