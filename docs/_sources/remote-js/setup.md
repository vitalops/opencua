# One-time Setup

## On the controlled machine

```bash
npm install -g @vitalops/opendesk-sdk   # or npx for one-off use
opendesk-js pair
```

You'll see something like:

```
┌──────────────────────────────────────────────────────┐
│  opendesk pairing                                    │
│  port:        8423                                   │
│  fingerprint: 9c2f:1abc:b3d4:8870                    │
│                                                      │
│  pairing code:  428901                               │
│                                                      │
│  On the controller machine run:                      │
│    opendesk-js pair-with <host> 428901               │
└──────────────────────────────────────────────────────┘
```

Leave this terminal open while pairing. Once a controller successfully pairs
the process exits.

## On the controller

```bash
npx opendesk-js discover
```

Lists `_opendesk._tcp.local` services on the LAN:

```
NAME                      ADDR                    FINGERPRINT              DESCRIPTION
mac-mini                  192.168.1.42:8423       9c2f:1abc:b3d4:8870
```

Now pair (using the code shown on the controlled machine):

```bash
npx opendesk-js pair-with mac-mini.local 428901 --name mini
# Paired with mini (9c2f:1abc:b3d4:8870)
# Now reachable as: opendesk-js connect mini
```

Both machines now have each other's static public keys stored in
`~/.opendesk/trusted-peers.json`.

---

Pairing done. Next: [start the daemon →](running.md)
