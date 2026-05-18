# Pairing Handshake

## 5. Pairing handshake (first contact)

Pairing establishes mutual trust between two machines that have never spoken
before. The shared secret is a **6-digit numeric code** displayed on the
controlled machine and typed by the operator on the controller.

### PSK derivation

```
PSK = PBKDF2-HMAC-SHA256(
    password = code.encode("utf-8"),
    salt     = b"opendesk-psk-v1",
    iterations = 200 000,
    dklen    = 32,
)
```

200 000 iterations costs ~100 ms on a modern CPU, making offline brute-force
of the 10⁶-entry code space take roughly a CPU-month.

### Wire messages (3-message exchange)

```
1. C → S:  {"v": 1, "kind": "pair_hello", "e": <e_c_pub 32 B>}

2. S → C:  {"v": 1, "kind": "pair_offer",
                    "e": <e_s_pub 32 B>,
                    "ct": AEAD(k1, nonce=0, plaintext=s_s_pub)}

3. C → S:  {"v": 1, "kind": "pair_finish",
                    "ct": AEAD(k2, nonce=0, plaintext=c_s_pub)}
```

All byte fields are raw msgpack `bin`.

### Key derivation inside the handshake

```
k1 = HKDF-SHA256(salt=PSK,       ikm=DH(e_s, e_c),  info="opendesk-pair-k1")
k2 = HKDF-SHA256(salt=PSK||ct1,  ikm=DH(e_c, e_s),  info="opendesk-pair-k2")
```

`ct1` in the salt of k2 binds the session to the server's encrypted static
key, preventing transcript substitution attacks.

### Session key derivation (pairing)

After message 3:

```
transcript = SHA-256(e_s_pub || e_c_pub || ct1 || ct2)

dh_chain = [
    DH(e_s, e_c),    # ephemeral × ephemeral
    DH(s_s, e_c),    # server static × client ephemeral
    DH(e_s, s_c),    # server ephemeral × client static
]

material = concat(dh_chain) || PSK

64 bytes = HKDF-SHA256(salt=transcript, ikm=material,
                        info="opendesk-session-keys", length=64)

c2s_key = material[:32]   # client→server direction
s2c_key = material[32:]   # server→client direction
```

Each side derives both keys; their send key is the other side's receive key.

### Outcome

Both peers now hold each other's static public key (`s_s_pub` / `s_c_pub`),
which they persist in their `trusted-peers.json`. The session continues
immediately on the same connection, encrypted with the derived session keys.

---

After pairing, subsequent connections skip the code entirely. See [Auth Handshake →](auth.md)
