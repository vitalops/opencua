# Encryption & Identity

## 3. Encryption

After a successful handshake (§5 or §6) the two peers share two 32-byte
session keys — one per direction — and all subsequent frames pass through
`EncryptedConnection` (`protocol/auth/encrypted.py`).

**Algorithm:** ChaCha20-Poly1305 AEAD (RFC 8439).

**Nonce:** A per-direction monotonic counter encoded as a 96-bit big-endian
integer. The counter starts at 0, increments by 1 per frame, and is **not
transmitted** — both ends maintain their own copy. Because the transport is
reliable and ordered, both counters stay in sync.

**Effect of tampering or desync:** AEAD verification fails → `ConnectionClosed`
is raised immediately on the receiving side. There is no retry; the TCP
connection must be re-established and a new handshake run.

**Key lifetime:** Session keys are ephemeral — they exist for one TCP
connection. Long-lived static keys never appear in frame payloads.

---

## 4. Identity and key material

Each machine holds a long-lived X25519 static keypair in its **identity**
(`protocol/auth/identity.py`):

| File | Mode | Contents |
|---|---|---|
| `~/.opendesk/identity.key` | `0600` | 32 raw bytes — the X25519 **private** key. Never leaves disk. |

The **fingerprint** is derived from the public key by hex-encoding it and
grouping into four colon-separated 4-character chunks:
`abcd:ef01:2345:6789` (16 hex chars = first 8 bytes of the 32-byte key).

Trusted peers are stored in `~/.opendesk/trusted-peers.json` (mode `0600`,
directory mode `0700`). Each entry:

```json
{
  "public_key": "<64-char hex>",
  "name": "mini",
  "paired_at": 1710000000.0,
  "description": "<cached from peer's mDNS/HELLO>",
  "description_override": "<user-set local label>",
  "last_host": "192.168.1.42",
  "last_port": 8423
}
```

`description_override` wins over `description` when non-empty (exposed as
`effective_description`). `last_host`/`last_port` allow reconnecting without
an mDNS round-trip — essential in WSL2 or AP-isolation environments.

---

Next: [Pairing Handshake →](pairing.md) — how two machines establish trust for the first time.
