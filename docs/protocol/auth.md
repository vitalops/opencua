# Auth Handshake

## 6. Auth handshake (reconnect)

After pairing, subsequent connections use a **2-message static-key handshake**
— no code required.

### Wire messages

```
1. C → S:  {"v": 1, "kind": "auth_hello",
                    "e": <e_c_pub 32 B>,
                    "s": <s_c_pub 32 B>}

2. S → C:  {"v": 1, "kind": "auth_offer",
                    "e": <e_s_pub 32 B>,
                    "ct": AEAD(k, nonce=0, plaintext=b"ok")}
```

If the server does not recognise the client's static key (`s_c_pub`), it
still responds with message 2 but with `ct = b""` (zero bytes), then closes
— deliberately ambiguous to avoid leaking whether the key was unknown vs.
the DH was bad.

### Key derivation (auth)

```
transcript = SHA-256(e_s_pub || e_c_pub || s_c_pub)

k = HKDF-SHA256(salt=transcript,
                ikm=DH(e_s, e_c) || DH(s_s, e_c),
                info="opendesk-auth-k")
```

The `b"ok"` payload encrypted under `k` gives the client proof that the server
holds the private key for `s_s_pub` (recorded at pairing time).

### Session key derivation (auth)

```
dh_chain = [
    DH(e_s, e_c),   # server ephemeral × client ephemeral
    DH(s_s, e_c),   # server static    × client ephemeral
    DH(e_s, s_c),   # server ephemeral × client static
    DH(s_s, s_c),   # server static    × client static
]

64 bytes = HKDF-SHA256(salt=transcript, ikm=concat(dh_chain),
                        info="opendesk-session-keys", length=64)
```

No PSK is mixed in for auth (only for pairing).

### Auth failure codes

| `reason` | Meaning |
|---|---|
| `wrong_code` | AEAD decryption failed — PSK mismatch (wrong pairing code). |
| `untrusted_peer` | Client's static key not in server's `trusted-peers.json`. |
| `unexpected_peer` | Server's auth proof didn't verify — server holds a different key than expected. |
| `protocol` | Malformed message, wrong version, or wrong `kind`. |

---

With the connection live, both sides exchange typed frames. See [Frames & Errors →](frames.md)
