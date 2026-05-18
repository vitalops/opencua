# Protocol Reference

opendesk's wire protocol carries every byte exchanged between a controller
(client) and a controlled machine (server). This section covers the full
stack: transport, encoding, encryption, handshakes, frames, and mDNS discovery.

| Section | |
|---|---|
| [Transport & Encoding](transport.md) | WebSocket transport, msgpack encoding |
| [Encryption & Identity](encryption.md) | ChaCha20-Poly1305 AEAD, X25519 keypairs |
| [Pairing Handshake](pairing.md) | First-contact 3-message PSK exchange |
| [Auth Handshake](auth.md) | Reconnect 2-message static-key exchange |
| [Frames & Errors](frames.md) | HELLO, REQ, RES, CANCEL, PUSH, error codes |
| [Session & Methods](session.md) | Session lifecycle, method namespace |
| [Discovery & Admin](discovery.md) | mDNS, capability manifest, admin IPC, crypto summary |
