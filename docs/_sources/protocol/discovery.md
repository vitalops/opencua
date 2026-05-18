# Discovery, Admin IPC & Cryptographic Summary

## 11. mDNS discovery

opendesk advertises itself on the LAN via Zeroconf / Bonjour.

**Service type:** `_opendesk._tcp.local.`

**TXT record properties:**

| Key | Type | Value |
|---|---|---|
| `v` | string | Protocol version, currently `"1"`. |
| `pk` | bytes (binary) | 32-byte X25519 static public key. |
| `fp` | string | Colon-separated fingerprint for human display. |
| `desc` | string | Truncated self-description (max 120 UTF-8 bytes, cut at a character boundary). |

`desc` is a short version of whatever the operator set via `opendesk describe`.
The full description flows over the HELLO frame after authentication.

**Discovery:** `discover(timeout=2.0)` browses for `timeout` seconds and
returns a list of `DiscoveredPeer` objects. Peers without a valid `pk` TXT
record are silently skipped.

**Limitations:**
* mDNS is multicast UDP — it does not cross IP routers.
* Some Wi-Fi access points enable client isolation, blocking mDNS between
  devices on the same SSID.
* WSL2 (NAT mode) blocks mDNS in both directions. opendesk caches the last
  known `(host, port)` of each peer in `trusted-peers.json` and tries that
  first on reconnect, bypassing the mDNS limitation. Alternatively, enable
  WSL2 mirrored networking mode (`networkingMode=mirrored` in `~/.wslconfig`).

---

## 12. Capability manifest

The server sends its `CapabilityManifest` in the HELLO frame's `capabilities`
field. Fields include:

* `capabilities` — list of `Capability` enum values the server supports
  (e.g. `"display.capture"`, `"input.pointer"`, …).
* `platform` — `"darwin"` | `"linux"` | `"windows"`.
* `backend` — accessibility backend name (`"appscript"`, `"atspi"`, `"uia"`, …).
* `description` — the server's self-description string (full, untruncated).

The client stores this manifest locally (no round-trip per call) and uses it
to raise `CapabilityUnsupported` before sending a REQ for a method the server
doesn't support.

---

## 13. Admin IPC

`OpendeskServer` exposes a local admin socket for CLI management commands.
It is **never reachable over the network**.

| Platform | Path |
|---|---|
| Linux / macOS | `~/.opendesk/admin.sock` (Unix domain socket) |
| Windows | `\\.\pipe\opendesk-admin` (named pipe) |

Used by: `opendesk sessions`, `opendesk disconnect`, `opendesk unpair`, and
the web UI's disconnect / unpair API endpoints.

---

## 14. Cryptographic summary

| Primitive | Usage |
|---|---|
| X25519 | Static identity keypairs; ephemeral keypairs per handshake |
| PBKDF2-HMAC-SHA256 (200 000 iter) | PSK stretching from 6-digit pairing code |
| HKDF-SHA256 | Key derivation in both handshakes and session key material |
| ChaCha20-Poly1305 | Per-frame AEAD encryption of all session traffic |
| SHA-256 | Transcript hashing to bind session keys to the exchange |
| msgpack | Serialisation of handshake messages and protocol frames |

---

That's the full protocol. To see it in action between real machines, head to [Remote Computer Use →](../remote/index.md)
