# Trust & Security

## What the threat model covers

- **Authentication.** Someone on your LAN cannot impersonate a paired
  controlled machine — the controller verifies the responder holds the X25519
  private key corresponding to the public key recorded during pairing.
  Symmetric for the controlled side.
- **Confidentiality.** Every protocol frame is ChaCha20-Poly1305 AEAD
  encrypted under per-direction session keys derived from a fresh
  Diffie-Hellman exchange in every connection.
- **Integrity.** Same AEAD provides integrity; tampering or replay causes the
  connection to close.
- **Forward secrecy.** Each connection uses fresh ephemeral keypairs;
  recording today's traffic and stealing a long-lived key tomorrow does not
  let an attacker decrypt the recording.

## What's not in v1

- **Internet-traversal.** The protocol is LAN-only for now. Cross-network
  needs STUN/TURN or a relay — phase 2.
- **CA-signed TLS.** Pairing uses out-of-band trust establishment (the code),
  not certificate authorities.
- **Multi-tenant accounts.** Trust is per-machine, single-user.

## Pairing-code strength

The code is 6 digits — 10⁶ possibilities. opendesk stretches the code via
PBKDF2-HMAC-SHA256 at 200 000 iterations before using it. At ~100 ms per
derivation on a modern CPU, exhausting the space takes ~a CPU-month. Pairing
only ever accepts one attempt and exits on success, so an online attacker has
one shot per `opendesk-js pair` invocation.

## Files written to disk

| Path | Mode | Contents |
|---|---|---|
| `~/.opendesk/identity.key` | `0600` | Raw 32-byte X25519 private key. Owner-only. Treat as a master secret. |
| `~/.opendesk/trusted-peers.json` | `0600` | `[{public_key, name, paired_at, description, description_override, last_host, last_port}, ...]` |
| `~/.opendesk/audit/<YYYY-MM-DD>.jsonl` | `0600` | Append-only audit log. One JSON object per line. |
| `~/.opendesk/description.txt` | `0600` | Broadcast description shown to controllers during discovery. |
| `~/.opendesk/default-peer` | `0600` | Persistent default peer name (written by `opendesk-js peers default`). |
| `~/.opendesk/admin.sock` | `0600` | Unix socket for CLI ↔ daemon IPC (Linux/macOS). Windows uses a localhost TCP port. |

---

Next: [CLI Reference →](cli.md)
